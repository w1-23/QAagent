from src.graph.general_agent_graph import _logical_key


def test_retry_of_same_target_reuses_one_timeline_key():
    first = {"name": "export_literature_evidence", "args": {"title": "核验汇总", "evidence_rows_json": "[1]"}}
    second = {"name": "export_literature_evidence", "args": {"title": "核验汇总", "evidence_rows_json": "[1,2]"}}
    assert _logical_key("export_literature_evidence", first) == \
        _logical_key("export_literature_evidence", second)


def test_distinct_targets_stay_separate():
    a = {"name": "audit_uploaded_data", "args": {"file_index": 0}}
    b = {"name": "audit_uploaded_data", "args": {"file_index": 1}}
    assert _logical_key("audit_uploaded_data", a) != _logical_key("audit_uploaded_data", b)


def test_key_never_contains_payload_text():
    call = {"name": "search_literature", "args": {"doi": "10.3390/plants11070967"}}
    key = _logical_key("search_literature", call)
    assert key.startswith("search_literature")
    assert "plants11070967" in key
