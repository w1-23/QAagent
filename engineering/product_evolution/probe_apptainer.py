"""Read-only runtime probe for the user's existing SIF; run over SSH stdin.

Runs only fixed engineering inspection code in a transient bounded user unit.
Does not access application state, credentials, accounts, or research data.
"""
import json
import os
import subprocess

image = '/home/WZQ/CitrusQA/container/citrusqa.sif'
cpus = sorted(os.sched_getaffinity(0))[:2]
code = '''import os,socket,json
from pathlib import Path
s=socket.socket(); s.settimeout(1)
print(json.dumps({'netns':os.readlink('/proc/self/ns/net'),'interfaces':socket.if_nameindex(),'host8030_connect_errno':s.connect_ex(('127.0.0.1',8030)),'private_state_visible':Path('/home/WZQ/citrusqa/state').exists(),'status':[x for x in Path('/proc/self/status').read_text().splitlines() if x.startswith(('NoNewPrivs:','CapEff:'))]}))
'''
command = ['systemd-run','--user','--pipe','--wait','--collect',
           '-p','NoNewPrivileges=yes','-p','MemoryMax=512M',
           '-p','MemorySwapMax=0','-p','TasksMax=128','-p','CPUQuota=100%',
           '-p','RuntimeMaxSec=30','-p','KillMode=control-group',
           '--','/usr/bin/env','-i','PATH=/usr/local/bin:/usr/bin:/bin','HOME=/tmp',
           'GOMAXPROCS=2','GOMEMLIMIT=128MiB','OPENBLAS_NUM_THREADS=1','OMP_NUM_THREADS=1',
           '/usr/bin/taskset','-c',','.join(map(str,cpus)),
           '/usr/bin/apptainer','exec','--containall','--cleanenv','--no-home',
           '--no-mount','hostfs,cwd,home,bind-paths,sys','--net','--network','none','--pid','--no-privs',
           image,'python','-c',code]
run = subprocess.run(command,capture_output=True,timeout=50)
print(json.dumps({'exit_code':run.returncode,'cpu_affinity':cpus,
                  'stdout':run.stdout.decode('utf-8','replace')[-4000:],
                  'stderr':run.stderr.decode('utf-8','replace')[-2000:]},ensure_ascii=False))
