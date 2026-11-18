"""Bounded read-only Windows/WSL diagnostic; does not change network or start WSL.

Public documentation only, default TLS verification, no model credentials, proxy
URLs, raw IPs, request bodies or exception messages in the report. An offline Code
container is not a VPN probe. This report cannot certify the N3 egress boundary.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

_PROBE = r'''
import ipaddress,json,os,socket,ssl,time,urllib.request,urllib.error,urllib.parse
hosts=['docs.python.org','docs.langchain.com']
proxies=urllib.request.getproxies()
metadata={}
for name in ['HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy']:
    raw=os.environ.get(name,'')
    parsed=urllib.parse.urlsplit(raw)
    metadata[name]={'present':bool(raw),'scheme':parsed.scheme if parsed.scheme in ['http','https','socks5','socks5h'] else 'other' if raw else '',
                    'credentials_present':bool(parsed.username or parsed.password)}
result={'proxy_environment':metadata,'system_or_environment_proxy_detected':bool(proxies.get('https')),'targets':[]}
for host in hosts:
    item={'domain':host}
    try:
        addresses={row[4][0] for row in socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)}
        classes=set()
        for address in addresses:
            ip=ipaddress.ip_address(address)
            classes.add('fake_ip_range' if ip.version==4 and ip in ipaddress.ip_network('198.18.0.0/15') else 'public' if ip.is_global else 'non_public')
        item['dns']={'status':'resolved','address_classes':sorted(classes),'count':len(addresses)}
    except Exception as exc:
        item['dns']={'status':'error','error_type':type(exc).__name__}
    item['https']=[]
    for mode in ['configured_default','configured_explicit_proxy']:
        proxy=proxies.get('https')
        if mode=='configured_explicit_proxy' and not proxy:
            item['https'].append({'mode':mode,'status':'not_run','reason':'no_configured_https_proxy'})
            continue
        started=time.monotonic()
        try:
            handlers=[urllib.request.HTTPSHandler(context=ssl.create_default_context())]
            if mode=='configured_explicit_proxy':
                handlers.append(urllib.request.ProxyHandler({'https':proxy}))
            opener=urllib.request.build_opener(*handlers)
            with opener.open('https://'+host+'/',timeout=5) as response:
                response.read(256)
                observed={'status':'http_response','http_status':response.status,'tls_verification':'enabled'}
        except urllib.error.HTTPError as exc:
            observed={'status':'http_response','http_status':exc.code,'tls_verification':'enabled'}
        except Exception as exc:
            reason=getattr(exc,'reason',exc)
            observed={'status':'error','error_type':type(reason).__name__,'tls_verification':'enabled'}
        item['https'].append({'mode':mode,**observed,'elapsed_ms':round((time.monotonic()-started)*1000)})
    result['targets'].append(item)
print(json.dumps(result))
'''


def run(command, timeout=35):
    try:
        completed = subprocess.run(command, capture_output=True, timeout=timeout)
        if completed.returncode:
            return {'status': 'error', 'reason': 'probe_process_failed', 'exit_code': completed.returncode}
        return json.loads(completed.stdout.decode('utf-8'))
    except subprocess.TimeoutExpired:
        return {'status': 'timed_out'}
    except Exception as exc:
        return {'status': 'error', 'error_type': type(exc).__name__}


def report():
    result = {'schema': 'citrus-network-diagnostic/1', 'network_changes': False,
              'n3_certified': False, 'windows': run([sys.executable, '-I', '-c', _PROBE]),
              'wsl': {'status': 'not_run'}, 'code_container': {'status': 'not_run'}}
    if sys.platform != 'win32':
        result['windows'] = {'status': 'not_run', 'reason': 'not_windows'}
        return result
    try:
        listed = subprocess.run(['wsl.exe', '--list', '--running', '--quiet'],
                                capture_output=True, timeout=5)
        text = listed.stdout.decode('utf-16-le', errors='replace')
        if listed.returncode or 'Ubuntu-24.04' not in text:
            result['wsl']['reason'] = 'distribution_not_running'
            return result
        prefix = ['wsl.exe', '--distribution', 'Ubuntu-24.04', '--exec']
        result['wsl'] = run(prefix + ['python3', '-I', '-c', _PROBE])
        docker = prefix + ['/usr/bin/docker', '--host', 'unix:///var/run/docker.sock']
        listed = subprocess.run(docker + ['ps', '--filter', 'label=org.citrusscholar.code.backend=offline-v1',
                                         '--format', '{{.ID}}'], capture_output=True, timeout=6)
        ids = listed.stdout.decode('ascii', errors='ignore').splitlines()
        if listed.returncode:
            result['code_container']['reason'] = 'docker_unavailable'
        elif not ids:
            result['code_container']['reason'] = 'no_running_actual_code_container'
        else:
            modes = []
            for identifier in ids[:4]:
                if not identifier.isalnum() or len(identifier) > 64:
                    continue
                inspected = subprocess.run(docker + ['inspect', '--format', '{{json .HostConfig.NetworkMode}}', identifier],
                                           capture_output=True, timeout=5)
                if inspected.returncode == 0:
                    modes.append(json.loads(inspected.stdout))
            result['code_container'] = {'status': 'observed', 'sampled': len(modes),
                'offline_count': modes.count('none'), 'https_probe': 'not_run',
                'reason': 'expected_offline' if modes and all(mode == 'none' for mode in modes) else 'network_mode_requires_review'}
    except subprocess.TimeoutExpired:
        result['wsl'] = {'status': 'timed_out'}
    except Exception as exc:
        result['wsl'] = {'status': 'error', 'error_type': type(exc).__name__}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output must be new; existing reports are not overwritten')
    value = report()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
    print(json.dumps(value, ensure_ascii=False))


if __name__ == '__main__':
    main()
