"""Explicit review snapshots and optional annotated copies; no implicit host state."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
from . import AuditSnapshot, review_bytes, export_workbook, rule_manifest

DEFAULT_CHECKS=('empty_table','empty_column','missing_values','missing_cell',
                'mixed_numeric_text','non_numeric_cell','duplicate_row','multi_header_suspected')


def _json(path):
    if path.stat().st_size>96*1024*1024:
        raise ValueError('snapshot_size_limit')
    return json.loads(path.read_text(encoding='utf-8'))


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='action',required=True)
    sub.add_parser('catalog')
    review=sub.add_parser('review');review.add_argument('input',type=Path)
    review.add_argument('--snapshot',type=Path,required=True)
    review.add_argument('--checks',nargs='+',default=list(DEFAULT_CHECKS))
    review.add_argument('--options',type=Path,help='JSON rule configuration: values, groups, ranges, etc.')
    review.add_argument('--sheet',default='');review.add_argument('--header-row',type=int,default=1)
    export=sub.add_parser('export');export.add_argument('snapshot',type=Path)
    export.add_argument('--output',type=Path,required=True)
    for flag in ('explain-visible','issue-list','overview','rules'):
        export.add_argument('--'+flag,action='store_true')
    args=parser.parse_args(argv)
    try:
        if args.action=='catalog':
            result={'status':'ok','rules':rule_manifest(),'version':'2.0.0'}
        elif args.action=='review':
            if args.input.stat().st_size>30*1024*1024:
                raise ValueError('input_size_limit')
            options=_json(args.options) if args.options else {}
            if not isinstance(options,dict):raise ValueError('options_must_be_object')
            if set(options)&{'checks','sheet_name','header_row','data','name'}:
                raise ValueError('duplicate_cli_options')
            snapshot=review_bytes(args.input.read_bytes(),args.input.name,checks=args.checks,
                sheet_name=args.sheet,header_row=args.header_row,**options)
            payload=json.dumps(snapshot.to_dict(),ensure_ascii=False,allow_nan=False).encode('utf-8')
            with args.snapshot.open('xb') as stream:stream.write(payload)
            result={'status':snapshot.report['status'],'source_sha256':snapshot.source_sha256,
                'snapshot':str(args.snapshot),'tables':[{'sheet':e['table']['sheet'],
                    'findings':len(e['result']['data']['findings']),'coverage':e['result']['coverage']}
                    for e in snapshot.report['tables']],'errors':snapshot.report['errors'],'version':'2.0.0'}
        else:
            snapshot=AuditSnapshot.from_dict(_json(args.snapshot))
            if args.output.suffix.lower() not in ({'.xlsm'} if snapshot.source_name.lower().endswith('.xlsm') else {'.xlsx'}):
                raise ValueError('output_extension_mismatch')
            content,receipt=export_workbook(snapshot,explain_visible=args.explain_visible,
                issue_list=args.issue_list,overview=args.overview,rules=args.rules)
            with args.output.open('xb') as stream:stream.write(content)
            result={'status':'ok','output':str(args.output),'receipt':receipt,'version':'2.0.0'}
    except (ValueError,TypeError,KeyError,OSError,ImportError) as error:
        result={'status':'error','errors':[{'code':type(error).__name__,'message':'Audit operation could not complete.'}],
            'coverage':{'complete':False},'version':'2.0.0'}
        print(json.dumps(result,ensure_ascii=True,allow_nan=False));return 1
    print(json.dumps(result,ensure_ascii=True,allow_nan=False));return 0


if __name__=='__main__':
    sys.exit(main())
