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

Assumption diagnostics are evidence with limitations. Experimental design
selects the method: a non-significant Shapiro or Levene test does not prove
normality or equal variance. Independent mean comparisons use Welch's test;
additional model structure must be declared and validated explicitly.

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
    "welch_anova": "Welch 单因素方差分析",
    "regression": "线性回归（HC3 稳健标准误）",
    "factorial": "因子交互模型（HC3 稳健标准误）",
    "blocked": "随机完全区组固定效应模型",
    "repeated": "完整平衡设计重复测量 ANOVA",
    "mixed": "随机截距线性混合模型",
    "power": "预设效应量的 t 检验功效/样本量",
    "randomization": "可复现的完全/区组随机分配",
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
        "missing_rate": (int(series.shape[0]) - count) / max(int(series.shape[0]), 1),
        "mean": float(values.mean()),
        "sd": float(values.std(ddof=1)) if count > 1 else None,
        "se": float(values.std(ddof=1) / math.sqrt(count)) if count > 1 else None,
        "median": float(values.median()),
        "q1": float(values.quantile(0.25)),
        "q3": float(values.quantile(0.75)),
        "min": float(values.min()),
        "max": float(values.max()),
    }


def _assumption_normality(values: Any, alpha: float = 0.05) -> dict[str, Any]:
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
    if values.nunique() < 2:
        return {"checked": False, "reason": "常数样本无法评估正态性。"}
    if count <= 5000:
        try:
            statistic, pvalue = scipy_stats.shapiro(values.to_numpy())
            _require_finite_result(statistic, pvalue)
            return {
                "checked": True, "test": "Shapiro-Wilk",
                "statistic": float(statistic),
                "p": float(pvalue),
                "reject_normality_at_alpha": bool(pvalue < alpha),
                "alpha": alpha,
                "caveat": "不拒绝正态性不证明总体正态；小样本检验能力有限。",
            }
        except Exception as exc:  # pragma: no cover - scipy edge cases
            return {"checked": False, "reason": f"正态性检验未能完成：{exc}"}
    skew = float(values.skew())
    kurtosis = float(values.kurtosis())
    return {
        "checked": True, "test": "描述性偏度/峰度（样本量 > 5000）",
        "skew": skew, "kurtosis": kurtosis,
        "caveat": "描述性诊断不证明正态性；未以阈值自动选择方法。",
    }


def _scipy_stats():
    from scipy import stats
    return stats


def _assumption_homoscedasticity(groups: list[Any], alpha: float = 0.05) -> dict[str, Any]:
    """Levene's test for equal variances across groups."""
    scipy_stats = _scipy_stats()
    usable = [group.to_numpy() for group in groups if group.shape[0] >= 2]
    if len(usable) < 2:
        return {"checked": False, "reason": "少于两组有足够样本，无法检验方差齐性。"}
    try:
        statistic, pvalue = scipy_stats.levene(*usable)
        _require_finite_result(statistic, pvalue)
        return {
            "checked": True, "test": "Levene",
            "statistic": float(statistic),
            "p": float(pvalue),
            "reject_equal_variances_at_alpha": bool(pvalue < alpha),
            "alpha": alpha,
            "caveat": "不拒绝等方差不证明方差相等；方法选择不依赖此阈值。",
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


def _require_finite_result(statistic: Any, pvalue: Any) -> None:
    """SciPy can return NaN/inf without raising on degenerate samples."""
    if not (math.isfinite(float(statistic)) and math.isfinite(float(pvalue))
            and 0 <= float(pvalue) <= 1):
        raise ValueError("检验返回非有限统计量或无效 p 值，无法作出推断。")


def _finite_json(value: Any) -> Any:
    """Undefined auxiliary summaries are null, never non-standard JSON NaN."""
    if isinstance(value, dict):
        return {key: _finite_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite_json(item) for item in value]
    if hasattr(value, "item"):
        return _finite_json(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


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
    return float(a.astype(float).mean() - b.astype(float).mean()) / math.sqrt(pooled)


def _hedges_g(a: Any, b: Any) -> float | None:
    """Hedges' g — Cohen's d with the small-sample bias correction."""
    na, nb = int(a.shape[0]), int(b.shape[0])
    value = _cohens_d(a, b)
    if value is None:
        return None
    correction = 1.0 - 3.0 / (4.0 * (na + nb) - 9.0)
    return value * correction


def _cliffs_delta(a: Any, b: Any) -> float | None:
    """P(a > b) - P(a < b), using sorted ranks rather than a cross product."""
    if a.shape[0] < 1 or b.shape[0] < 1:
        return None
    np = _np()
    left = a.astype(float).to_numpy()
    right = np.sort(b.astype(float).to_numpy())
    greater = int(np.searchsorted(right, left, side="left").sum(dtype=np.int64))
    less = int((len(right) - np.searchsorted(right, left, side="right")).sum(dtype=np.int64))
    return (greater - less) / (len(left) * len(right))


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
    return between / total


def _epsilon_squared(groups: list[Any]) -> float | None:
    """Epsilon squared — the rank-based counterpart of eta squared."""
    sizes = [int(group.shape[0]) for group in groups]
    if sum(sizes) < 2:
        return None
    try:
        from scipy import stats as scipy_stats
        statistic, _ = scipy_stats.kruskal(*[g.astype(float).to_numpy() for g in groups])
        total = sum(sizes)
        return float(statistic) / (total - 1)
    except Exception:
        return None


def _cohens_dz(differences: Any) -> float | None:
    """Cohen's d_z — the standardised mean of paired differences."""
    if int(differences.shape[0]) < 2:
        return None
    sd = float(differences.astype(float).std(ddof=1))
    if sd <= 0:
        return None
    return float(differences.astype(float).mean()) / sd


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
        return math.sqrt(chi2 / (total * minimum))
    except Exception:
        return None


def _pairwise(groups: dict[Any, Any], alpha: float, parametric: bool) -> list[dict[str, Any]]:
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
                pairs.append({"groups": [str(names[i]), str(names[j])],
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
                _require_finite_result(statistic, pvalue)
            except Exception as exc:
                pairs.append({"groups": [str(names[i]), str(names[j])],
                              "applicable": False,
                              "reason": f"比较未能完成：{exc}"})
                continue
            pairs.append({
                "groups": [str(names[i]), str(names[j])],
                "applicable": True,
                "test": method,
                "statistic": float(statistic),
                "p": float(pvalue),
                effect_name: effect,
            })
    usable = [item for item in pairs if item.get("applicable")]
    if len(usable) > 1:
        adjusted = _holm([float(item["p"]) for item in usable])
        for item, value in zip(usable, adjusted):
            item["p_holm"] = value
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
            _require_finite_result(statistic, pvalue)
        except Exception as exc:
            return _not_applicable(f"检验未能完成：{exc}")
        return {
            "applicable": True, "test": "Mann-Whitney U", "statistic": float(statistic),
            "p": float(pvalue), "significant": bool(pvalue < alpha),
            "assumptions": ["不要求正态分布", "不要求方差齐性"],
            "note": "秩和检验比较分布；仅在分布形状可比时解释为位置差异，不直接检验均值或一般中位数差。",
            "effect_size": {"name": "cliffs_delta", "value": _cliffs_delta(a, b),
                            "interpretation": _interpret_delta(_cliffs_delta(a, b))},
        }

    if a.shape[0] < 2 or b.shape[0] < 2:
        return _not_applicable("t 检验每组至少需要 2 个观测值。")
    assumptions = [_assumption_normality(a, alpha), _assumption_normality(b, alpha)]
    homoscedastic = _assumption_homoscedasticity([a, b], alpha)
    # Welch is specified in advance for independent mean comparisons; a
    # non-significant Levene test never establishes equal variances.
    equal_var = False
    try:
        statistic, pvalue = scipy_stats.ttest_ind(a.to_numpy(), b.to_numpy(),
                                                  equal_var=equal_var)
        _require_finite_result(statistic, pvalue)
    except Exception as exc:
        return _not_applicable(f"检验未能完成：{exc}")
    method = "等方差 t 检验" if equal_var else "Welch 校正 t 检验"
    notes = ["依据独立样本均值比较设计使用 Welch 检验；未按 Shapiro/Levene 阈值切换方法。",
             "独立性来自实验设计，无法由样本检验确认；小样本、强偏态或异常值需结合领域证据复核。"]
    variance_a, variance_b = float(a.var(ddof=1)) / len(a), float(b.var(ddof=1)) / len(b)
    se = math.sqrt(variance_a + variance_b)
    df = (variance_a + variance_b) ** 2 / (variance_a ** 2 / (len(a) - 1) + variance_b ** 2 / (len(b) - 1))
    difference = float(a.mean() - b.mean())
    margin = float(scipy_stats.t.ppf(1 - alpha / 2, df)) * se
    return {
        "applicable": True, "test": method, "statistic": float(statistic),
        "p": float(pvalue), "significant": bool(pvalue < alpha),
        "assumptions": assumptions, "homoscedasticity": homoscedastic,
        "notes": notes,
        "selection_basis": "independent mean comparison; unequal variances allowed",
        "effect_size": {"name": "hedges_g", "value": _hedges_g(a, b),
                        "direction": "first_group_minus_second_group",
                        "interpretation": _interpret_d(_hedges_g(a, b))},
        "mean_difference": difference, "df": df,
        "confidence_interval": {"level": 1 - alpha, "low": difference - margin,
                                "high": difference + margin, "method": "Welch t"},
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
            _require_finite_result(statistic, pvalue)
        except Exception as exc:
            return _not_applicable(f"检验未能完成：{exc}")
        return {
            "applicable": True, "test": "Kruskal-Wallis", "statistic": float(statistic),
            "p": float(pvalue), "significant": bool(pvalue < alpha),
            "assumptions": ["不要求正态分布", "不要求方差齐性"],
            "note": "非参数检验；显著时仍需事后比较才能定位差异组。",
            "effect_size": {"name": "epsilon_squared", "value": _epsilon_squared(groups)},
        }
    assumptions = [_assumption_normality(group, alpha) for group in groups]
    homoscedastic = _assumption_homoscedasticity(groups, alpha)
    try:
        statistic, pvalue = scipy_stats.f_oneway(*[g.to_numpy() for g in groups])
        _require_finite_result(statistic, pvalue)
    except Exception as exc:
        return _not_applicable(f"检验未能完成：{exc}")
    notes = ["按请求执行经典单因素 ANOVA，要求独立观测与近似等方差；诊断不能证明这些假设。",
             "方差不等时可预先选择 welch_anova；秩检验比较的目标与均值检验不同。"]
    return {
        "applicable": True, "test": "单因素方差分析", "statistic": float(statistic),
        "p": float(pvalue), "significant": bool(pvalue < alpha),
        "assumptions": assumptions, "homoscedasticity": homoscedastic,
        "notes": notes,
        "selection_basis": "explicit classical one-way ANOVA request",
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
    first, second = groups
    missing_keys = frame[[pair_col, group_col]].isna().any(axis=1)
    keyed = frame.loc[~missing_keys]
    duplicates = keyed.duplicated([pair_col, group_col], keep=False)
    pairing = {"missing_key_rows": int(missing_keys.sum()),
               "missing_value_rows": int(frame[value_col].isna().sum()),
               "duplicate_pair_group_rows": int(duplicates.sum())}
    if duplicates.any():
        return _not_applicable("同一配对键/处理组存在重复记录；请先明确重复测量设计，不能自动平均。",
                               pairing=pairing)
    pivot = keyed.pivot(index=pair_col, columns=group_col, values=value_col)
    paired = pivot.reindex(columns=[first, second]).dropna()
    pairing.update({"total_pair_keys": int(pivot.shape[0]),
                    "complete_pairs": int(paired.shape[0]),
                    "incomplete_pairs": int(pivot.shape[0] - paired.shape[0])})
    if missing_keys.any() or pairing["incomplete_pairs"]:
        return _not_applicable("存在缺失配对键、组别或不完整配对，未执行配对检验。",
                               n_pairs=int(paired.shape[0]), pairing=pairing)
    if paired.shape[0] < 2:
        return _not_applicable(
            f"按「{pair_col}」配对后只有 {paired.shape[0]} 对完整观测，配对检验至少需要 2 对。",
            n_pairs=int(paired.shape[0]), pairing=pairing)
    differences = paired[first].astype(float) - paired[second].astype(float)
    try:
        statistic, pvalue = scipy_stats.ttest_rel(paired[first].astype(float).to_numpy(),
                                                  paired[second].astype(float).to_numpy())
        _require_finite_result(statistic, pvalue)
    except Exception as exc:
        return _not_applicable(f"检验未能完成：{exc}", pairing=pairing)
    return {
        "applicable": True, "test": "配对样本 t 检验", "statistic": float(statistic),
        "p": float(pvalue), "significant": bool(pvalue < alpha),
        "n_pairs": int(paired.shape[0]), "pairing": pairing,
        "groups": [str(first), str(second)],
        "mean_difference": float(differences.mean()),
        "confidence_interval": _mean_interval(differences, alpha),
        "assumptions": [_assumption_normality(differences, alpha)],
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
    if subset[x_col].nunique() < 2 or subset[y_col].nunique() < 2:
        return _not_applicable("常数列无法计算相关系数。")
    try:
        if method == "spearman":
            statistic, pvalue = scipy_stats.spearmanr(x, y)
            name = "Spearman 秩相关"
        else:
            fitted = scipy_stats.pearsonr(x, y)
            statistic, pvalue = fitted
            name = "Pearson 相关"
        _require_finite_result(statistic, pvalue)
        interval = None
        if method != "spearman":
            ci = fitted.confidence_interval(confidence_level=1 - alpha)
            interval = {"level": 1 - alpha, "low": float(ci.low), "high": float(ci.high), "method": "Fisher z"}
    except Exception as exc:
        return _not_applicable(f"检验未能完成：{exc}")
    return {
        "applicable": True, "test": name, "statistic": float(statistic),
        "p": float(pvalue), "significant": bool(pvalue < alpha),
        "n": int(subset.shape[0]), "confidence_interval": interval,
        "assumptions": (["独立配对观测；变量至少为有序量"] if method == "spearman"
                        else ["独立配对观测；Pearson 的经典 p 值依赖双变量正态模型"]),
        "notes": (["Spearman 的渐近 p 值在小样本或大量并列秩时需谨慎；未执行置换检验。"]
                  if method == "spearman" else ["相关不证明因果；未从单变量正态性检验推断双变量正态。"]),
        "effect_size": {"name": "r", "value": float(statistic),
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
        _require_finite_result(statistic, pvalue)
    except Exception as exc:
        return _not_applicable(f"检验未能完成：{exc}")
    small = int((expected < 5).sum())
    notes = []
    if small:
        notes.append(
            f"有 {small} 个期望频数小于 5，χ² 检验的近似在此不可靠，"
            "建议合并稀疏类别或改用 Fisher 精确检验。")
    return {
        "applicable": True, "test": "卡方独立性检验", "statistic": float(statistic),
        "p": float(pvalue), "dof": int(dof), "significant": bool(pvalue < alpha),
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


def _mean_interval(values: Any, alpha: float) -> dict[str, Any]:
    n = len(values)
    mean = float(values.mean())
    margin = float(_scipy_stats().t.ppf(1 - alpha / 2, n - 1)) * float(values.std(ddof=1)) / math.sqrt(n)
    return {"level": 1 - alpha, "low": mean - margin, "high": mean + margin,
            "method": "Student t", "df": n - 1}


def _json_object(raw: Any, name: str) -> dict[str, Any]:
    if raw is None or raw == "":
        return {}
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, dict):
        raise ValueError(f"{name} must be an object")
    return raw


def _design_columns(design: dict[str, Any], key: str) -> list[str]:
    return _json_list(design.get(key), name=f"design.{key}")


def _validated_design(frame: Any, design: dict[str, Any], method: str,
                      group_cols: list[str], pair_cols: list[str]) -> None:
    """Validate semantics before fitting; do not interpret an arbitrary formula."""
    method_fields = {
        "regression": {"predictors", "factors", "interactions"},
        "factorial": {"factors", "interactions"},
        "blocked": {"factors", "block"}, "repeated": {"subject", "within"},
        "mixed": {"subject", "predictors", "factors"},
        "power": {"power_test", "effect_size", "nobs", "target_power", "ratio"},
        "randomization": {"seed", "treatments", "block"},
    }
    allowed = {"kind", "experimental_unit", "independent_units"} | method_fields[method]
    unknown = set(design) - allowed
    if unknown:
        raise ValueError(f"Unknown design fields: {sorted(unknown)}")
    if design.get("kind", method) != method:
        raise ValueError("Design kind does not match requested method")
    if pair_cols:
        raise ValueError("Use design.subject for repeated/mixed designs; paired_by_json is not ignored")
    if (design.get("independent_units") is not True or not isinstance(design.get("experimental_unit"), str)
            or not design["experimental_unit"].strip()):
        raise ValueError("Declare experimental_unit text and independent_units=true for the independent experimental units")
    if method in {"regression", "factorial", "blocked"} and design["experimental_unit"] in frame:
        units = frame[design["experimental_unit"]]
        if units.isna().any() or units.duplicated().any():
            raise ValueError("Experimental unit IDs are missing/repeated; an independent-row model would pseudoreplicate units")
    used = []
    for key in ("predictors", "factors", "within"):
        cols = _design_columns(design, key)
        if len(cols) != len(set(cols)):
            raise ValueError(f"Duplicate design {key}")
        used += cols
    for key in ("block", "subject"):
        if design.get(key):
            if not isinstance(design[key], str):
                raise ValueError(f"design.{key} must name a column")
            used.append(design[key])
    if any(col not in frame for col in used):
        raise ValueError("A design column is absent from the input table")
    if len(used) != len(set(used)):
        raise ValueError("A column cannot serve conflicting design roles")
    if len(used) > 12:
        raise ValueError("At most 12 design columns are supported")
    factors = _design_columns(design, "factors")
    within = _design_columns(design, "within")
    expected_groups = within if method == "repeated" else factors
    if group_cols and group_cols != expected_groups:
        raise ValueError("group_by_json does not match the declared design factors")
    if method not in {"blocked", "randomization"} and design.get("block"):
        raise ValueError("Block structure requires the blocked model or blocked randomization")
    if method not in {"repeated", "mixed"} and design.get("subject"):
        raise ValueError("Subject dependence requires repeated or mixed analysis")
    if method != "repeated" and within:
        raise ValueError("Within-subject factors require the repeated model")
    if method != "regression" and method != "mixed" and _design_columns(design, "predictors"):
        raise ValueError("Numeric predictors are only supported in regression or mixed models")
    if "interactions" in design and not isinstance(design["interactions"], bool):
        raise ValueError("interactions must be true or false")
    if method not in {"factorial", "regression"} and design.get("interactions"):
        raise ValueError("Interactions are unsupported for the requested design")
    if method in {"power", "randomization"} and (factors or within or design.get("subject")):
        raise ValueError("Unrelated model structure is not accepted for design calculations")
    if method == "power" and design.get("block"):
        raise ValueError("This power calculation does not support blocked designs")
    if method == "repeated" and factors:
        raise ValueError("Between-subject factors are not supported by repeated ANOVA")


def _linear_design(frame: Any, value_col: str, design: dict[str, Any],
                   method: str, alpha: float) -> dict[str, Any]:
    import itertools
    import warnings
    import numpy as np
    import statsmodels.api as sm
    import statsmodels.formula.api as smf
    factors = _design_columns(design, "factors")
    predictors = _design_columns(design, "predictors")
    if method == "regression" and not predictors:
        raise ValueError("Regression requires at least one numeric predictor")
    if method == "factorial" and (len(factors) < 2 or len(factors) > 3):
        raise ValueError("Factorial analysis requires two or three categorical factors")
    if method == "blocked" and (len(factors) != 1 or not design.get("block")):
        raise ValueError("Blocked analysis requires one treatment factor and a block column")
    if method == "mixed" and (not design.get("subject") or not (factors or predictors)):
        raise ValueError("Mixed analysis requires subject clusters and fixed predictors/factors")
    extra = [design[key] for key in ("block", "subject") if design.get(key)]
    columns = [value_col, *predictors, *factors, *extra]
    if len(columns) != len(set(columns)):
        raise ValueError("Response cannot also be a predictor, factor, block or subject")
    working = frame[columns].copy()
    for col in [value_col, *predictors]:
        working[col] = _numeric(working[col])
        if not np.isfinite(working[col].dropna().to_numpy(dtype=float)).all():
            raise ValueError(f"Non-finite numeric input in {col}")
    if working.isna().any(axis=None):
        raise ValueError("Model design has missing/non-numeric cells; explicitly resolve exclusions before fitting")
    if working[value_col].nunique() < 2:
        raise ValueError("Constant response cannot support this model")
    for factor in factors + ([design["block"]] if design.get("block") else []):
        if not 2 <= working[factor].nunique() <= _MAX_GROUP_LEVELS:
            raise ValueError("Every categorical factor needs 2 to 50 levels")
    if method == "factorial":
        cells = working.groupby(factors, observed=True).size()
        if len(cells) != math.prod(working[f].nunique() for f in factors) or cells.min() < 2:
            raise ValueError("Factorial model requires a complete crossed design with replication in every cell")
    if method == "blocked":
        counts = working.groupby([design["block"], factors[0]], observed=True).size()
        if len(counts) != working[design["block"]].nunique() * working[factors[0]].nunique() or not (counts == 1).all():
            raise ValueError("RCBD requires exactly one observation per block × treatment cell")
    aliases = {col: f"v{i}" for i, col in enumerate(columns)}
    data = working.rename(columns=aliases)
    terms = [aliases[c] for c in predictors] + [f"C({aliases[c]})" for c in factors]
    if design.get("interactions", method == "factorial"):
        if len(terms) < 2:
            raise ValueError("Interactions require at least two factors/predictors")
        rhs = " * ".join(terms)
    else:
        rhs = " + ".join(terms)
    if method == "blocked":
        rhs += f" + C({aliases[design['block']]})"
    formula = aliases[value_col] + " ~ " + rhs
    model = smf.ols(formula, data=data)
    matrix = model.exog
    if np.linalg.matrix_rank(matrix) < matrix.shape[1] or len(data) <= matrix.shape[1]:
        raise ValueError("Design matrix is singular or has no residual degrees of freedom")
    # A near-exact fit cannot estimate residual uncertainty reliably.
    preliminary = model.fit()
    centered_ss = float(np.sum(np.square(model.endog - np.mean(model.endog))))
    if float(np.sum(preliminary.resid ** 2)) <= np.finfo(float).eps * max(np.finfo(float).tiny, centered_ss):
        raise ValueError("Residual variation is zero at numerical precision")
    if method == "mixed":
        subject = aliases[design["subject"]]
        counts = data.groupby(subject).size()
        if len(counts) < 3 or counts.min() < 2:
            raise ValueError("Random-intercept model requires at least three subjects with repeated observations")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            fit = smf.mixedlm(formula, data, groups=data[subject]).fit(reml=True, method="lbfgs", disp=False)
        if not fit.converged or not np.isfinite(fit.cov_re).all(axis=None) or np.min(np.linalg.eigvalsh(fit.cov_re)) <= 0:
            raise ValueError("Mixed model did not converge to a non-singular random-intercept covariance")
        names = list(fit.fe_params.index)
        params, pvalues, intervals = fit.fe_params, fit.pvalues, fit.conf_int(alpha=alpha)
        caveats = [str(w.message) for w in caught]
        caveats += ["Random-intercept Gaussian model; fixed-effect tests use asymptotic Wald inference, unreliable with few clusters."]
        extra_outputs = {"random_intercept_variance": float(fit.cov_re.iloc[0, 0]), "n_subjects": len(counts)}
    else:
        fit = model.fit(cov_type="HC3")
        names = list(fit.params.index)
        params, pvalues, intervals = fit.params, fit.pvalues, fit.conf_int(alpha=alpha)
        caveats = ["HC3 standard errors do not establish independence, linearity or correct model specification."]
        extra_outputs = {"r_squared": float(fit.rsquared), "df_resid": float(fit.df_resid)}
    coefficients = []
    for term in names:
        values = [float(params[term]), float(pvalues[term]), *map(float, intervals.loc[term])]
        if not np.isfinite(values).all():
            raise ValueError("Model returned non-finite inference")
        coefficients.append({"term": term, "estimate": values[0], "p": values[1],
                             "confidence_interval": {"level": 1 - alpha, "low": values[2], "high": values[3]}})
    return {"applicable": True, "test": TESTS[method], "n": len(data),
            "coefficients": coefficients, "column_aliases": {v: k for k, v in aliases.items()},
            "formula": formula, "selection_basis": "explicit validated experimental design",
            "assumptions": [_assumption_normality(_pd().Series(preliminary.resid), alpha)],
            "notes": caveats, **extra_outputs}


def _repeated_design(frame: Any, value_col: str, design: dict[str, Any], alpha: float):
    import itertools
    from statsmodels.stats.anova import AnovaRM
    within = _design_columns(design, "within")
    subject = design.get("subject")
    if not subject or not 1 <= len(within) <= 2:
        raise ValueError("Repeated ANOVA requires a subject and one or two within-subject factors")
    if value_col in [subject, *within]:
        raise ValueError("Response cannot be a design key")
    working = frame[[subject, *within, value_col]].copy()
    working[value_col] = _numeric(working[value_col])
    if working.isna().any(axis=None) or not _np().isfinite(working[value_col].to_numpy()).all():
        raise ValueError("Repeated design must be complete and finite; no implicit deletion")
    if working.duplicated([subject, *within]).any():
        raise ValueError("Duplicate subject × within-factor cells cannot be silently averaged")
    levels = [list(working[f].unique()) for f in within]
    if any(not 2 <= len(v) <= _MAX_GROUP_LEVELS for v in levels):
        raise ValueError("Each within factor requires 2 to 50 levels")
    expected = set(itertools.product(*levels))
    for _, rows in working.groupby(subject):
        if set(rows[within].itertuples(index=False, name=None)) != expected:
            raise ValueError("Every subject must have the same complete crossed within design")
    if working[subject].nunique() < 3:
        raise ValueError("At least three complete subjects are required")
    aliases = {c: f"v{i}" for i, c in enumerate(working.columns)}
    fit = AnovaRM(working.rename(columns=aliases), aliases[value_col], aliases[subject],
                  within=[aliases[f] for f in within]).fit()
    effects = []
    for term, row in fit.anova_table.iterrows():
        statistic, p = float(row["F Value"]), float(row["Pr > F"])
        _require_finite_result(statistic, p)
        effects.append({"term": term, "statistic": statistic, "p": p,
                        "df_num": float(row["Num DF"]), "df_den": float(row["Den DF"])})
    return {"applicable": True, "test": TESTS["repeated"], "n_subjects": int(working[subject].nunique()),
            "effects": effects, "column_aliases": {v: k for k, v in aliases.items()},
            "notes": ["Uncorrected repeated-measures ANOVA assumes sphericity for factors with more than two levels; no sphericity correction is claimed.",
                      "Subjects must be independent experimental units; repeated cells are not independent replicates."]}


def _design_calculation(frame: Any, method: str, design: dict[str, Any], alpha: float):
    if method == "power":
        from statsmodels.stats.power import TTestIndPower, TTestPower
        power_test = design.get("power_test")
        if power_test not in {"independent_t", "paired_t"}:
            raise ValueError("power_test must be independent_t or paired_t")
        effect = float(design.get("effect_size", 0))
        if not math.isfinite(effect) or effect <= 0:
            raise ValueError("A positive prespecified standardized effect size is required")
        ratio = float(design.get("ratio", 1))
        if not math.isfinite(ratio) or ratio <= 0 or (power_test == "paired_t" and ratio != 1):
            raise ValueError("Invalid allocation ratio")
        model = TTestIndPower() if power_test == "independent_t" else TTestPower()
        args = {"effect_size": effect, "alpha": alpha, "alternative": "two-sided"}
        if power_test == "independent_t":
            args["ratio"] = ratio
        if ("nobs" in design) == ("target_power" in design):
            raise ValueError("Specify exactly one of nobs or target_power")
        if "nobs" in design:
            n = design["nobs"]
            if isinstance(n, bool) or not isinstance(n, int) or n < 2:
                raise ValueError("nobs must be an integer >=2 (group 1 units or complete pairs)")
            value = float(model.power(nobs1=n, **args) if power_test == "independent_t" else model.power(nobs=n, **args))
            result = {"power": value, "nobs": n}
        else:
            target = float(design["target_power"])
            if not alpha < target < 1:
                raise ValueError("target_power must lie strictly between alpha and 1")
            n = float(model.solve_power(power=target, **args))
            if not math.isfinite(n):
                raise ValueError("Power solver did not return a finite sample size")
            rounded = math.ceil(n)
            result = {"nobs_continuous": n, "nobs_required": rounded, "target_power": target}
            if power_test == "independent_t":
                result["group_2_required"] = math.ceil(rounded * ratio)
        return {"applicable": True, "test": TESTS[method], "power_test": power_test,
                "effect_size": effect, "ratio": ratio, **result,
                "notes": ["Prospective calculation for a prespecified effect; this is not post-hoc observed power.",
                          "Independent t power assumes common population variance; paired power uses the SD of within-pair differences."]}
    unit = design["experimental_unit"]
    if unit not in frame:
        raise ValueError("Randomization experimental_unit must name the unit ID column")
    block = design.get("block")
    if frame[unit].isna().any() or frame[unit].duplicated().any() or (block and frame[block].isna().any()):
        raise ValueError("Randomization requires unique nonmissing experimental units and complete block labels")
    treatments = _json_list(design.get("treatments"), name="design.treatments")
    if not 2 <= len(treatments) <= 50 or len(treatments) != len(set(treatments)):
        raise ValueError("Specify 2 to 50 unique treatment labels")
    seed = design.get("seed")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2 ** 32:
        raise ValueError("A reproducible integer seed in [0,2**32) is required")
    rng = _np().random.default_rng(seed)
    assignments = []
    partitions = frame.groupby(block, sort=False) if block else [(None, frame)]
    for label, rows in partitions:
        if len(rows) % len(treatments):
            raise ValueError("Each block/unit pool must be divisible by the treatment count for balanced assignment")
        labels = rng.permutation(treatments * (len(rows) // len(treatments)))
        for row_id, original, treatment in zip(rows.index, rows[unit], labels):
            assignments.append({"row_id": row_id, "unit": original, "block": label, "treatment": str(treatment)})
    return {"applicable": True, "test": TESTS[method], "seed": seed,
            "rng": "numpy.default_rng/PCG64", "assignments": assignments,
            "notes": ["Generated allocation is a design proposal; no treatment was applied and no scientific outcome was inferred."]}


def _compute_test(frame: Any, requested: str, metric_cols: list[str], group_cols: list[str],
                  pair_cols: list[str], alpha: float, design: dict[str, Any]) -> dict[str, Any]:
    if requested in {"regression", "factorial", "blocked", "repeated", "mixed", "power", "randomization"}:
        try:
            _validated_design(frame, design, requested, group_cols, pair_cols)
            if requested in {"power", "randomization"}:
                return _design_calculation(frame, requested, design, alpha)
            if len(metric_cols) != 1:
                raise ValueError("Each model fit requires exactly one response per calculation")
            if requested == "repeated":
                return _repeated_design(frame, metric_cols[0], design, alpha)
            return _linear_design(frame, metric_cols[0], design, requested, alpha)
        except Exception as exc:
            return _not_applicable(str(exc), design=design)
    if design:
        # Legacy test arguments cannot consume additional dependence or factor structure.
        allowed = {"experimental_unit", "independent_units", "kind"}
        if set(design) - allowed or design.get("kind", requested) != requested:
            return _not_applicable("Declared design cannot be represented by this test; no design fields were ignored.")
        if "independent_units" in design and design["independent_units"] is not True:
            return _not_applicable("Independent experimental units have not been confirmed.")
        unit = design.get("experimental_unit")
        if unit is not None and (not isinstance(unit, str) or not unit.strip()):
            return _not_applicable("experimental_unit must be nonempty text")
        if requested != "ttest_rel" and unit in frame:
            if frame[unit].isna().any() or frame[unit].duplicated().any():
                return _not_applicable("Experimental unit IDs are repeated/missing; an independent-row test would pseudoreplicate units.")
    if len(group_cols) > 1 and requested != "chi2":
        return _not_applicable("当前检验不支持两个分组字段的多因素设计，不能忽略第二个字段。")
    if len(pair_cols) > 1:
        return _not_applicable("当前不支持复合配对键，不能忽略第二个配对字段。")
    if pair_cols and requested != "ttest_rel":
        return _not_applicable("paired_by_json 仅适用于 ttest_rel，不能按独立样本忽略配对设计。")
    if requested == "chi2":
        return (_contingency(frame, *group_cols, alpha) if len(group_cols) == 2 else
                _not_applicable("卡方检验需要两个分类字段。"))
    if requested in {"pearson", "spearman"}:
        if group_cols:
            return _not_applicable("Correlation does not support grouping; grouped columns cannot be ignored.")
        if len(metric_cols) != 2:
            return _not_applicable("Correlation requires exactly two numeric columns per calculation.")
        return {**_correlation(frame, *metric_cols, requested, alpha), "columns": metric_cols}
    if len(metric_cols) != 1:
        return _not_applicable("No numeric response was specified")
    value_col = metric_cols[0]
    if not group_cols:
        return _not_applicable("组间/配对检验需要 group_by_json 指定处理组。")
    working = frame.copy()
    working[value_col] = _numeric(working[value_col])
    if requested == "ttest_rel":
        return (_paired_test(working, value_col, group_cols[0], pair_cols[0], alpha) if pair_cols else
                _not_applicable("配对检验需要 paired_by_json 指定配对键。"))
    usable = working.dropna(subset=[value_col, group_cols[0]])
    levels = list(usable[group_cols[0]].unique())
    if len(levels) > _MAX_GROUP_LEVELS:
        return _not_applicable("Group count exceeds the safe comparison limit")
    named = {level: usable.loc[usable[group_cols[0]] == level, value_col] for level in levels}
    if requested in {"ttest_ind", "mannwhitneyu"}:
        if len(levels) != 2:
            return _not_applicable("此检验需要恰好两组。")
        result = _independent_two_sample(*named.values(), alpha, requested)
    elif requested == "welch_anova":
        try:
            from statsmodels.stats.oneway import anova_oneway
            if len(named) < 2 or any(len(v) < 2 or v.var(ddof=1) <= 0 for v in named.values()):
                raise ValueError("Welch ANOVA requires at least two groups, each with n>=2 and positive variance")
            fit = anova_oneway([g.to_numpy() for g in named.values()], use_var="unequal", welch_correction=True)
            _require_finite_result(fit.statistic, fit.pvalue)
            result = {"applicable": True, "test": TESTS[requested], "statistic": float(fit.statistic),
                      "p": float(fit.pvalue), "df_num": float(fit.df_num), "df_den": float(fit.df_denom),
                      "notes": ["Welch comparison of means selected from the design, without a preliminary variance test."]}
        except Exception as exc:
            result = _not_applicable(str(exc))
    else:
        result = _one_way_anova(list(named.values()), alpha, requested)
    result.update(groups=[str(v) for v in levels], value_column=value_col)
    if requested in {"anova", "welch_anova", "kruskal"} and result.get("applicable"):
        result["pairwise"] = _pairwise(named, alpha, requested != "kruskal")
    return result


def _inference_nodes(result: dict[str, Any]):
    if "p" in result:
        yield result
    for key in ("pairwise", "coefficients", "effects"):
        for item in result.get(key, []):
            if "p" in item:
                yield item


@tool
def analyze_research_data(file_index: int = 0, sheet_name: str = "",
                           header_row: int = 1,
                           group_by_json: str | list[str] = "",
                           metrics_json: str | list[str] = "",
                           test: str = "",
                           paired_by_json: str | list[str] = "",
                           alpha: float = 0.05,
                           output_format: str = "none",
                           title: str = "统计结果",
                           design_json: str | dict = "",
                           units_json: str | dict = "",
                           dataset_ref: str | dict = "") -> str:
    """对本轮上传的表格做真实统计计算；不给不存在的结果。

    先用 `inspect_uploaded_table` 看清列名与类型，再调用本工具。

    file_index/sheet_name/header_row：与 inspect_uploaded_table 相同，指定要分析的表。
    dataset_ref：可直接引用已保存的数据快照（对象或ID），重新授权后重算；复用原行ID和单位，
    不需要重新上传文件。power 可仅使用 design_json 参数计算，不要求观测数据。
    group_by_json：描述汇总可用 1–2 个字段；chi2 需要两个分类字段。
    metrics_json：数值响应列列表；每个指标均计算，相关分析覆盖全部列对，统一 Holm 校正。
    留空使用全部可用数值响应列，排除分组、配对和声明的设计键；超过预算明确拒绝。
    test：ttest_ind（Welch）、ttest_rel、anova（经典等方差）、welch_anova、kruskal、
    mannwhitneyu、pearson、spearman、chi2，或 regression/factorial/blocked/repeated/mixed/
    power/randomization（需要明确 design_json）；留空只做描述统计。
    paired_by_json：配对键列名，仅 ttest_rel 需要；配对不完整会明确拒绝。
    alpha：显著性水平，默认 0.05。
    output_format：none / csv / xlsx；非 none 时把结果表作为文件交付。

    design_json：对象，kind 与 test 相同，experimental_unit 指实验单位，independent_units=true
    确认单位独立。regression 用 predictors 数值列，factorial 用 factors 分类列及 interactions；
    blocked 用 factors=[处理] 与 block；repeated 用 subject 与 within=[时间]，必须完整平衡；
    mixed 用 subject、predictors/factors，支持随机截距。power 用 power_test=independent_t/paired_t、
    正数 effect_size 与 nobs 或 target_power；randomization 用实验单位ID列、treatments 列表、seed，
    可加 block，返回平衡分配。未支持的结构会明确拒绝。units_json 将列名映射到单位文本。
    返回全精度数值、假设证据、排除行、覆盖及 owner 绑定的 dataset_ref/analysis_result_ref；
    图表和文档应引用同一结果版本。假设检验不能证明正态/等方差，不自动切换检验。
    """
    from src.research.context import get_request_tables
    bound_snapshot = None
    parameters_only = str(test or "").strip().lower() == "power" and not get_request_tables() and not dataset_ref
    try:
        from src.research.tools import _load_frame
        if dataset_ref:
            from src.core.tracing import get_user_id
            from src.research.analysis_results import load_dataset_snapshot
            selected_ref = json.loads(dataset_ref) if isinstance(dataset_ref, str) and dataset_ref.lstrip().startswith("{") else dataset_ref
            bound_snapshot = load_dataset_snapshot(get_user_id(), selected_ref)
            frame = bound_snapshot.to_frame()
            meta = dict(bound_snapshot.to_dict()["source"])
        elif parameters_only:
            frame, meta = _pd().DataFrame(), {"name": "Prospective design parameters", "header_row": 0,
                "header_rows": [], "source_type": "design_parameters", "truncated": False}
        else:
            frame, meta = _load_frame(file_index, sheet_name, header_row, None)
    except Exception as exc:
        return f"[STATS_INPUT_ERROR] 读取表格失败：{exc}"

    if bound_snapshot and str(test or "").strip().lower() == "power" and meta.get("source_type") == "design_parameters":
        parameters_only = True
    if frame is None or (frame.empty and not parameters_only):
        return "[STATS_EMPTY] 表格没有数据行可供统计。"

    try:
        alpha = float(alpha)
        if not math.isfinite(alpha) or not 0 < alpha < 1:
            return "[STATS_ARG] alpha 必须是 0 与 1 之间的有限数值。"
        group_cols = _json_list(group_by_json, name="group_by_json")
        metric_cols = _json_list(metrics_json, name="metrics_json")
        pair_cols = _json_list(paired_by_json, name="paired_by_json")
        design = _json_object(design_json, "design_json")
        for key in ("predictors", "factors", "within", "treatments"):
            if key in design:
                design[key] = _design_columns(design, key)
        units = _json_object(units_json, "units_json")
        if bound_snapshot:
            original_units = bound_snapshot.to_dict()["units"]
            if any(original_units.get(col) != unit for col, unit in units.items()):
                raise ValueError("A snapshot's units are immutable; unit changes require an explicitly transformed dataset")
            units = original_units
        if any(c not in frame.columns or not isinstance(v, str) for c, v in units.items()):
            raise ValueError("units_json must map existing columns to unit text")
    except (TypeError, ValueError) as exc:
        return f"[STATS_ARG] 参数无效：{exc}"
    fmt = str(output_format or "none").strip().lower()
    if fmt not in {"none", "csv", "xlsx"}:
        return "[STATS_ARG] output_format 必须为 none / csv / xlsx。"
    if any(len(columns) != len(set(columns)) for columns in (group_cols, metric_cols, pair_cols)):
        return "[STATS_ARG] 同一字段列表不能重复指定列名。"
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
        design_keys = [design.get(k) for k in ("experimental_unit", "subject", "block")]
        design_keys += _design_columns(design, "predictors") + _design_columns(design, "factors") + _design_columns(design, "within")
        metric_cols = [str(c) for c in frame.columns
                       if c not in [*group_cols, *pair_cols, *design_keys]
                       and _numeric(frame[c]).dropna().shape[0] > 0]
    if len(metric_cols) > 32:
        return "[STATS_ARG] At most 32 metrics can be requested; explicitly select a smaller family."

    requested = str(test or "").strip().lower()
    if requested and requested not in TESTS:
        return (f"[STATS_ARG] 不支持的检验方式「{test}」；可用："
                f"{', '.join(sorted(TESTS))}。")

    for column in metric_cols:
        values = _numeric(frame[column]).dropna()
        if not _np().isfinite(values.to_numpy(dtype=float)).all():
            return f"[STATS_ARG] 数值列「{column}」含有无穷值；请先修正输入。"

    payload: dict[str, Any] = {
        "source": {"name": str(meta.get("name") or ""),
                   "sheet": str(meta.get("sheet") or ""),
                   "rows": int(frame.shape[0]), "columns": int(frame.shape[1]),
                   "truncated": bool(meta.get("truncated", False)),
                   "truncation_reasons": meta.get("truncation_reasons") or [],
                   **{key: meta[key] for key in (
                       "source_rows", "source_columns", "processed_rows", "processed_columns")
                      if key in meta}},
        "requested_test": requested,
        "columns": [str(c) for c in frame.columns],
        "descriptive": {},
        "grouped": [],
        "test": None,
        "alpha": float(alpha),
    }

    if payload["source"]["truncated"] and str(meta.get("name", "")).lower().endswith((".csv", ".tsv")):
        payload["source"]["source_rows"] = None
        payload["source"]["row_count_caveat"] = "CSV parser stopped at its budget; the full source row count is unknown."
    for column in metric_cols:
        payload["descriptive"][column] = _describe(frame[column])

    if group_cols:
        payload["grouped"] = _group_summary(frame, group_cols, metric_cols)

    # One calculation per response (or every correlation pair). All inferential
    # probabilities, including model coefficients and post-hoc comparisons,
    # share a declared Holm family across the complete requested output.
    import itertools
    header_span = int(meta.get("header_row", header_row))
    row_ids = list(frame.index) if bound_snapshot else [int(i) + header_span + 1 for i in frame.index]
    frame = frame.copy()
    frame.index = row_ids
    payload["tests"] = []
    payload["exclusions"] = []
    if requested:
        if requested in {"chi2", "power", "randomization"}:
            jobs = [metric_cols]
        elif requested in {"pearson", "spearman"}:
            jobs = [list(p) for p in itertools.combinations(metric_cols, 2)] if len(metric_cols) >= 2 else [metric_cols]
            if len(jobs) > 120:
                return "[STATS_ARG] Correlation family exceeds 120 pairs; explicitly select fewer metrics."
        else:
            jobs = [[col] for col in metric_cols] or [[]]
        for cols in jobs:
            result = _compute_test(frame, requested, cols, group_cols, pair_cols, alpha, design)
            result["metrics"] = cols if requested not in {"chi2", "power", "randomization"} else []
            used = [] if requested in {"power", "randomization", "chi2"} else cols
            used = list(dict.fromkeys(used + _design_columns(design, "predictors")))
            keys = group_cols + pair_cols + _design_columns(design, "factors") + _design_columns(design, "within")
            keys += [design[k] for k in ("block", "subject", "experimental_unit")
                     if isinstance(design.get(k), str) and design[k] in frame]
            keys = list(dict.fromkeys(c for c in keys if c in frame))
            numeric_mask = _pd().Series(True, index=frame.index)
            for col in used:
                if col in frame:
                    numeric_mask &= _numeric(frame[col]).notna()
            key_mask = frame[keys].notna().all(axis=1)
            valid = numeric_mask & key_mask
            result["candidate_row_ids"] = list(frame.index[valid])
            result["included_row_ids"] = list(frame.index[valid]) if result.get("applicable") and requested != "power" else []
            result["not_run_row_ids"] = list(frame.index[valid]) if not result.get("applicable") else []
            result["data_usage"] = "design_parameters_only" if requested == "power" else "observed_rows"
            excluded = [{"row_id": idx, "reason": "missing_or_nonnumeric_numeric_variable" if not numeric_mask[idx]
                         else "missing_design_key"} for idx in frame.index[~valid]]
            result["excluded_rows"] = excluded
            payload["exclusions"].append({"metrics": result["metrics"], "rows": excluded})
            payload["tests"].append(result)
        nodes = [node for result in payload["tests"] for node in _inference_nodes(result)]
        for node, adjusted in zip(nodes, _holm([float(node["p"]) for node in nodes])):
            node["p_holm"] = adjusted
            node["significant"] = bool(adjusted < alpha)
        payload["multiplicity"] = {"method": "Holm", "hypotheses": len(nodes),
                                    "family": "all requested metrics, coefficients, effects and pairwise comparisons",
                                    "intervals": "pointwise; not multiplicity-adjusted"}
        payload["test"] = payload["tests"][0] if len(payload["tests"]) == 1 else {
            "applicable": all(r.get("applicable") for r in payload["tests"]),
            "test": TESTS[requested], "results": payload["tests"],
            "reason": "See every requested result in tests; no metric was silently omitted."}
    payload["design"] = {"group_columns": group_cols, "pair_columns": pair_cols,
                          "declared": design, "units": units,
                          "independence": "declared" if design.get("independent_units") is True else "not_verified_from_data"}
    payload["profile"] = {col: {"dtype": str(frame[col].dtype), "missing": int(frame[col].isna().sum()),
                                "unit": units.get(col), "unique": int(frame[col].nunique())}
                           for col in frame.columns}
    payload["coverage"] = {
        "processed_rows": len(frame), "original_row_ids": row_ids,
        "row_identity_kind": "table_record_number_including_headers",
        "source_truncated": payload["source"]["truncated"],
        "source_limitations": payload["source"]["truncation_reasons"],
        "requested_metrics": metric_cols,
        "requested_calculations": len(payload["tests"]),
        "completed_calculations": sum(bool(r.get("applicable")) for r in payload["tests"]),
        "rejected_calculations": sum(not r.get("applicable") for r in payload["tests"]),
    }
    payload["presentation"] = {"significant_digits": 6, "values": "full_precision",
                               "confidence_intervals": "pointwise", "effect_interpretations": []}
    for index, result in enumerate(payload["tests"]):
        effect = result.get("effect_size")
        if isinstance(effect, dict) and "interpretation" in effect:
            payload["presentation"]["effect_interpretations"].append({
                "calculation": index, "text": effect.pop("interpretation")})
    for col in metric_cols:
        vals = _numeric(frame[col]).dropna()
        payload["descriptive"][col]["confidence_interval"] = _mean_interval(vals, alpha) if len(vals) > 1 else None
        payload["descriptive"][col]["excluded_row_ids"] = list(frame.index[_numeric(frame[col]).isna()])
    for row in payload["grouped"]:
        n, se = row["n"], row.get("se")
        margin = float(_scipy_stats().t.ppf(1 - alpha / 2, n - 1)) * se if n > 1 else None
        row["confidence_interval"] = ({"level": 1 - alpha, "low": row["mean"] - margin,
                                       "high": row["mean"] + margin, "method": "Student t", "df": n - 1}
                                      if margin is not None else None)

    # ── optional file ─────────────────────────────────────────────────────
    payload = _finite_json(payload)
    # Persist first: every exported report can name the exact computed version.
    # Publication failure cannot erase the successful scientific calculation.
    payload["snapshot_status"] = "failed"
    try:
        import hashlib
        from src.core.tracing import get_user_id
        from src.research.analysis_results import persist_dataset_snapshot, persist_analysis_result
        owner = get_user_id()
        if bound_snapshot:
            snapshot = bound_snapshot
        else:
            item = (get_request_tables()[file_index] if not parameters_only else {
                "data": json.dumps({"design": design, "alpha": alpha}, sort_keys=True, ensure_ascii=False).encode("utf-8")})
            source = {**payload["source"], "file_id": (item.get("file_id") or
                      "upload:" + hashlib.sha256(item.get("data") or b"").hexdigest()) if not parameters_only else None,
                      "sha256": hashlib.sha256(item.get("data") or b"").hexdigest(),
                      "version": item.get("version", 1),
                      "source_type": "design_parameters" if parameters_only else "attachment",
                      "header_row": header_span, "header_rows": meta.get("header_rows", [header_span]),
                      "row_identity_kind": payload["coverage"]["row_identity_kind"]}
            snapshot = persist_dataset_snapshot(owner, frame, source=source, design=payload["design"],
                units=units, coverage=payload["coverage"], row_ids=row_ids)
        payload["dataset_ref"] = snapshot.ref
        analysis = persist_analysis_result(owner, snapshot.ref,
            outputs={k: v for k, v in payload.items() if k not in {"presentation", "dataset_ref", "snapshot_status"}},
            parameters={"test": requested, "alpha": alpha, "metrics": metric_cols,
                        "group_by": group_cols, "paired_by": pair_cols},
            design=payload["design"], exclusions=payload["exclusions"], coverage=payload["coverage"],
            presentation=payload["presentation"])
        payload["analysis_result_ref"] = analysis.ref
        payload["snapshot_status"] = "complete"
    except Exception as exc:
        payload["snapshot_error"] = str(exc)
    payload["export_status"] = "not_requested"
    if fmt in {"csv", "xlsx"}:
        try:
            from src.research.tools import _safe_name, _save_artifact, _safe_spreadsheet_value
            import io
            import csv as _csv

            rows: list[dict[str, Any]] = []
            for ref_name in ("analysis_result_ref", "dataset_ref"):
                if ref_name in payload:
                    for field, value in payload[ref_name].items():
                        rows.append({"区块": "结果版本", "指标": f"{ref_name}.{field}", "值": value})
            for column, stats in payload["descriptive"].items():
                row = {"区块": "描述统计", "分组": "", "指标": column}
                row.update(stats)
                rows.append(row)
            for row in payload["grouped"]:
                item = {"区块": "分组汇总"}
                item.update(row)
                rows.append(item)
            for test_rows in payload["tests"]:
                effect = test_rows.get("effect_size") or {}
                rows.append({"区块": "检验", "分组": "", "指标": test_rows.get("test", requested),
                             "响应列": ", ".join(test_rows.get("metrics", [])),
                             "p": test_rows.get("p"), "p_holm": test_rows.get("p_holm"),
                             "显著": test_rows.get("significant"),
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
                for term in test_rows.get("coefficients", []) + test_rows.get("effects", []):
                    rows.append({"区块": "模型项", "响应列": ", ".join(test_rows.get("metrics", [])), **term})
                if requested in {"power", "randomization"}:
                    rows.append({"区块": "设计计算", "计算结果": test_rows})
            if not rows:
                raise ValueError("没有可导出的统计结果。")
            def export_cell(value):
                if isinstance(value, (dict, list)):
                    value = json.dumps(value, ensure_ascii=False, allow_nan=False)
                return _safe_spreadsheet_value(value)
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
                sheet.append([_safe_spreadsheet_value(column) for column in columns])
                for row in rows:
                    sheet.append([export_cell(row.get(column, "")) for column in columns])
                sheet.freeze_panes = "A2"
                output = io.BytesIO()
                workbook.save(output)
                payload_bytes = output.getvalue()
                mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            else:
                output = io.StringIO(newline="")
                writer = _csv.writer(output)
                writer.writerow([_safe_spreadsheet_value(column) for column in columns])
                for row in rows:
                    writer.writerow([export_cell(row.get(column, "")) for column in columns])
                payload_bytes = output.getvalue().encode("utf-8-sig")
                mime = "text/csv; charset=utf-8"
            url = _save_artifact(filename, payload_bytes, mime)
            payload["download_url"] = url
            payload["export_status"] = "complete"
        except Exception as exc:
            payload["export_status"] = "failed"
            payload["export_error"] = str(exc)

    result = payload.get("test") or {}
    statistics_status = "complete"
    status, reason, coverage = "complete", "", "统计计算已完成"
    if requested and not result.get("applicable"):
        if any(r.get("applicable") for r in payload["tests"]):
            statistics_status = "partial"
            status, reason, coverage = "partial", "coverage_incomplete", "部分指标已计算，另一些指标明确不适用"
        else:
            statistics_status = "not_applicable"
            status, reason, coverage = "not_run", "scope_limited", "描述统计已返回；请求的检验不适用"
    elif not requested and not payload["descriptive"]:
        statistics_status = "no_results"
        status, reason, coverage = "no_results", "no_processable_table", "没有可用数值列"
    elif any(not pair.get("applicable") for r in payload["tests"] for pair in r.get("pairwise", [])):
        statistics_status = "partial"
        status, reason, coverage = "partial", "coverage_incomplete", "部分两两比较不适用"
    if payload["source"]["truncated"]:
        status, reason, coverage = "partial", "coverage_incomplete", "仅对解析预算内的数据计算；源表已截断"
    if payload["export_status"] == "failed":
        status, reason, coverage = "partial", "tool_error", "统计报告已返回；请求的文件导出失败"
    payload["statistics_status"] = statistics_status
    if payload["snapshot_status"] == "failed" and status == "complete":
        status, reason, coverage = "partial", "tool_error", "计算已完成；共享结果快照未能持久保存"
    payload["result_refs"] = [payload[key] for key in ("analysis_result_ref", "dataset_ref") if key in payload]
    excluded_ids = {r["row_id"] for item in payload["exclusions"] for r in item["rows"]}
    payload["facts"] = {
        "method": TESTS.get(requested, "描述统计"), "n": len(frame),
        "exclusions_count": len(excluded_ids),
        "confidence_interval": (payload["tests"][0].get("confidence_interval")
                                if len(payload["tests"]) == 1 else None),
        "coverage": {k: v for k, v in payload["coverage"].items() if k != "original_row_ids"},
        "calculations": [{"method": r.get("test", requested), "metrics": r.get("metrics", []),
                          "applicable": bool(r.get("applicable")),
                          "n": r.get("n_pairs", r.get("n_subjects", len(r.get("included_row_ids", [])))),
                          "confidence_interval": r.get("confidence_interval"),
                          "exclusions_count": len(r.get("excluded_rows", []))}
                         for r in payload["tests"]],
        "assumption_evidence": "diagnostics do not prove assumptions; independence requires experimental design",
    }
    payload["_tool_outcome"] = {
        "status": status, "reason_code": reason, "coverage": coverage,
        "artifacts": [payload["download_url"]] if payload.get("download_url") else [],
        "facts": payload["facts"], "result_refs": payload["result_refs"],
    }
    return json.dumps(payload, ensure_ascii=False, default=str, allow_nan=False)


__all__ = ["TESTS", "analyze_research_data"]
