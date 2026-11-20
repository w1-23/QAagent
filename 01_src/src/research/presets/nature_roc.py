"""ROC curve from explicit score and binary truth columns with trapezoid AUC."""
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


mode = parameters.get("input_format", "csv")
if mode == "dataset":
    frame = load_dataset(data)
elif mode == "excel":
    frame = pd.read_excel(data, sheet_name=parameters.get("sheet", 0))
elif mode == "csv":
    frame = pd.read_csv(data)
else:
    raise ValueError("input_format must be csv, excel or dataset")

score_name = parameters.get("score")
truth_name = parameters.get("truth")
if (not isinstance(score_name, str) or not score_name
        or not isinstance(truth_name, str) or not truth_name):
    raise ValueError("Declare explicit score and truth columns")
if score_name == truth_name:
    raise ValueError("Score and truth columns must be distinct")
score_label = str(parameters.get("score_label", score_name))
truth_label = str(parameters.get("truth_label", truth_name))
score = _numeric(score_name)
truth = _numeric(truth_name)
if not np.isin(truth, (0.0, 1.0)).all():
    raise ValueError("truth must contain only binary 0/1 values; no recoding is inferred")
count = len(score)
if count == 0:
    raise ValueError("ROC requires at least one observation")
positives = int(truth.sum())
negatives = count - positives
if positives == 0 or negatives == 0:
    raise ValueError("ROC requires at least one positive and one negative observation")

order = np.argsort(-score, kind="stable")
fprs = [0.0]
tprs = [0.0]
true_positive = 0
false_positive = 0
for index in order:
    if truth[index] == 1.0:
        true_positive += 1
    else:
        false_positive += 1
    fprs.append(false_positive / negatives)
    tprs.append(true_positive / positives)
points = sorted(zip(fprs, tprs), key=lambda point: (point[0], point[1]))
auc = 0.0
for (x0, y0), (x1, y1) in zip(points, points[1:]):
    auc += (x1 - x0) * (y0 + y1) / 2.0

length = len(points)
table = pd.DataFrame({
    "fpr": np.array([point[0] for point in points], dtype="float64"),
    "tpr": np.array([point[1] for point in points], dtype="float64"),
    "auc": np.full(length, auc, dtype="float64"),
    "score_label": pd.Series([score_label] * length, dtype="object"),
    "truth_label": pd.Series([truth_label] * length, dtype="object"),
    "positive_count": np.full(length, positives, dtype="int64"),
    "negative_count": np.full(length, negatives, dtype="int64"),
    "tie_policy": pd.Series(["stable_descending_score_input_order"] * length, dtype="object"),
})
emit.table("roc_data", table, parents=["data"])
emit.report({
    "input_rows": int(count),
    "plotted_rows": int(count),
    "excluded_rows": 0,
    "score_column": score_name,
    "truth_column": truth_name,
    "score_label": score_label,
    "truth_label": truth_label,
    "positive_count": positives,
    "negative_count": negatives,
    "auc": auc,
    "ordering": "descending_score_stable_input_ties",
    "repeated_fpr_policy": "retained and sorted by tpr ascending",
    "binarization": "explicit 0/1 only",
})
