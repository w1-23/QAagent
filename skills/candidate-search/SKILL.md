---
name: candidate-search
description: 组合本地混合召回、网页来源和同篇多路获取，先返回少量候选，再以持久证据引用直接读取原文。
license: MIT
metadata:
  version: "2.0.0"
---

# 候选检索与证据回读

目标是取得可回读资料，主 Agent 负责检索策略与最终解释。支持包不生成终答、不启动 Agent；本地检索和网页检索互补，同篇论文的多个获取来源才竞速。

独立运行依赖 `citrusscholar-support[retrieval]==2.0.0`。该 extra 不自动装模型运行时，避免将 CPU ORT 装进 GPU 环境；重排另用 retrieval-models extra，E5 单独选择 fastembed/fastembed-gpu 0.8.0 和相容 ONNX provider。Linux CUDA 可按原 requirements-gpu.txt，Windows DirectML 保留专属环境。不要同时安装 CPU/GPU ORT 发行。资源与参数见 references。所有数据、模型、证据目录和作用域显式传入，不读取平台密钥或默认语料。

## 选择路线

- 本地英文索引：将检索意图写成英文，保留专业名和精确标识。仅这条路线需要英文；翻译失败说明 translation_error，不视为零命中。
- 网页/原文抓取/中文原题名：保留原文。通过宿主已授权的 web/fetch 工具，或显式配置的库回调执行；此独立 CLI 不捏造网页搜索服务。
- DOI、PMID、PMCID、arXiv 等可用标识：优先直接取得对象；候选相关不等于引文身份已确认。需要核对时读 reference-verification 方法。

## 本地与读取入口

`python scripts/search_local.py "English research query" --data-root DATA --model-root MODELS --corpora CORPUS_ID --evidence-dir EVIDENCE --owner OWNER --session SESSION --turn TURN`

GPU 默认使用 Windows DirectML。Linux 可用 `--provider CUDAExecutionProvider` 或相容已安装 provider；`--allow-cpu` 显式选 CPU。缺 provider 不隐式回退。`--without-rerank` 会在诊断中明确未精排，不能写成完整双模型结果。

首返最多 6 条，每条 snippet≤240 字符，不含全文。读取返回的 ref：

`python scripts/read_evidence.py --ref REF --generation GENERATION --evidence-dir EVIDENCE --owner OWNER --session SESSION --turn TURN --offset 0 --max-chars 4000`

直接按 ref/paper_id/chunk_id 读取，不要求先 inspect。保存文件绑定 owner/session/turn/index generation，失效返回 evidence_expired，不能重搜后冒充同一证据。不要把另一个用户的作用域参数用于读取。

## 解释证据

逐项区分候选、已获取、已交付模型和已引用。passage/abstract/full_text 等 evidence_level 原样保留；摘要不能称全文。来源包含十个键；引用气泡应使用持久记录，不能现搜补全缺项。

竞速响应中的 cancelled_after_success/not_awaited_after_success 表示停止等待的事实；Future.cancel 不保证 HTTP 已中止。timeout/error/no_match 分开说明。失败历史不能被一次成功抹除。
