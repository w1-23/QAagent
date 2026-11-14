"""One explicit X against named numeric responses, one labeled panel per Y."""
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

mode = parameters.get("input_format", "csv")
if mode == "dataset":
    frame = load_dataset(data)
elif mode == "excel":
    frame = pd.read_excel(data, sheet_name=parameters.get("sheet", 0))
elif mode == "csv":
    frame = pd.read_csv(data)
else:
    raise ValueError("input_format must be csv, excel or dataset")
x = parameters["x"]
ys = parameters["ys"]
if not isinstance(ys, list) or not 1 <= len(ys) <= 6 or len(set(ys)) != len(ys):
    raise ValueError("Declare one to six distinct response columns")
if x in ys or any(column not in frame.columns for column in [x] + ys):
    raise ValueError("Explicit X and response columns must exist and be distinct")
labels = parameters.get("labels", {})
if not isinstance(labels, dict):
    raise ValueError("labels must map field names to labels including known units")
columns = min(3, len(ys))
rows = (len(ys) + columns - 1) // columns
fig, axes = plt.subplots(rows, columns, figsize=(4.0 * columns, 3.5 * rows),
                         squeeze=False, constrained_layout=True)
coverage = []
for index, response in enumerate(ys):
    ax = axes.flat[index]
    numeric = frame[[x, response]].apply(pd.to_numeric, errors="raise")
    selected = numeric.dropna()
    if selected.empty:
        raise ValueError("No paired numeric observations for " + response)
    ax.scatter(selected[x], selected[response], s=28, color="#2a78d6",
               edgecolors="white", linewidths=0.5, alpha=0.9)
    ax.set_xlabel(labels.get(x, x))
    ax.set_ylabel(labels.get(response, response))
    ax.set_title(str(labels.get(response, response)))
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(alpha=0.15, linewidth=0.5)
    ax.set_axisbelow(True)
    coverage.append({"x": x, "y": response, "n": len(selected),
                     "missing_pair_rows": int(len(frame) - len(selected))})
for index in range(len(ys), rows * columns):
    axes.flat[index].set_visible(False)
emit.figure("relations", fig)
emit.report({"input_rows": len(frame), "panels": coverage,
             "inference": "descriptive_only", "outliers_removed": False})
