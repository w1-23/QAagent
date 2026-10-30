# -*- coding: utf-8 -*-
"""S2 补：公开进度"运行期 vs 历史"的边界必须有测试钉住。

设计取舍（用户明确要求想清楚）：
* 完整进度序列 —— 只在运行期通过 SSE 发行，**不写库**：它包含模型的工作假设，
  数量按轮次增长，长期入库既违背"不是隐藏推理"的边界，也会让历史表膨胀；
* 最后一条进度 —— 写入 `task_jobs.progress_summary`：刷新/断线重连后，
  还能知道"上一轮停在哪一步"，这是用户实际会看的信息；
* 最终回答 —— 照旧写入消息历史，与进度完全分开。
"""
import unittest
from unittest import mock

from src.core import jobs as jobs_mod
from src.core import public_progress


class LastNotePersistence(unittest.TestCase):
    def setUp(self):
        public_progress.reset_emitted()

    def test_note_is_written_to_job_row(self):
        job_id = jobs_mod.create_job(session_id="sess-s2", request_id="req-s2", job_type="chat")
        with mock.patch("src.core.tracing.get_job_id", lambda: job_id):
            ok = public_progress.emit_public_progress("\u6b63\u5728\u6bd4\u5bf9\u4e24\u7bc7\u6587\u732e\u7684\u7ed3\u8bba")
        self.assertTrue(ok)
        row = jobs_mod.get_job(job_id) or {}
        self.assertIn("progress_summary", row)
        self.assertTrue(str(row.get("progress_summary") or ""))

    def test_long_note_is_truncated_not_rejected(self):
        job_id = jobs_mod.create_job(session_id="sess-s2b", request_id="req-s2b", job_type="chat")
        long_note = "\u6b63\u5728\u6bd4\u5bf9\u4e24\u7bc7\u6587\u732e\u7684\u7ed3\u8bba\u4e0e\u8bd5\u9a8c\u8bbe\u8ba1" * 30
        with mock.patch("src.core.tracing.get_job_id", lambda: job_id):
            public_progress._persist_last_note(long_note)
        row = jobs_mod.get_job(job_id) or {}
        self.assertLessEqual(len(str(row.get("progress_summary") or "")), public_progress._PROGRESS_SUMMARY_MAX)
        self.assertTrue(str(row.get("progress_summary") or ""))

    def test_persistence_failure_does_not_break_execution(self):
        with mock.patch("src.core.tracing.get_job_id", lambda: "job-does-not-exist"):
            self.assertTrue(public_progress.emit_public_progress("\u4e0b\u4e00\u6b65\u5148\u67e5\u75c5\u5bb3"))

    def test_no_job_bound_is_silent(self):
        with mock.patch("src.core.tracing.get_job_id", lambda: ""):
            self.assertTrue(public_progress.emit_public_progress("\u65e0\u4efb\u52a1\u7ed1\u5b9a"))

    def test_history_is_not_written_by_progress(self):
        """进度事件不应产生任何消息行（历史只由最终回答/工具行写）。"""
        from src.session import manager as session_manager
        job_id = jobs_mod.create_job(session_id="sess-s2c", request_id="req-s2c", job_type="chat")
        with mock.patch("src.core.tracing.get_job_id", lambda: job_id), \
             mock.patch.object(session_manager.SessionManager, "save_messages",
                               side_effect=AssertionError("progress wrote history")):
            self.assertTrue(public_progress.emit_public_progress("\u8bfb\u5b8c\u4e09\u4efd\u6587\u4ef6\u540e\u518d\u7ed9\u7ed3\u8bba"))


if __name__ == "__main__":
    unittest.main(verbosity=1)
