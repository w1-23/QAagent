# -*- coding: utf-8 -*-
"""Contracts for the retrieval translation path (gateway routing header)."""
from __future__ import annotations

import sys
import types

from src.retrieval import query_translation as qt


def test_gateway_headers_adds_opencode_session(monkeypatch, tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "opencode_session_id").write_text("abc123", encoding="utf-8")
    import src.config as cfg

    monkeypatch.setattr(cfg, "PROJECT_ROOT", tmp_path, raising=False)
    headers = qt._gateway_headers("https://opencode.ai/inference/go/openai/v1")
    assert headers == {"x-opencode-session": "abc123"}


def test_gateway_headers_skips_other_providers():
    assert qt._gateway_headers("https://dashscope.aliyuncs.com/compatible-mode/v1") == {}
    assert qt._gateway_headers("") == {}


def test_gateway_headers_without_session_file(tmp_path, monkeypatch):
    import src.config as cfg

    monkeypatch.setattr(cfg, "PROJECT_ROOT", tmp_path, raising=False)
    assert qt._gateway_headers("https://opencode.ai/inference/go/openai/v1") == {}


def test_translation_sends_session_header(monkeypatch, tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "opencode_session_id").write_text("sess-42", encoding="utf-8")
    import src.config as cfg

    monkeypatch.setattr(cfg, "PROJECT_ROOT", tmp_path, raising=False)
    captured = {}

    class _Msg:
        content = "How does rootstock affect fruit quality?"

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    class _Completions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return _Resp()

    class _Chat:
        completions = _Completions()

    class _Client:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs
            self.chat = _Chat()

    fake_openai = types.SimpleNamespace(OpenAI=_Client)
    monkeypatch.setitem(sys.modules, "openai", fake_openai)
    monkeypatch.setattr(qt.settings, "HYDE_THINKING_OFF", False, raising=False)

    out = qt._generate_english_translation("砧木对果实品质有什么影响")
    assert out == "How does rootstock affect fruit quality?"
    assert captured["client_kwargs"]["default_headers"] == {"x-opencode-session": "sess-42"}
