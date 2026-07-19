from __future__ import annotations

import hashlib
import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import sys
import tempfile
import threading
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from check_data_contract import check_remote  # noqa: E402
from export_warehouse import (  # noqa: E402
    AWARDED_INDEX_PART_ALGORITHM,
    AWARDED_INDEX_PART_COUNT,
    AWARDED_INDEX_PART_FORMAT_VERSION,
    SCHEMA_VERSION,
    SHARD_COUNT,
    awarded_index_part_config,
    index_part_for_ref,
    shard_for_ref,
)


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002
        del format, args


class RemoteContractTests(unittest.TestCase):
    def test_remote_checker_verifies_every_asset_and_detects_split_brain(self):
        with tempfile.TemporaryDirectory() as temp:
            site = Path(temp)
            data = site / "data"
            details = data / "awarded_details"
            details.mkdir(parents=True)
            ref = "REMOTE-1"
            target_shard = shard_for_ref(ref)
            assets = {}

            def write_asset(name: str, payload: dict):
                raw = json.dumps(
                    payload, ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8")
                path = data / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
                descriptor = {
                    "bytes": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
                if isinstance(payload.get("records"), list):
                    descriptor["records"] = len(payload["records"])
                assets[name] = descriptor
                return descriptor

            parts = []
            target_part = index_part_for_ref(ref)
            for part in range(AWARDED_INDEX_PART_COUNT):
                part_id = f"{part:02d}"
                name = f"awarded_index_parts/{part_id}.json"
                rows = (
                    [{"ref": ref, "_detailShard": f"{target_shard:02d}"}]
                    if part == target_part
                    else []
                )
                descriptor = write_asset(
                    name,
                    {
                        "meta": {
                            "schemaVersion": SCHEMA_VERSION,
                            "dataset": "awarded_index_part",
                            "part": part_id,
                            "partCount": AWARDED_INDEX_PART_COUNT,
                            "formatVersion": AWARDED_INDEX_PART_FORMAT_VERSION,
                            "algorithm": AWARDED_INDEX_PART_ALGORITHM,
                        },
                        "count": len(rows),
                        "records": rows,
                    },
                )
                parts.append(
                    {
                        "part": part_id,
                        "file": name,
                        "count": len(rows),
                        "bytes": descriptor["bytes"],
                        "sha256": descriptor["sha256"],
                    }
                )
            write_asset(
                "awarded_index.json",
                {
                    "meta": {
                        "schemaVersion": SCHEMA_VERSION,
                        "dataset": "awarded",
                        "detailShards": SHARD_COUNT,
                        "indexParts": awarded_index_part_config(),
                    },
                    "count": 1,
                    "parts": parts,
                },
            )
            for shard in range(SHARD_COUNT):
                rows = (
                    [{"ref": ref, "_detailShard": f"{shard:02d}"}]
                    if shard == target_shard
                    else []
                )
                write_asset(
                    f"awarded_details/{shard:02d}.json",
                    {
                        "meta": {"schemaVersion": SCHEMA_VERSION, "shard": f"{shard:02d}"},
                        "count": len(rows),
                        "records": rows,
                    },
                )
            write_asset(
                "fetch_status.json",
                {
                    "active_scan": {
                        "available": False,
                        "reason": "official_database_metadata_absent",
                    },
                    "still_missing": {
                        "active_refresh_sweep": {"complete": False}
                    },
                },
            )

            manifest = {
                "schema": "kashaf.static-warehouse",
                "schema_version": SCHEMA_VERSION,
                "snapshot_id": "remote-test",
                "as_of": "2026-07-18T12:00:00+00:00",
                "datasets": [
                    {
                        "id": "awarded",
                        "file": "awarded_index.json",
                        "count": 1,
                        "indexParts": awarded_index_part_config(),
                    }
                ],
                "assets": assets,
                "still_missing": {
                    "active_refresh_sweep": {"complete": False}
                },
            }
            (data / "manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )

            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                lambda *args, **kwargs: QuietHandler(
                    *args, directory=str(site), **kwargs
                ),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{server.server_port}"
                summary = check_remote(base_url, "remote-test", wait_seconds=0)
                self.assertEqual(
                    summary["assets"],
                    SHARD_COUNT + AWARDED_INDEX_PART_COUNT + 2,
                )
                self.assertEqual(summary["awarded"], 1)

                index_part = data / f"awarded_index_parts/{target_part:02d}.json"
                original_part = index_part.read_bytes()
                index_part.write_bytes(original_part + b" ")
                with self.assertRaisesRegex(
                    AssertionError, "remote snapshot did not converge"
                ):
                    check_remote(base_url, "remote-test", wait_seconds=0)
                index_part.write_bytes(original_part)

                stale = details / f"{target_shard:02d}.json"
                stale.write_bytes(stale.read_bytes() + b" ")
                with self.assertRaisesRegex(
                    AssertionError, "remote snapshot did not converge"
                ):
                    check_remote(base_url, "remote-test", wait_seconds=0)
                stale.write_bytes(stale.read_bytes()[:-1])

                manifest["still_missing"] = {}
                (data / "manifest.json").write_text(
                    json.dumps(manifest), encoding="utf-8"
                )
                with self.assertRaisesRegex(
                    AssertionError, "remote snapshot did not converge"
                ):
                    check_remote(base_url, "remote-test", wait_seconds=0)
                manifest["still_missing"] = {
                    "active_refresh_sweep": {"complete": False}
                }

                write_asset(
                    "fetch_status.json",
                    {
                        "active_scan": {
                            "denominator": 1,
                            "targets_scanned_unique": 2,
                            "targets_resolved_unique": 2,
                            "targets_absent_after_full_pass": 0,
                            "targets_remaining": 0,
                            "scanned_percent": 200.0,
                            "coverage_percent": 200.0,
                            "absence_confirmation_passes": 2,
                            "complete": True,
                        }
                    },
                )
                (data / "manifest.json").write_text(
                    json.dumps(manifest), encoding="utf-8"
                )
                with self.assertRaisesRegex(
                    AssertionError, "remote snapshot did not converge"
                ):
                    check_remote(base_url, "remote-test", wait_seconds=0)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
