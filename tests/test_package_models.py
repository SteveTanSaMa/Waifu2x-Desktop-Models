import hashlib
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "script"))
import package_models as models

class ModelRepairPackagingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="waifu2x-model-repair-tool-tests-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cache, self.dist = self.root / "models", self.root / "dist"
        self.payloads = {"ONNXModels/art/a.onnx": b"shared weights" * 50,
                         "ONNXModels/art/b.onnx": b"shared weights" * 50,
                         "Engine/models-cunet/a.param": b"parameter fixture"}
        self.files = []
        for name, data in self.payloads.items():
            path = self.cache / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            self.files.append(dict(path=name, bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                                   sourceURL="https://source.example.invalid/" + name))

    def test_identical_models_share_one_verified_single_entry_archive(self):
        result = models.package_repairs(self.cache, self.files, "models-v1", "models-v1", self.dist)
        self.assertEqual(result["schemaVersion"], 1)
        self.assertEqual(len(result["files"]), 3)
        self.assertEqual(len(list(self.dist.glob("*.tar.xz"))), 2)
        by_name = {item["path"]: item for item in result["files"]}
        self.assertEqual(by_name["ONNXModels/art/a.onnx"]["archive"], by_name["ONNXModels/art/b.onnx"]["archive"])
        for item in result["files"]:
            metadata = item["archive"]
            archive_path = self.dist / metadata["path"]
            self.assertEqual(models.sha256(archive_path), metadata["sha256"])
            self.assertEqual(archive_path.stat().st_size, metadata["bytes"])
            self.assertEqual(metadata["sourceURL"], "https://github.com/SteveTanSaMa/Waifu2x-Desktop-Models/releases/download/models-v1/" + metadata["path"])
            with tarfile.open(archive_path, "r:xz") as archive:
                self.assertEqual(archive.getnames(), ["model"])
                member = archive.getmember("model")
                self.assertTrue(member.isfile())
                self.assertEqual(member.mtime, 0)
                self.assertEqual(archive.extractfile(member).read(), self.payloads[item["path"]])

    def test_corrupt_or_symlink_sources_never_publish_a_manifest(self):
        path = self.cache / self.files[0]["path"]
        path.write_bytes(b"corrupt")
        with self.assertRaises(ValueError):
            models.package_repairs(self.cache, self.files, "models-v1", "models-v1", self.dist)
        self.assertFalse(self.dist.exists())
        path.unlink()
        path.symlink_to(self.cache / self.files[1]["path"])
        with self.assertRaises(ValueError):
            models.package_repairs(self.cache, self.files, "models-v1", "models-v1", self.dist)

    def test_repair_assets_are_reproducible(self):
        first = models.package_repairs(self.cache, self.files, "models-v1", "models-v1", self.dist)
        contents = {path.name: path.read_bytes() for path in self.dist.iterdir()}
        second = models.package_repairs(self.cache, self.files, "models-v1", "models-v1", self.dist)
        self.assertEqual(first, second)
        self.assertEqual(contents, {path.name: path.read_bytes() for path in self.dist.iterdir()})


if __name__ == "__main__":
    unittest.main()
