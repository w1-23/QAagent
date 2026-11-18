"""Optional Qdrant compatibility. LanceDB imports never require this driver."""
from __future__ import annotations


def open_qdrant(*,path=None,url=None,api_key=None,authorized=False,check=None,debit=None):
    if bool(path)==bool(url):
        raise ValueError('Choose exactly one explicit Qdrant path or URL')
    if url and (not authorized or not callable(check) or not callable(debit)):
        return {'status':'not_authorized','backend':'qdrant'}
    try:
        from qdrant_client import QdrantClient
    except ImportError:
        return {'status':'backend_unavailable','backend':'qdrant','reason_code':'optional_dependency_missing'}
    # The driver owns and releases its own lock; never delete another process lock.
    # Later RPCs remain the caller adapter's responsibility, with its own request hooks.
    if url:check();debit()
    client=QdrantClient(path=str(path)) if path else QdrantClient(url=url,api_key=api_key,timeout=3)
    if url:
        try:check()
        except BaseException:
            client.close();raise
    return {'status':'ok','backend':'qdrant','client':client}
