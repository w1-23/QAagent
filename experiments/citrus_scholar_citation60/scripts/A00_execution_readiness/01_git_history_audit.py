#!/usr/bin/env python3
"""Audit reachable Git history for credentials and protected L1 path exposure."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SECRET_PATTERNS = {
    "aliyun_key": re.compile(rb"sk-ws-[A-Za-z0-9._-]{10,}"),
    "opencode_key": re.compile(rb"oc_sk_[A-Za-z0-9_-]{10,}"),
    "bearer_header": re.compile(
        rb"(?i)authorization\s*:\s*bearer\s+[A-Za-z0-9._~+/-]{16,}"
    ),
    "assigned_api_key": re.compile(
        rb"(?i)(?:CITRUS_(?:AGENT|JUDGE)_API_KEY|OPENAI_API_KEY|"
        rb"DASHSCOPE_API_KEY|ANTHROPIC_API_KEY)\s*=\s*[^\s#\r\n]{8,}"
    ),
    "pem_private_key": re.compile(
        rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
    ),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed: "
            f"{result.stderr.decode('utf-8', errors='replace').strip()}"
        )
    return result.stdout


def object_inventory(repo: Path) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for raw in git(repo, "rev-list", "--objects", "--all").decode(
        "utf-8", errors="replace"
    ).splitlines():
        oid, _, path = raw.partition(" ")
        rows.append((oid, path))
    return rows


def protected_history_rows(
    objects: list[tuple[str, str]],
    protected_paths: set[str],
) -> list[dict[str, Any]]:
    grouped: dict[str, set[str]] = defaultdict(set)
    for oid, path in objects:
        normalized = path.strip('"')
        if normalized in protected_paths:
            grouped[normalized].add(oid)
    return [
        {
            "path": path,
            "reachable_object_count": len(objects_for_path),
        }
        for path, objects_for_path in sorted(grouped.items())
    ]


def repo_protected_paths(
    manifest: dict[str, Any],
    experiment_relative: str,
) -> set[str]:
    prefix = experiment_relative.strip("/")
    return {
        f"{prefix}/{str(row['former_public_path']).replace(chr(92), '/').lstrip('/')}"
        for row in manifest.get("assets", [])
        if row.get("remove_from_public_tree") is True
    }


def scan_blobs(
    repo: Path,
    objects: list[tuple[str, str]],
) -> tuple[int, list[dict[str, Any]]]:
    findings: list[dict[str, Any]] = []
    scanned = 0
    seen: set[str] = set()
    for oid, path in objects:
        if oid in seen:
            continue
        seen.add(oid)
        object_type = git(repo, "cat-file", "-t", oid).strip()
        if object_type != b"blob":
            continue
        data = git(repo, "cat-file", "-p", oid)
        if len(data) > 20_000_000 or b"\x00" in data[:4096]:
            continue
        scanned += 1
        for name, pattern in SECRET_PATTERNS.items():
            if pattern.search(data):
                findings.append(
                    {
                        "pattern": name,
                        "object": oid,
                        "path": path or "<no-path>",
                        "bytes": len(data),
                    }
                )
    return scanned, findings


def build_report(
    repo: Path,
    protected_manifest: Path,
) -> dict[str, Any]:
    manifest = json.loads(protected_manifest.read_text(encoding="utf-8-sig"))
    manifest_relative = protected_manifest.resolve().relative_to(repo.resolve())
    experiment_relative = manifest_relative.parent.parent.as_posix()
    protected_paths = repo_protected_paths(manifest, experiment_relative)
    objects = object_inventory(repo)
    scanned, findings = scan_blobs(repo, objects)
    history_rows = protected_history_rows(objects, protected_paths)
    current_rows = sorted(
        path for path in protected_paths if (repo / path).exists()
    )
    blockers: list[str] = []
    if findings:
        blockers.append("credential_pattern_in_reachable_history")
    if current_rows:
        blockers.append("protected_l1_path_in_current_tree")
    status = (
        "FAIL"
        if blockers
        else "PASS_NO_CREDENTIALS_HISTORY_L1_PRESENT"
        if history_rows
        else "PASS"
    )
    return {
        "schema_version": "1.0",
        "protocol_version": "4.4.0",
        "artifact": "GIT_HISTORY_SECRET_AND_PROTECTED_PATH_AUDIT",
        "generated_utc": utc_now(),
        "head": git(repo, "rev-parse", "HEAD").decode().strip(),
        "tree": git(repo, "rev-parse", "HEAD^{tree}").decode().strip(),
        "scanned_text_blobs": scanned,
        "reachable_objects": len(objects),
        "credential_findings": findings,
        "current_protected_paths_present": current_rows,
        "history_protected_paths": history_rows,
        "history_exposure_disposition": (
            "PI_AUTHORIZED_PUBLIC_VISIBILITY_WITH_LEGACY_OBJECTS_NOT_REWRITTEN"
            if history_rows
            else "NO_PROTECTED_HISTORY_PATHS_FOUND"
        ),
        "history_rewrite_performed": False,
        "force_push_performed": False,
        "status": status,
        "blockers": blockers,
        "meaning": (
            "Credential scanning is separate from source-redistribution review. "
            "A protected path in history is disclosed and is not evidence that "
            "the current tree contains that path."
        ),
    }


def write_new_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--protected-manifest", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    repo = Path(args.repo_root).expanduser().resolve()
    manifest = Path(args.protected_manifest).expanduser().resolve()
    output = Path(args.out).expanduser().resolve()
    if output.exists():
        raise SystemExit(f"output path already exists: {output}")
    report = build_report(repo, manifest)
    write_new_json(output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] != "FAIL" else 2


if __name__ == "__main__":
    raise SystemExit(main())
