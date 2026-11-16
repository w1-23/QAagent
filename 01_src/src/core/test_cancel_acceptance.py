# -*- coding: utf-8 -*-
"""S1 验收缺口补齐：真实副作用、幂等、迟到完成、越权读取。

`test_cancel_lifecycle.py` 覆盖状态机与检查点本身；这里只补"用户真的担心"的几件事：

1. 写文件类工具在取消后**不能留下半个文件**（`.part` 必须清理，正式名不得出现）；
2. 重复点停止是幂等的，不能把已完成任务改写成 cancelled；
3. 停止请求晚于完成时，结果是 completed + 一条 note，不是 cancelled；
4. 跨用户不能读别人的 job / 会话任务列表。
"""
import contextvars
import os
import sqlite3
import unittest
from unittest import mock

from src.core import jobs as jobs_mod
from src.core.cancel import CancelToken, ToolCancelled, bound_token


def _new_job(session_id="sess-acc-test", request_id="req-acc"):
    return jobs_mod.create_job(session_id=session_id, request_id=request_id, job_type="chat")


class _FakeArtifactRoot:
    """把 _save_artifact 的落盘根目录换到临时目录，避免脏写 state/artifacts。"""

    def __init__(self, root):
        self.root = root

    def __call__(self):
        return self.root


class ArtifactAtomicity(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = self.tmp.name
        self.sid = "sess-artifact-001"
        self.addCleanup(self.tmp.cleanup)

    def _save(self, content=b"x" * 32):
        from src.research import tools as research_tools
        with mock.patch.object(research_tools, "PROJECT_ROOT", __import__("pathlib").Path(self.root)), \
             mock.patch.object(research_tools, "get_session_id", lambda: self.sid), \
             mock.patch("src.session.files.init_store", lambda: None), \
             mock.patch("src.session.files.register_file", lambda *a, **k: None), \
             mock.patch.object(research_tools, "_db_path", lambda: os.path.join(self.root, "t.sqlite3")):
            with sqlite3.connect(os.path.join(self.root, "t.sqlite3")) as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS artifacts "
                                 "(artifact_id TEXT, session_id TEXT, filename TEXT, "
                                 "mime TEXT, created_at REAL, path TEXT)")
                conn.commit()
            return research_tools._save_artifact("report.xlsx", content, "application/vnd.ms-excel")

    def _files(self):
        found = []
        for base, _dirs, names in os.walk(self.root):
            for n in names:
                if n.endswith(".xlsx"):
                    found.append(n)
        return found

    def test_cancelled_before_write_creates_nothing(self):
        token = CancelToken(); token.cancel("user")
        with bound_token(token):
            with self.assertRaises(ToolCancelled):
                self._save()
        self.assertEqual(self._files(), [])

    def test_cancel_between_write_and_commit_leaves_no_partial_file(self):
        """关键场景：字节已经写进 .part，此时才收到停止。"""
        token = CancelToken()
        calls = {"n": 0}
        real = __import__("src.core.cancel", fromlist=["raise_if_cancelled"]).raise_if_cancelled

        def gate(where=""):
            calls["n"] += 1
            if calls["n"] >= 2:      # 第一次放行（写前），第二次=提交前，此时取消
                token.cancel("user")
            return real(where)

        with bound_token(token):
            with mock.patch("src.core.cancel.raise_if_cancelled", gate):
                with self.assertRaises(ToolCancelled):
                    self._save()
        files = self._files()
        self.assertEqual([f for f in files if not f.endswith(".part")], [])
        leftovers = [f for f in files if f.endswith(".part")]
        self.assertEqual(leftovers, [], f"临时文件未清理: {leftovers}")

    def test_normal_save_produces_exactly_one_file(self):
        path = self._save()
        files = self._files()
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].startswith(os.path.basename(path).split("_")[0]))


class CancelIdempotency(unittest.TestCase):
    def test_repeated_request_cancel_is_idempotent(self):
        job_id = _new_job(request_id="req-idem")
        self.assertTrue(jobs_mod.request_cancel(job_id, "user"))
        # 第二次：状态没变，返回 False（没有"又一次改变状态"）
        self.assertFalse(jobs_mod.request_cancel(job_id, "user"))
        self.assertEqual((jobs_mod.get_job(job_id) or {}).get("status"), "cancel_requested")

    def test_repeated_stop_does_not_double_clean(self):
        job_id = _new_job(request_id="req-idem2")
        jobs_mod.request_cancel(job_id, "user")
        self.assertTrue(jobs_mod.mark_stopped(job_id, "user", note="前端点击"))
        self.assertFalse(jobs_mod.mark_stopped(job_id, "user", note="前端点击"))
        job = jobs_mod.get_job(job_id) or {}
        self.assertEqual(job.get("status"), "cancelled")
        self.assertNotIn("前端点击前端点击", str(job.get("stop_note") or ""))


class LateCompletion(unittest.TestCase):
    def test_cancel_after_completed_is_ignored(self):
        job_id = _new_job(request_id="req-late1")
        self.assertTrue(jobs_mod.mark_completed_if_open(job_id))
        self.assertFalse(jobs_mod.request_cancel(job_id, "user"))
        self.assertEqual((jobs_mod.get_job(job_id) or {}).get("status"), "completed")

    def test_completion_after_cancel_request_is_recorded_as_completed(self):
        job_id = _new_job(request_id="req-late2")
        jobs_mod.request_cancel(job_id, "user")
        self.assertTrue(jobs_mod.mark_completed_if_open(job_id, note="完成早于停止生效"))
        job = jobs_mod.get_job(job_id) or {}
        self.assertEqual(job.get("status"), "completed")
        self.assertTrue(str(job.get("stop_note") or ""), "必须留下'停止晚于完成'的说明")


class CrossUserJobGuard(unittest.TestCase):
    """job 状态与会话任务列表不能跨用户读。"""

    def setUp(self):
        from src.api import main as api_main
        self.api = api_main
        self.calls = []

        # Must be synchronous: ``current_user`` is called without ``await``, so an
        # ``async def`` stub would hand the route a coroutine and every assertion
        # below would be decided by the ownership stub instead of the identity.
        def fake_user(request):
            return {"user_id": 2, "username": "other"}

        def deny(session_id, user):
            raise self.api.HTTPException(status_code=404, detail="session not found")

        self._patch = [
            mock.patch.object(api_main, "current_user", fake_user),
            mock.patch.object(api_main, "require_owned_session", deny),
        ]
        for p in self._patch:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self._patch])

    def _run(self, coro):
        import asyncio
        return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)

    def test_get_job_of_other_user_is_not_found(self):
        job_id = _new_job(session_id="sess-someone-else", request_id="req-x")
        with self.assertRaises(self.api.HTTPException) as ctx:
            self._run(self.api.get_job_status(job_id, object()))
        self.assertEqual(ctx.exception.status_code, 404)

    def test_list_jobs_of_other_session_is_not_found(self):
        with self.assertRaises(self.api.HTTPException) as ctx:
            self._run(self.api.list_session_jobs("sess-someone-else", object()))
        self.assertEqual(ctx.exception.status_code, 404)

    def test_identity_reaches_the_route(self):
        """Positive control: the same routes must work when the caller owns it.

        Without this, a route that denied every request would satisfy both tests
        above and the guard would look correct while being useless.
        """
        job = _new_job(session_id="sess-mine", request_id="req-mine")
        seen = {}

        def allow(session_id, user):
            seen["session_id"] = session_id
            seen["user_id"] = user["user_id"]

        with mock.patch.object(self.api, "require_owned_session", allow):
            got = self._run(self.api.get_job_status(job, object()))

        self.assertEqual(seen.get("user_id"), 2,
                         "the route must see the authenticated user, not a coroutine")
        self.assertEqual(seen.get("session_id"), "sess-mine")
        self.assertTrue(got)


if __name__ == "__main__":
    unittest.main(verbosity=1)
