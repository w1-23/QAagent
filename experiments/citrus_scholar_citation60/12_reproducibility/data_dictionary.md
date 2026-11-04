# 输出字段字典

核心 E01 与补充 E01 是两套独立数据链。核心 `qa60_LOCKED.csv`、统一有效性、机器判官原始响应和 `qa_judge_aggregated.csv` 用于答案质量、拒答和失败评价；补充 `citation60` 的文件、run plan、citation observations 和分析表只用于 target-chunk 引用定位。两套题号、run_id、评分分母和统计结果不得合并。

`citation60/chunks_FROZEN.jsonl`: 一行一个冻结 chunk。`citation_id` 是离线审计 ID；`chunk_id`、`paper_id`、`source_file`、`source_registry_sha256` 和 `chunk_text_sha256` 共同记录来源；`freeze_record_sha256` 固定除自身外的记录内容。该文件不得成为运行 prompt。

`citation60/questions_FROZEN.jsonl`: 一行一题，`question_id=citation_id`；`target_chunk_id` 必须与 chunk 文件的 `chunk_id` 唯一对应；`one_chunk_only=true`；`question_sha256` 是规范化题干哈希；`freeze_record_sha256` 是该题冻结记录的哈希。target 字段只供离线评分/审计。

`06_runs/manifests/citation_run_plan.csv`: 每行是 `question×condition`。`prompt` 只能含题干、允许语料标签和引用要求；`source_scope` 固定为 `仅本地:<corpus_id>`；不得出现 target chunk/document/text、作者锚点或评审备注。`run_order` 由固定 seed 产生。

`citation_hit_observations.csv`: `target_chunk_cited` 只依据最终 `citations`/`sources` 是否明确出现冻结 chunk ID；`target_document_cited` 依据 paper ID/source file 或 chunk 命中；`allowed_corpus_cited` 依据目标语料标识；`wrong_corpus_cited` 依据其他四类语料的明确标识；`no_citation` 只在最终引用和可核验引用信号均缺失时为 1。该表不包含 semantic-support 字段。

不生成 `citation_hit_observations_rater1.csv`、`rater2.csv`、`citation_hit_adjudicated.csv` 或 `semantic_support`。当前协议明确不报告语义支持率。

`07_scoring/judge/qa_judge_aggregated.csv`: 核心 QA60 的正式机器判官聚合文件，每题×B0/B1/B2一行；`judge_1_decision`、`judge_2_decision`、必要时的 `judge_3_decision` 和 `final_decision` 可追溯；`egts` 由 `final_decision=PASS` 映射为 1，否则为 0；判官缺失或错误必须保留 `aggregation_error`，不得静默填 0。

`07_scoring/validity/question_validity_review_packet.csv` 与
`question_validity_adjudication_TEMPLATE.csv` 是空白模板，不含真实评审。
实际评审写入版本化的 `question_validity_review_WORKING.csv` 和
`question_validity_adjudication_WORKING.csv`：120 题×2 名评审=240 条，
每行记录五项有效性 0/1、评审决定、reviewer 假名、UTC 和题面 SHA。
`question_validity_LOCKED.csv` 只有在 120 题全部 ACCEPT 后才能写一次。

`intent_observations.csv`: 每一单意图题 `available_1_0` 表示本次必要服务可用；`required_met_1_0` 为所有必需的真实工具组已执行且符合请求；`forbidden_violated_1_0` 为任一禁用工具组实际执行；`observed_primary_group` 由工具轨迹规则确定，可填 GENERAL, LOCAL, WEB, FUSION, PDF_READ, TABLE_AUDIT, VERIFY_REFS, EXPORT, MIXED, NONE, OTHER，但它是**动作观察类别**。

`composite_observations.csv`: `observed_groups`用`|`分隔，例如`local|web`；无实际调用写`NONE`；不应写文本“成功”。

`workflow_acceptance.csv`: `observed_1_0_NA`为1通过/0失败/NA明确不适用；无法验证证据应0而非NA；`evidence_path`必须指真实来源文件或截图；critical属性决定该案例能否算完成。

`06_runs/raw/...json`: 每次请求真实prompt、来源范围、附件、时间、模型与参数哈希、原始回答与最后回答、工具事件、错误与产物引用、真实成本/时延；密码、Cookie 和个人认证信息不得落盘。

`06_runs/simulated_dry_run/` 与 `09_analysis/simulated_dry_run/`：接口联调专用
合成数据。每条记录固定包含 `evidence_class=SIMULATED_DRY_RUN` 和
`not_for_publication_claims=true`。这些数字不得进入正式统计、论文 Results、
摘要或图表。

`synthetic_leaf_qPCR_WF07.xlsx`: 纯虚构叶片元数据与检测记录，不代表真实感染实验，也不可用于推断感染/抗病性。`synthetic_leaf_qPCR_truth.csv`: 10条人为设定数据问题，其中一些是领域/跨表问题并非既有通用审查规则必检。`synthetic_qPCR_audit.xlsx`: 另一套原版模拟测试（7条已知问题）。
