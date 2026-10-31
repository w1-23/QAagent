"""契约测试：展示路径保留内部键（可点击），模型上下文仍清洗。

背景
----
回答正文里的 `paper_<hash>_...` 是本地文献的唯一句柄。前端把它渲染成可点击的
橙色标识 → 打开本地原文（`web/assets/app.js` 的 `linkifyCorpusKeys`）。
但同一批文本也会作为历史消息进入模型上下文，编号在里面是噪声。

因此读侧必须一分为二：
  * `for_display=True`  → 原样返回，前端才能点；
  * `for_display=False` → `scrub_internal_keys` 清洗，不进提示词。
"""
from __future__ import annotations

import pathlib
import sqlite3

import pytest

from src.session import manager as mgr_mod

ROOT = pathlib.Path(__file__).resolve().parents[2]
KEY = "paper_45bbcac3_000004_B_2017_Nature_Genetics_"


def _fresh_manager(tmp_db: pathlib.Path):
    """绕开单例与真实库，用临时 DB 造一个 SessionManager。"""
    m = mgr_mod.SessionManager.__new__(mgr_mod.SessionManager)
    m.db_path = str(tmp_db)
    m._initialized = True
    m._init_db_sync()
    return m


def _insert_ai(db: pathlib.Path, session_id: str, content: str) -> None:
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "INSERT INTO messages (session_id, msg_type, content) VALUES (?,?,?)",
            (session_id, "ai", content),
        )


def _texts(messages) -> list[str]:
    out = []
    for m in messages:
        c = getattr(m, "content", "")
        if isinstance(c, list):
            c = "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in c)
        out.append(str(c))
    return out


def test_display_mode_keeps_internal_key(tmp_path):
    db = tmp_path / "sessions.db"
    m = _fresh_manager(db)
    body = f"建议以记录 `{KEY}` 为核心证据。"
    _insert_ai(db, "s-display", body)

    msgs, _ids = m._get_messages_sync("s-display", True, True)
    joined = "\n".join(_texts(msgs))
    assert KEY in joined, "展示模式必须保留内部键，否则前端无法生成可点击标识"


def test_model_mode_scrubs_internal_key(tmp_path):
    db = tmp_path / "sessions.db"
    m = _fresh_manager(db)
    body = f"建议以记录 `{KEY}` 为核心证据。"
    _insert_ai(db, "s-model", body)

    msgs, _ids = m._get_messages_sync("s-model", True)
    joined = "\n".join(_texts(msgs))
    assert "paper_" not in joined, "模型上下文不得出现内部编号"
    assert "Nature Genetics" in joined, "清洗应替换成可读短语而非删除"


def test_async_wrapper_exposes_for_display():
    import inspect

    sig = inspect.signature(mgr_mod.SessionManager.get_messages_with_ids)
    assert "for_display" in sig.parameters
    assert sig.parameters["for_display"].default is False, "默认必须清洗（安全侧）"


def test_messages_endpoint_asks_for_display_mode():
    src = (ROOT / "src" / "api" / "main.py").read_text(encoding="utf-8")
    assert "get_messages_with_ids(\n            session_id, for_display=True)" in src, (
        "/messages 端点必须请求展示模式，否则历史里的标识又会被洗掉")
    # 上下文细分端点仍然拿清洗版，避免编号进入模型上下文
    assert "context_detail_endpoint" in src
    idx = src.index("async def context_detail_endpoint")
    tail = src[idx: idx + 4000]
    assert "for_display=True" not in tail, "context-detail 不得使用展示模式"


@pytest.mark.parametrize("flag,expect_key", [(True, True), (False, False)])
def test_manager_source_branch(tmp_path, flag, expect_key):
    """双保险：源码里分支写作单行三元表达式，便于审计。"""
    src = (ROOT / "src" / "session" / "manager.py").read_text(encoding="utf-8")
    assert ("shown = content if for_display else scrub_internal_keys(content)"
            in src)
