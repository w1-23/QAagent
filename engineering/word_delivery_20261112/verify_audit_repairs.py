"""Record offline checks, rebuild the fixed prompt, and bundle a reviewable patch.

Run from the trusted checkout:
    python -I -X utf8 engineering/word_delivery_20261112/verify_audit_repairs.py

No server, network, benchmark, staging, commit or restart operations are performed.
The patch includes ignored test inputs needed by run_checks.py, without staging them.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import runpy
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "01_src"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args: str, allowed: tuple[int, ...] = (0,)) -> bytes:
    process = subprocess.run(["git", "-c", "core.safecrlf=false", *args], cwd=ROOT,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if process.returncode not in allowed:
        raise RuntimeError(process.stderr.decode("utf-8", errors="replace"))
    return process.stdout


def tracked(path: str) -> bool:
    return bool(git("ls-files", "--", path).strip())


def write_new_or_equal(path: Path, content: bytes) -> None:
    # Never silently replace an earlier, different evidence bundle.
    if path.exists() and path.read_bytes() != content:
        raise FileExistsError(f"Evidence already exists with different content: {path}")
    path.write_bytes(content)


def main() -> None:
    from src.prompts import loader

    prompts = loader.ensure_fixed_prompts(reload=True)
    expected = "\n\n".join((loader.PROMPT_DIR / "source" / name)
                           .read_text(encoding="utf-8").strip()
                           for name in loader.ROLE_SOURCE_FILES["supervisor"])
    built_path = loader.PROMPT_DIR / "builds" / loader.BUILD_FILE_NAMES["supervisor"]
    assert prompts["supervisor"] == expected == loader.get_system_prompt("supervisor")
    assert built_path.read_text(encoding="utf-8").strip() == expected
    prompt_proof = {
        "source_files": [{"path": str((loader.PROMPT_DIR / "source" / name).relative_to(ROOT)),
                          "sha256": sha256(loader.PROMPT_DIR / "source" / name)}
                         for name in loader.ROLE_SOURCE_FILES["supervisor"]],
        "source_count": len(loader.ROLE_SOURCE_FILES["supervisor"]),
        "characters": len(expected),
        "runtime_prefix_sha256": hashlib.sha256(expected.encode("utf-8")).hexdigest(),
        "built_file": str(built_path.relative_to(ROOT)),
        "built_file_sha256": sha256(built_path),
        "equality": True,
        "running_service_reloaded": False,
    }
    groups = {}
    for group in ("main", "word_download", "attachment_boundaries"):
        xml_path, log_path = OUT / f"{group}.xml", OUT / f"{group}.log"
        root = ET.parse(xml_path).getroot()
        suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
        totals = {key: sum(int(suite.attrib.get(key, "0")) for suite in suites)
                  for key in ("tests", "failures", "errors", "skipped")}
        assert totals["tests"] > 0
        assert not any(totals[key] for key in ("failures", "errors", "skipped")), totals
        totals["log_sha256"] = sha256(log_path)
        totals["junit_sha256"] = sha256(xml_path)
        groups[group] = totals

    runner = runpy.run_path(str(OUT / "run_checks.py"))
    test_paths = sorted({"01_src/src/" + name
                         for tests in runner["GROUPS"].values() for name in tests})
    new_paths = ["01_src/src/core/ooxml_determinism.py",
                 "01_src/src/tools/argument_check.py",
                 str(Path(__file__).resolve().relative_to(ROOT)).replace("\\", "/")]
    new_paths.extend(path for path in test_paths if not tracked(path))
    new_paths = sorted(set(new_paths))
    patch_parts = [git("diff", "--binary", "HEAD", "--", "01_src/src",
                       "engineering/word_delivery_20261112/run_checks.py")]
    for path in new_paths:
        assert (ROOT / path).is_file(), path
        if not tracked(path):
            patch_parts.append(git("diff", "--no-index", "--binary", "--", "/dev/null", path,
                                   allowed=(1,)))
    patch = b"".join(patch_parts)
    patch_path = OUT / "audit_repair_source_changes.patch"
    write_new_or_equal(patch_path, patch)
    git("diff", "--check")
    verification = {
        "baseline_commit": git("rev-parse", "HEAD").decode().strip(),
        "scope": "Offline source regression and prompt build only; no live service or benchmark",
        "groups": groups,
        "total_passed": sum(group["tests"] for group in groups.values()),
        "prompt": prompt_proof,
        "source_patch": {"path": str(patch_path.relative_to(ROOT)),
                         "sha256": hashlib.sha256(patch).hexdigest(), "bytes": len(patch),
                         "new_file_paths": new_paths},
        "test_inputs": [{"path": path, "tracked": tracked(path),
                         "sha256": sha256(ROOT / path)} for path in test_paths],
        "notes": ["Third-party Starlette TestClient deprecation warning in word_download.log",
                  "Patch includes ignored regression inputs without modifying the git index",
                  "Compiled prompt was checked in this isolated verifier process only"],
    }
    target = OUT / "audit_verification.json"
    write_new_or_equal(target, (json.dumps(verification, ensure_ascii=False, indent=2)
                               + "\n").encode("utf-8"))
    print(json.dumps({"total_passed": verification["total_passed"], "groups": groups,
                      "prompt": prompt_proof, "patch_files_added": len(new_paths),
                      "verification": str(target.relative_to(ROOT))},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
