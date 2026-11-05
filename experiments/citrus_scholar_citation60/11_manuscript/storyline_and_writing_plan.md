# 论文完整叙事设计：从问题、方法、实验到结论（交给 dsh）

当前写作基线：v4.3.3，`AWAITING_SIXTH_EXTERNAL_GPT_REVIEW`，无新增结果。核心 QA60
确认性系统终点为 `egts_system_outcome`；两位专家对全部 180 份回答的独立评分及
第三专家字段仲裁生成 `human_consensus_egts`，单独报告并披露语义评分覆盖率。
三 LLM 判官通过同一 OpenCode Go 服务入口，作为独立自动测量。实验与分析仅在
服务器隔离实例执行。

## 推荐定位 / 标题
**CitrusScholar: An Integrated AI-Assisted Research Workbench for Citrus Science**

预想文章类型：Agricultural informatics / scientific software / digital agriculture tool / application paper；不声称为新算法、首个柑橘Agent、严格端到端自主科研。投稿前按目标期刊 Scope 和开发/发布条款选刊。系统有MIT许可证，但当前登录限定、公开CI/测试历史不足可能不适合某些软件期刊；不因此突击重构产品。直接比较 HAIant 不合适，因为模型、资源、工具数量、任务与实验背景不同；Related Work只借鉴它用 intent/task & cases 讲完整平台能力的写法。

## 一句话主论点
**Traditional single-purpose agronomic Q&A interfaces answer questions; CitrusScholar brings heterogeneous citrus evidence and practical research actions into a traceable, human-verifiable workflow.** 这句话只是主张，是否由实验证明必须以真实数据和案例为准。不要写“多智能体协作”“强隐私本地LLM”——当前代码是单General Agent，可调用本地检索、网络、附件和科研工具；公网模型/附件外发风险不能掩盖。

## Introduction 建议五段结构（约1000–1200英文词，最终依期刊调整）
1. 植物科学/柑橘研究高度异构：论文全文与补充材料、UCR等种质品种资源、中文地方品种资料、实验SOP与原始表格相互分离。具体例子：比较Gold Nugget和Tango时既要看UCR原始记录，还要看研究论文（D03）的砧木设计，不能混淆品种描述与试验处理。科学数据需要验证而非只听语言模型说“正确”。
2. 现有通用LLM、单源RAG、人工在网页/PDF/Excel软件间切换的优势与不足。不要一概断言所有现有研究都没有证据链/流程；必须对相关文献逐篇说明差异。
3. HAIant (2026)等生物科研工作平台已证明整合工作流的叙事价值，但不能把其安全个性化架构、BioSkills工具规模视作本系统实现；CitrusScholar选择柑橘科学、可检查证据、研究文件交付的轻量路线。
4. 本研究解决的具体缺口：单一入口中跨异构公开/登录授权数据检索、原文阅读、元数据核验、数据审查和可追溯交付，并对用户明确本地/联网/仅附件/禁止检索进行约束；不是提出新的检索或分类模型。
5. 三个贡献：一体化柑橘研究工作台实现；基于来源约束和可审计结果的科研辅助工作流；多层问答/动作路由评价 + 高难度融合案例的可复现系统实证。避免预先宣称性能领先或显著节约时间。

## Related Work 四类，必须区分事实
A. Plant/agricultural language models & knowledge-enhanced QA：包括AgroLLM（需核验DOI与发布版本）、针对果树的问答、作物知识图谱RAG，说明它们评测的对象多聚焦答案/特定任务；不得遗漏其他工作中已经有工作流功能。
B. Scientific AI assistants and research workbenches：HAIant（DOI 10.1016/j.molp.2026.08.011, Molec Plant, 2026），以及相关文献助手/生物信息学平台。对照维度应是**学科覆盖、数据边界、工具类型、公开评测、可重复产物**，而不是其工具数多或少。
C. Evidence traceability/LLM evaluation：引用可定位、双专家共识评分、冻结机器判官的独立对照、回答在来源中是否可由引用回指；不报告 semantic support，不将检索命中率等同答案正确。
D. Research data quality and spreadsheet auditing：缺失、重复、IQR、规则适用与跳过；审查只能识别待核验问题，不可判断科研造假。

## 3. System Design（应以代码复核后写）
**3.1 User-centered workflow & scope**：科研者 → 问题 / PDF / Excel → 当前会话；账户附件不自动进入共享语料；用户可指定本地/联网/融合/不用检索。品种及文献元数据共享授权数据；个人成果/笔记私有。
**3.2 Data integration**：公开语料 cqa_literature、cqa_ucr_varieties、cqa_deng_book、cqa_translated、cqa_methods（必须审查最后者授权），联网开放论文与发行元数据，PDF/Excel附件；文献/来源入口、原文阅读器。重点描述异构性而非虚构通用语义统一算法。
**3.3 Task routing/constraints**：单 General Agent + 17注册工具；请求约束和运行环境可用性筛出禁止/不可能的工具；LLM选择调用。不能把代码规则决定请求范围写成训练出的“意图分类器”。
**3.4 Research utilities & safety**：科研读写、DOI核验、审查表格和已登记不可变产物；真实状态complete/partial/no_results/failed；下载哈希和所有者权限；表格审查规则 available/executed/skipped。
**3.5 Deployment & reproducibility**：模型服务、数据库、索引版本、CPU/GPU fallback按实际记录；私人资料脱敏、合法联网、登录与公共演示的实际限制。

## 4. Experimental design（建议全文最严谨部分）
**4.1 Questions & source controls:** 核心 QA60 保留既有 L/V/W/X/P/N 分层、52 answerable + 8 unanswerable；补充 citation60 从五类语料来源各随机冻结12个 chunk，每题恰好一个目标 chunk。`cqa_methods`、`cqa_literature` 和 `cqa_translated` 可能包含非柑橘材料，因此不称为“五类公开柑橘语料”，也不主张代表全部柑橘文献。全部 120 题由两名领域评审/研究生双人认证有效性，不写 Gold Answer。运行器只接收题干、允许语料和引用要求，不接收 chunk 正文或目标定位。两套题不共享分母。
**4.2 Baselines:** B0无检索诊断；B1公平同源simple RAG；B2完整平台。相同模型、材料、权限与附件解析，B1/B2使用同一硬预算：每题最多8次模型调用、32768输入token、8192输出token、8次检索、16000 context token、12次工具调用和600秒；实际调用、token、cost、list_cost、allowance、耗时与检索次数全部报告。核心 QA60 与补充 citation60 各60题×3条件、各180轮，分别分析；补充重复敏感性另行预设。
**4.3 Routing intent/action study:** 192道(8×24)，评估真实工具执行符合率；多意图24测组合约束；若无独立predictor不能叫“意图分类准确率”。
**4.4 Workflows and deliverables:** WF01–WF06日常科研任务，WF07跨品种、ACP论文与叶片Ct模拟数据，专家验证证据边界和交付文件。声明这些是可行性案例不是统计性智能证明。
**4.5 Annotation & metrics:** 核心 QA60 两位专家对 180 个计划槽位中的可语义评分回答独立评分，第三专家
逐字段仲裁四维分歧，确定性脚本计算人类共识主结果。四个字段是
`answerability_correct`、`answer_correct`、`evidence_traceable`、
`fatal_error`；不可答题的两个适用字段为 NA。GLM/MiMo 独立评分、MiniMax 仅仲裁
机器分歧，作为独立自动测量。报告 raw agreement、Gwet AC1（主）、Cohen kappa
（辅）、机器间、机器与人类共识一致率及两类仲裁率。补充指标只检查最终引用是否
明确命中冻结 target chunk；不报告 semantic support。真实
`failed/cancelled/skipped` 保留为系统任务失败，不伪造专家或机器语义分数；
`RAW_RUN_MISSING` 保留计划槽位并使 strict gate 失败。
**4.6 Statistics:** 核心 QA60 的唯一主要确认性比较是 `B2 vs B1` 的
`egts_system_outcome`：双侧 exact McNemar、配对成功率差（百分点）、10,000 次
L/V/W/X/P/N 分层配对 bootstrap 95% CI，seed `20261104`，以 question ID 为
重采样单位并保留该题 B0/B1/B2 三条记录；报告完整 2×2 转移表。条件层面另报
系统终点/60、人类语义终点覆盖率与可评分率、answerability/60、answer
correctness/52、evidence traceability/52、fatal error/60 与 Wilson CI。
B1/B2 相对 B0 只作描述性诊断。补充 citation60
单独分析 B2−B1 逐题 target chunk 命中差、语料分层 bootstrap CI、Wilson CI、
exact McNemar 描述性检验与 2×2 表。60 个补充目标 chunk 不等于额外独立问题，
不把同一题三条件当独立来源；负结果如实公开。
**4.7 Real user study (optional):** 12人探索性配对使用研究，仅合规批准和真参与者到位才能做。

## 5. Results 叙事顺序（现在全部待填，不能填预测）
R5.1 **What does the core QA60 evaluation show?** 用核心 QA60 表/Fig02–03 报告
`egts_system_outcome`、单独的人类语义 EGTS 与覆盖率、answerability、答案正确性、
evidence traceability、fatal error、52 题可答与 8 题拒答、失败和环境不可用；
按原分层报告，不把补充 locator hit 当作核心指标。
R5.2 **Supplementary: can answers locate the intended frozen source?** 用Supplemental Fig.S3–S4和Table S2，单独报告五类来源中B2与公平B1的 target chunk 引用命中、目标文档/允许语料/缺失/错误语料；不报告语义支持，也不推断柑橘文献总体。
R5.3 **Are task requests translated into allowed actions?** 用Fig04–05观察路由合规与冲突类别，注明这是执行观察不是分类器。明确环境不可用的数量/比例。
R5.4 **Does integration reach usable artifacts?** 用Fig06以及工作流输出审核表，介绍WF04标注Excel、WF05 DOI表、WF01笔记→HTML等，文件哈希和真实下载验证。
R5.5 **Can the workbench handle a difficult fusion workflow without scientific overclaim?** 用WF07展示：局部品种资料引用、ACP检测论文事实、模拟叶片数据审查、可疑阴性对照、拒绝品种抗病性断言、备忘录+成果。若自动执行未能完成某步，应展示真实失败与人工干预边界，不能说全部自主。
R5.6 **Limitations/robustness:** 来源不完整、解析失败、无结果、模型随机性、自动逆向题的词汇重合、有限规模，以及专家分歧、LLM 与专家间误差和人工判断局限。负结果也是研究结果。

## 6. Discussion 叙事
1. 系统差异是实践中“研究任务的衔接”而非引入新的LLM/Embedding算法；同源B1接近时应强调整合和可追溯交付，不歪曲统计。
2. 仅靠任务路由与多工具调用不证明“智能”；真正有意义的是**能判断哪些方法不适用、哪些数据不能支持结论、何处需人工验证**。以WF07解释智能的操作化定义。
3. 域内贡献和通用性边界：架构可改做其他植物科研，但未在其他作物完成跨域实验，不宣称普适准确率。
4. 知识库更新、出版社更正、UCR条目变化、中文语料授权、外部模型数据外发、登录门槛、安全与隐私。
5. 推广/未来工作只能轻描淡写用户研究与自动错误分析，不提出大型新架构Roadmap淹没当前论文。

## 7. Conclusion 三句的稿件结构（要根据结果重写）
(1) 我们实现了面向柑橘科学的统一AI研究工作台，将异构资源和实用科研工具纳入一个可追踪交互界面；
(2) 在预定义的柑橘问题、执行路由和七个科研案例中，实际结果显示其已验证能力与不足（填真实数据，不得事先判更优）；
(3) 平台更适合研究者在环的证据整理、数据审查与成果准备，而不替代实验验证或科学判断。

## Availability / Data / Code / Ethical declaration
- GitHub公开代码（需核实提交/许可证）：https://github.com/w1-23/QAagent ，基线SHA和资源快照可验证。
- 发布可公开的 `L2` 题目、来源定位、脱敏运行、评分、统计与绘图程序；`L0`
  永不进入仓库，`L1` 版权受限全文、个人资料和实验室 SOP 只保留受控服务器存储，
  公开包记录 SHA、定位、访问等级和 redaction lineage，不误称完整开放。
- 指明接入外部LLM/公网服务时的数据隐私边界，实验用户的知情同意和模型辅助写作披露遵循目标期刊。
- 未实际公开服务访问与无账户测试前，不称“publicly available with no registration”。

## 图片与表格安排
Main Fig.1 系统架构；Main Fig.2 核心 QA60/EGTS；Main Fig.3 核心 QA60 失败与证据使用；Main Fig.4（组合板）观察路由执行合规/常规工作流；Main Fig.5（可选）WF07关键步骤的证据与成果。Supplementary Fig.S3–S4 单独呈现补充单 chunk 引用定位与配对效应；其余补充图包括引用失败组成、各类路由混淆、Excel审查定位、时间与重复稳定性。

Table1 五类语料、抽样/合格数、generation/SHA；Table2 3基线、模型与资料公平设置；Table3 核心 QA60 专家共识结果；Table Sx 专家一致性、LLM 一致性及 LLM-人类共识一致性/仲裁率；Table4 路由8类与复合24；Table5 WF01–WF07逐项交付与主要失败边界；Supplement Table S1 补充60题→chunk来源映射；Table S2 补充 target chunk 引用命中与五语料CI；Table S3 引用失败分类、数据许可。各表数值完全由分析脚本输出后填入，**不得使用示例或预期数字**。
