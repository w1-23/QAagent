"""Real generic table bytes, formula safety, and no silent row truncation."""
import csv
import io
import re

import pytest
from openpyxl import load_workbook
from src.core.tool_errors import ToolArgumentError
from src.research import tools
from src.research.test_single_report import isolated
from src.session import files


@pytest.mark.parametrize("fmt", ["csv", "tsv", "xlsx"])
def test_real_table_output_and_formula_safety(isolated, fmt):
    result = tools.export_table.func(title="合成数据", columns_json=["标识", "值"],
        rows_json=[["=1+1", 2], ["中文项", 0]], output_format=fmt)
    assert "已生成" in result
    entry = files.list_files("session-publish-001", 101)[0]
    path = files.get_account_file(entry["file_id"], 101)["path"]
    if fmt == "xlsx":
        wb = load_workbook(path)
        rows = list(wb.active.values)
        assert wb.active["A2"].data_type != "f"
        wb.close()
    else:
        rows = list(csv.reader(io.StringIO(path.read_text(encoding="utf-8-sig")), delimiter="\t" if fmt == "tsv" else ","))
    assert len(rows) == 3 and rows[1][0] == "'=1+1"
    assert rows[2][0] == "中文项" and str(rows[2][1]) == "0"


def test_large_or_wrong_shape_batch_is_rejected_before_publish(isolated):
    for rows in ([["value"]] * 5001, [[1, 2]], [{"unexpected": "value"}]):
        with pytest.raises(ToolArgumentError):
            tools.export_table.func(title="测试", columns_json=["x"], rows_json=rows)
    assert not (isolated / "state/artifacts").exists()
