"""Fixed-renderer emit contract and deterministic failure prompts.

The v2 separated renderer receives only frozen ``data`` tables. It has no
``emit`` API. These tests pin the static preflight (AST based, never a
substring search), the runtime fallback for dynamically constructed calls,
and the valid legacy, v2 main-script and frozen-table replay paths.

The real paths run the in-container capture code with normal Matplotlib and
synthetic fixtures. No sandbox policy, permission or image is changed.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

from src.figures.core import capture_outputs
from src.figures.script_outputs import (RenderScriptEmitUnavailable,
                                        classify_render_script_error,
                                        normalize_output_contract,
                                        render_script_emit_line, output_slots)
from src.research import figure_tools
from src.research.script_presets import get_preset, list_presets

GOOD_RENDERER = (
    "frame = data['tidy']\n"
    "fig, ax = plt.subplots()\n"
    "ax.scatter(frame['x'], frame['y'])\n"
)


def _call_tool(**kwargs):
    """Call the preset tool's raw function, not the LangChain wrapper."""
    tool = figure_tools.run_research_figure_script
    function = getattr(tool, "func", tool)
    return function(**kwargs)


def _tool_help() -> str:
    tool = figure_tools.run_research_figure_script
    return (getattr(tool, "description", "")
            or getattr(getattr(tool, "func", None), "__doc__", ""))


def _frame() -> pd.DataFrame:
    return pd.DataFrame({"x": [1.0, 2.0, 3.0], "y": [2.0, 1.5, 3.5]})


def _contract(render_source: str, *, name: str = "relations") -> dict:
    return {
        "version": 2,
        "tables": [{"name": "tidy", "parents": ["data"]}],
        "figures": [{
            "name": name,
            "title": "变量关系",
            "formats": ["svg"],
            "parents": ["tidy"],
            "render_script": render_source,
        }],
    }


class StaticRenderScriptContractTests(unittest.TestCase):
    def test_provable_emit_reference_is_rejected_with_stable_code(self):
        source = ("frame = data['tidy']\n"
                  "emit.table('tidy', frame, parents=['data'])\n")
        with self.assertRaises(RenderScriptEmitUnavailable) as caught:
            normalize_output_contract(_contract(source))
        self.assertEqual(caught.exception.reason_code,
                         "render_script_emit_not_available")
        self.assertEqual(caught.exception.line, 2)
        message = str(caught.exception)
        self.assertIn("第 2 行", message)
        self.assertIn("主 script", message)
        self.assertIn("emit.table", message)
        self.assertEqual(render_script_emit_line(source), 2)

    def test_comments_strings_and_local_bindings_are_not_rejected(self):
        commented = ("# emit.table('tidy', frame)\n"
                     "note = \"emit.figure(...)\"\n" + GOOD_RENDERER)
        self.assertIsNone(render_script_emit_line(commented))
        self.assertEqual(normalize_output_contract(_contract(commented))["version"], 2)
        local = ("emit = print\n"
                 "emit('debug')\n" + GOOD_RENDERER)
        self.assertIsNone(render_script_emit_line(local))
        normalize_output_contract(_contract(local))

    def test_zero_figure_and_legacy_v2_are_not_rejected(self):
        tables_only = {"version": 2,
                       "tables": [{"name": "tidy", "parents": ["data"]}],
                       "figures": []}
        self.assertEqual(normalize_output_contract(tables_only)["figures"], [])
        legacy = {"version": 2, "tables": [], "figures": [
            {"name": "f", "title": "t", "formats": ["svg"]}]}
        self.assertEqual(normalize_output_contract(legacy)["figures"][0]["name"], "f")


class PreflightFailureTests(unittest.TestCase):
    def test_rejection_does_not_select_or_start_a_worker(self):
        contract = _contract("emit.figure('relations', object())\n")
        with mock.patch.object(figure_tools, "_select_script_runner") as runner:
            content, payload = _call_tool(script="pass\n", outputs_json=contract)
        runner.assert_not_called()
        self.assertEqual(payload["_tool_outcome"], {
            "status": "not_run",
            "reason_code": "script_execution_rejected",
            "artifacts": [],
        })
        self.assertEqual(payload["error_code"], "render_script_emit_not_available")
        self.assertIn("RENDER_SCRIPT_EMIT_NOT_AVAILABLE", content)
        self.assertEqual(json.loads(content)["error_code"],
                         "render_script_emit_not_available")

    def test_repeated_preflight_rejection_is_deterministic_and_side_effect_free(self):
        contract = _contract("emit.table('tidy', frame)\n")
        with mock.patch.object(figure_tools, "_select_script_runner") as runner:
            first = _call_tool(script="pass\n", outputs_json=contract)
            second = _call_tool(script="pass\n", outputs_json=contract)
        runner.assert_not_called()
        self.assertEqual(first, second)


class RealCaptureContractTests(unittest.TestCase):
    def test_v2_main_script_and_separated_renderer_execute(self):
        contract = normalize_output_contract(_contract(GOOD_RENDERER))
        result = capture_outputs(
            "emit.table('tidy', data, parents=['data'])\n",
            output_contract=contract, namespace={"data": _frame()})
        self.assertEqual(result.error, "")
        self.assertIn("relations", result.figures)
        self.assertEqual(
            result.frozen_render_receipts["relations"]["version"], 1)

    def test_frozen_table_replay_executes_without_the_main_script(self):
        contract = normalize_output_contract(_contract(GOOD_RENDERER))
        result = capture_outputs(
            "raise AssertionError('main script must not run on replay')\n",
            output_contract=contract, output_key="relations",
            frozen_tables={"tidy": _frame()})
        self.assertEqual(result.error, "")
        self.assertIn("relations", result.figures)

    def test_legacy_v2_emit_figure_still_works(self):
        contract = normalize_output_contract({
            "version": 2, "tables": [], "figures": [
                {"name": "f", "title": "t", "formats": ["svg"]}]})
        result = capture_outputs(
            "fig, ax = plt.subplots()\n"
            "ax.plot([1, 2], [3, 4])\n"
            "emit.figure('f', fig)\n",
            output_contract=contract)
        self.assertEqual(result.error, "")
        self.assertIn("f", result.figures)

    def test_dynamic_emit_is_classified_at_the_runtime_boundary(self):
        source = ("eval(\"emit.table('tidy', 1)\")\n"
                  "fig, ax = plt.subplots()\n")
        self.assertIsNone(render_script_emit_line(source))
        contract = normalize_output_contract(_contract(source))
        result = capture_outputs(
            "emit.table('tidy', data, parents=['data'])\n",
            output_contract=contract, namespace={"data": _frame()})
        self.assertEqual(result.error_type, "render_script_emit_not_available")
        self.assertIn("RENDER_SCRIPT_EMIT_NOT_AVAILABLE", result.error)
        self.assertEqual(result.figures, {})
        self.assertIsNotNone(classify_render_script_error(
            "NameError: name 'emit' is not defined"))
        self.assertIsNone(classify_render_script_error(
            "NameError: name 'x' is not defined"))

    def test_real_worker_classifies_dynamic_emit(self):
        contract = normalize_output_contract(_contract(
            "eval(\"emit.table('tidy', 1)\")\n"
            "fig, ax = plt.subplots()\n"))
        main = ("import pandas as pd\n"
                "emit.table('tidy', pd.DataFrame({'x': [1.0, 2.0]}))\n")
        with tempfile.TemporaryDirectory() as root:
            work = Path(root) / "job"
            inbox = work / "in"
            outbox = work / "out"
            inbox.mkdir(parents=True)
            outbox.mkdir()
            job = {"job_id": "emit_dynamic", "script": main, "inputs": {},
                   "outputs": output_slots(contract), "output_contract": contract,
                   "qa_profile": {}, "render_mode": "initial", "output_key": ""}
            job_path = inbox / "job.json"
            job_path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
            environment = dict(os.environ)
            environment["MPLBACKEND"] = "Agg"
            environment["MPLCONFIGDIR"] = str(Path(root) / "mplconfig")
            Path(environment["MPLCONFIGDIR"]).mkdir(exist_ok=True)
            completed = subprocess.run(
                [sys.executable, "-B", "-m", "src.figures.worker",
                 "--job", str(job_path)],
                capture_output=True, text=True, timeout=300,
                env=environment, cwd=str(work))
        self.assertEqual(completed.returncode, 10, completed.stderr[-2000:])
        message = json.loads(completed.stdout)["message"]
        self.assertIn("RENDER_SCRIPT_EMIT_NOT_AVAILABLE", message)
        self.assertIn("data['表名']", message)


class PresetRendererContractTests(unittest.TestCase):
    def test_builtin_renderers_hold_the_fixed_emit_contract(self):
        for preset in list_presets():
            full = get_preset(preset["script_ref"]["id"])
            for figure in full["outputs"]["figures"]:
                source = figure.get("render_script")
                if source:
                    self.assertIsNone(render_script_emit_line(source), figure["name"])
                    normalize_output_contract(full["outputs"])

    def test_help_and_presets_describe_read_only_inspection_and_environments(self):
        help_text = _tool_help()
        self.assertIn("inspect_uploaded_table", help_text)
        self.assertIn("Docker", help_text)
        self.assertIn("WSL-Docker", help_text)
        self.assertIn("Apptainer", help_text)
        for preset in list_presets():
            self.assertIn("inspect_uploaded_table", preset["description"])
            self.assertIn("Docker", preset["description"])
            self.assertIn("Apptainer", preset["description"])


if __name__ == "__main__":
    unittest.main()
