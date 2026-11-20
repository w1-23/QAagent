"""Joint scatter with marginal histograms from the same explicit X/Y values."""
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
                         "joint and marginal panels require complete pairs")
    return values


def _bin(values, bins):
    low = float(values.min())
    high = float(values.max())
    if low == high:
        edges = np.linspace(low - 0.5, high + 0.5, bins + 1)
    else:
        edges = np.histogram_bin_edges(values, bins=bins, range=(low, high))
    counts, edges = np.histogram(values, bins=edges)
    return counts, edges


mode = parameters.get("input_format", "csv")
if mode == "dataset":
    frame = load_dataset(data)
elif mode == "excel":
    frame = pd.read_excel(data, sheet_name=parameters.get("sheet", 0))
elif mode == "csv":
    frame = pd.read_csv(data)
else:
    raise ValueError("input_format must be csv, excel or dataset")

x_name = parameters.get("x")
y_name = parameters.get("y")
if (not isinstance(x_name, str) or not x_name
        or not isinstance(y_name, str) or not y_name):
    raise ValueError("Declare explicit x and y columns")
if x_name == y_name:
    raise ValueError("x and y columns must be distinct")
bins = parameters.get("bins", 30)
if isinstance(bins, bool) or not isinstance(bins, int) or not 1 <= bins <= 200:
    raise ValueError("bins must be an integer from 1 to 200")
x = _numeric(x_name, "x")
y = _numeric(y_name, "y")
count = len(x)
if count < 2:
    raise ValueError("Marginal plot requires at least two complete observations")
x_label = str(parameters.get("x_label", x_name))
y_label = str(parameters.get("y_label", y_name))

points = pd.DataFrame({
    "row_index": np.arange(count, dtype="int64"),
    "x": x,
    "y": y,
    "x_label": pd.Series([x_label] * count, dtype="object"),
    "y_label": pd.Series([y_label] * count, dtype="object"),
})
axis_rows = []
for axis_name, values in (("x", x), ("y", y)):
    counts, edges = _bin(values, bins)
    for index, number in enumerate(counts):
        axis_rows.append({
            "axis": axis_name,
            "bin_index": index,
            "bin_left": float(edges[index]),
            "bin_right": float(edges[index + 1]),
            "count": int(number),
        })
bins_frame = pd.DataFrame({
    "axis": pd.Series([row["axis"] for row in axis_rows], dtype="object"),
    "bin_index": np.array([row["bin_index"] for row in axis_rows], dtype="int64"),
    "bin_left": np.array([row["bin_left"] for row in axis_rows], dtype="float64"),
    "bin_right": np.array([row["bin_right"] for row in axis_rows], dtype="float64"),
    "count": np.array([row["count"] for row in axis_rows], dtype="int64"),
})
emit.table("marginal_points", points, parents=["data"])
emit.table("marginal_bins", bins_frame, parents=["data"])
emit.report({
    "input_rows": int(count),
    "plotted_rows": int(count),
    "excluded_rows": 0,
    "x_column": x_name,
    "y_column": y_name,
    "x_label": x_label,
    "y_label": y_label,
    "bins": bins,
    "x_min": float(x.min()),
    "x_max": float(x.max()),
    "y_min": float(y.min()),
    "y_max": float(y.max()),
    "shared_source": True,
    "missing_values_policy": "reject; joint and marginal panels use complete finite pairs",
    "histogram_policy": "numpy.histogram on the same values as the joint panel",
})
