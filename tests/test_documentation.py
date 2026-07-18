from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DocumentationContractTests(unittest.TestCase):
    def test_phase12_governance_documents_exist(self):
        for relative in (
            "ARCHITECTURE.md",
            "CLOUD_OPERATIONS.md",
            "CHANGELOG.md",
            "LANGUAGE_POLICY.md",
            "LICENSE",
        ):
            with self.subTest(relative=relative):
                self.assertTrue((ROOT / relative).is_file())

    def test_private_license_and_language_policy_are_explicit(self):
        license_text = " ".join(
            (ROOT / "LICENSE").read_text(encoding="utf-8").split()
        )
        policy = (ROOT / "LANGUAGE_POLICY.md").read_text(encoding="utf-8")
        self.assertIn("All rights reserved", license_text)
        self.assertIn("No license is granted", license_text)
        self.assertIn("العربية", policy)
        self.assertIn("الإنجليزية", policy)

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

    def test_pages_workflow_enforces_python_and_browser_quality_gates(self):
        workflow = (ROOT / ".github/workflows/pages.yml").read_text(
            encoding="utf-8"
        )
        for command in (
            "python -m ruff check .",
            "python -m mypy",
            "node --check assets/app.js",
            "node --test tests/test_app.cjs",
        ):
            with self.subTest(command=command):
                self.assertIn(command, workflow)


if __name__ == "__main__":
    unittest.main()
