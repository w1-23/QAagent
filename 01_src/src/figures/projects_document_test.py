"""A successful save returns the document it committed, never a newer one."""
import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.figures import projects, store


SEMANTIC = {"gid": "loc-1", "role": "title", "semantic_key": "panel/title", "identity_scope": "semantic"}


class SaveDocumentTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        store.set_state_dir(Path(self._tmp.name))
        self.addCleanup(store.set_state_dir, None)
        self.owner = 4242
        lease = patch("src.core.account_leases.account_lease",
                      lambda owner: contextlib.nullcontext())
        lease.start()
        self.addCleanup(lease.stop)
        self.project = store.create_project(
            owner_user_id=self.owner, title="figure", source_kind="spec",
            source={"spec": {"mark": "initial"}}, session_id="session-1")

    def _render(self, project, owner, raw_overrides, *, formats=(), render_mode="edit"):
        return list(raw_overrides), ["gid"], {"objects": [], "version": 2}, {"svg": b"<svg/>"}

    def _save(self, overrides=None):
        with patch.object(projects, "_load_revision", return_value=self.project), \
             patch.object(projects, "_render_edits", side_effect=self._render), \
             patch.object(projects, "publish_rendered", return_value={"svg": {}}):
            return projects.save_overrides(
                self.project["figure_id"], self.owner,
                overrides if overrides is not None else [])

    def test_success_returns_the_committed_document(self):
        result = self._save()

        stored = store.get_project(self.project["figure_id"], self.owner)
        self.assertEqual(result["render_version"], 1)
        self.assertIsInstance(result["document"], dict)
        self.assertEqual(result["document"]["revision"], 1)
        self.assertEqual(result["document"]["schema"], "citrus.figure-document/1")
        # The returned identity set is exactly the one the save persisted.
        self.assertEqual(result["document"], stored["document_json"])

    def _semantic_project(self):
        manifest = {"semantic_identity_version": 1, "objects": [SEMANTIC]}
        project = store.update_project_committed(
            self.project["figure_id"], self.owner, expected_revision=0,
            manifest_json=manifest, render_version=1)
        binding, = project["document_json"]["objects"]
        return project, manifest, {"object_id": binding["object_id"], "prop": "color", "value": "#123456"}

    def test_object_edit_resolves_both_entry_points_at_the_requested_revision(self):
        project, manifest, edit = self._semantic_project()
        figure_id = project["figure_id"]
        translated = {"gid": "loc-1", "prop": "color", "value": "#123456"}
        with patch.object(projects, "_render_edits", return_value=(
                [translated], ["loc-1"], manifest, {"svg": b"<svg/>"})) as render, \
             patch.object(projects, "_assert_current"), patch.object(projects, "_verify_sources"), \
             patch.object(projects, "publish_rendered", return_value={"svg": {}}):
            preview = projects.preview_overrides(figure_id, self.owner, [edit], expected_revision=1)
            saved = projects.save_overrides(figure_id, self.owner, [edit], expected_revision=1)
            self.assertEqual([call.args[2] for call in render.call_args_list], [[translated], [translated]])
        self.assertEqual(preview["render_version"], 1)
        self.assertEqual(saved["render_version"], 2)
        self.assertEqual(saved["document"]["objects"][0]["object_id"], edit["object_id"])

    def test_object_edit_rejects_wrong_identity_before_publication(self):
        project, manifest, edit = self._semantic_project()
        figure_id = project["figure_id"]
        moved = {**manifest, "objects": [{**SEMANTIC, "gid": "loc-2"}]}
        with patch.object(projects, "_render_edits", return_value=(
                [], [], moved, {"svg": b"<svg/>"})) as render, \
             patch.object(projects, "publish_rendered") as publish:
            for command, revision, code in (
                (edit, None, "revision_required"),
                (edit, 0, "revision_conflict"),
                ({**edit, "object_id": "obj_" + "0" * 32}, 1, "object_target_unavailable"),
                ({**edit, "gid": "loc-1"}, 1, "invalid_object_target"),
                (edit, 1, "object_target_changed"),
            ):
                with self.subTest(code=code), self.assertRaises(projects.ProjectError) as caught:
                    projects.save_overrides(figure_id, self.owner, [command], expected_revision=revision)
                self.assertEqual(caught.exception.code, code)
            publish.assert_not_called()
            render.assert_called_once()
        self.assertEqual(store.get_project(figure_id, self.owner)["render_version"], 1)

    def test_stale_save_does_not_attach_the_concurrent_document(self):
        def concurrent_render(project, owner, raw_overrides, *, formats=(), render_mode="edit"):
            # Another writer commits revision 1 after this save loaded revision 0.
            store.update_project(
                self.project["figure_id"], self.owner, expected_revision=0,
                manifest_json={"objects": [{"gid": "concurrent", "role": "bar"}],
                               "version": 2},
                render_version=1)
            return list(raw_overrides), ["gid"], {"objects": [], "version": 2}, {"svg": b"<svg/>"}

        with patch.object(projects, "_load_revision", return_value=self.project), \
             patch.object(projects, "_render_edits", side_effect=concurrent_render), \
             patch.object(projects, "publish_rendered", return_value={"svg": {}}):
            with self.assertRaises(projects.ProjectError) as caught:
                projects.save_overrides(self.project["figure_id"], self.owner, [])

        self.assertEqual(caught.exception.code, "revision_conflict")
        self.assertEqual(caught.exception.status, 409)
        stored = store.get_project(self.project["figure_id"], self.owner)
        self.assertEqual(stored["render_version"], 1)
        self.assertEqual(stored["document_json"]["revision"], 1)
        self.assertEqual([obj["gid"] for obj in stored["manifest_json"]["objects"]],
                         ["concurrent"])
        # The public boolean contract for a losing compare-and-swap is unchanged.
        self.assertFalse(store.update_project(
            self.project["figure_id"], self.owner,
            expected_revision=0, render_version=2))


if __name__ == "__main__":
    unittest.main()
