"""Offline acceptance for the five hashed Nature builtin figure presets.

Each preset is exercised through the real in-container output path
(``src.figures.core.capture_outputs``): the analysis script freezes the table,
the independent renderer replays from those frozen bytes, and the scientific
values are compared with exact arithmetic. A final suite runs the real sandbox
worker (``src.figures.worker``) in a subprocess for every preset, and replays
one renderer from table files alone.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd

from src.figures.core import capture_outputs
from src.figures.dataset_codec import decode_frame
from src.figures.nature_receipts import validate_nature_receipt
from src.figures.script_outputs import (normalize_output_contract, output_slots,
                                        render_script_emit_line,
                                        strict_json, validate_receipt)
from src.research.script_presets import get_preset, list_presets

NATURE_PRESETS = ("nature_volcano", "nature_roc", "nature_dotplot",
                  "nature_marginal", "nature_paired")

# These two source digests predate F2 and must not drift with it.
LEGACY_DIGESTS = {
    "wide_to_long": "a95e7ddb59db1abe79bc7f744383102984751172e993b93fe8ca226bce0d162d",
    "relationship_panels": "28ff7d3e7aae1405f1ff14f7dab6d89e3c45378500fa5466c9a4012eadc64ed7",
}


def _preset(name):
    return get_preset(name)


def _contract(name):
    return normalize_output_contract(_preset(name)["outputs"])


def _write_csv(directory, name, columns, rows):
    path = Path(directory) / (name + ".csv")
    pd.DataFrame(rows, columns=columns).to_csv(path, index=False)
    return path


def _run(name, parameters, path):
    preset = _preset(name)
    contract = normalize_output_contract(preset["outputs"])
    result = capture_outputs(preset["source"], output_contract=contract,
                             namespace={"data": Path(path), "parameters": parameters})
    return preset, contract, result


class NaturePresetCatalogTests(unittest.TestCase):
    def test_catalog_identity_contracts_and_legacy_preservation(self):
        presets = {item["script_ref"]["id"]: item for item in list_presets()}
        for legacy, digest in LEGACY_DIGESTS.items():
            self.assertIn(legacy, presets)
            self.assertEqual(presets[legacy]["script_ref"]["version"], 1)
            self.assertEqual(presets[legacy]["script_ref"]["sha256"], digest)
        for name in NATURE_PRESETS:
            self.assertIn(name, presets)
            item = presets[name]
            reference = item["script_ref"]
            self.assertEqual(reference["kind"], "builtin_script")
            self.assertEqual(reference["version"], 2)
            self.assertEqual(reference["id"], name)
            full = _preset(name)
            self.assertEqual(reference["sha256"],
                             hashlib.sha256(full["source"].encode("utf-8")).hexdigest())
            self.assertTrue(full["read_only"])
            self.assertEqual(full["retention"], "service_version")
            contract = normalize_output_contract(item["outputs"])
            self.assertEqual(contract["version"], 2)
            table_names = {table["name"] for table in contract["tables"]}
            self.assertTrue(table_names)
            self.assertEqual(len(contract["figures"]), 1)
            figure = contract["figures"][0]
            self.assertTrue(figure["render_script"].strip())
            self.assertNotIn("parameters", figure["render_script"])
            self.assertIsNone(render_script_emit_line(figure["render_script"]))
            self.assertNotIn("load_dataset", figure["render_script"])
            self.assertTrue(set(figure["parents"]).issubset(table_names))
            self.assertEqual(figure["formats"], ["svg"])
        # A stale reference must be refused, never silently re-bound.
        stale = dict(_preset("nature_volcano")["script_ref"])
        stale["version"] = 1
        with self.assertRaises(ValueError):
            get_preset(stale)
        tampered = dict(_preset("nature_volcano")["script_ref"])
        tampered["sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            get_preset(tampered)


class NatureVolcanoPresetTests(unittest.TestCase):
    COLUMNS = ["gene", "log2FC", "pvalue"]
    ROWS = [
        ("A", 2.5, 0.001),
        ("B", -1.7, 0.01),
        ("C", 0.4, 0.2),
        ("D", 3.0, 0.049),
        ("E", -2.0, 0.5),
        ("F", 1.0, 0.05),
        ("G", 1.5, 0.012345678901234567),
    ]
    PARAMETERS = {
        "effect": "log2FC", "significance": "pvalue",
        "effect_threshold": 1.0, "p_threshold": 0.05,
        "effect_label": "log2 fold change (a.u.)", "significance_label": "p value",
        "label_column": "gene", "label_top": 2,
    }

    def test_values_counts_labels_and_precision(self):
        with tempfile.TemporaryDirectory() as directory:
            path = _write_csv(directory, "volcano", self.COLUMNS, self.ROWS)
            _, contract, result = _run("nature_volcano", self.PARAMETERS, path)
        self.assertFalse(result.error)
        frame = result.tables["volcano_data"]
        self.assertEqual(list(frame.columns), [
            "feature", "effect", "p_value", "neg_log10_p", "direction", "label",
            "effect_threshold", "neg_log10_threshold", "effect_label", "significance_label"])
        self.assertEqual(len(frame), len(self.ROWS))
        self.assertEqual(frame["feature"].tolist(), [row[0] for row in self.ROWS])
        for index, row in enumerate(frame.itertuples(index=False)):
            effect = self.ROWS[index][1]
            p_value = self.ROWS[index][2]
            self.assertEqual(row.effect, effect)
            self.assertEqual(row.p_value, p_value)
            self.assertEqual(row.neg_log10_p, -np.log10(np.float64(p_value)))
        self.assertEqual(frame["direction"].tolist(),
                         ["up", "down", "not_significant", "up",
                          "not_significant", "up", "up"])
        selected = [label for label in frame["label"].tolist() if label]
        self.assertEqual(selected, ["A", "B"])
        self.assertEqual(float(frame["effect_threshold"].iloc[0]), 1.0)
        self.assertEqual(float(frame["neg_log10_threshold"].iloc[0]), -np.log10(0.05))
        self.assertEqual(set(frame["effect_label"]), {"log2 fold change (a.u.)"})
        self.assertEqual(set(frame["significance_label"]), {"p value"})
        report = result.report
        self.assertEqual(report["counts"], {"up": 4, "down": 1, "not_significant": 2})
        self.assertEqual(report["labels_selected"], 2)
        self.assertEqual(report["excluded_rows"], 0)
        self.assertEqual(result.frozen_render_receipts["volcano"]["geometry_status"],
                         "supported")

    def test_invalid_values_are_rejected_not_dropped(self):
        cases = [
            (("X", 1.0, "nan"), "non-finite"),
            (("X", 1.0, 0.0), "p-values must lie"),
            (("X", 1.0, 1.5), "p-values must lie"),
            (("X", "inf", 0.01), "non-finite"),
        ]
        for extra, fragment in cases:
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as directory:
                path = _write_csv(directory, "volcano", self.COLUMNS, [extra])
                _, _, result = _run("nature_volcano", self.PARAMETERS, path)
                self.assertIn(fragment, result.error)
                self.assertEqual(result.tables, {})
        with tempfile.TemporaryDirectory() as directory:
            path = _write_csv(directory, "volcano", ["gene", "log2FC", "pvalue"],
                              [("X", 1.0, 0.01)])
            _, _, result = _run("nature_volcano",
                                {**self.PARAMETERS, "effect": "absent"}, path)
        self.assertIn("Column absent", result.error)


class NatureRocPresetTests(unittest.TestCase):
    COLUMNS = ["score", "truth"]
    ROWS = [(0.9, 1), (0.8, 1), (0.7, 0), (0.6, 0), (0.5, 1), (0.4, 0)]
    PARAMETERS = {"score": "score", "truth": "truth",
                  "score_label": "decision score", "truth_label": "outcome"}

    def test_trapezoid_auc_and_repeated_fpr_vertical_segments(self):
        with tempfile.TemporaryDirectory() as directory:
            path = _write_csv(directory, "roc", self.COLUMNS, self.ROWS)
            _, _, result = _run("nature_roc", self.PARAMETERS, path)
        self.assertFalse(result.error)
        frame = result.tables["roc_data"]
        self.assertEqual(list(frame.columns), [
            "fpr", "tpr", "auc", "score_label", "truth_label",
            "positive_count", "negative_count", "tie_policy"])
        fpr = frame["fpr"].tolist()
        tpr = frame["tpr"].tolist()
        self.assertEqual(fpr, [0.0, 0.0, 0.0, 1 / 3, 2 / 3, 2 / 3, 1.0])
        self.assertEqual(tpr, [0.0, 1 / 3, 2 / 3, 2 / 3, 2 / 3, 1.0, 1.0])
        auc = float(frame["auc"].iloc[0])
        self.assertAlmostEqual(auc, 7.0 / 9.0, places=15)
        repeated = [y for x, y in zip(fpr, tpr) if x == 0.0]
        self.assertEqual(repeated, [0.0, 1 / 3, 2 / 3])
        self.assertEqual(repeated, sorted(repeated))
        self.assertEqual(int(frame["positive_count"].iloc[0]), 3)
        self.assertEqual(int(frame["negative_count"].iloc[0]), 3)
        self.assertEqual(set(frame["tie_policy"]),
                         {"stable_descending_score_input_order"})
        self.assertEqual(result.report["auc"], auc)
        self.assertEqual(result.frozen_render_receipts["roc"]["geometry_status"],
                         "supported")

    def test_non_binary_or_single_class_truth_rejected(self):
        for rows, fragment in [
            ([(0.9, 2), (0.8, 0)], "binary"),
            ([(0.9, 1), (0.8, 1)], "positive and one negative"),
        ]:
            with self.subTest(rows=rows), tempfile.TemporaryDirectory() as directory:
                path = _write_csv(directory, "roc", self.COLUMNS, rows)
                _, _, result = _run("nature_roc", self.PARAMETERS, path)
                self.assertIn(fragment, result.error)


class NatureDotplotPresetTests(unittest.TestCase):
    COLUMNS = ["cell", "gene", "pct", "mean"]
    ROWS = [
        ("T", "G1", 10, 1.5), ("T", "G2", 20, 2.5), ("T", "G3", 30, 3.5),
        ("B", "G1", 40, 4.5), ("B", "G2", 50, 5.5), ("B", "G3", 60, 6.5),
        ("NK", "G1", 70, 7.5), ("NK", "G2", 80, 8.5), ("NK", "G3", 90, 9.5),
    ]
    PARAMETERS = {
        "category": "cell", "group": "gene", "size": "pct", "color": "mean",
        "category_order": ["NK", "T", "B"], "group_order": ["G3", "G1", "G2"],
        "category_label": "Cell type", "group_label": "Gene",
        "size_label": "Percent expressed (%)", "color_label": "Mean expression (log2)",
    }

    def test_stable_declared_order_and_exact_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = _write_csv(directory, "dotplot", self.COLUMNS, self.ROWS)
            _, _, result = _run("nature_dotplot", self.PARAMETERS, path)
        self.assertFalse(result.error)
        frame = result.tables["dotplot_data"]
        self.assertEqual(list(frame.columns), [
            "category", "group", "category_index", "group_index",
            "size_value", "color_value", "size_min", "size_max",
            "color_min", "color_max", "category_label", "group_label",
            "size_label", "color_label"])
        self.assertEqual(len(frame), len(self.ROWS))
        self.assertEqual(frame["category_index"].tolist(),
                         [1, 1, 1, 2, 2, 2, 0, 0, 0])
        self.assertEqual(frame["group_index"].tolist(), [1, 2, 0, 1, 2, 0, 1, 2, 0])
        self.assertEqual(frame["size_value"].tolist(), [row[2] for row in self.ROWS])
        self.assertEqual(frame["color_value"].tolist(), [row[3] for row in self.ROWS])
        self.assertEqual(float(frame["size_min"].iloc[0]), 10.0)
        self.assertEqual(float(frame["size_max"].iloc[0]), 90.0)
        self.assertEqual(float(frame["color_min"].iloc[0]), 1.5)
        self.assertEqual(float(frame["color_max"].iloc[0]), 9.5)
        self.assertEqual(result.report["category_order"], ["NK", "T", "B"])
        self.assertEqual(result.report["group_order"], ["G3", "G1", "G2"])
        self.assertEqual(result.report["order_policy"], "explicit")
        self.assertEqual(result.frozen_render_receipts["dotplot"]["geometry_status"],
                         "supported")

    def test_duplicate_pairs_and_incomplete_order_rejected(self):
        duplicated = self.ROWS + [("T", "G1", 11, 1.6)]
        with tempfile.TemporaryDirectory() as directory:
            path = _write_csv(directory, "dotplot", self.COLUMNS, duplicated)
            _, _, result = _run("nature_dotplot", self.PARAMETERS, path)
        self.assertIn("pair must occur exactly once", result.error)
        with tempfile.TemporaryDirectory() as directory:
            path = _write_csv(directory, "dotplot", self.COLUMNS, self.ROWS)
            _, _, result = _run("nature_dotplot",
                                {**self.PARAMETERS, "category_order": ["T", "B"]}, path)
        self.assertIn("exactly the observed categories", result.error)


class NatureMarginalPresetTests(unittest.TestCase):
    COLUMNS = ["time_s", "signal_mv"]
    ROWS = [(0.1, 1.1), (0.2, 1.4), (0.3, 1.2), (0.4, 1.9), (0.5, 1.6),
            (0.6, 2.4), (0.7, 2.1), (0.8, 2.8), (0.9, 2.3), (1.0, 3.2),
            (1.1, 2.9), (1.2, 3.6)]
    PARAMETERS = {"x": "time_s", "y": "signal_mv", "bins": 4,
                  "x_label": "Time (s)", "y_label": "Signal (mV)"}

    def test_joint_and_marginal_share_the_same_frozen_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = _write_csv(directory, "marginal", self.COLUMNS, self.ROWS)
            _, _, result = _run("nature_marginal", self.PARAMETERS, path)
        self.assertFalse(result.error)
        points = result.tables["marginal_points"]
        bins = result.tables["marginal_bins"]
        self.assertEqual(list(points.columns),
                         ["row_index", "x", "y", "x_label", "y_label"])
        self.assertEqual(list(bins.columns),
                         ["axis", "bin_index", "bin_left", "bin_right", "count"])
        self.assertEqual(points["x"].tolist(), [row[0] for row in self.ROWS])
        self.assertEqual(points["y"].tolist(), [row[1] for row in self.ROWS])
        self.assertEqual(points["row_index"].tolist(), list(range(len(self.ROWS))))
        for axis in ("x", "y"):
            selected = bins.loc[bins["axis"] == axis].sort_values("bin_index")
            self.assertEqual(len(selected), 4)
            self.assertEqual(int(selected["count"].sum()), len(self.ROWS))
            self.assertEqual(selected["bin_index"].tolist(), [0, 1, 2, 3])
            self.assertTrue((selected["bin_right"] > selected["bin_left"]).all())
            self.assertEqual(float(selected["bin_left"].iloc[0]),
                             min(row[0] if axis == "x" else row[1] for row in self.ROWS))
            self.assertEqual(float(selected["bin_right"].iloc[-1]),
                             max(row[0] if axis == "x" else row[1] for row in self.ROWS))
        self.assertEqual(result.report["shared_source"], True)
        self.assertEqual(result.frozen_render_receipts["marginal"]["geometry_status"],
                         "supported")

    def test_missing_or_nonfinite_pairs_rejected(self):
        for bad in [(0.5, "nan"), ("inf", 1.0)]:
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as directory:
                path = _write_csv(directory, "marginal", self.COLUMNS,
                                  self.ROWS + [bad])
                _, _, result = _run("nature_marginal", self.PARAMETERS, path)
                self.assertIn("non-finite", result.error)


class NaturePairedPresetTests(unittest.TestCase):
    COLUMNS = ["subject", "condition", "value"]
    ROWS = [("S1", "before", 10.0), ("S1", "after", 12.0),
            ("S2", "before", 11.0), ("S2", "after", 9.0),
            ("S3", "before", 13.0), ("S3", "after", 15.0)]
    PARAMETERS = {"subject": "subject", "condition": "condition", "value": "value",
                  "condition_order": ["before", "after"],
                  "value_label": "Response (a.u.)"}

    def test_two_conditions_and_exact_pairs(self):
        with tempfile.TemporaryDirectory() as directory:
            path = _write_csv(directory, "paired", self.COLUMNS, self.ROWS)
            _, _, result = _run("nature_paired", self.PARAMETERS, path)
        self.assertFalse(result.error)
        frame = result.tables["paired_data"]
        excluded = result.tables["paired_excluded"]
        self.assertEqual(list(frame.columns), [
            "subject", "condition", "condition_index", "subject_index",
            "value", "value_label"])
        self.assertEqual(frame["subject"].tolist(), ["S1", "S1", "S2", "S2", "S3", "S3"])
        self.assertEqual(frame["condition"].tolist(),
                         ["before", "after"] * 3)
        self.assertEqual(frame["condition_index"].tolist(), [0, 1] * 3)
        self.assertEqual(frame["subject_index"].tolist(), [0, 0, 1, 1, 2, 2])
        self.assertEqual(frame["value"].tolist(), [row[2] for row in self.ROWS])
        self.assertEqual(list(excluded.columns), ["subject", "reason", "detail"])
        self.assertEqual(len(excluded), 0)
        self.assertEqual(result.report["condition_order"], ["before", "after"])
        self.assertEqual(result.report["excluded_subjects"], [])
        self.assertEqual(result.frozen_render_receipts["paired"]["geometry_status"],
                         "supported")

    def test_duplicates_and_missing_default_to_reject(self):
        duplicate = self.ROWS + [("S1", "before", 99.0)]
        with tempfile.TemporaryDirectory() as directory:
            path = _write_csv(directory, "paired", self.COLUMNS, duplicate)
            _, _, result = _run("nature_paired", self.PARAMETERS, path)
        self.assertIn("exclusion_policy='list'", result.error)
        missing = [row for row in self.ROWS if row != ("S2", "after", 9.0)]
        with tempfile.TemporaryDirectory() as directory:
            path = _write_csv(directory, "paired", self.COLUMNS, missing)
            _, _, result = _run("nature_paired", self.PARAMETERS, path)
        self.assertIn("exclusion_policy='list'", result.error)
        third = self.ROWS + [("S3", "later", 16.0)]
        with tempfile.TemporaryDirectory() as directory:
            path = _write_csv(directory, "paired", self.COLUMNS, third)
            _, _, result = _run("nature_paired", self.PARAMETERS, path)
        self.assertIn("exactly two conditions", result.error)

    def test_explicit_exclusion_lists_subjects_and_reasons(self):
        rows = [("S1", "before", 10.0), ("S1", "before", 99.0),
                ("S1", "after", 12.0), ("S2", "before", 11.0),
                ("S3", "before", 13.0), ("S3", "after", 15.0)]
        with tempfile.TemporaryDirectory() as directory:
            path = _write_csv(directory, "paired", self.COLUMNS, rows)
            _, _, result = _run("nature_paired",
                                {**self.PARAMETERS, "exclusion_policy": "list"}, path)
        self.assertFalse(result.error)
        frame = result.tables["paired_data"]
        excluded = result.tables["paired_excluded"]
        self.assertEqual(frame["subject"].tolist(), ["S3", "S3"])
        self.assertEqual(frame["value"].tolist(), [13.0, 15.0])
        self.assertEqual(excluded["subject"].tolist(), ["S1", "S2"])
        self.assertEqual(excluded["reason"].tolist(),
                         ["duplicate_rows", "missing_condition"])
        self.assertIn("before", excluded["detail"].iloc[0])
        self.assertIn("after", excluded["detail"].iloc[1])
        self.assertEqual(sorted(result.report["excluded_subjects"]), ["S1", "S2"])
        self.assertEqual(result.frozen_render_receipts["paired"]["geometry_status"],
                         "supported")


class NatureWorkerAcceptanceTests(unittest.TestCase):
    CASES = {
        "nature_volcano": (NatureVolcanoPresetTests.COLUMNS,
                           NatureVolcanoPresetTests.ROWS,
                           NatureVolcanoPresetTests.PARAMETERS),
        "nature_roc": (NatureRocPresetTests.COLUMNS,
                       NatureRocPresetTests.ROWS, NatureRocPresetTests.PARAMETERS),
        "nature_dotplot": (NatureDotplotPresetTests.COLUMNS,
                           NatureDotplotPresetTests.ROWS,
                           NatureDotplotPresetTests.PARAMETERS),
        "nature_marginal": (NatureMarginalPresetTests.COLUMNS,
                            NatureMarginalPresetTests.ROWS,
                            NatureMarginalPresetTests.PARAMETERS),
        "nature_paired": (NaturePairedPresetTests.COLUMNS,
                          NaturePairedPresetTests.ROWS,
                          NaturePairedPresetTests.PARAMETERS),
    }

    def _worker(self, root, name, parameters, *, source_path=None,
                replay_inputs=None, output_key="", job_id=""):
        preset = _preset(name)
        contract = normalize_output_contract(preset["outputs"])
        contract["figures"][0]["formats"] = ["svg", "png", "pdf"]
        job_id = job_id or name
        work = Path(root) / job_id
        inbox = work / "in"
        outbox = work / "out"
        inbox.mkdir(parents=True)
        outbox.mkdir()
        hashes = {}
        if replay_inputs:
            inputs = {}
            for variable, path in replay_inputs.items():
                target = inbox / (variable + ".json")
                shutil.copyfile(path, target)
                inputs[variable] = target.name
                hashes[target.name] = hashlib.sha256(target.read_bytes()).hexdigest()
            script = "pass\n"
        else:
            target = inbox / "data.csv"
            shutil.copyfile(source_path, target)
            inputs = {"data": target.name}
            hashes[target.name] = hashlib.sha256(target.read_bytes()).hexdigest()
            prefix = ("import json as _citrus_parameters_json\nparameters = "
                      "_citrus_parameters_json.loads("
                      + repr(json.dumps(parameters, ensure_ascii=False, allow_nan=False))
                      + ")\n")
            script = prefix + preset["source"]
        job = {"job_id": job_id, "script": script, "inputs": inputs,
               "outputs": output_slots(contract, output_key=output_key),
               "output_contract": contract, "expected_input_sha256": hashes,
               "qa_profile": {}, "render_mode": "initial", "output_key": output_key}
        job_path = inbox / "job.json"
        job_path.write_text(json.dumps(job, ensure_ascii=False, allow_nan=False),
                            encoding="utf-8")
        environment = dict(os.environ)
        environment["MPLBACKEND"] = "Agg"
        environment["MPLCONFIGDIR"] = str(Path(root) / "mplconfig")
        Path(environment["MPLCONFIGDIR"]).mkdir(exist_ok=True)
        completed = subprocess.run(
            [sys.executable, "-B", "-m", "src.figures.worker", "--job", str(job_path)],
            capture_output=True, text=True, timeout=300, env=environment,
            cwd=str(work))
        if completed.returncode != 0:
            raise AssertionError("worker exit %s\nstdout:\n%s\nstderr:\n%s" % (
                completed.returncode, completed.stdout[-4000:],
                completed.stderr[-4000:]))
        slots = output_slots(contract, output_key=output_key)
        receipt = validate_receipt((outbox / "result.json").read_bytes(), contract,
                                   job_id=job_id, output_key=output_key)
        self.assertEqual(receipt["status"], "ok", receipt)
        return {"contract": contract, "slots": slots, "outbox": outbox,
                "receipt": receipt}

    def _manifest(self, run, figure_name):
        slot = run["slots"]["figure:%s:manifest" % figure_name]
        return json.loads((run["outbox"] / slot).read_text(encoding="utf-8"))

    def test_real_worker_exports_every_preset(self):
        with tempfile.TemporaryDirectory() as root:
            for name in NATURE_PRESETS:
                columns, rows, parameters = self.CASES[name]
                with self.subTest(preset=name):
                    csv = _write_csv(root, name, columns, rows)
                    _, contract, captured = _run(name, parameters, csv)
                    self.assertFalse(captured.error)
                    run = self._worker(root, name, parameters, source_path=csv)
                    figure_name = contract["figures"][0]["name"]
                    for table in contract["tables"]:
                        slot = run["slots"]["table:%s" % table["name"]]
                        decoded = decode_frame(strict_json(
                            (run["outbox"] / slot).read_bytes()))
                        expected = captured.tables[table["name"]]
                        self.assertEqual(list(decoded.columns), list(expected.columns))
                        self.assertEqual(len(decoded), len(expected))
                        for column in expected.columns:
                            self.assertEqual(decoded[column].tolist(),
                                             expected[column].tolist())
                    manifest = self._manifest(run, figure_name)
                    pdf_slot = run["slots"]["figure:%s:pdf" % figure_name]
                    pdf = (run["outbox"] / pdf_slot).read_bytes()
                    receipt = validate_nature_receipt(manifest, pdf=pdf)
                    self.assertIn(receipt["status"], {"pass", "warn", "review_required"})
                    self.assertEqual(
                        manifest["frozen_render_receipt"]["geometry_status"], "supported")
                    self.assertEqual(manifest["frozen_render_receipt"]["geometry_unsupported"], [])
                    for fmt in ("svg", "png", "pdf"):
                        slot = run["slots"]["figure:%s:%s" % (figure_name, fmt)]
                        blob = (run["outbox"] / slot).read_bytes()
                        self.assertTrue(blob)
                    self.assertGreater(len(manifest["objects"]), 0)

    def test_renderer_replays_from_table_files_alone(self):
        with tempfile.TemporaryDirectory() as root:
            columns, rows, parameters = self.CASES["nature_marginal"]
            csv = _write_csv(root, "marginal_replay", columns, rows)
            initial = self._worker(root, "nature_marginal", parameters,
                                   source_path=csv, job_id="marginal_initial")
            table_files = {
                table["name"]: initial["outbox"]
                / initial["slots"]["table:%s" % table["name"]]
                for table in initial["contract"]["tables"]
            }
            replay = self._worker(root, "nature_marginal", {},
                                  replay_inputs=table_files,
                                  output_key="marginal", job_id="marginal_replay")
            manifest = self._manifest(replay, "marginal")
            pdf = (replay["outbox"]
                   / replay["slots"]["figure:marginal:pdf"]).read_bytes()
            validate_nature_receipt(manifest, pdf=pdf)
            self.assertEqual(manifest["frozen_render_receipt"]["geometry_status"],
                             "supported")
            self.assertEqual(replay["receipt"]["figure_count"], 1)
            for fmt in ("svg", "png", "pdf"):
                blob = (replay["outbox"]
                        / replay["slots"]["figure:marginal:%s" % fmt]).read_bytes()
                self.assertTrue(blob)


if __name__ == "__main__":
    unittest.main()
