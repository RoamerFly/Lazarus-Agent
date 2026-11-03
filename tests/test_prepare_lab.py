from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

from tools.prepare_lab import _extract_without_overwrite


class PrepareLabSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _zip(self, name: str, contents: dict[str, str]) -> Path:
        archive = self.root / name
        with ZipFile(archive, "w") as bundle:
            for member, text in contents.items():
                bundle.writestr(member, text)
        return archive

    def test_rejects_path_traversal_without_publishing_destination(self) -> None:
        archive = self._zip("unsafe.zip", {"../outside.txt": "no"})
        destination = self.root / "component"
        with self.assertRaises(ValueError):
            _extract_without_overwrite(archive, destination)
        self.assertFalse(destination.exists())
        self.assertFalse((self.root / "outside.txt").exists())

    def test_existing_component_is_never_overwritten(self) -> None:
        archive = self._zip("safe.zip", {"package/file.txt": "new"})
        destination = self.root / "component"
        destination.mkdir()
        sentinel = destination / "sentinel.txt"
        sentinel.write_text("keep", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            _extract_without_overwrite(archive, destination)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_successful_extraction_publishes_complete_tree(self) -> None:
        archive = self._zip("safe.zip", {"package/file.txt": "ready"})
        destination = self.root / "component"
        _extract_without_overwrite(archive, destination)
        self.assertEqual((destination / "package" / "file.txt").read_text(encoding="utf-8"), "ready")


if __name__ == "__main__":
    unittest.main()
