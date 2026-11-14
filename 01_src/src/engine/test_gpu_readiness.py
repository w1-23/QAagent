"""Offline contracts: controlled sessions are not evidence of physical GPU execution."""
from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from src.engine import hardware, embedder as embedding, reranker as ranking

GPU = "CUDAExecutionProvider"
CPU = "CPUExecutionProvider"


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch):
    monkeypatch.delenv("CITRUS_REQUIRE_GPU", raising=False)
    monkeypatch.setattr(hardware, "_runtime_mode", "auto")
    monkeypatch.setattr(hardware, "_probe_cache", {})
    monkeypatch.setattr(hardware, "_provider_priority", lambda: [GPU, CPU])
    monkeypatch.setattr(hardware.ort, "get_available_providers", lambda: [GPU, CPU])
    monkeypatch.setattr(embedding.Embedder, "_instance", None)
    monkeypatch.setattr(ranking.Reranker, "_instance", None)


class Session:
    def __init__(self, providers):
        self.providers = providers
        self.fallback_disabled = False

    def get_providers(self):
        return self.providers

    def disable_fallback(self):
        self.fallback_disabled = True


def probe_session(monkeypatch, tmp_path, actual, executed):
    class ProbeSession(Session):
        def __init__(self, *args, **kwargs):
            super().__init__(actual)
            self.profile = tmp_path / "profile.json"
            self.profile.write_text(json.dumps([
                {"cat": "Node", "args": {"provider": p, "op_name": "Gemm"}}
                for p in executed
            ]), encoding="utf-8")

        def run(self, names, inputs):
            return [inputs["x"] * 0.5]

        def end_profiling(self):
            return str(self.profile)

    monkeypatch.setattr(hardware, "_probe_model_path", lambda: tmp_path / "probe.onnx")
    monkeypatch.setattr(hardware.ort, "InferenceSession", ProbeSession)


def test_probe_rejects_ort_silent_cpu_session(monkeypatch, tmp_path):
    probe_session(monkeypatch, tmp_path, [CPU], [CPU])
    ok, reason = hardware.probe_provider(GPU)
    assert not ok
    assert "actual session providers=['CPUExecutionProvider']" in reason


@pytest.mark.parametrize("executed", [[CPU], [], [GPU, CPU]])
def test_probe_rejects_unproven_or_cpu_compute(monkeypatch, tmp_path, executed):
    probe_session(monkeypatch, tmp_path, [GPU, CPU], executed)
    ok, reason = hardware.probe_provider(GPU)
    assert not ok
    assert "executed compute providers=" in reason


def test_probe_accepts_verified_requested_kernel_in_controlled_profile(monkeypatch, tmp_path):
    probe_session(monkeypatch, tmp_path, [GPU, CPU], [GPU])
    ok, reason = hardware.probe_provider(GPU)
    assert ok
    assert "executed compute=['CUDAExecutionProvider']" in reason
    assert not (tmp_path / "profile.json").exists()


def test_probe_unavailable_is_not_success(monkeypatch):
    monkeypatch.setattr(hardware, "_probe_model_path", lambda: None)
    assert hardware.probe_provider(GPU)[0] is False


def test_probe_preparation_failure_is_actionable(monkeypatch):
    def fail():
        raise PermissionError("state directory is read-only")
    monkeypatch.setattr(hardware, "_probe_model_path", fail)
    assert hardware.probe_provider(GPU) == (False, "cannot prepare EP probe: state directory is read-only")


def test_probe_timeout_is_not_success(monkeypatch, tmp_path):
    monkeypatch.setattr(hardware, "_probe_model_path", lambda: tmp_path / "probe.onnx")
    monkeypatch.setattr(hardware.threading, "Thread", lambda **kwargs: SimpleNamespace(
        start=lambda: None, join=lambda timeout: None, is_alive=lambda: True))
    assert hardware.probe_provider(GPU)[0] is False


@pytest.mark.parametrize("mode", ["auto", "gpu"])
def test_strict_mode_does_not_return_cpu_on_failed_gpu(monkeypatch, mode):
    monkeypatch.setenv("CITRUS_REQUIRE_GPU", "1")
    monkeypatch.setattr(hardware, "_runtime_mode", mode)
    hardware._probe_cache[GPU] = (False, "CUDA library load failed")
    with pytest.raises(hardware.GPUUnavailableError, match="CUDA library load failed"):
        hardware.get_ort_providers()


@pytest.mark.parametrize("mode", ["auto", "gpu"])
def test_default_mode_preserves_cpu_fallback(monkeypatch, mode):
    monkeypatch.setattr(hardware, "_runtime_mode", mode)
    hardware._probe_cache[GPU] = (False, "failed")
    assert hardware.get_ort_providers() == [CPU]


def test_strict_mode_rejects_cpu_preference(monkeypatch):
    monkeypatch.setenv("CITRUS_REQUIRE_GPU", "1")
    monkeypatch.setattr(hardware, "_runtime_mode", "cpu")
    assert hardware.runtime_readiness()["status"] == "unavailable"
    with pytest.raises(hardware.GPUUnavailableError, match="conflicts"):
        hardware.get_ort_providers()


def test_no_advertised_gpu_is_actionable(monkeypatch):
    monkeypatch.setenv("CITRUS_REQUIRE_GPU", "1")
    monkeypatch.setattr(hardware.ort, "get_available_providers", lambda: [CPU])
    with pytest.raises(hardware.GPUUnavailableError, match="--nv"):
        hardware.get_ort_providers()


def test_readiness_does_not_initialize_models_and_requires_real_forward(monkeypatch):
    monkeypatch.setenv("CITRUS_REQUIRE_GPU", "1")
    hardware._probe_cache[GPU] = (True, "controlled test")
    result = hardware.runtime_readiness()
    assert not result["ready"]
    assert embedding.Embedder._instance is None
    assert ranking.Reranker._instance is None
    assert result["active_model_providers"] == {}
    for cls in (embedding.Embedder, ranking.Reranker):
        monkeypatch.setattr(cls, "_instance", SimpleNamespace(
            _providers=[GPU], _shared_model=SimpleNamespace(session=Session([CPU])),
            _inference_verified=True))
    result = hardware.runtime_readiness()
    assert not result["ready"]
    assert result["active_model_providers"] == {"embedder": [CPU], "reranker": [CPU]}


def test_readiness_requires_successful_loaded_gpu_sessions(monkeypatch):
    monkeypatch.setenv("CITRUS_REQUIRE_GPU", "1")
    hardware._probe_cache[GPU] = (True, "controlled test")
    for cls in (embedding.Embedder, ranking.Reranker):
        monkeypatch.setattr(cls, "_instance", SimpleNamespace(
            _shared_model=SimpleNamespace(model=SimpleNamespace(model=Session([GPU, CPU]))),
            _inference_verified=False))
    assert not hardware.runtime_readiness()["ready"]
    embedding.Embedder._instance._inference_verified = True
    ranking.Reranker._instance._inference_verified = True
    assert not hardware.runtime_readiness()["ready"]  # loaded reranker must prove GPU compute too
    ranking.Reranker._instance._compute_providers = [GPU]
    assert hardware.runtime_readiness()["ready"]
    ranking.Reranker._instance._runtime_error = "forward failed"
    assert not hardware.runtime_readiness()["ready"]


def test_default_readiness_keeps_lazy_cpu_startup(monkeypatch):
    hardware._probe_cache[GPU] = (False, "no GPU")
    assert hardware.runtime_readiness()["ready"]


def test_cpu_thread_budget_applies_to_ort_host_work(monkeypatch):
    monkeypatch.setenv("CITRUSQA_CPU_THREADS", "3")
    options = hardware.session_options(strict=True)
    assert options.intra_op_num_threads == 3
    assert options.inter_op_num_threads == 1
    assert options.get_session_config_entry("session.disable_cpu_ep_fallback") == "1"


def make_embedder():
    instance = object.__new__(embedding.Embedder)
    instance._force_cpu = False
    instance._providers = [GPU]
    instance._needs_lock = False
    instance._shared_model = None
    instance._shared_lock = threading.Lock()
    instance._dim = 2
    instance.model_name = "controlled-test-model"
    return instance


def test_strict_embedder_overrides_legacy_force_cpu(monkeypatch):
    monkeypatch.setenv("CITRUS_REQUIRE_GPU", "1")
    hardware._probe_cache[GPU] = (True, "controlled test")
    instance = make_embedder()
    instance._force_cpu = True
    instance._load_config()
    assert instance._providers == [GPU]


@pytest.mark.parametrize("strict", [False, True])
def test_embedder_runtime_failure_cpu_retry_only_by_default(monkeypatch, strict):
    if strict:
        monkeypatch.setenv("CITRUS_REQUIRE_GPU", "1")
    instance = make_embedder()
    attempts = []

    class Model:
        session = Session([GPU])

        def embed(self, texts):
            attempts.append(list(instance._providers))
            if instance._providers == [GPU]:
                raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "provider error")
            return [np.array([1.0, 2.0])]

    instance._get_model = lambda: Model()
    if strict:
        with pytest.raises(hardware.GPUUnavailableError, match="CPU retry is disabled"):
            instance._safe_embed(["test"])
        assert attempts == [[GPU]]
        assert not instance._inference_verified
    else:
        assert instance._safe_embed(["test"]) == [[1.0, 2.0]]
        assert attempts == [[GPU], [CPU]]


def test_strict_embedder_load_checks_actual_session_and_forwards_even_with_known_dim(monkeypatch, tmp_path):
    monkeypatch.setenv("CITRUS_REQUIRE_GPU", "1")
    monkeypatch.setattr(embedding, "MODEL_CACHE_ROOT", tmp_path)
    captured = {}
    calls = []
    model = SimpleNamespace(model=SimpleNamespace(model=Session([GPU, CPU])),
                            embed=lambda texts: calls.append(texts) or [np.array([1.0, 2.0])])
    monkeypatch.setattr(embedding, "TextEmbedding", lambda **kwargs: captured.update(kwargs) or model)
    instance = make_embedder()
    assert instance._create_session() is model
    assert calls == [["validate"]]
    assert instance._inference_verified
    assert captured["local_files_only"] is True
    assert model.model.model.fallback_disabled


def make_reranker():
    instance = object.__new__(ranking.Reranker)
    instance._force_cpu = False
    instance._providers = [GPU]
    instance._needs_lock = False
    instance._shared_model = None
    instance._shared_tokenizer = None
    instance._shared_lock = threading.Lock()
    instance._profile_pending = False
    return instance


@pytest.mark.parametrize("strict", [False, True])
def test_reranker_runtime_failure_cpu_retry_only_by_default(monkeypatch, strict):
    if strict:
        monkeypatch.setenv("CITRUS_REQUIRE_GPU", "1")
    instance = make_reranker()
    attempts = []

    class Model:
        session = Session([GPU])

        def __call__(self, **inputs):
            attempts.append(list(instance._providers))
            if instance._providers == [GPU]:
                raise RuntimeError("CUDA failed")
            return SimpleNamespace(logits=np.array([[1.0]]))

    instance._get_model = lambda: (Model(), lambda *args, **kwargs: {})
    if strict:
        with pytest.raises(hardware.GPUUnavailableError, match="CPU retry is disabled"):
            instance.rerank("query", [{"text": "test"}])
        assert attempts == [[GPU]]
        assert not instance._inference_verified
    else:
        assert instance.rerank("query", [{"text": "test"}])[0]["rerank_score"] > 0
        assert attempts == [[GPU], [CPU]]


def test_strict_reranker_cache_missing_never_exports_or_downloads(monkeypatch, tmp_path):
    monkeypatch.setenv("CITRUS_REQUIRE_GPU", "1")
    instance = make_reranker()
    instance._cache_dir = tmp_path / "missing"
    with pytest.raises(hardware.GPUUnavailableError, match="stage the exported ONNX"):
        instance._create_session()


def test_reranker_rejects_profile_without_gpu_compute(monkeypatch):
    monkeypatch.setenv("CITRUS_REQUIRE_GPU", "1")
    instance = make_reranker()
    instance._profile_pending = True
    model = SimpleNamespace(session=Session([GPU]))

    class Model:
        session = model.session
        def __call__(self, **kwargs):
            return SimpleNamespace(logits=np.array([[1.0]]))

    instance._get_model = lambda: (Model(), lambda *args, **kwargs: {})
    monkeypatch.setattr(ranking, "profile_compute_providers", lambda session: [CPU])
    for _ in range(2):  # a consumed failed profile must never become success on the next call
        with pytest.raises(hardware.GPUUnavailableError, match="GPU compute was not observed"):
            instance.rerank("query", [{"text": "test"}])
        assert not instance._inference_verified


@pytest.mark.parametrize("actual", [[CPU], [GPU, CPU]])
def test_strict_reranker_load_verifies_session_and_sets_thread_budget(monkeypatch, tmp_path, actual):
    monkeypatch.setenv("CITRUS_REQUIRE_GPU", "1")
    monkeypatch.setenv("CITRUSQA_CPU_THREADS", "2")
    instance = make_reranker()
    instance._cache_dir = tmp_path
    captured = {}
    model = SimpleNamespace(session=Session(actual))
    monkeypatch.setattr(ranking, "AutoTokenizer", SimpleNamespace(
        from_pretrained=lambda *args, **kwargs: SimpleNamespace()))
    monkeypatch.setattr(ranking, "ORTModelForSequenceClassification", SimpleNamespace(
        from_pretrained=lambda *args, **kwargs: captured.update(kwargs) or model))
    if actual[0] == CPU:
        with pytest.raises(hardware.GPUUnavailableError, match="actual ONNX session providers"):
            instance._create_session()
    else:
        assert instance._create_session()[0] is model
        assert instance._profile_pending
        assert model.session.fallback_disabled
    assert captured["local_files_only"] is True
    assert captured["session_options"].intra_op_num_threads == 2
    assert captured["session_options"].inter_op_num_threads == 1
    assert captured["session_options"].enable_profiling


def test_strict_reranker_success_keeps_profile_evidence_for_later_forwards(monkeypatch):
    monkeypatch.setenv("CITRUS_REQUIRE_GPU", "1")
    instance = make_reranker()
    instance._profile_pending = True

    class Model:
        session = Session([GPU, CPU])
        def __call__(self, **kwargs):
            return SimpleNamespace(logits=np.array([[1.0]]))

    instance._get_model = lambda: (Model(), lambda *args, **kwargs: {})
    monkeypatch.setattr(ranking, "profile_compute_providers", lambda session: [GPU])
    for _ in range(2):
        assert instance.rerank("query", [{"text": "test"}])[0]["rerank_score"] > 0
        assert instance._inference_verified
        assert instance._compute_providers == [GPU]


def test_profile_parser_does_not_count_gpu_memory_copies_as_compute(tmp_path):
    path = tmp_path / "profile.json"
    path.write_text(json.dumps([
        {"cat": "Node", "args": {"provider": GPU, "op_name": "MemcpyFromHost"}},
        {"cat": "Node", "args": {"provider": CPU, "op_name": "Gemm"}},
    ]), encoding="utf-8")
    assert hardware.profile_compute_providers(SimpleNamespace(end_profiling=lambda: str(path))) == [CPU]


def test_real_small_onnx_cpu_profile_reports_executed_cpu(monkeypatch, tmp_path):
    """Real local CPU inference validates profile parsing, with no GPU claim."""
    pytest.importorskip("onnx")
    monkeypatch.setattr(hardware, "PROJECT_ROOT", tmp_path)
    path = hardware._probe_model_path()
    options = hardware.session_options()
    options.enable_profiling = True
    options.profile_file_prefix = str(tmp_path / "real-profile")
    session = hardware.ort.InferenceSession(str(path), providers=[CPU], sess_options=options)
    data = np.ones((4, 8), dtype=np.float32)
    assert np.allclose(session.run(None, {"x": data})[0], data * 0.5)
    assert hardware.profile_compute_providers(session) == [CPU]
