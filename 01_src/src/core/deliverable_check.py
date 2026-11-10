# -*- coding: utf-8 -*-
"""发布闸门：生成物在「原子发布」之前必须通过内容完整性校验。

为什么需要这个模块
------------------
HTML 报告原先由 ``src/research/tools.py::_save_artifact`` 直接落盘。实测到的
截断并不发生在写文件这一步，而发生在**更早的生成阶段**：

* 模型把整份正文/整段 HTML 当作**工具参数**一次性输出；
* 撞到 completion token 预算后，参数在 JSON 里就是断的；
* ``_export_markdown`` 再用 ``<!doctype html>…</body></html>`` 把这个断掉的
  正文包起来 —— 于是产物**看起来**结构完整（``</html>`` 齐全、标签平衡），
  只有正文内容在中途停住；
* ``_save_artifact`` 当时**不做任何内容校验**，连 ``<html>ok</html>``（15 字节）
  都会被登记成「报告已生成」。

只读扫描 ``/home/WZQ`` 下 8 份已生成 html 的实测结果：

    size=0                 : 0
    缺 ``</html>``         : 0
    15 字节 stub           : 1   （text_chars=2，无任何标题）
    无标题（h1/h2 均为 0） : 1   （同一个 stub）
    遗留 ``.part`` 临时文件: 0

也就是说：磁盘层面的「写一半」已经被 ``.part`` + ``os.replace`` 挡住了，
``src/session/files.py`` 的登记路径也不是漏点；漏的是**内容层面**的
「写完了，但是断的」，以及重复提交时无条件新建第二份文件。

本模块因此只做三件事，且全部是确定性的、可单测的：

1. ``validate_deliverable`` / ``validate_html_document`` /
   ``validate_markdown_payload`` —— 按种类校验完整性，失败抛
   ``DeliverableRejected``（带机器可读的 ``code``，供调用方回报真实原因）。
2. ``RetryLedger`` —— 同一逻辑轮次的重试**硬上限** ``MAX_ATTEMPTS``，超限抛
   ``RetryLimitExceeded``，不再允许「模型自己反复修复」。
3. ``dedupe_key`` / ``logical_key`` —— 幂等键。发布去重键刻意**不含**
   ``client_request_id``，理由见 ``dedupe_key`` 的 docstring。

不碰网络、不碰 bpy、不写业务表；数据库路径由调用方注入，便于测试用 tmp_path。
"""
from __future__ import annotations

import hashlib
import html as _html
import re
import sqlite3
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict

__all__ = [
    "DeliverableRejected",
    "RetryLimitExceeded",
    "RetryLedger",
    "MAX_ATTEMPTS",
    "MIN_HTML_BYTES",
    "MIN_HTML_TEXT_CHARS",
    "dedupe_key",
    "logical_key",
    "validate_deliverable",
    "validate_html_document",
    "validate_markdown_payload",
    "validate_svg_document",
    "validate_text_deliverable",
]

# ---------------------------------------------------------------------------
# 阈值
# ---------------------------------------------------------------------------

#: 一份 html 交付物的最少可见文字数。只用来挡「只有标题/空壳」，
#: 不能用来要求「报告够长」——一条很短的结论也是合法交付物（82 字符的
#: 真实短报告被 120 的阈值误杀过），所以取一个低水位。
MIN_HTML_TEXT_CHARS = 40

#: 一份 html 报告的最少字节数（含外壳与样式）。
MIN_HTML_BYTES = 200

#: 同一逻辑轮次允许的生成/重试次数。第 ``MAX_ATTEMPTS + 1`` 次直接拒绝。
MAX_ATTEMPTS = 2

#: 正文类产物（markdown / txt）的最少字符数。
#: 只挡"占位/空壳"，不挡短内容——一条很短的课题记录也必须能导出。
MIN_PAYLOAD_CHARS = 20

_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}

_TAG_STRIP = re.compile(r"<[^>]+>")
_SCRIPT_STYLE = re.compile(r"<(script|style)\b[\s\S]*?</\1\s*>", re.I)
_WS = re.compile(r"\s+")
_HEADING = re.compile(r"<h[1-6]\b", re.I)


class DeliverableRejected(ValueError):
    """产物未通过完整性校验 —— 必须当作**失败**回报，不能登记为已生成。

    ``code`` 是机器可读原因（``empty_content`` / ``unbalanced_markup`` / …），
    调用方拿它拼用户可见的真实失败原因；``detail`` 是给人看的一句话。
    """

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = str(code or "rejected")
        self.detail = str(detail or "")
        message = "[DELIVERABLE_REJECTED:%s] %s" % (self.code, self.detail)
        super().__init__(message.strip())


class RetryLimitExceeded(DeliverableRejected):
    """同一逻辑轮次的自动重试已用完。"""

    def __init__(self, detail: str = "", attempts: int = MAX_ATTEMPTS) -> None:
        self.attempts = int(attempts)
        super().__init__("retry_limit", detail)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _decode(content: bytes) -> str:
    if content is None:
        raise DeliverableRejected("empty_content", "产物内容为空（None），未写入任何文件。")
    if not isinstance(content, (bytes, bytearray)):
        raise DeliverableRejected("bad_content_type", "产物内容必须是 bytes。")
    raw = bytes(content)
    if not raw:
        raise DeliverableRejected("empty_content", "产物内容为 0 字节，未写入任何文件。")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise DeliverableRejected(
                "bad_encoding", "产物不是合法 UTF-8，可能是写入中途被切断。") from None


def _visible_text(document: str) -> str:
    stripped = _SCRIPT_STYLE.sub(" ", document)
    stripped = _TAG_STRIP.sub(" ", stripped)
    return _WS.sub(" ", _html.unescape(stripped)).strip()


class _BalanceChecker(HTMLParser):
    """严格一点的标签平衡检查。

    ``html.parser`` 本身很宽容；这里要求栈为空，用来抓「外壳在、正文断在某个
    未闭合标签里」的情况。``convert_charrefs`` 打开以避免实体干扰。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.orphans: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: D102
        if tag not in _VOID_TAGS:
            self.stack.append(tag)

    def handle_startendtag(self, tag: str, attrs) -> None:  # noqa: D102
        return

    def handle_endtag(self, tag: str) -> None:  # noqa: D102
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        elif tag in self.stack:
            while self.stack and self.stack[-1] != tag:
                self.stack.pop()
            if self.stack:
                self.stack.pop()
        else:
            self.orphans.append(tag)


# ---------------------------------------------------------------------------
# 校验：HTML 文档
# ---------------------------------------------------------------------------


def validate_html_document(content: bytes, *, filename: str = "",
                           strict: bool = True) -> Dict[str, Any]:
    """校验一份**完整 HTML 文档**（含 ``<!doctype>``/``<html>`` 外壳）。

    两层，别混用：

    * ``strict=False``（结构底线）：非空、``<html>`` 根、``</body></html>``
      收尾、可解析、标签平衡。底层写入器 ``_save_artifact`` 用这一层：
      被截断/半截写入的文件绝对过不去，但"内容短"本身不算错。
    * ``strict=True``（交付契约）：再加上字节下限、可见正文下限、
      至少一个章节标题。报告类工具在发布前用这一层。

    返回统计字典（供日志/断言用），不通过则抛 ``DeliverableRejected``。
    """
    document = _decode(content)
    label = filename or "html"

    if strict and len(content) < MIN_HTML_BYTES:
        raise DeliverableRejected(
            "document_too_small",
            "%s 只有 %d 字节（低于 %d 字节下限），判断为占位符或被截断。"
            % (label, len(content), MIN_HTML_BYTES),
        )

    lowered = document.lower()
    if "<html" not in lowered:
        raise DeliverableRejected(
            "missing_document_root", "%s 没有 <html> 根节点，不是完整 HTML 文档。" % label)
    if "</body>" not in lowered:
        raise DeliverableRejected(
            "missing_document_end", "%s 缺少 </body>，文档尾部被截断。" % label)
    if not lowered.rstrip().endswith("</html>"):
        raise DeliverableRejected(
            "missing_document_end",
            "%s 没有以 </html> 收尾，文档尾部被截断（最后 40 字符：%r）。"
            % (label, document.rstrip()[-40:]),
        )

    checker = _BalanceChecker()
    try:
        checker.feed(document)
        checker.close()
    except Exception as exc:  # noqa: BLE001 - 解析器内部异常同样说明文档坏了
        raise DeliverableRejected(
            "unparsable_html", "%s 无法被 html.parser 解析：%s" % (label, exc)) from None
    if checker.stack:
        raise DeliverableRejected(
            "unbalanced_markup",
            "%s 有未闭合标签 %s，正文在标签中途截断。"
            % (label, checker.stack[:5]),
        )
    if checker.orphans:
        raise DeliverableRejected(
            "unbalanced_markup",
            "%s 有孤立的闭合标签 %s，标记结构被破坏（常见于正文被拼接/截断后重包）。"
            % (label, checker.orphans[:5]),
        )

    text = _visible_text(document)
    headings = len(_HEADING.findall(document))
    has_figure = bool(re.search(r"<svg\b", document, re.I))
    # 「只有图、没有正文」是合法导出（export_paper_analysis 的 svg_code 单独导出），
    # 所以有内嵌图形时只要求有标题文字；纯文字报告则必须有实质正文。
    min_text = 10 if has_figure else MIN_HTML_TEXT_CHARS
    if strict and len(text) < min_text:
        raise DeliverableRejected(
            "content_too_thin",
            "%s 可见正文只有 %d 字符（低于 %d），判断为空壳或截断残留（内容：%r）。"
            % (label, len(text), min_text, text[:60]),
        )

    if strict and headings == 0:
        raise DeliverableRejected(
            "missing_sections",
            "%s 没有任何 <h1>–<h6> 章节标题，报告结构缺失。" % label,
        )

    return {
        "kind": "html",
        "bytes": len(content),
        "text_chars": len(text),
        "headings": headings,
    }


# ---------------------------------------------------------------------------
# 校验：markdown / 纯文本正文（套 HTML 外壳之前）
# ---------------------------------------------------------------------------


def validate_markdown_payload(text: str, *, filename: str = "") -> Dict[str, Any]:
    """校验模型作为工具参数提交的**正文**，必须在套 HTML 外壳之前调用。

    外壳会把任何正文包成「结构完整」，所以对外壳校验抓不到正文截断；这里
    用截断在文本上留下的确定痕迹来判断：

    * 空 / 过短；
    * ``` 代码围栏数量为奇数（断在代码块里）；
    * ``<svg`` 比 ``</svg>`` 多（断在 SVG 里）；
    * ``<!--`` 没有配对的 ``-->``；
    * 既无标题、正文又短 —— 结构没形成。
    """
    body = str(text or "").strip()
    label = filename or "正文"

    if not body:
        raise DeliverableRejected("empty_payload", "%s 为空，没有可导出的内容。" % label)
    if len(body) < MIN_PAYLOAD_CHARS:
        raise DeliverableRejected(
            "payload_too_short",
            "%s 只有 %d 字符（低于 %d），判断为截断或占位内容：%r"
            % (label, len(body), MIN_PAYLOAD_CHARS, body[:60]),
        )

    fences = body.count("```")
    if fences % 2:
        raise DeliverableRejected(
            "unbalanced_code_fence",
            "%s 有 %d 个 ``` 围栏（奇数），正文断在代码块内部。" % (label, fences),
        )

    opened_svg = len(re.findall(r"<svg\b", body, re.I))
    closed_svg = len(re.findall(r"</svg\s*>", body, re.I))
    if opened_svg > closed_svg:
        raise DeliverableRejected(
            "unterminated_svg",
            "%s 有 %d 个 <svg> 但只有 %d 个 </svg>，SVG 参数被截断。"
            % (label, opened_svg, closed_svg),
        )

    if body.count("<!--") > body.count("-->"):
        raise DeliverableRejected(
            "unterminated_comment", "%s 有未闭合的 HTML 注释，正文被截断。" % label)

    headings = len(re.findall(r"^\s{0,3}#{1,6}\s+\S", body, re.M))
    if headings == 0 and len(body) < 200:
        raise DeliverableRejected(
            "missing_sections",
            "%s 既没有 Markdown 标题、正文又只有 %d 字符，报告结构没有形成。"
            % (label, len(body)),
        )

    return {
        "kind": "markdown",
        "chars": len(body),
        "headings": headings,
        "fences": fences,
    }


# ---------------------------------------------------------------------------
# 校验：SVG / 纯文本 / 二进制
# ---------------------------------------------------------------------------


def validate_svg_document(content: bytes, *, filename: str = "") -> Dict[str, Any]:
    """校验独立 SVG 文件：``<svg``/``</svg>`` 成对、非空。"""
    document = _decode(content)
    label = filename or "svg"
    if len(content) < 32:
        raise DeliverableRejected(
            "document_too_small", "%s 只有 %d 字节，判断为占位或截断。" % (label, len(content)))
    opened = len(re.findall(r"<svg\b", document, re.I))
    closed = len(re.findall(r"</svg\s*>", document, re.I))
    if opened == 0:
        raise DeliverableRejected("missing_document_root", "%s 没有 <svg> 根节点。" % label)
    if opened != closed:
        raise DeliverableRejected(
            "unbalanced_markup",
            "%s 有 %d 个 <svg> 但只有 %d 个 </svg>，SVG 被截断。"
            % (label, opened, closed),
        )
    return {"kind": "svg", "bytes": len(content), "roots": opened}


def validate_text_deliverable(content: bytes, *, filename: str = "") -> Dict[str, Any]:
    """校验文本类产物（csv/tsv/ris/bib/json/txt/md）：非空且可解码。"""
    document = _decode(content)
    label = filename or "text"
    if not document.strip():
        raise DeliverableRejected("empty_content", "%s 只有空白，未写入文件。" % label)
    return {"kind": "text", "bytes": len(content)}


def validate_binary_deliverable(content: bytes, *, filename: str = "") -> Dict[str, Any]:
    """校验二进制产物（xlsx/gz/zip/png…）：至少非空。

    二进制格式的完整性强校验需要按格式解析，超出本缺陷范围；这里只保证
    「0 字节/None 不得发布」这一条底线，避免把空壳当成成功。
    """
    if not content:
        raise DeliverableRejected(
            "empty_content", "%s 内容为 0 字节，未写入文件。" % (filename or "binary"))
    return {"kind": "binary", "bytes": len(content)}


_TEXT_EXTS = {".csv", ".tsv", ".txt", ".md", ".json", ".bib", ".ris", ".xml"}
_HTML_EXTS = {".html", ".htm"}
_SVG_EXTS = {".svg"}


def validate_deliverable(*, filename: str, content: bytes,
                         mime: str = "", strict: bool = True) -> Dict[str, Any]:
    """按文件名/MIME 分派到具体校验器。失败抛 ``DeliverableRejected``。

    ``strict`` 只对 HTML 生效（结构底线 vs 交付契约），语义见
    :func:`validate_html_document`。
    """
    suffix = Path(str(filename or "")).suffix.lower()
    mime_l = str(mime or "").lower()
    if suffix in _HTML_EXTS or "text/html" in mime_l:
        return validate_html_document(content, filename=str(filename or ""),
                                      strict=strict)
    if suffix in _SVG_EXTS or "image/svg" in mime_l:
        return validate_svg_document(content, filename=str(filename or ""))
    if suffix in _TEXT_EXTS or mime_l.startswith("text/"):
        return validate_text_deliverable(content, filename=str(filename or ""))
    return validate_binary_deliverable(content, filename=str(filename or ""))


# ---------------------------------------------------------------------------
# 幂等键
# ---------------------------------------------------------------------------


def dedupe_key(*, session_id: str, tool_name: str, filename: str,
               source_key: str = "", source_sha256: str = "",
               output_sha256: str = "") -> str:
    """发布去重键：（会话 + 工具 + 目标文件名）→ 一个逻辑报告身份。

    刻意**不含** ``client_request_id``。实测 ``session_files`` 里同名的
    两份 ``..._标注.xlsx`` 带着两个**不同**的 crid（``67fd4cd3…`` /
    ``cba6b052…`` / ``54bb5549…`` / ``a8056596…``）—— 自动重试在框架层是
    新的一次请求，把 crid 放进键里正是「重试后多出一份文件」的成因。

    因此同一会话内、同一工具、同一目标文件名视为同一份产物：重复提交时
    原地替换字节并复用 ``artifact_id``（旧聊天链接继续有效），而不是新建
    第二份文件。
    """
    seed = "|".join([
        str(session_id or ""), str(tool_name or ""), str(filename or ""),
        str(source_key or ""), str(source_sha256 or ""),
        str(output_sha256 or ""),
    ])
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def logical_key(*, session_id: str, request_id: str, tool_name: str,
                filename: str) -> str:
    """重试预算键：关联**原逻辑轮次**（含 crid），每次请求独立计 2 次。"""
    seed = "|".join([
        str(session_id or ""), str(request_id or ""),
        str(tool_name or ""), str(filename or ""),
    ])
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 重试预算
# ---------------------------------------------------------------------------


class RetryLedger:
    """按逻辑键记录生成尝试次数，硬上限 ``max_attempts``。

    存 sqlite 而不是进程内存：自动重试可能是另一次请求（进程内缓存会失忆，
    于是又变回无限修复）。表结构自建，不与业务表混用。
    """

    def __init__(self, db_path: str | Path, max_attempts: int = MAX_ATTEMPTS) -> None:
        self.path = Path(db_path)
        self.max_attempts = max(1, int(max_attempts))

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path))
        conn.execute(
            """CREATE TABLE IF NOT EXISTS deliverable_attempts (
                   logical_key TEXT PRIMARY KEY,
                   attempts    INTEGER NOT NULL,
                   first_at    REAL NOT NULL,
                   last_at     REAL NOT NULL)"""
        )
        return conn

    def attempts(self, key: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT attempts FROM deliverable_attempts WHERE logical_key=?",
                (str(key),)).fetchone()
        return int(row[0]) if row else 0

    def begin(self, key: str) -> int:
        """登记一次尝试；超过上限抛 ``RetryLimitExceeded``（不累加）。"""
        key = str(key)
        now = time.time()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT attempts FROM deliverable_attempts WHERE logical_key=?",
                (key,)).fetchone()
            used = int(row[0]) if row else 0
            if used >= self.max_attempts:
                raise RetryLimitExceeded(
                    "该逻辑轮次已生成/重试 %d 次（上限 %d 次），本轮不再重试。"
                    "请改成分章节生成后由模板组装，而不是继续整份重出。"
                    % (used, self.max_attempts),
                    attempts=used,
                )
            attempts = used + 1
            if row:
                conn.execute(
                    "UPDATE deliverable_attempts SET attempts=?, last_at=? WHERE logical_key=?",
                    (attempts, now, key))
            else:
                conn.execute(
                    "INSERT INTO deliverable_attempts(logical_key,attempts,first_at,last_at)"
                    " VALUES(?,?,?,?)", (key, attempts, now, now))
        return attempts

    def clear(self, key: str) -> None:
        """成功发布后清账：下一次独立的用户请求重新计数。"""
        with self._connect() as conn:
            conn.execute("DELETE FROM deliverable_attempts WHERE logical_key=?", (str(key),))
