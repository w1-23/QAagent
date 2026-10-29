import sqlite3
import time

from src.research import tools


def _artifact_db(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE artifacts (artifact_id TEXT, session_id TEXT, "
                     "filename TEXT, mime TEXT, created_at REAL, path TEXT, "
                     "PRIMARY KEY(artifact_id, session_id))")


def test_artifact_retention_tracks_session_idle_time(tmp_path, monkeypatch):
    root = tmp_path / "state"
    artifact_db = root / "research_artifacts.sqlite3"
    monkeypatch.setattr(tools, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(tools, "_db_path", lambda: artifact_db)
    monkeypatch.setattr(tools, "_session_last_activity", lambda _sid: time.time())
    _artifact_db(artifact_db)

    sid = "session_active_artifact_01"
    artifact_id = "c" * 32
    artifact_path = root / "artifacts" / sid / "report.html"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text("<html>active</html>", encoding="utf-8")
    with sqlite3.connect(artifact_db) as conn:
        conn.execute("INSERT INTO artifacts VALUES(?,?,?,?,?,?)",
                     (artifact_id, sid, "报告.html", "text/html", 0, str(artifact_path)))

    assert tools.cleanup_expired_artifacts() == 0
    assert artifact_path.is_file()
    assert tools.resolve_artifact(sid, artifact_id) is not None

    monkeypatch.setattr(tools, "_session_last_activity", lambda _sid: 0)
    assert tools.cleanup_expired_artifacts() == 1
    assert not artifact_path.exists()
    assert tools.resolve_artifact(sid, artifact_id) is None


def test_expired_artifact_row_cannot_unlink_outside_its_session(tmp_path, monkeypatch):
    root = tmp_path / "state"
    artifact_db = root / "research_artifacts.sqlite3"
    monkeypatch.setattr(tools, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(tools, "_db_path", lambda: artifact_db)
    monkeypatch.setattr(tools, "_session_last_activity", lambda _sid: 0)
    _artifact_db(artifact_db)

    sid = "session_corrupt_artifact_01"
    artifact_id = "d" * 32
    outside = tmp_path / "keep-me.txt"
    outside.write_text("not an artifact", encoding="utf-8")
    with sqlite3.connect(artifact_db) as conn:
        conn.execute("INSERT INTO artifacts VALUES(?,?,?,?,?,?)",
                     (artifact_id, sid, "x.txt", "text/plain", 0, str(outside)))

    assert tools.resolve_artifact(sid, artifact_id) is None
    assert outside.read_text(encoding="utf-8") == "not an artifact"
