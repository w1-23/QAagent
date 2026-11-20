"""Paired observations for exactly two conditions with explicit exclusions."""
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


mode = parameters.get("input_format", "csv")
if mode == "dataset":
    frame = load_dataset(data)
elif mode == "excel":
    frame = pd.read_excel(data, sheet_name=parameters.get("sheet", 0))
elif mode == "csv":
    frame = pd.read_csv(data)
else:
    raise ValueError("input_format must be csv, excel or dataset")

subject_name = parameters.get("subject")
condition_name = parameters.get("condition")
value_name = parameters.get("value")
roles = [subject_name, condition_name, value_name]
if any(not isinstance(item, str) or not item for item in roles):
    raise ValueError("Declare explicit subject, condition and value columns")
if len(set(roles)) != 3:
    raise ValueError("Subject, condition and value roles must be distinct columns")
value_label = str(parameters.get("value_label", value_name))
policy = parameters.get("exclusion_policy", "reject")
if policy not in {"reject", "list"}:
    raise ValueError("exclusion_policy must be 'reject' or 'list'")

subjects = _text(subject_name, "subject")
conditions = _text(condition_name, "condition")
values = _numeric(value_name, "value")
count = len(values)
if count == 0:
    raise ValueError("Paired plot requires at least one observation")
observed_conditions = list(dict.fromkeys(conditions.tolist()))
if len(observed_conditions) != 2:
    raise ValueError("Paired plot requires exactly two conditions; observed "
                     + str(len(observed_conditions)))
raw_order = parameters.get("condition_order")
if raw_order is None:
    condition_order = observed_conditions
else:
    if (not isinstance(raw_order, list) or len(raw_order) != 2
            or any(not isinstance(item, str) or not item for item in raw_order)
            or len(set(raw_order)) != 2 or set(raw_order) != set(observed_conditions)):
        raise ValueError("condition_order must list exactly the two observed conditions")
    condition_order = list(raw_order)
condition_index_of = {name: index for index, name in enumerate(condition_order)}
subject_order = list(dict.fromkeys(subjects.tolist()))
subject_index_of = {name: index for index, name in enumerate(subject_order)}

present = {}
pair_counts = {}
for subject, condition in zip(subjects.tolist(), conditions.tolist()):
    present.setdefault(subject, set()).add(condition)
    pair_counts[(subject, condition)] = pair_counts.get((subject, condition), 0) + 1
extra_by_subject = {}
duplicate_conditions = {}
for (subject, condition), number in pair_counts.items():
    if number > 1:
        extra_by_subject[subject] = extra_by_subject.get(subject, 0) + number - 1
        duplicate_conditions.setdefault(subject, []).append(condition)
missing_by_subject = {subject: sorted(set(condition_order) - present[subject])
                      for subject in subject_order
                      if present[subject] != set(condition_order)}
excluded_subjects = set(extra_by_subject) | set(missing_by_subject)

if policy == "reject" and excluded_subjects:
    ordered = [name for name in subject_order if name in excluded_subjects]
    sample = ordered[:20]
    raise ValueError("Paired data contain duplicate or incomplete subjects; "
                     "no rows were dropped. Duplicate subjects: "
                     + str(sorted(extra_by_subject))
                     + "; incomplete subjects: " + str(sorted(missing_by_subject))
                     + "; sample: " + repr(sample)
                     + ". Use exclusion_policy='list' to exclude them explicitly.")

kept_indices = [index for index, subject in enumerate(subjects.tolist())
                if subject not in excluded_subjects]
if not kept_indices:
    raise ValueError("No complete pairs remain after declared exclusions")
kept_indices.sort(key=lambda index: (subject_index_of[subjects[index]],
                                     condition_index_of[conditions[index]]))
kept_count = len(kept_indices)
paired = pd.DataFrame({
    "subject": pd.Series([subjects[index] for index in kept_indices], dtype="object"),
    "condition": pd.Series([conditions[index] for index in kept_indices], dtype="object"),
    "condition_index": np.array([condition_index_of[conditions[index]]
                                 for index in kept_indices], dtype="int64"),
    "subject_index": np.array([subject_index_of[subjects[index]]
                               for index in kept_indices], dtype="int64"),
    "value": np.array([values[index] for index in kept_indices], dtype="float64"),
    "value_label": pd.Series([value_label] * kept_count, dtype="object"),
})
exclusion_rows = []
for subject in subject_order:
    if subject in extra_by_subject:
        exclusion_rows.append({"subject": subject, "reason": "duplicate_rows",
                               "detail": "duplicate subject-condition rows: "
                                         + str(extra_by_subject[subject])
                                         + " (condition: "
                                         + ", ".join(sorted(duplicate_conditions[subject]))
                                         + ")"})
    if subject in missing_by_subject:
        exclusion_rows.append({"subject": subject, "reason": "missing_condition",
                               "detail": "missing condition(s): "
                                         + ", ".join(missing_by_subject[subject])})
excluded_frame = pd.DataFrame({
    "subject": pd.Series([row["subject"] for row in exclusion_rows], dtype="object"),
    "reason": pd.Series([row["reason"] for row in exclusion_rows], dtype="object"),
    "detail": pd.Series([row["detail"] for row in exclusion_rows], dtype="object"),
})
emit.table("paired_data", paired, parents=["data"])
emit.table("paired_excluded", excluded_frame, parents=["data"])
emit.report({
    "input_rows": int(count),
    "paired_rows": int(kept_count),
    "subjects": int(len(subject_order)),
    "paired_subjects": int(kept_count // 2),
    "excluded_subjects": sorted(excluded_subjects, key=subject_index_of.get),
    "exclusion_policy": policy,
    "duplicate_subjects": sorted(extra_by_subject),
    "missing_condition_subjects": sorted(missing_by_subject),
    "condition_order": condition_order,
    "value_label": value_label,
    "missing_values_policy": "reject non-finite values; no imputation",
})
