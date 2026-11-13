# -*- coding: utf-8 -*-
"""联网来源标题清洗（v10.6 架构决策的**保留部分**）。

背景——**为什么不再混合**
------------------------
五组对照实验（Pool C 48 题/臂，逐题配对，MDD = t·SD/√n）：

* 联网证据混入本地证据池会**显著提高幻觉率**：B（联网未过滤）vs A（不联网）
  **+20.8pp**，MDD 0.078，显著；
* 过滤管线**无法可靠挽回**：最好的混合臂 C′（模型可见级过滤）仍比 A 高
  **+15.6pp**（MDD 0.086，显著）；
* 阈值标定显示**过滤无效**：余弦 AUC 0.581、精排 AUC 0.544，各候选阈值误杀
  被引证据的比例**恒在 ~30%**；
* 内容门在筛**抓取可达性**，不是在判来源质量：被剔除的 547 条里 **545 条（99.6%）
  是"抓不到正文"**。
  ⚠️ **原稿此处曾写「内容门优先砍掉最权威来源：被剔除的 545 条中 T1+T2 占 49%，
  而存活集合里只占 33%」——该结论已被复核推翻，勿再引用。** 实测（v10.17 复核）：
  存活集（n=111）T1+T2 占 **52.3%**，被剔集（n=545）占 **49.2%** —— **存活集的
  高权威来源占比反而略高**，数据不支持"优先砍最权威来源"。逐级剔除率 T1 83.4% /
  T2 67.4% / T3 83.2%，**T1 与 T3 几乎相同**。出处：审计报告 30 §1.1；
  tier 分布原始数据 `reports/_data/webpipe_thresholds.json`
  （`tier_dist_kept` / `tier_dist_unverifiable`）。

决策——**分离而非仲裁**
----------------------
本地答案带完整引用 ``[n]``（证据池不变）；联网信息**净化后单独输出**（列 URL、
标来源分级），追加在本地答案之后，**由用户自行判断**可信度。

⚠️ **口径纪律（写进论文/PPT 时不得越界）**
--------------------------------------------
这是**工程决策**，不是实验结论。实验**否定的是"混合"**；"分离更好"是**逻辑推论**，
**未经实验验证**。因此：

* ❌ 不得写"分离方案经实验验证"
* ❌ 不得写"分离降低幻觉率"
* ❌ 不得写"分离是实验结论"
* ✅ 只能写"**混合已被实验否定，分离是逻辑推论**"

本模块现在只剩**两件**还有调用方的事：

* ``clean_web_title()``：把搜索/抓取返回的页面标题清洗成一条可展示的标题
  （去导航残片、去内嵌 URL、缺标题时用 DOI / URL slug / 主机名兜底）；
* ``SEPARATION.enabled``：跨轮历史是否排除联网条目的开关
  （``src/session/manager.py`` 读取）。

分离小节的装配（``build_web_section`` / ``append_web_section`` / 分级标签 /
模型侧说明）随统一 Agent 链路下线：联网内容现在由 Supervisor 自行提炼，
本模块不再拼装第二套正文。
"""
from __future__ import annotations

import os
import re
import html
import unicodedata
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

from src.core.web_text_cleaner import strip_web_markers

__all__ = [
    "SEPARATION", "SeparationConfig", "clean_web_title", "strip_web_markers",
]

_TRUE = {"1", "true", "yes", "on"}
_TITLE_TAG_RE = re.compile(r"<[^>]{1,160}>")
_TITLE_SEGMENT_RE = re.compile(r"\s+(?:\||•|·|—|–|-)\s+")
_NAVIGATION_ONLY_RE = re.compile(
    r"^(?:"
    r"skip\s+to\s+(?:main\s+)?(?:content|article)|"
    r"(?:main\s+)?menu|navigation|(?:site\s+)?search|filters?|"
    r"(?:sign|log)\s*in|register|subscribe|share|export(?:ar|ieren)?|"
    r"\d+\s+(?:results?|publications?|articles?)|"
    r"首页|网站首页|菜单|导航|筛选|搜索|登录|注册|订阅|分享|导出|"
    r"\d+\s*(?:条|个)?(?:结果|文章|出版物)|"
    r"(?:экспортировать|экспорт|експорт|публикаци[йи]|публікацій|стат[еи]й|"
    r"фільтри|фильтры|поиск|пошук)"
    r"|\d+\s+(?:публикаци[йи]|публікацій|стат[еи]й)\s*"
    r"(?:экспортировать|экспорт|експорт|фільтри|фильтры)?"
    r")\s*$",
    re.I,
)
_NAVIGATION_INLINE_RE = re.compile(
    r"\b(?:skip\s+to\s+(?:main\s+)?(?:content|article|main\s+menu|article\s+page\s+content))\b|"
    r"\b(?:main\s+)?(?:navigation\s+menu|menu)\b|"
    r"\b(?:search\s+search|search|filters)\b|"
    r"(?:首页|网站首页|菜单|导航|筛选|搜索)",
    re.I,
)
_SLUG_EXTENSION_RE = re.compile(
    r"\.(?:html?|php|aspx?|jsp|pdf|xml|json)$", re.I
)
_SLUG_DIGITS_ONLY_RE = re.compile(r"^[\d_-]+$")
_GENERIC_WEB_TITLE_RE = re.compile(
    r"^(?:"
    r"ouci|nstl(?:\s+.*)?|text|pdf|html|frontiers?|"
    r"home(?:page)?|search(?:\s+results?)?|results?|filters?|"
    r"увати|експортувати|публікації|фільтри|"
    r"(?:main\s+)?menu|navigation|article(?:\s+page)?|"
    r"文献|外文文献\s+中文文献|作者作品|"
    r"(?:由\s+.+\s+所著的作品)|"
    r"[\w\u4e00-\u9fff .-]{0,24}(?:期刊|数据库|文献中心)$"
    r")$",
    re.I,
)
_URL_DOI_RE = re.compile(r"(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)", re.I)
_NAVIGATION_PREFIX_RE = re.compile(
    r"^\s*(?:skip\s+to\s+(?:main\s+)?(?:content|article|page\s*content)"
    r"|skip\s+to\s+main\s+menu"
    r"|article\s+page\s+content"
    r"|page\s+content"
    r"|\bouci\b|\bsearch\b|\bfilters\b|"
    r"(?:main\s+)?menu\b|"
    r"首页|网站首页|菜单|导航|筛选|搜索|"
    r"экспортировать|экспорт|експорт|публикаци[йи]|публікацій|"
    r"фільтри|фильтры|поиск|пошук)",
    re.I,
)
_TRIM_CHARS = " \t-|•·—–:："


def _tidy_web_title(value: object) -> str:
    text = html.unescape(str(value or ""))
    text = _TITLE_TAG_RE.sub(" ", text)
    text = unicodedata.normalize("NFKC", text)
    text = "".join(
        " " if unicodedata.category(ch).startswith("C") else ch
        for ch in text
    )
    text = _NAVIGATION_INLINE_RE.sub(" ", text)
    return " ".join(text.replace("\u00a0", " ").split())


def _readable_url_slug(url: str) -> str:
    """Return a factual, human-readable URL slug when page title is chrome."""
    try:
        path = urlsplit(str(url or "")).path
    except Exception:
        return ""
    parts = [unquote(part) for part in path.split("/") if part]
    for raw in reversed(parts):
        slug = _SLUG_EXTENSION_RE.sub("", raw).strip()
        if not slug or _SLUG_DIGITS_ONLY_RE.match(slug):
            continue
        words = [part for part in re.split(r"[-_+]+", slug) if part]
        if not words or not re.search(r"[A-Za-z\u4e00-\u9fff]{3}", slug):
            continue
        readable = " ".join(words).strip()
        if _GENERIC_WEB_TITLE_RE.match(readable):
            continue
        if 3 <= len(readable) <= 160:
            return readable
    return ""


def _doi_from_url(url: str) -> str:
    """Return a DOI already present in the URL, without guessing a title."""
    try:
        decoded = unquote(str(url or ""))
    except Exception:
        decoded = str(url or "")
    match = _URL_DOI_RE.search(decoded)
    if not match:
        return ""
    doi = match.group(1).rstrip(".,;:)]}")
    return re.sub(r"/(?:text|full|fulltext|html|pdf|abstract)$", "", doi, flags=re.I)


def _strip_site_wrapper(segment: str, site: str) -> str:
    text = str(segment or "").strip(" \t-|•·—–")
    if site:
        text = re.sub(
            r"^\s*" + re.escape(site) + r"(?:\s*[-|•·—–]\s*|\s+|$)",
            "", text, flags=re.I,
        ).strip(" \t-|•·—–")
    return text


def _strip_navigation_residue(segment: str) -> str:
    text = str(segment or "")
    text = _NAVIGATION_INLINE_RE.sub(" ", text)
    # Only trim residue when the whole segment *starts* with chrome. Real
    # titles that merely contain a word like "search" stay untouched.
    while text and _NAVIGATION_PREFIX_RE.match(text):
        text = _NAVIGATION_PREFIX_RE.sub("", text, count=1).strip(_TRIM_CHARS)
    return text.strip(_TRIM_CHARS)


def clean_web_title(value: object, url: str = "", site_name: object = "") -> str:
    """Return one display-safe page title.

    Search providers sometimes prepend accessibility/navigation text or append a
    menu label to the real title. Keep the first meaningful title segment, then
    fall back to the declared site name and finally the hostname. Hyperlinks and
    source identity come from ``url``; this helper never invents one.
    """
    from src.core.web_text_cleaner import host_of

    raw = _tidy_web_title(value)
    site = _tidy_web_title(site_name)
    if site and _NAVIGATION_ONLY_RE.match(site):
        site = ""
    segments = [part.strip(" \t-|•·—–") for part in _TITLE_SEGMENT_RE.split(raw)]
    candidates: list[str] = []
    for segment in segments or [raw]:
        if not segment or _NAVIGATION_ONLY_RE.match(segment):
            continue
        segment = _strip_site_wrapper(segment, site)
        segment = _NAVIGATION_INLINE_RE.sub(" ", segment)
        segment = _strip_navigation_residue(segment)
        segment = " ".join(segment.split()).strip(" \t-|•·—–")
        if (len(segment) >= 3
                and not _NAVIGATION_ONLY_RE.match(segment)
                and (not site or segment.casefold() != site.casefold())):
            candidates.append(segment)
    title = candidates[0] if candidates else ""
    if title and site:
        suffix = re.compile(r"\s+(?:\||•|·|—|–| - )\s*" + re.escape(site) + r"\s*$", re.I)
        title = suffix.sub("", title).strip()
    if title and _GENERIC_WEB_TITLE_RE.match(title):
        title = ""
    doi = _doi_from_url(str(url or ""))
    title = (
        title
        or (f"DOI {doi}" if doi else "")
        or _readable_url_slug(str(url or ""))
        or host_of(str(url or ""))
        or site
        or "联网来源"
    )
    return title[:180]


def _env(name: str, default: str) -> str:
    try:
        return os.environ.get(name, default)
    except Exception:                                          # noqa: BLE001
        return default


@dataclass
class SeparationConfig:
    """分离输出配置（env 驱动；``refresh()`` 供测试与运行期改开关）。

    只有 ``enabled`` 还有读者（跨轮历史是否排除联网条目）；其余字段随分离
    小节的下线一并移除。
    """

    enabled: bool = True

    @classmethod
    def from_env(cls) -> "SeparationConfig":
        return cls(
            enabled=_env("CITRUS_SEPARATE_WEB_OUTPUT", "1").strip().lower() in _TRUE,
        )

    def refresh(self) -> "SeparationConfig":
        """就地刷新（测试改 env 后调用；返回 self 便于链式使用）。"""
        new = SeparationConfig.from_env()
        for k, v in vars(new).items():
            setattr(self, k, v)
        return self


#: 进程级单例（与 WEB_PIPELINE 同风格）
SEPARATION = SeparationConfig.from_env()
