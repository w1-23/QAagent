# 逐条文献核验

## 适用条件

默认核对“是否有可核查登记记录”与“输入书目字段是否准确”。不默认审查全文科学内容，不把登记记录存在当成正文论点已获支持。聊天和 PDF 面板使用同一个 owner/session 绑定的 `ReferenceAudit`。

## 输入与库存

使用授权的 DOCX/PDF/TXT/MD、粘贴文本或结构化条目创建库存，保留 `audit_id`、source hash/version、parser version、原始条目、位置与 `record_id`。重复 DOI 的不同条目以及无 DOI 条目都必须保留；不要按 DOI 折叠库存。结构化输入的完整性仅针对提交清单，不证明覆盖原文全部文献。

现有解析使用 PyMuPDF/python-docx 与保守条目分段；未配置 GROBID，未执行 OCR。自动分段的来源覆盖仍为 partial；无参考文献标题、扫描页、截断、Word 表格未扫描等限制均需保留。来源覆盖与逐字段解析确定性分别报告：清晰的“作者 (年份). 标题. 期刊”可保留显式作者、年份、标题、期刊及明确卷期页，不得将所有字段统一抹成 uncertain。读取每条 `field_certainty`、`uncertain_fields`；边界含糊的字段仍须确认，不能据此声称全文 100% 完整。通用文件/编码工具可协助提取复杂引用，但必须保留原文和定位；模型修订字段不能冒充确定性解析结果。

## 方法顺序

1. 明确目标库存和来源版本；创建后始终使用该 `audit_id`。先检查 `extraction`、`coverage` 和原条目，不能把提取到的 DOI 数当参考文献总数。
2. 用 `verify_uploaded_references` 的同 audit 批次接口推进核验。精确 DOI/arXiv 先查现有登记库；错误 DOI 或题名不符时独立题名检索。该审查路径不调用额外 LLM 翻译或网页回退；原文题名查询及未翻译限制随结果保留。网络授权关闭时不发送查询，包括缓存复用。
3. 分开阅读 `existence`、`identity`、`fields`。精确 DOI/arXiv 对应 `identifier_records`，主判断使用 `selected_record`；不得用标题更相似的记录覆盖标识符身份。标题写错时报告 DOI 对应记录的标题冲突，独立检索记录保存在 `title_candidates`。标题、作者、年份、期刊、卷、期、页码、article number 各有 matched/conflicting/missing/not_checked/uncertain 状态；`input_certainty` 与 `comparison_status` 区分解析待确认和字段比较。作者缩写、et al、转写、期刊简称保持保守；明确不同作者或不同期刊仍报告冲突。仅登记库明示 online/print/issued 年可支持年份变体；未证明的一年差保持 uncertain，明显年份差报告冲突。不要把 DOI-only 匹配改写为全部字段准确。
4. 核对每个 provider 的候选、URL、时间、HTTP 状态、响应 hash、规范化记录和错误。不同登记库冲突不得被首个成功记录覆盖；缓存仅复用提供者元数据，当前 claims 始终重新比较。not_found、400/429、网络失败、解析遗漏都不能推出文献虚构。
5. 每条结果已落库后才发布进度。批次只处理尚未完成的条目；`resume=True` 也允许重查可恢复失败。取消检查在条目和请求之间进行；已发出的同步请求受有限超时约束，取消不代表请求在网络上瞬间消失。重启或取消后沿同 audit 继续，不重置已消耗请求预算。
6. 返回结果分页不等于库存分页；用 `audit_id`、`coverage.total/processed/pending/retryable` 和具体 record ID 判断整体覆盖。超过 100 条仍在同一库存累积，不能重新创建只含最后一批的审查来冒充整项完成。工具 `operation="read"` 配合 `offset`、`max_rows` 按 `next_offset` 回读；`operation="run"` 推进下一批，`operation="cancel"` 取消。原 start_index 不再允许跳过条目。
7. 当 total=processed 且 pending=retryable=0 时，读取 `coverage.processing_complete`、`terminal`、`next_action`。`completed_with_review` 表示库存处理结束、解析或字段仍待人工复核；`coverage.complete=false`/`extraction.partial` 不要求无限 resume。应停止联网重查，说明具体不确定项并导出。预算耗尽也应导出部分结果，不能重建库存刷新预算。只有 pending 条目继续 run，可恢复联网失败或取消才使用 resume。
8. 用户需要文件时按明确 `audit_id` 导出全量 JSON/XLSX，包括未核、失败、原始条目、解析确定性、终态与证据；允许在 completed_with_review 或部分完成时导出。使用 `deliver_research_artifact(kind="bibliography", audit_id=..., output_format="json")` 和同 ID 的 `output_format="xlsx"`，可直接采用返回的 `export_requests`；不得只重抄当前分页。`publish=True` 只发布 XLSX，不能称同时交付 JSON。RIS/BibTeX 是提交书目的投影，包含 audit/record/status 注记，不是完整审查证据；完整证据用 JSON/XLSX。禁止读取含糊的 latest/last_verification 代替当前审查。

## 验收与措辞

每个目标条目有稳定身份、来源定位、实际执行或未执行原因。完成批次不等于全文全部文献核验完成；partial extraction、待核、可重试错误与候选不确定性分别报告。未查到或字段冲突不是造假证据；任何风险解释都必须附具体事实与限制。导出不产生新的核验覆盖，不让模型重抄已保存的审查表。
