"""A 组静态绑定测试：每个工具的 requires / produces / authorization 是否如实。

这些断言只检查"声明与代码事实是否一致"，不检查模型会不会用工具。目的有两个：

1. 防止再次把"关键词 → 工具可见性"塞回来：声明只有 7 列，没有意图字段。
2. 防止用环境条件替代原来的关键词绑定：例如把"补充材料获取"写成必须上传论文，
   事实上它接受 DOI；把"实验设计导出"写成必须附件，事实上它可以只凭对话生成。

运行：``python src/graph/test_tool_bindings.py``（也兼容 pytest）。
"""
from __future__ import annotations

import inspect
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.graph.tool_availability import (  # noqa: E402
    AUTHORIZATION_EXPLICIT_USER_REQUEST,
    KNOWN_AUTHORIZATIONS,
    KNOWN_PRODUCES,
    KNOWN_REQUIRES,
    Env,
    available_tool_names,
    declarations,
    explain,
)
from src.tools import _TOOL_BINDINGS, _TOOL_DECLARATIONS  # noqa: E402

EXPECTED_TOOLS = {
    "deepseek_search", "deepseek_fetch", "deepseek_fetch_paper", "local_hybrid_search",
    "draw_svg", "read_account_file", "inspect_uploaded_table", "audit_uploaded_data",
    "verify_uploaded_references", "fetch_and_audit_open_supplements",
    "export_literature_evidence", "export_paper_analysis", "export_experiment_design",
    "save_research_note", "recall_research_notes", "export_research_notes",
    "export_corpus_gap_report",
}

RESEARCH_TOOLS = {
    "inspect_uploaded_table", "audit_uploaded_data", "verify_uploaded_references",
    "fetch_and_audit_open_supplements", "export_literature_evidence",
    "export_paper_analysis", "export_experiment_design", "save_research_note",
    "recall_research_notes", "export_research_notes", "export_corpus_gap_report",
}

FAILS: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"PASS  {label}")
    else:
        message = f"{label} :: {detail}" if detail else label
        print(f"FAIL  {message}")
        FAILS.append(message)


def test_registry_shape() -> None:
    names = set(declarations())
    check("17 个工具全部声明", names == EXPECTED_TOOLS,
          f"缺少={sorted(EXPECTED_TOOLS - names)} 多出={sorted(names - EXPECTED_TOOLS)}")
    check("声明列数固定为 7（无意图/关键词列）",
          all(len(row) == 7 for row in _TOOL_DECLARATIONS),
          str({len(row) for row in _TOOL_DECLARATIONS}))
    check("_TOOL_BINDINGS 与声明同源",
          set(_TOOL_BINDINGS) == names and len(_TOOL_BINDINGS) == 17)


def test_vocabulary_is_closed() -> None:
    unknown_requires, unknown_produces, unknown_auth = [], [], []
    for name, (requires, produces, authorization) in declarations().items():
        unknown_requires += [f"{name}:{t}" for t in requires if t not in KNOWN_REQUIRES]
        unknown_produces += [f"{name}:{p}" for p in produces if p not in KNOWN_PRODUCES]
        if authorization and authorization not in KNOWN_AUTHORIZATIONS:
            unknown_auth.append(f"{name}:{authorization}")
    check("requires 只用已知的环境前提", not unknown_requires, str(unknown_requires))
    check("produces 只用已知的成果类型", not unknown_produces, str(unknown_produces))
    check("authorization 只用已知授权", not unknown_auth, str(unknown_auth))


def test_real_preconditions() -> None:
    """每个断言对应一处已核对的工具实现，不是按名字猜的。"""
    requires = {name: set(binding[0]) for name, binding in declarations().items()}

    # util._attachment() / audit 读本轮上传，确实需要附件
    check("inspect_uploaded_table 需要本轮附件", "attachment" in requires["inspect_uploaded_table"])
    check("audit_uploaded_data 需要本轮附件", "attachment" in requires["audit_uploaded_data"])

    # verify_uploaded_references(references_json=...) 不传附件也能核验
    check("verify_uploaded_references 不强制附件",
          "attachment" not in requires["verify_uploaded_references"],
          str(requires["verify_uploaded_references"]))

    # fetch_and_audit_open_supplements(doi) 只查 Europe PMC/PMC OA，接受 DOI；
    # 它会把归档与审查报告写进会话，所以需要联网 + 会话，但**不**需要上传论文。
    check("fetch_and_audit_open_supplements 靠 DOI + 联网，不要求附件",
          requires["fetch_and_audit_open_supplements"] == {"network", "research_tools", "session"},
          str(requires["fetch_and_audit_open_supplements"]))

    # 导出类只依赖模型给出的正文，不依赖附件
    for name in ("export_experiment_design", "export_paper_analysis",
                 "export_literature_evidence", "export_research_notes",
                 "export_corpus_gap_report"):
        check(f"{name} 不要求附件", "attachment" not in requires[name])

    # 联网工具只需要联网；本地检索只需要本地索引
    for name in ("deepseek_search", "deepseek_fetch", "deepseek_fetch_paper"):
        check(f"{name} 只要求联网", requires[name] == {"network"}, str(requires[name]))
    check("local_hybrid_search 只要求本地索引",
          requires["local_hybrid_search"] == {"local_corpus"})

    # 只读笔记回看不需要会话写入能力
    check("recall_research_notes 不要求会话",
          "session" not in requires["recall_research_notes"])


def test_authorization_only_where_needed() -> None:
    authorized = {name: binding[2] for name, binding in declarations().items() if binding[2]}
    check("只有 save_research_note 需要显式授权",
          authorized == {"save_research_note": AUTHORIZATION_EXPLICIT_USER_REQUEST},
          str(authorized))
    produces = {name: set(binding[1]) for name, binding in declarations().items()}
    check("draw_svg 只产出图，不产出报告",
          produces["draw_svg"] == {"figure"}, str(produces["draw_svg"]))
    check("audit_uploaded_data 产出数据审查报告",
          produces["audit_uploaded_data"] == {"audit_report"})
    check("verify_uploaded_references 产出核验表",
          produces["verify_uploaded_references"] == {"verification_sheet"})


def test_availability_follows_environment() -> None:
    # 「健康环境」= 有附件、账号库有文件：这才对应全部 17 个工具都可用的场景。
    healthy = Env(attachments=1, account_files=1)
    check("默认环境下发全部 17 个工具", len(available_tool_names(healthy)) == 17,
          str(available_tool_names(healthy)))

    offline = Env(network=False, attachments=1, account_files=1)
    names = set(available_tool_names(offline))
    check("断网时联网工具消失",
          {"deepseek_search", "deepseek_fetch", "deepseek_fetch_paper",
           "fetch_and_audit_open_supplements"} & names == set(),
          str(names))
    check("断网不影响本地检索与导出", {"local_hybrid_search",
                                      "export_experiment_design"} <= names)

    no_upload = Env(attachments=0)
    names = set(available_tool_names(no_upload))
    check("无附件时上传类工具消失",
          {"inspect_uploaded_table", "audit_uploaded_data"} & names == set(), str(names))
    check("无附件时导出与核验仍在",
          {"export_experiment_design", "verify_uploaded_references"} <= names)

    with_upload = Env(attachments=1)
    check("有附件时上传类工具回来",
          {"inspect_uploaded_table", "audit_uploaded_data"} <= set(available_tool_names(with_upload)))

    no_research = Env(research_tools=False, attachments=1, account_files=1)
    names = set(available_tool_names(no_research))
    check("关闭科研工具后只剩 6 个基础工具",
          names == EXPECTED_TOOLS - RESEARCH_TOOLS, str(sorted(names)))
    check("这 6 个与图里 _toolset() 的基础集合一致",
          names == {"deepseek_search", "deepseek_fetch", "deepseek_fetch_paper",
                    "local_hybrid_search", "draw_svg", "read_account_file"})

    no_session = Env(session=False, attachments=1, account_files=1)
    names = set(available_tool_names(no_session))
    check("无会话时成果写入类工具消失",
          not ({"export_experiment_design", "audit_uploaded_data",
                "save_research_note"} & names), str(names))
    check("无会话不影响只读工具", {"deepseek_search", "local_hybrid_search"} <= names)

    denied = Env(denied=frozenset({"deepseek_search", "draw_svg"}),
                 attachments=1, account_files=1)
    names = set(available_tool_names(denied))
    check("用户明确禁用的工具不下发",
          {"deepseek_search", "draw_svg"} & names == set(), str(names))


def test_explain_matches_availability() -> None:
    env = Env(network=False, attachments=2)
    reasons = explain(env)
    names = set(available_tool_names(env))
    check("可用工具的解释为空",
          all(reasons[name] == "" for name in names), str({n: r for n, r in reasons.items() if r}))
    check("不可用工具都有具体原因",
          all(reasons[name] for name in reasons if name not in names),
          str({n: r for n, r in reasons.items() if n not in names and not r}))
    check("原因指向环境而不是主题词",
          "联网" in reasons["deepseek_search"], reasons["deepseek_search"])


def test_no_keyword_gate_in_module() -> None:
    """能力过滤只能看环境，不能读用户问题——用签名与依赖来约束，而不是查字串。"""
    from src.graph import tool_availability as module

    for func in (module.available_tool_names, module.explain, module.missing_requirements):
        params = set(inspect.signature(func).parameters)
        check(f"{func.__name__} 只接受工具名与环境", params <= {"name", "tool_name", "env"},
              str(params))
    source = (pathlib.Path(__file__).parent / "tool_availability.py").read_text("utf-8")
    check("能力过滤不导入正则库", "import re" not in source)
    # 任何接受自由文本的函数都等同于关键词门控的入口，这里不允许存在。
    free_text = [
        line for line in source.splitlines()
        if line.lstrip().startswith("def ") and any(
            f"{word}" in line for word in ("query", "question", "message", "text:"))
    ]
    check("没有接受自由文本的入口", not free_text, str(free_text))


def test_artifact_turn_binding_is_plumbed() -> None:
    """约束 C 的最小前置：文件登记必须带"本轮 id + 生产者工具"。"""
    from src.core import tracing
    from src.session import files as session_files

    check("tracing 暴露 set_client_request_id", hasattr(tracing, "set_client_request_id"))
    check("tracing 暴露 get_current_tool_name", hasattr(tracing, "get_current_tool_name"))
    params = inspect.signature(session_files.register_file).parameters
    check("register_file 接受 client_request_id", "client_request_id" in params)
    check("register_file 接受 producer_tool", "producer_tool" in params)
    tools_source = (ROOT / "src" / "research" / "tools.py").read_text("utf-8")
    check("_save_artifact 传本轮 id 与生产者",
          "client_request_id=get_client_request_id()" in tools_source
          and "producer_tool=get_current_tool_name()" in tools_source)


def main() -> int:
    for test in (test_registry_shape, test_vocabulary_is_closed, test_real_preconditions,
                 test_authorization_only_where_needed, test_availability_follows_environment,
                 test_explain_matches_availability, test_no_keyword_gate_in_module,
                 test_artifact_turn_binding_is_plumbed):
        print(f"\n--- {test.__name__}")
        test()
    print(f"\nFAILS: {len(FAILS)}")
    for message in FAILS:
        print("  -", message)
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
