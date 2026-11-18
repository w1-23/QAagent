"""Coverage by immutable source, record and check, never by tool-name count."""
from __future__ import annotations
from typing import Any
from src.core.op_receipt import result_payload

VERIFICATION_RESOLVED = frozenset({"metadata_matched", "metadata_mismatch", "web_candidate",
                                  "title_candidate", "not_found_in_checked_registries"})
TRACKED_TOOLS = frozenset({"verify_uploaded_references", "audit_uploaded_data",
                           "run_research_figure_script", "create_research_figure"})

def _nonnegative_int(value):
    return type(value) is int and value >= 0


def _reference_scope(payload):
    scope = payload.get("coverage")
    if not isinstance(scope, dict):
        return None
    source, kind = scope.get("source_sha256"), scope.get("source_kind")
    total = scope.get("submitted")
    if (not isinstance(source, str) or not source
            or kind not in {"attachment", "supplied_records"}
            or not _nonnegative_int(total) or not total):
        return None
    index = scope.get("file_index") if kind == "attachment" else None
    if kind == "attachment" and not _nonnegative_int(index):
        return None
    return kind, index, source


def _reference_row(row, scope):
    return (isinstance(row, dict) and isinstance(row.get("record_id"), str)
            and bool(row["record_id"]) and _nonnegative_int(row.get("record_index"))
            and row["record_index"] < scope["submitted"]
            and row.get("source_sha256") == scope["source_sha256"]
            and isinstance(row.get("verification_rule"), str) and bool(row["verification_rule"]))


class CoverageLedger:
    def __init__(self):
        self.references: dict[tuple, dict] = {}
        self.audits: dict[tuple, dict] = {}
        self.figures: dict[str, dict] = {}
        self.datasets: set[str] = set()
        self.new_targets = 0
        self.repeated_targets = 0
        self.last_delta = {}

    def observe(self, tool_name: str, *, content: Any = "", artifact: Any = None):
        payload = result_payload(content, artifact)
        receipt = payload.get("op_receipt")
        if isinstance(receipt, dict):
            payload = receipt.get("coverage") or {}
        if not isinstance(payload, dict):
            payload = {}
        added = repeated = 0
        if tool_name == "verify_uploaded_references":
            identity = _reference_scope(payload)
            if identity is not None:
                scope = payload["coverage"]
                for row in payload.get("rows", []):
                    if not _reference_row(row, scope):
                        continue
                    source_key = (*identity, row["verification_rule"])
                    ledger = self.references.setdefault(source_key, {
                        "total": scope["submitted"], "rows": {}, "conflicted": False,
                        "file_index": identity[1], "source_kind": identity[0],
                        "source_sha256": identity[2], "verification_rule": row["verification_rule"],
                        "extraction_scope": scope.get("extraction_scope", "unknown")})
                    # A hash/rule must describe one stable ordered record set.
                    # Never silently combine incompatible manifests or claims.
                    if ledger["total"] != scope["submitted"]:
                        ledger["conflicted"] = True
                    key = row["record_index"]
                    previous = ledger["rows"].get(key)
                    if previous is not None and previous["record_id"] != row["record_id"]:
                        ledger["conflicted"] = True
                    resolved = row.get("status") in VERIFICATION_RESOLVED
                    if previous is None or (resolved and previous.get("status") not in VERIFICATION_RESOLVED):
                        ledger["rows"][key] = dict(row)
                        added += 1
                    else:
                        repeated += 1
        elif tool_name == "audit_uploaded_data":
            for item in payload.get("results", payload.get("audit_results", [])):
                if (not isinstance(item, dict) or not item.get("source_sha256")
                        or not item.get("checks_sha256") or not item.get("audit_mode")
                        or not _nonnegative_int(item.get("file_index"))):
                    continue
                sheets = item.get("sheets")
                if not isinstance(sheets, list) or not sheets or any(not isinstance(s, str) for s in sheets):
                    continue
                requested_sheet = item.get("requested_sheet")
                cov = item.get("coverage")
                if not isinstance(cov, dict):
                    continue
                complete = bool(item.get("ok") is True and not item.get("skipped")
                    and not item.get("annotation_errors")
                    and not any(cov.get(k) for k in ("truncation_reasons", "unannotated_rows",
                        "unprocessed_sheets", "unscanned_columns", "findings_truncated"))
                    and all(_nonnegative_int(cov.get("input_" + axis))
                        and cov["input_" + axis] > 0
                        and _nonnegative_int(cov.get("processed_" + axis))
                        and cov["processed_" + axis] == cov["input_" + axis]
                        for axis in ("rows", "columns")))
                for sheet in sheets:
                    # A requested-sheet receipt cannot attest other sheets or
                    # the whole workbook, even if its output contains them.
                    if requested_sheet and sheet != requested_sheet:
                        continue
                    key = (item["file_index"], item["source_sha256"], sheet,
                           item["checks_sha256"], item["audit_mode"])
                    old = self.audits.get(key)
                    value = {"file_index": item["file_index"], "file": item.get("file"),
                             "source_file_id": item.get("source_file_id"),
                             "sha256": item["source_sha256"], "sheet": sheet, "complete": complete,
                             "checks_sha256": item["checks_sha256"],
                             "whole_file": requested_sheet == "" and complete,
                             "rules": item.get("rule_execution", []), "mode": item["audit_mode"],
                             "coverage": cov}
                    if (old is None or (complete and not old["complete"])
                            or (value["whole_file"] and not old["whole_file"])):
                        self.audits[key] = value
                        added += 1
                    else:
                        repeated += 1
        elif tool_name in {"run_research_figure_script", "create_research_figure"} or (
                tool_name == "research_workspace" and (payload.get("_tool_outcome") or {}).get("reason_code") == "figure_edit_saved"):
            # Internal datasets advance preparation without satisfying a figure.
            import json
            for ref in payload.get("dataset_refs", []):
                identity = json.dumps(ref, sort_keys=True, ensure_ascii=False)
                if identity not in self.datasets:
                    self.datasets.add(identity)
                    added += 1
                else:
                    repeated += 1
            # An explicit empty figures list is a zero-figure exploration.
            items = payload["figures"] if isinstance(payload.get("figures"), list) else [payload]
            for item in items:
                if not isinstance(item, dict) or not item.get("figure_id"):
                    continue
                exports = [e for e in item.get("exports", []) if isinstance(e, dict)
                           and e.get("format") in {"svg", "png", "pdf", "tiff"} and e.get("artifact_url")]
                if not exports:
                    continue
                fid = str(item["figure_id"])
                from src.figures.nature_receipts import validate_nature_receipt
                try:
                    validate_nature_receipt(item)
                    nature_ok = True
                except ValueError:
                    nature_ok = False
                checks = (item.get("qa_receipt") or {}).get("checks", {})
                qa_ok = not item.get("qa_failures") and not any(
                    isinstance(v, dict) and v.get("status") == "fail" for v in checks.values())
                edited = tool_name == "research_workspace" and bool(item.get("edit_provenance"))
                complete = (edited or nature_ok) and qa_ok and not item.get("failures")
                previous = self.figures.get(fid)
                self.figures[fid] = {"figure_id": fid, "edited": edited, "title": str(item.get("title") or ""),
                    "exports": exports, "complete": complete, "nature_ok": nature_ok,
                    "qa_receipt": item.get("qa_receipt", {}), "nature_skill_receipt": item.get("nature_skill_receipt", {})}
                if previous is None or (complete and not previous["complete"]):
                    added += 1
                else:
                    repeated += 1
        self.new_targets += added
        self.repeated_targets += repeated
        self.last_delta = {"new_targets": added, "repeated_targets": repeated,
                           "tracked": tool_name in TRACKED_TOOLS}
        return self.last_delta

    def reconciles_reference_batch(self, record):
        """Prove that *only* this attempt's remaining-records gap was filled.

        A terminal publication/persistence/validation failure is independent of
        coverage. Missing or ambiguous receipts must keep their original status.
        """
        if ((record.get("name") or record.get("tool_name")) != "verify_uploaded_references"
                or record.get("status") != "partial"
                or (record.get("reason") or record.get("reason_code")) != "coverage_incomplete"):
            return False
        receipt = record.get("op_receipt")
        if (not isinstance(receipt, dict) or receipt.get("schema_version") != 1
                or receipt.get("tool_name") != "verify_uploaded_references"
                or not record.get("tool_call_id")
                or receipt.get("tool_call_id") != record["tool_call_id"]
                or receipt.get("status") != "partial"
                or receipt.get("reason_code") != "coverage_incomplete"):
            return False
        payload = receipt.get("coverage")
        if not isinstance(payload, dict):
            return False
        identity = _reference_scope(payload)
        if identity is None:
            return False
        scope = payload["coverage"]
        start, selected, next_start = (scope.get(k) for k in
                                      ("start_index", "selected", "next_start_index"))
        if (not all(_nonnegative_int(v) for v in (start, selected, next_start))
                or not selected or next_start != start + selected
                or next_start >= scope["submitted"]):
            return False
        rows = payload.get("rows")
        if not isinstance(rows, list):
            return False
        checked = [row for row in rows if _reference_row(row, scope)]
        # The sole anonymous not_checked row is the tool's overflow marker.
        # Network errors, invalid records and any other unaccounted row remain
        # independent partial outcomes even when other batches cover the source.
        markers = [row for row in rows if not _reference_row(row, scope)]
        if (len(markers) != 1 or not isinstance(markers[0], dict)
                or markers[0].get("status") != "not_checked"
                or any(markers[0].get(k) is not None for k in
                       ("record_id", "record_index", "source_sha256", "verification_rule"))
                or len(checked) != selected
                or {r["record_index"] for r in checked} != set(range(start, next_start))
                or any(r.get("status") not in VERIFICATION_RESOLVED for r in checked)):
            return False
        rules = {row["verification_rule"] for row in checked}
        if len(rules) != 1:
            return False
        ledger = self.references.get((*identity, next(iter(rules))))
        if (not ledger or ledger["conflicted"] or ledger["total"] != scope["submitted"]
                or len(ledger["rows"]) != ledger["total"]
                or any(row.get("status") not in VERIFICATION_RESOLVED for row in ledger["rows"].values())):
            return False
        return all(ledger["rows"][row["record_index"]]["record_id"] == row["record_id"] for row in checked)

    def required_obligations_satisfied(self, contract, *, deliverables_satisfied=False):
        """Positive host proof, not merely the absence of a known gap.

        Empty/unknown contracts cannot classify other work as incidental. File
        obligations additionally need the independent artifact/export gate.
        """
        required = [o for o in contract.obligations if o.required]
        return bool(contract.understanding_status == "structured" and required
                    and all(o.kind in {"verification", "audit", "figure", "deliverable"}
                            for o in required)
                    and not self.gaps(contract)
                    and (not any(o.kind in {"figure", "deliverable"} for o in required)
                         or deliverables_satisfied))

    def gaps(self, contract):
        gaps = []
        required = [o for o in contract.obligations if o.required]
        if contract.understanding_status != "structured":
            gaps.append({"obligation_id": "contract", "kind": "input", "reason_code": "task_scope_unconfirmed",
                         "detail": "任务范围未能确认，不能标记全部完成。"})
        figure_obligations = [o for o in required if o.kind == "figure"]
        # Match product-by-product; one image must not satisfy multiple requests.
        candidates = [f for f in self.figures.values() if f["complete"]]
        assigned = {}
        def assign(index, visited):
            required = set(figure_obligations[index].subject["requirement"].get("formats") or [])
            subject = figure_obligations[index].subject
            for candidate_index, figure in enumerate(candidates):
                if subject.get("phase") == "edit":
                    if not figure.get("edited") or (subject.get("figure_id") and subject["figure_id"] != figure["figure_id"]):
                        continue
                elif figure.get("edited"):
                    continue
                if candidate_index in visited or not required.issubset({e["format"] for e in figure["exports"]}):
                    continue
                visited.add(candidate_index)
                if candidate_index not in assigned or assign(assigned[candidate_index], visited):
                    assigned[candidate_index] = index
                    return True
            return False
        for index in range(len(figure_obligations)):
            assign(index, set())
        matches = set(assigned.values())
        for index, obligation in enumerate(figure_obligations):
            if index not in matches:
                gaps.append({"obligation_id": obligation.obligation_id, "kind": "figure", "subject": obligation.subject,
                    "reason_code": "figure_not_produced" if not self.figures else "figure_acceptance_incomplete",
                    "detail": "所需图件尚未完成发布、项目登记、格式或初始 Nature 检查。"})
        for o in required:
            base = {"obligation_id": o.obligation_id, "kind": o.kind, "subject": o.subject}
            if o.kind not in {"input", "audit", "verification", "figure", "deliverable"}:
                gaps.append({**base, "reason_code": "unsupported_obligation",
                             "detail": "所需任务没有可确认的验收规则，不能标记全部完成。"})
            elif o.kind == "input":
                gaps.append({**base, "reason_code": "input_required", "detail": o.subject["detail"]})
            elif o.kind == "audit":
                indexes = o.subject.get("file_indexes", [])
                sheets = o.subject.get("sheets", [])
                missing = []
                targets = {t["index"]: t for t in o.subject.get("targets", [])}
                for index in indexes:
                    target = targets.get(index, {})
                    records = [r for r in self.audits.values() if r["file_index"] == index and r["complete"]
                               and (not target.get("file_id") or r.get("source_file_id") == target["file_id"])]
                    groups = {}
                    for record in records:
                        key = (record["sha256"], record["checks_sha256"], record["mode"])
                        groups.setdefault(key, []).append(record)
                    # Sheets checked against different source/rule snapshots
                    # cannot collectively satisfy one workbook obligation.
                    if not any((set(sheets).issubset({r["sheet"] for r in group}) if sheets
                                else any(r["whole_file"] for r in group)) for group in groups.values()):
                        missing.append(index)
                if missing or not indexes:
                    gaps.append({**base, "reason_code": "audit_files_uncovered", "missing_indexes": missing,
                                 "requested": len(indexes), "covered": len(indexes) - len(missing),
                                 "detail": "尚未完成指定文件/工作表的审查：" + str(missing)})
            elif o.kind == "verification":
                indexes = o.subject.get("file_indexes", [])
                groups = [[r for r in self.references.values()
                           if r["file_index"] == i and r["source_kind"] == "attachment"] for i in indexes]
                if not indexes:
                    groups = [[r for r in self.references.values() if r["source_kind"] == "supplied_records"]]
                total_missing = checked = 0
                absent = False
                start, count = o.subject.get("record_start", 0), o.subject.get("record_count", 0)
                for sources in groups:
                    if not sources:
                        absent = True
                        continue
                    for source in sources:
                        if (source["source_kind"] == "attachment"
                                and source["extraction_scope"] != "all_references"
                                and o.subject.get("scope") != "extracted_identifiers"):
                            gaps.append({**base, "reason_code": "reference_extraction_incomplete",
                                "detail": "附件仅完成可提取 DOI 的核验；尚不能确认覆盖全部参考文献或原文指定条目。"})
                        # An explicit requested range must not shrink to the
                        # shorter range a tool happened to submit.
                        end = start + count if count else source["total"]
                        resolved = {row["record_index"] for row in source["rows"].values()
                                    if not source["conflicted"] and row.get("status") in VERIFICATION_RESOLVED
                                    and start <= row["record_index"] < end}
                        checked += len(resolved)
                        total_missing += max(0, end - start - len(resolved))
                        if end <= start or source["conflicted"]:
                            absent = True
                if absent or total_missing:
                    gaps.append({**base, "reason_code": "verification_not_started" if absent else "verification_records_unresolved",
                                 "unresolved": total_missing, "checked": checked,
                                 "detail": "指定记录仍未全部核验；请补充缺失来源或只处理未覆盖范围。"})
        return gaps

    def as_dict(self):
        return {"schema_version": 1, "verification": [{**{k: v for k, v in s.items() if k != "rows"},
                "rows": list(s["rows"].values())} for s in self.references.values()],
                "audit": list(self.audits.values()), "figures": list(self.figures.values()),
                "new_targets": self.new_targets, "repeated_targets": self.repeated_targets}


def _modern_audit_scope(record, receipt):
    """A page is not proof. Only accept aggregate coverage of one durable audit."""
    payload = receipt.get("coverage")
    if not isinstance(payload, dict) or payload.get("schema") != "reference_audit/2":
        return None
    scope = payload.get("coverage")
    if not isinstance(scope, dict):
        return None
    audit, rule, source = payload.get("audit_id"), payload.get("rule_version"), scope.get("source_sha256")
    total, submitted, processed, pending, retryable = (scope.get(key) for key in
        ("total", "submitted", "processed", "pending", "retryable"))
    if (not all(isinstance(key, str) and key for key in (audit, rule, source))
            or len(source) != 64 or set(source) - set("0123456789abcdef")
            or not all(_nonnegative_int(n) for n in (total, submitted, processed, pending, retryable))
            or not total or submitted != total or processed + pending != total or retryable > processed
            or scope.get("extraction_complete") is not True
            or receipt.get("status") != record.get("status")):
        return None
    identity = audit, source, rule, total
    reason = record.get("reason") or record.get("reason_code")
    if reason != receipt.get("reason_code"):
        return None
    if (reason in {"audit_complete", "audit_completed_with_review"}
            and record.get("status") == "complete" and payload.get("terminal") is True
            and scope.get("complete") is True and scope.get("processing_complete") is True
            and not (pending or retryable)):
        return identity, "settled"
    if (reason == "reference_coverage_incomplete" and record.get("status") == "partial"
            and payload.get("terminal") is False and (pending or retryable)
            and payload.get("export_status") in {None, "not_requested"}
            and not payload.get("run_reason") in {"cancelled", "cancelled_use_resume", "lease_lost", "already_running"}):
        return identity, "open"
    return None


def reconcile_coverage_records(records):
    """Return an effective view; never modify the raw execution journal.

    Host integration: copy the immutable ``artifact['op_receipt']`` onto each
    terminal record, after artifact validation, retaining its original call ID,
    status and reason. Legacy records without receipts remain independent.
    """
    records = list(records)
    ledger = CoverageLedger()
    modern = {}
    settled = {}
    for index, record in enumerate(records):
        receipt = record.get("op_receipt")
        name = record.get("name") or record.get("tool_name")
        if (name == "verify_uploaded_references" and isinstance(receipt, dict)
                and receipt.get("schema_version") == 1
                and receipt.get("tool_name") == name and record.get("tool_call_id")
                and receipt.get("tool_call_id") == record["tool_call_id"]
                and receipt.get("status") in {"complete", "partial"}
                and record.get("status") in {"complete", "partial"}):
            ledger.observe(name, artifact={"op_receipt": receipt})
            proof = _modern_audit_scope(record, receipt)
            if proof:
                identity, phase = proof
                modern[index] = proof
                if phase == "settled":
                    settled[identity] = index
    effective = []
    for index, record in enumerate(records):
        proof = modern.get(index)
        covered = proof and proof[1] == "open" and settled.get(proof[0], -1) > index
        if covered or ledger.reconciles_reference_batch(record):
            effective.append({**record, "status": "complete",
                              "reason": "cumulative_coverage_completed",
                              "reason_code": "cumulative_coverage_completed"})
        else:
            effective.append(record)
    return effective
