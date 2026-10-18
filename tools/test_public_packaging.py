"""Source-release checks. Fixtures are ephemeral silence, never voice recordings."""
import hashlib
import json
import tempfile
import unittest
import wave
from pathlib import Path

from add_local_voice import add_voice
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


if __name__ == '__main__':
    unittest.main()
