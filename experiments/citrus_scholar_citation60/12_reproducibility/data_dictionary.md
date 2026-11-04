# 输出字段字典

核心 E01 与补充 E01 是两套独立数据链。核心 `qa60_LOCKED.csv`、Gold/证据定位、`qa_score_template_180.csv`、`qa_scores_*` 与 `qa_scores_adjudicated.csv` 继续用于 QA60/EGTS、拒答和失败评价；补充 `citation60` 的文件、run plan、citation observations 和分析表只用于 target-chunk 引用定位。两套题号、run_id、评分分母和统计结果不得合并。以下逐项说明补充实验及仍有效的核心评分字段。

`citation60/chunks_FROZEN.jsonl`: 一行一个冻结 chunk。`citation_id` 是离线审计 ID；`chunk_id`、`paper_id`、`source_file`、`source_registry_sha256` 和 `chunk_text_sha256` 共同记录来源；`freeze_record_sha256` 固定除自身外的记录内容。该文件不得成为运行 prompt。

`citation60/questions_FROZEN.jsonl`: 一行一题，`question_id=citation_id`；`target_chunk_id` 必须与 chunk 文件的 `chunk_id` 唯一对应；`one_chunk_only=true`；`question_sha256` 是规范化题干哈希；`freeze_record_sha256` 是该题冻结记录的哈希。target 字段只供离线评分/审计。

`06_runs/manifests/citation_run_plan.csv`: 每行是 `question×condition`。`prompt` 只能含题干、允许语料标签和引用要求；`source_scope` 固定为 `仅本地:<corpus_id>`；不得出现 target chunk/document/text、作者锚点或 Gold 答案。`run_order` 由固定 seed 产生。

`citation_hit_observations.csv`: `target_chunk_cited` 只依据最终 `citations`/`sources` 是否明确出现冻结 chunk ID；`target_document_cited` 依据 paper ID/source file 或 chunk 命中；`allowed_corpus_cited` 依据目标语料标识；`wrong_corpus_cited` 依据其他四类语料的明确标识；`no_citation` 只在最终引用和可核验引用信号均缺失时为 1。自动定位不等于语义支持。

`citation_hit_observations_rater1.csv` / `rater2.csv` / `citation_hit_adjudicated.csv`: 如报告语义支持，使用锁定 manifest 中每语料 4 题×3 条件的 60-run 分层样本。人工 `semantic_support` 使用 `1` 完全支持、`0.5` 部分支持/有外推、`0` 不支持或答非所问；必须保留 reviewer、comments、UTC 和独立原始判断。不得用自动命中列回填人工列。

`qa_scores_adjudicated.csv`: 核心 QA60 的正式评分文件，每题×B0/B1/B2一行；`egts` 0/1，`slot_correct`与`slot_total`均为非负整数且前者不大于后者；引用数同理；`duration_s`真实墙钟秒；`error_type`使用人工分类；`source_cluster`用于文献簇敏感性。不得用补充 `citation_hit_observations.csv` 回填或替代。

`intent_observations.csv`: 每一单意图题`available_1_0`表示本次必要服务可用；`required_met_1_0`为所有必需的真实工具组已执行且符合请求；`forbidden_violated_1_0`为任一禁用工具组实际执行；`observed_primary_group`是人工从工具轨迹确定的主要执行组，可填GENERAL, LOCAL, WEB, FUSION, PDF_READ, TABLE_AUDIT, VERIFY_REFS, EXPORT, MIXED, NONE, OTHER，但它是**动作观察类别**。

`composite_observations.csv`: `observed_groups`用`|`分隔，例如`local|web`；无实际调用写`NONE`；不应写文本“成功”。

`workflow_acceptance.csv`: `observed_1_0_NA`为1通过/0失败/NA明确不适用；无法验证证据应0而非NA；`evidence_path`必须指真实来源文件或截图；critical属性决定该案例能否算完成。

`06_runs/raw/...json`: 每次请求真实prompt、来源范围、附件、时间、模型与参数哈希、原始回答与最后回答、工具事件、错误与产物引用、真实成本/时延；密码或未授权个人资料不得落盘。

`synthetic_leaf_qPCR_WF07.xlsx`: 纯虚构叶片元数据与检测记录，不代表真实感染实验，也不可用于推断感染/抗病性。`synthetic_leaf_qPCR_truth.csv`: 10条人为设定数据问题，其中一些是领域/跨表问题并非既有通用审查规则必检。`synthetic_qPCR_audit.xlsx`: 另一套原版模拟测试（7条已知问题）。
