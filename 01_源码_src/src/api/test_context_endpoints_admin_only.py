"""上下文类接口的管理员门禁（纵深防御，2026-10-31 R13）。

背景：`/api/v2/session/{id}/context-detail` 与 `/turn-context` 原本只有属主校验，
普通账号能读到系统提示词全文、token 预算与压缩阈值、实际发送给模型的请求结构。
前端已隐藏对应区块，这里锁住服务端，避免"直接调 API 就能绕开 UI"。

测试用 ASGI 直连（不启真实服务、不加载 RAG 模型），只验授权分支。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import httpx
import pytest

from src import auth
from src.api import main as main_module
from src.api.main import app

REGULAR = {"user_id": 7, "username": "member", "role": "user", "active": True}
ADMIN = {"user_id": 1, "username": "owner", "role": "admin", "active": True}


@asynccontextmanager
async def _test_lifespan(_app):
    yield


app.router.lifespan_context = _test_lifespan


def _request(method: str, path: str, **kwargs):
    transport = httpx.ASGITransport(app=app)
    cookies = kwargs.pop("cookies", None)

    async def _go():
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://testserver",
                                     follow_redirects=False) as client:
            if cookies:
                client.cookies.update(cookies)
            return await client.request(method, path, **kwargs)

    return asyncio.run(_go())


@pytest.fixture()
def own_session(monkeypatch):
    """把"会话归属"置为真：中间件只做属主校验，管理员门禁在端点内，必须先过属主。"""
    monkeypatch.setattr(main_module.session_manager, "session_belongs_to",
                        lambda sid, uid: sid == "some-session")
    return "some-session"


@pytest.mark.parametrize("path", [
    "/api/v2/session/some-session/turn-context",
    "/api/v2/session/some-session/context-detail",
])
def test_regular_account_is_denied(monkeypatch, own_session, path):
    monkeypatch.setattr(auth, "user_for_token",
                        lambda token: REGULAR if token == "member-session" else None)
    response = _request("GET", path, cookies={"citrus_auth": "member-session"})
    assert response.status_code == 403, f"{path} 对普通账号未设门禁"


@pytest.mark.parametrize("path", [
    "/api/v2/session/some-session/turn-context",
    "/api/v2/session/some-session/context-detail",
])
def test_anonymous_is_denied(monkeypatch, path):
    monkeypatch.setattr(auth, "user_for_token", lambda _token: None)
    response = _request("GET", path, cookies={"citrus_auth": "nope"})
    assert response.status_code in (401, 403), response.status_code


def test_foreign_session_is_404_before_admin_gate(monkeypatch):
    """越权会话由中间件先判 404（不泄漏存在性），属主会话才轮到管理员门禁。"""
    monkeypatch.setattr(auth, "user_for_token",
                        lambda token: REGULAR if token == "member-session" else None)
    monkeypatch.setattr(main_module.session_manager, "session_belongs_to",
                        lambda sid, uid: False)
    response = _request("GET",
                        "/api/v2/session/00000000-0000-0000-0000-000000000000/turn-context",
                        cookies={"citrus_auth": "member-session"})
    assert response.status_code == 404


def test_endpoints_declare_require_admin() -> None:
    """静态兜底：两个端点体内必须出现 require_admin，防止被误删。"""
    import inspect

    from src.api import main as main_module

    for fn in (main_module.session_turn_context, main_module.context_detail_endpoint):
        src = inspect.getsource(fn)
        assert "require_admin(request)" in src, fn.__name__
