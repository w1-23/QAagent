"""Synthetic-only S6 checks. No SSH, service commands, models or app imports.

Run: python -I engineering/product_evolution/release/test_release.py
"""
from __future__ import annotations
from contextlib import closing
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tarfile
import tempfile
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import release as r
import gpu_entry
import launch_gpu
import switch_service


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        temp_parent = HERE.parent / "results/release-tests"
        temp_parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="citrus-s6-synthetic-", dir=temp_parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        (self.source / "01_src/src/api").mkdir(parents=True)
        (self.source / "01_src/src/api/main.py").write_text("# current DIRTY worktree version\n", encoding="utf-8")
        self.data = self.root / "runtime"
        (self.data / "state").mkdir(parents=True)
        self.asset = self.data / "workspace/attachment.bin"
        self.asset.parent.mkdir()
        self.asset.write_bytes(b"private synthetic referenced bytes")
        self.db = self.data / "state/sessions.db"
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("CREATE TABLE parent(id INTEGER PRIMARY KEY)")
            conn.execute("CREATE TABLE files(id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES parent(id), storage_path TEXT, metadata TEXT)")
            conn.execute("INSERT INTO parent VALUES(1)")
            conn.execute("INSERT INTO files VALUES(1,1,?,?)", (str(self.asset), json.dumps({"output_path": str(self.asset)})))
        # A second DB with no conventional extension proves header discovery.
        with closing(sqlite3.connect(self.data / "state/secondary-store")) as conn, conn:
            conn.execute("CREATE TABLE audit(value TEXT)")
            conn.execute("INSERT INTO audit VALUES('new 8030 data')")
        self.profile = {"service": r.SERVICE, "port": 8030, "roots": {"runtime": str(self.data)},
                        "source_path": str(self.source), "relative_reference_base": str(self.data),
                        "missing_reference_waivers": {}, "max_total_backup_bytes": 100 * 1024**2, "min_free_bytes": 0}
        self.backups = self.root / "backups"
        self.backups.mkdir()
        self.compat = self.root / "compat.json"
        self.compat.write_bytes(r.json_bytes({"format": r.FORMAT, "databases": {
            name: [info["schema_sha256"]] for name, info in r.schema_inventory({"runtime": self.data}).items()}}))
        self.bundle = self.root / "release.tar.gz"
        self.stopped = lambda: {"ActiveState": "inactive", "MainPID": "0", "InvocationID": "synthetic-8030"}

    def build(self):
        return r.build_bundle(self.source, self.bundle, "s6-synthetic", self.compat, apply=True)

    def snapshot(self, name="snapshot"):
        target = self.backups / name
        r.backup(self.profile, target, boundary=self.stopped, apply=True)
        return target

    def test_bundle_preserves_dirty_bytes_excludes_state_secrets_cache_results(self):
        for rel in ("01_src/agent/.env", "01_src/state/accounts.sqlite3", "01_src/workspace/private.txt",
                    "01_src/embedding/vector.bin", "01_src/.cache/private", "01_src/results/log.txt"):
            p = self.source / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("never bundle", encoding="utf-8")
        plan = r.build_bundle(self.source, self.bundle, "s6-synthetic", self.compat)
        self.assertTrue(plan["dry_run"])
        self.assertFalse(self.bundle.exists())
        result = self.build()
        manifest = r.verify_bundle(self.bundle, result["sha256"])
        self.assertEqual(set(manifest["files"]), {"01_src/src/api/main.py", "rollback-compatibility.json"})
        self.assertEqual(manifest["files"]["01_src/src/api/main.py"]["sha256"], r.digest(self.source / "01_src/src/api/main.py"))
        self.assertTrue(Path(str(self.bundle) + ".sha256.json").is_file())
        with self.assertRaises(r.ReleaseError): self.build()

    def test_source_only_bundle_explicitly_blocks_apply(self):
        r.build_bundle(self.source, self.bundle, "s6-synthetic", apply=True)
        manifest = r.verify_bundle(self.bundle)
        self.assertEqual(manifest["deployment_status"], "BLOCKED")
        self.assertTrue(manifest["blocked_apply"])
        self.assertFalse(manifest["rollback_compatibility_reviewed"])
        stage = Path(r.stage_bundle(self.bundle, self.root / "releases", r.digest(self.bundle), apply=True)["target"])
        self.assertEqual(r.read_json(stage / "rollback-compatibility.json")["databases"], {})
        with self.assertRaisesRegex(r.ReleaseError, "BLOCKED"):
            switch_service.configure(self.profile, stage, self.snapshot(), self.root / "transaction", self.compat,
                                     self.root / "citrusqa-next.service.d/s6-release.conf", boundary=self.stopped)

    def test_publisher_does_not_replace_even_empty_directory(self):
        source, target = self.root / "publish-from", self.root / "publish-to"
        source.mkdir(); target.mkdir()
        with self.assertRaises(OSError): r.publish_directory(source, target)
        self.assertTrue(source.exists())
        self.assertTrue(target.exists())

    def test_exact_fixture_hash_waiver_invalidates_on_change(self):
        config = self.source / "02_config/config.yaml"
        config.parent.mkdir()
        config.write_text("api_key: synthetic-fixture\n", encoding="utf-8")
        review = self.root / "review.json"
        review.write_bytes(r.json_bytes({"02_config/config.yaml": {"sha256": r.digest(config), "reason": "Synthetic test literal only"}}))
        r.build_bundle(self.source, self.bundle, "s6-synthetic", self.compat, apply=True, secret_review=review)
        config.write_text("api_key: changed-fixture\n", encoding="utf-8")
        with self.assertRaises(r.ReleaseError):
            r.build_bundle(self.source, self.root / "second.tar.gz", "s6-second", self.compat, secret_review=review)

    def test_literal_config_secret_blocks_bundle(self):
        config = self.source / "02_config/config.yaml"
        config.parent.mkdir()
        config.write_text("api_key: a-real-looking-private-value\n", encoding="utf-8")
        with self.assertRaises(r.ReleaseError): self.build()

    def test_stage_hashes_and_existing_release_protection(self):
        result = self.build()
        releases = self.root / "releases"
        self.assertTrue(r.stage_bundle(self.bundle, releases, result["sha256"])["dry_run"])
        self.assertFalse(releases.exists())
        staged = r.stage_bundle(self.bundle, releases, result["sha256"], apply=True)
        self.assertEqual((Path(staged["target"]) / "01_src/src/api/main.py").read_text(), "# current DIRTY worktree version\n")
        with self.assertRaises(r.ReleaseError): r.stage_bundle(self.bundle, releases, result["sha256"], apply=True)
        with self.assertRaises(r.ReleaseError): r.verify_bundle(self.bundle, "0" * 64)

    def malicious_archive(self, name, member_type=tarfile.REGTYPE):
        archive = self.root / "evil.tar.gz"
        with tarfile.open(archive, "w:gz") as out:
            info = tarfile.TarInfo(name)
            info.type = member_type
            info.linkname = "../../outside"
            info.size = 0
            out.addfile(info, io.BytesIO(b""))
        return archive

    def test_safe_extract_rejects_traversal_absolute_backslash_and_links(self):
        for name, kind in (("../escape", tarfile.REGTYPE), ("/absolute", tarfile.REGTYPE),
                           ("C:/escape", tarfile.REGTYPE), ("a\\escape", tarfile.REGTYPE),
                           ("safe", tarfile.SYMTYPE), ("safe", tarfile.LNKTYPE), ("safe", tarfile.FIFOTYPE)):
            with self.subTest(name=name, kind=kind):
                with self.assertRaises(r.ReleaseError): r.verify_bundle(self.malicious_archive(name, kind))
        self.assertFalse((self.root / "escape").exists())

    def test_corrupt_bundle_member_rejected(self):
        self.build()
        bad = self.root / "bad.tar.gz"
        with tarfile.open(self.bundle, "r:gz") as inp, tarfile.open(bad, "w:gz") as out:
            for info in inp.getmembers():
                data = inp.extractfile(info).read()
                if info.name.endswith("main.py"):
                    data = b"x" * len(data)
                out.addfile(info, io.BytesIO(data))
        with self.assertRaises(r.ReleaseError): r.verify_bundle(bad)

    def test_backup_all_databases_bytes_and_isolated_restore(self):
        plan = r.backup(self.profile, self.backups / "snapshot", boundary=self.stopped)
        self.assertEqual(len(plan["databases"]), 2)
        self.assertFalse((self.backups / "snapshot").exists())
        backup = self.snapshot()
        evidence = r.verify_backup(backup)
        self.assertEqual(len(evidence["databases"]), 2)
        self.assertEqual((backup / "runtime/workspace/attachment.bin").read_bytes(), self.asset.read_bytes())
        drill = self.root / "isolated-drill"
        self.assertTrue(r.restore_drill(backup, drill)["dry_run"])
        self.assertFalse(drill.exists())
        result = r.restore_drill(backup, drill, apply=True)
        self.assertEqual(result["all_file_hashes"], "ok")
        self.assertEqual(r.verify_backup(drill)["databases"], evidence["databases"])
        self.assertTrue(self.db.exists())
        with self.assertRaises(r.ReleaseError): r.restore_drill(backup, self.data / "wrong", apply=True)
        with self.assertRaises(r.ReleaseError): r.restore_drill(backup, drill, apply=True)

    def test_wal_backup_is_self_contained(self):
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("INSERT INTO parent VALUES(2)")
            conn.commit()
            backup = self.snapshot()
            with closing(sqlite3.connect(backup / "runtime/state/sessions.db")) as out:
                self.assertEqual(out.execute("SELECT count(*) FROM parent").fetchone()[0], 2)
                self.assertEqual(out.execute("PRAGMA journal_mode").fetchone()[0], "delete")
            self.assertFalse((backup / "runtime/state/sessions.db-wal").exists())
            r.restore_drill(backup, self.root / "wal-drill", apply=True)

    def test_corrupt_backup_bytes_fail_before_restore(self):
        backup = self.snapshot()
        (backup / "runtime/workspace/attachment.bin").write_bytes(b"CORRUPT")
        target = self.root / "drill"
        with self.assertRaises(r.ReleaseError): r.restore_drill(backup, target, apply=True)
        self.assertFalse(target.exists())

    def test_damaged_database_header_and_inventory_mismatch_fail(self):
        self.profile["expected_databases"] = ["runtime/state/sessions.db"]
        with self.assertRaisesRegex(r.ReleaseError, "DB inventory"):
            self.snapshot()
        self.profile["expected_databases"] = ["runtime/state/sessions.db", "runtime/state/secondary-store"]
        original = self.db.read_bytes()
        self.db.write_bytes(b"BROKEN DB HEADER" + original[16:])
        with self.assertRaisesRegex(r.ReleaseError, "invalid header"):
            self.snapshot()
        self.assertFalse((self.backups / "snapshot").exists())

    def test_foreign_key_corruption_rejected(self):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("INSERT INTO files VALUES(2,999,?, '{}')", (str(self.asset),))
        with self.assertRaises(r.ReleaseError): self.snapshot()
        self.assertFalse((self.backups / "snapshot").exists())

    def test_live_boundary_change_aborts_without_publication(self):
        counter = [0]
        def changed():
            counter[0] += 1
            return self.stopped() | {"InvocationID": str(counter[0])}
        with self.assertRaises(r.ReleaseError):
            r.backup(self.profile, self.backups / "snapshot", boundary=changed, apply=True)
        self.assertFalse((self.backups / "snapshot").exists())
        self.assertFalse(list(self.backups.glob(".backup-*")))

    def test_reference_outside_backup_or_missing_fails_closed(self):
        outside = self.root / "outside.bin"
        outside.write_bytes(b"outside")
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("UPDATE files SET storage_path=?", (str(outside),))
        with self.assertRaises(r.ReleaseError): self.snapshot()
        outside.unlink()
        with self.assertRaises(r.ReleaseError): self.snapshot()
        self.profile["missing_reference_waivers"] = {str(outside): "Synthetic tombstoned historical file"}
        evidence = r.verify_backup(self.snapshot())
        self.assertTrue(evidence["missing_reference_waivers"])

    def reference_alias_fixture(self):
        legacy = self.root / "old8000/legacy-report.bin"
        legacy.parent.mkdir()
        legacy.write_bytes(self.asset.read_bytes())
        expected = r.digest(self.asset)
        entry = {"path": str(self.asset), "sha256": expected,
                 "reason": "Synthetic exact legacy reference, measured equal current8030 copy"}
        self.profile["reference_aliases"] = {str(legacy): entry}
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("UPDATE files SET storage_path=?", (str(legacy),))
        return legacy, entry

    def test_reference_alias_backup_restore_uses_equal_root_bytes_without_live_sources(self):
        legacy, entry = self.reference_alias_fixture()
        observed = r.referenced_files(self.db, {"runtime": self.data}, self.profile)
        alias = next(row for row in observed if row["original"] == str(legacy))
        self.assertEqual(alias["backup_path"], "runtime/workspace/attachment.bin")
        self.assertEqual(alias["sha256"], entry["sha256"])
        self.assertEqual(alias["alias"]["reason"], entry["reason"])
        plan = r.backup(self.profile, self.backups / "snapshot", boundary=self.stopped)
        self.assertEqual(plan["reference_alias_count"], 1)
        snapshot = self.snapshot()
        manifest = r.verify_backup(snapshot)
        self.assertEqual(manifest["reference_aliases"][str(legacy)], alias)
        self.assertNotIn("old8000", "/".join(manifest["files"]))
        with closing(r.db_open(snapshot / "runtime/state/sessions.db")) as conn:
            self.assertEqual(conn.execute("SELECT storage_path FROM files").fetchone()[0], str(legacy))
        expected_bytes = self.asset.read_bytes()
        legacy.unlink()
        self.asset.unlink()
        r.verify_backup(snapshot)  # Neither original nor live alias target exists.
        drill = self.root / "alias-drill"
        r.restore_drill(snapshot, drill, apply=True)
        self.assertEqual((drill / "runtime/workspace/attachment.bin").read_bytes(), expected_bytes)
        r.verify_backup(drill)

    def test_reference_alias_bad_fields_outside_targets_and_missing_bytes_refused(self):
        legacy, entry = self.reference_alias_fixture()
        outside = self.root / "outside-target.bin"
        outside.write_bytes(self.asset.read_bytes())
        invalid = [entry | {"sha256": "0" * 64}, entry | {"sha256": "not-a-hash"},
                   {key: value for key, value in entry.items() if key != "sha256"},
                   entry | {"reason": " "}, {key: value for key, value in entry.items() if key != "reason"},
                   entry | {"path": str(outside)}, entry | {"path": str(self.data / "missing.bin")},
                   entry | {"path": "relative.bin"}, entry | {"path": str(self.asset.parent)}, None]
        for invalid_entry in invalid:
            with self.subTest(entry=invalid_entry):
                self.profile["reference_aliases"] = {str(legacy): invalid_entry}
                with self.assertRaises(r.ReleaseError):
                    self.snapshot()
                self.assertFalse((self.backups / "snapshot").exists())
        self.profile["reference_aliases"] = {str(legacy): entry}
        legacy.unlink()
        with self.assertRaisesRegex(r.ReleaseError, "file missing"):
            self.snapshot()
        legacy.mkdir()
        with self.assertRaisesRegex(r.ReleaseError, "regular file"):
            self.snapshot()

    def test_reference_alias_requires_exact_key_and_unique_target_root(self):
        legacy, entry = self.reference_alias_fixture()
        other = self.root / "other-legacy.bin"
        other.write_bytes(legacy.read_bytes())
        self.profile["reference_aliases"] = {str(other): entry}
        with self.assertRaisesRegex(r.ReleaseError, "outside backup roots"):
            self.snapshot()  # An unrelated exact alias must not adopt the DB path.
        self.profile["reference_aliases"] = {str(legacy): entry}
        with self.assertRaisesRegex(r.ReleaseError, "unique backup root"):
            r.validate_reference_aliases({"runtime": self.data, "overlap": self.asset.parent}, self.profile)

    def test_reference_alias_hardlinks_and_symlink_flags_refused(self):
        legacy, entry = self.reference_alias_fixture()
        for side in (legacy, self.asset):
            link = self.root / ("extra-link-" + side.name)
            os.link(side, link)
            try:
                with self.subTest(side=str(side)), self.assertRaisesRegex(r.ReleaseError, "single-link"):
                    self.snapshot()
            finally:
                link.unlink()
        # Windows may forbid unprivileged symlink creation; exercise the link
        # flag seam for both files while hardlink rejection above uses real links.
        original_flag = Path.is_symlink
        for side in (legacy, self.asset):
            def flagged(path, selected=side):
                return path == selected or original_flag(path)
            with self.subTest(symlink=str(side)), patch.object(Path, "is_symlink", flagged):
                with self.assertRaisesRegex(r.ReleaseError, "links are refused|Symlink refused"):
                    self.snapshot()
        r.validate_reference_aliases({"runtime": self.data}, self.profile)

    def test_reference_alias_original_changes_during_copy_abort_backup_publication(self):
        legacy, entry = self.reference_alias_fixture()
        real_copy = r.shutil.copyfile
        changed = []
        def copy_and_change_original(source, target, *args, **kwargs):
            result = real_copy(source, target, *args, **kwargs)
            if Path(source) == self.asset:
                legacy.write_bytes(b"changed outside8030 source during snapshot")
                changed.append(True)
            return result
        with patch.object(r.shutil, "copyfile", side_effect=copy_and_change_original):
            with self.assertRaisesRegex(r.ReleaseError, "Reference alias hash mismatch"):
                self.snapshot()
        self.assertTrue(changed)
        self.assertFalse((self.backups / "snapshot").exists())
        self.assertFalse(list(self.backups.glob(".backup-*")))
        self.assertEqual(r.digest(self.asset), entry["sha256"])

    def test_reference_alias_expected_bytes_not_replaced_by_generic_manifest_hash(self):
        legacy, entry = self.reference_alias_fixture()
        snapshot = self.snapshot()
        alias_target = snapshot / "runtime/workspace/attachment.bin"
        alias_target.write_bytes(b"corrupted alias target")
        manifest = r.read_json(snapshot / "BACKUP.json")
        row = manifest["files"]["runtime/workspace/attachment.bin"]
        row["sha256"], row["size"] = r.digest(alias_target), alias_target.stat().st_size
        (snapshot / "BACKUP.json").write_bytes(r.json_bytes(manifest))
        # Even a rehashed generic inventory cannot override the independently
        # recorded, dual-measured alias expected bytes.
        with self.assertRaisesRegex(r.ReleaseError, "Alias bytes hash mismatch"):
            r.verify_backup(snapshot)
        with self.assertRaises(r.ReleaseError):
            r.restore_drill(snapshot, self.root / "corrupt-alias-drill", apply=True)

    def test_root_requirements_and_old8000_root_prohibition_are_preserved(self):
        requirements = ("requirements.txt", "requirements-base.txt", "requirements-gpu.txt")
        for name in requirements:
            (self.source / name).write_text("# synthetic requirement\n", encoding="utf-8")
        self.build()
        manifest = r.verify_bundle(self.bundle)
        self.assertTrue(set(requirements).issubset(manifest["files"]))
        profile = self.profile | {"roots": {"runtime": str(Path("/home/WZQ/CitrusQA/agent").resolve())}}
        with patch.object(Path, "is_dir", return_value=True):
            with self.assertRaisesRegex(r.ReleaseError, "Old 8000"):
                r.load_roots(profile)

    def test_quota_refuses_without_auto_prune(self):
        self.profile["max_total_backup_bytes"] = 1
        with self.assertRaises(r.ReleaseError): self.snapshot()
        self.assertFalse((self.backups / "snapshot").exists())

    def test_schema_rollback_checks_every_db_and_user_version(self):
        roots = {"runtime": self.data}
        compatibility = r.read_json(self.compat)
        self.assertEqual(len(r.check_compatibility(roots, compatibility)), 2)
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute("PRAGMA user_version=9")
        with self.assertRaises(r.ReleaseError): r.check_compatibility(roots, compatibility)
        compatibility["databases"].pop("runtime/state/secondary-store")
        with self.assertRaises(r.ReleaseError): r.check_compatibility(roots, compatibility)

    def test_retention_is_dry_by_default_keeps_two_and_unknown_dirs(self):
        for index in range(3):
            target = self.snapshot(f"backup-{index}")
            manifest = r.read_json(target / "BACKUP.json")
            manifest["created_utc"] = f"2020010{index+1}T000000Z"
            (target / "BACKUP.json").write_bytes(r.json_bytes(manifest))
        unknown = self.backups / "do-not-delete"
        unknown.mkdir()
        plan = r.prune(self.backups, keep=2, max_age_days=30)
        self.assertEqual(len(plan["remove"]), 1)
        self.assertTrue((self.backups / "backup-0").exists())
        r.prune(self.backups, keep=2, max_age_days=30, apply=True)
        self.assertFalse((self.backups / "backup-0").exists())
        self.assertTrue(unknown.exists())
        with self.assertRaises(r.ReleaseError): r.prune(self.backups, keep=1, max_age_days=30)

    def gpu_launch_fixture(self, mode=None):
        result = self.build()
        staged = Path(r.stage_bundle(self.bundle, self.root / "releases", result["sha256"], apply=True)["target"])
        # A synthetic directory stands in for the Linux link, no SIF is run.
        data = staged / "agent"
        data.mkdir()
        cache = self.root / "model-cache"
        cache.mkdir()
        sif = self.root / "synthetic.sif"
        sif.write_bytes(b"never executed")
        profile = self.profile | {"roots": {"runtime": str(data)}, "model_cache_root": str(cache),
            "apptainer_executable": sys.executable, "figure_apptainer_executable": "/usr/bin/apptainer",
            "figure_sif": str(sif), "cuda_sif": str(sif), "cuda_sif_sha256": r.digest(sif),
            "cuda_python": "/usr/bin/python3", "extra_binds": []}
        if mode is not None:
            profile["runtime_mode"] = mode
        return profile, staged, sif

    def conda_fixture(self):
        profile, staged, sif = self.gpu_launch_fixture("conda")
        prefix = self.root / "independent-conda"
        (prefix / "bin").mkdir(parents=True)
        python = prefix / "bin/python"
        python.write_text("SYNTHETIC INTERPRETER: NEVER EXECUTED", encoding="utf-8")
        python.chmod(0o755)
        (prefix / "conda-meta").mkdir()
        (prefix / "conda-meta/history").write_text("# synthetic conda create history\n", encoding="utf-8")
        (prefix / "conda-meta/python-3.12-synthetic.json").write_text('{"name":"python","version":"3.12"}', encoding="utf-8")
        profile["conda_python"] = str(python)
        identity = {"prefix": str(prefix), "base_prefix": str(prefix), "executable": str(python)}
        return profile, staged, sif, prefix, python, identity

    def test_gpu_launcher_separates_cuda_app_from_cpu_worker(self):
        profile, staged, sif = self.gpu_launch_fixture()
        cache = Path(profile["model_cache_root"])
        with patch.object(launch_gpu, "checked_path", lambda value, directory=False: Path(value)), \
             patch.object(launch_gpu, "BASE_SHA", r.digest(sif)):
            argv = launch_gpu.command(profile, staged)
            self.assertIn("--nv", argv)
            self.assertIn("CITRUS_REQUIRE_GPU=1", argv)
            self.assertIn("CITRUS_REQUIRE_FIGURE_WORKER=1", argv)
            self.assertIn("FIGURES_SANDBOX_BACKEND=apptainer", argv)
            self.assertIn("FIGURES_SANDBOX_IMAGE=" + str(sif), argv)
            self.assertIn("CITRUSQA_MODEL_CACHE_ROOT=" + str(cache), argv)
            manifest = r.read_json(staged / "MANIFEST.json")
            manifest["blocked_apply"] = True
            (staged / "MANIFEST.json").write_bytes(r.json_bytes(manifest))
            with self.assertRaises(ValueError): launch_gpu.command(profile, staged)
        self.assertNotIn("torch", sys.modules)

    def test_conda_argv_and_shared_strict_environment_scrub_pollution(self):
        profile, stage, sif, prefix, python, identity = self.conda_fixture()
        dirty = {"APPTAINER_BIND": "/", "APPTAINERENV_MAIN_API_KEY": "synthetic poison",
                 "SINGULARITYENV_LD_PRELOAD": "/poison.so", "LD_PRELOAD": "/poison.so",
                 "LD_LIBRARY_PATH": "/old-env/lib", "PYTHONPATH": "/poison", "PYTHONHOME": "/poison",
                 "CONDA_PREFIX": "/wrong", "VIRTUAL_ENV": "/wrong", "PATH": "/poison/bin",
                 "CITRUS_REQUIRE_GPU": "0", "CITRUS_ONNX_DEVICE": "cpu", "CITRUS_REQUIRE_FIGURE_WORKER": "0",
                 "FIGURES_SANDBOX_BACKEND": "local", "FIGURES_SANDBOX_ALLOW_LOCAL": "1",
                 "CUDA_VISIBLE_DEVICES": "0", "CITRUS_COOKIE_SECURE": "1"}
        with patch.object(launch_gpu, "checked_path", lambda value, directory=False: Path(value)), \
             patch.object(launch_gpu, "BASE_SHA", r.digest(sif)), \
             patch.object(launch_gpu.subprocess, "run", return_value=SimpleNamespace(stdout=json.dumps(identity))) as probe:
            argv, env = launch_gpu.launch_plan(profile, stage, dirty)
            argv_apptainer, env_apptainer = launch_gpu.launch_plan(profile | {"runtime_mode": "apptainer"}, stage, dirty)
        self.assertIn("--nv", argv_apptainer)
        for key in ("CITRUS_REQUIRE_GPU", "CITRUS_ONNX_DEVICE", "CITRUS_REQUIRE_FIGURE_WORKER",
                    "FIGURES_SANDBOX_BACKEND", "FIGURES_SANDBOX_IMAGE_SHA256", "FIGURES_SANDBOX_ALLOW_LOCAL"):
            self.assertEqual(env_apptainer[key], env[key])
        self.assertNotIn("APPTAINER_BIND", env_apptainer)
        self.assertNotIn("LD_LIBRARY_PATH", env_apptainer)
        self.assertNotIn("PYTHONHOME", env_apptainer)
        self.assertEqual(argv, [str(python), "-I", str(stage / "engineering/product_evolution/release/gpu_entry.py"),
                                "--source", str(stage / "01_src")])
        self.assertNotIn("--nv", argv)
        for key in dirty:
            if key.startswith(("APPTAINER", "SINGULARITY")) or key in launch_gpu.UNSAFE_ENV:
                self.assertNotIn(key, env)
                self.assertNotIn(key, probe.call_args.kwargs["env"])
        self.assertEqual(env["CONDA_PREFIX"], str(prefix.resolve()))
        self.assertEqual(env["CITRUS_RUNTIME_MODE"], "conda")
        self.assertEqual(env["CITRUS_REQUIRE_GPU"], "1")
        self.assertEqual(env["CITRUS_ONNX_DEVICE"], "gpu")
        self.assertEqual(env["CITRUS_REQUIRE_FIGURE_WORKER"], "1")
        self.assertEqual(env["FIGURES_SANDBOX_BACKEND"], "apptainer")
        self.assertEqual(env["FIGURES_SANDBOX_IMAGE_SHA256"], r.digest(sif))
        self.assertEqual(env["FIGURES_SANDBOX_ALLOW_LOCAL"], "0")
        self.assertEqual(env["HF_HUB_OFFLINE"], "1")
        self.assertEqual(env["CUDA_VISIBLE_DEVICES"], "0")
        self.assertTrue(env["PATH"].startswith(str(prefix / "bin")))
        probe.assert_called_once()
        self.assertIn("-I", probe.call_args.args[0])
        self.assertNotIn("CONDA_PREFIX", probe.call_args.kwargs["env"])
        self.assertNotIn("torch", sys.modules)

    def test_conda_keeps_blocked_manifest_sha_and_agent_gates(self):
        profile, stage, sif, prefix, python, identity = self.conda_fixture()
        with patch.object(launch_gpu, "checked_path", lambda value, directory=False: Path(value)), \
             patch.object(launch_gpu, "BASE_SHA", r.digest(sif)), \
             patch.object(launch_gpu.subprocess, "run", side_effect=AssertionError("must not reach interpreter")) as probe:
            bad_hash = profile | {"cuda_sif_sha256": "0" * 64}
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                launch_gpu.launch_plan(bad_hash, stage)
            with patch.object(launch_gpu, "BASE_SHA", "f" * 64):
                with self.assertRaisesRegex(ValueError, "hash mismatch"):
                    launch_gpu.launch_plan(profile, stage)
            bad_data = profile | {"roots": {"runtime": str(self.data)}}
            with self.assertRaisesRegex(ValueError, "agent link"):
                launch_gpu.launch_plan(bad_data, stage)
            manifest = r.read_json(stage / "MANIFEST.json")
            manifest["blocked_apply"] = True
            (stage / "MANIFEST.json").write_bytes(r.json_bytes(manifest))
            with self.assertRaisesRegex(ValueError, "BLOCKED"):
                launch_gpu.launch_plan(profile, stage)
            probe.assert_not_called()

    def test_unknown_runtime_mode_has_no_automatic_fallback(self):
        self.assertEqual(launch_gpu.runtime_mode({}), "apptainer")
        for value in ("auto", "docker", "", None, "CONDA"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "runtime_mode"):
                launch_gpu.launch_plan({"runtime_mode": value}, self.source)

    def test_conda_invalid_metadata_and_venv_refused_before_interpreter(self):
        profile, stage, sif, prefix, python, identity = self.conda_fixture()
        history = prefix / "conda-meta/history"
        with patch.object(launch_gpu, "checked_path", lambda value, directory=False: Path(value)), \
             patch.object(launch_gpu.subprocess, "run", side_effect=AssertionError("must not execute invalid prefix")) as probe:
            history.unlink()
            with self.assertRaisesRegex(ValueError, "genuine Conda"):
                launch_gpu.conda_interpreter(profile)
            history.write_text("", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "genuine Conda"):
                launch_gpu.conda_interpreter(profile)
            history.write_text("synthetic history", encoding="utf-8")
            (prefix / "pyvenv.cfg").write_text("home = /system/python\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "venvs"):
                launch_gpu.conda_interpreter(profile)
            (prefix / "pyvenv.cfg").unlink()
            (prefix / "conda-meta/python-3.12-synthetic.json").unlink()
            with self.assertRaisesRegex(ValueError, "Python package metadata"):
                launch_gpu.conda_interpreter(profile)
            probe.assert_not_called()

    def test_conda_probe_rejects_masquerading_or_wrong_interpreter(self):
        profile, stage, sif, prefix, python, identity = self.conda_fixture()
        for field in ("prefix", "base_prefix", "executable"):
            wrong = identity | {field: str(self.root / "another-environment")}
            with self.subTest(field=field), \
                 patch.object(launch_gpu, "checked_path", lambda value, directory=False: Path(value)), \
                 patch.object(launch_gpu.subprocess, "run", return_value=SimpleNamespace(stdout=json.dumps(wrong))):
                with self.assertRaisesRegex(ValueError, "identity"):
                    launch_gpu.conda_interpreter(profile)
        with patch.object(launch_gpu, "checked_path", lambda value, directory=False: Path(value)), \
             patch.object(launch_gpu.subprocess, "run") as probe:
            with self.assertRaisesRegex(ValueError, "Old 8000"):
                launch_gpu.conda_interpreter({"conda_python": "/home/WZQ/CitrusQA/.venv/bin/python"})
            probe.assert_not_called()

    def test_entry_requires_actual_onnx_cuda_probe_after_safe_wheel_preload(self):
        events = []
        def preload(**kwargs):
            self.assertEqual(kwargs, {"directory": ""})
            events.append("preload")
        def failed_probe(provider):
            self.assertEqual(provider, "CUDAExecutionProvider")
            events.append("probe")
            return False, "numeric mismatch or CPU kernel execution"
        ort = SimpleNamespace(get_available_providers=lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"],
                              preload_dlls=preload)
        with self.assertRaisesRegex(RuntimeError, "kernel/numeric probe failed"):
            gpu_entry.require_cuda_runtime(ort, failed_probe)
        self.assertEqual(events, ["preload", "probe"])
        self.assertEqual(gpu_entry.require_cuda_runtime(ort, lambda provider: (True, "actual CUDA Gemm/profile")),
                         "actual CUDA Gemm/profile")
        no_cuda = SimpleNamespace(get_available_providers=lambda: ["CPUExecutionProvider"], preload_dlls=Mock())
        probe = Mock()
        with self.assertRaisesRegex(RuntimeError, "CUDAExecutionProvider is required"):
            gpu_entry.require_cuda_runtime(no_cuda, probe)
        no_cuda.preload_dlls.assert_not_called()
        probe.assert_not_called()
        old_ort = SimpleNamespace(get_available_providers=lambda: ["CUDAExecutionProvider"])
        self.assertEqual(gpu_entry.require_cuda_runtime(old_ort, lambda provider: (True, "actual kernels")), "actual kernels")
        self.assertNotIn("torch", sys.modules)

    def test_entry_both_modes_abort_on_actual_cuda_probe_failure_before_models(self):
        fake_ort = ModuleType("onnxruntime")
        fake_ort.get_available_providers = lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"]
        fake_ort.preload_dlls = Mock()
        fake_hardware = ModuleType("src.engine.hardware")
        fake_hardware.probe_provider = Mock(return_value=(False, "CUDA kernel profiling failed"))
        fake_hardware.runtime_readiness = Mock()
        fake_src = ModuleType("src")
        fake_src.__path__ = []
        fake_engine = ModuleType("src.engine")
        fake_engine.__path__ = []
        modules = {"onnxruntime": fake_ort, "src": fake_src, "src.engine": fake_engine,
                   "src.engine.hardware": fake_hardware}
        original_cwd, original_path = Path.cwd(), list(sys.path)
        try:
            for mode in ("apptainer", "conda"):
                fake_hardware.probe_provider.reset_mock()
                fake_ort.preload_dlls.reset_mock()
                env = {"CITRUS_REQUIRE_GPU": "1", "CITRUS_ONNX_DEVICE": "gpu",
                       "CITRUS_REQUIRE_FIGURE_WORKER": "1", "CITRUS_RUNTIME_MODE": mode}
                with self.subTest(mode=mode), patch.dict(sys.modules, modules), patch.dict(os.environ, env, clear=True), \
                     patch.object(sys, "argv", ["gpu_entry.py", "--source", str(self.source / "01_src")]):
                    with self.assertRaisesRegex(RuntimeError, "CUDA kernel profiling failed"):
                        gpu_entry.main()
                    fake_hardware.probe_provider.assert_called_once_with("CUDAExecutionProvider")
                    fake_ort.preload_dlls.assert_called_once_with(directory="")
                    fake_hardware.runtime_readiness.assert_not_called()
        finally:
            os.chdir(original_cwd)
            sys.path[:] = original_path
        self.assertNotIn("torch", sys.modules)

    def test_gpu_gate_rejects_unloaded_cpu_or_unverified_models(self):
        model = {"loaded": True, "gpu": True, "inference_verified": True, "runtime_error": None}
        evidence = {"ready": True, "require_gpu": True, "mode": "gpu", "models": {"embedder": dict(model), "reranker": dict(model)}}
        gpu_entry.require_ready(evidence)
        for key, value in (("loaded", False), ("gpu", False), ("inference_verified", False), ("runtime_error", "failed")):
            evidence["models"]["embedder"] = model | {key: value}
            with self.assertRaises(RuntimeError): gpu_entry.require_ready(evidence)
        self.assertNotIn("torch", sys.modules)

    def test_switch_transaction_and_schema_gated_code_only_rollback(self):
        result = self.build()
        stage = Path(r.stage_bundle(self.bundle, self.root / "releases", result["sha256"], apply=True)["target"])
        # Windows CI cannot create directory symlinks without extra privilege.
        # The transaction itself is real; mock only the Linux agent link and
        # the argv validation requiring that link (never execute a runtime).
        sif = self.root / "base.sif"
        sif.write_bytes(b"synthetic SIF bytes; never executed")
        cache = self.root / "cache"
        cache.mkdir()
        private = self.root / "private-profile.json"
        self.profile.update({"figure_sif": str(sif), "cuda_sif": str(sif), "cuda_sif_sha256": r.digest(sif),
                             "model_cache_root": str(cache), "apptainer_executable": sys.executable,
                             "figure_apptainer_executable": "/usr/bin/apptainer", "cuda_python": "/usr/bin/python3",
                             "host_python": sys.executable, "private_profile_path": str(private), "extra_binds": []})
        private.write_bytes(r.json_bytes(self.profile))
        dropin = self.root / "citrusqa-next.service.d/s6-release.conf"
        dropin.parent.mkdir()
        original = b"[Service]\nEnvironment=EXISTING_TEST=1\n"
        dropin.write_bytes(original)
        backup = self.snapshot()
        transaction = self.root / "transactions/first"
        # The real systemd renderer rejects Windows paths; use a synthetic
        # renderer here while testing the complete transaction/rollback logic.
        with patch.object(r, "BASE_SIF_SHA256", r.digest(sif)), patch.object(launch_gpu, "BASE_SHA", r.digest(sif)), \
             patch.object(launch_gpu, "checked_path", lambda value, directory=False: Path(value)), \
             patch.object(launch_gpu, "command", return_value=["synthetic", "--nv"]), \
             patch.object(r, "preflight", return_value={"synthetic": True}), \
             patch.object(Path, "symlink_to") as agent_link, \
             patch.object(switch_service, "unit_text", lambda *a: b"[Service]\n# synthetic new code\n"):
            plan = switch_service.configure(self.profile, stage, backup, transaction, self.compat, dropin,
                                            boundary=self.stopped)
            self.assertTrue(plan["dry_run"])
            self.assertFalse(transaction.exists())
            switch_service.configure(self.profile, stage, backup, transaction, self.compat, dropin, apply=True,
                                     boundary=self.stopped, capture_unit=lambda: b"synthetic effective service")
            agent_link.assert_called_once_with(self.data.resolve(), target_is_directory=True)
            self.assertNotEqual(dropin.read_bytes(), original)
            # New user data survives code rollback. A NEW snapshot is mandatory.
            self.asset.write_bytes(b"new user data since release")
            with self.assertRaisesRegex(r.ReleaseError, "changed since backup"):
                switch_service.rollback(self.profile, transaction, backup, apply=True, boundary=self.stopped)
            with closing(sqlite3.connect(self.db)) as conn:
                conn.execute("PRAGMA user_version=9")
            incompatible = self.snapshot("incompatible")
            with self.assertRaisesRegex(r.ReleaseError, "schema"):
                switch_service.rollback(self.profile, transaction, incompatible, apply=True, boundary=self.stopped)
            self.assertNotEqual(dropin.read_bytes(), original)
            with closing(sqlite3.connect(self.db)) as conn:
                conn.execute("PRAGMA user_version=0")
            fresh = self.snapshot("fresh")
            switch_service.rollback(self.profile, transaction, fresh, apply=True, boundary=self.stopped)
            self.assertEqual(dropin.read_bytes(), original)
            self.assertEqual(self.asset.read_bytes(), b"new user data since release")

    def test_systemd_renderer_requires_simple_absolute_paths(self):
        profile = {"host_python": "/home/WZQ/citrusqa/venv/bin/python", "private_profile_path": "/home/WZQ/citrusqa/ops/profile.json"}
        text = switch_service.unit_text(profile, "/home/WZQ/citrusqa/releases/s6-test").decode()
        self.assertIn("--execute", text)
        self.assertIn("Restart=no", text)
        self.assertNotIn("8000", text)
        with self.assertRaises(r.ReleaseError): switch_service.unit_text(profile, "/bad path")


if __name__ == "__main__":
    unittest.main(verbosity=2)
