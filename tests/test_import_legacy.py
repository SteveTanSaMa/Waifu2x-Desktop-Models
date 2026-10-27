import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'script'))
import import_legacy
import package_models


class LegacyImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        cache = self.root / 'cache'
        cache.mkdir()
        data = b'test model bytes'
        (cache / 'model.onnx').write_bytes(data)
        self.files = [dict(path='model.onnx', bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), sourceURL='https://example.test/model.onnx')]
        package_models.package_repairs(cache, self.files, 'models-v1', 'models-v1', self.source)
        manifest_path = self.source / 'Waifu2x-models-v1-repair.json'
        manifest = json.loads(manifest_path.read_text())
        for item in manifest['files']:
            item['archive']['sourceURL'] = import_legacy.OLD_BASE + item['archive']['path']
        manifest_path.write_text(json.dumps(manifest))
        full = self.source / 'Waifu2x-models-v1.tar.xz'
        full.write_bytes(b'verified complete archive fixture')
        catalog = dict(archive=dict(path=full.name, bytes=full.stat().st_size, sha256=package_models.sha256(full)), files=self.files)
        (self.root / 'catalog').mkdir()
        (self.root / 'catalog/models-v1.json').write_text(json.dumps(catalog))
        self.write_sums()

    def write_sums(self):
        files = sorted(p for p in self.source.iterdir() if p.name != 'SHA256SUMS')
        (self.source / 'SHA256SUMS').write_text(''.join(f'{package_models.sha256(p)}  {p.name}\n' for p in files))

    def run_import(self):
        with patch.object(import_legacy, 'PROJECT', self.root), patch.object(sys, 'argv', ['import_legacy.py', '--source', str(self.source)]), contextlib.redirect_stdout(io.StringIO()):
            import_legacy.main()

    def test_verifies_and_rewrites_manifest(self):
        self.run_import()
        output = self.root / 'dist'
        manifest = json.loads((output / 'Waifu2x-models-v1-repair.json').read_text())
        item = manifest['files'][0]['archive']
        self.assertEqual(item['sourceURL'], import_legacy.NEW_BASE + item['path'])
        self.assertTrue((output / 'SHA256SUMS').is_file())

    def test_rejects_corrupt_archive_before_staging(self):
        repair = next(self.source.glob('Waifu2x-model-*.tar.xz'))
        repair.write_bytes(b'damaged')
        with self.assertRaisesRegex(ValueError, 'Checksum mismatch'):
            self.run_import()
        self.assertFalse((self.root / 'dist').exists())

    def test_rejects_manifest_that_changes_model_hash(self):
        path = self.source / 'Waifu2x-models-v1-repair.json'
        manifest = json.loads(path.read_text())
        manifest['files'][0]['sha256'] = 'a' * 64
        path.write_text(json.dumps(manifest))
        self.write_sums()
        with self.assertRaisesRegex(ValueError, 'Invalid model entry'):
            self.run_import()
        self.assertFalse((self.root / 'dist').exists())


if __name__ == '__main__':
    unittest.main()
