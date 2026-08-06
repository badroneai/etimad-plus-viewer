from __future__ import annotations

import re
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
            "python -m pip install -r requirements-dev.txt",
            "python -m ruff check .",
            "python -m mypy",
            "node --check assets/app.js",
            "node --test tests/test_app.cjs",
            "npm ci",
            "npm run test:e2e",
        ):
            with self.subTest(command=command):
                self.assertIn(command, workflow)

    def test_publication_branch_cannot_replace_privileged_pages_workflow(self):
        pages = (ROOT / ".github/workflows/pages.yml").read_text(encoding="utf-8")
        signal = (ROOT / ".github/workflows/publication-signal.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn('workflow_run:\n    workflows: ["Kashaf data publication signal"]', pages)
        self.assertIn('branches: ["publication/kashaf-data"]', pages)
        self.assertIn("permissions: {}", pages)
        self.assertIn("name: Select trusted source and publication", pages)
        self.assertIn('SIGNAL_REPOSITORY: ${{ github.event.workflow_run.head_repository.full_name }}', pages)
        self.assertIn('if [[ -z "${tip}" || "${tip}" != "${SIGNAL_SHA}" ]]', pages)
        self.assertIn("python scripts/stage_publication_data.py", pages)
        self.assertIn("permissions:\n      pages: write\n      id-token: write", pages)

        self.assertIn('branches: ["publication/kashaf-data"]', signal)
        self.assertIn("permissions:\n  contents: read", signal)
        self.assertNotIn("pages: write", signal)
        self.assertNotIn("id-token: write", signal)
        self.assertNotIn("secrets.", signal)

    def test_pr_ci_workflow_is_pinned_and_non_deploying(self):
        ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        pages = (ROOT / ".github/workflows/pages.yml").read_text(encoding="utf-8")

        for required in (
            "on:\n  pull_request:\n    branches: [main]\n"
            "  workflow_dispatch:\n\npermissions:",
            "permissions:\n  contents: read\n\nconcurrency:",
            "concurrency:\n"
            "  group: pr-ci-${{ github.workflow }}-${{ github.ref }}\n"
            "  cancel-in-progress: true\n\njobs:",
            "name: Kashaf quality gates",
            "runs-on: ubuntu-24.04",
            'python-version: "3.12"',
            "python -m pip install -r requirements-dev.txt",
            "python -m ruff check .",
            "python -m mypy",
            "python -m unittest discover -s tests -v",
            "node --check assets/app.js",
            "node --test tests/test_app.cjs",
            "npm ci",
            "npx playwright install --with-deps chromium",
            "npm run test:e2e",
            "python scripts/check_data_contract.py --root .",
        ):
            with self.subTest(required=required):
                self.assertIn(required, ci)

        for forbidden in (
            "actions/configure-pages",
            "actions/upload-pages-artifact",
            "actions/deploy-pages",
            "pages: write",
            "id-token: write",
            "\n  push:",
            "environment:",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, ci)

        action_pattern = re.compile(
            r"uses: (actions/(?:checkout|setup-python|setup-node))@([0-9a-f]{40})"
        )
        ci_actions = dict(action_pattern.findall(ci))
        pages_actions = dict(action_pattern.findall(pages))
        self.assertEqual(
            ci_actions,
            {
                "actions/checkout": pages_actions["actions/checkout"],
                "actions/setup-python": pages_actions["actions/setup-python"],
                "actions/setup-node": pages_actions["actions/setup-node"],
            },
        )

    def test_quality_gate_dependencies_and_readme_commands_are_governed(self):
        requirements = (ROOT / "requirements-dev.txt").read_text(
            encoding="utf-8"
        ).splitlines()
        self.assertEqual(requirements, ["ruff==0.15.22", "mypy==2.3.0"])

        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for required in (
            "python3 -m pip install -r requirements-dev.txt",
            "python3 -m ruff check .",
            "python3 -m mypy",
            "python3 -m unittest discover -s tests -v",
            "node --check assets/app.js",
            "node --test tests/test_app.cjs",
            "npm ci",
            "npx playwright install chromium",
            "npm run test:e2e",
            "python3 scripts/check_data_contract.py --root .",
            ".github/workflows/ci.yml",
            ".github/workflows/pages.yml",
        ):
            with self.subTest(required=required):
                self.assertIn(required, readme)


if __name__ == "__main__":
    unittest.main()
