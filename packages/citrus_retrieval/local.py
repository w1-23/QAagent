"""Read-only hybrid retrieval with explicit data, models, scope and parameters."""
from __future__ import annotations
from dataclasses import dataclass, asdict
import json
from pathlib import Path
import re
from .bm25 import BM25Plus, rrf_fuse


@dataclass(frozen=True)
class RetrievalParams:
    top_k_vector: int = 40
    top_k_bm25: int = 40
    candidate_window: int = 20
    top_k_final: int = 10
    rrf_k: int = 60
    dense_weight: float = 1.0
    bm25_weight: float = 1.0
    adjacent_decay: float = .6
    max_passages_per_paper: int = 2

    def __post_init__(self):
        if any(type(x)is not int or x<1 for x in (self.top_k_vector,self.top_k_bm25,
                self.candidate_window,self.top_k_final,self.rrf_k,self.max_passages_per_paper)):
            raise ValueError('positive_integer_retrieval_parameter_required')
        if not 0<=self.adjacent_decay<=1 or min(self.dense_weight,self.bm25_weight)<0:
            raise ValueError('invalid_retrieval_weight')


def local_query(query,*,translate=None):
    query=str(query or '').strip()
    if not query:return {'status':'invalid_input','query':None,'error':'empty_query'}
    if query.isascii():return {'status':'ok','query':query,'translated':False}
    if re.search(r'10\.\d{4,9}/\S+',query,re.I) or query.startswith(('https://','http://')):
        return {'status':'ok','query':query,'translated':False}
    if translate is None:return {'status':'translation_error','query':None,'error':'english_local_query_required'}
    try:english=translate(query)
    except Exception:return {'status':'translation_error','query':None,'error':'translation_failed'}
    if not isinstance(english,str) or not english.strip() or not english.isascii():
        return {'status':'translation_error','query':None,'error':'invalid_english_translation'}
    return {'status':'ok','query':english.strip(),'translated':True}


def _within(root,relative):
    path=(root/relative).resolve()
    if not path.is_relative_to(root):raise ValueError('corpus_path_outside_root')
    return path


class LanceCorpus:
    def __init__(self,data_root,*,allowed_corpus_ids,check):
        if not callable(check):raise ValueError('cancellation_context_required')
        self.check=check;self.root=Path(data_root).resolve()
        self.pointer=json.loads((self.root/'active_index.json').read_text(encoding='utf-8'))
        if (self.pointer.get('dimension')!=1024 or self.pointer.get('query_prefix')!='query:'
                or self.pointer.get('document_prefix')!='passage:' or self.pointer.get('index_schema_version')!=2):
            raise ValueError('index_contract_mismatch')
        self.generation=self.pointer['generation']
        registry=json.loads(_within(self.root,self.pointer['registry_path']).read_text(encoding='utf-8'))
        self.registry=registry
        self.allowed=frozenset(allowed_corpus_ids)
        if not self.allowed<=set(registry['corpora']):raise ValueError('unregistered_corpus_scope')
        self.rows=[];self.index={};self.tables=[]
        self.lance_root=_within(self.root,self.pointer['lancedb_path'])
        for corpus_id in sorted(self.allowed):
            self.check();record=registry['corpora'][corpus_id]
            table=record.get('table_name',corpus_id)
            if not re.fullmatch('[A-Za-z0-9_-]+',table):raise ValueError('invalid_table_name')
            self.tables.append(table)
            path=_within(self.root,record['chunks_path'])
            with path.open(encoding='utf-8') as stream:
                for line in stream:
                    self.check();row=json.loads(line)
                    if row.get('corpus_id')!=corpus_id:raise ValueError('chunk_corpus_mismatch')
                    chunk_id=row.get('chunk_id')
                    if not chunk_id or chunk_id in self.index:raise ValueError('invalid_chunk_identity')
                    self.index[chunk_id]=len(self.rows);self.rows.append(row)
        self.bm25=BM25Plus()
        # In-memory scoring state only; never writes/rebuilds a persisted generation.
        self.bm25.fit([r.get('text','') for r in self.rows])
        self.connection=None
    def bm25_search(self,query,k):
        self.check();return self.bm25.top_k(query,k=k)
    def dense_search(self,vector,k):
        if not self.allowed:return []
        self.check()
        if len(vector)!=self.pointer['dimension']:raise ValueError('embedding_dimension_mismatch')
        if self.connection is None:
            import lancedb
            self.connection=lancedb.connect(str(self.lance_root))
        hits=[]
        for name in self.tables:
            self.check()
            result=self.connection.open_table(name).search(vector).limit(k).to_list()
            for row in result:
                if row.get('corpus_id') not in self.allowed:raise PermissionError('dense_corpus_scope_mismatch')
                if row.get('chunk_id') not in self.index:raise ValueError('dense_chunk_generation_mismatch')
                hits.append((self.index[row['chunk_id']],-float(row.get('_distance',0))))
        return sorted(hits,key=lambda pair:pair[1],reverse=True)[:k]


class E5Embedder:
    def __init__(self,cache_root,*,provider='DmlExecutionProvider',allow_cpu=False):
        self.cache_root=Path(cache_root);self.provider=provider;self.allow_cpu=allow_cpu;self.model=None
    def __call__(self,query):
        if self.model is None:
            import onnxruntime as ort
            if self.provider not in ort.get_available_providers():
                if not self.allow_cpu:raise RuntimeError('gpu_provider_unavailable')
                self.provider='CPUExecutionProvider'
            from fastembed import TextEmbedding
            self.model=TextEmbedding('intfloat/multilingual-e5-large',cache_dir=str(self.cache_root),
                providers=[self.provider],local_files_only=True,lazy_load=True)
        vector=next(self.model.embed(['query: '+query]))
        if len(vector)!=1024:raise ValueError('embedding_dimension_mismatch')
        return vector.tolist()


class HybridSearch:
    def __init__(self,corpus,*,embed,rerank=None,params=None):
        self.corpus,self.embed,self.rerank=corpus,embed,rerank
        self.params=params or RetrievalParams()
    def search(self,query,*,evidence_store,query_ref,translate=None,limit=6):
        language=local_query(query,translate=translate)
        if language['status']!='ok':return {'status':language['status'],'candidates':[],'query_language':language}
        if type(limit)is not int or limit<1:raise ValueError('invalid_result_limit')
        query=language['query'];p=self.params;self.corpus.check()
        lexical=self.corpus.bm25_search(query,p.top_k_bm25)
        dense=self.corpus.dense_search(self.embed(query),p.top_k_vector)
        fused=rrf_fuse(dense,lexical,k=p.rrf_k,weights=[p.dense_weight,p.bm25_weight])[:p.candidate_window]
        candidates=[{**self.corpus.rows[index],'score':score} for index,score in fused]
        if self.rerank is not None:
            candidates=self.rerank(query,candidates,top_k=p.top_k_final)
        else:candidates=candidates[:p.top_k_final]
        selected=[];per_paper={};seen=set();decay=0
        for row in candidates:
            key=row['chunk_id'];paper=row.get('paper_id') or row.get('document_id') or key
            if key in seen or per_paper.get(paper,0)>=p.max_passages_per_paper:continue
            rank=row.get('chunk_index')
            adjacent=any(paper==(r.get('paper_id') or r.get('document_id')) and isinstance(rank,int)
                and isinstance(r.get('chunk_index'),int) and abs(rank-r['chunk_index'])<=1 for r in selected)
            if adjacent:
                row={**row,'score':float(row.get('score',0))*p.adjacent_decay};decay+=1
            seen.add(key);per_paper[paper]=per_paper.get(paper,0)+1;selected.append(row)
        selected.sort(key=lambda row:row.get('score',0),reverse=True)
        observations=[]
        for row in selected[:min(limit,6)]:
            self.corpus.check()
            observations.append(evidence_store.put(row,query_ref=query_ref))
        return {'status':'ok','candidates':observations,'query_language':language,
            'diagnostics':{'bm25_candidates':len(lexical),'dense_candidates':len(dense),
                'rrf_candidates':len(fused),'decay_filtered':decay,'generation':self.corpus.generation,
                'rerank_executed':self.rerank is not None,'allowed_corpora':sorted(self.corpus.allowed),
                'parameters':asdict(p)}}
