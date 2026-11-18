"""Persistent bounded observations, scoped by owner/session/turn/generation."""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time
import uuid

SOURCE_FIELDS=('source_ref','origin','document_key','title','display_label','locator','url',
               'evidence_level','retrieved_at','query_ref')


class EvidenceStore:
    def __init__(self,root: Path,*,owner,session_id,turn_id,generation):
        if not all(str(x).strip() for x in (owner,session_id,turn_id,generation)):
            raise ValueError('Explicit evidence scope required')
        self.scope={'owner':str(owner),'session_id':str(session_id),'turn_id':str(turn_id),'generation':str(generation)}
        self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True)
        self.path=self.root/'evidence.jsonl'
        self.lock=self.root/'.evidence.lock'
        if self.path.is_symlink() or self.lock.is_symlink():
            raise ValueError('evidence_symlink_denied')
    @contextmanager
    def _locked(self):
        deadline=time.monotonic()+5
        while True:
            try:
                fd=os.open(self.lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
                break
            except FileExistsError:
                if time.monotonic()>=deadline:raise RuntimeError('evidence_store_busy')
                time.sleep(.01)
        try:yield
        finally:os.close(fd);self.lock.unlink(missing_ok=True)
    def _rows(self):
        if not self.path.exists():return {}
        if self.path.stat().st_size>64*1024*1024:raise ValueError('evidence_store_limit')
        rows={}
        for line in self.path.read_text(encoding='utf-8').splitlines():
            row=json.loads(line)
            if row['scope']!=self.scope:raise PermissionError('evidence_scope_mismatch')
            if hashlib.sha256(row['text'].encode()).hexdigest()!=row['text_sha256']:
                raise ValueError('evidence_integrity_error')
            rows[row['ref']]=row
        return rows
    def _save(self,rows):
        temp=self.root/('.evidence.'+uuid.uuid4().hex+'.tmp')
        try:
            with temp.open('x',encoding='utf-8',newline='\n') as stream:
                for row in rows.values():stream.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n')
                stream.flush();os.fsync(stream.fileno())
            os.replace(temp,self.path)
        finally:temp.unlink(missing_ok=True)
    def put(self,record,*,query_ref):
        text=str(record.get('text') or '')
        if len(text)>2_000_000:raise ValueError('evidence_text_limit')
        key=str(record.get('chunk_id') or record.get('paper_id') or record.get('url') or '')
        if not key:raise ValueError('stable_document_identity_required')
        ref='ev:'+hashlib.sha256(json.dumps([self.scope,key,text],sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:32]
        stamp=datetime.now(timezone.utc).isoformat()
        level=str(record.get('evidence_level') or 'passage')
        source={name:record.get(name) for name in SOURCE_FIELDS}
        source.update(source_ref=ref,origin=record.get('origin') or 'local',document_key=key,
            title=record.get('title') or '',display_label=record.get('title') or key,
            locator=record.get('locator') or record.get('chunk_id') or key,
            evidence_level=level,retrieved_at=stamp,query_ref=query_ref)
        row={'ref':ref,'scope':self.scope,'paper_id':record.get('paper_id'),
            'chunk_id':record.get('chunk_id'),'text':text,'text_sha256':hashlib.sha256(text.encode()).hexdigest(),
            'source':source,'evidence_level':level,'states':{'candidate':True,'fetched':bool(text),'delivered':False,'cited':False}}
        with self._locked():
            rows=self._rows()
            if ref not in rows:rows[ref]=row;self._save(rows)
        return {'ref':ref,'title':record.get('title') or '', 'doi':record.get('doi') or '',
            'year':record.get('year') or '', 'corpus_family':record.get('corpus_family_id') or '',
            'source_type':record.get('source_type') or source['origin'],'evidence_level':level,
            'snippet':text[:240],'read_entry':{'ref':ref}}
    def read(self,*,ref=None,paper_id=None,chunk_id=None,offset=0,max_chars=4000):
        if type(offset)is not int or offset<0 or type(max_chars)is not int or not 1<=max_chars<=30000:
            raise ValueError('invalid_read_range')
        rows=self._rows()
        row=rows.get(ref) if ref else next((r for r in rows.values() if
            (paper_id is None or r.get('paper_id')==paper_id) and
            (chunk_id is None or r.get('chunk_id')==chunk_id)),None) if paper_id or chunk_id else None
        if row is None:
            return {'status':'evidence_expired','ref':ref,'range':None,'next_offset':None,
                    'evidence_level':None,'error':'Evidence is absent for this scope; no search was performed.'}
        text=row['text'];end=min(len(text),offset+max_chars)
        if offset>len(text):raise ValueError('offset_beyond_evidence')
        return {'status':'ok','ref':row['ref'],'range':{'start':offset,'end':end},
            'next_offset':end if end<len(text) else None,'evidence_level':row['evidence_level'],
            'text':text[offset:end],'source':row['source']}
    def mark(self,ref,*,delivered=False,cited=False):
        with self._locked():
            rows=self._rows()
            if ref not in rows:raise ValueError('evidence_expired')
            rows[ref]['states']['delivered']|=bool(delivered)
            rows[ref]['states']['cited']|=bool(cited)
            self._save(rows)
