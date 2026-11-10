#!/usr/bin/env python3
"""离线管理账号：重置密码、创建管理员、启用/停用账号。

用途：忘记管理员密码时的本地恢复手段。直接写 agent/state/accounts.sqlite3，
因此必须在服务所在机器执行（不需要服务在线，改动在下一次登录时生效）。

示例：
  python scripts/manage_account.py list
  python scripts/manage_account.py reset wangziqiang123            # 交互输入新密码
  python scripts/manage_account.py reset wangziqiang123 --password 'Xxxx...'
  python scripts/manage_account.py create citrus_admin --admin     # 交互输入新密码
  python scripts/manage_account.py create citrus_admin --admin --password 'Xxxx...'
  python scripts/manage_account.py enable  wangziqiang123
  python scripts/manage_account.py disable exp_runner
"""
from __future__ import annotations

import argparse
import getpass
import os
import sqlite3
import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE_ROOT))

from src.auth import (DB_PATH, _now, _password_hash, _stamp, init_store,  # noqa: E402
                      list_users, validate_credentials)

MIN_LEN, MAX_LEN = 10, 256


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ask_password(hint: str = "新密码") -> str:
    while True:
        first = getpass.getpass(f"{hint}（{MIN_LEN}–{MAX_LEN} 位，不会回显）: ")
        if not (MIN_LEN <= len(first) <= MAX_LEN):
            print(f"  长度必须在 {MIN_LEN}–{MAX_LEN} 之间，请重试。")
            continue
        second = getpass.getpass("再次输入: ")
        if first != second:
            print("  两次输入不一致，请重试。")
            continue
        return first


def cmd_list(_args: argparse.Namespace) -> int:
    rows = list_users()
    if not rows:
        print("（账号库为空）")
        return 0
    print(f"{'用户名':<22}{'角色':<8}{'状态':<6}{'日配额':<8}创建时间")
    for u in rows:
        print(f"{u['username']:<22}{u['role']:<8}"
              f"{'启用' if u.get('active', 1) else '停用':<6}"
              f"{u.get('daily_limit', ''):<8}{u.get('created_at', '')}")
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (args.username,)).fetchone()
        if row is None:
            print(f"账号不存在：{args.username}（可用 list 查看，或用 create 新建）")
            return 2
        password = args.password or _ask_password("新密码")
        if not (MIN_LEN <= len(password) <= MAX_LEN):
            print(f"密码长度必须在 {MIN_LEN}–{MAX_LEN} 之间。")
            return 2
        salt = os.urandom(16)
        conn.execute(
            "UPDATE users SET salt = ?, password_hash = ?, active = 1 WHERE username = ?",
            (salt, _password_hash(password, salt), args.username),
        )
    print(f"已重置 {args.username} 的密码，并确保账号为启用状态。")
    return 0


def cmd_create(args: argparse.Namespace) -> int:
    error = validate_credentials(args.username, args.password or "x" * MIN_LEN)
    if error:
        print(error)
        return 2
    with _connect() as conn:
        if conn.execute("SELECT 1 FROM users WHERE username = ?", (args.username,)).fetchone():
            print(f"账号已存在：{args.username}（请改用 reset）")
            return 2
        password = args.password or _ask_password("管理员密码" if args.admin else "新账号密码")
        if not (MIN_LEN <= len(password) <= MAX_LEN):
            print(f"密码长度必须在 {MIN_LEN}–{MAX_LEN} 之间。")
            return 2
        salt = os.urandom(16)
        conn.execute(
            "INSERT INTO users(username, salt, password_hash, role, active, daily_limit, "
            "created_at, avatar_preset) VALUES (?, ?, ?, ?, 1, ?, ?, '01')",
            (args.username, salt, _password_hash(password, salt),
             "admin" if args.admin else "user", args.daily_limit, _stamp(_now())),
        )
    print(f"已创建{'管理员' if args.admin else ''}账号 {args.username}（日配额 {args.daily_limit}）。")
    return 0


def cmd_set_active(args: argparse.Namespace, active: bool) -> int:
    with _connect() as conn:
        cur = conn.execute("UPDATE users SET active = ? WHERE username = ?",
                           (1 if active else 0, args.username))
        if cur.rowcount == 0:
            print(f"账号不存在：{args.username}")
            return 2
    print(f"{args.username} 已{'启用' if active else '停用'}。")
    return 0


def main() -> int:
    init_store()
    parser = argparse.ArgumentParser(description="CitrusQA 本地账号管理")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="列出账号")

    p = sub.add_parser("reset", help="重置密码（忘记密码时用）")
    p.add_argument("username")
    p.add_argument("--password", default="")

    p = sub.add_parser("create", help="新建账号")
    p.add_argument("username")
    p.add_argument("--admin", action="store_true", help="创建为管理员")
    p.add_argument("--daily-limit", type=int, default=300)
    p.add_argument("--password", default="")

    for name, flag in (("enable", True), ("disable", False)):
        p = sub.add_parser(name, help=f"{'启用' if flag else '停用'}账号")
        p.add_argument("username")

    args = parser.parse_args()
    if args.cmd == "list":
        return cmd_list(args)
    if args.cmd == "reset":
        return cmd_reset(args)
    if args.cmd == "create":
        return cmd_create(args)
    if args.cmd == "enable":
        return cmd_set_active(args, True)
    return cmd_set_active(args, False)


if __name__ == "__main__":
    raise SystemExit(main())