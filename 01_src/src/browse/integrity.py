"""Pin and verify a versioned Browse deployment on Linux (standard library only)."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys


class IntegrityError(ValueError):
    """Browse deployment bytes or filesystem trust did not match the contract."""


@dataclass(frozen=True)
class Bundle:
    config_path: Path
    config_sha256: str
    image: Path
    image_sha256: str
    packages: Path
    packages_manifest: Path
    packages_manifest_sha256: str
    host_python: Path
    host_python_sha256: str
    hosts: tuple[str, ...]


_MAX_CONFIG = 32 * 1024
_MAX_MANIFEST = 4 * 1024 * 1024
_MAX_TREE_ENTRIES = 8192
_MAX_TREE_DEPTH = 40
_APPTAINER = '/usr/bin/apptainer'


def _absolute(value: object) -> Path:
    if (not isinstance(value, str) or not value.startswith('/')
            or any(char in ':,\\$%' or ord(char) < 32 or ord(char) == 127 for char in value)
            or any(part in {'', '.', '..'} for part in value.split('/')[1:])):
        raise IntegrityError('expected an absolute, canonical, bind-safe Linux path')
    return Path(value)


def _sha256(value: object, field: str) -> str:
    if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value):
        raise IntegrityError(f'{field} requires a lowercase SHA-256 pin')
    return value


def _same_object(before, after):
    return (os.path.samestat(before, after)
            and (before.st_mode, before.st_uid, before.st_gid, before.st_nlink,
                 before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            == (after.st_mode, after.st_uid, after.st_gid, after.st_nlink,
                after.st_size, after.st_mtime_ns, after.st_ctime_ns))


def _trusted(info, path, owners, *, directory=False, executable=False):
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if not kind(info.st_mode) or (not directory and info.st_nlink != 1):
        raise IntegrityError(f'{path}: expected a real {"directory" if directory else "single-link regular file"}')
    if info.st_uid not in owners or info.st_mode & 0o022:
        raise IntegrityError(f'{path}: untrusted owner or group/world write permission')
    if not directory and info.st_mode & 0o7000:
        raise IntegrityError(f'{path}: unexpected special mode bits')
    if executable and not os.access(path, os.X_OK):
        raise IntegrityError(f'{path}: not executable by the service user')


def _readonly_mount(path: Path, *, allow_parent=False) -> Path:
    """Locate the actual readonly bind, never the container's synthetic root."""
    try:
        options = {}
        for line in Path('/proc/self/mountinfo').read_text(encoding='utf-8').splitlines():
            fields = line.split()
            if len(fields) < 7:
                continue
            candidate = Path(fields[4])
            if candidate == Path('/') or not (candidate == path or (allow_parent and path.is_relative_to(candidate))):
                continue
            options.setdefault(candidate, set()).add('ro' in fields[5].split(','))
        if options:
            mount = max(options, key=lambda p: len(p.parts))
            if options[mount] == {True} and os.statvfs(path).f_flag & os.ST_RDONLY:
                return mount
    except (OSError, UnicodeError) as exc:
        raise IntegrityError(f'{path}: could not verify readonly bind') from exc
    raise IntegrityError(f'{path}: proven readonly bind required')


def _container_root_uid() -> int:
    """Translate host root through Apptainer's actual UID mapping."""
    try:
        for line in Path('/proc/self/uid_map').read_text(encoding='ascii').splitlines():
            inside, outside, size = map(int, line.split())
            if outside == 0 and size > 0:
                return inside
        return int(Path('/proc/sys/kernel/overflowuid').read_text(encoding='ascii'))
    except (OSError, ValueError) as exc:
        raise IntegrityError('could not verify host interpreter UID mapping') from exc


def _trusted_path(path: Path, *, root_only=False, directory=False, executable=False, mount=None):
    owners = {(_container_root_uid() if mount is not None else 0)} if root_only else {0, os.geteuid()}
    try:
        for ancestor in reversed(path.parents):
            info = ancestor.lstat()
            if mount is not None and not ancestor.is_relative_to(mount):
                # Apptainer synthesizes parents for binds. Their host permissions
                # were checked by the launcher; here reject aliases and rely on
                # the kernel-verified readonly mount below, not those stubs.
                if not stat.S_ISDIR(info.st_mode):
                    raise IntegrityError(f'{ancestor}: expected a real mount parent directory')
            else:
                _trusted(info, ancestor, owners, directory=True)
        info = path.lstat()
        _trusted(info, path, owners, directory=directory, executable=executable)
        return info
    except OSError as exc:
        raise IntegrityError(f'{path}: could not validate path: {exc.strerror}') from exc


def trusted_source_file(path: str, *, mounted=False) -> None:
    """Check a selected source member without normalizing away any symlink."""
    source = _absolute(path)
    _trusted_path(source, mount=_readonly_mount(source, allow_parent=True) if mounted else None)


@contextmanager
def _open_file(path: Path, before):
    try:
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
            if not _same_object(before, os.fstat(stream.fileno())):
                raise IntegrityError(f'{path}: changed before verification')
            yield stream
            if (not _same_object(before, os.fstat(stream.fileno()))
                    or not _same_object(before, path.lstat())):
                raise IntegrityError(f'{path}: changed during verification')
    except OSError as exc:
        raise IntegrityError(f'{path}: could not read file: {exc.strerror}') from exc


def _file_sha256(path: Path, *, root_only=False, executable=False, mounted=False) -> str:
    before = _trusted_path(path, root_only=root_only, executable=executable,
                           mount=_readonly_mount(path) if mounted else None)
    with _open_file(path, before) as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _read_file(path: Path, maximum: int, *, mounted=False) -> bytes:
    before = _trusted_path(path, mount=_readonly_mount(path) if mounted else None)
    with _open_file(path, before) as stream:
        body = stream.read(maximum + 1)
        if len(body) > maximum:
            raise IntegrityError(f'{path}: file exceeds verification size limit')
        return body


def _json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':')) + '\n').encode('ascii')


def _hosts(value) -> tuple[str, ...]:
    if (not isinstance(value, (tuple, list)) or not 1 <= len(value) <= 40
            or any(not isinstance(host, str) or not host or len(host) > 253 for host in value)):
        raise IntegrityError('expected 1 to 40 Browse host names')
    return tuple(value)


def _paths(image: str, packages: str, host_python: str, manifest: str, config: str):
    image, packages, host_python, manifest, config = map(_absolute, (image, packages, host_python, manifest, config))
    if image.suffix != '.sif':
        raise IntegrityError(f'{image}: expected a local .sif image')
    files = (image, host_python, manifest, config)
    if len(set(files)) != len(files) or any(path.is_relative_to(packages) for path in files):
        raise IntegrityError('image, interpreter, manifest and config must be distinct and outside the package tree')
    return image, packages, host_python, manifest, config


def load_bundle(path: str, expected_sha256: str | None = None, *, mounted=False) -> Bundle:
    """Read the config, requiring the host launcher's pin for container binds."""
    if mounted:
        _sha256(expected_sha256, 'config_sha256')
    config_path = _absolute(path)
    body = _read_file(config_path, _MAX_CONFIG, mounted=mounted)
    config_sha256 = hashlib.sha256(body).hexdigest()
    if expected_sha256 is not None and config_sha256 != _sha256(expected_sha256, 'config_sha256'):
        raise IntegrityError(f'{config_path}: Browse configuration SHA-256 mismatch')
    try:
        data = json.loads(body)
        image, packages, host_python, manifest, _ = _paths(
            data['image'], data['packages'], data['host_python'], data['packages_manifest'], path)
        return Bundle(config_path, config_sha256, image, _sha256(data['image_sha256'], 'image_sha256'),
                      packages, manifest, _sha256(data['packages_manifest_sha256'], 'packages_manifest_sha256'),
                      host_python, _sha256(data['host_python_sha256'], 'host_python_sha256'), _hosts(data['hosts']))
    except IntegrityError:
        raise
    except (KeyError, TypeError, ValueError, RecursionError) as exc:
        detail = f'missing field {exc.args[0]}' if isinstance(exc, KeyError) else 'invalid configuration JSON'
        raise IntegrityError(f'{config_path}: {detail}') from exc


def _tree(packages: Path, *, mounted=False) -> dict:
    """Inventory every member, including empty directories and hidden files."""
    owners = {0, os.geteuid()}
    entries = {}

    def walk(path, before, depth):
        if depth > _MAX_TREE_DEPTH or len(entries) >= _MAX_TREE_ENTRIES:
            raise IntegrityError(f'{packages}: package tree exceeds verification limits')
        relative = path.relative_to(packages).as_posix()
        if stat.S_ISDIR(before.st_mode):
            _trusted(before, path, owners, directory=True)
            entries[relative] = {'type': 'directory', 'mode': stat.S_IMODE(before.st_mode)}
            with os.scandir(path) as children:
                for child in children:
                    walk(path / child.name, child.stat(follow_symlinks=False), depth + 1)
            if not _same_object(before, path.lstat()):
                raise IntegrityError(f'{path}: directory changed during verification')
        else:
            _trusted(before, path, owners)
            with _open_file(path, before) as stream:
                entries[relative] = {'type': 'file', 'mode': stat.S_IMODE(before.st_mode),
                                     'size': before.st_size, 'sha256': hashlib.file_digest(stream, 'sha256').hexdigest()}

    try:
        mount = _readonly_mount(packages) if mounted else None
        walk(packages, _trusted_path(packages, directory=True, mount=mount), 0)
    except OSError as exc:
        raise IntegrityError(f'{packages}: could not enumerate package tree: {exc.strerror}') from exc
    if not any(entry['type'] == 'file' for entry in entries.values()):
        raise IntegrityError(f'{packages}: empty package tree')
    return {'format': 1, 'entries': entries}


def verify_host_python(bundle: Bundle, *, mounted=False) -> None:
    if _file_sha256(bundle.host_python, root_only=True, executable=True,
                    mounted=mounted) != bundle.host_python_sha256:
        raise IntegrityError(f'{bundle.host_python}: host interpreter SHA-256 mismatch')


def verify_bundle(bundle: Bundle, *, mounted=False) -> None:
    verify_host_python(bundle, mounted=mounted)
    expected = _read_file(bundle.packages_manifest, _MAX_MANIFEST, mounted=mounted)
    if hashlib.sha256(expected).hexdigest() != bundle.packages_manifest_sha256:
        raise IntegrityError(f'{bundle.packages_manifest}: package manifest SHA-256 mismatch')
    actual = _tree(bundle.packages, mounted=mounted)
    if _json_bytes(actual) != expected:
        try:
            entries = json.loads(expected)['entries']
            if not isinstance(entries, dict):
                raise TypeError
        except (KeyError, TypeError, ValueError, UnicodeError, RecursionError) as exc:
            raise IntegrityError(f'{bundle.packages_manifest}: invalid package manifest') from exc
        present = actual['entries']
        different = [name for name in sorted(entries.keys() | present.keys()) if entries.get(name) != present.get(name)]
        suffix = f' ({", ".join(repr(name) for name in different[:4])})' if different else ''
        raise IntegrityError(f'{bundle.packages}: package tree does not match fixed manifest{suffix}')
    if _file_sha256(bundle.image, mounted=mounted) != bundle.image_sha256:
        raise IntegrityError(f'{bundle.image}: renderer SIF SHA-256 mismatch')


def _write_new(path: Path, contents: bytes) -> None:
    _trusted_path(path.parent, directory=True)
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        with os.fdopen(os.open(path, flags, 0o600), 'wb') as stream:
            stream.write(contents)
    except OSError as exc:
        raise IntegrityError(f'{path}: could not create new pin file: {exc.strerror}') from exc


def pin_bundle(*, image: str, packages: str, host_python: str, manifest: str, config: str, hosts) -> None:
    image_path, packages_path, python_path, manifest_path, config_path = _paths(
        image, packages, host_python, manifest, config)
    hosts = _hosts(hosts)
    for path in (manifest_path, config_path):
        _trusted_path(path.parent, directory=True)
        if os.path.lexists(path):
            raise IntegrityError(f'{path}: refusing to replace an existing pin file')
    contents = _json_bytes(_tree(packages_path))
    if len(contents) > _MAX_MANIFEST:
        raise IntegrityError('package manifest exceeds verification size limit')
    data = {'image': image, 'image_sha256': _file_sha256(image_path), 'packages': packages,
            'packages_manifest': manifest, 'packages_manifest_sha256': hashlib.sha256(contents).hexdigest(),
            'host_python': host_python, 'host_python_sha256': _file_sha256(python_path, root_only=True, executable=True),
            'hosts': hosts}
    _write_new(manifest_path, contents)
    _write_new(config_path, _json_bytes(data))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    pin = commands.add_parser('pin', help='create a new manifest and config from a trusted build')
    for flag in ('image', 'packages', 'host-python', 'manifest', 'config'):
        pin.add_argument('--' + flag, required=True)
    pin.add_argument('--host', action='append', dest='hosts', required=True)
    verify = commands.add_parser('verify', help='check a deployed Browse bundle without starting it')
    verify.add_argument('--config', required=True)
    boot = commands.add_parser('exec', help=argparse.SUPPRESS)
    boot.add_argument('config')
    boot.add_argument('config_sha256')
    boot.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if sys.platform != 'linux':
        parser.error('Browse bundle verification requires Linux')
    try:
        if args.action == 'pin':
            values = vars(args).copy()
            del values['action']
            pin_bundle(**values)
            print(f'Browse pins written: {args.config}')
        elif args.action == 'verify':
            bundle = load_bundle(args.config)
            verify_bundle(bundle)
            print(f'Browse bundle verified: {bundle.config_sha256}')
        else:
            command = args.command[1:] if args.command[:1] == ['--'] else args.command
            if not command or command[0] != _APPTAINER or os.geteuid() == 0:
                raise IntegrityError('expected the unprivileged Browse Apptainer launch')
            verify_bundle(load_bundle(args.config, args.config_sha256))
            os.execv(command[0], command)
    except (IntegrityError, OSError) as exc:
        if args.action == 'exec':
            # The service reads stdout as IPC; no local paths leave the host logs.
            print(_json_bytes({'kind': 'integrity_error', 'detail': str(exc)}).decode('ascii'), end='', flush=True)
        parser.exit(2, f'Browse bundle integrity: {exc}\n')


if __name__ == '__main__':
    main()
