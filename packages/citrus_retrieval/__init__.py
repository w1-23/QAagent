"""Independent retrieval primitives and explicitly configured execution adapters."""
__version__='2.0.0'
from .evidence import EvidenceStore, SOURCE_FIELDS
from .local import E5Embedder, HybridSearch, LanceCorpus, RetrievalParams, local_query
from .models import OptimumReranker
from .fetch import FetchCandidate, fetch_race, search_web

__all__=['EvidenceStore','SOURCE_FIELDS','E5Embedder','HybridSearch','LanceCorpus',
         'RetrievalParams','local_query','OptimumReranker','FetchCandidate','fetch_race','search_web']
