"""Host entrypoint for a trusted Web/Worker SIF and a separate offline CPU SIF.

Use the same absolute host paths in the application SIF and in systemd jobs.
No credentials in argv; no service installation, fallback or automatic deletion.
"""
from __future__ import annotations

import argparse
import hashlib
from ipaddress import IPv4Address
import json
import os
from pathlib import Path
import re
import sys


def absolute(value):
    if (not isinstance(value, str) or not value.startswith('/')
            or any(c in value for c in ':,\n\r\x00')
            or any(part in {'.', '..', ''} for part in value.split('/')[1:])):
        raise ValueError('An absolute normalized bind-safe Linux path is required')
    return value


def lan_ingress(config, port):
    ingress = config.get('lan_ingress')
    if ingress is None:
        return [], []
    if not isinstance(ingress, dict) or set(ingress) != {'ipv4', 'port', 'proxy_token_file'}:
        raise ValueError('LAN ingress requires ipv4, port and proxy_token_file')
    if not isinstance(ingress['ipv4'], str):
        raise ValueError('LAN ingress requires an explicit IPv4 address')
    ip = IPv4Address(ingress['ipv4'])
    ingress_port = ingress['port']
    if (ip.is_unspecified or ip.is_multicast or ip.is_reserved or ip.is_link_local
            or type(ingress_port) is not int or not 1024 <= ingress_port <= 65535
            or ingress_port == port or config.get('listen_host', '127.0.0.1') != '127.0.0.1'):
        raise ValueError('LAN ingress requires a dedicated IPv4 port and a loopback Web listener')
    token_file = absolute(ingress['proxy_token_file'])
    return ([(token_file, 'ro')], ['--env', f'CITRUS_LAN_AUTHORITY={ip}:{ingress_port}',
                                  '--env', f'CITRUS_LAN_UPSTREAM_PORT={port}',
                                  '--env', f'CITRUS_LAN_TOKEN_FILE={token_file}'])


def release_identity(config):
    source = Path(absolute(config['source_root']))
    manifest = (source / 'LINUX-SOURCE-MANIFEST.json').read_bytes()
    # Config contains paths and expected image hashes, never private file contents.
    configuration = json.dumps(config, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    return hashlib.sha256(hashlib.sha256(manifest).digest() + configuration).hexdigest()


def command(config, role):
    if role not in {'web', 'worker', 'prepare'}:
        raise ValueError('Unknown deployment role')
    runtime = absolute(config['runtime_root'])
    source = absolute(config['source_root'])
    app = absolute(config['app_sif'])
    sandbox = absolute(config['sandbox_sif'])
    host_python = absolute(config['host_python'])
    apptainer = absolute(config.get('apptainer_path', '/usr/bin/apptainer'))
    staging = absolute(config['code_staging'] if role == 'worker' else config['figure_staging'])
    namespace = config.get('namespace_sif', '')
    namespace_digest = config.get('namespace_sha256', '')
    if bool(namespace) != bool(namespace_digest):
        raise ValueError('Namespace SIF path and SHA-256 must be supplied together')
    if namespace:
        namespace = absolute(namespace)
    for digest in (config['app_sha256'], config['sandbox_sha256'], *([namespace_digest] if namespace else [])):
        if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest):
            raise ValueError('All configured SIF files require SHA-256 pins')
    if any(not path.endswith('.sif') for path in (app, sandbox, *([namespace] if namespace else []))):
        raise ValueError('Local .sif images are required')
    port = config.get('port', 8030)
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError('Invalid service port')
    result = [apptainer, 'exec', '--cleanenv', '--no-home', '--no-mount',
              'hostfs,cwd,home,bind-paths', '--pwd', source]
    # GPU exposure belongs to the trusted Web model runtime only. The ordinary
    # Code sandbox command never inherits this flag or these application binds.
    if role in {'web', 'worker'}:
        result += ['--env', 'CITRUS_WORKER_RELEASE=' + release_identity(config)]
    if role == 'worker' and os.getenv('INVOCATION_ID'):
        invocation = os.environ['INVOCATION_ID']
        if not re.fullmatch('[a-f0-9]{32}', invocation) or invocation == '0' * 32:
            raise ValueError('Invalid Worker systemd activation')
        result += ['--env', 'CITRUS_WORKER_ACTIVATION=' + invocation]
    if role != 'worker':
        gpu_device = config.get('gpu_device', 0)
        if type(gpu_device) is not int or not 0 <= gpu_device < 128:
            raise ValueError('Invalid GPU device index')
        result += ['--nv', '--env', 'CUDA_VISIBLE_DEVICES=' + str(gpu_device)]
        # Trusted Web uses the host resolver in the same network namespace.
        # Runtime configuration binds are otherwise disabled; the SIF's file
        # is empty. This never changes the host or the offline Code command.
        resolver = absolute(str(Path('/etc/resolv.conf').resolve(strict=True)))
        result += ['--bind', resolver + ':/etc/resolv.conf:ro']
    binds = [(runtime, 'rw'), (source, 'ro'), (staging, 'rw'),
             (str(Path(sandbox).parent), 'ro'), (apptainer, 'ro'), (host_python, 'ro'),
             (f'/run/user/{os.getuid()}', 'rw')]
    if role == 'worker' and namespace:
        binds += [(namespace, 'ro')]
    if role != 'worker':
        binds += [(absolute(config['corpus_root']), 'ro'),
                  (absolute(config['model_cache_root']), 'rw')]
    if role == 'web':
        shared_enabled = config.get('shared_literature_single_web', False)
        if type(shared_enabled) is not bool:
            raise ValueError('Invalid shared literature deployment mode')
        if shared_enabled:
            data = Path(absolute(config['corpus_root']))
            for private_dir in (data / 'shared_agent_literature', data / '_review_queue'):
                if not private_dir.is_dir() or private_dir.is_symlink():
                    raise ValueError('Provide existing private shared literature and review queue directories')
                binds.append((str(private_dir), 'rw'))
            result += ['--env', 'CITRUS_SHARED_CORPUS=single-web']
        ingress_binds, ingress_env = lan_ingress(config, port)
        binds += ingress_binds
        result += ingress_env
    if role == 'web' and config.get('browse_config'):
        browse_path = absolute(config['browse_config'])
        browse = json.loads(Path(browse_path).read_text(encoding='utf-8'))
        browser_image = absolute(browse['image'])
        browser_packages = absolute(browse['packages'])
        binds += [(browse_path, 'ro'), (browser_image, 'ro'), (browser_packages, 'ro')]
        result += ['--env', 'CITRUS_BROWSE_CONFIG=' + browse_path]
    for path, mode in dict(binds).items():
        result += ['--bind', f'{path}:{path}:{mode}']
    result += [app, '/usr/local/bin/python']
    if role == 'worker':
        result += [source + '/01_src/scripts/code_worker.py', '--runtime-root', runtime,
            '--staging-root', staging, '--backend', 'apptainer', '--sif-path', sandbox,
            '--image-id', 'sha256:' + config['sandbox_sha256'], '--apptainer-path', apptainer,
            '--host-python', host_python, '--service-path',
            source + '/01_src/src/code_execution/apptainer_service.py', '--development-oci']
        if namespace:
            result += ['--namespace-sif-path', namespace, '--namespace-sif-sha256', namespace_digest]
    else:
        result += [source + '/01_src/scripts/configured_local.py', '--runtime-root', runtime,
            '--model-config-root', absolute(config['model_config_root']),
            '--corpus-root', absolute(config['corpus_root']),
            '--model-cache-root', absolute(config['model_cache_root']), '--worker-staging', staging,
            '--backend', 'apptainer', '--image', sandbox, '--image-sha256', config['sandbox_sha256'],
            '--apptainer-path', apptainer, '--host-python', host_python,
            '--host', config.get('listen_host', '127.0.0.1'), '--port', str(port), '--require-full']
        if role == 'prepare':
            if config.get('existing_runtime'):
                raise ValueError('Do not prepare/reinitialize an existing runtime')
            result += ['--prepare-only']
        else:
            result += ['--offline-code']
            if config.get('existing_runtime'):
                result += ['--existing-runtime']
        if config.get('reuse_web_search_settings') is False:
            result += ['--without-source-web-search']
        # Existing configuration can live outside the writable runtime during
        # isolated acceptance. The Web alone may read its authorized key source.
        model_root = absolute(config['model_config_root'])
        if not Path(model_root).is_relative_to(runtime):
            result[result.index(app):result.index(app)] = ['--bind', f'{model_root}:{model_root}:ro']
    return result


def verify_image(path, expected):
    p = Path(path)
    if p.suffix != '.sif' or any(q.is_symlink() for q in (p, *p.parents)):
        raise ValueError('SIF must use a real immutable absolute path')
    before = p.stat()
    import stat
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_mode & 0o022:
        raise ValueError('SIF must be a nonlinked regular file without group/world write access')
    digest = hashlib.sha256()
    with p.open('rb') as stream:
        if not os.path.samestat(before, os.fstat(stream.fileno())):
            raise ValueError('SIF changed')
        for block in iter(lambda: stream.read(1024**2), b''):
            digest.update(block)
        after = os.fstat(stream.fileno())
    if (digest.hexdigest() != expected or not os.path.samestat(after, p.stat())
            or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
        raise ValueError('SIF pin mismatch or image changed')


def worker_dependency_inventory(config):
    # The App SIF may have an older libc than the host Apptainer binary.
    # Run this offline probe before entering the App SIF.
    source = Path(absolute(config['source_root']))
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(source / '01_src'))
    try:
        from src.code_execution.apptainer import ApptainerConfig, OfflineApptainerBackend
        backend = OfflineApptainerBackend(ApptainerConfig(
            image_id='sha256:' + config['sandbox_sha256'],
            staging_root=absolute(config['code_staging']),
            sif_path=absolute(config['sandbox_sif']),
            runtime=absolute(config.get('apptainer_path', '/usr/bin/apptainer')),
            host_python=absolute(config['host_python']),
        ))
        return backend.base_dependency_versions()
    except (OSError, RuntimeError, ValueError):
        return {}
    finally:
        sys.path.pop(0)


def _worker_activation(unit):
    import subprocess
    if not re.fullmatch(r'citrusscholar-worker@[a-zA-Z0-9_.-]+\.service', unit):
        raise ValueError('Invalid Worker unit name')
    try:
        receipt = subprocess.run(['/usr/bin/systemctl', '--user', 'show', unit,
            '--property=ActiveState', '--property=InvocationID'], capture_output=True,
            text=True, timeout=3, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    fields = dict(line.split('=', 1) for line in receipt.stdout.splitlines() if '=' in line)
    value = fields.get('InvocationID', '')
    if fields.get('ActiveState') == 'active' and re.fullmatch('[a-f0-9]{32}', value) and value != '0' * 32:
        return value
    return None


def wait_for_worker(config, *, worker_unit=None, timeout=90):
    """Wait for a fresh heartbeat from this release and the active Worker unit."""
    import time

    source = Path(absolute(config['source_root']))
    runtime = Path(absolute(config['runtime_root']))
    sys.path.insert(0, str(source / '01_src'))
    try:
        from src.code_execution.presence import worker_available
    finally:
        sys.path.pop(0)
    db = runtime / 'state' / 'code_executions.sqlite3'
    release = release_identity(config)
    since = time.time()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        activation = _worker_activation(worker_unit) if worker_unit else None
        if ((not worker_unit or activation) and worker_available(db, since=since, release=release, activation=activation)
                and (not worker_unit or _worker_activation(worker_unit) == activation)):
            return
        time.sleep(0.5)
    raise SystemExit('No new offline Code Worker heartbeat for the configured runtime')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--role', choices=('web', 'worker', 'prepare', 'wait-worker'), required=True)
    parser.add_argument('--print-command', action='store_true')
    parser.add_argument('--worker-unit', help='Expected systemd Worker unit for the startup gate')
    args = parser.parse_args()
    if sys.platform != 'linux' or os.getuid() == 0:
        parser.error('Run as the unprivileged Linux service user')
    config = json.loads(args.config.read_text(encoding='utf-8'))
    if args.role == 'wait-worker':
        if args.print_command:
            parser.error('wait-worker does not launch an Apptainer command')
        wait_for_worker(config, worker_unit=args.worker_unit)
        return
    if args.worker_unit:
        parser.error('--worker-unit applies only to wait-worker')
    argv = command(config, args.role)
    if args.print_command:
        print(json.dumps(argv))
        return
    prefixes = ['app', 'sandbox']
    if args.role == 'worker' and config.get('namespace_sif'):
        prefixes.append('namespace')
    for prefix in prefixes:
        verify_image(config[prefix + '_sif'], config[prefix + '_sha256'])
    threads = config.get('cpu_threads', 8)
    if type(threads) is not int or not 1 <= threads <= 64:
        raise ValueError('Invalid CPU thread budget')
    allowed = sorted(os.sched_getaffinity(0))
    os.sched_setaffinity(0, allowed[:threads])
    env = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': str(Path.home()), 'LANG': 'C.UTF-8',
           'GOMAXPROCS': str(threads), 'GOMEMLIMIT': '256MiB',
           'XDG_RUNTIME_DIR': f'/run/user/{os.getuid()}',
           'DBUS_SESSION_BUS_ADDRESS': f'unix:path=/run/user/{os.getuid()}/bus'}
    if args.role == 'worker':
        argv += ['--base-dependency-inventory', json.dumps(worker_dependency_inventory(config))]
    os.execve(argv[0], argv, env)


if __name__ == '__main__':
    main()
