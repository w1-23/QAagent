from src.engine.embedder import Embedder


def test_passage_prefix_is_exactly_once_and_embed_docs_stays_unprefixed():
    embedder = object.__new__(Embedder)
    seen = []

    def capture(texts):
        seen.extend(texts)
        return [[float(i)] for i, _ in enumerate(texts)]

    embedder.embed_docs = capture
    embedder.embed_passages(["alpha", "passage: beta", "passage: passage: gamma", ""])
    assert seen == ["passage: alpha", "passage: beta", "passage: gamma", ""]

    seen.clear()
    embedder.embed_docs(["alpha"])
    assert seen == ["alpha"]


def test_query_prefix_is_exactly_once():
    embedder = object.__new__(Embedder)
    embedder._query_cache = {}
    embedder._cache_lock = __import__("threading").Lock()
    seen = []

    def capture(texts):
        seen.extend(texts)
        return [[1.0, 0.0] for _ in texts]

    embedder.embed_docs = capture
    embedder.embed_query("query: query: citrus")
    assert seen == ["query: citrus"]


def test_model_uses_persistent_fastembed_cache(monkeypatch, tmp_path):
    import src.engine.embedder as module

    captured = {}

    class FakeModel:
        def embed(self, texts):
            return [[0.0, 1.0] for _ in texts]

    def fake_text_embedding(**kwargs):
        captured.update(kwargs)
        return FakeModel()

    monkeypatch.setattr(module, "TextEmbedding", fake_text_embedding)
    monkeypatch.setattr(module, "MODEL_CACHE_ROOT", tmp_path)
    embedder = object.__new__(Embedder)
    embedder._force_cpu = True
    embedder._providers = ["CPUExecutionProvider"]
    embedder._dim = None
    embedder.model_name = "intfloat/multilingual-e5-large"

    model = embedder._create_session()

    assert isinstance(model, FakeModel)
    assert captured["cache_dir"] == str(tmp_path / "fastembed")
    assert (tmp_path / "fastembed").is_dir()
