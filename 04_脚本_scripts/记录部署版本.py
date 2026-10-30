#!/usr/bin/env python3
"""把「真实部署的版本」写进站点根目录，别再靠手写标签。

背景：staging 的 DEPLOYED_COMMIT 曾长期写着 e3fc7c7，而磁盘上的源码早已是
f83c369 之后的内容——标签和实际不一致，排查时只能靠逐个文件 sha 反推。
本脚本把可核对的事实一次写全：提交号、部署时间、src/web 清单哈希、关键文件哈希。

用法（在站点根目录执行，或 --root 指定）：
    python3 04_脚本_scripts/记录部署版本.py --commit 14f00c7
    python3 04_脚本_scripts/记录部署版本.py --verify-only     # 只打印，不写

产物：
    DEPLOYED_COMMIT          纯提交号（兼容旧脚本）
    DEPLOYED_RECORD.json     提交号 / 时间 / 清单哈希 / 关键文件哈希 / 主机
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import pathlib
import platform
import socket
import sys

# 这些文件一旦与候选版本不一致，说明部署包不是同一份东西
KEY_FILES = [
    "src/api/main.py",
    "src/tools/deepseek_search_fetch.py",
    "src/core/tool_results.py",
    "web/assets/app.js",
    "web/assets/turn_rail.js",
    "web/assets/mascot.js",
    "web/index.html",
]


def _sha(path: pathlib.Path) -> str:
    data = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def _manifest(root: pathlib.Path) -> tuple[str, int]:
    rows: list[str] = []
    for top in ("src", "web"):
        base = root / top
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file() or "__pycache__" in p.parts:
                continue
            rows.append(f"{p.relative_to(root).as_posix()}:{_sha(p)}")
    return hashlib.sha256("\n".join(rows).encode()).hexdigest(), len(rows)


def _key_hashes(root: pathlib.Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for rel in KEY_FILES:
        p = root / rel
        out[rel] = _sha(p)[:16] if p.is_file() else "MISSING"
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="记录真实部署版本")
    ap.add_argument("--root", default=".", help="站点根目录（含 src/ 与 web/）")
    ap.add_argument("--commit", default="", help="本次部署的提交号")
    ap.add_argument("--note", default="", help="备注（例如 分批部署第一批）")
    ap.add_argument("--verify-only", action="store_true", help="只打印，不写文件")
    args = ap.parse_args(argv)

    root = pathlib.Path(args.root).resolve()
    if not (root / "src").is_dir():
        print(f"[FAIL] {root} 下没有 src/，请用 --root 指向站点根目录", file=sys.stderr)
        return 2

    manifest_sha, count = _manifest(root)
    keys = _key_hashes(root)
    record = {
        "commit": args.commit or _read_old_commit(root),
        "deployed_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "manifest_sha256": manifest_sha,
        "file_count": count,
        "key_files": keys,
        "host": socket.gethostname(),
        "platform": platform.platform(),
        "pid_owner": os.getpid(),
        "note": args.note,
    }

    print(f"root        : {root}")
    print(f"commit      : {record['commit'] or '(未提供)'}")
    print(f"manifest    : {manifest_sha}  ({count} files)")
    for k, v in keys.items():
        print(f"  {k:<40} {v}")

    if args.verify_only:
        old = root / "DEPLOYED_COMMIT"
        if old.is_file():
            print(f"DEPLOYED_COMMIT 现状: {old.read_text(encoding='utf-8').strip()}")
        return 0

    if not record["commit"]:
        print("[FAIL] 没有 --commit，也没有历史标记可继承", file=sys.stderr)
        return 2

    (root / "DEPLOYED_COMMIT").write_text(record["commit"] + "\n", encoding="utf-8")
    (root / "DEPLOYED_RECORD.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("\n[OK] 已写入 DEPLOYED_COMMIT 与 DEPLOYED_RECORD.json")
    return 0


def _read_old_commit(root: pathlib.Path) -> str:
    p = root / "DEPLOYED_COMMIT"
    if p.is_file():
        return p.read_text(encoding="utf-8").strip()
    return ""


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
