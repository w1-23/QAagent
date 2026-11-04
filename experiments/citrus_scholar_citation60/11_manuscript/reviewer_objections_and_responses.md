# 预期审稿意见及对应真实证据

**Q1: This is just another RAG wrapper.** 需展示差异在统一科研工作台的证据归属、附件原文阅读、引用核验、Excel审查和真文件交付的连贯流程；Fig01+WF07的可复核全过程，不能只列17工具。不能谎称创新了RAG。

**Q2: Intent recognition is trivial keyword rules / no classifier.** 说明评估的是“用户预期动作与真实工具执行是否匹配”，并有明确/改写/干扰三难度，约束禁止检索/误调表格，报告真正的任务执行而非空泛的分类器准确率。无单独预测器就不使用classifier这个词，称observed request-to-action alignment。

**Q3: Why are there two 60-question sets?** 核心 QA60 是原有 L/V/W/X/P/N 分层、52题可答+8题拒答的正式问答评价；补充 citation60 是新增的单 chunk 证据定位实验。补充题抽样固定 seed、五语料各12、每题恰好一个冻结 chunk；公开抽样规则、hash、运行计划和防泄漏测试。自动逆向补充题只有在两名领域评审逐题审计并仲裁后才可称 expert Gold；否则论文明确写 chunk-anchored questions，并把词汇重合、自动化偏差和小样本列为局限。两套结果分别报告，不增加独立样本或合并分母。

**Q4: Baseline intentionally weak?** 公平B1同模型、同语料和附件解析、同知识获取权限，任务来源约束一致；详细公开检索预算与prompt和失败。B0只是诊断，不作为主比较。WF07并不与缺少材料的基线进行不公平对比。

**Q5: Workflow case is cherry-picked; where is autonomy?** 公开所有7个案例的成功/部分/失败日志；WF07单轮先运行，若失败保留原始失败，再声明人工介入版本的另组结果；只有真实记录的自主过程才写自主。

**Q6: Data QA shows false positives or no IQR.** 人工植入异常按预登记规则适用条件评分；未设置字段的IQR按跳过而非错。跨表ID匹配如果工具没有实现就不宣称它能发现；专家只评真实支持功能。

**Q7: Private and public data mixed?** 登录可访问的共享语料许可、方法SOP授权均逐条审查；附件和生成成果按所有者隔离；公布的不是实验室私有文本。不要冒称本地私密LLM部署；实际模型通信范围披露。

**Q8: Evaluator bias / code was tuned on test?** 用独立 pilot 调 SSE、EGTS、引用提取和评分准则；核心 QA60 与补充 citation60 的题目、Gold/来源定位、run plan 均在正式运行前冻结 SHA，模型配置和适配器另行锁定。自动定位、两名原始语义判断和仲裁分开保存；披露开发者在出题、来源审计和评分中的角色、词汇重合风险与利益冲突。

**Q9: User-study evidence missing.** 没做配对用户研究则不说节省时间/改善满意度，只宣称完成可复核科研工作流；在Discussion中建议未来扩展真实用户验证。

**Q10: PDFs not inside public archive.** 源材料清单列官方链接与SHA供合法重复下载，不分发版权未授权全文；这是合法复现实验的合理边界，但仍需说明依赖文献可用性。
