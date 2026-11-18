"""One explicit X against named numeric responses, one labeled panel per Y."""
import pandas as pd

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
coverage = []
panels = []
for index, response in enumerate(ys):
    numeric = frame[[x, response]].apply(pd.to_numeric, errors="raise")
    selected = numeric.dropna()
    if selected.empty:
        raise ValueError("No paired numeric observations for " + response)
    panel = selected.rename(columns={x: "x", response: "y"}).copy()
    panel["panel"] = index
    panel["x_label"] = str(labels.get(x, x))
    panel["y_label"] = str(labels.get(response, response))
    panels.append(panel)
    coverage.append({"x": x, "y": response, "n": len(selected),
                     "missing_pair_rows": int(len(frame) - len(selected))})
emit.table("relations_data", pd.concat(panels, ignore_index=True), parents=["data"])
emit.report({"input_rows": len(frame), "panels": coverage,
             "inference": "descriptive_only", "outliers_removed": False})
