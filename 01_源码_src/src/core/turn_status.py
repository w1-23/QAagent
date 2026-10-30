"""本轮状态：由结构化执行记录推导，不从回答文本猜。

历史问题：`api/main.py` 用 `answer.startswith("本轮未完成")` 判断整轮是否完成，
于是「超时提示」「部分工具失败但模型仍给出回答」这两种情况都可能被判成完成。
这里把判定收敛成一个纯函数，输入是每个工具的真实终态（progress_bus 下发、
general_agent_graph 汇总的 tool_status），便于单测与回归。
"""
from __future__ import annotations

from .turn_notices import INCOMPLETE_PREFIX

SUCCESS_STATUSES = frozenset({"complete", "no_results"})
DEGRADED_STATUSES = frozenset({"partial"})
NOT_EXECUTED_STATUSES = frozenset({"skipped", "not_run"})
FAILED_STATUSES = frozenset({"failed"})
# S1: 用户停止导致的未采用步骤。既不算成功也不算失败，整轮应显示 cancelled。
CANCELLED_STATUSES = frozenset({"cancelled"})

# 回答里出现这些标记说明本轮被时限/上游错误中止：即使前面有工具成功，
# 也不能因此判成 completed。
DEADLINE_MARKERS = (
    "超出请求时限",
    "请求超时",
    "本轮检索或生成超出",
    "模型服务商暂时不可用",
)

VALID_TURN_STATUSES = ("completed", "partial", "failed", "cancelled")


def derive_turn_status(answer: str = "", tool_status: list[dict] | None = None,
                       missing_groups: list | None = None) -> str:
    """返回 completed / partial / failed；只依据机器信号。

    - 有时限/上游中止标记且没有任何一步成功 → failed（有步骤成功 → partial）
    - 有失败或未执行，且没有任何一步成功 → failed
    - 有失败/未执行/部分完成，或回答被标注「本轮未完成」，或明确要求的步骤未调用 → partial
    - 其余 → completed
    """
    records = [r for r in (tool_status or []) if isinstance(r, dict)]
    statuses = [str(r.get("status") or "") for r in records]
    succeeded = any(s in SUCCESS_STATUSES or s in DEGRADED_STATUSES for s in statuses)
    bad = any(s in FAILED_STATUSES or s in NOT_EXECUTED_STATUSES for s in statuses)
    stopped = any(s in CANCELLED_STATUSES for s in statuses)
    head = str(answer or "").lstrip()[:240]
    unfinished_text = head.startswith(INCOMPLETE_PREFIX)
    deadline_hit = any(marker in head for marker in DEADLINE_MARKERS)
    gaps = bool(missing_groups)
    if stopped:
        # S1: 停止是用户决定。没有真实失败 → cancelled；期间确有失败 → 至多
        # partial（不能因为"用户停止"就把整轮判成系统失败）。
        return "partial" if bad else "cancelled"
    if bad and not succeeded:
        return "failed"
    if deadline_hit:
        # 超时中止：有步骤成功也只是部分完成，不能显示为已完成。
        return "partial" if succeeded else "failed"
    if bad or unfinished_text or gaps:
        return "partial"
    return "completed"
