# 人工评分操作手册

## citation60 主指标
每题只有一个冻结 `target_chunk_id`。自动主指标 `target_chunk_cited=1` 仅当最终 `citations`/`sources` 明确出现该 chunk ID；检索工具曾返回该 chunk 但最终回答未引用时仍记 0。

人工指标 `semantic_support`：`1` = 回答的关键内容被冻结 chunk 直接支持且无实质外推；`0.5` = 部分支持或有未标注外推；`0` = 不支持、答非所问或引用位置不能支撑声明。自动定位命中与人工语义支持必须分列，不能互相回填。

## 各指标
- Target document cite = 引用含目标 paper ID/source file，或已命中 target chunk。
- Allowed corpus cite = 引用含目标语料 ID/路径，或已命中 target chunk/document。
- Missing citation = 最终 citations/sources 为空且正文无可核验 `[n]`、URL、DOI 或“来源：”信号。
- Wrong corpus cite = 引用出现其他四类公共语料的明确标识；不从标题猜测。
- Fabricated citations count: 不存在 DOI/来源，或链接不对应声明。
- Fatal scientific overclaim: 把品种表现当HLB抗性，把昆虫采样论文当树叶确诊SOP，把模拟数据当真实研究结果等。
- 意图192：只评价预注册可观察的工具类别匹配、约束违规；无工具预期的GENERAL若无不必要工具可过。观察到环境不可用时单列不计路由分母。
- 复合24：对目标工具集合计算precision/recall/F1；对被禁止调用单独计0/1违规；导出要求必须验证实际文件存在、SHA一致，纯文本不算完成。
- 工作流：每项步骤 accept 1 / fail 0 / not-applicable NA；critical fail 不得被其他步骤通过抵消；截图、哈希、来源定位入台账。

若报告语义支持，使用锁定 manifest 中每语料 4 题×3 条件的 60-run 抽样。标注者按匿名 run_id 评分，不应事先知道 B0/B1/B2 或自动命中列；分歧由第三者或负责人仲裁。最终输出单独保存两份原始评分和一份仲裁评分，严禁覆盖原值。报告严格支持率、`1/0.5/0` 分布、weighted kappa 和自动—人工不一致案例；不做抽样时不生成 rater 表，也不能拿模型自评作人工金标准。

核心 QA60 的 EGTS/Atomic-slot、拒答与失败规则继续有效，并按原计划评分、仲裁和报告；这些规则不与补充 citation60 的 target chunk 自动定位混表，也不被其替代。
