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
from export_warehouse import SCHEMA_VERSION, SHARD_COUNT, shard_for_ref  # noqa: E402


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
                assets[name] = {
                    "bytes": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "records": len(payload["records"]),
                }

            write_asset(
                "awarded_index.json",
                {
                    "meta": {"schemaVersion": SCHEMA_VERSION},
                    "count": 1,
                    "records": [
                        {"ref": ref, "_detailShard": f"{target_shard:02d}"}
                    ],
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
                    }
                ],
                "assets": assets,
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
                self.assertEqual(summary["assets"], SHARD_COUNT + 1)
                self.assertEqual(summary["awarded"], 1)

                stale = details / f"{target_shard:02d}.json"
                stale.write_bytes(stale.read_bytes() + b" ")
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
