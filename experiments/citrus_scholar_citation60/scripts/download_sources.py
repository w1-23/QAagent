#!/usr/bin/env python3
"""Download only manually licensed, explicit HTTPS direct PDF URLs. Never bypass paywalls."""
import argparse,urllib.request,urllib.parse,hashlib,time,datetime,sys,io
from pathlib import Path
from common import ROOT,csvrows,write_csv,utc,sha256_file
p=argparse.ArgumentParser();p.add_argument('--dry-run',action='store_true');p.add_argument('--execute',action='store_true');p.add_argument('--source-id',default='');a=p.parse_args()
if not(a.execute or a.dry_run):p.error('Use --dry-run or --execute')
source_path='02_sources/manifests/materials_tracking.csv';records=csvrows(source_path)
allowed=('pmc.ncbi.nlm.nih.gov','www.ncbi.nlm.nih.gov','cdn.ncbi.nlm.nih.gov','www.mdpi.com','mdpi.com','www.nature.com','www.frontiersin.org','journals.plos.org','www.jstage.jst.go.jp','www.cell.com','static-content.springer.com')
def allowed_url(s):
 q=urllib.parse.urlparse(s);return q.scheme=='https' and (q.hostname in allowed)
class SafeRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self, req,fp,code,msg,headers,newurl):
  if not allowed_url(newurl):raise RuntimeError('redirect_host_not_allowed:'+str(newurl))
  return super().redirect_request(req,fp,code,msg,headers,newurl)
for r in records:
 sid=r['source_id'];
 if a.source_id and sid!=a.source_id:continue
 if not sid.startswith('D'):continue
 url=r['pdf_direct_url'].strip();legal=r['legal_fulltext_download'].strip().upper()
 destination=ROOT/'02_sources/pdf'/f'{sid}.pdf'
 if destination.exists():
  r['download_status']='PRESENT';r['source_sha256']=sha256_file(destination)
  print(sid,'already present SHA=',r['source_sha256']);continue
 if not url or legal!='YES':print(sid,'SKIP: pdf_direct_url and manual legal_fulltext_download=YES required');continue
 if not allowed_url(url):print(sid,'BLOCKED: not allowlisted HTTPS host');continue
 if a.dry_run:print(sid,'WOULD DOWNLOAD',url);continue
 try:
  opener=urllib.request.build_opener(SafeRedirect())
  req=urllib.request.Request(url,headers={'User-Agent':'CitrusScholarResearchEvaluator/1.0 (non-commercial research; respects public licensing)'})
  with opener.open(req,timeout=35) as resp:
   payload=resp.read(35*1024*1024+1)
   if len(payload)>35*1024*1024:raise ValueError('PDF over 35MB limit')
   if not payload.lstrip(b'\xef\xbb\xbf\x00 \r\n').startswith(b'%PDF-'):raise ValueError('not a valid direct PDF; do not save HTML as PDF')
  destmp=destination.with_suffix('.pdf.tmp');destmp.write_bytes(payload);destmp.replace(destination)
  r['download_status']='DOWNLOADED';r['downloaded_utc']=utc();r['source_sha256']=sha256_file(destination)
  print(sid,'OK',len(payload),r['source_sha256'])
 except Exception as e:
  r['download_status']='FAILED';r['notes']=(r.get('notes','')+' | '+str(e))[:1000];print(sid,'FAILED',str(e))
if a.execute:write_csv(source_path,records)
