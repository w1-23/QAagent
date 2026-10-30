# -*- coding: utf-8 -*-
"""v8.15.3 特性回归：联网失败熔断 / 检索统计回传 / 数据源覆盖提示 / 推理控制。

全部离线、无模型、无网络（仅验证逻辑与门控纯函数）。约定见 test_batch1.py。
覆盖：
  VF-9   rag_stats_note 检索统计回执（早停阈值 + 并发防串号）
  VF-10  build_evidence_report web_unavailable 熔断提示（确定性回执）
  VF-11  model.reasoning_mode 配置默认 + llm_pool thinking_off 接线
  VF-12  deepseek_web_search HTTP 失败详情（状态码落回执，超时单独分支）
"""
import sys
import os
import inspect as _inspect

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import settings

passed, failed = [], []


def check(name, cond, detail=""):
    if cond:
        passed.append(name)
    else:
        failed.append(name)
        if os.environ.get("PYTEST_CURRENT_TEST"):
            raise AssertionError(name + (f" {detail}" if detail else ""))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail}")


# ── F-15.3-1 检索统计回执（决策器每轮检索后自审依据）────────────────
def test_v8153_rag_stats_note():
    print("[VF-9] rag_stats_note 检索统计回执")
    from src.tools.search import rag_stats_note

    check("无统计 → 空串", rag_stats_note(None) == "")
    check("非 dict → 空串", rag_stats_note("x") == "")
    check("候选 0 → 空串", rag_stats_note({"candidates": 0, "passed": 0, "filtered": 0}) == "")
    ok_note = rag_stats_note({"candidates": 20, "passed": 12, "filtered": 3, "query": "citrus hlb"})
    check("通过率高 → 仅统计不提示", "[检索统计]" in ok_note and "相关性低" not in ok_note, ok_note)
    low_note = rag_stats_note({"candidates": 20, "passed": 4, "filtered": 6, "query": "citrus hlb"})
    check("过滤占比 60% → 提示相关性低", "相关性低" in low_note, low_note)
    thin_note = rag_stats_note({"candidates": 20, "passed": 2, "filtered": 1, "query": "citrus hlb"})
    check("通过 ≤2 → 提示相关性低", "相关性低" in thin_note, thin_note)
    check("统计数字字面量正确", "候选 20 条" in ok_note and "通过 12 条" in ok_note, ok_note)
    # 并发防串号：last_stats 归属查询与本次查询不一致 → 不输出
    mismatch = rag_stats_note({"candidates": 20, "passed": 1, "filtered": 9, "query": "other query"},
                              expect_query="citrus hlb")
    check("串号统计被丢弃", mismatch == "", mismatch)
    match = rag_stats_note({"candidates": 20, "passed": 1, "filtered": 9, "query": "citrus hlb"},
                           expect_query="citrus hlb")
    check("同查询统计正常输出", match != "" and "相关性低" in match, match)


# ── F-15.3-3 推理控制（v8.17.17: reasoning_mode=off；v8.17.19: extra_body 下发关闭字段）──
def test_v8153_reasoning_mode():
    print("[VF-11] model.reasoning_mode 接线（v8.17.19 = off，extra_body 通道）")
    check("默认值 = off（用户决策：检索决策关思维链）",
          getattr(settings, "MODEL_REASONING_MODE", "default") == "off")
    from src.core.llm_pool import get_llm
    off_body = dict(getattr(settings, "MODEL_REASONING_OFF_BODY", None) or {})

    llm_off = get_llm("t-model", "k-1", "https://x", max_tokens=64, thinking_off=True)
    check("thinking_off → extra_body 下发关闭字段（v8.17.19 通道）",
          getattr(llm_off, "extra_body", None) == off_body,
          str(getattr(llm_off, "extra_body", None)))
    llm_on = get_llm("t-model", "k-1", "https://x", max_tokens=64, thinking_off=False)
    check("默认 → 不发送任何参数（思维链保持开启）",
          llm_on.model_kwargs == {}, str(llm_on.model_kwargs))
    check("缓存键区分 thinking_off", llm_off is not llm_on)
    # 同参数复用：wrapper 每次新建，但底层 primary 客户端进程级复用
    llm_off2 = get_llm("t-model", "k-1", "https://x", max_tokens=64, thinking_off=True)
    check("primary 客户端复用（进程级缓存不回退）",
          getattr(llm_off, "primary", None) is getattr(llm_off2, "primary", None))
    # v8.15.3: retrieve-agent 接线（agent_runner thinking_off=...=="off"）
    from src.core import agent_runner as ar
    ar_src = _inspect.getsource(ar)
    check("retrieve-agent 接线 reasoning_mode==off → thinking_off",
          'agent_name in ("retrieve-agent",)' in ar_src
          and 'MODEL_REASONING_MODE' in ar_src)


# ── F-15.3-4 联网失败详情（HTTP 状态码 / 超时分支）─────────────────
def test_v8153_web_failure_details():
    print("[VF-12] deepseek_web_search 失败详情")
    import src.tools.deepseek_web as dw
    from src.core.tracing import set_web_search_enabled, reset_web_budget

    set_web_search_enabled(True)
    reset_web_budget(1)

    class _FakeResp:
        status_code = 503
        text = "<html>Service Unavailable</html>"

    class _FakeRequestsHTTP:
        def post(self, *a, **k):
            return _FakeResp()

    class _FakeRequestsTimeout(_FakeRequestsHTTP):
        class exceptions:
            class Timeout(Exception):
                pass

        def post(self, *a, **k):
            raise self.exceptions.Timeout("timed out after 30s")

    old = dw.requests
    try:
        dw.requests = _FakeRequestsHTTP()
        c, a = dw.deepseek_web_search.func("最新柑橘行情")
        check("HTTP 失败 → [ERR_NETWORK] 且带状态码",
              c.startswith("[ERR_NETWORK]") and "HTTP 503" in c, c[:80])
        check("HTTP 失败 artifact 双键空", a == {"main_results": [], "web_results": []}, str(a))
        dw.requests = _FakeRequestsTimeout()
        reset_web_budget(1)
        c2, _a2 = dw.deepseek_web_search.func("最新柑橘行情")
        check("超时 → 单独分支含超时提示", c2.startswith("[ERR_NETWORK]") and "超时" in c2, c2[:80])
    finally:
        dw.requests = old
        set_web_search_enabled(False)


# ── F-15.3-6 提示词机制回归（防未来清理误删 v8.15.3 机制标记）──────
def test_v8153_prompt_mechanisms():
    print("[VF-14] 提示词机制标记存在性")
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    # v9.0: 提示词改为"启动时固定拼接"——直接断言最终装配出的固定提示词内容
    from src.prompts.loader import assemble_agent_prompt, assemble_system_prompt
    ra = assemble_agent_prompt("retrieve-agent")
    dg = assemble_system_prompt(mode="expert", format_hint=None, query=None)

    check("retrieve-agent 含数据源覆盖边界", "数据源覆盖边界" in ra)
    check("retrieve-agent 覆盖表(政策/新闻=本地不覆盖)",
          "政府工作报告" in ra and "最新新闻" in ra)
    check("retrieve-agent 早停阈值规则(通过≤2/过滤≥50%)",
          "通过 ≤2 条" in ra and "过滤占比 ≥50%" in ra)
    # v9.1: 联网移出 retrieve-agent（架构变更）；本地拆解唯一层语义
    check("retrieve-agent 无联网工具（v9.1 架构）",
          "deepseek_web_search" not in ra and "citrus_rag_search" in ra)
    check("retrieve-agent 单路（v10.15：不拆角度）",
          "唯一查询" in ra and "不拆解角度" in ra and "调用一次" in ra)
    check("supervisor 统一入口 call_search_both（不预判不跳过）",
          "call_search_both" in dg and "不预先跳过任何一方" in dg
          and "禁止传空字符串" in dg)
    check("supervisor 含覆盖表+自审", "数据源覆盖边界与检索前自审" in dg)
    check("supervisor 含检索后自审(引用对齐)", "检索后自审" in dg and "引用对齐" in dg)
    # ── v10.6（2026-09）架构变更：旧的"证据来源仲裁"已被"本地与联网分离输出"取代 ──
    #   原断言（"证据来源仲裁与引用规则"/"时效信息优先联网"/"冲突并列+禁止捏造折中值"）
    #   描述的是**跨源仲裁**语义，正是本轮决策要删除的东西；继续断言它等于把
    #   被否定的架构锁死。故改为断言**新语义就位 + 旧指令清除**。
    check("supervisor 含分离输出规则（v10.14 两段输出规范取代 v10.6 分离规则）",
          "两段输出规范" in dg)
    check("supervisor 分离: 只挂本地编号、禁止引用 [Wn]",
          "禁止引用 `[Wn]`" in dg and "只挂本地编号" in dg)
    check("supervisor 分离: 不存在跨源优先级",
          "不存在跨源优先级" in dg)
    check("supervisor 分离: 联网信息由系统单独输出、用户自行判断",
          "由用户自行判断" in dg)
    check("旧仲裁指令已清除（v10.6）",
          "证据来源仲裁与引用规则" not in dg
          and "时效信息优先联网" not in dg
          and "禁止捏造折中值" not in dg
          and "联网默认可信" not in dg)


# ── F-15.3-7 单工具超时覆盖（联网工具 120s，防执行层 60s 硬上限误杀）──
def test_v8153_tool_timeout_override():
    print("[VF-15] 单工具超时覆盖")
    from src.tools.registry import _tool_exec_timeout

    check("联网工具放行 120s", _tool_exec_timeout("deepseek_web_search") == 120,
          str(_tool_exec_timeout("deepseek_web_search")))
    check("其他工具默认 60s", _tool_exec_timeout("citrus_rag_search") == 60,
          str(_tool_exec_timeout("citrus_rag_search")))
    # 误配/钳制（覆盖非法值 → 回退默认；负数钳到下限 1s）
    old = settings.TOOL_TIMEOUTS
    try:
        settings.TOOL_TIMEOUTS = {"deepseek_web_search": "abc"}
        check("非法覆盖回退默认", _tool_exec_timeout("deepseek_web_search") == 60,
              str(_tool_exec_timeout("deepseek_web_search")))
        settings.TOOL_TIMEOUTS = {"deepseek_web_search": -3}
        check("超时下限钳制 1s", _tool_exec_timeout("deepseek_web_search") == 1,
              str(_tool_exec_timeout("deepseek_web_search")))
    finally:
        settings.TOOL_TIMEOUTS = old


# ── F-15.3-8 联网工具 goal 驱动（v8.17.15：input 以工具参数 query 开头）──
def test_v8153d_original_query_direct():
    print("[VF-16] 联网工具 goal 驱动（input=工具参数 query）")
    import src.tools.deepseek_web as dw
    from src.core.tracing import set_web_search_enabled, reset_web_budget

    captured: dict = {}

    class _FakeResp:
        status_code = 200

        def json(self):
            return {"output": [
                {"type": "message", "content": [{"type": "output_text", "text":
                    "根据检索，[政府工作报告](https://gov.example.com/report)指出……"}]}]}

    class _FakeRequests:
        def post(self, url, **kw):
            captured["url"] = url
            captured["payload"] = kw.get("json", {})
            return _FakeResp()

    old = dw.requests
    old_style = getattr(dw.settings, "WEB_SEARCH_API_STYLE", "responses")
    dw.settings.WEB_SEARCH_API_STYLE = "responses"  # this test targets the Responses line
    set_web_search_enabled(True)
    reset_web_budget(1)
    try:
        dw.requests = _FakeRequests()
        c, a = dw.deepseek_web_search.func("2025年以来HLB田间种群感染密度")
    finally:
        dw.requests = old
        dw.settings.WEB_SEARCH_API_STYLE = old_style
        set_web_search_enabled(False)

    inp = str(captured.get("payload", {}).get("input", ""))
    check("input 以工具参数 query（goal）开头",
          inp.startswith("2025年以来HLB田间种群感染密度"), inp[:80])
    check("不再引用用户原始问题 contextvar", "原始问题直传" not in inp)
    check("仍要求标注真实网址", "真实网址" in inp)
    check("返回内容含 [W1] 引用", "[W1]" in c, c[:120])
    ok_url = (a.get("web_results") or [{}])[0].get("url") == "https://gov.example.com/report"
    check("artifact 带 URL", bool(a.get("web_results")) and ok_url, str(a))
    # v8.15.3f: 正文 summary 必须进 artifact（此前只进 content，回执拿不到 → 只有 URL 无正文）
    check("artifact 带 web_summary 正文", bool(a.get("web_summary")), str(a.get("web_summary")))


# ── F-15.3-10 网络综述正文进确定性回执（根治"只有 URL 无正文"）──
def test_v8153f_web_summary_in_report():
    """v8.15.3f 的**意图**（联网正文不能被丢掉）保留，**机制**已按 v10.6 反转。

    原口径：把联网综述正文 + [Wn] 清单注入**模型侧回执**，让 supervisor 能引用。
    新口径（分离输出）：联网内容**不进模型上下文**（否则"本地答案不受联网影响"
    只是提示词约定；且模型若挂 [Wn]，净化器剥掉后会留下无出处断言）。
    联网内容改为经 `web_output_separator` 在答案之后**单独输出给用户**——
    所以"不丢"这件事仍然被断言，只是落点从"回执"换成了"分离小节"。
    """
    print("[VF-18] 联网正文的去向：不进模型回执、改走分离小节（v10.6）")
    import os
    from src.core.agent_runner import build_evidence_report
    from src.core.web_output_separator import (SEPARATION, append_web_section,
                                               build_web_section)

    arts = {
        "main_results": [],
        "web_results": [{"ref_id": "W1", "title": "2026柑橘产业报告",
                         "url": "https://gov.example.com/report", "source": "web",
                         "source_tier": "T1"}],
        "web_summaries": ["2026年柑橘产业政府工作报告指出，全年产量同比增长……"],
    }
    rep = build_evidence_report(arts, "2026柑橘政府报告", 0)
    # ── v10.14（阶段 2 · 撤销"隐藏联网正文"入口③）────────────────────────────
    #   依《输出架构重构》§四 第 4 项 + §五 第 9 项：**Supervisor 必须看到联网原文**
    #   （它要据此提炼"第二段：联网小节"）。故这批断言由"**不含**综述段"反转为
    #   "**含**综述段"，同时仍断言**引用池隔离保留**（不出现 [W1]/URL 编号）。
    check("回执**含**网络综述段（阶段2 撤销隐藏后）", "## 网络综述" in rep, rep[:200])
    check("回执**含**综述正文", "2026年柑橘产业政府工作报告指出" in rep)
    # v10.13-B：`[Wn]` 恢复 ⇒ 回执含 [W1] 编号
    check("回执**含** [W1] 编号（供识别引用）", "[W1]" in rep)
    check("回执不再引导挂 [Wn]", "联网事实必须挂 [Wn]" not in rep)
    check("回执含联网条目（带 [Wn] 编号供识别引用）",
          "联网检索结果" in rep or "gov.example.com" in rep, rep[-300:])

    # v10.13（任务书改动 1/3）：条目清单**移到侧栏** ⇒ 正文**不再**出现 URL/分级/🔗
    sep = append_web_section("本地答案 [1]。", arts["web_results"] + [
        {"title": "联网检索综述（未逐条署名）", "text": arts["web_summaries"][0],
         "source_tier": "TU"}], SEPARATION,
        web_prose="2026年柑橘产业政府工作报告指出，全年产量同比增长……")
    check("分离小节含本地答案", sep.startswith("本地答案 [1]。"))
    check("分离小节**不含** URL（条目清单已移到侧栏）",
          "https://gov.example.com/report" not in sep)
    check("分离小节**不含** 🔗 列表（正文 🔗 数 = 0）", "🔗" not in sep)
    check("分离小节**不含**冗长前缀", "以下内容来自公开网络检索" not in sep)
    check("分离小节**不含**来源可靠性提示", "来源可靠性提示" not in sep)
    check("分离小节**不含**来源分级说明", "来源分级仅供参考" not in sep)
    check("分离小节含综述正文", "2026年柑橘产业政府工作报告指出" in sep)
    check("分离小节结尾为 `（联网信息，请自行核验）`",
          sep.rstrip().endswith("（联网信息，请自行核验）"))
    check("分离小节无 [Wn]", "[W" not in sep)

    rep2 = build_evidence_report(
        {"main_results": [], "web_results": arts["web_results"]}, "q", 0)
    check("无摘要时不渲染综述段（但条目仍在）",
          "网络综述" not in rep2 and "联网检索结果" in rep2)

    # 回退档：旧行为必须可恢复（CITRUS_SEPARATE_HIDE_WEB_FROM_MODEL=0）
    _old = os.environ.get("CITRUS_SEPARATE_HIDE_WEB_FROM_MODEL")
    try:
        os.environ["CITRUS_SEPARATE_HIDE_WEB_FROM_MODEL"] = "0"
        SEPARATION.refresh()
        rep3 = build_evidence_report(arts, "2026柑橘政府报告", 0)
        check("回退档恢复综述段", "## 网络综述" in rep3)
        check("回退档恢复 [W1] 清单", "[W1]" in rep3)
        check("回退档恢复 [Wn] 导引", "联网事实必须挂 [Wn]" in rep3)
    finally:
        if _old is None:
            os.environ.pop("CITRUS_SEPARATE_HIDE_WEB_FROM_MODEL", None)
        else:
            os.environ["CITRUS_SEPARATE_HIDE_WEB_FROM_MODEL"] = _old
        SEPARATION.refresh()


# ── F-15.3-9 加载阶段状态推送（消除静默期，前端零改动复用现有事件）──
def test_v8153e_load_progress_events():
    print("[VF-17] load 阶段进度事件")
    import asyncio
    import json
    from pathlib import Path
    from src.core.progress_bus import (
        set_request_queue, clear_request_queue, emit_status, emit_progress)

    q = asyncio.Queue()
    set_request_queue(q)
    try:
        emit_status("step_active", step_id="load")
        emit_progress("tool_progress", {
            "message": "已加载历史 12 条，正在理解问题并规划检索…", "tool_call_id": ""})
        emit_status("step_done", step_id="load")
        items = [q.get_nowait() for _ in range(3)]
    finally:
        clear_request_queue()

    check("事件入队 3 条", len(items) == 3, str(items))
    first = json.loads(items[0]["data"]) if isinstance(items[0].get("data"), str) else {}
    check("step_active 载荷(event=status, stage=step_active)",
          items[0]["event"] == "status" and first.get("stage") == "step_active"
          and first.get("step_id") == "load", str(items[0]))
    second = json.loads(items[1]["data"]) if isinstance(items[1].get("data"), str) else {}
    check("tool_progress 带自定义文案（前端 updateStatus 直显）",
          items[1]["event"] == "tool_progress"
          and "正在理解问题并规划检索" in second.get("message", ""), str(items[1]))
    third = json.loads(items[2]["data"]) if isinstance(items[2].get("data"), str) else {}
    check("step_done 载荷", items[2]["event"] == "status" and third.get("stage") == "step_done",
          str(items[2]))

    # 源码断言：expert/light 两个 load 节点都已接上推送（防静默期回归）
    root = Path(__file__).resolve().parents[1]  # agent/
    ex = (root / "01_源码_src/src/graph/expert_graph.py").read_text(encoding="utf-8")
    lg = (root / "01_源码_src/src/graph/light_graph.py").read_text(encoding="utf-8")
    check("expert load 节点含 step_active+tool_progress 推送",
          'emit_status("step_active", step_id="load")' in ex
          and 'emit_status("step_done", step_id="load")' in ex
          and "正在理解问题并规划检索" in ex)
    check("light load 节点含完成推送", 'emit_status("step_done", step_id="load")' in lg
          and "已加载历史" in lg)


# ── 汇总 ──────────────────────────────────────────────────────────
def _summary():
    print(f"\n[VF-15.3] PASS {len(passed)} / FAIL {len(failed)}"
          f"  ({len(passed) + len(failed)} total)")
    for f in failed:
        print(f"  [FAIL] {f}")
    return len(failed) == 0


if __name__ == "__main__":
    import types as _types
    _funcs = [v for v in list(globals().values())
              if isinstance(v, _types.FunctionType)
              and v.__name__.startswith("test_")]
    for _f in sorted(_funcs, key=lambda x: x.__name__):
        try:
            _f()
        except Exception as _e:
            if os.environ.get("PYTEST_CURRENT_TEST"):
                raise
            print(f"  {_e}")
    ok = _summary()
    sys.exit(0 if ok else 1)