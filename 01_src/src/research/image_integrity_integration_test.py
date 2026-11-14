"""Real temporary auth/files/artifact publication integration, synthetic PNGs only.

Run directly with Python; the config module is replaced BEFORE application
imports so no real .env, accounts, sessions, files, or model settings are read.
"""
from __future__ import annotations

import ast
from dataclasses import replace
import gc
import hashlib
import importlib
import importlib.util
import io
import json
from pathlib import Path
import random
import socket
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import warnings

from PIL import Image
from pydantic import ValidationError


class ImageAuditIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bootstrap = tempfile.TemporaryDirectory(prefix="image-audit-bootstrap-")
        cls.addClassCleanup(cls.bootstrap.cleanup)
        cls.addClassCleanup(gc.collect)
        config = types.ModuleType("src.config")
        config.PROJECT_ROOT = Path(cls.bootstrap.name)
        config.settings = types.SimpleNamespace()
        configuration = patch.dict(sys.modules, {"src.config": config})
        configuration.start()
        cls.addClassCleanup(configuration.stop)
        paths = patch.object(sys, "path", [str(Path(__file__).resolve().parents[2]), *sys.path])
        paths.start()
        cls.addClassCleanup(paths.stop)
        noise = warnings.catch_warnings()
        noise.__enter__()
        warnings.simplefilter("ignore", ResourceWarning)
        cls.addClassCleanup(noise.__exit__, None, None, None)
        offline = patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden in image integration test"))
        offline.start()
        cls.addClassCleanup(offline.stop)
        cls.auth = importlib.import_module("src.auth")
        cls.files = importlib.import_module("src.session.files")
        cls.manager = importlib.import_module("src.session.manager")
        cls.tracing = importlib.import_module("src.core.tracing")
        cls.publisher = importlib.import_module("src.research.tools")
        cls.adapter = importlib.import_module("src.research.image_integrity_tools")
        cls.catalog = importlib.import_module("src.core.tool_catalog")
        # Avoid importing every unrelated executable tool just to inspect its
        # provider-agnostic outcome normalizer.
        spec = importlib.util.spec_from_file_location(
            "_image_audit_outcomes", Path(__file__).parents[1] / "tools" / "outcome.py")
        cls.outcomes = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.outcomes
        spec.loader.exec_module(cls.outcomes)
        cls.addClassCleanup(sys.modules.pop, spec.name, None)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="image-audit-integration-")
        self.addCleanup(temporary.cleanup)
        self.addCleanup(gc.collect)
        self.root = Path(temporary.name)
        state = self.root / "state"
        state.mkdir()
        for module, name, value in (
            (self.auth, "PROJECT_ROOT", self.root),
            (self.auth, "DB_PATH", state / "accounts.sqlite3"),
            (self.files, "PROJECT_ROOT", self.root),
            (self.files, "DB_PATH", state / "session_files.sqlite3"),
            (self.files, "FILES_ROOT", state / "session_files"),
            (self.publisher, "PROJECT_ROOT", self.root),
            (self.manager, "PROJECT_ROOT", self.root),
            (self.manager, "STATE_DIR", state),
            (self.manager.session_manager, "db_path", str(state / "sessions.db")),
        ):
            p = patch.object(module, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.manager.session_manager._init_db_sync()
        self.auth.init_store()
        self.files.init_store()
        self.user, token, error = self.auth.register("image_owner", "temporary-test-password")
        self.assertIsNone(error)
        self.assertEqual(self.auth.user_for_token(token)["user_id"], self.user["user_id"])
        self.other, _, error = self.auth.register("image_other", "temporary-test-password")
        self.assertIsNone(error)
        self.owner = self.user["user_id"]
        self.sid = "image_audit_session_01"
        self.other_sid = "image_other_session_02"
        self.manager.session_manager._create_session_sync(self.sid, self.owner)
        self.manager.session_manager._create_session_sync(self.other_sid, self.other["user_id"])
        tokens = [
            (self.tracing._user_id, self.tracing._user_id.set(self.owner)),
            (self.tracing._session_id, self.tracing._session_id.set(self.sid)),
            (self.tracing._client_request_id, self.tracing._client_request_id.set("image-audit-turn")),
        ]
        for variable, token in tokens:
            self.addCleanup(variable.reset, token)
        rng = random.Random(7)
        image = Image.frombytes("RGB", (64, 64), bytes(rng.randrange(256) for _ in range(64 * 64 * 3)))
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        image.close()
        self.png = stream.getvalue()
        self.first = self.upload()
        self.second = self.upload()
        self.ids = [self.first["file_id"], self.second["file_id"]]

    def upload(self, *, owner=None, data=None):
        uid = self.owner if owner is None else owner
        return self.files.register_file(
            session_id=self.sid if uid == self.owner else self.other_sid,
            owner_user_id=uid, filename="synthetic_image.png", mime="image/png",
            content=self.png if data is None else data)

    def invoke(self, ids=None, **extra):
        response = self.adapter.audit_research_images.invoke({
            "type": "tool_call", "id": "image_audit_call", "name": "audit_research_images",
            "args": {"file_ids": self.ids if ids is None else ids, **extra}})
        content = json.loads(response.content)
        self.assertEqual(content, response.artifact)
        outcome = self.outcomes.normalize_tool_outcome("audit_research_images", response.content, response.artifact)
        self.assertEqual(outcome.status, content["_tool_outcome"]["status"])
        return content

    def generated_count(self):
        with self.files._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM session_files WHERE origin='generated'").fetchone()[0]

    def assert_no_link(self, result):
        self.assertIsNone(result["report"])
        self.assertEqual(result["_tool_outcome"]["artifacts"], [])
        self.assertNotIn("download_url", json.dumps(result))

    def test_actual_account_file_publication_and_stored_report(self):
        result = self.invoke()
        self.assertEqual(result["_tool_outcome"]["status"], "complete")
        self.assertEqual(result["publication_status"], "complete")
        self.assertEqual(result["finding_count"], 1)
        entry = self.files.get_account_file(result["report"]["file_id"], self.owner)
        self.assertIsNotNone(entry)
        self.assertIsNone(self.files.get_account_file(entry["file_id"], self.other["user_id"]))
        stored = entry["path"].read_bytes()
        report = json.loads(stored)
        self.assertEqual(hashlib.sha256(stored).hexdigest(), result["report"]["sha256"])
        self.assertEqual(entry["producer_tool"], "audit_research_images")
        self.assertEqual(report["sources"][0]["sha256"], self.first["sha256"])
        self.assertTrue(report["human_review_required"])
        self.assertEqual(report["findings"][0]["verification"]["different_pixels"], 0)
        self.assertEqual(result["_tool_outcome"]["artifacts"], [result["report"]["artifact_url"]])
        self.assertEqual(self.generated_count(), 1)
        self.assertLess(len(json.dumps(result)), 9000)

    def test_own_historical_session_image_is_allowed(self):
        history_sid = "image_owned_history_03"
        self.manager.session_manager._create_session_sync(history_sid, self.owner)
        historical = self.files.register_file(
            session_id=history_sid, owner_user_id=self.owner,
            filename="historical_synthetic.png", mime="image/png", content=self.png)
        result = self.invoke([self.ids[0], historical["file_id"]])
        self.assertEqual(result["_tool_outcome"]["status"], "complete")
        entry = self.files.get_account_file(result["report"]["file_id"], self.owner)
        self.assertEqual(entry["session_id"], self.sid)
        self.assertEqual(self.files.get_account_file(historical["file_id"], self.owner)["session_id"], history_sid)

    def test_foreign_file_is_not_read_or_published(self):
        foreign = self.upload(owner=self.other["user_id"])
        result = self.invoke([self.ids[0], foreign["file_id"]])
        self.assertEqual(result["error"]["code"], "source_unavailable")
        self.assertEqual(result["_tool_outcome"]["status"], "failed")
        self.assert_no_link(result)
        self.assertEqual(self.generated_count(), 0)

    def test_missing_disabled_and_deleted_account_fail_before_file_resolution(self):
        for owner in (None, 0, 999999):
            with self.subTest(owner=owner):
                self.tracing.set_user_id(owner)
                with patch.object(self.files, "get_account_file", side_effect=AssertionError("must not resolve")):
                    result = self.invoke()
                self.assertEqual(result["error"]["code"], "account_unavailable")
                self.assert_no_link(result)
        self.tracing.set_user_id(self.owner)
        self.auth.set_user_status(self.owner, False)
        with patch.object(self.files, "get_account_file", side_effect=AssertionError("must not resolve")):
            result = self.invoke()
        self.assertEqual(result["error"]["code"], "account_unavailable")
        self.assertEqual(self.generated_count(), 0)

    def test_foreign_missing_and_unowned_session_fail_closed(self):
        self.manager.session_manager._create_session_sync("image_unowned_session_03", None)
        for sid in ("", "missing_session_04", self.other_sid, "image_unowned_session_03"):
            with self.subTest(sid=sid):
                self.tracing.set_session_id(sid)
                result = self.invoke()
                self.assertEqual(result["error"]["code"], "session_unavailable")
                self.assert_no_link(result)
        self.assertEqual(self.generated_count(), 0)

    def test_schema_refuses_model_owner_path_url_and_limits(self):
        for extras in ({"owner_user_id": self.other["user_id"]}, {"path": "E:/private.png"},
                       {"url": "https://example.invalid/image.png"}, {"limits": {"max_images": 100}}):
            with self.subTest(extras=extras):
                with self.assertRaises(ValidationError):
                    self.invoke(**extras)
        for ids in ([self.ids[0], "https://example.invalid/image.png"],
                    [self.ids[0], "E:/private.png"], [self.ids[0], self.ids[0]]):
            result = self.invoke(ids)
            self.assertEqual(result["error"]["code"], "invalid_file_ids")
            self.assert_no_link(result)
        self.assertEqual(self.generated_count(), 0)

    def test_missing_bytes_and_unknown_file_are_unavailable(self):
        path = self.files.get_account_file(self.ids[1], self.owner)["path"]
        path.unlink()
        for ids in (self.ids, [self.ids[0], "f" * 32]):
            result = self.invoke(ids)
            self.assertEqual(result["error"]["code"], "source_unavailable")
            self.assert_no_link(result)
        self.assertEqual(self.generated_count(), 0)

    def test_changed_registered_source_bytes_are_rejected(self):
        path = self.files.get_account_file(self.ids[1], self.owner)["path"]
        altered = bytearray(path.read_bytes())
        altered[-1] ^= 1
        path.write_bytes(altered)
        result = self.invoke()
        self.assertEqual(result["error"]["code"], "checksum_mismatch")
        self.assert_no_link(result)
        self.assertEqual(self.generated_count(), 0)

    def test_bounded_read_does_not_trust_false_registered_size(self):
        path = self.files.get_account_file(self.ids[0], self.owner)["path"]
        with self.files._connect() as conn:
            conn.execute("UPDATE session_files SET size_bytes=0 WHERE file_id=?", (self.ids[0],))
        requested = []
        original_open = Path.open

        class ObservedReader:
            def __init__(self, handle):
                self.handle = handle
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.handle.close()
            def read(self, size=-1):
                requested.append(size)
                return self.handle.read(size)

        def bounded(candidate, *args, **kwargs):
            handle = original_open(candidate, *args, **kwargs)
            return ObservedReader(handle) if candidate == path else handle

        with patch.object(self.adapter, "_LIMITS", replace(self.adapter._LIMITS, max_image_bytes=100)), \
                patch.object(Path, "open", bounded):
            result = self.invoke()
        self.assertEqual(result["error"]["code"], "byte_limit")
        self.assertEqual(requested, [101])
        self.assert_no_link(result)

    def test_total_bytes_pixel_limit_and_archive_rejection(self):
        for changes, code in (({"max_total_bytes": len(self.png) * 2 - 1}, "byte_limit"),
                              ({"max_image_pixels": 100}, "pixel_limit"),
                              ({"max_total_pixels": 5000}, "pixel_limit")):
            with self.subTest(changes=changes):
                with patch.object(self.adapter, "_LIMITS", replace(self.adapter._LIMITS, **changes)):
                    result = self.invoke()
                self.assertEqual(result["error"]["code"], code)
                self.assert_no_link(result)
        archive = self.upload(data=b"PK\x03\x04never extracted")
        result = self.invoke([self.ids[0], archive["file_id"]])
        self.assertEqual(result["error"]["code"], "archive_unsupported")
        self.assertEqual(self.generated_count(), 0)

    def test_execution_error_is_failed_not_partial(self):
        with patch.object(self.adapter, "analyze_image_reuse", side_effect=RuntimeError("private path must not leak")):
            result = self.invoke()
        self.assertEqual(result["_tool_outcome"]["status"], "failed")
        self.assertEqual(result["error"]["code"], "analysis_failed")
        self.assertNotIn("private path", json.dumps(result))
        self.assert_no_link(result)
        self.assertEqual(self.generated_count(), 0)

    def test_real_publisher_registration_failure_has_no_false_link(self):
        with patch.object(self.files, "register_file", side_effect=OSError("test registration failure")):
            result = self.invoke()
        self.assertEqual(result["analysis_status"], "complete")
        self.assertEqual(result["publication_status"], "failed")
        self.assertEqual(result["_tool_outcome"]["status"], "partial")
        self.assertEqual(result["finding_count"], 1)
        self.assert_no_link(result)
        self.assertEqual(self.generated_count(), 0)

    def test_fabricated_publication_metadata_is_not_a_download(self):
        with patch.object(self.adapter, "publish_artifact", return_value={
                "file_id": "a" * 32, "artifact_id": "b" * 32, "sha256": "c" * 64,
                "download_url": "/made-up-link"}):
            result = self.invoke()
        self.assertEqual(result["_tool_outcome"]["status"], "partial")
        self.assert_no_link(result)
        self.assertNotIn("made-up-link", json.dumps(result))

    def test_actual_published_bytes_are_rechecked_before_link_delivery(self):
        original = self.adapter.publish_artifact

        def corrupt(**kwargs):
            publication = original(**kwargs)
            entry = self.files.get_account_file(publication["file_id"], self.owner)
            entry["path"].write_bytes(b"{}")
            return publication

        with patch.object(self.adapter, "publish_artifact", corrupt):
            result = self.invoke()
        self.assertEqual(result["publication_status"], "failed")
        self.assert_no_link(result)

    def test_partial_computation_publishes_actual_partial_report(self):
        with patch.object(self.adapter, "_LIMITS", replace(self.adapter._LIMITS, max_verification_pixels=1)):
            result = self.invoke()
        self.assertEqual(result["_tool_outcome"]["status"], "partial")
        self.assertEqual(result["analysis_status"], "partial")
        self.assertEqual(result["publication_status"], "complete")
        self.assertFalse(result["coverage"]["complete_within_declared_search"])
        entry = self.files.get_account_file(result["report"]["file_id"], self.owner)
        self.assertEqual(json.loads(entry["path"].read_bytes())["status"], "partial")

    def test_account_lookup_failure_is_structured_and_fail_closed(self):
        with patch.object(self.auth, "user_by_id", side_effect=OSError("private account DB path")), \
                patch.object(self.files, "get_account_file", side_effect=AssertionError("must not resolve")):
            result = self.invoke()
        self.assertEqual(result["_tool_outcome"]["status"], "failed")
        self.assertEqual(result["error"]["code"], "admission_failed")
        self.assertNotIn("private account DB path", json.dumps(result))
        self.assert_no_link(result)

    def test_cancellation_propagates_without_publishing_or_false_failure(self):
        cancel = importlib.import_module("src.core.cancel")
        token = cancel.CancelToken()
        token.cancel()
        with cancel.bound_token(token):
            with self.assertRaises(cancel.ToolCancelled):
                self.invoke()
        self.assertEqual(self.generated_count(), 0)

    def test_model_gets_three_samples_while_full_report_retains_all_evidence(self):
        ids = self.ids + [self.upload()["file_id"] for _ in range(2)]
        result = self.invoke(ids)
        self.assertEqual(result["finding_count"], 6)
        self.assertEqual(len(result["evidence_samples"]), 3)
        self.assertLess(len(json.dumps(result)), 9000)
        entry = self.files.get_account_file(result["report"]["file_id"], self.owner)
        self.assertEqual(len(json.loads(entry["path"].read_bytes())["findings"]), 6)

    def test_real_json_report_satisfies_shared_delivery_contract(self):
        contract = importlib.import_module("src.graph.export_contract")
        availability = importlib.import_module("src.graph.tool_availability")
        result = self.invoke()
        validations = self.publisher.validate_artifact_links(
            result["_tool_outcome"]["artifacts"], session_id=self.sid,
            owner_user_id=self.owner, request_id="image-audit-turn")
        self.assertEqual(len(validations), 1)
        self.assertTrue(validations[0]["valid"], validations)
        requirement = contract.deliverable_contract("请导出 JSON 图片复用证据报告")
        self.assertEqual(len(requirement), 1)
        self.assertEqual(requirement[0]["kinds"], ["report_json"])
        self.assertEqual(requirement[0]["formats"], ["json"])
        # Read the exact declared bindings without importing unrelated retrieval
        # implementations/configuration. No synthetic producer/type is inserted.
        tree = ast.parse((Path(__file__).parents[1] / "tools" / "__init__.py").read_text(encoding="utf-8"))
        assignment = next(node for node in tree.body if isinstance(node, ast.Assign)
                          and any(isinstance(target, ast.Name) and target.id == "_TOOL_DECLARATIONS"
                                  for target in node.targets))
        bindings = {row.elts[0].id: tuple(ast.literal_eval(part) for part in row.elts[4:])
                    for row in assignment.value.elts}
        with patch.object(availability, "declarations", return_value=bindings):
            self.assertTrue(contract.deliverables_satisfied(validations, requirement))
            self.assertEqual(contract.deliverable_tool_groups(requirement),
                             [{"audit_research_images", "audit_uploaded_data"}])
            self.assertFalse(contract.artifact_matches_requirement(
                {**validations[0], "valid": False}, requirement[0]))
            self.assertFalse(contract.artifact_matches_requirement(
                {**validations[0], "producer_tool": "export_paper_analysis"}, requirement[0]))
            self.assertFalse(contract.artifact_matches_requirement(
                {**validations[0], "filename": "wrong.html"}, requirement[0]))
            self.assertFalse(contract.artifact_matches_requirement(
                validations[0], {"kinds": ["report_docx"], "formats": ["docx"]}))
        self.assertIn("report_json", availability.KNOWN_PRODUCES)
        self.assertEqual(contract.deliverable_contract("请导出 JSON 文件")[0]["kinds"], ["report_json"])
        self.assertEqual(contract.deliverable_contract("请导出报告")[0]["formats"], ["docx"])
        self.assertEqual(contract.forced_export_plan(requirement[0], "导出 JSON 报告", "模型散文不是证据", [], {}),
                         ("", {}, "structured_report_source_required"))

    def test_registry_declaration_respects_real_catalog_invariant(self):
        path = Path(__file__).parents[1] / "tools" / "__init__.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assignment = next(node for node in tree.body if isinstance(node, ast.Assign)
                          and any(isinstance(target, ast.Name) and target.id == "_TOOL_DECLARATIONS"
                                  for target in node.targets))
        names = [row.elts[0].id for row in assignment.value.elts]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(set(names), set(self.catalog.TOOL_METADATA))
        declaration = next(row for row in assignment.value.elts if row.elts[0].id == "audit_research_images")
        self.assertEqual(ast.literal_eval(declaration.elts[3]), "session_artifact")
        self.assertEqual(ast.literal_eval(declaration.elts[4]), ("account_files", "research_tools", "session"))
        self.assertEqual(ast.literal_eval(declaration.elts[5]), ("report_json",))
        self.assertTrue(self.catalog.TOOL_METADATA["audit_research_images"].display_label)
        self.assertEqual(self.adapter.audit_research_images.response_format, "content_and_artifact")
        schema = self.adapter.audit_research_images.args_schema.model_json_schema()
        self.assertEqual(set(schema["properties"]), {"file_ids"})
        self.assertFalse(schema["additionalProperties"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
