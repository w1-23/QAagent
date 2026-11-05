#!/usr/bin/env python3
"""Build the stage-0 repository, asset, and data-lineage audit artifacts."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


EXPERIMENT_RELATIVE = Path("experiments/citrus_scholar_citation60")
PROTECTED_CITATION_FILES = {
    "03_benchmarks/citation60/CORPUS_SCOPE_METADATA.json",
    "03_benchmarks/citation60/README_FROZEN.md",
    "03_benchmarks/citation60/chunk_manifest.csv",
    "03_benchmarks/citation60/chunks_FROZEN.jsonl",
    "03_benchmarks/citation60/question_authoring_packet.jsonl",
    "03_benchmarks/citation60/question_manifest.csv",
    "03_benchmarks/citation60/questions_DRAFT.csv",
    "03_benchmarks/citation60/questions_FROZEN.jsonl",
    "03_benchmarks/citation60/questions_FROZEN.manifest.json",
    "03_benchmarks/citation60/sampling_config.json",
    "06_runs/manifests/citation_run_plan.csv",
    "06_runs/manifests/citation_run_plan_meta.json",
    "06_runs/manifests/citation_semantic_audit_plan.csv",
    "06_runs/manifests/citation_semantic_audit_plan_meta.json",
}
EXPECTED_STRATA = {"L": 12, "V": 10, "W": 10, "X": 12, "P": 8, "N": 8}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def jsonl_rows(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]


def run(
    args: list[str],
    *,
    cwd: Path,
    check: bool = False,
    timeout: int = 60,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update({"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"})
    return subprocess.run(
        args,
        cwd=cwd,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=check,
        timeout=timeout,
    )


def git(repo: Path, *args: str) -> str:
    result = run(["git", "-c", "core.quotepath=false", *args], cwd=repo)
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed: {result.stderr.strip()}"
        )
    return result.stdout.strip()


def gh_json(repo_slug: str, endpoint: str) -> tuple[Any | None, str | None]:
    result = run(
        ["gh", "api", endpoint],
        cwd=Path.cwd(),
        timeout=60,
    )
    if result.returncode != 0:
        message = result.stderr.strip().splitlines()
        return None, message[-1][:300] if message else "gh_api_failed"
    try:
        return json.loads(result.stdout), None
    except json.JSONDecodeError:
        return None, "gh_api_returned_invalid_json"


def build_repository_audit(
    repo: Path,
    *,
    expected_head: str | None,
    github_repo: str | None,
    require_private: bool,
) -> tuple[dict[str, Any], list[str]]:
    blockers: list[str] = []
    head = git(repo, "rev-parse", "HEAD")
    tree = git(repo, "rev-parse", "HEAD^{tree}")
    branch = git(repo, "branch", "--show-current")
    status = git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    remotes = git(repo, "remote", "-v").splitlines()
    refs = git(
        repo, "for-each-ref", "--format=%(refname)|%(objectname)|%(symref)"
    ).splitlines()
    fsck = run(["git", "fsck", "--full", "--no-reflogs"], cwd=repo, timeout=120)
    changed = [line for line in status.splitlines() if line.strip()]
    if changed:
        blockers.append("git_worktree_not_clean")
    if expected_head and head != expected_head:
        blockers.append("git_head_differs_from_expected")
    if branch != "main":
        blockers.append("git_branch_not_main")

    github: dict[str, Any] = {"checked": False}
    if github_repo:
        metadata, metadata_error = gh_json(github_repo, f"repos/{github_repo}")
        main_content, content_error = gh_json(
            github_repo,
            f"repos/{github_repo}/contents/{EXPERIMENT_RELATIVE.as_posix()}/AUDIT_INDEX.md?ref=main",
        )
        main_tree, tree_error = gh_json(
            github_repo,
            f"repos/{github_repo}/git/trees/main?recursive=1",
        )
        commits, commits_error = gh_json(
            github_repo,
            f"repos/{github_repo}/commits?sha=main&per_page=1",
        )
        gh_errors = [
            error
            for error in (
                metadata_error,
                content_error,
                tree_error,
                commits_error,
            )
            if error
        ]
        if gh_errors:
            blockers.extend(f"github_access:{error}" for error in gh_errors)
        tree_entries = main_tree.get("tree", []) if isinstance(main_tree, dict) else []
        tree_truncated = (
            main_tree.get("truncated")
            if isinstance(main_tree, dict)
            else None
        )
        experiment_tree_entries = [
            row
            for row in tree_entries
            if str(row.get("path", "")).startswith(EXPERIMENT_RELATIVE.as_posix() + "/")
        ]
        commit_sha = None
        if isinstance(commits, list) and commits:
            commit_sha = commits[0].get("sha")
        content_sha = (
            main_content.get("sha")
            if isinstance(main_content, dict)
            else None
        )
        is_private = metadata.get("private") if isinstance(metadata, dict) else None
        admin = (
            metadata.get("permissions", {}).get("admin")
            if isinstance(metadata, dict)
            else None
        )
        default_branch = (
            metadata.get("default_branch")
            if isinstance(metadata, dict)
            else None
        )
        github = {
            "checked": not gh_errors,
            "repository": github_repo,
            "private": is_private,
            "admin": admin,
            "default_branch": default_branch,
            "main_commit_read": commit_sha,
            "audit_index_read": content_sha is not None,
            "audit_index_blob_sha": content_sha,
            "recursive_tree_read": isinstance(main_tree, dict),
            "recursive_tree_truncated": tree_truncated,
            "experiment_tree_entry_count": len(experiment_tree_entries),
            "history_read": commit_sha is not None,
            "errors": gh_errors,
        }
        if require_private and is_private is not True:
            blockers.append("github_repository_not_private")
        if tree_truncated is not False:
            blockers.append("github_tree_not_complete")
        if commit_sha and commit_sha != head:
            blockers.append("github_main_differs_from_local_head")
    elif require_private:
        blockers.append("github_repository_not_provided_for_private_check")

    report = {
        "schema_version": "1.0",
        "protocol_version": "4.3.3",
        "artifact": "REPOSITORY_AUDIT",
        "generated_utc": utc_now(),
        "repo_root": str(repo),
        "head": head,
        "tree": tree,
        "branch": branch,
        "working_tree_clean": not changed,
        "changed_path_count": len(changed),
        "changed_paths": changed,
        "remotes": remotes,
        "refs": refs,
        "fsck": {
            "exit_code": fsck.returncode,
            "status": "PASS" if fsck.returncode == 0 else "FAIL",
            "stderr_tail": fsck.stderr.strip().splitlines()[-5:],
        },
        "github": github,
        "expected_head": expected_head,
        "status": "PASS" if not blockers else "BLOCKED",
        "blockers": sorted(set(blockers)),
    }
    return report, blockers


def verify_l1_assets(
    l1_root: Path | None,
    manifest: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    blockers: list[str] = []
    for asset in manifest.get("assets", []):
        declared = {
            "asset_id": asset["asset_id"],
            "protected_relative_path": asset["protected_relative_path"],
            "bytes": int(asset["bytes"]),
            "sha256": asset["sha256"],
            "release_status": asset["release_status"],
        }
        if l1_root is None:
            rows.append(
                {
                    **declared,
                    "verification": "NOT_RUN_L1_ROOT_NOT_PROVIDED",
                }
            )
            continue
        path = l1_root / asset["protected_relative_path"]
        if not path.is_file():
            blockers.append(f"l1_asset_missing:{asset['asset_id']}")
            rows.append({**declared, "verification": "MISSING"})
            continue
        actual_bytes = path.stat().st_size
        actual_sha = sha256_file(path)
        matches = (
            actual_bytes == int(asset["bytes"])
            and actual_sha == asset["sha256"]
        )
        if not matches:
            blockers.append(f"l1_asset_mismatch:{asset['asset_id']}")
        rows.append(
            {
                **declared,
                "actual_bytes": actual_bytes,
                "actual_sha256": actual_sha,
                "verification": "PASS" if matches else "MISMATCH",
            }
        )
    return rows, blockers


def build_asset_report(
    experiment_root: Path,
    git_repo: Path,
    l1_root: Path | None,
) -> tuple[dict[str, Any], list[str]]:
    blockers: list[str] = []
    manifest_path = (
        experiment_root / "12_reproducibility/L1_CONTROLLED_ASSET_MANIFEST.json"
    )
    manifest = read_json(manifest_path)
    l1_rows, l1_blockers = verify_l1_assets(l1_root, manifest)
    blockers.extend(l1_blockers)

    protected_present = [
        relative
        for relative in PROTECTED_CITATION_FILES
        if (experiment_root / relative).exists()
    ]
    blockers.extend(
        f"protected_citation_file_in_repository_tree:{relative}"
        for relative in protected_present
    )

    qa_path = experiment_root / "03_benchmarks/qa60/qa60_CANDIDATE.csv"
    qa = csv_rows(qa_path)
    qa_ids = {row["题号"] for row in qa}
    strata = Counter(row["组别"] for row in qa)
    if len(qa) != 60 or len(qa_ids) != 60:
        blockers.append("core_qa_not_60_unique_questions")
    if dict(strata) != EXPECTED_STRATA:
        blockers.append("core_qa_strata_mismatch")

    validity_path = (
        experiment_root
        / "07_scoring/validity/question_validity_review_packet.csv"
    )
    validity = csv_rows(validity_path)
    review_slots = {
        (row["experiment_id"], row["question_id"], row["reviewer_slot"])
        for row in validity
    }
    if len(validity) != 120 or len(review_slots) != 120:
        blockers.append("qa_validity_slots_not_120_unique")

    plan_path = experiment_root / "06_runs/manifests/run_plan.csv"
    run_plan = csv_rows(plan_path)
    plan_counts = Counter(row["kind"] for row in run_plan)
    qa_runs = [row for row in run_plan if row["kind"] == "qa"]
    if len(qa_runs) != 180:
        blockers.append("core_qa_run_plan_not_180")
    condition_counts = Counter(row["condition"] for row in qa_runs)
    if condition_counts != {"B0": 60, "B1": 60, "B2": 60}:
        blockers.append("core_qa_condition_counts_invalid")
    per_question: dict[str, set[str]] = {}
    for row in qa_runs:
        per_question.setdefault(row["case_id"], set()).add(row["condition"])
    if len(per_question) != 60 or any(
        conditions != {"B0", "B1", "B2"}
        for conditions in per_question.values()
    ):
        blockers.append("core_qa_not_three_independent_conditions_per_question")
    if any(row["case_id"] not in qa_ids for row in qa_runs):
        blockers.append("core_qa_run_plan_has_unknown_question")

    tracking_path = (
        experiment_root / "00_protocol/03_实验资产与数据留存台账.csv"
    )
    tracking = csv_rows(tracking_path)
    data_classes = Counter(row.get("data_class", "") for row in tracking)
    if not {"L1", "L2"}.issubset(data_classes):
        blockers.append("retention_ledger_missing_data_classes")

    citation_state: dict[str, Any] = {
        "verification": "NOT_RUN_L1_ROOT_NOT_PROVIDED",
    }
    if l1_root is not None:
        qpath = l1_root / "03_benchmarks/citation60/questions_FROZEN.jsonl"
        ppath = l1_root / "06_runs/manifests/citation_run_plan.csv"
        if not qpath.is_file() or not ppath.is_file():
            blockers.append("citation_controlled_inputs_missing")
        else:
            questions = jsonl_rows(qpath)
            citation_plan = csv_rows(ppath)
            citation_counts = Counter(row["condition"] for row in citation_plan)
            citation_state = {
                "verification": "PASS",
                "questions": len(questions),
                "run_rows": len(citation_plan),
                "condition_counts": dict(citation_counts),
                "questions_sha256": sha256_file(qpath),
                "run_plan_sha256": sha256_file(ppath),
                "semantic_support_reported": False,
            }
            if len(questions) != 60 or len(citation_plan) != 180:
                blockers.append("citation60_frozen_counts_invalid")
            if citation_counts != {"B0": 60, "B1": 60, "B2": 60}:
                blockers.append("citation60_condition_counts_invalid")

    forbidden_outputs = sorted(
        path.relative_to(experiment_root).as_posix()
        for path in experiment_root.rglob("*citation_semantic_support*")
        if path.is_file()
    )
    blockers.extend(
        f"semantic_support_output_present:{path}" for path in forbidden_outputs
    )

    tracked = git(
        git_repo,
        "ls-files",
        "--",
        EXPERIMENT_RELATIVE.as_posix(),
    ).splitlines()
    report = {
        "schema_version": "1.0",
        "protocol_version": "4.3.3",
        "artifact": "ASSET_INTEGRITY_REPORT",
        "generated_utc": utc_now(),
        "source_commit": git(git_repo, "rev-parse", "HEAD"),
        "experiment_tracked_file_count": len(tracked),
        "qa": {
            "questions": len(qa),
            "unique_ids": len(qa_ids),
            "strata": dict(sorted(strata.items())),
            "sha256": sha256_file(qa_path),
        },
        "validity": {
            "rows": len(validity),
            "unique_review_slots": len(review_slots),
            "sha256": sha256_file(validity_path),
            "decisions_present": False,
        },
        "core_run_plan": {
            "all_kinds": dict(sorted(plan_counts.items())),
            "total_rows": len(run_plan),
            "qa_rows": len(qa_runs),
            "qa_condition_counts": dict(sorted(condition_counts.items())),
            "sha256": sha256_file(plan_path),
        },
        "citation60": citation_state,
        "protected_citation_files_in_repository_tree": protected_present,
        "l1_manifest": {
            "path": manifest_path.relative_to(experiment_root).as_posix(),
            "sha256": sha256_file(manifest_path),
            "declared_assets": len(manifest.get("assets", [])),
            "verified_assets": sum(
                row["verification"] == "PASS" for row in l1_rows
            ),
            "verification_state": (
                "PASS"
                if l1_root is not None and not l1_blockers
                else "BLOCKED" if l1_root is not None
                else "NOT_RUN_L1_ROOT_NOT_PROVIDED"
            ),
            "assets": l1_rows,
        },
        "retention_ledger": {
            "rows": len(tracking),
            "data_class_counts": dict(sorted(data_classes.items())),
            "sha256": sha256_file(tracking_path),
        },
        "semantic_support": {
            "reported": False,
            "forbidden_outputs_present": forbidden_outputs,
        },
        "status": "PASS" if not blockers else "BLOCKED",
        "blockers": sorted(set(blockers)),
    }
    return report, blockers


def role_for(relative: str) -> tuple[str, str, bool]:
    if relative.startswith("03_benchmarks/") or relative.startswith("04_fixtures/"):
        return "EXPERIMENT_INPUT", "FROZEN_OR_SYNTHETIC_INPUT", False
    if relative.startswith("06_runs/simulated_dry_run/"):
        return "SYNTHETIC_FIXTURE", "INTERFACE_TEST_ONLY", False
    if relative.startswith("06_runs/manifests/"):
        return "EXPERIMENT_INPUT", "RUN_PLAN", False
    if relative.startswith("06_runs/"):
        return "RAW_OR_DERIVED_RUN", "CONTROLLED_OUTPUT", False
    if relative.startswith("07_scoring/"):
        return "HIDDEN_SCORING_REFERENCE_OR_TEMPLATE", "SCORING_ONLY", False
    if relative.startswith("08_workflows/"):
        return "WORKFLOW_SPECIFICATION", "EXPERIMENT_CONTROL", False
    if relative.startswith("09_analysis/") or relative.startswith("10_figures/"):
        return "ANALYSIS_OR_FIGURE", "DERIVED_REVIEW_ARTIFACT", False
    if relative.startswith("11_manuscript/"):
        return "MANUSCRIPT_MATERIAL", "WRITING_AND_AUDIT", False
    if relative.startswith("scripts/"):
        return "EXECUTABLE_CODE", "CONTROL_OR_ANALYSIS", False
    if relative.startswith(("00_protocol/", "01_preflight/", "12_reproducibility/")):
        return "PROTOCOL_OR_EVIDENCE", "AUDIT_CONTROL", False
    return "PACKAGE_METADATA", "REVIEW_NAVIGATION", False


def build_lineage(
    repo: Path,
    experiment_root: Path,
) -> dict[str, Any]:
    manifest = read_json(
        experiment_root / "12_reproducibility/L1_CONTROLLED_ASSET_MANIFEST.json"
    )
    protected = {
        row["former_public_path"]: row for row in manifest.get("assets", [])
    }
    tracked = git(
        repo,
        "ls-files",
        "--",
        EXPERIMENT_RELATIVE.as_posix(),
    ).splitlines()
    entries: list[dict[str, Any]] = []
    for repo_relative in tracked:
        path = repo / repo_relative
        experiment_relative = path.relative_to(experiment_root).as_posix()
        role, stage, agent_visible = role_for(experiment_relative)
        entry = {
            "path": experiment_relative,
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
            "role": role,
            "stage": stage,
            "git_visibility": "PRIVATE_REPOSITORY",
            "agent_visible": agent_visible,
        }
        if experiment_relative in protected:
            declared = protected[experiment_relative]
            entry.update(
                {
                    "role": "PROTECTED_REFERENCE_OR_LOCATOR",
                    "stage": "HIDDEN_SCORING_REFERENCE",
                    "git_visibility": "DECLARED_L1_NOT_PRESENT",
                    "agent_visible": False,
                    "declared_sha256": declared["sha256"],
                    "declared_bytes": declared["bytes"],
                }
            )
        entries.append(entry)

    entries.extend(
        {
            "path": row["protected_relative_path"],
            "bytes": row["bytes"],
            "sha256": row["sha256"],
            "role": "PROTECTED_REFERENCE_OR_LOCATOR",
            "stage": "HIDDEN_SCORING_REFERENCE",
            "git_visibility": "PROTECTED_L1_SERVER_ONLY",
            "agent_visible": False,
        }
        for row in manifest.get("assets", [])
        if row["former_public_path"] not in tracked
    )

    return {
        "schema_version": "1.0",
        "protocol_version": "4.3.3",
        "artifact": "DATA_LINEAGE_MANIFEST",
        "generated_utc": utc_now(),
        "source_commit": git(repo, "rev-parse", "HEAD"),
        "classification_rule": {
            "experiment_input": "one frozen question may be copied into exactly one Agent request",
            "hidden_reference": "never enters Agent input; scoring and citation verification only",
            "raw_run": "written once per independent request; feeds scoring, never overwrites",
            "repository_evidence": "metadata and reviewed derivatives only; private visibility is not redistribution permission",
        },
        "entry_count": len(entries),
        "entries": entries,
        "forbidden_flows": [
            "hidden_reference -> Agent request/context/tool argument",
            "other_condition_answer -> current Agent request/session",
            "expert_or_judge_score -> Agent request/context",
            "target_chunk_id_or_text -> direct Agent injection",
        ],
        "required_flows": [
            {
                "from": "frozen_question",
                "to": "one_new_agent_session",
                "rule": "copy exactly one current question and condition policy",
            },
            {
                "from": "platform_retrieval",
                "to": "agent_evidence",
                "rule": "normal platform search is allowed and logged",
            },
            {
                "from": "raw_run",
                "to": "blinded_expert_and_judge_scoring",
                "rule": "immutable raw SHA; no rewrite or cross-condition context",
            },
            {
                "from": "hidden_target",
                "to": "deterministic_citation_hit_verification",
                "rule": "used only after the answer is frozen",
            },
        ],
        "status": "PASS",
        "blockers": [],
    }


def write_new_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def default_repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=str(default_repo_root()))
    parser.add_argument("--experiment-root")
    parser.add_argument("--l1-root")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--github-repo")
    parser.add_argument("--expected-head")
    parser.add_argument("--require-private", action="store_true")
    parser.add_argument("--require-l1", action="store_true")
    args = parser.parse_args()

    repo = Path(args.repo_root).expanduser().resolve()
    experiment_root = (
        Path(args.experiment_root).expanduser().resolve()
        if args.experiment_root
        else repo / EXPERIMENT_RELATIVE
    )
    l1_root = Path(args.l1_root).expanduser().resolve() if args.l1_root else None
    output = Path(args.output_dir).expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit(f"output directory is not empty: {output}")

    repository, repository_blockers = build_repository_audit(
        repo,
        expected_head=args.expected_head,
        github_repo=args.github_repo,
        require_private=args.require_private,
    )
    assets, asset_blockers = build_asset_report(experiment_root, repo, l1_root)
    if args.require_l1 and l1_root is None:
        asset_blockers.append("l1_root_required_for_stage0_acceptance")
        assets["blockers"] = sorted(set(asset_blockers))
        assets["status"] = "BLOCKED"
    lineage = build_lineage(repo, experiment_root)
    outputs = {
        "REPOSITORY_AUDIT.json": repository,
        "ASSET_INTEGRITY_REPORT.json": assets,
        "DATA_LINEAGE_MANIFEST.json": lineage,
    }
    output.mkdir(parents=True, exist_ok=True)
    for name, payload in outputs.items():
        write_new_json(output / name, payload)
    blockers = sorted(set(repository_blockers + asset_blockers))
    manifest = {
        "schema_version": "1.0",
        "protocol_version": "4.3.3",
        "artifact": "STAGE0_AUDIT_MANIFEST",
        "generated_utc": utc_now(),
        "status": "PASS" if not blockers else "BLOCKED",
        "files": {
            name: {
                "bytes": (output / name).stat().st_size,
                "sha256": sha256_file(output / name),
            }
            for name in outputs
        },
        "blockers": blockers,
        "note": "This manifest is self-identifying; commit it with the three files.",
    }
    write_new_json(output / "STAGE0_AUDIT_MANIFEST.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0 if manifest["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
