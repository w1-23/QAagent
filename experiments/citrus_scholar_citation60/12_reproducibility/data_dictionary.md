# 输出字段字典（v4.4.0）

版本：`4.4.0`
状态：`AWAITING_EXTERNAL_GPT_REVIEW_V4_4`

所有原始证据必须保留，但仓库纳入和对外发布必须分别按 `L0/L1/L2` 审核：

- `L0` API Key、Cookie、密码、token、认证头和私有身份认证数据：禁止进入 Git、
  日志、评分包、请求归档、截图或论文补充材料；
- `L1` 未获再分发许可的全文/受限摘录、私有 SOP、身份映射和原始敏感日志：只在
  服务器受控存储；获批的公开派生件只保留 source SHA、locator、访问类别、redaction log 和
  public derivative SHA；
- `L2` 可公开题面、许可证据定位、脱敏运行、SHA、盲化评分、统计、图和论文材料：
  只有通过文件级 release 检查后才允许进入私有 GitHub；公开分发另行批准。

“完整留痕”不等于“完整公开”。任何 `L1` 缺失都必须在公开可用性声明中如实说明。

核心 E01 与补充 E01 是两套独立数据链。核心 `qa60_LOCKED.csv`、统一有效性、两份独立专家原始评分、第三专家仲裁、人类共识表、机器判官原始响应和机器聚合分别用于答案质量、拒答和失败评价。确认性系统终点是 `egts_system_outcome`，人类语义终点 `human_consensus_egts` 单独报告并披露可评分覆盖率。补充 `citation60` 的文件、run plan、citation observations 和分析表只用于 target-chunk 引用定位。两套题号、run_id、评分分母和统计结果不得合并。

`citation60/chunks_FROZEN.jsonl`: 一行一个冻结 chunk。完整 `chunk_text` 属于
`L1`，只允许保存在服务器受控目录，不得进入 Git，即使仓库为私有。版本化仓库树中的
`L1_CONTROLLED_ASSET_MANIFEST.json` 记录 former path、bytes、SHA-256、
source/locator 和 release status；`chunk_manifest.csv` 只保留来源与文本 SHA。
这些文件均不得成为运行 prompt。

`citation60/question_authoring_packet.jsonl`: 包含完整 `chunk_text` 的离线出题包，
属于独立登记的 `L1` 资产，只允许保存在服务器受控目录。Git 仓库树不保存该文件。

`citation60/questions_FROZEN.jsonl`: 一行一题，`question_id=citation_id`；`target_chunk_id` 必须与 chunk 文件的 `chunk_id` 唯一对应；`one_chunk_only=true`；`question_sha256` 是规范化题干哈希；`freeze_record_sha256` 是该题冻结记录的哈希。该文件在 G12 完成前属于受控 `L1`，Git 仓库树不得保存。

`06_runs/manifests/citation_run_plan.csv`: 每行是 `question×condition`。`prompt` 只能含题干、允许语料标签和引用要求；`source_scope` 固定为 `仅本地:<corpus_id>`；不得出现 target chunk/document/text、作者锚点或评审备注。`run_order` 由固定 seed 产生。G12 前该运行计划属于受控 `L1`。

`citation_hit_observations.csv`: `target_chunk_cited` 只依据最终 `citations`/`sources` 是否明确出现冻结 chunk ID；`target_document_cited` 依据 paper ID/source file 或 chunk 命中；`allowed_corpus_cited` 依据目标语料标识；`wrong_corpus_cited` 依据其他四类语料的明确标识；`no_citation` 只在最终引用和可核验引用信号均缺失时为 1。该表不包含 semantic-support 字段。

不生成 `citation_hit_observations_rater1.csv`、`rater2.csv`、`citation_hit_adjudicated.csv` 或 `semantic_support`。当前协议明确不报告语义支持率。

`07_scoring/expert/expert_scoring_TEMPLATE.csv`: 固定字段为
`batch_id,blind_id,reviewer_id,score_version,answerability_correct,answer_correct,evidence_traceable,fatal_error,reason_codes_by_field_json,short_reason,answer_span_quote,evidence_span_locators_json,score_status,issue_id,reviewed_utc`。
`score_status` 只允许 `SCORED`、`UNRATEABLE`、`MISSING`、`INVALID`。
`UNANSWERABLE` 的 `answer_correct` 和 `evidence_traceable` 必须是
`not_applicable`；专家不填写 EGTS 或 PASS/FAIL。
`reason_codes_by_field_json` 必须含四个字段键，理由码只放入其解释的字段数组。

`07_scoring/expert/expert_adjudication_TEMPLATE.csv`: 每个争议字段一行，包含
两位专家原值、仲裁者、仲裁值、固定 reason code、短理由、回答片段、证据定位和
仲裁记录 ID。两位一致字段不进入仲裁；不得使用多数票、平均值或 LLM 替代。

`07_scoring/expert/score_issue_log_TEMPLATE.csv`: 原始评分不可覆盖。修订记录必须
包含原评分记录 ID、被修改字段、原值、新值、修订原因、修订人、修订 UTC 和
批准/审核记录。修订是追加记录，不是原地编辑。

`07_scoring/expert/human_consensus_TEMPLATE.csv`: 每个计划 run 一行，连接两位
专家原始评分、必要字段仲裁、输入 SHA、packet manifest SHA、协议版本和确定性
人类共识 EGTS。`internal_run_key`、`internal_question_key`、`condition_internal`
与盲映射属于 `L1`，只在受控聚合环境使用。真实语义回答的 `human_consensus_egts`
只能是 0/1；真实基础设施终态的该字段为空，语义字段为
`not_scored_infrastructure`，并由 `egts_system_outcome=0` 表示系统任务失败。
`egts_system_outcome` 是意向运行层面的确定性终点：语义 EGTS 为 1 时为 1，
语义 EGTS 为 0 或基础设施终态时为 0；不得用它伪造语义评分。

`run_status` 为 `completed/partial/no_results/failed/cancelled/skipped`；
`failure_class` 单列 `MODEL_EMPTY_ANSWER/MODEL_CORRECT_REFUSAL/TOOL_FAILURE/
SERVICE_UNAVAILABLE/TRANSPORT_FAILURE/BUDGET_VIOLATION/RAW_RUN_MISSING`。各状态的专家包、
EGTS、citation observation、分母和 strict 规则见
`09_analysis/FAILURE_DENOMINATOR_MATRIX.md`。基础设施失败不得伪装成
`answer_correct=false` 或 `fatal_error=true`。

`07_scoring/judge/qa_judge_aggregated.csv`: 独立机器测量，每个 run 一行，连接
J1/J2 原始响应、必要 J3、实际 model/response ID、usage/cost/allowance、SHA 和
确定性机器 EGTS。机器输出不得含模型自行生成的 `decision`/PASS/FAIL，也不得覆盖
人类表。

J1/J2 固定输出
`answerability_correct, answer_correct, evidence_traceable, fatal_error,
reason_codes_by_field, short_reason, answer_span_quote,
evidence_span_locators`。字段级 false 和 fatal=true 需要对应 reason code、
非空短理由和非空 answer span（仅 `NO_RESPONSE` 可空）；`fatal_error=false`
是普通无致命错误结果，本身不要求 reason/quote。J3 只输出
`disputed_values`、对应字段理由、短理由、answer span 和 locator；任何
`decision`/score/PASS/FAIL 字段均为 invalid schema。

`strict-human` 只在真实 run 或合法基础设施终态、两份完整专家评分（仅对需要语义
评分的 run）和所有必要仲裁存在时通过；`strict-judge` 只在每个
`completed/partial/no_results` run 的 J1/J2 与必要 J3 完整、且
`failed/cancelled/skipped` 有合法基础设施记录时通过；`strict-citation` 只在
180 条确定性观察完整时通过；`strict-all` 要求所有声明模块完整。机器判官缺失
不得阻止完整的人类主结果，但必须使 `strict-all` 失败。任一缺失/错误保留状态，
禁止静默填 0 或删行。

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

未审查的 raw run、SSE、tool trace、截图、artifact、download check、failure
和 retry 默认是 `L1`。只有逐文件脱敏、许可、SHA 和发布审查后的衍生副本才是
`L2`。

`06_runs/simulated_dry_run/` 与 `09_analysis/simulated_dry_run/`：接口联调专用
合成数据。每条记录固定包含 `evidence_class=SIMULATED_DRY_RUN` 和
`not_for_publication_claims=true`。这些数字不得进入正式统计、论文 Results、
摘要或图表。

`synthetic_leaf_qPCR_WF07.xlsx`: 纯虚构叶片元数据与检测记录，不代表真实感染实验，也不可用于推断感染/抗病性。`synthetic_leaf_qPCR_truth.csv`: 10条人为设定数据问题，其中一些是领域/跨表问题并非既有通用审查规则必检。`synthetic_qPCR_audit.xlsx`: 另一套原版模拟测试（7条已知问题）。
