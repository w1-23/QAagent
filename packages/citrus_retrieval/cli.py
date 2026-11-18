"""Local hybrid search and persistent evidence readback CLI."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from . import EvidenceStore, E5Embedder, HybridSearch, LanceCorpus, OptimumReranker


def _run(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='action',required=True)
    local=sub.add_parser('local');local.add_argument('query')
    local.add_argument('--data-root',type=Path,required=True)
    local.add_argument('--model-root',type=Path,required=True)
    local.add_argument('--corpora',nargs='+',required=True)
    local.add_argument('--allow-cpu',action='store_true')
    local.add_argument('--provider',default='DmlExecutionProvider' if sys.platform=='win32' else 'CUDAExecutionProvider')
    local.add_argument('--without-rerank',action='store_true')
    read=sub.add_parser('read');read.add_argument('--ref');read.add_argument('--paper-id');read.add_argument('--chunk-id')
    read.add_argument('--generation',required=True);read.add_argument('--offset',type=int,default=0)
    read.add_argument('--max-chars',type=int,default=4000)
    for command in (local,read):
        command.add_argument('--evidence-dir',type=Path,required=True)
        command.add_argument('--owner',required=True);command.add_argument('--session',required=True)
        command.add_argument('--turn',required=True)
    args=parser.parse_args(argv)
    check=lambda:None  # foreground CLI lifetime; Ctrl-C interrupts the operation
    if args.action=='local':
        corpus=LanceCorpus(args.data_root,allowed_corpus_ids=args.corpora,check=check)
        store=EvidenceStore(args.evidence_dir,owner=args.owner,session_id=args.session,turn_id=args.turn,generation=corpus.generation)
        if args.provider=='CPUExecutionProvider' and not args.allow_cpu:
            raise ValueError('explicit_cpu_permission_required')
        provider='CPUExecutionProvider' if args.allow_cpu else args.provider
        search=HybridSearch(corpus,embed=E5Embedder(args.model_root/'fastembed',provider=provider,allow_cpu=args.allow_cpu),
            rerank=None if args.without_rerank else OptimumReranker(args.model_root/'onnx_reranker',check=check,provider=provider))
        result=search.search(args.query,evidence_store=store,query_ref=args.turn)
    else:
        store=EvidenceStore(args.evidence_dir,owner=args.owner,session_id=args.session,turn_id=args.turn,generation=args.generation)
        result=store.read(ref=args.ref,paper_id=args.paper_id,chunk_id=args.chunk_id,offset=args.offset,max_chars=args.max_chars)
    print(json.dumps(result,ensure_ascii=True,allow_nan=False))
    return 0 if result['status']=='ok' else 1


def main(argv=None):
    try:return _run(argv)
    except (OSError,ValueError,KeyError,TypeError,RuntimeError,ImportError) as error:
        print(json.dumps({'status':'error','candidates':[],'errors':[{'code':type(error).__name__}],
            'coverage':{'complete':False},'version':'2.0.0'},allow_nan=False))
        return 1


if __name__=='__main__':
    raise SystemExit(main())
