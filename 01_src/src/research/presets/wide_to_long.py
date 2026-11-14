"""Explicit wide-to-long transformation; no inferred scientific roles."""
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
ids = parameters["id_columns"]
values = parameters["value_columns"]
if not isinstance(ids, list) or not isinstance(values, list) or not values:
    raise ValueError("Declare id_columns and nonempty value_columns lists")
if len(set(ids + values)) != len(ids + values):
    raise ValueError("Columns must be unique and identifier/value roles disjoint")
missing = [column for column in ids + values if column not in frame.columns]
if missing:
    raise ValueError("Fields absent from input: " + repr(missing))
variable = parameters.get("variable_name", "metric")
value = parameters.get("value_name", "value")
if variable == value or variable in ids or value in frame.columns:
    raise ValueError("Output field names conflict with existing fields")
tidy = frame.melt(id_vars=ids, value_vars=values, var_name=variable, value_name=value)
emit.table("tidy", tidy, parents=["data"])
emit.report({"input_rows": len(frame), "output_rows": len(tidy),
             "selected_columns": ids + values, "excluded_rows": 0,
             "operation": "wide_to_long", "missing_values_preserved": True})
