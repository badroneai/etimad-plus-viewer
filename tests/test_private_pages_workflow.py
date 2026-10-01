from __future__ import annotations

import functools
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/pages.yml"
SOURCE_SHA = "a" * 40
PUBLICATION_SHA = "b" * 40


def shell_step(name: str) -> str:
    step = WORKFLOW.read_text(encoding="utf-8").split(f"      - name: {name}\n", 1)[1]
    step = step.split("\n      - name:", 1)[0]
    lines = step.split("        run: |\n", 1)[1].splitlines()
    return "\n".join(line[10:] for line in lines if line.startswith("          ")) + "\n"


FAKE_GH = r"""
import json
import os
import sys
from pathlib import Path

assert os.environ.get("GH_TOKEN") == "test-only-token"
args = sys.argv[1:]
with open(os.environ["API_LOG"], "a") as log:
    log.write(json.dumps(args) + "\n")
if os.environ.get("API_FAILURE") == "all":
    sys.exit(1)
endpoint = next(arg for arg in args if arg.startswith("repos/"))
if "/git/ref/heads/main" in endpoint:
    print("a" * 40)
elif "/git/matching-refs/heads/publication/kashaf-data" in endpoint:
    if os.environ.get("API_FAILURE") == "publication":
        sys.exit(1)
    assert args[-1] == 'map(select(.ref == "refs/heads/publication/kashaf-data")) | first | .object.sha // empty'
    print(os.environ.get("PUBLICATION_TIP", "b" * 40))
elif "/contents/scripts/select_pages_publication.py?ref=" in endpoint:
    assert endpoint.endswith("a" * 40)
    assert "Accept: application/vnd.github.raw+json" in args
    print(Path(os.environ["SELECTOR_SOURCE"]).read_text(), end="")
elif "/contents/data/manifest.json?ref=" in endpoint:
    assert endpoint.endswith("b" * 40)
    assert "Accept: application/vnd.github.raw+json" in args
    print('{"snapshot_id":"private-publication"}', end="")
else:
    raise AssertionError(endpoint)
"""


class PrivatePagesWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="private pages ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        executable = self.root / "gh"
        executable.write_text(f"#!{sys.executable}\n{FAKE_GH}", encoding="utf-8")
        executable.chmod(0o755)
        self.output = self.root / "output"
        self.output.touch()
        self.log = self.root / "api.log"
        self.env = {
            **os.environ,
            "PATH": f"{self.root}{os.pathsep}{os.environ['PATH']}",
            "GH_TOKEN": "test-only-token",
            "REPOSITORY": "example/private-viewer",
            "API_LOG": str(self.log),
            "SELECTOR_SOURCE": str(ROOT / "scripts/select_pages_publication.py"),
            "GITHUB_OUTPUT": str(self.output),
            "GITHUB_STEP_SUMMARY": str(self.root / "summary"),
            "RUNNER_TEMP": str(self.root),
            "EVENT_NAME": "schedule",
            "USE_MAIN_DATA": "false",
            "PUSH_SHA": "c" * 40,
            "SIGNAL_BRANCH": "publication/kashaf-data",
            "SIGNAL_CONCLUSION": "success",
            "SIGNAL_REPOSITORY": "example/private-viewer",
            "SIGNAL_SHA": PUBLICATION_SHA,
            "SOURCE_REF": SOURCE_SHA,
            "PUBLICATION_REF": PUBLICATION_SHA,
            "GITHUB_RUN_ID": "123",
            "GITHUB_RUN_ATTEMPT": "1",
        }

    def run_selection(self, **env: str) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
        self.output.write_text("")
        result = subprocess.run(
            ["bash", "-c", shell_step("Select immutable revisions")],
            env={**self.env, **env},
            capture_output=True,
            text=True,
            check=False,
        )
        values = dict(line.split("=", 1) for line in self.output.read_text().splitlines())
        return result, values

    def test_reads_use_only_existing_read_only_job_token(self) -> None:
        workflow = WORKFLOW.read_text()
        selection_job = workflow.split("  select-revisions:", 1)[1].split(
            "\n  verify-and-package:", 1
        )[0]
        self.assertIn("      contents: read", selection_job)
        self.assertEqual(selection_job.count("GH_TOKEN: $" + "{{ github.token }}"), 2)
        self.assertNotIn("git ls-remote", selection_job)
        self.assertNotIn("raw.githubusercontent.com", selection_job)
        self.assertNotIn("contents: write", selection_job)

    def test_schedule_resolves_private_refs_and_waits_for_comparison(self) -> None:
        result, values = self.run_selection()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(values["source_ref"], SOURCE_SHA)
        self.assertEqual(values["publication_ref"], PUBLICATION_SHA)
        self.assertEqual(values["use_publication"], "true")
        self.assertEqual(values["should_deploy"], "false")

    def test_missing_branch_retains_main_compatibility(self) -> None:
        result, values = self.run_selection(PUBLICATION_TIP="", EVENT_NAME="push")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(values["source_ref"], "c" * 40)
        self.assertEqual(values["use_publication"], "false")
        self.assertEqual(values["should_deploy"], "true")

    def test_api_failures_do_not_look_like_absent_branch(self) -> None:
        for failure in ("all", "publication"):
            with self.subTest(failure=failure):
                result, values = self.run_selection(API_FAILURE=failure)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(values, {})

    def test_signal_only_accepts_matching_successful_publication(self) -> None:
        for override in ({}, {"SIGNAL_SHA": "d" * 40}, {"SIGNAL_CONCLUSION": "failure"},
                         {"SIGNAL_REPOSITORY": "untrusted/fork"},
                         {"SIGNAL_BRANCH": "another-branch"}):
            with self.subTest(override=override):
                result, values = self.run_selection(EVENT_NAME="workflow_run", **override)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(values["should_deploy"], "false" if override else "true")

    def test_explicit_emergency_rollback_uses_main(self) -> None:
        result, values = self.run_selection(EVENT_NAME="workflow_dispatch", USE_MAIN_DATA="true")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(values["use_publication"], "false")
        self.assertEqual(values["publication_ref"], "")
        self.assertEqual(values["should_deploy"], "true")

    def test_private_manifest_is_local_and_live_request_has_no_token(self) -> None:
        requests: list[dict[str, str]] = []
        manifest = b'{"snapshot_id":"private-publication"}'

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                requests.append(dict(self.headers))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(manifest)

            def log_message(self, format: str, *args: object) -> None:
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.addCleanup(server.server_close)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(functools.partial(thread.join, timeout=5))
        self.addCleanup(server.shutdown)
        script = shell_step("Compare publication tip with live Pages").replace(
            "https://badroneai.github.io/etimad-plus-viewer",
            f"http://127.0.0.1:{server.server_port}",
        )
        result = subprocess.run(
            ["bash", "-c", script], env=self.env, capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("reason=publication_already_live", self.output.read_text())
        self.assertEqual((self.root / "publication-manifest.json").read_bytes(), manifest)
        self.assertEqual(len(requests), 1)
        self.assertNotIn("Authorization", requests[0])
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
