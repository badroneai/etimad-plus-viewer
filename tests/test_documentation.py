from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DocumentationContractTests(unittest.TestCase):
    def test_legacy_handover_documents_are_replaced(self):
        self.assertFalse((ROOT / "HANDOVER_FETCH.md").exists())
        self.assertFalse((ROOT / "CROSS_DEVICE_SYNC.md").exists())
        self.assertTrue((ROOT / "CLOUD_OPERATIONS.md").is_file())

    def test_cloud_contract_names_live_sources_of_truth(self):
        document = (ROOT / "CLOUD_OPERATIONS.md").read_text(encoding="utf-8")
        for required in (
            "data/manifest.json",
            "data/fetch_status.json",
            "etimad-periodic-state-v1",
            "scripts/check_data_contract.py",
            "snapshot_id",
        ):
            with self.subTest(required=required):
                self.assertIn(required, document)

    def test_markdown_has_no_personal_or_legacy_repository_paths(self):
        forbidden = (
            "ksa-coffee" + "-atlas",
            "/" + "Users" + "/",
            "C:" + "\\" + "Users",
            "bader" + "alsalman",
        )
        markdown_files = [
            path
            for path in ROOT.rglob("*.md")
            if ".git" not in path.parts
        ]
        self.assertTrue(markdown_files)
        for path in markdown_files:
            contents = path.read_text(encoding="utf-8")
            for marker in forbidden:
                with self.subTest(path=path.relative_to(ROOT), marker=marker):
                    self.assertNotIn(marker, contents)


if __name__ == "__main__":
    unittest.main()
