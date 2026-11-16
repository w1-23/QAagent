"""Kill a synthetic child at real production checkpoint/outbox boundaries."""
from pathlib import Path
import asyncio
import json
import subprocess
import sys
import time
from types import ModuleType


def _child(root: Path, phase: str, boundary: str):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    config = ModuleType("src.config")
    config.PROJECT_ROOT = root
    sys.modules["src.config"] = config
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from src.agent.batch_journal import BatchJournal, RecoveryRequiresReconciliation
    from src.agent.checkpoint_store import open_checkpoint, checkpoint_scope
    from src.agent.harness import HarnessRuntime, execution_lock
    from src.agent.identity import ExecutionIdentity
    from src.agent.runtime import BatchObservation, FinalCheck, RunLimits
    from src.core import jobs
    jobs._DB_PATH = root / "jobs.sqlite3"
    if phase == "first":
        job_id = jobs.claim_job("synthetic-session", "synthetic-turn", owner_user_id=17).job_id
        (root / "run_id").write_text(job_id, encoding="utf-8")
    else:
        job_id = (root / "run_id").read_text(encoding="utf-8")
    identity = ExecutionIdentity(17, "synthetic-session", "synthetic-turn", job_id)
    scope = ("https://synthetic.invalid/v1", "fixture-model")
    schemas = [{"type": "function", "function": {"name": "fixture", "description": "Synthetic write",
        "parameters": {"type": "object", "properties": {}}}}]
    async def model(messages, _):
        if any(isinstance(m, ToolMessage) for m in messages):
            return AIMessage(content="done")
        return AIMessage(content="", tool_calls=[{"name": "fixture", "args": {}, "id": "native-one"}])
    async def pause():
        (root / "ready").write_text("ready", encoding="utf-8")
        await asyncio.Event().wait()
    async def effect(response, _):
        with (root / "effects").open("a", encoding="utf-8") as stream:
            stream.write("effect\n")
        if phase == "first" and boundary == "before_receipt":
            await pause()
        return BatchObservation((ToolMessage(content="saved observation", name="fixture", tool_call_id="native-one"),))
    async def execute(response, _):
        result = await BatchJournal(identity).execute(response, effect)
        if phase == "first" and boundary == "after_receipt":
            await pause()
        return result
    async def final(*_):
        return FinalCheck()
    async def run():
        with execution_lock(root / "locks", identity):
            async with open_checkpoint(root, identity, scope) as saver:
                try:
                    result = await HarnessRuntime(model, execute, lambda *_: None, final,
                        RunLimits(), schemas).run(identity, [HumanMessage(content="original task")],
                        saver=saver, resume=phase == "resume", checkpoint_config=checkpoint_scope(identity, scope))
                except RecoveryRequiresReconciliation:
                    return {"status": "reconciliation", "human_messages": 1}
                return {"status": result.status, "steps": result.model_steps,
                        "human_messages": sum(isinstance(m, HumanMessage) for m in result.messages)}
    print(json.dumps(asyncio.run(run())), flush=True)


def test_killed_process_reuses_saved_result_and_blocks_unknown_effect(tmp_path):
    for boundary in ("after_receipt", "before_receipt"):
        root = tmp_path / boundary
        root.mkdir()
        command = [sys.executable, "-I", "-B", str(Path(__file__).resolve()), str(root)]
        child = subprocess.Popen([*command, "first", boundary], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 15
            while not (root / "ready").exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            assert (root / "ready").exists(), "synthetic child did not reach the requested crash boundary"
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=10)
        resumed = subprocess.run([*command, "resume", boundary], capture_output=True, timeout=20)
        assert resumed.returncode == 0, resumed.stderr.decode(errors="replace")
        report = json.loads(resumed.stdout)
        assert report["status"] == ("complete" if boundary == "after_receipt" else "reconciliation")
        assert report["human_messages"] == 1
        if boundary == "after_receipt":
            assert report["steps"] == 2
        assert (root / "effects").read_text(encoding="utf-8") == "effect\n"


if __name__ == "__main__":
    _child(Path(sys.argv[1]), sys.argv[2], sys.argv[3])
