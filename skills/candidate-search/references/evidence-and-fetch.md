# 持久证据与多来源获取

source 合同的十个键：source_ref、origin、document_key、title、display_label、locator、url、evidence_level、retrieved_at、query_ref。空值表示原输入缺信息，不能编造 DOI、题名、定位或时间。

EvidenceStore 显式接收 root/owner/session_id/turn_id/generation；保存 evidence.jsonl，记录正文哈希和四态。ref 绑定作用域、标识与正文。跨进程回读必须同作用域；缺记录为 evidence_expired。产品宿主负责分配受控目录、授权和会话清理，不能把该 CLI 路径参数当成平台访问授权。

本地全文段落与网页搜索摘要证据强度不同。网页检索的 `search_web` 需要显式 search/check/debit/authorized，原文输入不受本地语言门限影响。provider 的书目查询由 citrus_refcheck 负责；此包不复制另一套书目响应映射。

`fetch_race` 接受同篇论文的来源回调，每个回调的传输负责授权、请求预算、deadline、响应大小和取消检查。包只竞速、记录实际分支状态并丢弃取消后的结果。全局线程池有界；已运行 HTTP 可能继续，回调应自行用有界超时。不要对不同行文献、本地与网页搜索使用胜者替代合并。

独立 CLI 目前只有本地检索与证据回读。通用网页搜索、自动 OA PDF 发现与抓取不是本 CLI 已实现能力；需要宿主工具或显式执行适配，不得写成已经访问了网页。
