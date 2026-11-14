"""Offline resource-pin contracts; run directly with Python (stdlib only).

Load files.py against a synthetic config module so collection never reads the
real .env, configuration, session database, or account files.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError
import importlib.util
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch


def load_store(root: Path):
    config = types.ModuleType("src.config")
    config.PROJECT_ROOT = root
    spec = importlib.util.spec_from_file_location(
        "resource_reference_test_store", Path(__file__).with_name("files.py"))
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"src.config": config}):
        spec.loader.exec_module(module)
    module.init_store()
    return module


class ResourceReferencesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="qaagent-resource-refs-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.files = load_store(self.root)
        self.sid = "session_resource_pins_01"

    def upload(self, owner=7, content=b"source", sid=None):
        return self.files.register_file(
            session_id=sid or self.sid, owner_user_id=owner,
            filename="source.txt", content=content, client_request_id="")

    def generated(self, owner=7):
        artifact_id = "b" * 32
        path = self.root / "state" / "artifacts" / self.sid / "export.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x,y\n1,2\n")
        return self.files.register_file(
            session_id=self.sid, owner_user_id=owner, filename=path.name,
            storage_path=str(path), origin=self.files.ORIGIN_GENERATED,
            artifact_id=artifact_id), path

    def pin(self, info, owner=7, resource_id="project-1"):
        return self.files.replace_resource_references(
            owner, "figure_project", resource_id, [info["file_id"]])

    def path(self, info, owner=7):
        return self.files.get_account_file(info["file_id"], owner)["path"]

    def count(self, table):
        with self.files._connect() as conn:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def test_owner_isolation_and_no_implicit_owner(self):
        own, foreign = self.upload(), self.upload(owner=8)
        refs = self.pin(own)
        self.assertEqual(refs[0]["sha256"], own["sha256"])
        self.assertNotIn("storage_path", refs[0])
        self.assertEqual(self.files.file_dependencies(own["file_id"], 8), [])
        self.assertFalse(self.files.is_file_referenced(own["file_id"], 0))
        for owner in (0, -1, 8):
            with self.assertRaises(ValueError):
                self.pin(own, owner=owner)
        with self.assertRaises(ValueError):
            self.files.replace_resource_references(
                7, "figure_project", "project-1", [own["file_id"], foreign["file_id"]])
        self.assertEqual(self.files.release_resource_references(8, "figure_project", "project-1"), 0)
        self.assertEqual(self.files.file_dependencies(own["file_id"], 7), refs)

    def test_replacement_is_all_or_nothing_and_deduplicates(self):
        first, second = self.upload(), self.upload(content=b"second")
        self.pin(first)
        with self.assertRaises(ValueError):
            self.files.replace_resource_references(
                7, "figure_project", "project-1", [second["file_id"], "f" * 32])
        self.assertTrue(self.files.is_file_referenced(first["file_id"], 7))
        self.assertFalse(self.files.is_file_referenced(second["file_id"], 7))
        refs = self.files.replace_resource_references(
            7, "figure_project", "project-1", [second["file_id"], second["file_id"]])
        self.assertEqual(len(refs), 1)
        self.assertFalse(self.files.is_file_referenced(first["file_id"], 7))
        self.assertEqual(self.files.replace_resource_references(7, "figure_project", "project-1", []), [])
        self.assertFalse(self.files.is_file_referenced(second["file_id"], 7))

    def test_insert_failure_rolls_back_old_reference_set(self):
        first, second = self.upload(), self.upload(content=b"new")
        refs = self.pin(first)
        with self.files._connect() as conn:
            conn.execute("CREATE TRIGGER reject_pin BEFORE INSERT ON session_file_resource_refs "
                         "BEGIN SELECT RAISE(ABORT, 'test insert failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.pin(second)
        self.assertEqual(self.files.file_dependencies(first["file_id"], 7), refs)
        self.assertFalse(self.files.is_file_referenced(second["file_id"], 7))

    def test_missing_bytes_and_checksum_mismatch_preserve_pins(self):
        first, second = self.upload(), self.upload(content=b"second")
        self.pin(first)
        second_path = self.path(second)
        second_path.write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.pin(second)
        second_path.unlink()
        with self.assertRaises(ValueError):
            self.pin(second)
        self.assertTrue(self.files.is_file_referenced(first["file_id"], 7))

    def test_all_resources_must_release_before_upload_deletion(self):
        info = self.upload()
        path = self.path(info)
        self.pin(info)
        self.pin(info, resource_id="project-2")
        self.assertFalse(self.files.delete_account_file(info["file_id"], 7))
        self.assertFalse(self.files.delete_file(self.sid, info["file_id"], owner_user_id=7))
        self.assertFalse(self.files.delete_file(self.sid, info["file_id"]))
        self.assertTrue(path.exists())
        self.assertEqual(self.files.release_resource_references(7, "figure_project", "project-1"), 1)
        self.assertFalse(self.files.delete_account_file(info["file_id"], 7))
        self.assertEqual(self.files.release_resource_references(7, "figure_project", "project-2"), 1)
        self.assertTrue(self.files.delete_account_file(info["file_id"], 7))
        self.assertFalse(path.exists())
        self.assertEqual(self.files.release_resource_references(7, "figure_project", "project-2"), 0)

    def test_generated_entry_and_artifact_helpers_respect_pins(self):
        info, path = self.generated()
        refs = self.pin(info)
        aid, fid = info["artifact_id"], info["file_id"]
        self.assertEqual(self.files.artifact_dependencies(aid, 7), refs)
        self.assertTrue(self.files.is_artifact_referenced(aid, 7))
        self.assertFalse(self.files.is_artifact_referenced(aid, 8))
        self.assertFalse(self.files.remove_generated_entry(self.sid, aid))
        self.assertFalse(self.files.delete_file(self.sid, fid, allow_generated=True, owner_user_id=7))
        self.files.release_resource_references(7, "figure_project", "project-1")
        self.assertTrue(self.files.delete_file(self.sid, fid, allow_generated=True, owner_user_id=7))
        self.assertTrue(path.exists())  # Artifact bytes have a separate lifecycle.

    def test_pinned_refresh_rejects_new_path_and_digest(self):
        info, old_path = self.generated()
        refs = self.pin(info)
        new_path = old_path.with_name("new.csv")
        for data in (old_path.read_bytes(), b"new bytes"):
            new_path.write_bytes(data)
            with self.assertRaisesRegex(ValueError, "resource-referenced"):
                self.files.register_file(
                    session_id=self.sid, owner_user_id=7, filename=new_path.name,
                    storage_path=str(new_path), origin=self.files.ORIGIN_GENERATED,
                    artifact_id=info["artifact_id"])
        self.assertEqual(self.path(info), old_path)
        self.assertEqual(self.files.file_dependencies(info["file_id"], 7), refs)
        repeated = self.files.register_file(
            session_id=self.sid, owner_user_id=7, filename=old_path.name,
            storage_path=str(old_path), origin=self.files.ORIGIN_GENERATED,
            artifact_id=info["artifact_id"])
        self.assertEqual(repeated["file_id"], info["file_id"])

    def test_ttl_skips_upload_and_export_pins_then_expires_after_release(self):
        pinned, idle = self.upload(), self.upload(content=b"idle")
        generated, generated_path = self.generated()
        pinned_path, idle_path = self.path(pinned), self.path(idle)
        self.files.replace_resource_references(
            7, "figure_project", "project-1", [pinned["file_id"], generated["file_id"]])
        with self.files._connect() as conn:
            conn.execute("UPDATE session_files SET last_accessed_at=0")
        result = self.files.cleanup_idle_sessions(1)
        self.assertEqual(result, {"purged_sessions": 1, "removed_files": 1, "expired_records": 1})
        self.assertTrue(pinned_path.exists())
        self.assertFalse(idle_path.exists())
        self.assertTrue(generated_path.exists())
        self.files.release_resource_references(7, "figure_project", "project-1")
        result = self.files.cleanup_idle_sessions(1)
        self.assertEqual(result["expired_records"], 2)
        self.assertEqual(result["removed_files"], 1)
        self.assertTrue(generated_path.exists())

    def test_missing_pinned_index_survives_read_pruning(self):
        info = self.upload()
        path = self.path(info)
        refs = self.pin(info)
        path.unlink()  # Simulate external loss, not a lifecycle operation.
        self.assertIsNone(self.files.get_file(self.sid, info["file_id"], owner_user_id=7))
        self.assertIsNone(self.files.get_account_file(info["file_id"], 7))
        self.assertEqual(self.files.list_files(self.sid, owner_user_id=7), [])
        with patch.object(self.files, "migrate_legacy_files_for_account", return_value=0):
            self.assertEqual(self.files.list_account_files(7), [])
        self.assertEqual(self.files.file_dependencies(info["file_id"], 7), refs)
        self.assertFalse(self.files.delete_account_file(info["file_id"], 7))

    def test_force_purge_clears_only_target_owner_pins_and_cross_session_turns(self):
        own, foreign = self.upload(), self.upload(owner=8)
        own_path, foreign_path = self.path(own), self.path(foreign, 8)
        self.pin(own)
        self.pin(foreign, owner=8)
        self.files.link_turn_files("different_session_01", 7, "turn-1", [own["file_id"]])
        self.files.purge_session(self.sid, strict=True, owner_user_id=7)
        self.assertFalse(own_path.exists())
        self.assertTrue(foreign_path.exists())
        self.assertEqual(self.count("session_file_resource_refs"), 1)
        self.assertEqual(self.count("session_file_turn_refs"), 0)
        self.assertTrue(self.files.is_file_referenced(foreign["file_id"], 8))

    def test_unpinned_refresh_keeps_existing_file_interface(self):
        info, old_path = self.generated()
        self.pin(info)
        self.files.release_resource_references(7, "figure_project", "project-1")
        new_path = old_path.with_name("updated.csv")
        new_path.write_bytes(b"x,y\n3,4\n")
        refreshed = self.files.register_file(
            session_id=self.sid, owner_user_id=7, filename=new_path.name,
            storage_path=str(new_path), origin=self.files.ORIGIN_GENERATED,
            artifact_id=info["artifact_id"])
        self.assertEqual(refreshed["file_id"], info["file_id"])
        self.assertNotEqual(refreshed["sha256"], info["sha256"])
        self.assertEqual(self.path(info), new_path)
        self.assertTrue(self.files.remove_generated_entry(self.sid, info["artifact_id"]))
        self.assertEqual(self.count("session_files"), 0)
        self.assertTrue(new_path.exists())

    def test_unlink_failure_preserves_index_and_allows_later_pin(self):
        info = self.upload()
        path = self.path(info)
        with patch.object(Path, "unlink", side_effect=OSError("test unlink denied")):
            self.assertFalse(self.files.delete_account_file(info["file_id"], 7))
        self.assertTrue(path.exists())
        self.assertEqual(self.count("session_files"), 1)
        self.assertEqual(len(self.pin(info)), 1)

    def test_unowned_file_is_not_adopted_and_legacy_delete_still_works(self):
        info = self.upload(owner=0)
        with self.assertRaises(ValueError):
            self.pin(info)
        self.assertFalse(self.files.delete_account_file(info["file_id"], 7))
        self.assertTrue(self.files.delete_file(self.sid, info["file_id"]))
        self.assertEqual(self.count("session_files"), 0)

    def test_pin_wins_concurrent_delete_and_ttl(self):
        for ttl in (False, True):
            with self.subTest(ttl=ttl):
                info = self.upload(content=str(ttl).encode())
                path = self.path(info)
                with self.files._connect() as conn:
                    conn.execute("UPDATE session_files SET last_accessed_at=0")
                entered, resume, deleting = threading.Event(), threading.Event(), threading.Event()
                original_hash = self.files._sha256_file

                def paused_hash(candidate):
                    entered.set()
                    if not resume.wait(5):
                        raise RuntimeError("test pin release timed out")
                    return original_hash(candidate)

                def delete():
                    deleting.set()
                    return (self.files.cleanup_idle_sessions(1) if ttl else
                            self.files.delete_account_file(info["file_id"], 7))

                with patch.object(self.files, "_sha256_file", paused_hash):
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        pin = pool.submit(self.pin, info, resource_id=f"race-{ttl}")
                        self.assertTrue(entered.wait(5))
                        deletion = pool.submit(delete)
                        try:
                            self.assertTrue(deleting.wait(5))
                            with self.assertRaises(TimeoutError):
                                deletion.result(timeout=0.1)
                        finally:
                            resume.set()
                        pin.result(timeout=5)
                        result = deletion.result(timeout=5)
                self.assertEqual(result["expired_records"] if ttl else result, 0)
                self.assertTrue(path.exists())
                self.assertTrue(self.files.is_file_referenced(info["file_id"], 7))

    def test_internal_artifact_gc_checks_pins_across_owners(self):
        info, path = self.generated(owner=8)
        self.pin(info, owner=8)
        for owner in (None, 8):
            with self.files.unreferenced_artifact(info["artifact_id"], owner) as allowed:
                self.assertFalse(allowed)
        with self.assertRaises(ValueError):
            with self.files.unreferenced_artifact(info["artifact_id"], 0):
                self.fail("zero owner must not imply a global query")
        self.assertTrue(path.exists())
        self.files.release_resource_references(8, "figure_project", "project-1")
        with self.files.unreferenced_artifact(info["artifact_id"]) as allowed:
            self.assertTrue(allowed)
            path.unlink()
        self.assertTrue(self.files.remove_generated_entry(self.sid, info["artifact_id"]))

    def test_artifact_gc_holds_lock_until_byte_deletion_finishes(self):
        info, path = self.generated()
        entered, resume, pinning = threading.Event(), threading.Event(), threading.Event()

        def gc():
            with self.files.unreferenced_artifact(info["artifact_id"]) as allowed:
                self.assertTrue(allowed)
                entered.set()
                if not resume.wait(5):
                    raise RuntimeError("test GC release timed out")
                path.unlink()
            return self.files.remove_generated_entry(self.sid, info["artifact_id"])

        def pin():
            pinning.set()
            return self.pin(info)

        with ThreadPoolExecutor(max_workers=2) as pool:
            deletion = pool.submit(gc)
            self.assertTrue(entered.wait(5))
            reference = pool.submit(pin)
            try:
                self.assertTrue(pinning.wait(5))
                with self.assertRaises(TimeoutError):
                    reference.result(timeout=0.1)
            finally:
                resume.set()
            self.assertTrue(deletion.result(timeout=5))
            with self.assertRaises(ValueError):
                reference.result(timeout=5)
        self.assertFalse(path.exists())
        self.assertEqual(self.count("session_file_resource_refs"), 0)

    def test_artifact_gc_waits_for_pin_and_refuses_deletion(self):
        info, path = self.generated()
        entered, resume, sweeping = threading.Event(), threading.Event(), threading.Event()
        original_hash = self.files._sha256_file

        def paused_hash(candidate):
            entered.set()
            if not resume.wait(5):
                raise RuntimeError("test pin release timed out")
            return original_hash(candidate)

        def gc():
            sweeping.set()
            with self.files.unreferenced_artifact(info["artifact_id"]) as allowed:
                if allowed:
                    path.unlink()
                return allowed

        with patch.object(self.files, "_sha256_file", paused_hash):
            with ThreadPoolExecutor(max_workers=2) as pool:
                reference = pool.submit(self.pin, info)
                self.assertTrue(entered.wait(5))
                deletion = pool.submit(gc)
                try:
                    self.assertTrue(sweeping.wait(5))
                    with self.assertRaises(TimeoutError):
                        deletion.result(timeout=0.1)
                finally:
                    resume.set()
                reference.result(timeout=5)
                self.assertFalse(deletion.result(timeout=5))
        self.assertTrue(path.exists())
        self.assertTrue(self.files.is_artifact_referenced(info["artifact_id"], 7))

    def test_artifact_gc_exception_releases_lock(self):
        info, _ = self.generated()
        with self.assertRaisesRegex(RuntimeError, "test GC failed"):
            with self.files.unreferenced_artifact(info["artifact_id"], 7) as allowed:
                self.assertTrue(allowed)
                raise RuntimeError("test GC failed")
        self.assertEqual(len(self.pin(info)), 1)

    def test_delete_wins_concurrent_pin(self):
        info = self.upload()
        path = self.path(info)
        entered, resume, pinning = threading.Event(), threading.Event(), threading.Event()
        original_unlink = Path.unlink

        def paused_unlink(candidate, *args, **kwargs):
            if candidate == path:
                entered.set()
                if not resume.wait(5):
                    raise RuntimeError("test delete release timed out")
            return original_unlink(candidate, *args, **kwargs)

        def pin():
            pinning.set()
            return self.pin(info)

        with patch.object(Path, "unlink", paused_unlink):
            with ThreadPoolExecutor(max_workers=2) as pool:
                deletion = pool.submit(self.files.delete_account_file, info["file_id"], 7)
                self.assertTrue(entered.wait(5))
                reference = pool.submit(pin)
                try:
                    self.assertTrue(pinning.wait(5))
                    with self.assertRaises(TimeoutError):
                        reference.result(timeout=0.1)
                finally:
                    resume.set()
                self.assertTrue(deletion.result(timeout=5))
                with self.assertRaises(ValueError):
                    reference.result(timeout=5)
        self.assertFalse(path.exists())
        self.assertEqual(self.count("session_file_resource_refs"), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
