# 受控 Word 与交付工具接口

本次仅修改源码和运行离线工程测试，不执行三个真实科研案例或旧 FW20/40。所有数据库和下载验证使用临时工程目录。

## Word 工具

注册入口为 `src.research.word_tools.word_document`，由唯一 `_TOOL_DECLARATIONS` 声明，工具名 `word_document`。调用者只能提交结构化内容，不可提交 Python、磁盘路径、owner 或任意图片 URL。账号、会话和当前轮次来自可信 tracing 上下文。

| operation | 必需参数 | 结果 |
| --- | --- | --- |
| create | title、profile、outline、唯一 batch_id | document_id、version=1、预计章节 |
| inspect | document_id；可选 offset、limit | 当前版本、块标识、最后块、字符/表格/图片数、完成章节 |
| append_blocks | document_id、batch_id、expected_version、blocks | 已接受 block_id、新版本、累计计数 |
| replace_blocks | 上述参数及已有、连续的 block_ids | 替换指定块，其他块保持 |
| finalize | document_id、expected_version、expected_last_block_id、filename | 校验并登记后返回 artifact_id、file_id、实际 filename、requested_filename、hash、size、下载地址 |

`profile` 可选 experiment_plan、methods_comparison、data_audit、research_report、general。模型负责依据任务选择 profile 和实际章节，后端验证允许值并执行排版；本次未用在线模型验证自动选择质量。

大纲格式：`[{"section_id":"s1","title":"目标与范围"}]`。

结构块有稳定 `block_id` 和 `type`。除整篇结束块外须指定大纲中的 `section_id`。

| type | 内容字段 |
| --- | --- |
| heading | text 或 runs；level=1、2、3 |
| paragraph、emphasis、reference、caption | text 或 runs |
| list_item | text 或 runs；ordered 可选 |
| table | columns、等宽 rows；caption 可选 |
| image | 本账号 file_id、caption、width_cm 可选；PNG/JPEG |
| section_end | 表示本节本批正文已完成 |
| document_end | 整篇结束；作为最后块 |

runs 为文字格式数组，如 `[{"text":"H","bold":true},{"text":"2","subscript":true},{"text":"O"}]`。表格是真实 Word 表格，图片保持比例，标题使用真实 Word 标题样式。

一个最小调用顺序：

```python
word_document(operation="create", title="工程说明", profile="general",
              outline=[{"section_id":"s1","title":"范围"}], batch_id="unique-create-1")
# 使用实际返回的 document_id 和 version。
word_document(operation="append_blocks", document_id=document_id,
              batch_id="unique-body-1", expected_version=1, blocks=[
    {"block_id":"h1","type":"heading","section_id":"s1","text":"范围"},
    {"block_id":"p1","type":"paragraph","section_id":"s1","text":"实际正文内容。"},
    {"block_id":"end-s1","type":"section_end","section_id":"s1"},
    {"block_id":"end-document","type":"document_end"},
])
word_document(operation="finalize", document_id=document_id, expected_version=2,
              expected_last_block_id="end-document", filename="工程说明.docx")
```

上述只是工具参数示意，不是独立可执行脚本，也不包含科研案例数据。

## 中断和一致性

- 每批 1–80 块，输入 JSON 不超过 60,000 字符。文档最多 3,000 块、结构源约 8,000,000 字符；大表每块最多 12 列、5,000 单元格，宽表另导 XLSX。
- SQLite 同一事务提交结构源、真实 DOCX BLOB 和批次回执。重新创建写入器后仍可恢复。
- 相同 batch_id、相同载荷返回原回执；相同 batch_id 改变内容明确拒绝；不同新批次必须匹配 expected_version。
- 超时先 inspect，再核对是否已提交；已提交批次不要换 batch_id 重写。不确定时重发原 batch_id 和完整原载荷是幂等的。
- finalize 要求各节有真实标题和正文、最终 section_end，以及整篇 document_end；校验 ZIP/CRC、重新打开、表格和图片数量。
- 草稿操作的工具完成不等于文件交付；草稿没有下载附件。只有 finalize 完成发布、资料库登记和实际字节验证才返回下载入口。
- 文件名沿用旧发布器的安全规范化；返回的 filename 是资料库实际名称，requested_filename 用于同版本重试识别。
- 同轮、同来源、同字节精确重试复用；不同轮次或新字节生成独立版本，保留旧下载文件。

## 与既有导出兼容

新增 `export_table(title, columns_json, rows_json, output_format="csv", append=False)` 支持 CSV、TSV、XLSX。每批最多 5,000 行/100,000 单元格；超量、列宽不符、嵌套值和非有限数字会拒绝，不通过切片伪称完整。文本公式以安全方式写入表格，不当作公式执行。

旧 HTML、XLSX、SVG、RIS、BibTeX 入口仍保留。截断 JSON 不补引号或括号；截断代码围栏不删尾后发布。文献清单从本轮真实检索候选或真实核验工作簿交接，缺字段不补造。

Word Skill 安装位置：`01_src/src/prompts/skills/word-export/SKILL.md`。仅识别到正向 Word 交付需求且工具可用时注入，不放进普通 QA 的固定提示前缀。

## 使用边界

- 支持本工具草稿的结构化修改；不支持任意既有 DOCX 保真局部修改、修订跟踪或期刊模板导入。
- 完整性校验保证已提交内容和结构的可恢复性，不能证明科学结论正确，也不能证明模型没有遗漏未写入大纲的内容。
- 文档版本保存完整快照，空间随版本增长；已接入账号删除清理，尚无按会话闲置时间自动回收草稿策略。
- 旧 append 路径有进程内串行与批次幂等保护；跨多个服务进程的同名追加不能据此宣称具有完整串行一致性。Word 草稿自身使用数据库版本 CAS。
- 本次没有渲染器可用，未做 Word/WPS 的页面视觉验收；已做 OOXML、可重新打开、真实表格/图片和下载字节检查。
- 工具与提示源码需要受控重启后才可确认在服务实例中生效；本次未重启当前服务，也未确认其内存版本。
