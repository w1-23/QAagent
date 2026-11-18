"""Offline Optimum reranking adapter; no environment writes or hidden fallback."""
from __future__ import annotations
import math
from pathlib import Path
import threading


class OptimumReranker:
    def __init__(self,model_dir,*,check,provider='DmlExecutionProvider',batch_size=8,max_length=512):
        if not callable(check):raise ValueError('cancellation_context_required')
        if type(batch_size)is not int or batch_size<1:raise ValueError('invalid_batch_size')
        self.model_dir=Path(model_dir);self.check=check;self.provider=provider
        self.batch_size=batch_size;self.max_length=max_length
        self.model=None;self.tokenizer=None;self.lock=threading.Lock()
    def __call__(self,query,chunks,*,top_k=10):
        if not chunks:return []
        with self.lock:
            self.check()
            if self.model is None:
                if not (self.model_dir/'model.onnx').is_file():raise RuntimeError('reranker_model_missing')
                import onnxruntime as ort
                if self.provider not in ort.get_available_providers():raise RuntimeError('reranker_provider_unavailable')
                from optimum.onnxruntime import ORTModelForSequenceClassification
                from transformers import AutoTokenizer
                self.tokenizer=AutoTokenizer.from_pretrained(str(self.model_dir),local_files_only=True)
                self.model=ORTModelForSequenceClassification.from_pretrained(str(self.model_dir),
                    provider=self.provider,local_files_only=True,use_io_binding=False)
            logits=[]
            for start in range(0,len(chunks),self.batch_size):
                self.check();batch=chunks[start:start+self.batch_size]
                inputs=self.tokenizer([(query,row['text']) for row in batch],padding=True,
                    truncation=True,max_length=self.max_length,return_tensors='np')
                scores=self.model(**inputs).logits.reshape(-1).tolist()
                if len(scores)!=len(batch):raise ValueError('reranker_score_count_mismatch')
                logits.extend(scores)
            self.check()
            result=[]
            for row,logit in zip(chunks,logits):
                # Stable sigmoid, equivalent to the legacy definition without overflow.
                score=round(1/(1+math.exp(-logit)) if logit>=0 else math.exp(logit)/(1+math.exp(logit)),4)
                result.append({**row,'rerank_score':score,'score':score})
            return sorted(result,key=lambda row:row['rerank_score'],reverse=True)[:top_k]
