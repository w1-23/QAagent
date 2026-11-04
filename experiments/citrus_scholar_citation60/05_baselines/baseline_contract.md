# B0/B1/B2 操作合同（dsh 必须在实际源码上实现，不能假装已联通）

**适配器设计**：`scripts/run_matrix.py`会按condition调用`05_baselines/adapters/B0.py`、`B1.py`或`B2.py`导出的`run_case(case, config)`，返回真实结果字典。B0示范的是OpenAI兼容ChatCompletions；B1/B2初始模板故意拒绝运行，避免凭猜测的接口写出虚假“成功”日志。dsh必须读取仓库API、当前认证流程、实际可用模型/索引和处理后，**独立完成并试验实际适配器**。没有合法访问时暂停并向用户提出所需权限；不得采集或存储密钥到实验目录。

**返回 JSON 结构**：`{'status':'completed|partial|failed|no_results|cancelled|skipped','final_answer':str,'raw_answer':str,'tool_calls':[{'name':str,'status':str,'source_ids':list}],'citations':[...],'artifact_paths':list,'artifact_sha256':dict,'model_id':str,'tokens_in':int|null,'tokens_out':int|null,'elapsed_ms':number|null,'first_token_ms':number|null,'api_cost':number|null,'error':str|null,'implementation_notes':str}`。运行脚本独立加上Run ID、时间、question_id、SHA和真实耗时；不允许返回固定的`'成功'`假数据。所有工具状态以服务真实日志为准。

- B0：同LLM；不得访问内部文献库/联网工具/上传PDF。无证据回答诚实计分，P组B0只说明无PDF情况下能力，不将其计入公平架构优势。
- B1：简单RAG，**同样可用**的本地/联网/附件材料与B2；单个纯脚本retriever + prompt，不使用CitrusScholar集成Agent额外工具。两组文本读取/解析与检索预算尽可能公平，差异如实披露。B1不应是故意较差的prompt或残缺数据库。可按照题目来源约束筛选正确文档集合；B1也可先用检索器搜索再生成，不能直接喂Gold证据（B2也没给）。
- B2：原生CitrusScholar，在新会话、真实账号和真实权限下提交提问/附件；保留实际SSE工具事件和`done`正文，确保当前UI与历史文本一致。B2对本地/联网要求应遵守原控制；pure Excel请求不能意外联网。禁止将模拟数据写入共享科研语料以便提前“记住答案”。

公平报告必须包含：模型实际ID、温度/seed（若支持）、网络/工具可用性、embedding/reranker、top-k、上下文预算、是否本地缓存、文献快照、PDF parser版本、硬件与调用费、网络时间范围，以及B0无法访问PDF的限制。联网结果如果不能冻结快照则提供检索日期与差异分析，不能谎称严格控制。

## 一句话定义

- `B0`：只让同一个基础模型回答，不给语料、搜索、附件或工具。
- `B1`：把检索到的相同材料拼进 prompt，再让同一个模型回答；不使用 Agent 的多工具工作流。
- `B2`：最后更新并锁定的 CitrusScholar 完整实验版，允许真实检索、读文件、审查表格、核验引用和生成交付物。

## 模拟运行边界

`scripts/S00_simulated_dry_run/` 用于在真实接口未接通时演练完整数据链，所有输出
必须标记 `SIMULATED_DRY_RUN` 和 `not_for_publication_claims=true`。模拟结果可以
进入仓库做可复现性测试，但不得进入论文 Results、摘要、图表或任何正式统计。
