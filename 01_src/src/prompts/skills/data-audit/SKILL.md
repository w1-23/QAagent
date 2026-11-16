# 数据审查与真实覆盖

## 适用条件

检查数据字段完整性、类型、范围、重复、离群、跨列逻辑或跨文件一致性时使用。复用 `audit_uploaded_data` 和现有方法库，不另写场景执行器。

## 输入要求

用户要求的文件、工作表和检查范围；授权文件 ID/hash/版本；真实工作表、表头、字段、单位、实验单位、分组与有效检查规则；已有结果引用。不要以文件名推断变量含义、允许范围或实验设计。

## 方法顺序

1. 用 `inspect_uploaded_table` 或已有结构回执明确实际文件、工作表、表头位置及解析范围。多表歧义时明确目标，不默认首表；摘要预览只帮助选规则，不证明全表已审查。
2. 把用户要求范围与本次工具选中范围分别保留。`file_indexes_json`、`sheet_name` 必须对应真实对象；不能把只传 `[0]` 的结果描述为全部附件。跨轮追加新规则时，以宿主返回的真实账号文件ID传 `file_ids_json=[真实file_id]` 给 inspect/audit，无需用户重附；非空ID列表就是本次来源，索引在此列表内从0选择，不混入当前附件或其他历史文件。空ID参数保留本轮附件索引用法。账号来源必须通过owner、登记大小和实际SHA-256校验，单次最多20MB/20文件；仍受原有行列/单元格解析上限约束。新增规则产生新扫描与新review；仅导出原扫描继续用原review_id，不能把两者混为重扫。用户缩小范围时遵守限制；工具或模型缩小范围时保留原要求的差集。
3. 选择现有缺失、非数值、重复行/主键、组内 IQR、范围和跨列规则。缺失的单位、合理范围、独立性、配对或分组依据不能猜测。疑似离群是复核提示，不等于数据错误；不自动删除、补值或修改原数值。
4. `checks_json` 只传已有工具支持的规则。分组和测量的标准映射是 `{"groups":["Group"],"values":["Value1","Value2"]}`，使用实际表头；`column_mapping` 是 mean/n/sd/replicate 等角色到列名，不替代 groups/values。字段名或配置未识别时按工具反馈修正，不能改写为自然语言规则、另建路由器或猜分组。整数汇总和报告统计量复核需要工具规定的真实单元格映射、精度、单位、实验单位与 SD 定义；不足则明确该项未检查。跨文件快照可能有行/列/表/单元格上限，必须按返回范围说明，不冒充全量跨表检查。
5. `annotations_json` 是独立人工/模型注释层，与确定性扫描同时保留；不能改写规则执行凭证。注释保留原文件行号、表名、字段和理由；无效标注单独列出。正常标注不能覆盖该单元格的规则信号。
6. 从实际 `audit_results` 和 coverage 回执逐项核对文件版本、工作表、检查项与已解析范围。保留跳过、失败、未支持和截断范围；未提供可靠检查项回执时标为未证实，不凭传入参数补造成功。
7. 用户要求文件时，首次扫描显式传所需 `publish_formats`，例如要求 XLSX、HTML 和机器可读定位 JSON 时传 `["xlsx","html","json"]`。仅 `publish=true` 保持旧行为：发布标注 XLSX（跨文件旧路径另有关系 JSON），不默认导出所有格式。未要求文件则不发布。已有扫描时用工具真实返回的 `integrity_review.review_id` 调用 `deliver_research_artifact(kind="data_audit", review_id=真实ID, output_format="xlsx"或"html"或"json")`，每次指定一种需要的格式；不要重抄发现、重复扫描或编造 `read_tool_result` ID。交付文件链接必须来自真实发布回执；发布失败时保留扫描引用，重试交付不扩大覆盖。

显式必填缺失、用户数值范围不满足、显式单列/复合唯一键重复属于 `error`；IQR 和统计模式属于 `review`，可选 Note 空白比例为 `info`，不逐格报缺失错误。Excel 问题清单和批注保留真实 severity。输出 JSON 的 `issues` 包含原始 `sheet`、`cell`/`cells`、机器类别 `category` 和 `severity`；列/表级信号的 cell 留空，不伪造具体位置。完整规则回执、覆盖、跳过/失败/截断及跨文件证据保留在同一导出中。

输入兼容范围仍由共享解析器决定，JSON 不是表格输入格式；审查 JSON 是输出证据文件，不能当作原数据再次扫描。XLSX 导出为副本，原件哈希必须与扫描一致；原始公式和单元格值不主动修正。非 Excel 来源仅为转换到工作簿重新解析，不重做扫描。旧复核记录可导出已有 HTML/JSON；缺少表头/工作表渲染元数据时明确拒绝无法可靠生成的 XLSX。

## 可选数值模式和实验结构

- `checks_json.composite_keys=[["subject","visit"]]` 对完整元组查重；原有 `key_columns` 仍分别检查单列。缺失键不补造。
- `values` 声明确切测量列。`pattern_checks.columns` 从中选择并保留原列顺序，排除 ID、分组、实验单位、重复编号、n。可选择 `raw_numeric_tail`、`adjacent_equal_differences`、`sliding_equal_differences`、`repeated_sequences`、`repeated_blocks`（布尔值）。
- 顺序模式必须确认 `order_confirmed=true`。`axis="rows"` 沿原始行顺序；`axis="columns"` 沿原始列顺序，还需 `same_units_confirmed=true`。不先排序，不删除缺失后拼接窗口。相邻差分检查三值；滑窗与重复片段使用 `window`（3…100，默认5）。重复块为连续 window 行×2列，不能称任意子矩阵搜索。
- 默认差分容差为0，可显式给非负 `absolute_tolerance` 与 `relative_tolerance`，相对项按窗口变化幅度缩放，不按巨大基线缩放。至少3个不同值；首20000行/30个所选列、每规则最多100000个合格窗口。应核对实际tested、skipped、截断以及位置；搜索越多巧合越多，无p值或不端评分。
- `raw_numeric_tail` 仅对解析器保留原始文本词法精度的 CSV/TSV/Word 数字执行；Excel数值、PDF提取或浮点不能反推原始末位。`tail_digits` 为1…6（默认2），`frequency_threshold` 默认0.35；按实际小数位和指数分层，每层至少12个token/8个不同值。不给整数或短小数补零。原有 `decimal_ending_concentration` 保留六位格式化的描述性规则，明确不是原始末位。
- `expected_structure` 必须明确 `factors`、`unit_column`、可选 `replicate_column` 和最多500个 `expected_cells`。例如 `{"factors":["treatment"],"unit_column":"animal","replicate_column":"rep","expected_cells":[{"treatment":"control","expected_units":3,"expected_replicates":2}]}`。仅 `exhaustive=true` 才对预期表之外的组合提示；不自行构造平衡设计，也不把技术重复当独立单位。语义缺失时该项 skipped。

## 执行凭证和科学边界

每表 `rule_execution.rules` 与 `rule_catalog` 来自实际执行分支，含 `executed/skipped/invalid_input/truncated/failed`、方法版本、实际计数单位、范围、阈值和原因。`tested=0` 不是通过；配置错误不自动换成另一个运算符。解析失败和空sheet另有源级凭证。跨文件按行序号对齐，不能声称已经按样本ID配对；其更小快照/比较上限与表内检查分开报告。汇总比较只覆盖显式绑定单元格，不覆盖正文、caption或未绑定p值。

范围、关系和模式使用有界十进制/Fraction比较；Excel输入本身已丢失的精度无法恢复。IQR/六位格式化尾数仍是探索规则。仪器舍入、剂量/时间网格、单位换算、公式派生、共享对照、不平衡设计和合法周期信号均可触发。输出统一为数据质量错误或需核对的结构信号，不能据此认定科研不端。

补充材料复用相同有界解析器，逐个发现的sheet/表审查；总30表/2000000 cells，单文件解析仍受原预算约束。PDF前200页/最多500表的提取不等于OCR或全页全表覆盖。失败、未发现和预算外对象分别记录，不能称下载到文件即完成扫描。

## 验收条件

完成范围由实际回执而非工具调用参数决定。回答明确用户要求、实际已审文件/表/规则、发现及原始位置、未覆盖对象与原因；零发现只适用于已检查范围。只有每个要求对象及检查项都有有效结果才可支持完成判定，否则保留部分完成，不擅自扩大任务来填平差集。
