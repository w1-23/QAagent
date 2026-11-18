"""Review immutable bytes; exports consume the completed review, never rescan."""
from __future__ import annotations
from dataclasses import dataclass
import base64
import hashlib
import json
from .envelope import TableEnvelope
from .methods import audit_frame
from .readers import read_tables
from .data_audit_rules import RULES


def audit_table(table: TableEnvelope, *, checks=None, **options):
    if not isinstance(table,TableEnvelope):
        raise ValueError('An explicit TableEnvelope is required')
    if any(k in options for k in ('row_offset','source_coverage','execution_receipt')):
        raise ValueError('Envelope owns source coordinates and coverage')
    receipt={}
    findings,_=audit_frame(table.to_frame(),row_offset=0,source_coverage=table.coverage,
                           execution_receipt=receipt,checks=checks,**options)
    diagnostics={'iqr_insufficient','design_checks_skipped',
                 'invalid_group_mapping','invalid_value_mapping','grim_sd_skipped',
                 'integer_summary_not_checked','findings_truncated'}
    for item in findings:
        local=item.get('column_index')
        if type(local)is int:
            item['column_index']=table.column_numbers[local-1]
        if item.get('evidence',{}).get('cells'):
            item['scope']='cell'
        else:
            item['scope']='cell' if item.get('row') else 'column' if item.get('column_index') else 'table'
        item['coverage_only']=item['check'] in diagnostics
        item['sheet']=table.sheet
    rules=receipt['rules']
    partial=bool(table.coverage.get('truncated')) or any(r['status'] in {'invalid_input','failed','truncated'} for r in rules.values())
    return {'status':'partial' if partial else 'complete','data':{'findings':findings,'rule_execution':receipt},
            'errors':[],'coverage':{'requested':receipt['requested'],'actual':receipt['actual'],
                'gap':receipt['gap'],'source':table.coverage,'source_sha256':table.source_sha256,
                'lexical_coverage':table.lexical_coverage},'version':'2.0.0'}


@dataclass(frozen=True)
class AuditSnapshot:
    source_bytes: bytes
    source_name: str
    report_json: str
    source_sha256: str

    def __post_init__(self):
        if not isinstance(self.source_bytes,bytes) or hashlib.sha256(self.source_bytes).hexdigest()!=self.source_sha256:
            raise ValueError('Reviewed snapshot source hash mismatch')
        report=json.loads(self.report_json)
        if report.get('version')!='2.0.0' or report.get('source_sha256')!=self.source_sha256:
            raise ValueError('Reviewed report source identity mismatch')
        for entry in report.get('tables',[]):
            envelope=TableEnvelope.from_dict(entry['table'])
            if envelope.source_sha256!=self.source_sha256:
                raise ValueError('Reviewed envelope source identity mismatch')

    @property
    def report(self):
        return json.loads(self.report_json)

    def to_dict(self):
        return {'version':'2.0.0','source_name':self.source_name,'source_sha256':self.source_sha256,
                'source_base64':base64.b64encode(self.source_bytes).decode('ascii'),'report':self.report}

    @classmethod
    def from_dict(cls,value):
        if value.get('version')!='2.0.0':raise ValueError('Unsupported snapshot version')
        data=base64.b64decode(value['source_base64'],validate=True)
        return cls(data,value['source_name'],json.dumps(value['report'],ensure_ascii=False,allow_nan=False),value['source_sha256'])


def review_bytes(data: bytes, name: str, *, checks=None, sheet_name='', header_row=1, header_rows=None, **options):
    tables,errors=read_tables(data,name,sheet_name=sheet_name,header_row=header_row,header_rows=header_rows)
    entries=[]
    for table in tables:
        entries.append({'table':table.to_dict(),'result':audit_table(table,checks=checks,**options)})
    report={'version':'2.0.0','source_sha256':hashlib.sha256(data).hexdigest(),'source_name':name,
        'status':'partial' if errors or any(e['result']['status']=='partial' for e in entries) else 'complete',
        'tables':entries,'errors':errors,'claims':['Review leads only; no misconduct verdict or synthetic risk score.']}
    return AuditSnapshot(data,name,json.dumps(report,ensure_ascii=False,allow_nan=False),report['source_sha256'])


def export_workbook(snapshot: AuditSnapshot, **options):
    from .workbook import export_snapshot
    return export_snapshot(snapshot,**options)
