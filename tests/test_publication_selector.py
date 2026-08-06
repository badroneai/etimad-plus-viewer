from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.select_pages_publication import (
    compare_manifests,
    manifest_identity,
    write_github_output,
)


def manifest(snapshot_id: str, *, marker: str = "same") -> bytes:
    return json.dumps(
        {"snapshot_id": snapshot_id, "marker": marker},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


class PublicationSelectorTests(unittest.TestCase):
    def test_exact_manifest_match_skips_expensive_deployment(self) -> None:
        payload = manifest("run_1_1")
        selection = compare_manifests(payload, payload)

        self.assertFalse(selection.should_deploy)
        self.assertEqual(selection.reason, "publication_already_live")

    def test_snapshot_divergence_requests_deployment(self) -> None:
        selection = compare_manifests(
            manifest("run_2_1"),
            manifest("run_1_1"),
        )

        self.assertTrue(selection.should_deploy)
        self.assertEqual(selection.reason, "snapshot_id_diverged")
        self.assertEqual(selection.publication.snapshot_id, "run_2_1")
        self.assertEqual(selection.live.snapshot_id, "run_1_1")

    def test_same_snapshot_with_different_bytes_requests_deployment(self) -> None:
        selection = compare_manifests(
            manifest("run_2_1", marker="publication"),
            manifest("run_2_1", marker="live"),
        )

        self.assertTrue(selection.should_deploy)
        self.assertEqual(selection.reason, "manifest_sha256_diverged")

    def test_missing_or_invalid_live_manifest_requests_deployment(self) -> None:
        missing = compare_manifests(manifest("run_2_1"), None)
        invalid = compare_manifests(manifest("run_2_1"), b"<html>failure</html>")

        self.assertEqual(missing.reason, "live_manifest_unavailable")
        self.assertEqual(invalid.reason, "live_manifest_invalid")
        self.assertTrue(missing.should_deploy)
        self.assertTrue(invalid.should_deploy)

    def test_invalid_publication_manifest_fails_closed(self) -> None:
        for payload in (b"[]", b"{}", b'{"snapshot_id":""}', b"not-json"):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    manifest_identity(payload, label="publication")

    def test_github_outputs_are_deterministic(self) -> None:
        selection = compare_manifests(
            manifest("run_2_1"),
            manifest("run_1_1"),
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "github-output"
            output.touch()
            write_github_output(output, selection)

            values = dict(
                line.split("=", 1)
                for line in output.read_text(encoding="utf-8").splitlines()
            )

        self.assertEqual(values["should_deploy"], "true")
        self.assertEqual(values["reason"], "snapshot_id_diverged")
        self.assertEqual(values["publication_snapshot_id"], "run_2_1")
        self.assertEqual(values["live_snapshot_id"], "run_1_1")
        self.assertEqual(len(values["publication_manifest_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
