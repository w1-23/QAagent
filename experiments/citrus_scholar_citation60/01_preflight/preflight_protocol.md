# dsh需要执行的源码-部署前门禁

## A 版本核对（代码/运行/真实UI分层）
1. 对候选本地仓库`git rev-parse HEAD`，远端main、测试部署、生产实例分别记录SHA、时间、关键文件SHA；候选 v15复审SHA `b22c178c0343d22c0bbe2e4cddda94e296f59280`仅作比较，不预设当前仍是该SHA。
2. 审核`01_src/src/tools/__init__.py`集中注册的17工具、`01_src/src/graph/tool_policy.py`和`tool_availability.py`对no retrieval/local/web/fusion/paper_only/table_only约束，`01_src/src/core/resource_access.py`和允许的公开语料注册表。确认`cqa_methods`授权边界不是把实验室私密SOP开放给全体登录账号。
3. 对已有预先声明的SSE风险：观察`01_src/src/graph/general_agent_graph.py`是否先发`text`再后处理`answer`加下载/状态，再查看`01_src/web/assets/app.js`是否只按text增量显示，最终`done`是否替换。必须用真实浏览器/历史回放对照核实；不能凭源码潜在缺陷直接判当前失败。
4. 测试一份附件Excel完整文件SHA、同时查看回答当轮显示与历史；以一次正式产物登记、跨账号下载返回拒绝、取消/失败/无结果的对外UI真实表现核实。所有修复必须经过单独授权，加入回归测试并重跑预实验、更新SHA。
5. 生产如仍为公网明文HTTP不能开展外部受试者登录实验；开发/内网单机且无真实私密资料也应遵循学校安全要求。只通过HTTPS域名、最小权限、受控测试账号进行外部研究。无需以改动产品架构满足某期刊。

## B 输入与资源
- 以文献/品种条目为单位抽样验证搜索命中以及原文阅读能找到确切证据；不是单纯health=200即代表准备好。所有实验用的已授权中文品种条目至少保留不侵权证据定位号及本地索引SHA。
- `02_sources/manifests/materials_tracking.csv`所有正式QA用到的paper ID应可访问。明确只在“本地”组入索引，附件组保持指定附件可读，Web组保留具体抓取时间。请勿为了跑题目把Web上的目标文献提前喂给B2本地索引，使两个条件的信息范围变化。
- 故障隔离：本地没收录→锁题前修订样本/索引并记变更；锁题后中断→保留正式失败记录；网络页面更正变更→记时间与敏感性，不暗改问题。

## C 初始试运行
只用`pilot12.csv`，另从192候选意图中拿非正式独立试测集另造一小组并作标记（如确需调试prompt，正式意图192也不可复用）；核对运行JSON schema、日志完整、工具轨迹、脱敏、文件哈希以及B0/B1/B2公平性。两名评审先试标得到对EGTS与引用有效性的共同理解。

## D 每条门禁状态
`01_preflight/gates.csv`: PENDING / PASS / FAIL / NA。P0全部PASS，P1若不适用允许NA且必须说明；每项需`evidence_path`且确实存在。dsh不可替领域评审编造Gold或签名。若门禁不通过只能报告阻塞、修复并复测，**不能为了画图跳过**。
