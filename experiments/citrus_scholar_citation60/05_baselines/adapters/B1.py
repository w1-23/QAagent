"""B1 FAIR simple RAG adapter: dsh must implement against *real frozen* local/web/pdf corpus.
DO NOT create toy keyword matching baseline and call it fair. Same model, corpus,
retrieval budget and parsing as B2 wherever possible. Intentionally fails closed.
"""
def run_case(case,config):
 raise NotImplementedError('B1 integration required: read 05_baselines/baseline_contract.md; use actual same sources/model/PDF parsing, do not fake retrieval results')
