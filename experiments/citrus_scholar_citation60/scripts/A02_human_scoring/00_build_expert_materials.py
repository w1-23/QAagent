#!/usr/bin/env python3
"""Build condition-blinded expert scoring directories from L1 judge packets."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys_path = ROOT / "scripts"
import sys

sys.path.insert(0, str(sys_path))
from A02_human_scoring.scoring_common import SCORE_FIELDS, sha256_file  # noqa: E402

FORBIDDEN_KEYS = {
    "run_id",
    "question_id",
    "condition",
    "internal_condition",
    "provider",
    "model",
    "adapter",
    "stratum",
    "target_chunk_id",
    "raw_run_sha256",
}
SCORE_HEADERS_CN = [
    "批次",
    "盲编号",
    "评审编号",
    "评分版本",
    "可答性判断正确",
    "答案正确",
    "证据可追溯",
    "致命错误",
    "字段理由代码JSON",
    "简短理由",
    "回答证据片段",
    "证据定位JSON",
    "评分状态",
    "问题编号",
    "评分时间UTC",
]


def protected_root() -> Path:
    value = os.environ.get("CITRUS_EXPERIMENT_L1_ROOT", "").strip()
    if not value:
        raise SystemExit("CITRUS_EXPERIMENT_L1_ROOT is required")
    path = Path(value).expanduser().resolve()
    if path.is_relative_to(ROOT.resolve()):
        raise SystemExit("expert materials must be stored outside the public Git tree")
    return path


def recursive_forbidden_key(value: object) -> str | None:
    if isinstance(value, dict):
        hit = FORBIDDEN_KEYS.intersection(value)
        if hit:
            return sorted(hit)[0]
        for child in value.values():
            found = recursive_forbidden_key(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = recursive_forbidden_key(child)
            if found:
                return found
    return None


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open("x", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_xlsx(
    path: Path,
    order_rows: list[dict],
    reviewer_id: str,
    score_version: str,
) -> None:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.worksheet.datavalidation import DataValidation
    except Exception as exc:
        raise SystemExit(f"openpyxl is required to build expert workbooks: {exc}")

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Expert scoring"
    sheet.append(SCORE_HEADERS_CN)
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
        cell.alignment = Alignment(horizontal="center", vertical="center")
    for order in order_rows:
        sheet.append(
            [
                order["batch_id"],
                order["blind_id"],
                reviewer_id,
                score_version,
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
            ]
        )
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    widths = [12, 16, 14, 12, 18, 14, 16, 14, 34, 40, 48, 34, 15, 14, 24]
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[
            sheet.cell(row=1, column=index).column_letter
        ].width = width
    binary_validation = DataValidation(
        type="list",
        formula1='"true,false,not_applicable"',
        allow_blank=True,
    )
    status_validation = DataValidation(
        type="list",
        formula1='"SCORED,UNRATEABLE,MISSING,INVALID"',
        allow_blank=True,
    )
    sheet.add_data_validation(binary_validation)
    sheet.add_data_validation(status_validation)
    binary_validation.add(f"E2:H{sheet.max_row}")
    status_validation.add(f"M2:M{sheet.max_row}")
    guide = workbook.create_sheet("Instructions")
    guide.append(["QA60 / independent Pilot expert scoring materials"])
    guide.append(["Protocol", "4.4.2"])
    guide.append(["Blind rule", "Do not infer or record B0/B1/B2, model, provider, run ID, or another reviewer's score."])
    guide.append(["NA rule", "For UNANSWERABLE questions, answer_correct and evidence_traceable must be not_applicable."])
    guide.append(["Reason rule", "Every substantive false field or fatal_error=true needs a fixed field-scoped reason code, short reason, answer quote, and locator."])
    guide.append(["Do not", "Do not browse, call tools, consult another reviewer, or edit original responses."])
    guide.column_dimensions["A"].width = 24
    guide.column_dimensions["B"].width = 120
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)


def instruction_text() -> str:
    return """# 专家评分说明

版本：`4.4.2`

1. 你只看到盲编号和回答材料，不知道条件、模型、提供方、运行编号、原题编号或另一名专家评分。
2. 不要联网、不要调用工具、不要与其他评审讨论，也不要尝试改动原始回答。
3. 每题只填写四个判断字段：`answerability_correct`、`answer_correct`、
   `evidence_traceable`、`fatal_error`。
4. 不可答题的 `answer_correct` 与 `evidence_traceable` 必须填 `not_applicable`。
5. 任何实质 false 或 fatal_error=true 必须给出字段级固定理由代码、简短理由、
   回答片段和证据定位。不要复制密钥、个人身份或私有 SOP 原文。
6. 专家不填写 EGTS 或 PASS/FAIL；最终值由确定性脚本计算。
7. 提交原始 XLSX 或 CSV，不要覆盖已提交文件。修订必须走追加式修订表。
8. 完成全部行后填写审查者确认文件，并核对 `05_完成检查清单.md`。
"""


def checklist_text(expected_rows: int) -> str:
    return f"""# 完成检查清单

- [ ] 已阅读 `00_评分说明.md` 和 `04_评分代码表.md`
- [ ] 已独立完成全部 {expected_rows} 行
- [ ] 没有查看条件、模型、运行编号或另一评审结果
- [ ] 不可答题的 `answer_correct` 与 `evidence_traceable` 均为 `not_applicable`
- [ ] 所有必要理由、回答片段和证据定位均非空
- [ ] 原始响应没有被改写
- [ ] 已填写 `reviewer_attestation.json`
- [ ] 已记录提交时间和文件 SHA-256
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet-root", default="judge_packets")
    parser.add_argument("--order-root", default="judge_blinding/reviewer_orders")
    parser.add_argument("--output-root", default="expert_packets_v4_4_2")
    parser.add_argument("--score-version", default="4.4.2")
    parser.add_argument("--expected-packets", type=int, default=180)
    args = parser.parse_args()

    l1_root = protected_root()
    packet_root = (l1_root / args.packet_root).resolve()
    order_root = (l1_root / args.order_root).resolve()
    output_root = (l1_root / args.output_root).resolve()
    for path in (packet_root, order_root, output_root):
        if not path.is_relative_to(l1_root):
            raise SystemExit("expert material path escapes CITRUS_EXPERIMENT_L1_ROOT")
    if output_root.exists():
        raise SystemExit(f"refuse overwrite: {output_root}")
    packet_manifest_path = packet_root / "manifest.json"
    packet_manifest = json.loads(packet_manifest_path.read_text(encoding="utf-8"))
    packet_records = {
        row["blind_id"]: row for row in packet_manifest.get("packets", [])
    }
    if not packet_records:
        raise SystemExit("judge packet manifest contains no packets")
    if len(packet_records) != args.expected_packets:
        raise SystemExit(
            f"expected {args.expected_packets} expert packets, "
            f"found {len(packet_records)}"
        )
    if any(not re.fullmatch(r"BLIND_[0-9]{6}", blind_id) for blind_id in packet_records):
        raise SystemExit("judge packet manifest contains a non-blind identifier")

    reviewers = {
        "reviewer_1": "EXPERT_A",
        "reviewer_2": "EXPERT_B",
    }
    packet_files = sorted(packet_root.glob("BLIND_*.json"))
    packet_by_id = {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in packet_files
    }
    if set(packet_by_id) != set(packet_records):
        raise SystemExit("judge packet manifest and packet files disagree")
    for blind_id, packet in packet_by_id.items():
        expected_sha = packet_records[blind_id].get("packet_sha256")
        if expected_sha != sha256_file(packet_root / f"{blind_id}.json"):
            raise SystemExit(f"{blind_id}: packet SHA does not match packet manifest")
        leaked = recursive_forbidden_key(packet)
        if leaked:
            raise SystemExit(f"{blind_id}: forbidden key in expert input: {leaked}")

    output_root.mkdir(parents=True)
    manifest = {
        "manifest_version": "4.4.2",
        "status": "BUILT_FOR_EXPERT_SCORING",
        "source_judge_packet_manifest_sha256": sha256_file(packet_manifest_path),
        "score_version": args.score_version,
        "packet_count": len(packet_records),
        "reviewers": {},
        "condition_fields_excluded": True,
        "reviewer_cross_visibility": False,
        "machine_scores_included": False,
    }

    for reviewer_slot, reviewer_id in reviewers.items():
        order_path = order_root / f"{reviewer_slot}.csv"
        with order_path.open(encoding="utf-8-sig", newline="") as handle:
            order_rows = list(csv.DictReader(handle))
        if len(order_rows) != len(packet_records):
            raise SystemExit(
                f"{reviewer_slot}: order has {len(order_rows)} rows, "
                f"expected {len(packet_records)}"
            )
        blind_ids = [row["blind_id"] for row in order_rows]
        if len(set(blind_ids)) != len(blind_ids):
            raise SystemExit(f"{reviewer_slot}: duplicate blind IDs in order")
        if set(blind_ids) != set(packet_records):
            raise SystemExit(f"{reviewer_slot}: order does not match packet set")

        reviewer_root = output_root / reviewer_slot
        reviewer_root.mkdir()
        (reviewer_root / "00_评分说明.md").write_text(
            instruction_text(),
            encoding="utf-8",
        )
        qa_rows: list[dict] = []
        evidence_rows: list[dict] = []
        for order in order_rows:
            blind_id = order["blind_id"]
            packet = packet_by_id[blind_id]
            if packet.get("batch_id") != order.get("batch_id"):
                raise SystemExit(f"{blind_id}: batch mismatch for {reviewer_slot}")
            qa_rows.append(
                {
                    "blind_id": blind_id,
                    "batch_id": order["batch_id"],
                    "question": packet.get("question", ""),
                    "expected_answerability": packet.get(
                        "expected_answerability", ""
                    ),
                    "source_scope": packet.get("source_scope", ""),
                    "final_answer": packet.get("final_answer", ""),
                    "citations_json": json.dumps(
                        packet.get("citations", []),
                        ensure_ascii=False,
                    ),
                    "system_evidence_excerpts_json": json.dumps(
                        packet.get("system_evidence_excerpts", []),
                        ensure_ascii=False,
                    ),
                    "frozen_reference_evidence_json": json.dumps(
                        packet.get("frozen_reference_evidence", []),
                        ensure_ascii=False,
                    ),
                }
            )
            for index, item in enumerate(
                packet.get("frozen_reference_evidence", []),
                start=1,
            ):
                if not isinstance(item, dict):
                    raise SystemExit(f"{blind_id}: reference evidence is not an object")
                evidence_rows.append(
                    {
                        "blind_id": blind_id,
                        "evidence_index": str(index),
                        "source_id": item.get("source_id", ""),
                        "version_or_sha256": item.get("sha256", "")
                        or item.get("version", ""),
                        "locator": item.get("locator", ""),
                        "excerpt": item.get("excerpt", "")
                        or item.get("text", ""),
                    }
                )
        write_csv(
            reviewer_root / "01_问题与回答.csv",
            [
                "blind_id",
                "batch_id",
                "question",
                "expected_answerability",
                "source_scope",
                "final_answer",
                "citations_json",
                "system_evidence_excerpts_json",
                "frozen_reference_evidence_json",
            ],
            qa_rows,
        )
        write_csv(
            reviewer_root / "02_参考证据.csv",
            [
                "blind_id",
                "evidence_index",
                "source_id",
                "version_or_sha256",
                "locator",
                "excerpt",
            ],
            evidence_rows,
        )
        score_rows = [
            {
                "batch_id": row["batch_id"],
                "blind_id": row["blind_id"],
                "reviewer_id": reviewer_id,
                "score_version": args.score_version,
            }
            for row in order_rows
        ]
        write_csv(
            reviewer_root / "03_专家评分表.csv",
            SCORE_FIELDS,
            [
                {
                    **{field: "" for field in SCORE_FIELDS},
                    **row,
                }
                for row in score_rows
            ],
        )
        write_xlsx(
            reviewer_root / "03_专家评分表.xlsx",
            order_rows,
            reviewer_id,
            args.score_version,
        )
        codebook_source = ROOT / "07_scoring/expert/SCORING_CODEBOOK_v4_4_2.md"
        if not codebook_source.is_file():
            raise SystemExit("active v4.4.2 expert scoring codebook is missing")
        shutil.copyfile(codebook_source, reviewer_root / "04_评分代码表.md")
        (reviewer_root / "05_完成检查清单.md").write_text(
            checklist_text(len(order_rows)),
            encoding="utf-8",
        )
        attestation = {
            "batch_id": "ALL",
            "reviewer_id": reviewer_id,
            "role": reviewer_slot,
            "protocol_version": "4.4.2",
            "independent_scoring_confirmed": False,
            "no_external_search_confirmed": False,
            "no_consultation_confirmed": False,
            "rubric_training_completed": False,
            "completed_utc": "",
            "packet_manifest_sha256": "",
            "reviewer_signature": "",
        }
        (reviewer_root / "reviewer_attestation.json").write_text(
            json.dumps(attestation, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        files = {}
        for path in sorted(reviewer_root.iterdir()):
            if path.is_file():
                files[path.name] = {
                    "bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
        manifest["reviewers"][reviewer_slot] = {
            "reviewer_id": reviewer_id,
            "rows": len(order_rows),
            "files": files,
        }

    digest_payload = json.dumps(
        {
            key: value
            for key, value in manifest.items()
            if key != "reviewers"
        },
        sort_keys=True,
        ensure_ascii=False,
    ).encode("utf-8")

    manifest["materials_sha256_excluding_itself"] = hashlib.sha256(
        digest_payload
    ).hexdigest()
    (output_root / "packet_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": "BUILT",
                "packets": len(packet_records),
                "reviewers": len(reviewers),
                "output_root": str(output_root),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
