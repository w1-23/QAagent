"""Batch admission must fail before registration; readiness imports the loop."""
import base64
import importlib

import pytest
from fastapi import HTTPException

from src.api import main


@pytest.mark.parametrize("bad", [
    {"name": "bad.exe", "data": "YQ=="},
    {"name": "bad.txt", "data": "***invalid***"},
])
def test_bad_later_attachment_cannot_partially_register(monkeypatch, bad):
    from src.session import files
    written = []
    monkeypatch.setattr(files, "init_store", lambda: None)
    monkeypatch.setattr(files, "register_file", lambda **kw: written.append(kw))
    with pytest.raises(HTTPException) as error:
        main._register_session_files([{"name": "good.txt", "data": "YQ=="}, bad], "session", 17)
    assert error.value.status_code == 400 and written == []


def test_json_rules_attachment_uses_existing_text_path(monkeypatch):
    from src.session import files
    written = []
    monkeypatch.setattr(files, "init_store", lambda: None)
    def register(**kw):
        written.append(kw)
        return {"file_id": "actual-generated-id"}
    monkeypatch.setattr(files, "register_file", register)
    encoded = base64.b64encode(b'{"required_fields":["Sample"]}').decode("ascii")
    assert main._register_session_files([{"name": "rules.json", "data": encoded}], "session", 17) == ["actual-generated-id"]
    assert written[0]["content"] == b'{"required_fields":["Sample"]}'


def test_missing_lazy_agent_runtime_prevents_readiness(monkeypatch):
    real_import = importlib.import_module
    def missing(name):
        if name == "src.agent.skill_adapter":
            raise ModuleNotFoundError("synthetic-private-error")
        return real_import(name)
    monkeypatch.setattr(importlib, "import_module", missing)
    result = main._agent_runtime_readiness()
    assert result["ready"] is False
    assert result["errors"] == [{"module": "src.agent.skill_adapter", "error": "ModuleNotFoundError"}]
    assert "synthetic-private-error" not in str(result)
