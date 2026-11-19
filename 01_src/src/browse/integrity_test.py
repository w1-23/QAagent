"""Browse pinning tests use real Linux metadata, without starting any service."""
import asyncio
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src.browse import integrity


@unittest.skipUnless(sys.platform == 'linux', 'uses the Linux Browse trust contract')
class BrowseBundleTest(unittest.TestCase):
    def setUp(self):
        self.python = Path('/usr/bin/python3').resolve()
        if not self.python.is_file():
            self.skipTest('requires a system Python interpreter in /usr/bin')
        self.addCleanup(os.umask, os.umask(0o077))
        temp = tempfile.TemporaryDirectory(prefix='browse-integrity-test-', dir=Path.home())
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.image = self.root / 'playwright.sif'
        self.image.write_bytes(b'pinned image')
        self.packages = self.root / 'browser-python'
        self.packages.mkdir(mode=0o750)
        (self.packages / 'empty').mkdir()
        self.module = self.packages / 'module.py'
        self.module.write_bytes(b'pinned module')
        self.native = self.packages / 'native.so'
        self.native.write_bytes(b'pinned native binary')
        self.native.chmod(0o755)
        self.manifest = self.root / 'browser-python.manifest.json'
        self.config = self.root / 'browse.json'
        self.arguments = dict(image=str(self.image), packages=str(self.packages), host_python=str(self.python),
                              manifest=str(self.manifest), config=str(self.config), hosts=['example.org'])
        integrity.pin_bundle(**self.arguments)
        self.bundle = integrity.load_bundle(str(self.config))

    def test_container_mount_requires_exact_pin_and_effective_readonly(self):
        mountinfo = f'10 1 0:2 / {self.config} ro,nodev - ext4 /dev/example rw\n'
        with patch.object(Path, 'read_text', return_value=mountinfo), patch.object(os, 'statvfs') as st:
            st.return_value = SimpleNamespace(f_flag=os.ST_RDONLY)
            with self.assertRaisesRegex(integrity.IntegrityError, 'config_sha256'):
                integrity.load_bundle(str(self.config), mounted=True)
            self.assertEqual(integrity.load_bundle(str(self.config), self.bundle.config_sha256, mounted=True), self.bundle)
            st.return_value = SimpleNamespace(f_flag=0)
            with self.assertRaisesRegex(integrity.IntegrityError, 'readonly bind'):
                integrity.load_bundle(str(self.config), self.bundle.config_sha256, mounted=True)

    def test_fixed_manifest_is_complete_and_reproducible(self):
        integrity.verify_bundle(self.bundle)
        entries = json.loads(self.manifest.read_bytes())['entries']
        self.assertEqual(set(entries), {'.', 'empty', 'module.py', 'native.so'})
        self.assertEqual(entries['native.so']['mode'], 0o755)
        args = {**self.arguments, 'manifest': str(self.root / 'second.manifest.json'),
                'config': str(self.root / 'second.json')}
        integrity.pin_bundle(**args)
        self.assertEqual(self.manifest.read_bytes(), Path(args['manifest']).read_bytes())
        with self.assertRaisesRegex(integrity.IntegrityError, 'refusing to replace'):
            integrity.pin_bundle(**self.arguments)

    def test_mutated_added_hidden_removed_and_changed_modes_fail(self):
        operations = [
            (lambda: self.module.write_bytes(b'mutated data!'), "module.py"),
            (lambda: (self.packages / '.unlisted').write_bytes(b'new member'), ".unlisted"),
            (lambda: (self.packages / 'empty').rmdir(), 'empty'),
            (lambda: self.native.chmod(0o644), 'native.so'),
        ]
        for operation, member in operations:
            with self.subTest(member=member):
                operation()
                with self.assertRaisesRegex(integrity.IntegrityError, f'fixed manifest.*{member}'):
                    integrity.verify_bundle(self.bundle)
                self.module.write_bytes(b'pinned module')
                (self.packages / '.unlisted').unlink(missing_ok=True)
                (self.packages / 'empty').mkdir(exist_ok=True)
                self.native.chmod(0o755)
                integrity.verify_bundle(self.bundle)

    def test_extra_directory_symlink_hardlink_and_fifo_fail(self):
        path = self.packages / 'new-member'
        path.mkdir()
        with self.assertRaisesRegex(integrity.IntegrityError, 'fixed manifest.*new-member'):
            integrity.verify_bundle(self.bundle)
        path.rmdir()
        for make in (lambda: path.symlink_to(self.image), lambda: os.link(self.image, path),
                     lambda: os.mkfifo(path)):
            with self.subTest(make=make):
                make()
                with self.assertRaisesRegex(integrity.IntegrityError, 'single-link regular file'):
                    integrity.verify_bundle(self.bundle)
                path.unlink()

    def test_canonical_paths_and_other_user_writable_permissions_fail(self):
        self.root.chmod(0o770)
        try:
            with self.assertRaisesRegex(integrity.IntegrityError, 'group/world write'):
                integrity.load_bundle(str(self.config))
        finally:
            self.root.chmod(0o700)
        self.module.chmod(0o666)
        with self.assertRaisesRegex(integrity.IntegrityError, 'group/world write'):
            integrity.verify_bundle(self.bundle)
        self.module.chmod(0o644)
        alias = self.root / 'alias'
        alias.symlink_to(self.root, target_is_directory=True)
        for path in (f'{alias}/browse.json', f'{self.root}/empty/../browse.json', f'{self.root}//browse.json'):
            with self.subTest(path=path), self.assertRaises(integrity.IntegrityError):
                integrity.load_bundle(path)
        fields = list(self.module.stat())
        fields[4] = 65534
        with self.assertRaisesRegex(integrity.IntegrityError, 'untrusted owner'):
            integrity._trusted(os.stat_result(fields), self.module, {0, 1000})

    def test_image_and_manifest_require_exact_hash(self):
        self.manifest.write_bytes(self.manifest.read_bytes() + b' ')
        with self.assertRaisesRegex(integrity.IntegrityError, 'manifest SHA-256 mismatch'):
            integrity.verify_bundle(self.bundle)
        self.manifest.write_bytes(self.manifest.read_bytes()[:-1])
        self.image.write_bytes(b'other image')
        with self.assertRaisesRegex(integrity.IntegrityError, 'SIF SHA-256 mismatch'):
            integrity.verify_bundle(self.bundle)

    def test_config_and_host_interpreter_require_exact_hash(self):
        data = json.loads(self.config.read_bytes())
        data['host_python_sha256'] = '0' * 64
        self.config.write_text(json.dumps(data))
        with self.assertRaisesRegex(integrity.IntegrityError, 'configuration SHA-256 mismatch'):
            integrity.load_bundle(str(self.config), self.bundle.config_sha256)
        with self.assertRaisesRegex(integrity.IntegrityError, 'host interpreter SHA-256 mismatch'):
            integrity.verify_bundle(integrity.load_bundle(str(self.config)))
        data.pop('host_python_sha256')
        self.config.write_text(json.dumps(data))
        with self.assertRaisesRegex(integrity.IntegrityError, 'missing field host_python_sha256'):
            integrity.load_bundle(str(self.config))

    @unittest.skipIf(sys.platform == 'linux' and os.geteuid() == 0, 'requires an unprivileged Linux user')
    def test_host_guard_rechecks_after_initial_verification(self):
        integrity.verify_bundle(self.bundle)
        args = ['integrity.py', 'exec', str(self.config), self.bundle.config_sha256, '--',
                '/usr/bin/apptainer', 'exec']
        with patch.object(sys, 'argv', args), patch.object(integrity.os, 'execv') as execute:
            integrity.main()
            execute.assert_called_once_with('/usr/bin/apptainer', ['/usr/bin/apptainer', 'exec'])
            execute.reset_mock()
            self.module.write_text('changed since the Web launcher verified')
            output = io.StringIO()
            with redirect_stdout(output), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as failure:
                integrity.main()
            self.assertEqual(failure.exception.code, 2)
            self.assertEqual(json.loads(output.getvalue())['kind'], 'integrity_error')
            self.assertIn('module.py', output.getvalue())
            execute.assert_not_called()

    def test_renderer_uses_host_guard_and_reports_integrity_failure(self):
        from src.browse import service

        def renderer(bundle):
            return service.Renderer(service.Config(bundle.image, bundle.packages, frozenset(bundle.hosts),
                                                   str(bundle.host_python), bundle))

        async def verify_start():
            with patch.object(service.asyncio, 'create_subprocess_exec') as spawn:
                bad = renderer(replace(self.bundle, host_python_sha256='0' * 64))
                with self.assertLogs(service.__name__, 'ERROR') as logs:
                    with self.assertRaisesRegex(service.BrowseError, '^browse_bundle_integrity$'):
                        await bad.start()
                self.assertIn('host interpreter SHA-256 mismatch', logs.output[0])
                spawn.assert_not_called()

                good = renderer(self.bundle)
                with patch.object(service, 'trusted_source_file'), patch.object(good, 'receive') as receive:
                    receive.return_value = {'kind': 'integrity_error', 'detail': 'package tree changed'}
                    with self.assertLogs(service.__name__, 'ERROR') as logs:
                        with self.assertRaisesRegex(service.BrowseError, '^browse_bundle_integrity$'):
                            await good.start()
                    self.assertIn('package tree changed', logs.output[0])
                argv = spawn.call_args.args
                verifier = next(index for index, arg in enumerate(argv) if arg.endswith('/integrity.py'))
                self.assertEqual(argv[verifier - 3:verifier], (str(self.python), '-I', '-S'))
                self.assertEqual(argv[verifier + 1:verifier + 6],
                                 ('exec', str(self.config), self.bundle.config_sha256, '--', '/usr/bin/apptainer'))
                self.assertIn(f'{self.packages}:/browser-python:ro', argv)

        asyncio.run(verify_start())

    def test_web_launcher_binds_only_verified_browse_assets(self):
        from scripts.apptainer_launch import command

        source = self.root / 'source'
        browse = source / '01_src' / 'src' / 'browse'
        browse.mkdir(parents=True)
        for name in ('integrity.py', 'sandbox.py'):
            (browse / name).write_text('# trusted test source\n')
        environment = dict(runtime_root=str(self.root / 'runtime'), source_root=str(source),
                           app_sif=str(self.root / 'app.sif'), sandbox_sif=str(self.root / 'sandbox.sif'),
                           host_python=str(self.python), code_staging=str(self.root / 'code'),
                           figure_staging=str(self.root / 'figures'), app_sha256='0' * 64, sandbox_sha256='1' * 64,
                           corpus_root=str(self.root / 'corpus'), model_cache_root=str(self.root / 'cache'),
                           model_config_root=str(self.root / 'model'), browse_config=str(self.config))
        with patch('sys.path', sys.path.copy()):
            result = command(environment, 'web')
            self.assertIn('CITRUS_BROWSE_CONFIG_SHA256=' + self.bundle.config_sha256, result)
            for path in (self.config, self.manifest, self.image, self.packages, self.python):
                self.assertIn(f'{path}:{path}:ro', result)
            with self.assertRaisesRegex(ValueError, 'outside writable deployment binds'):
                command({**environment, 'runtime_root': str(self.root)}, 'web')
            # A Browse failure must not affect the independent offline worker.
            self.module.unlink()
            self.assertNotIn(str(self.config), ' '.join(command(environment, 'worker')))
            with self.assertRaisesRegex(ValueError, 'Browse bundle integrity.*module.py'):
                command(environment, 'web')


if __name__ == '__main__':
    unittest.main()
