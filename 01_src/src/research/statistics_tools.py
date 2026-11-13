"""Research statistics computed from the user's own table.

What this tool is for
---------------------
Turning a spreadsheet the user uploaded into numbers they can rely on: how
many observations there are, how much they vary, and — when the design
actually supports it — whether a difference between groups is unlikely to be
noise.

What this tool will not do
--------------------------
Invent a result. Every number in the output comes from a computation over the
user's rows. When a test does not apply — too few observations, a pairing
that does not line up, a variable that is not the kind of variable the test
needs — the tool says so and explains why, and returns no probability value.
A tool that produced a p-value for every input would be indistinguishable
from one that made the value up.

The assumptions behind each test are checked and reported, because a test
result without its assumptions is not a result: normality is assessed before
a parametric test, homogeneity of variance before a pooled-variance
comparison, and the pairing key before a paired test.

Reuse
-----
The table is read with the existing ``_load_frame`` helper, which already
enforces the service's spreadsheet safety budgets, and any exported table is
written with the existing ``_save_artifact`` helper, which publishes it to
the account library. This module adds the statistics, not a second
spreadsheet reader.
"""
from __future__ import annotations

import json
import math
from typing import Any

from langchain_core.tools import tool

from src.core.tool_errors import ToolArgumentError

# The tests this tool can run. Each entry names the design it suits.
TESTS = {
    "ttest_ind": "两组独立样本 t 检验",
    "ttest_rel": "配对样本 t 检验",
    "anova": "单因素方差分析（3 组及以上）",
    "kruskal": "Kruskal-Wallis 秩和检验（非参数）",
    "mannwhitneyu": "Mann-Whitney U 检验（非参数）",
    "pearson": "Pearson 相关",
    "spearman": "Spearman 秩相关（非参数）",
    "chi2": "卡方独立性检验",
}

_MAX_GROUP_LEVELS = 50


def _pd():
    import pandas as pd
    return pd


def _np():
    import numpy as np
    return np


def _json_list(raw: Any, *, name: str) -> list[str]:
    """Parse a JSON list argument into a list of non-empty strings."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            raise ToolArgumentError(
                f"{name} 需要 JSON 数组，例如 [\"处理\",\"时间\"]。") from None
    if not isinstance(raw, (list, tuple)):
        raise ToolArgumentError(f"{name} 需要 JSON 数组，例如 [\"处理\",\"时间\"]。")
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise ToolArgumentError(f"{name} 的每一项必须是非空列名文本。")
        out.append(item.strip())
    return out


def _numeric(series: Any) -> Any:
    """Coerce a column to numbers, leaving non-numeric rows as missing."""
    pd = _pd()
    if pd.api.types.is_numeric_dtype(series):
        return series.astype(float)
    converted = pd.to_numeric(series, errors="coerce")
    return converted


def _describe(series: Any) -> dict[str, Any]:
    """Descriptive statistics for one column."""
    pd = _pd()
    values = _numeric(series).dropna()
    count = int(values.shape[0])
    if count == 0:
        return {"n": 0, "missing": int(series.shape[0])}
    return {
        "n": count,
        "missing": int(series.shape[0]) - count,
        "missing_rate": round((int(series.shape[0]) - count) / max(int(series.shape[0]), 1), 4),
        "mean": round(float(values.mean()), 6),
        "sd": round(float(values.std(ddof=1)), 6) if count > 1 else None,
        "se": round(float(values.std(ddof=1) / math.sqrt(count)), 6) if count > 1 else None,
        "median": round(float(values.median()), 6),
        "q1": round(float(values.quantile(0.25)), 6),
        "q3": round(float(values.quantile(0.75)), 6),
        "min": round(float(values.min()), 6),
        "max": round(float(values.max()), 6),
    }


def _assumption_normality(values: Any) -> dict[str, Any]:
    """Assess normality of one sample.

    Shapiro-Wilk for samples up to a few thousand rows; beyond that the
    test's own limits make it uninformative, so the skew/kurtosis criterion
    is used instead.
    """
    pd = _pd()
    scipy_stats = _scipy_stats()
    count = int(values.shape[0])
    if count < 3:
        return {"checked": False, "reason": "样本量不足 3，无法检验正态性。"}
    if count <= 5000:
        try:
            statistic, pvalue = scipy_stats.shapiro(values.to_numpy())
            return {
                "checked": True, "test": "Shapiro-Wilk",
                "statistic": round(float(statistic), 6),
                "p": round(float(pvalue), 6),
                "normal_at_alpha": bool(pvalue > 0.05),
            }
        except Exception as exc:  # pragma: no cover - scipy edge cases
            return {"checked": False, "reason": f"正态性检验未能完成：{exc}"}
    skew = float(values.skew())
    kurtosis = float(values.kurtosis())
    normal = abs(skew) < 1.0 and abs(kurtosis) < 3.0
    return {
        "checked": True, "test": "偏度-峰度判据（样本量 > 5000）",
        "skew": round(skew, 4), "kurtosis": round(kurtosis, 4),
        "normal_at_alpha": bool(normal),
    }


def _scipy_stats():
    from scipy import stats
    return stats


def _assumption_homoscedasticity(groups: list[Any]) -> dict[str, Any]:
    """Levene's test for equal variances across groups."""
    scipy_stats = _scipy_stats()
    usable = [group.to_numpy() for group in groups if group.shape[0] >= 2]
    if len(usable) < 2:
        return {"checked": False, "reason": "少于两组有足够样本，无法检验方差齐性。"}
    try:
        statistic, pvalue = scipy_stats.levene(*usable)
        return {
            "checked": True, "test": "Levene",
            "statistic": round(float(statistic), 6),
            "p": round(float(pvalue), 6),
            "equal_variances": bool(pvalue > 0.05),
        }
    except Exception as exc:  # pragma: no cover
        return {"checked": False, "reason": f"方差齐性检验未能完成：{exc}"}


def _holm(pvalues: list[float]) -> list[float]:
    """Holm-Bonferroni adjusted p-values, kept monotonic."""
    count = len(pvalues)
    if count == 0:
        return []
    order = sorted(range(count), key=lambda i: pvalues[i])
    adjusted = [0.0] * count
    running = 0.0
    for rank, index in enumerate(order):
        value = min(1.0, (count - rank) * pvalues[index])
        running = max(running, value)
        adjusted[index] = running
    return adjusted


def _not_applicable(reason: str, **extra: Any) -> dict[str, Any]:
    payload = {"applicable": False, "reason": reason}
    payload.update(extra)
    return payload


# ── Effect sizes ──────────────────────────────────────────────────────────
#
# A probability value says how surprising a result is; an effect size says
# how big the difference actually is. A difference can be highly significant
# in a large sample and still be practically negligible, so the two are
# reported together and never one alone.

def _cohens_d(a: Any, b: Any) -> float | None:
    """Cohen's d with the pooled standard deviation."""
    na, nb = int(a.shape[0]), int(b.shape[0])
    if na < 2 or nb < 2:
        return None
    va = float(a.astype(float).var(ddof=1))
    vb = float(b.astype(float).var(ddof=1))
    pooled = ((na - 1) * va + (nb - 1) * vb) / (na + nb - 2)
    if pooled <= 0:
        return None
    return round(float(a.astype(float).mean() - b.astype(float).mean())
                 / math.sqrt(pooled), 6)


def _hedges_g(a: Any, b: Any) -> float | None:
    """Hedges' g — Cohen's d with the small-sample bias correction."""
    na, nb = int(a.shape[0]), int(b.shape[0])
    value = _cohens_d(a, b)
    if value is None:
        return None
    correction = 1.0 - 3.0 / (4.0 * (na + nb) - 9.0)
    return round(value * correction, 6)


def _cliffs_delta(a: Any, b: Any) -> float | None:
    """Cliff's delta — the non-parametric effect size, in [-1, 1]."""
    if a.shape[0] < 1 or b.shape[0] < 1:
        return None
    left = a.astype(float).to_numpy()
    right = b.astype(float).to_numpy()
    greater = 0
    less = 0
    for value in left:
        greater += int((right > value).sum())
        less += int((right < value).sum())
    total = len(left) * len(right)
    if not total:
        return None
    return round((greater - less) / total, 6)


def _eta_squared(groups: list[Any]) -> float | None:
    """Eta squared — the share of variance the grouping explains."""
    sizes = [int(group.shape[0]) for group in groups]
    if sum(sizes) < 2 or any(size < 1 for size in sizes):
        return None
    grand = float(sum(float(group.astype(float).sum()) for group in groups))
    grand_mean = grand / sum(sizes)
    between = 0.0
    within = 0.0
    for group in groups:
        values = group.astype(float).to_numpy()
        mean = float(values.mean())
        between += len(values) * (mean - grand_mean) ** 2
        within += float(((values - mean) ** 2).sum())
    total = between + within
    if total <= 0:
        return None
    return round(between / total, 6)


def _epsilon_squared(groups: list[Any]) -> float | None:
    """Epsilon squared — the rank-based counterpart of eta squared."""
    sizes = [int(group.shape[0]) for group in groups]
    if sum(sizes) < 2:
        return None
    try:
        from scipy import stats as scipy_stats
        statistic, _ = scipy_stats.kruskal(*[g.astype(float).to_numpy() for g in groups])
        total = sum(sizes)
        return round(float(statistic) / (total - 1), 6)
    except Exception:
        return None


def _cohens_dz(differences: Any) -> float | None:
    """Cohen's d_z — the standardised mean of paired differences."""
    if int(differences.shape[0]) < 2:
        return None
    sd = float(differences.astype(float).std(ddof=1))
    if sd <= 0:
        return None
    return round(float(differences.astype(float).mean()) / sd, 6)


# ── How to read an effect size ────────────────────────────────────────────
#
# The thresholds below are the conventional ones. They are given so the
# reader does not have to remember them, and they are given as a description
# of magnitude, never as a verdict: the design and the field decide what
# counts as meaningful, and the tool does not get to decide that.

def _interpret_d(value: float | None) -> str:
    if value is None:
        return ""
    magnitude = abs(value)
    if magnitude < 0.2:
        label = "很小"
    elif magnitude < 0.5:
        label = "小"
    elif magnitude < 0.8:
        label = "中等"
    else:
        label = "大"
    return f"{label}（|d| = {magnitude:.2f}）"


def _interpret_delta(value: float | None) -> str:
    if value is None:
        return ""
    magnitude = abs(value)
    if magnitude < 0.147:
        label = "很小"
    elif magnitude < 0.33:
        label = "小"
    elif magnitude < 0.474:
        label = "中等"
    else:
        label = "大"
    return f"{label}（|δ| = {magnitude:.2f}）"


def _interpret_eta(value: float | None) -> str:
    if value is None:
        return ""
    if value < 0.06:
        label = "很小"
    elif value < 0.14:
        label = "小"
    elif value < 0.21:
        label = "中等"
    else:
        label = "大"
    return f"{label}（η² = {value:.3f}）"


def _interpret_r(value: float) -> str:
    magnitude = abs(value)
    if magnitude < 0.1:
        label = "几乎无相关"
    elif magnitude < 0.3:
        label = "弱相关"
    elif magnitude < 0.5:
        label = "中等相关"
    else:
        label = "强相关"
    return f"{label}（r = {value:.3f}）"


def _interpret_v(value: float | None) -> str:
    if value is None:
        return ""
    if value < 0.1:
        label = "很弱"
    elif value < 0.3:
        label = "弱"
    elif value < 0.5:
        label = "中等"
    else:
        label = "强"
    return f"{label}（V = {value:.3f}）"


def _cramers_v(table: Any) -> float | None:
    """Cramér's V — the strength of a categorical association."""
    import numpy as np
    try:
        values = table.to_numpy(dtype=float)
        total = float(values.sum())
        if total <= 0:
            return None
        row = values.sum(axis=1, keepdims=True)
        column = values.sum(axis=0, keepdims=True)
        expected = (row / total) * column
        if (expected <= 0).any():
            return None
        chi2 = float((((values - expected) ** 2) / expected).sum())
        minimum = min(values.shape) - 1
        if minimum <= 0:
            return None
        return round(math.sqrt(chi2 / (total * minimum)), 6)
    except Exception:
        return None


def _pairwise(groups: dict[str, Any], alpha: float, parametric: bool) -> list[dict[str, Any]]:
    """Compare every pair of groups, with Holm correction.

    An omnibus test says whether *some* difference exists; it does not say
    where. The pairwise table with a family-wise correction answers that, and
    the correction is what keeps a dozen comparisons from manufacturing a
    false positive.
    """
    scipy_stats = _scipy_stats()
    names = list(groups)
    pairs: list[dict[str, Any]] = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            first = groups[names[i]]
            second = groups[names[j]]
            if first.shape[0] < 2 or second.shape[0] < 2:
                pairs.append({"groups": [names[i], names[j]],
                              "applicable": False,
                              "reason": "至少一组观测值少于 2。"})
                continue
            try:
                if parametric:
                    statistic, pvalue = scipy_stats.ttest_ind(
                        first.astype(float).to_numpy(),
                        second.astype(float).to_numpy(), equal_var=False)
                    method = "Welch t 检验"
                    effect = _hedges_g(first, second)
                    effect_name = "hedges_g"
                else:
                    statistic, pvalue = scipy_stats.mannwhitneyu(
                        first.astype(float).to_numpy(),
                        second.astype(float).to_numpy(), alternative="two-sided")
                    method = "Mann-Whitney U 检验"
                    effect = _cliffs_delta(first, second)
                    effect_name = "cliffs_delta"
            except Exception as exc:
                pairs.append({"groups": [names[i], names[j]],
                              "applicable": False,
                              "reason": f"比较未能完成：{exc}"})
                continue
            pairs.append({
                "groups": [names[i], names[j]],
                "applicable": True,
                "test": method,
                "statistic": round(float(statistic), 6),
                "p": round(float(pvalue), 6),
                effect_name: effect,
            })
    usable = [item for item in pairs if item.get("applicable")]
    if len(usable) > 1:
        adjusted = _holm([float(item["p"]) for item in usable])
        for item, value in zip(usable, adjusted):
            item["p_holm"] = round(value, 6)
            item["significant"] = bool(value < alpha)
    for item in usable:
        if len(usable) <= 1:
            item["p_holm"] = item["p"]
            item["significant"] = bool(float(item["p"]) < alpha)
    return pairs


def _independent_two_sample(a: Any, b: Any, alpha: float,
                            test: str) -> dict[str, Any]:
    """Compare two independent groups."""
    scipy_stats = _scipy_stats()
    if test == "mannwhitneyu":
        if a.shape[0] < 2 or b.shape[0] < 2:
            return _not_applicable("每组至少需要 2 个观测值。")
        try:
            statistic, pvalue = scipy_stats.mannwhitneyu(a.to_numpy(), b.to_numpy(),
                                                        alternative="two-sided")
        except Exception as exc:
            return _not_applicable(f"检验未能完成：{exc}")
        return {
            "applicable": True, "test": "Mann-Whitney U", "statistic": round(float(statistic), 6),
            "p": round(float(pvalue), 6), "significant": bool(pvalue < alpha),
            "assumptions": ["不要求正态分布", "不要求方差齐性"],
            "note": "秩和检验比较分布位置，不给出均值差。",
            "effect_size": {"name": "cliffs_delta", "value": _cliffs_delta(a, b),
                            "interpretation": _interpret_delta(_cliffs_delta(a, b))},
        }

    if a.shape[0] < 2 or b.shape[0] < 2:
        return _not_applicable("t 检验每组至少需要 2 个观测值。")
    assumptions = [_assumption_normality(a), _assumption_normality(b)]
    normal = all(item.get("normal_at_alpha") for item in assumptions
                 if item.get("checked"))
    homoscedastic = _assumption_homoscedasticity([a, b])
    equal_var = bool(homoscedastic.get("equal_variances"))
    try:
        statistic, pvalue = scipy_stats.ttest_ind(a.to_numpy(), b.to_numpy(),
                                                  equal_var=equal_var)
    except Exception as exc:
        return _not_applicable(f"检验未能完成：{exc}")
    method = "等方差 t 检验" if equal_var else "Welch 校正 t 检验"
    notes = []
    if not normal:
        notes.append("正态性未通过；t 检验结果应谨慎解读，可改用 Mann-Whitney U 检验。")
    if not equal_var:
        notes.append("方差齐性未通过，已使用 Welch 校正。")
    return {
        "applicable": True, "test": method, "statistic": round(float(statistic), 6),
        "p": round(float(pvalue), 6), "significant": bool(pvalue < alpha),
        "assumptions": assumptions, "homoscedasticity": homoscedastic,
        "notes": notes,
        "alternative_used": "mannwhitneyu" if not normal else "",
        "effect_size": {"name": "hedges_g", "value": _hedges_g(a, b),
                        "interpretation": _interpret_d(_hedges_g(a, b))},
        "mean_difference": round(float(a.astype(float).mean()
                                       - b.astype(float).mean()), 6),
    }


def _one_way_anova(groups: list[Any], alpha: float, test: str) -> dict[str, Any]:
    """Compare three or more groups."""
    scipy_stats = _scipy_stats()
    if len(groups) < 2:
        return _not_applicable("至少需要 2 组才能比较。")
    if any(group.shape[0] < 2 for group in groups):
        return _not_applicable("每组至少需要 2 个观测值。")
    if test == "kruskal":
        try:
            statistic, pvalue = scipy_stats.kruskal(*[g.to_numpy() for g in groups])
        except Exception as exc:
            return _not_applicable(f"检验未能完成：{exc}")
        return {
            "applicable": True, "test": "Kruskal-Wallis", "statistic": round(float(statistic), 6),
            "p": round(float(pvalue), 6), "significant": bool(pvalue < alpha),
            "assumptions": ["不要求正态分布", "不要求方差齐性"],
            "note": "非参数检验；显著时仍需事后比较才能定位差异组。",
            "effect_size": {"name": "epsilon_squared", "value": _epsilon_squared(groups)},
        }
    assumptions = [_assumption_normality(group) for group in groups]
    normal = all(item.get("normal_at_alpha") for item in assumptions
                 if item.get("checked"))
    homoscedastic = _assumption_homoscedasticity(groups)
    try:
        statistic, pvalue = scipy_stats.f_oneway(*[g.to_numpy() for g in groups])
    except Exception as exc:
        return _not_applicable(f"检验未能完成：{exc}")
    notes = []
    if not normal:
        notes.append("正态性未通过；可改用 Kruskal-Wallis 检验。")
    if not homoscedastic.get("equal_variances"):
        notes.append("方差齐性未通过，Welch 校正的方差分析更稳妥。")
    return {
        "applicable": True, "test": "单因素方差分析", "statistic": round(float(statistic), 6),
        "p": round(float(pvalue), 6), "significant": bool(pvalue < alpha),
        "assumptions": assumptions, "homoscedasticity": homoscedastic,
        "notes": notes,
        "alternative_used": "kruskal" if not normal else "",
        "effect_size": {"name": "eta_squared", "value": _eta_squared(groups),
                        "interpretation": _interpret_eta(_eta_squared(groups))},
    }


def _paired_test(frame: Any, value_col: str, group_col: str,
                 pair_col: str, alpha: float) -> dict[str, Any]:
    """Compare two groups measured on the same units."""
    scipy_stats = _scipy_stats()
    pd = _pd()
    groups = list(frame[group_col].dropna().unique())
    if len(groups) != 2:
        return _not_applicable(
            f"配对检验需要恰好 2 个处理组，当前「{group_col}」有 {len(groups)} 个取值。")
    first, second = str(groups[0]), str(groups[1])
    pivot = frame.pivot_table(index=pair_col, columns=group_col, values=value_col,
                              aggfunc="mean")
    if first not in pivot.columns or second not in pivot.columns:
        return _not_applicable("配对后缺少某一组的观测值。")
    paired = pivot[[first, second]].dropna()
    if paired.shape[0] < 2:
        return _not_applicable(
            f"按「{pair_col}」配对后只有 {paired.shape[0]} 对完整观测，配对检验至少需要 2 对。")
    differences = paired[first].astype(float) - paired[second].astype(float)
    try:
        statistic, pvalue = scipy_stats.ttest_rel(paired[first].astype(float).to_numpy(),
                                                  paired[second].astype(float).to_numpy())
    except Exception as exc:
        return _not_applicable(f"检验未能完成：{exc}")
    return {
        "applicable": True, "test": "配对样本 t 检验", "statistic": round(float(statistic), 6),
        "p": round(float(pvalue), 6), "significant": bool(pvalue < alpha),
        "n_pairs": int(paired.shape[0]),
        "mean_difference": round(float(differences.mean()), 6),
        "assumptions": [_assumption_normality(differences)],
        "notes": ["配对差值应近似正态分布。"],
        "effect_size": {"name": "cohens_dz", "value": _cohens_dz(differences)},
    }


def _correlation(frame: Any, x_col: str, y_col: str, method: str,
                 alpha: float) -> dict[str, Any]:
    """Correlate two numeric columns."""
    scipy_stats = _scipy_stats()
    pd = _pd()
    subset = frame[[x_col, y_col]].copy()
    subset[x_col] = _numeric(subset[x_col])
    subset[y_col] = _numeric(subset[y_col])
    subset = subset.dropna()
    if subset.shape[0] < 3:
        return _not_applicable(
            f"两列同时有效的观测只有 {subset.shape[0]} 个，相关分析至少需要 3 个。")
    x = subset[x_col].astype(float).to_numpy()
    y = subset[y_col].astype(float).to_numpy()
    if method == "spearman":
        statistic, pvalue = scipy_stats.spearmanr(x, y)
        name = "Spearman 秩相关"
    else:
        statistic, pvalue = scipy_stats.pearsonr(x, y)
        name = "Pearson 相关"
    return {
        "applicable": True, "test": name, "statistic": round(float(statistic), 6),
        "p": round(float(pvalue), 6), "significant": bool(pvalue < alpha),
        "n": int(subset.shape[0]),
        "assumptions": (["不要求正态分布"] if method == "spearman"
                        else ["要求两个变量近似正态且线性相关"]),
        "effect_size": {"name": "r", "value": round(float(statistic), 6),
                        "interpretation": _interpret_r(float(statistic))},
    }


def _contingency(frame: Any, row_col: str, col_col: str, alpha: float) -> dict[str, Any]:
    """Chi-square test of independence between two categorical columns."""
    scipy_stats = _scipy_stats()
    pd = _pd()
    table = pd.crosstab(frame[row_col].dropna(), frame[col_col].dropna())
    if table.shape[0] < 2 or table.shape[1] < 2:
        return _not_applicable("两个字段都需要至少 2 个取值才能做独立性检验。")
    try:
        statistic, pvalue, dof, expected = scipy_stats.chi2_contingency(table.to_numpy())
    except Exception as exc:
        return _not_applicable(f"检验未能完成：{exc}")
    small = int((expected < 5).sum())
    notes = []
    if small:
        notes.append(
            f"有 {small} 个期望频数小于 5，χ² 检验的近似在此不可靠，"
            "建议合并稀疏类别或改用 Fisher 精确检验。")
    return {
        "applicable": True, "test": "卡方独立性检验", "statistic": round(float(statistic), 6),
        "p": round(float(pvalue), 6), "dof": int(dof), "significant": bool(pvalue < alpha),
        "table": [[int(v) for v in row] for row in table.to_numpy()],
        "row_labels": [str(v) for v in table.index],
        "column_labels": [str(v) for v in table.columns],
        "notes": notes,
        "effect_size": {"name": "cramers_v", "value": _cramers_v(table),
                        "interpretation": _interpret_v(_cramers_v(table))},
    }


def _group_summary(frame: Any, group_cols: list[str], value_cols: list[str]) -> list[dict[str, Any]]:
    """Summarise each value column within each group."""
    pd = _pd()
    rows: list[dict[str, Any]] = []
    for value_col in value_cols:
        numeric = _numeric(frame[value_col])
        if numeric.dropna().empty:
            continue
        grouped = numeric.groupby([frame[c] for c in group_cols], dropna=False)
        for key, values in grouped:
            if not isinstance(key, tuple):
                key = (key,)
            stats = _describe(values)
            row = {column: ("" if value is None else str(value))
                   for column, value in zip(group_cols, key)}
            row["指标"] = value_col
            row.update(stats)
            rows.append(row)
    return rows


def _build_table(rows: list[dict[str, Any]]) -> Any:
    """Turn summary rows into a spreadsheet-ready frame."""
    pd = _pd()
    return pd.DataFrame(rows)


@tool
def analyze_research_data(file_index: int = 0, sheet_name: str = "",
                           header_row: int = 1,
                           group_by_json: str | list[str] = "",
                           metrics_json: str | list[str] = "",
                           test: str = "",
                           paired_by_json: str | list[str] = "",
                           alpha: float = 0.05,
                           output_format: str = "none",
                           title: str = "统计结果") -> str:
    """对本轮上传的表格做真实统计计算；不给不存在的结果。

    先用 `inspect_uploaded_table` 看清列名与类型，再调用本工具。

    file_index/sheet_name/header_row：与 inspect_uploaded_table 相同，指定要分析的表。
    group_by_json：分组字段（1–2 个列名），如 ["处理"] 或 ["处理","时间"]。
    metrics_json：需要描述统计的数值列名列表；留空则用表中全部数值列。
    test：检验方式，可选 ttest_ind（两组独立）、ttest_rel（配对，需 paired_by_json）、
    anova（3 组及以上）、kruskal、mannwhitneyu、pearson、spearman、chi2；留空只做描述统计。
    paired_by_json：配对键列名，仅 ttest_rel 需要；配对不完整会明确拒绝。
    alpha：显著性水平，默认 0.05。
    output_format：none / csv / xlsx；非 none 时把结果表作为文件交付。

    返回描述统计、分组汇总与检验结论。设计不适用时返回 not_applicable 并说明原因，
    不会给出未计算的 p 值；显著性检验前会先检验正态性与方差齐性并写明假设。
    """
    try:
        from src.research.tools import _load_frame

        frame, meta = _load_frame(file_index, sheet_name, header_row, None)
    except Exception as exc:
        return f"[STATS_INPUT_ERROR] 读取表格失败：{exc}"

    if frame is None or frame.empty:
        return "[STATS_EMPTY] 表格没有数据行可供统计。"

    group_cols = _json_list(group_by_json, name="group_by_json")
    metric_cols = _json_list(metrics_json, name="metrics_json")
    pair_cols = _json_list(paired_by_json, name="paired_by_json")

    if len(group_cols) > 2:
        return "[STATS_ARG] group_by_json 最多支持 2 个分组字段。"
    for column in group_cols + pair_cols:
        if column not in frame.columns:
            return (f"[STATS_ARG] 表中没有列「{column}」；可用列："
                    f"{', '.join(str(c) for c in frame.columns[:40])}。")
    if metric_cols:
        missing = [c for c in metric_cols if c not in frame.columns]
        if missing:
            return (f"[STATS_ARG] 表中没有列：{', '.join(missing)}；可用列："
                    f"{', '.join(str(c) for c in frame.columns[:40])}。")
    else:
        metric_cols = [str(c) for c in frame.columns
                       if _numeric(frame[c]).dropna().shape[0] > 0][:12]

    requested = str(test or "").strip().lower()
    if requested and requested not in TESTS:
        return (f"[STATS_ARG] 不支持的检验方式「{test}」；可用："
                f"{', '.join(sorted(TESTS))}。")

    payload: dict[str, Any] = {
        "source": {"name": str(meta.get("name") or ""),
                   "sheet": str(meta.get("sheet") or ""),
                   "rows": int(frame.shape[0]), "columns": int(frame.shape[1])},
        "columns": [str(c) for c in frame.columns],
        "descriptive": {},
        "grouped": [],
        "test": None,
        "alpha": float(alpha),
    }

    for column in metric_cols:
        payload["descriptive"][column] = _describe(frame[column])

    if group_cols:
        payload["grouped"] = _group_summary(frame, group_cols, metric_cols)

    # ── the test ──────────────────────────────────────────────────────────
    if requested:
        value_col = metric_cols[0] if metric_cols else None
        if value_col is None:
            payload["test"] = _not_applicable("表中没有可用于检验的数值列。")
        elif requested in {"ttest_ind", "mannwhitneyu", "anova", "kruskal"}:
            if not group_cols:
                payload["test"] = _not_applicable(
                    f"{TESTS[requested]} 需要 group_by_json 指定分组字段。")
            else:
                numeric = _numeric(frame[value_col])
                usable = frame[[group_cols[0]]].copy()
                usable["__value__"] = numeric
                usable = usable.dropna(subset=["__value__"])
                levels = list(usable[group_cols[0]].dropna().unique())
                if len(levels) > _MAX_GROUP_LEVELS:
                    payload["test"] = _not_applicable(
                        f"分组「{group_cols[0]}」有 {len(levels)} 个取值，超过 "
                        f"{_MAX_GROUP_LEVELS} 个上限，请先聚合再比较。")
                elif requested in {"ttest_ind", "mannwhitneyu"}:
                    if len(levels) != 2:
                        payload["test"] = _not_applicable(
                            f"{TESTS[requested]} 需要恰好 2 组，"
                            f"「{group_cols[0]}」有 {len(levels)} 个取值。"
                            "多于两组请用 anova。")
                    else:
                        first = usable[usable[group_cols[0]] == levels[0]]["__value__"]
                        second = usable[usable[group_cols[0]] == levels[1]]["__value__"]
                        payload["test"] = _independent_two_sample(
                            first, second, alpha, requested)
                        payload["test"]["groups"] = [str(levels[0]), str(levels[1])]
                        payload["test"]["value_column"] = value_col
                else:
                    groups = [usable[usable[group_cols[0]] == level]["__value__"]
                              for level in levels]
                    payload["test"] = _one_way_anova(groups, alpha, requested)
                    payload["test"]["groups"] = [str(level) for level in levels]
                    payload["test"]["value_column"] = value_col
                    if payload["test"].get("applicable"):
                        # The omnibus test says *whether* the groups differ;
                        # the pairwise table says *where*, with a
                        # family-wise correction so the many comparisons do
                        # not manufacture a difference that is not there.
                        named = {str(level): usable[usable[group_cols[0]] == level]["__value__"]
                                 for level in levels}
                        parametric = requested == "anova" and not payload["test"].get(
                            "alternative_used")
                        payload["test"]["pairwise"] = _pairwise(named, alpha, parametric)
        elif requested == "ttest_rel":
            if not group_cols or not pair_cols:
                payload["test"] = _not_applicable(
                    "配对检验需要 group_by_json（处理组）与 paired_by_json（配对键）。")
            elif value_col is None:
                payload["test"] = _not_applicable("表中没有可用于检验的数值列。")
            else:
                numeric = _numeric(frame[value_col])
                working = frame.copy()
                working["__value__"] = numeric
                working = working.dropna(subset=["__value__"])
                payload["test"] = _paired_test(
                    working, "__value__", group_cols[0], pair_cols[0], alpha)
                payload["test"]["value_column"] = value_col
        elif requested in {"pearson", "spearman"}:
            if len(metric_cols) < 2:
                payload["test"] = _not_applicable(
                    f"{TESTS[requested]} 需要至少两个数值列（metrics_json）。")
            else:
                payload["test"] = _correlation(
                    frame, metric_cols[0], metric_cols[1], requested, alpha)
                payload["test"]["columns"] = [metric_cols[0], metric_cols[1]]
        elif requested == "chi2":
            if len(group_cols) < 2:
                payload["test"] = _not_applicable(
                    "卡方检验需要两个分类字段（group_by_json 提供 2 个列名）。")
            else:
                payload["test"] = _contingency(frame, group_cols[0], group_cols[1], alpha)

    # ── optional file ─────────────────────────────────────────────────────
    fmt = str(output_format or "none").strip().lower()
    if fmt in {"csv", "xlsx"}:
        try:
            from src.research.tools import _safe_name, _save_artifact
            import io
            import csv as _csv

            rows: list[dict[str, Any]] = []
            for column, stats in payload["descriptive"].items():
                row = {"区块": "描述统计", "分组": "", "指标": column}
                row.update(stats)
                rows.append(row)
            for row in payload["grouped"]:
                item = {"区块": "分组汇总"}
                item.update(row)
                rows.append(item)
            if payload.get("test"):
                test_rows = payload["test"]
                effect = test_rows.get("effect_size") or {}
                rows.append({"区块": "检验", "分组": "", "指标": test_rows.get("test", requested),
                             "p": test_rows.get("p"), "显著": test_rows.get("significant"),
                             "效应量": (f"{effect.get('name')}={effect.get('value')}"
                                        if effect.get("name") else ""),
                             "说明": test_rows.get("reason", "; ".join(test_rows.get("notes", [])) or "")})
                for pair in test_rows.get("pairwise", []) or []:
                    if not pair.get("applicable"):
                        continue
                    rows.append({"区块": "两两比较",
                                 "分组": " vs ".join(str(g) for g in pair.get("groups", [])),
                                 "指标": pair.get("test", ""),
                                 "p": pair.get("p"),
                                 "p_holm": pair.get("p_holm"),
                                 "显著": pair.get("significant"),
                                 "效应量": (f"{pair.get('hedges_g')}" if pair.get("hedges_g") is not None
                                            else f"{pair.get('cliffs_delta')}")})
            if not rows:
                return json.dumps(payload, ensure_ascii=False, default=str)
            columns: list[str] = []
            for row in rows:
                for key in row:
                    if key not in columns:
                        columns.append(key)
            filename = _safe_name(title, "统计结果") + "." + fmt
            if fmt == "xlsx":
                from openpyxl import Workbook
                workbook = Workbook()
                sheet = workbook.active
                sheet.title = "统计结果"
                sheet.append(columns)
                for row in rows:
                    sheet.append([row.get(column, "") for column in columns])
                sheet.freeze_panes = "A2"
                output = io.BytesIO()
                workbook.save(output)
                payload_bytes = output.getvalue()
                mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            else:
                output = io.StringIO(newline="")
                writer = _csv.writer(output)
                writer.writerow(columns)
                for row in rows:
                    writer.writerow([row.get(column, "") for column in columns])
                payload_bytes = output.getvalue().encode("utf-8-sig")
                mime = "text/csv; charset=utf-8"
            url = _save_artifact(filename, payload_bytes, mime)
            payload["download_url"] = url
        except Exception as exc:
            payload["export_error"] = str(exc)

    return json.dumps(payload, ensure_ascii=False, default=str)


__all__ = ["TESTS", "analyze_research_data"]
