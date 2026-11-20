"""Volcano plot from explicit effect and significance columns, all rows retained."""
import numpy as np
import pandas as pd


def _numeric(column):
    if not isinstance(column, str) or not column:
        raise ValueError("Every numeric role requires an explicit column name")
    if column not in frame.columns:
        raise ValueError("Column absent from input: " + repr(column))
    try:
        values = pd.to_numeric(frame[column], errors="raise").to_numpy(
            dtype="float64", na_value=np.nan)
    except (TypeError, ValueError) as exc:
        raise ValueError("Column " + column + " must contain only numeric values") from exc
    if not np.isfinite(values).all():
        raise ValueError("Column " + column + " contains missing or non-finite values; "
                         "no rows were dropped or imputed")
    return values


def _labels(column):
    if not isinstance(column, str) or not column:
        raise ValueError("label_column must be an explicit column name")
    if column not in frame.columns:
        raise ValueError("Column absent from input: " + repr(column))
    series = frame[column]
    if series.isna().any():
        raise ValueError("Column " + column + " contains missing feature labels")
    return series.astype(str).to_numpy(dtype=object)


def _number(key):
    value = parameters.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(key + " must be a declared number")
    number = float(value)
    if not np.isfinite(number):
        raise ValueError(key + " must be finite")
    return number


mode = parameters.get("input_format", "csv")
if mode == "dataset":
    frame = load_dataset(data)
elif mode == "excel":
    frame = pd.read_excel(data, sheet_name=parameters.get("sheet", 0))
elif mode == "csv":
    frame = pd.read_csv(data, float_precision="round_trip")
else:
    raise ValueError("input_format must be csv, excel or dataset")

effect_name = parameters.get("effect")
significance_name = parameters.get("significance")
if (not isinstance(effect_name, str) or not effect_name
        or not isinstance(significance_name, str) or not significance_name):
    raise ValueError("Declare explicit effect and significance columns")
if effect_name == significance_name:
    raise ValueError("Effect and significance columns must be distinct")
effect_label = str(parameters.get("effect_label", effect_name))
significance_label = str(parameters.get("significance_label", significance_name))
effect_threshold = _number("effect_threshold")
if effect_threshold <= 0.0:
    raise ValueError("effect_threshold must be greater than zero")
p_threshold = _number("p_threshold")
if not 0.0 < p_threshold <= 1.0:
    raise ValueError("p_threshold must lie in (0, 1]")
label_top = parameters.get("label_top", 0)
if isinstance(label_top, bool) or not isinstance(label_top, int) or not 0 <= label_top <= 200:
    raise ValueError("label_top must be an integer from 0 to 200")
label_column = parameters.get("label_column")
if label_column is not None:
    feature = _labels(label_column)
else:
    feature = np.array([str(index) for index in range(len(frame))], dtype=object)

effect = _numeric(effect_name)
p_value = _numeric(significance_name)
if (p_value <= 0.0).any() or (p_value > 1.0).any():
    raise ValueError("p-values must lie in (0, 1]; zero, negative and non-finite values "
                     "are rejected rather than clipped")
neg_log10_p = -np.log10(p_value)
significant = p_value <= p_threshold
direction = [
    "up" if flagged and value >= effect_threshold else
    "down" if flagged and value <= -effect_threshold else "not_significant"
    for value, flagged in zip(effect, significant)
]
label = [""] * len(frame)
if label_top:
    ranked = sorted((index for index in np.flatnonzero(significant)),
                    key=lambda index: (-float(neg_log10_p[index]), int(index)))
    for index in ranked[:label_top]:
        label[index] = feature[index]

count = len(frame)
table = pd.DataFrame({
    "feature": pd.Series(feature, dtype="object"),
    "effect": effect,
    "p_value": p_value,
    "neg_log10_p": neg_log10_p,
    "direction": pd.Series(direction, dtype="object"),
    "label": pd.Series(label, dtype="object"),
    "effect_threshold": np.full(count, effect_threshold, dtype="float64"),
    "neg_log10_threshold": np.full(count, -float(np.log10(p_threshold)), dtype="float64"),
    "effect_label": pd.Series([effect_label] * count, dtype="object"),
    "significance_label": pd.Series([significance_label] * count, dtype="object"),
})
emit.table("volcano_data", table, parents=["data"])
emit.report({
    "input_rows": int(count),
    "plotted_rows": int(count),
    "excluded_rows": 0,
    "effect_column": effect_name,
    "significance_column": significance_name,
    "effect_label": effect_label,
    "significance_label": significance_label,
    "effect_threshold": effect_threshold,
    "p_threshold": p_threshold,
    "counts": {
        "up": sum(item == "up" for item in direction),
        "down": sum(item == "down" for item in direction),
        "not_significant": sum(item == "not_significant" for item in direction),
    },
    "labels_selected": sum(item != "" for item in label),
    "p_value_policy": "reject outside (0, 1]; reject missing or non-finite",
    "outliers_removed": False,
    "inference": "threshold_classification_only",
})
