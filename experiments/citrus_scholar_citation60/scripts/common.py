"""File/CSV helpers used by CitrusScholar experiment scripts. Only stdlib."""
from pathlib import Path
import csv, json, hashlib, datetime, os
ROOT=Path(__file__).resolve().parents[1]
def csvrows(rel):
 p=ROOT/rel
 with p.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def write_csv(rel,items,fields=None):
 p=ROOT/rel;p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('w',encoding='utf-8-sig',newline='') as f:
  w=csv.DictWriter(f,fieldnames=fields or list(items[0]) if items else fields or []);w.writeheader();w.writerows(items)
def sha256_file(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def utc():return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')
def jsondump(rel,obj):
 p=ROOT/rel;p.parent.mkdir(parents=True,exist_ok=True)
 tmp=p.with_suffix(p.suffix+'.tmp');tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8');os.replace(tmp,p)
def source_safe_path(rel):
 p=(ROOT/rel).resolve()
 if not p.is_relative_to(ROOT.resolve()):raise ValueError('path escape')
 return p
