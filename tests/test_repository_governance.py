from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


class RepositoryGovernanceTests(unittest.TestCase):
    def test_ownership_security_and_license_are_explicit(self) -> None:
        owners = (ROOT / ".github/CODEOWNERS").read_text(encoding="utf-8")
        security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
        license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")

        self.assertRegex(owners, r"(?m)^\*\s+@badroneai$")
        self.assertIn("private vulnerability reporting", security)
        self.assertIn("Do not open a public issue", security)
        self.assertIn("All rights reserved", license_text)
        self.assertIn("No license is granted", license_text)

    def test_dependabot_is_monthly_and_covers_all_dependency_sources(self) -> None:
        config = (ROOT / ".github/dependabot.yml").read_text(encoding="utf-8")

        self.assertEqual(config.count("interval: monthly"), 3)
        for ecosystem in ("npm", "pip", "github-actions"):
            with self.subTest(ecosystem=ecosystem):
                self.assertIn(f"package-ecosystem: {ecosystem}", config)
        self.assertIn("npm-minor-and-patch", config)
        self.assertIn("python-minor-and-patch", config)
        self.assertIn("actions-minor-and-patch", config)
        self.assertNotIn("interval: daily", config)

    def test_workflow_actions_are_pinned_and_pr_workflows_are_read_only(self) -> None:
        action_pattern = re.compile(r"(?m)^\s*uses:\s*([^@\s]+)@([^\s#]+)")
        workflows = sorted(WORKFLOWS.glob("*.yml"))
        self.assertTrue(workflows)

        for path in workflows:
            contents = path.read_text(encoding="utf-8")
            with self.subTest(workflow=path.name):
                self.assertNotIn("pull_request_target:", contents)
                for action, ref in action_pattern.findall(contents):
                    self.assertRegex(
                        ref,
                        r"^[0-9a-f]{40}$",
                        f"{action} in {path.name} must use a full commit SHA",
                    )
                if "pull_request:" in contents:
                    self.assertNotRegex(contents, r"(?m)^\s+\w[\w-]*:\s*write\s*$")

    def test_dependency_and_license_metadata_are_consistent(self) -> None:
        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))

        self.assertTrue(package["private"])
        self.assertEqual(package["license"], "UNLICENSED")
        self.assertEqual(lock["packages"][""]["license"], "UNLICENSED")
        for dependency, version in package["devDependencies"].items():
            with self.subTest(dependency=dependency):
                self.assertRegex(version, r"^\d+\.\d+\.\d+$")

        for line in (ROOT / "requirements-dev.txt").read_text(
            encoding="utf-8"
        ).splitlines():
            dependency = line.strip()
            if not dependency or dependency.startswith("#"):
                continue
            with self.subTest(dependency=dependency):
                self.assertRegex(
                    dependency,
                    r"^[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_,.-]+\])?==[^=\s]+$",
                )


if __name__ == "__main__":
    unittest.main()
