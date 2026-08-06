from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.stage_publication_data import (
    stage_publication_data,
    validate_publication_tree,
)


class StagePublicationDataTests(unittest.TestCase):
    def test_replaces_source_data_with_publication_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            publication = root / "publication"
            data = publication / "data"
            data.mkdir(parents=True)
            (data / "manifest.json").write_text('{"snapshot_id":"run_1_1"}')
            (data / "open.json").write_text('{"records":[]}')

            destination = root / "source" / "data"
            destination.mkdir(parents=True)
            (destination / "stale.json").write_text("{}")

            stage_publication_data(publication, destination)

            self.assertFalse((destination / "stale.json").exists())
            self.assertEqual(
                (destination / "manifest.json").read_text(),
                '{"snapshot_id":"run_1_1"}',
            )
            self.assertTrue((destination / "open.json").is_file())

    def test_rejects_symlinks_before_replacing_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            publication = root / "publication"
            data = publication / "data"
            data.mkdir(parents=True)
            (data / "manifest.json").write_text("{}")
            (data / "unsafe.json").symlink_to(root / "outside.json")

            destination = root / "source" / "data"
            destination.mkdir(parents=True)
            marker = destination / "current.json"
            marker.write_text("{}")

            with self.assertRaisesRegex(ValueError, "unsafe file"):
                stage_publication_data(publication, destination)

            self.assertTrue(marker.is_file())

    def test_requires_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary) / "data"
            data.mkdir()
            with self.assertRaisesRegex(ValueError, "manifest.json"):
                validate_publication_tree(data)


if __name__ == "__main__":
    unittest.main()
