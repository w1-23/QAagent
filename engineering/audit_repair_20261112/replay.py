#!/usr/bin/env python
"""Read-only, stdlib-only replay of trusted source against selected text inputs.

Run from the trusted worktree with Python -I -X utf8. No project module,
external Python, runner, harness, service, network, model, artifact, or scorer
is imported or invoked. Only this new directory receives output.
"""
from __future__ import annotations

import argparse
import ast
import builtins
import collections
import csv
import dataclasses
import hashlib
import io
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import types
from typing import Any

BASELINE = "318b935266f2f3c56e30f7a37c404428975acb7f"
RELATIVE_SOURCES = {
    "intent": "01_src/src/graph/intent_text.py",
    "contract": "01_src/src/graph/export_contract.py",
    "router": "01_src/src/graph/retrieval_router.py",
    "binding": "01_src/src/graph/general_agent_graph.py",
    "policy": "01_src/src/graph/tool_policy.py",
}
SELECTED_IDS = tuple(
    "TD" + value for value in
    ("044", "045", "046", "047", "052", "053", "054", "061", "062", "063", "070", "071", "072", "078")
)
FORMAL_IDS = (
    tuple(f"L-N{i:02d}" for i in range(1, 9))
    + tuple(f"W{i:02d}" for i in range(1, 9))
    + tuple(f"R{i:02d}" for i in range(1, 5))
    + tuple(f"FW{i:02d}" for i in range(1, 21))
)
STRING_TOKEN = r'"(?:[^"\\]|\\.)*"'
ID_FIELD = re.compile(r'"task_id"\s*:\s*(' + STRING_TOKEN + r')')
PROMPT_FIELD = re.compile(r'"prompt"\s*:\s*(' + STRING_TOKEN + r')')


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def text_sha(text: str) -> str:
    return sha256(text.encode("utf-8"))


def git(repo: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout


def file_input(path: Path, raw: bytes) -> dict[str, Any]:
    return {"path": path.as_posix(), "bytes": len(raw), "sha256": sha256(raw)}


def index_evidence(repo: Path) -> dict[str, Any]:
    index = Path(git(repo, "rev-parse", "--git-path", "index").decode("utf-8").strip())
    if not index.is_absolute():
        index = repo / index
    return file_input(index.resolve(), index.read_bytes())


def normalize(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {f.name: normalize(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {str(k): normalize(v) for k, v in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted(normalize(v) for v in value)
    if isinstance(value, (list, tuple)):
        return [normalize(v) for v in value]
    return value


def symbol_names(node: ast.AST) -> set[str]:
    if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
        return {node.name}
    if isinstance(node, ast.Assign):
        return {n.id for target in node.targets for n in ast.walk(target) if isinstance(n, ast.Name)}
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return {node.target.id}
    return set()


def load_pure(
    text: str, filename: str, roots: list[str], namespace_name: str,
    supplied: dict[str, Any] | None = None,
) -> tuple[types.ModuleType, dict[str, Any]]:
    """Compile an AST dependency closure; imports and unrelated code are omitted.

    The only input code is trusted git-show/worktree source. This is never used
    for external files. Known stdlib dependencies and pure intent helpers are
    supplied explicitly instead of importing the application and its startup.
    """
    tree = ast.parse(text, filename=filename)
    definitions = {name: node for node in tree.body for name in symbol_names(node)}
    missing = sorted(set(roots) - definitions.keys())
    if missing:
        raise ValueError(f"Missing pure source roots in {filename}: {missing}")
    selected: set[str] = set()
    pending = list(roots)
    while pending:
        name = pending.pop()
        if name in selected or name not in definitions:
            continue
        selected.add(name)
        node = definitions[name]
        pending.extend(
            n.id for n in ast.walk(node)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
            and n.id in definitions and n.id not in selected
        )
    chosen = [node for node in tree.body if symbol_names(node) & selected]
    forbidden_nodes = (ast.Import, ast.ImportFrom, ast.AsyncFunctionDef, ast.Await)
    forbidden_calls = {"open", "eval", "exec", "compile", "__import__", "input", "breakpoint"}
    for node in chosen:
        for part in ast.walk(node):
            if isinstance(part, forbidden_nodes):
                raise ValueError(f"Non-pure dependency in {filename}:{getattr(part, 'lineno', '?')}")
            if isinstance(part, ast.Call) and isinstance(part.func, ast.Name) and part.func.id in forbidden_calls:
                raise ValueError(f"Prohibited call in {filename}: {part.func.id}")
    module = types.ModuleType(namespace_name)
    module.__file__ = filename
    module.__dict__.update({
        "__builtins__": builtins.__dict__, "re": re, "Any": Any,
        "dataclass": dataclasses.dataclass, "field": dataclasses.field,
    })
    module.__dict__.update(supplied or {})
    # dataclasses consults sys.modules for string annotations. No import occurs.
    sys.modules[namespace_name] = module
    code_tree = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *chosen],
        type_ignores=[],
    )
    ast.fix_missing_locations(code_tree)
    exec(compile(code_tree, filename, "exec"), module.__dict__)
    lines = text.splitlines(keepends=True)
    slices = []
    for node in chosen:
        start = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
        exact = "".join(lines[start - 1:node.end_lineno])
        slices.append({
            "symbols": sorted(symbol_names(node)), "start_line": start,
            "end_line": node.end_lineno, "source_text": exact,
            "source_text_utf8_sha256": text_sha(exact),
        })
    extracted = ast.unparse(code_tree) + "\n"
    return module, {
        "roots": roots, "selected_symbols": sorted(selected),
        "original_source_slices": slices,
        "compiled_ast_unparse_utf8_sha256": text_sha(extracted),
    }


def source_replay(repo: Path, version: str, commit: str | None) -> dict[str, Any]:
    source_texts: dict[str, str] = {}
    provenance: dict[str, Any] = {}
    for key, relative in RELATIVE_SOURCES.items():
        path = (repo / relative).resolve()
        if not path.is_relative_to(repo):
            raise ValueError(f"Source resolves outside trusted worktree: {path}")
        if commit:
            raw = git(repo, "show", f"{commit}:{relative}")
            origin = f"git show {commit}:{relative}"
        else:
            raw = path.read_bytes()
            origin = "trusted worktree bytes"
        source_texts[key] = raw.decode("utf-8-sig")
        provenance[key] = {
            **file_input(path, raw), "relative_path": relative,
            "origin": origin,
        }
    prefix = f"_audit_repair_{version}"
    intent, extracted = load_pure(
        source_texts["intent"], provenance["intent"]["origin"] + ":intent_text.py",
        ["intent_clauses", "negated_before"], prefix + "_intent",
    )
    provenance["intent"]["pure_extraction"] = extracted
    contract, extracted = load_pure(
        source_texts["contract"], provenance["contract"]["origin"] + ":export_contract.py",
        ["deliverable_contract"], prefix + "_contract",
        {"intent_clauses": intent.intent_clauses, "negated_before": intent.negated_before},
    )
    provenance["contract"]["pure_extraction"] = extracted
    router_tree = ast.parse(source_texts["router"])
    router_functions = {n.name for n in router_tree.body if isinstance(n, ast.FunctionDef)}
    router_roots = ["parse_request_constraints"]
    router_roots.extend(name for name in ("source_scope", "retrieval_denied") if name in router_functions)
    router, extracted = load_pure(
        source_texts["router"], provenance["router"]["origin"] + ":retrieval_router.py",
        router_roots, prefix + "_router",
    )
    provenance["router"]["pure_extraction"] = extracted
    binding_tree = ast.parse(source_texts["binding"])
    binding_defined = any(isinstance(n, ast.FunctionDef) and n.name == "_source_scope" for n in binding_tree.body)
    if binding_defined:
        binding, extracted = load_pure(
            source_texts["binding"], provenance["binding"]["origin"] + ":general_agent_graph.py",
            ["_source_scope"], prefix + "_binding",
        )
        binding_scope = binding._source_scope
        provenance["binding"]["pure_extraction"] = extracted
    else:
        aliases = [
            node for node in binding_tree.body if isinstance(node, ast.ImportFrom)
            and node.module == "src.graph.retrieval_router"
            and any(a.name == "source_scope" and a.asname == "_source_scope" for a in node.names)
        ]
        if len(aliases) != 1 or not hasattr(router, "source_scope"):
            raise ValueError("Cannot establish the trusted binding source-scope alias")
        binding_scope = router.source_scope
        provenance["binding"]["pure_extraction"] = {
            "executed": False, "alias_evidence": ast.get_source_segment(source_texts["binding"], aliases[0]),
            "alias_line": aliases[0].lineno, "resolved_to": "retrieval_router.source_scope",
        }
    supplied = {"retrieval_denied": router.retrieval_denied} if hasattr(router, "retrieval_denied") else {}
    policy, extracted = load_pure(
        source_texts["policy"], provenance["policy"]["origin"] + ":tool_policy.py",
        ["resolve_tool_policy"], prefix + "_policy", supplied,
    )
    provenance["policy"]["pure_extraction"] = extracted
    return {
        "contract": contract.deliverable_contract, "binding_scope": binding_scope,
        "constraints": router.parse_request_constraints, "policy": policy.resolve_tool_policy,
        "provenance": provenance,
    }


def evaluate_scope(replay: dict[str, Any], prompt: str) -> dict[str, Any]:
    binding_scope = replay["binding_scope"](prompt)
    policy = replay["policy"](prompt, binding_scope)
    return {
        "binding_source_scope": binding_scope,
        "constraints_auto": normalize(replay["constraints"](prompt, "auto")),
        "constraints_binding_scope": normalize(replay["constraints"](prompt, binding_scope)),
        "tool_policy_binding_scope": {
            **normalize(policy),
            "allows": {name: policy.allows(name) for name in (
                "local_hybrid_search", "deepseek_search", "deepseek_fetch", "deepseek_fetch_paper",
            )},
        },
    }


def aggregate_contracts(cases: list[dict[str, Any]], version: str) -> dict[str, Any]:
    contracts = [case[version] for case in cases if isinstance(case.get(version), list)]
    requirements = [requirement for contract in contracts for requirement in contract]
    return {
        "cases_evaluated": len(contracts), "requirements": len(requirements),
        "cases_without_requirements": sum(not contract for contract in contracts),
        "requirement_count_histogram": dict(sorted(collections.Counter(len(c) for c in contracts).items())),
        "kind_memberships": dict(sorted(collections.Counter(
            kind for requirement in requirements for kind in requirement.get("kinds", [])
        ).items())),
        "format_memberships": dict(sorted(collections.Counter(
            fmt for requirement in requirements for fmt in requirement.get("formats", [])
        ).items())),
        "requirements_without_kinds": sum(not r.get("kinds") for r in requirements),
        "requirements_without_formats": sum(not r.get("formats") for r in requirements),
        "body_scope_fields": dict(sorted(collections.Counter(
            r["body_scope"] for r in requirements if "body_scope" in r
        ).items())),
    }


def without_body_scope(contract: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{key: value for key, value in r.items() if key != "body_scope"} for r in contract]


def md(value: Any) -> str:
    return str(value).replace("\\", "\\\\").replace("|", "\\|").replace("\r", "").replace("\n", "<br>").replace("`", "&#96;")


def product_text(contract: Any) -> str:
    if not isinstance(contract, list):
        return "FAILED"
    if not contract:
        return "(none)"
    return "<br>".join(
        md(r.get("product_id", "")) + " · "
        + md("+".join(r.get("kinds", [])) or "(no kind)") + " / "
        + md("+".join(r.get("formats", [])) or "(no format)")
        + (" · body=" + md(r["body_scope"]) if "body_scope" in r else "")
        + " · " + md(r.get("label", ""))
        for r in contract
    )


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Source-only audit repair evidence replay", "",
        "This records observed pure predicate output. No benchmark, experiment, harness, service, "
        "network, model, gold, scorer, attachment body, generated artifact, or historical answer was executed/evaluated. "
        "The 40 formal cases are prompt-field replays only, including FW20; they are not reruns of those tasks.", "",
        f"Baseline: `{report['baseline_commit']}` loaded only with `git show` in the trusted checkout. "
        "Current: the worktree bytes whose exact hashes and pure source slices are embedded in the JSON.", "",
        f"Exit code: **{report['exit_code']}**. Failures: **{len(report['failures'])}**. "
        f"Warnings: **{len(report['warnings'])}**.", "",
        "## Reproduction command", "", "```bash", report["invocation"]["command"], "```", "",
        "Run from the trusted worktree. Python isolation and UTF-8 mode are checked before reading external data. "
        "All output paths are restricted to this new audit directory. Reproduction uses current worktree source; "
        "the recorded hashes identify the particular observed source state and can be checked before rerunning.", "",
        "## Observed totals", "",
        "| Measure | Baseline | Current |", "|---|---:|---:|",
    ]
    for label, key in (("Formal prompts evaluated", "cases_evaluated"), ("Requirements", "requirements"),
                       ("Cases without requirements", "cases_without_requirements"),
                       ("Requirements without kinds", "requirements_without_kinds"),
                       ("Requirements without formats", "requirements_without_formats")):
        lines.append(f"| {label} | {summary['formal']['baseline'][key]} | {summary['formal']['current'][key]} |")
    lines.extend([
        "", f"Full contract output changed in {summary['formal']['full_contract_changed_cases']}/40 cases. "
        f"Ignoring only the newly introduced body_scope field, output changed in "
        f"{summary['formal']['changed_cases_ignoring_body_scope']}/40 cases; requirement counts changed in "
        f"{summary['formal']['requirement_count_changed_cases']}/40 cases.", "",
        "Kind/format totals count membership in each requirement; a requirement can have multiple kinds/formats. "
        "An absent field in baseline output is preserved as absent, never interpreted as an observed baseline scope.", "",
        "| Kind | Baseline memberships | Current memberships |", "|---|---:|---:|",
    ])
    for key in sorted(set(summary["formal"]["baseline"]["kind_memberships"]) | set(summary["formal"]["current"]["kind_memberships"])):
        lines.append(f"| {key} | {summary['formal']['baseline']['kind_memberships'].get(key, 0)} | {summary['formal']['current']['kind_memberships'].get(key, 0)} |")
    lines.extend(["", "| Format | Baseline memberships | Current memberships |", "|---|---:|---:|"])
    for key in sorted(set(summary["formal"]["baseline"]["format_memberships"]) | set(summary["formal"]["current"]["format_memberships"])):
        lines.append(f"| {key} | {summary['formal']['baseline']['format_memberships'].get(key, 0)} | {summary['formal']['current']['format_memberships'].get(key, 0)} |")
    lines.extend(["", "## Annotation and body-scope focus", ""])
    for case_id in ("FW11", "FW12", "FW20", "FW18"):
        focus = report["focus"][case_id]
        lines.extend([f"### {case_id}", "", "| Version | Observed products |", "|---|---|",
                      "| Baseline | " + product_text(focus.get("baseline")) + " |",
                      "| Current | " + product_text(focus.get("current")) + " |", ""])
    lines.extend([
        "FW18 baseline has no body_scope field or report_body_scope predicate. The current scope values "
        "above are actual per-product parser output. Removing accidental requirements and separating product "
        "bodies does not establish that the model will produce a correct report or annotation.", "",
        "## Formal comparison by case", "",
        "Exact prompt strings, original run.json paths, full-file SHA256, exact UTF-8 prompt SHA256, "
        "stored historical prompt digests, and full product dictionaries are preserved in comparison.json.", "",
        "| Case | Count baseline → current | Baseline kinds / formats / product IDs / labels | Current kinds / formats / product IDs / labels |",
        "|---|---:|---|---|",
    ])
    for case in report["formal_cases"]:
        baseline, current = case.get("baseline"), case.get("current")
        lines.append(f"| {case['case_id']} | {len(baseline) if isinstance(baseline, list) else 'FAILED'} → "
                     f"{len(current) if isinstance(current, list) else 'FAILED'} | {product_text(baseline)} | {product_text(current)} |")
    lines.extend([
        "", "## Selected local-only prompt predicates", "",
        "Only the 14 requested task IDs' prompt strings were decoded and passed to source predicates. "
        "Other prompts and all capability/gold/scoring fields were not decoded or inspected. The JSONL bytes "
        "were streamed for a full-file checksum and task-ID selection. No benchmark result or score was calculated.", "",
        "The binding parser is the historical general_agent_graph._source_scope versus the current imported "
        "retrieval_router.source_scope alias. Constraints are called with both auto and the binding scope. "
        "Tool policy means resolve_tool_policy on that text/scope only; it is not a runtime tool exposure check.", "",
        "| Measure | Baseline | Current |", "|---|---:|---:|",
    ])
    for label, key in (("Prompts evaluated", "cases_evaluated"), ("Binding scope local", "binding_local"),
                       ("Auto constraints require local", "auto_constraints_required_local"),
                       ("Binding-scope constraints require local", "binding_constraints_required_local"),
                       ("Policy allows local and denies named web tools", "policy_local_only")):
        lines.append(f"| {label} | {summary['selected_scope']['baseline'][key]} | {summary['selected_scope']['current'][key]} |")
    lines.extend(["", "| ID | Baseline binding / auto constraint | Current binding / auto constraint | Selected original prompt |", "|---|---|---|---|"])
    for case in report["selected_local_prompts"]:
        def scope_label(version: str) -> str:
            result = case.get(version)
            if not isinstance(result, dict):
                return "FAILED"
            c = result["constraints_auto"]
            return md(f"{result['binding_source_scope']} / {c['source_policy']}:{c['requested_mode']} ({c['reason_code']})")
        lines.append(f"| {case['case_id']} | {scope_label('baseline')} | {scope_label('current')} | {md(case['prompt'])} |")
    lines.extend(["", "## Provenance and boundaries", "", "| Input | SHA256 |", "|---|---|"])
    for item in report["metadata_inputs"] + [report["selected_jsonl_input"]]:
        lines.append(f"| {md(item['path'])} | {item['sha256']} |")
    for version in ("baseline", "current"):
        for source in report["source_inputs"].get(version, {}).values():
            lines.append(f"| {version}: {md(source['relative_path'])} | {source['sha256']} |")
    lines.extend([
        "", f"Formal run.json inventory hashes matched: {summary['integrity']['formal_inventory_hash_matches']}/"
        f"{summary['integrity']['formal_files_read']}. All external inputs and current source bytes were rehashed "
        "before writing results; observed input/source drift is reported as a failure. Git-index hashes were "
        "recorded before/after (the script never invokes an index-writing command).", "",
        "Historical metadata describes a frozen earlier runtime; the trusted commit selected here is an "
        "engineering comparison baseline, not a claim that it produced those historical results. This replay "
        "does not validate attachment processing, merged cells, output artifacts, model behavior, delivery success, "
        "or score improvements. Existing reports, logs, tests, runner, production sources, historical files, "
        "gold/scorer data, and git index receive no writes.", "",
        "## Failures", "",
    ])
    lines.extend(["- " + md(item) for item in report["failures"]] or ["None observed."])
    lines.extend(["", "## Warnings and expectation differences", ""])
    lines.extend(["- " + md(item) for item in report["warnings"]] or ["None observed."])
    lines.extend(["", "## Replay expectations", "", "| Check | Observed | Result |", "|---|---|---|"])
    for check in report["checks"]:
        lines.append(f"| {md(check['check'])} | {md(check['observed'])} | {'PASS' if check['passed'] else 'FAIL'} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--audit-root", type=Path, required=True)
    parser.add_argument("--formal-root", type=Path, required=True)
    parser.add_argument("--selected-jsonl", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    script = Path(__file__).resolve()
    repo = args.repo.resolve()
    output = args.output_dir.resolve()
    if not sys.flags.isolated or not sys.flags.utf8_mode:
        parser.error("Required interpreter flags: -I -X utf8")
    if repo != script.parents[2] or Path.cwd().resolve() != repo:
        parser.error("Run from the trusted worktree containing this script")
    if output != script.parent or output != repo / "engineering" / "audit_repair_20261112":
        parser.error("Output must be the new engineering/audit_repair_20261112 directory")
    sys.dont_write_bytecode = True
    audit_root = args.audit_root.resolve()
    formal_root = args.formal_root.resolve()
    selected_jsonl = args.selected_jsonl.resolve()
    if not formal_root.is_relative_to(audit_root):
        parser.error("Formal data must be under the supplied external audit root")
    failures: list[str] = []
    warnings: list[str] = []
    checks: list[dict[str, Any]] = []
    input_snapshots: dict[Path, str] = {}

    def read_external(path: Path) -> bytes:
        path = path.resolve()
        if not (path.is_relative_to(audit_root) or path == selected_jsonl):
            raise ValueError(f"External read outside specified input paths: {path}")
        raw = path.read_bytes()
        input_snapshots[path] = sha256(raw)
        return raw

    def check(name: str, passed: bool, observed: Any) -> None:
        checks.append({"check": name, "passed": bool(passed), "observed": observed})
        if not passed:
            failures.append(f"{name}: {observed}")

    command = shlex.join([sys.executable, "-I", "-X", "utf8", str(script), *sys.argv[1:]])
    report: dict[str, Any] = {
        "schema_version": 1, "baseline_commit": BASELINE,
        "invocation": {"command": command, "cwd": repo.as_posix(),
                       "python_executable": sys.executable, "python_version": sys.version,
                       "isolated": bool(sys.flags.isolated), "utf8_mode": bool(sys.flags.utf8_mode),
                       "script": file_input(script, script.read_bytes())},
        "boundary": {"replay_only": True, "baseline_code_origin": "trusted checkout git show only",
                     "external_data_executed": False, "application_imported": False,
                     "network_model_service_calls": 0, "benchmarks_or_harnesses_executed": 0,
                     "outputs_restricted_to": output.as_posix()},
        "metadata_inputs": [], "source_inputs": {}, "formal_cases": [],
        "selected_local_prompts": [], "focus": {}, "checks": checks,
        "failures": failures, "warnings": warnings,
    }
    index_before = index_evidence(repo)
    report["git_index_before"] = index_before
    commit = git(repo, "rev-parse", "--verify", BASELINE + "^{commit}").decode("ascii").strip()
    check("Trusted baseline commit resolves exactly", commit == BASELINE, commit)
    replays: dict[str, dict[str, Any]] = {}
    for version, commit_arg in (("baseline", BASELINE), ("current", None)):
        try:
            replays[version] = source_replay(repo, version, commit_arg)
            report["source_inputs"][version] = replays[version]["provenance"]
        except Exception as exc:
            failures.append(f"{version} source extraction: {type(exc).__name__}: {exc}")

    inventory: dict[str, dict[str, str]] = {}
    for filename in ("REVIEW_GUIDE.md", "RUN_LOG.md", "INVENTORY.csv"):
        path = audit_root / filename
        try:
            raw = read_external(path)
            report["metadata_inputs"].append(file_input(path, raw))
            if filename == "INVENTORY.csv":
                inventory = {row["path"].replace("\\", "/"): row for row in csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))}
        except Exception as exc:
            failures.append(f"Metadata {path}: {type(exc).__name__}: {exc}")
    formal_paths = sorted(formal_root.glob("*/run.json"), key=lambda p: p.parent.name)
    check("Formal record count is 40", len(formal_paths) == 40, len(formal_paths))
    observed_ids = []
    for path in formal_paths:
        try:
            raw = read_external(path)
            # Only task_id, prompt, and the historical prompt checksum are used.
            # Answers, tool traces, attachment contents, and results are ignored.
            record = json.loads(raw.decode("utf-8-sig"))
            case_id, prompt = record["task_id"], record["prompt"]
            if not isinstance(case_id, str) or not isinstance(prompt, str):
                raise TypeError("Expected string task_id and prompt fields")
            observed_ids.append(case_id)
            check(f"{case_id}: directory agrees with task_id", case_id == path.parent.name, path.parent.name)
            inv_key = audit_root.name + "/" + path.relative_to(audit_root).as_posix()
            inventory_row = inventory.get(inv_key)
            file_hash_match = bool(inventory_row and inventory_row.get("sha256", "").casefold() == sha256(raw))
            check(f"{case_id}: run.json SHA256 matches inventory", file_hash_match, sha256(raw))
            prompt_hash = text_sha(prompt)
            stored_hash = record.get("prompt_sha256")
            suffix_marker = "\n\n【随题附件】"
            prefix = prompt.split(suffix_marker, 1)[0] if suffix_marker in prompt else None
            prefix_hash = text_sha(prefix) if prefix is not None else None
            case = {
                "case_id": case_id, "input": file_input(path, raw),
                "prompt_field": "/prompt", "prompt": prompt, "prompt_utf8_sha256": prompt_hash,
                "historical_prompt_sha256": stored_hash,
                "historical_sha_matches_full_stored_prompt": stored_hash == prompt_hash,
                "pre_attachment_prefix_utf8_sha256": prefix_hash,
                "historical_sha_matches_pre_attachment_prefix": prefix_hash is not None and stored_hash == prefix_hash,
                "inventory_record": inventory_row, "inventory_sha256_matches": file_hash_match,
            }
            for version, replay in replays.items():
                try:
                    case[version] = normalize(replay["contract"](prompt))
                except Exception as exc:
                    case[version] = {"error": f"{type(exc).__name__}: {exc}"}
                    failures.append(f"{case_id} {version} deliverable_contract: {type(exc).__name__}: {exc}")
            report["formal_cases"].append(case)
        except Exception as exc:
            failures.append(f"Formal input {path}: {type(exc).__name__}: {exc}")
    check("Formal IDs match the specified 40-case set", sorted(observed_ids) == sorted(FORMAL_IDS), observed_ids)

    selected: dict[str, dict[str, Any]] = {}
    try:
        # Hash all bytes, but parse only ID strings and the 14 selected prompt
        # string tokens. Capability, gold, judgement and scoring fields stay opaque.
        digest = hashlib.sha256()
        total_bytes = 0
        with selected_jsonl.open("rb") as handle:
            for line_number, raw_line in enumerate(handle, 1):
                digest.update(raw_line)
                total_bytes += len(raw_line)
                line = raw_line.decode("utf-8-sig")
                id_match = ID_FIELD.search(line)
                if not id_match:
                    continue
                case_id = json.loads(id_match.group(1))
                if case_id not in SELECTED_IDS:
                    continue
                if case_id in selected:
                    raise ValueError(f"Duplicate selected ID {case_id}")
                prompt_match = PROMPT_FIELD.search(line)
                if not prompt_match:
                    raise ValueError(f"No prompt string for selected ID {case_id}")
                prompt = json.loads(prompt_match.group(1))
                case = {
                    "case_id": case_id, "path": selected_jsonl.as_posix(), "line_number": line_number,
                    "raw_line_sha256": sha256(raw_line), "prompt_field": "prompt",
                    "prompt": prompt, "prompt_utf8_sha256": text_sha(prompt),
                }
                for version, replay in replays.items():
                    try:
                        case[version] = evaluate_scope(replay, prompt)
                    except Exception as exc:
                        case[version] = {"error": f"{type(exc).__name__}: {exc}"}
                        failures.append(f"{case_id} {version} source predicates: {type(exc).__name__}: {exc}")
                selected[case_id] = case
        input_snapshots[selected_jsonl] = digest.hexdigest()
        report["selected_jsonl_input"] = {
            "path": selected_jsonl.as_posix(), "bytes": total_bytes, "sha256": digest.hexdigest(),
            "selected_ids": list(SELECTED_IDS), "evaluated_prompt_count": len(selected),
            "unselected_prompt_values_decoded": 0,
            "gold_capability_scoring_values_decoded": 0,
        }
    except Exception as exc:
        failures.append(f"Selected JSONL input: {type(exc).__name__}: {exc}")
        report["selected_jsonl_input"] = {"path": selected_jsonl.as_posix(), "sha256": "FAILED"}
    report["selected_local_prompts"] = [selected[case_id] for case_id in SELECTED_IDS if case_id in selected]
    check("Exactly the 14 selected prompt strings were evaluated", set(selected) == set(SELECTED_IDS), list(selected))

    summary: dict[str, Any] = {"formal": {}, "selected_scope": {}, "integrity": {}}
    for version in ("baseline", "current"):
        summary["formal"][version] = aggregate_contracts(report["formal_cases"], version)
        results = [case[version] for case in report["selected_local_prompts"] if version in case and "error" not in case[version]]
        summary["selected_scope"][version] = {
            "cases_evaluated": len(results),
            "binding_local": sum(result["binding_source_scope"] == "local" for result in results),
            "auto_constraints_required_local": sum(result["constraints_auto"]["source_policy"] == "required" and result["constraints_auto"]["requested_mode"] == "local" for result in results),
            "binding_constraints_required_local": sum(result["constraints_binding_scope"]["source_policy"] == "required" and result["constraints_binding_scope"]["requested_mode"] == "local" for result in results),
            "policy_local_only": sum(result["tool_policy_binding_scope"]["allows"]["local_hybrid_search"] and not any(result["tool_policy_binding_scope"]["allows"][name] for name in ("deepseek_search", "deepseek_fetch", "deepseek_fetch_paper")) for result in results),
            "binding_scope_counts": dict(sorted(collections.Counter(result["binding_source_scope"] for result in results).items())),
            "auto_constraint_mode_counts": dict(sorted(collections.Counter(result["constraints_auto"]["requested_mode"] for result in results).items())),
        }
    paired = [case for case in report["formal_cases"] if isinstance(case.get("baseline"), list) and isinstance(case.get("current"), list)]
    for key, predicate in (
        ("full_contract_changed_cases", lambda c: c["baseline"] != c["current"]),
        ("changed_cases_ignoring_body_scope", lambda c: without_body_scope(c["baseline"]) != without_body_scope(c["current"])),
        ("requirement_count_changed_cases", lambda c: len(c["baseline"]) != len(c["current"])),
    ):
        ids = [case["case_id"] for case in paired if predicate(case)]
        summary["formal"][key] = len(ids)
        summary["formal"][key + "_ids"] = ids
    mismatches = [case for case in report["formal_cases"] if not case["historical_sha_matches_full_stored_prompt"]]
    prefix_matches = [case for case in mismatches if case["historical_sha_matches_pre_attachment_prefix"]]
    unexplained = [case["case_id"] for case in mismatches if not case["historical_sha_matches_pre_attachment_prefix"]]
    summary["integrity"] = {
        "formal_files_read": len(report["formal_cases"]),
        "formal_inventory_hash_matches": sum(case["inventory_sha256_matches"] for case in report["formal_cases"]),
        "historical_prompt_sha_matches_full_field": len(report["formal_cases"]) - len(mismatches),
        "historical_prompt_sha_mismatches_full_field": len(mismatches),
        "mismatches_explained_by_pre_attachment_prefix": len(prefix_matches),
        "mismatch_ids": [case["case_id"] for case in mismatches],
        "unexplained_mismatch_ids": unexplained,
    }
    if mismatches:
        warnings.append(
            f"Historical prompt_sha256 differs from the exact stored /prompt field in {len(mismatches)}/"
            f"{len(report['formal_cases'])} records. {len(prefix_matches)} match the exact prefix before "
            f"the attachment suffix instead. Unexplained IDs: {unexplained}. The exact full /prompt field "
            "was replayed and independently hashed; the historical checksum was never substituted for it."
        )
    if unexplained:
        warnings.append("Some historical prompt digests have no verified mapping to full stored text or its exact pre-attachment prefix; no provenance equivalence is inferred.")
    for case_id in ("FW11", "FW12", "FW20", "FW18"):
        case = next((c for c in report["formal_cases"] if c["case_id"] == case_id), {})
        report["focus"][case_id] = {key: case.get(key) for key in ("baseline", "current")}
        current = case.get("current")
        if case_id != "FW18":
            observed = [r for r in current if "annotation_table" in r.get("kinds", [])] if isinstance(current, list) else []
            check(f"{case_id}: current annotation_table/xlsx classification", len(observed) == 1 and observed[0]["formats"] == ["xlsx"], observed)
        else:
            scopes = [r.get("body_scope") for r in current if set(r.get("kinds", [])) & {"report_html", "report_docx"}] if isinstance(current, list) else []
            check("FW18: current report bodies are shared then distinct", scopes == ["shared", "distinct"], scopes)
    for case in report["selected_local_prompts"]:
        current = case.get("current", {})
        constraints = current.get("constraints_auto", {})
        check(f"{case['case_id']}: current pure source predicates require local", current.get("binding_source_scope") == "local" and constraints.get("source_policy") == "required" and constraints.get("requested_mode") == "local" and constraints.get("required_tool_groups") == [["local_hybrid_search"]], {
            "binding": current.get("binding_source_scope"), "constraints_auto": constraints,
        })

    drift = []
    for path, before in input_snapshots.items():
        if sha256(path.read_bytes()) != before:
            drift.append(path.as_posix())
    check("External input hashes remain unchanged during replay", not drift, drift)
    source_drift = []
    for item in report["source_inputs"].get("current", {}).values():
        if sha256(Path(item["path"]).read_bytes()) != item["sha256"]:
            source_drift.append(item["path"])
    check("Current source hashes remain unchanged during replay", not source_drift, source_drift)
    index_after = index_evidence(repo)
    report["git_index_after"] = index_after
    check("Git index SHA256 remains unchanged during replay", index_before["sha256"] == index_after["sha256"], index_after["sha256"])
    report["summary"] = summary
    report["exit_code"] = 1 if failures else 0
    output.mkdir(parents=True, exist_ok=True)
    (output / "comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    (output / "comparison.md").write_text(render_markdown(report), encoding="utf-8", newline="\n")
    print(json.dumps({
        "exit_code": report["exit_code"], "failures": failures, "warnings": warnings,
        "summary": summary,
        "outputs": [(output / name).as_posix() for name in ("comparison.json", "comparison.md")],
    }, ensure_ascii=False, indent=2))
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
