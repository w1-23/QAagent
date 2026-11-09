# -*- coding: utf-8 -*-
"""模型预下载/预导出。

首次运行把检索模型下载/导出到项目内缓存（agent/.hf_cache，已在 .gitignore，
不入仓库），此后启动秒级就绪：
  1. 向量编码模型  intfloat/multilingual-e5-large（fastembed）
  2. 重排模型      BAAI/bge-reranker-v2-m3（ONNX 导出，~1-2GB，可选跳过）

用法:  python prepare_models.py [--skip-reranker]
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


def step(n, msg):
    print(f"\n[{n}] {msg}", flush=True)


def prepare_embedding() -> None:
    step("1/2", "下载向量编码模型 intfloat/multilingual-e5-large（首次约 2-5 分钟）...")
    t0 = time.time()
    from fastembed import TextEmbedding
    TextEmbedding(model_name="intfloat/multilingual-e5-large")
    print(f"    ✓ 向量编码模型就绪（{time.time() - t0:.0f}s）", flush=True)


def prepare_reranker() -> None:
    cache_dir = CACHE / "onnx_reranker"
    if (cache_dir / "model.onnx").exists():
        print("    ✓ 重排模型已存在，跳过", flush=True)
        return
    step("2/2", "导出重排模型 BAAI/bge-reranker-v2-m3 为 ONNX（首次约 5-15 分钟，"
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


if __name__ == "__main__":
    print("CitrusQA 模型准备", flush=True)
    prepare_embedding()
    if not SKIP_RERANKER:
        prepare_reranker()
    print("\n全部就绪 ✓ 现在可以启动服务了。", flush=True)
