"""Reference CLI: JSON stdout, explicit offline fixtures or authorized network."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import time
from . import extract_inventory, hint_differences, lookup_evidence, FixtureTransport, LookupLimits


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError('invalid_arguments')


def _execute(argv=None):
    parser=_Parser(description=__doc__)
    sub=parser.add_subparsers(dest='action',required=True)
    extract=sub.add_parser('extract');extract.add_argument('input',type=Path)
    one=sub.add_parser('lookup');one.add_argument('--claims',required=True)
    one.add_argument('--fixture',type=Path);one.add_argument('--allow-network',action='store_true')
    one.add_argument('--providers',nargs='+',default=['Crossref'])
    batch=sub.add_parser('batch')
    batch.add_argument('--inventory',type=Path)
    batch.add_argument('--repository',type=Path,required=True)
    batch.add_argument('--owner',required=True);batch.add_argument('--session',required=True)
    batch.add_argument('--audit-id');batch.add_argument('--fixture',type=Path,required=True)
    batch.add_argument('--request-budget',type=int,default=1200);batch.add_argument('--max-rows',type=int)
    batch.add_argument('--resume',action='store_true')
    render=sub.add_parser('export')
    render.add_argument('--repository',type=Path,required=True)
    render.add_argument('--owner',required=True);render.add_argument('--session',required=True)
    render.add_argument('--audit-id',required=True)
    render.add_argument('--format',choices=['json','xlsx','ris','bibtex'],default='json')
    render.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv)
    if args.action in {'batch','export'}:
        from . import AuditRepository, export_snapshot
        repo=AuditRepository(args.repository,owner=args.owner,session_id=args.session)
        if args.action=='batch':
            audit_id=args.audit_id
            if not audit_id:
                if args.inventory is None:parser.error('--inventory is required for a new audit')
                inventory=json.loads(args.inventory.read_text(encoding='utf-8'))
                audit_id=repo.create(inventory,request_budget=args.request_budget)
            result=repo.run(audit_id,LookupLimits(FixtureTransport(json.loads(args.fixture.read_text(encoding='utf-8')))),
                            max_rows=args.max_rows,resume=args.resume)
        else:
            snapshot=repo.snapshot(args.audit_id)
            content=export_snapshot(snapshot,format=args.format)
            with args.output.open('xb') as output:output.write(content)
            result={'status':'saved','audit_id':args.audit_id,'output':str(args.output),'coverage':snapshot['coverage']}
    elif args.action=='extract':
        result=extract_inventory(name=args.input.name,content=args.input.read_bytes())
    else:
        claims=json.loads(args.claims)
        if not isinstance(claims,dict):
            raise ValueError('claims_must_be_an_object')
        if args.fixture:
            transport=FixtureTransport(json.loads(args.fixture.read_text(encoding='utf-8')))
            limits=LookupLimits(transport,tuple(args.providers))
            result=lookup_evidence(claims,limits)
        elif args.allow_network:
            import httpx
            from .lookup import HttpTransport
            deadline=time.monotonic()+20
            budget=[16]
            def check():
                if time.monotonic()>=deadline:raise RuntimeError('deadline')
            def debit():
                if budget[0]<=0:raise RuntimeError('request_budget_exhausted')
                budget[0]-=1
            with httpx.Client(trust_env=True) as client:
                limits=LookupLimits(HttpTransport(client=client,deadline=deadline,check=check),
                    tuple(args.providers),deadline,check,debit,True)
                result=lookup_evidence(claims,limits)
        else:
            result={'records':[],'providers':{name:'not_authorized' for name in args.providers},
                    'errors':{},'evidence_refs':[],'fetched_at':None}
        result['differences']=[hint_differences(claims,row) for row in result['records']]
    return result


def main(argv=None):
    try:
        result = _execute(argv)
        output = json.dumps(result, ensure_ascii=True, allow_nan=False)
        print(output)
    except (Exception, KeyboardInterrupt) as error:
        # Do not echo provider bodies, paths, submitted text or exception strings.
        codes = {FileNotFoundError: 'input_not_found', FileExistsError: 'output_exists',
                 PermissionError: 'permission_denied', json.JSONDecodeError: 'invalid_json',
                 ValueError: 'invalid_input', TypeError: 'invalid_input',
                 ImportError: 'dependency_unavailable', ModuleNotFoundError: 'dependency_unavailable',
                 KeyboardInterrupt: 'cancelled'}
        code = codes.get(type(error), 'operation_failed')
        print(json.dumps({'status': 'error', 'data': None, 'errors': [
            {'code': code, 'message': 'Reference operation could not complete.', 'retryable': False}]},
            ensure_ascii=True, allow_nan=False))
        return 130 if isinstance(error, KeyboardInterrupt) else 1
    return 0


if __name__=='__main__':
    raise SystemExit(main())
