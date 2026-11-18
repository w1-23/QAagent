# 检索合同与参数

索引 manifest 为 generation/schema 2、1024 维、E5 `query:`/`passage:` 前缀。allowed_corpus_ids 为显式许可集合；空集合对应无可检索语料，未知 ID 拒绝；两者都不解释为所有语料。只读已存在索引，不重建旧索引，不读取不可信 pickle。

| 配置字段 | 默认 | 实际调用 | 运行诊断 |
| --- | --- | --- | --- |
| top_k_vector | 40 | LanceCorpus.dense_search | dense_candidates |
| top_k_bm25 | 40 | LanceCorpus.bm25_search | bm25_candidates |
| rrf_k / dense_weight / bm25_weight | 60 / 1 / 1 | HybridSearch weighted RRF | rrf_candidates 与 parameters |
| candidate_window | 20 | RRF 后精排窗口 | rrf_candidates |
| top_k_final | 10 | rerank 输出 | parameters |
| adjacent_decay | 0.6 | 同文档相邻段落扣分 | decay_filtered |
| max_passages_per_paper | 2 | 最终同文档限额 | parameters |
| limit | 6，硬上限6 | 小观察壳 | 实际 candidates 数 |
| reranker batch_size / max_length | 8 / 512 | OptimumReranker | 显式模型适配配置 |
| race concurrency / min_chars | 2 / 1500 | fetch_race | attempts / elapsed_ms |

包 API 可注入 RetrievalParams；CLI 当前提供基线参数，不声称 CLI 已暴露每项开关。参数对照不证明与旧的两条混合排序路径数值等同；旧路径兼容接线与排序对照在 P2 验证。

embedding、reranker 和 LanceDB 连接按执行惰性初始化。模型目录必须已有本地文件，禁止隐式下载或 CPU 回退。Windows 与 Linux 的 provider 安装约束分别处理。

大语料 BM25 当前按选定 corpus 在内存建倒排；未声称与原懒加载语料路径内存表现相同。实际测试的语料规模在验收记录中列出。
