"""回归：合并丢失的模块级常量必须存在且真的生效。

背景（2026-10-29 排查）：`_DOI_PATTERN`、`_AUDIT_CATEGORIES` 与
`web_text_cleaner` 的 4 个常量在历次文件合并中丢了定义，引用点还在；
由于调用链都包着 `except Exception: 返回原值/跳过`，缺失时**不报错、只静默失效**：
参考文献提取恒为空、数据审查报告只有一句话、联网标记不被剥离。
本测试把「存在」和「生效」分开断言，避免再次回退成静默降级。
"""

from __future__ import annotations

from src.core import web_text_cleaner as cleaner
from src.research import tools as research_tools


def test_doi_pattern_exists_and_matches():
    pattern = getattr(research_tools, "_DOI_PATTERN", None)
    assert pattern is not None, "_DOI_PATTERN 定义丢失（引用点仍在，会静默失效）"
    assert pattern.search("doi: 10.1016/j.scienta.2023.112345").group(0) == \
        "10.1016/j.scienta.2023.112345"
    assert pattern.search("https://doi.org/10.1093/jxb/erab123").group(0).endswith("erab123")


def test_audit_categories_cover_all_checks():
    cats = getattr(research_tools, "_AUDIT_CATEGORIES", None)
    assert cats, "_AUDIT_CATEGORIES 定义丢失（审查报告退化为一句 summary）"
    checks = [c for _, group in cats for c in group]
    # 八类：缺失与格式 / 重复复用 / 异常值 / 行列关系 / 小数与末位 / 组别重复 / 整数自洽 / 常量列
    assert len(cats) >= 8, f"分类数不足：{len(cats)}"
    assert len(set(checks)) == len(checks), "同一 check 不得落在两个分类"
    for required in ("missing_values", "duplicate_row", "outlier", "empty_table"):
        assert required in checks, f"缺少检查项：{required}"


def test_web_cleaner_constants_are_effective():
    assert cleaner._WEB_MARKER is not None
    assert cleaner._URL is not None
    assert cleaner._URL_TAIL
    assert cleaner._WS_MAP is not None
    # [W1] 与 [W1-W3] 都要被剥掉
    assert cleaner.strip_web_markers("结论[W1]与[W2-W3]成立。") == "结论与成立。"
    # 全角/不可见字符归一
    assert "\u3000" not in cleaner.normalize_ws("a\u3000b")
    urls = cleaner.find_urls("见 https://doi.org/10.1/x，另 https://a.cn/b。")
    assert urls and all(u.startswith("http") for u in urls)
    assert not any(u.endswith(("，", "。")) for u in urls)


def test_reference_extraction_returns_dois():
    """端到端：给一段带 DOI 的文本，必须抽出 DOI（以前恒为空）。"""
    text = ("Chen C, et al. (2019). Huanglongbing. Scientia Horticulturae 250: 1-10. "
            "doi:10.1016/j.scienta.2019.01.001")
    extractor = getattr(research_tools, "_extract_dois", None)
    if extractor is None:
        # 名称可能不同，退化为直接用 pattern 扫描
        found = research_tools._DOI_PATTERN.findall(text)
    else:
        found = extractor(text)
    assert found, "带 DOI 的文本抽不出 DOI"
