"""Dot plot from explicit category/group/size/color columns with a declared order."""
import numpy as np
import pandas as pd


def _numeric(column, role):
    if not isinstance(column, str) or not column:
        raise ValueError(role + " requires an explicit column name")
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


def _text(column, role):
    if not isinstance(column, str) or not column:
        raise ValueError(role + " requires an explicit column name")
    if column not in frame.columns:
        raise ValueError("Column absent from input: " + repr(column))
    series = frame[column]
    if series.isna().any():
        raise ValueError("Column " + column + " contains missing " + role + " values")
    return series.astype(str).to_numpy(dtype=object)


def _order(key, observed):
    raw = parameters.get(key)
    if raw is None:
        return list(observed)
    if (not isinstance(raw, list) or not raw
            or any(not isinstance(item, str) or not item for item in raw)):
        raise ValueError(key + " must be a nonempty list of category strings")
    if len(set(raw)) != len(raw):
        raise ValueError(key + " must not repeat a category")
    if set(raw) != set(observed):
        raise ValueError(key + " must name exactly the observed categories")
    return list(raw)


mode = parameters.get("input_format", "csv")
if mode == "dataset":
    frame = load_dataset(data)
elif mode == "excel":
    frame = pd.read_excel(data, sheet_name=parameters.get("sheet", 0))
elif mode == "csv":
    frame = pd.read_csv(data)
else:
    raise ValueError("input_format must be csv, excel or dataset")
if len(frame) == 0:
    raise ValueError("Dot plot requires at least one category-group pair")

category_name = parameters.get("category")
group_name = parameters.get("group")
size_name = parameters.get("size")
color_name = parameters.get("color")
roles = [category_name, group_name, size_name, color_name]
if any(not isinstance(item, str) or not item for item in roles):
    raise ValueError("Declare explicit category, group, size and color columns")
if len(set(roles)) != 4:
    raise ValueError("Category, group, size and color roles must be distinct columns")

categories = _text(category_name, "category")
groups = _text(group_name, "group")
size_values = _numeric(size_name, "size")
color_values = _numeric(color_name, "color")
pairs = list(zip(categories.tolist(), groups.tolist()))
if len(set(pairs)) != len(pairs):
    raise ValueError("Each category-group pair must occur exactly once; "
                     "duplicate dots are ambiguous and are not summed")

observed_categories = list(dict.fromkeys(categories.tolist()))
observed_groups = list(dict.fromkeys(groups.tolist()))
category_order = _order("category_order", observed_categories)
group_order = _order("group_order", observed_groups)
category_index_of = {name: index for index, name in enumerate(category_order)}
group_index_of = {name: index for index, name in enumerate(group_order)}

category_label = str(parameters.get("category_label", category_name))
group_label = str(parameters.get("group_label", group_name))
size_label = str(parameters.get("size_label", size_name))
color_label = str(parameters.get("color_label", color_name))
count = len(frame)
table = pd.DataFrame({
    "category": pd.Series(categories, dtype="object"),
    "group": pd.Series(groups, dtype="object"),
    "category_index": np.array([category_index_of[name] for name in categories], dtype="int64"),
    "group_index": np.array([group_index_of[name] for name in groups], dtype="int64"),
    "size_value": size_values,
    "color_value": color_values,
    "size_min": np.full(count, float(size_values.min()), dtype="float64"),
    "size_max": np.full(count, float(size_values.max()), dtype="float64"),
    "color_min": np.full(count, float(color_values.min()), dtype="float64"),
    "color_max": np.full(count, float(color_values.max()), dtype="float64"),
    "category_label": pd.Series([category_label] * count, dtype="object"),
    "group_label": pd.Series([group_label] * count, dtype="object"),
    "size_label": pd.Series([size_label] * count, dtype="object"),
    "color_label": pd.Series([color_label] * count, dtype="object"),
})
emit.table("dotplot_data", table, parents=["data"])
emit.report({
    "input_rows": int(count),
    "plotted_rows": int(count),
    "excluded_rows": 0,
    "category_column": category_name,
    "group_column": group_name,
    "size_column": size_name,
    "color_column": color_name,
    "category_order": category_order,
    "group_order": group_order,
    "order_policy": "explicit" if parameters.get("category_order") is not None else "first_appearance",
    "size_label": size_label,
    "color_label": color_label,
    "duplicates_summed": False,
    "missing_values_imputed": False,
})
