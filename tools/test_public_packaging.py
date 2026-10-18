"""Source-release checks. Fixtures are ephemeral silence, never voice recordings."""
import hashlib
import json
import os
import re
import tempfile
import unittest
import wave
import uuid
from pathlib import Path
from unittest import mock

from add_local_voice import add_voice
import build_app
import preview_app
from build_app import catalog_files


class PublicPackagingTests(unittest.TestCase):
    def fixture(self, root):
        path = root/'silence-format-fixture.wav'
        with wave.open(str(path), 'wb') as stream:
            stream.setparams((1, 2, 24000, 24000, 'NONE', 'not compressed'))
            stream.writeframes(b'\0\0'*24000)
        return path

    def test_confirmation_required_and_reference_not_replaced(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = self.fixture(root)
            before = original.read_bytes()
            output = root/'local-catalog'
            with self.assertRaises(ValueError):
                add_voice(output,'local-fixture','Format fixture',original,'Format fixture only.',False)
            self.assertFalse(output.exists())
            result = add_voice(output,'local-fixture','Format fixture',original,'Format fixture only.',True)
            self.assertEqual(result['reference_sha256'],hashlib.sha256(before).hexdigest())
            self.assertEqual(original.read_bytes(),before)
            snapshot = (output/'approved-voices.json').read_bytes()
            with self.assertRaises(ValueError):
                add_voice(output,'local-fixture','Duplicate',original,'Duplicate.',True)
            self.assertEqual((output/'approved-voices.json').read_bytes(),snapshot)
            self.assertEqual(len(catalog_files(output)),2)

    def test_outside_catalog_asset_and_tampering_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = self.fixture(root)
            output = root/'local-catalog'
            add_voice(output,'local-fixture','Format fixture',original,'Format fixture only.',True)
            path = output/'approved-voices.json'
            catalog = json.loads(path.read_text())
            catalog['voices'][0]['reference']['path'] = '../silence-format-fixture.wav'
            path.write_text(json.dumps(catalog))
            with self.assertRaises(ValueError): catalog_files(output)
            catalog['voices'][0]['reference']['path'] = 'approved-references/local-fixture.wav'
            path.write_text(json.dumps(catalog))
            (output/'approved-references/local-fixture.wav').write_bytes(b'not the declared audio')
            with self.assertRaises(ValueError): catalog_files(output)

    def test_fresh_public_catalog_has_no_private_assets(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(catalog_files(root/'config'),[Path('approved-voices.json')])
        self.assertEqual(json.loads((root/'config/approved-voices.json').read_text())['voices'],[])


class PreviewToolTests(unittest.TestCase):
    def test_prune_keeps_newest_and_nothing_else(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            root = home/preview_app.PREVIEW_DATA
            (root/'Logs').mkdir(parents=True)
            (root/'Recordings').mkdir()
            for index in range(8):
                key = str(uuid.UUID(int=index))
                for path in (root/('library-request-'+key+'.json'), root/'Logs'/('run-'+key+'.log')):
                    path.write_text('x')
                    os.utime(path, (1000+index, 1000+index))
            kept = [root/'Logs/last-run.log', root/'Recordings/take.json', root/'project.json']
            for path in kept:
                path.write_text('keep')
            with mock.patch.object(Path, 'home', return_value=home):
                removed = preview_app.prune_preview_data(root, 5)
            self.assertEqual(len(removed), 6)
            self.assertEqual(sorted(p.name for p in root.glob('library-request-*.json')),
                             ['library-request-'+str(uuid.UUID(int=i))+'.json' for i in range(3, 8)])
            self.assertEqual(sorted(p.name for p in (root/'Logs').glob('run-*.log')),
                             ['run-'+str(uuid.UUID(int=i))+'.log' for i in range(3, 8)])
            self.assertTrue(all(p.read_text() == 'keep' for p in kept))
            self.assertEqual(preview_app.prune_preview_data(root/'missing', 5), [])

    def test_staged_worker_prunes_only_preview_dir(self):
        source = (preview_app.ROOT/'src/worker.py').read_text()
        staged = preview_app.preview_worker_source(source)
        compile(staged, 'preview-worker.py', 'exec')
        self.assertIn('Offline Voice Studio Open Source Preview', staged)
        self.assertIn('prune_preview_data(DATA,%d)' % preview_app.PREVIEW_KEEP, staged)
        self.assertNotIn('prune_preview_data', source)

    def test_rebuild_cleans_only_preview_outputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            build = Path(temporary)
            (build/'OfflineVoiceStudioPreview.app/Contents').mkdir(parents=True)
            (build/'PreviewSwiftModuleCache/x').mkdir(parents=True)
            (build/'PreviewVoiceStudio.swift').write_text('x')
            (build/'preview-worker.py').write_text('x')
            (build/'SwiftModuleCache').mkdir()
            (build/'other.txt').write_text('keep')
            self.assertEqual(sorted(preview_app.clean_preview_outputs(build)), sorted(preview_app.PREVIEW_OUTPUTS))
            self.assertEqual(sorted(p.name for p in build.iterdir()), ['SwiftModuleCache', 'other.txt'])
            self.assertEqual(preview_app.clean_preview_outputs(build), [])

    def test_macos_minimum_consistent(self):
        self.assertEqual(preview_app.MIN_MACOS, build_app.MIN_MACOS)
        for module in (preview_app, build_app):
            text = Path(module.__file__).read_text()
            self.assertNotIn('macos14', text)
            self.assertIn('LSMinimumSystemVersion=MIN_MACOS', text)
            self.assertEqual(module.SWIFT_TARGET, 'arm64-apple-macos'+module.MIN_MACOS)
        readme = (preview_app.ROOT/'README.md').read_text()
        self.assertIn('macOS 26.2', readme)
        self.assertNotIn('编译目标为 macOS 14', readme)
        for phrase in ('--rebuild', '--keep-cache', '运行测试', 'Open Source Preview'):
            self.assertIn(phrase, readme)


class PreviewRetentionSafetyTests(unittest.TestCase):
    def make_logs(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        for i in range(7):
            path=directory/('run-'+str(uuid.UUID(int=i))+'.log')
            path.write_text('isolated test log')
            os.utime(path,(1000+i,1000+i))

    def test_nonpreview_and_full_app_data_are_never_pruned(self):
        with tempfile.TemporaryDirectory() as temporary:
            home=Path(temporary)
            for folder in (home/'unrelated',home/'Library/Application Support/Offline Voice Studio',
                           home/'Library/Application Support/Offline Voice Studio Open Source'):
                self.make_logs(folder/'Logs')
                with mock.patch.object(Path,'home',return_value=home):
                    self.assertEqual(preview_app.prune_preview_data(folder,5),[])
                self.assertEqual(len(list((folder/'Logs').iterdir())),7)

    def test_linked_logs_and_linked_files_are_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            home=Path(temporary);preview=home/preview_app.PREVIEW_DATA
            external=home/'external';self.make_logs(external)
            preview.mkdir(parents=True);(preview/'Logs').symlink_to(external,target_is_directory=True)
            linked=preview/('library-request-'+str(uuid.uuid4())+'.json');linked.symlink_to(next(external.iterdir()))
            with mock.patch.object(Path,'home',return_value=home):
                self.assertEqual(preview_app.prune_preview_data(preview,5),[])
            self.assertEqual(len(list(external.iterdir())),7);self.assertTrue(linked.is_symlink())

    def test_linked_preview_root_is_not_followed(self):
        with tempfile.TemporaryDirectory() as temporary:
            home=Path(temporary);preview=home/preview_app.PREVIEW_DATA
            external=home/'external';self.make_logs(external/'Logs')
            preview.parent.mkdir(parents=True);preview.symlink_to(external,target_is_directory=True)
            with mock.patch.object(Path,'home',return_value=home):
                self.assertEqual(preview_app.prune_preview_data(preview,5),[])
            self.assertEqual(len(list((external/'Logs').iterdir())),7)

    def test_manual_files_and_invalid_retention(self):
        with tempfile.TemporaryDirectory() as temporary:
            home=Path(temporary);preview=home/preview_app.PREVIEW_DATA
            self.make_logs(preview/'Logs')
            manual=preview/'Logs/run-manual.log';manual.write_text('keep')
            with mock.patch.object(Path,'home',return_value=home):
                self.assertEqual(len(preview_app.prune_preview_data(preview,5)),2)
                for keep in (0,-1,True):
                    with self.assertRaises(ValueError):preview_app.prune_preview_data(preview,keep)
            self.assertEqual(manual.read_text(),'keep')

    def test_rebuild_refuses_running_app_before_any_removal(self):
        with tempfile.TemporaryDirectory() as temporary:
            build=Path(temporary);app=build/'OfflineVoiceStudioPreview.app'
            app.mkdir();marker=app/'do-not-delete';marker.write_text('running fixture')
            result='123 '+str(app/'Contents/MacOS/OfflineVoiceStudio')+'\n'
            with mock.patch.object(preview_app.subprocess,'run',return_value=mock.Mock(stdout=result)):
                with self.assertRaisesRegex(SystemExit,'quit it normally'):preview_app.clean_preview_outputs(build)
            self.assertEqual(marker.read_text(),'running fixture')

    def test_failed_process_probe_preserves_outputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            build=Path(temporary);marker=build/'PreviewVoiceStudio.swift';marker.write_text('keep')
            with mock.patch.object(preview_app.subprocess,'run',side_effect=OSError('probe unavailable')):
                with self.assertRaisesRegex(SystemExit,'no build files'):preview_app.clean_preview_outputs(build)
            self.assertEqual(marker.read_text(),'keep')

    def test_build_lock_rejects_second_builder(self):
        with tempfile.TemporaryDirectory() as temporary:
            build=Path(temporary)
            first=preview_app.acquire_build_lock(build)
            try:
                with self.assertRaisesRegex(SystemExit,'Another preview build'):preview_app.acquire_build_lock(build)
            finally:first.close()
            preview_app.acquire_build_lock(build).close()


if __name__ == '__main__':
    unittest.main()
