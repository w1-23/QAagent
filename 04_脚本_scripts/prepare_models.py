# -*- coding: utf-8 -*-
"""v8.18.0 模型预下载/预导出——run.ps1 首次启动自动调用。

解决"模型下载慢"的痛点：首次运行把模型下载/导出到项目内缓存
（agent/.hf_cache 与 agent/models，已在 .gitignore，不入仓库），此后启动秒级就绪：
  1. 向量编码模型  intfloat/multilingual-e5-large（fastembed）
  2. 重排模型      BAAI/bge-reranker-v2-m3（ONNX 导出，~1-2GB，可选跳过）
  3. 病害图像初筛模型（3 个 ONNX，共约 525MB，可选跳过）

用法:  python prepare_models.py [--skip-reranker] [--skip-vision]
"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "agent"          # 运行期数据根
CACHE = ROOT / ".hf_cache"
CACHE.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("FASTEMBED_CACHE_PATH", str(CACHE / "fastembed"))
os.environ.setdefault("HF_HOME", str(CACHE / "hf"))
os.environ.setdefault("TRANSFORMERS_CACHE", str(CACHE / "hf"))
# v8.5.0 镜像：模型统一走 HuggingFace 国内镜像（hf-mirror.com），
# 加速首次下载；取消该环境变量即回官方源
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

SKIP_RERANKER = "--skip-reranker" in sys.argv
SKIP_VISION = "--skip-vision" in sys.argv

# 病害图像初筛：3 个公开预训练权重导出的 ONNX（详见 docs/vision.md）
VISION_ASSETS = (
    "citrus_convnext.onnx", "citrus_convnext.json",
    "dima_orange_vit.onnx", "dima_orange_vit.json",
    "plantvillage_mnv2.onnx", "plantvillage_mnv2.json",
)
VISION_RELEASE = os.getenv(
    "CITRUSQA_VISION_RELEASE",
    "https://github.com/w1-23/QAagent/releases/download/vision-v1",
)


def step(n, msg):
    print(f"\n[{n}] {msg}", flush=True)


def _download(url: str, target: Path, timeout: int = 900) -> None:
    import shutil
    import urllib.request

    request = urllib.request.Request(url, headers={"User-Agent": "citrusqa-prepare/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response, \
            open(target, "wb") as handle:
        shutil.copyfileobj(response, handle, length=1024 * 1024)


def prepare_embedding() -> None:
    step("1/3", "下载向量编码模型 intfloat/multilingual-e5-large（首次约 2-5 分钟）...")
    t0 = time.time()
    from fastembed import TextEmbedding
    TextEmbedding(model_name="intfloat/multilingual-e5-large")
    print(f"    ✓ 向量编码模型就绪（{time.time() - t0:.0f}s）", flush=True)


def prepare_reranker() -> None:
    cache_dir = CACHE / "onnx_reranker"
    if (cache_dir / "model.onnx").exists():
        print("    ✓ 重排模型已存在，跳过", flush=True)
        return
    step("2/3", "导出重排模型 BAAI/bge-reranker-v2-m3 为 ONNX（首次约 5-15 分钟，"
                "模型较大约 2GB；如网络慢可 Ctrl+C 后用 --skip-reranker 跳过，"
                "检索将使用基础阈值降级）...")
    t0 = time.time()
    from transformers import AutoTokenizer
    from optimum.onnxruntime import ORTModelForSequenceClassification
    model = ORTModelForSequenceClassification.from_pretrained(
        "BAAI/bge-reranker-v2-m3", export=True)
    model.save_pretrained(cache_dir)
    tok = AutoTokenizer.from_pretrained("BAAI/bge-reranker-v2-m3")
    tok.save_pretrained(cache_dir)
    print(f"    ✓ 重排模型导出完成（{time.time() - t0:.0f}s）", flush=True)


def prepare_vision() -> None:
    """下载病害图像初筛模型（3 个 ONNX 共约 525MB）。

    模型文件不入仓库：来源与许可证见 docs/vision.md，发布地址可用环境变量
    CITRUSQA_VISION_RELEASE 覆盖（例如企业内网镜像）。下载失败不影响问答主链，
    `/api/v2/vision/status` 会如实报告缺哪个模型。
    """
    dest = Path(os.getenv("VISION_MODEL_DIR") or (ROOT / "models" / "vision"))
    dest.mkdir(parents=True, exist_ok=True)
    pending = [name for name in VISION_ASSETS if not (dest / name).is_file()]
    if not pending:
        print("    ✓ 病害图像模型已存在，跳过", flush=True)
        return
    step("3/3", f"下载病害图像初筛模型（{len(pending)} 个文件，约 525MB；"
                "网络慢可 Ctrl+C 后用 --skip-vision 跳过）...")
    t0 = time.time()
    for name in pending:
        target = dest / name
        try:
            _download(f"{VISION_RELEASE}/{name}", target)
            size_mb = target.stat().st_size / (1024 * 1024)
            print(f"    ✓ {name}（{size_mb:.1f} MB）", flush=True)
        except Exception as exc:  # noqa: BLE001
            if target.exists():
                target.unlink()
            if name.endswith(".json"):
                # 元数据只用于人工核对（类别/许可/来源），标签与归一化参数在代码里，
                # 缺了不影响推理，不要因此判定整个模型不可用。
                print(f"    ⚠ {name} 元数据缺失（{exc}）——推理不受影响", flush=True)
            else:
                print(f"    ⚠ {name} 下载失败（{exc}）——图像初筛将降级跳过", flush=True)
    print(f"    ✓ 图像模型目录：{dest}（{time.time() - t0:.0f}s）", flush=True)


if __name__ == "__main__":
    print("CitrusQA 模型准备（v8.18.0）", flush=True)
    prepare_embedding()
    if not SKIP_RERANKER:
        prepare_reranker()
    if not SKIP_VISION:
        prepare_vision()
    print("\n全部就绪 ✓ 现在可以启动服务了。", flush=True)
