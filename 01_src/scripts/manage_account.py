#!/usr/bin/env python3
"""离线管理账号：列出、创建、重置密码、启停、签发邀请码。

用途：忘记管理员密码时的本地恢复手段。所有写操作都通过 ``src.auth`` 的
facade 完成（不直接拼写 SQL），数据库路径由既有配置决定，不额外触碰运行期
文件。密码只在本机终端交互输入，永远不会出现在命令行参数或输出中；邀请码
只在签发命令中显示一次。

示例：
  python scripts/manage_account.py list
  python scripts/manage_account.py reset example_user            # 交互输入新密码
  python scripts/manage_account.py create citrus_admin --admin     # 交互输入新密码
  python scripts/manage_account.py enable  example_user
  python scripts/manage_account.py disable example_collaborator
  python scripts/manage_account.py issue-invite --admin citrus_admin --days 7
"""
from __future__ import annotations

import argparse
import getpass
from getpass import GetPassWarning
import sys
import warnings
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE_ROOT))

from src.auth import (AccountInviteError, create_local_account, init_store,  # noqa: E402
                      issue_invite, list_users, reset_password,
                      set_user_status, validate_credentials)

MIN_LEN, MAX_LEN = 10, 256
DEFAULT_DAILY_LIMIT = 300
DEFAULT_INVITE_DAYS = 7
MAX_INVITE_DAYS = 30

_APPROVAL_LABELS = {"approved": "已通过", "pending": "待审核", "rejected": "已拒绝"}

# Failures are reported through stable, secret-free text only.
_SAFE_ERRORS = {
    "invalid_credentials": "用户名或密码不符合要求。",
    "invalid_password": "密码长度需为 10–256 个字符。",
    "invalid_account_options": "账号参数无效。",
    "username_unavailable": "账号已存在，请改用 reset。",
    "admin_required": "需要已启用且审核通过的管理员账号。",
    "account_not_approved": "该账号尚未通过审核，不能执行此操作。",
    "account_deletion_pending": "该账号正在删除流程中，无法执行此操作。",
    "cannot_disable_self": "不能停用当前操作者自己的账号。",
}


def _safe_error(exc: AccountInviteError) -> str:
    return _SAFE_ERRORS.get(exc.code, f"操作失败（{exc.code}）。")


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


def _find_user(username: str) -> dict | None:
    """Resolve a username through the auth facade without direct SQL."""
    needle = str(username or "").strip().casefold()
    for user in list_users():
        if str(user.get("username", "")).casefold() == needle:
            return user
    return None


def cmd_list(_args: argparse.Namespace) -> int:
    rows = list_users()
    if not rows:
        print("（账号库为空）")
        return 0
    print(f"{'用户名':<22}{'角色':<8}{'状态':<6}{'审核':<8}{'日配额':<8}创建时间")
    for u in rows:
        approval = _APPROVAL_LABELS.get(u.get("approval_status", ""), u.get("approval_status", ""))
        print(f"{u['username']:<22}{u['role']:<8}"
              f"{'启用' if u.get('active', 1) else '停用':<6}"
              f"{approval:<8}"
              f"{u.get('daily_limit', ''):<8}{u.get('created_at', '')}")
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    user = _find_user(args.username)
    if user is None:
        print(f"账号不存在：{args.username}（可用 list 查看，或用 create 新建）")
        return 2
    password = _ask_password("新密码")
    try:
        _temporary, ok = reset_password(user["user_id"], password)
    except AccountInviteError as exc:
        print(_safe_error(exc))
        return 2
    if not ok:
        print(f"账号不存在：{args.username}")
        return 2
    state = "启用" if user.get("active") else "停用"
    print(f"已重置 {user['username']} 的密码；账号保持{state}，旧会话已清除。")
    return 0


def cmd_create(args: argparse.Namespace) -> int:
    if validate_credentials(args.username, "x" * MIN_LEN):
        print("用户名或密码不符合要求。")
        return 2
    if args.daily_limit < 0:
        print("日配额不能为负数。")
        return 2
    password = _ask_password("管理员密码" if args.admin else "新账号密码")
    try:
        user_id = create_local_account(
            args.username, password,
            role="admin" if args.admin else "user",
            daily_limit=args.daily_limit)
    except AccountInviteError as exc:
        print(_safe_error(exc))
        return 2
    print(f"已创建{'管理员' if args.admin else ''}账号 {args.username}"
          f"（user_id {user_id}，日配额 {args.daily_limit}）。")
    return 0


def cmd_set_active(args: argparse.Namespace, active: bool) -> int:
    user = _find_user(args.username)
    if user is None:
        print(f"账号不存在：{args.username}")
        return 2
    try:
        ok = set_user_status(user["user_id"], active)
    except AccountInviteError as exc:
        print(_safe_error(exc))
        return 2
    if not ok:
        print(f"无法{'启用' if active else '停用'} {user['username']}。")
        return 2
    print(f"{user['username']} 已{'启用' if active else '停用'}。")
    return 0


def cmd_issue_invite(args: argparse.Namespace) -> int:
    if not 1 <= args.days <= MAX_INVITE_DAYS:
        print(f"有效期需为 1–{MAX_INVITE_DAYS} 天。")
        return 2
    admin = _find_user(args.admin)
    if admin is None:
        print(f"账号不存在：{args.admin}")
        return 2
    try:
        invite = issue_invite(actor_user_id=admin["user_id"], expires_in_days=args.days)
    except AccountInviteError as exc:
        print(_safe_error(exc))
        return 2
    label = "固定邀请码（每次签发可使用一次）" if invite.get("fixed") else "邀请码（仅显示一次）"
    print(f"{label}：{invite['code']}")
    print(f"有效期至：{invite['expires_at']}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CitrusQA 本地账号管理")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="列出账号")

    p = sub.add_parser("reset", help="重置密码（忘记密码时用）")
    p.add_argument("username")

    p = sub.add_parser("create", help="新建账号")
    p.add_argument("username")
    p.add_argument("--admin", action="store_true", help="创建为管理员")
    p.add_argument("--daily-limit", type=int, default=DEFAULT_DAILY_LIMIT)

    for name, flag in (("enable", True), ("disable", False)):
        p = sub.add_parser(name, help=f"{'启用' if flag else '停用'}账号")
        p.add_argument("username")

    p = sub.add_parser("issue-invite", help="签发一次性邀请码（需已授权管理员）")
    p.add_argument("--admin", required=True, metavar="USERNAME",
                   help="具名且已启用、审核通过的管理员账号")
    p.add_argument("--days", type=int, default=DEFAULT_INVITE_DAYS,
                   help=f"有效期天数（1–{MAX_INVITE_DAYS}，默认 {DEFAULT_INVITE_DAYS}）")
    return parser


def main(argv: list[str] | None = None) -> int:
    # Parse first so ``--help`` never opens or creates the accounts database.
    args = _build_parser().parse_args(argv)
    if args.cmd in {"reset", "create"}:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", GetPassWarning)
                init_store()
                return cmd_reset(args) if args.cmd == "reset" else cmd_create(args)
        except (GetPassWarning, EOFError):
            print("请在可隐藏密码输入的交互终端运行此命令。")
            return 2
    init_store()
    if args.cmd == "list":
        return cmd_list(args)
    if args.cmd == "enable":
        return cmd_set_active(args, True)
    if args.cmd == "disable":
        return cmd_set_active(args, False)
    if args.cmd == "issue-invite":
        return cmd_issue_invite(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
