from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from check_data_contract import (  # noqa: E402
    AWARDED_INDEX_DESCRIPTOR_MAX_BYTES,
    AWARDED_INDEX_PART_MAX_BYTES,
    assert_awarded_index_asset_size,
    validate_awarded_index_descriptor,
    validate_awarded_index_part,
)
from export_warehouse import (  # noqa: E402
    AWARDED_INDEX_PART_ALGORITHM,
    AWARDED_INDEX_PART_COUNT,
    AWARDED_INDEX_PART_FORMAT_VERSION,
    SCHEMA_VERSION,
    awarded_index_part_config,
    index_part_for_ref,
    shard_for_ref,
    write_awarded_index,
)


def index_row(ref: str) -> dict:
    return {
        "ref": ref,
        "name": f"Tender {ref}",
        "_detailShard": f"{shard_for_ref(ref):02d}",
    }


class AwardedIndexPartitionTests(unittest.TestCase):
    def test_descriptor_and_part_growth_caps_are_fail_closed(self):
        assert_awarded_index_asset_size(
            "awarded_index.json",
            AWARDED_INDEX_DESCRIPTOR_MAX_BYTES - 1,
        )
        assert_awarded_index_asset_size(
            "awarded_index_parts/00.json",
            AWARDED_INDEX_PART_MAX_BYTES - 1,
        )
        with self.assertRaisesRegex(AssertionError, "descriptor exceeds 1 MiB"):
            assert_awarded_index_asset_size(
                "awarded_index.json",
                AWARDED_INDEX_DESCRIPTOR_MAX_BYTES,
            )
        with self.assertRaisesRegex(AssertionError, "part exceeds 5 MiB"):
            assert_awarded_index_asset_size(
                "awarded_index_parts/00.json",
                AWARDED_INDEX_PART_MAX_BYTES,
            )

    def test_writer_is_deterministic_and_removes_stale_parts(self):
        records = [index_row(f"REF-{number:03d}") for number in range(80, -1, -1)]
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            stale = out / "awarded_index_parts/99.json"
            stale.parent.mkdir(parents=True)
            stale.write_text("{}", encoding="utf-8")

            first_assets: dict[str, dict] = {}
            first = write_awarded_index(
                out,
                first_assets,
                records,
                partial=True,
                completeness_basis=["fixture"],
            )
            first_hashes = {
                name: descriptor["sha256"]
                for name, descriptor in first_assets.items()
            }
            self.assertFalse(stale.exists())
            self.assertNotIn("records", first)
            self.assertEqual(first["count"], len(records))
            self.assertEqual(
                first["meta"]["indexParts"], awarded_index_part_config()
            )
            self.assertEqual(len(first["parts"]), AWARDED_INDEX_PART_COUNT)
            self.assertNotIn("records", first_assets["awarded_index.json"])

            seen: set[str] = set()
            for descriptor in first["parts"]:
                payload = json.loads((out / descriptor["file"]).read_text())
                refs = [str(row["ref"]) for row in payload["records"]]
                self.assertEqual(refs, sorted(refs))
                self.assertTrue(
                    all(
                        index_part_for_ref(ref) == int(descriptor["part"])
                        for ref in refs
                    )
                )
                self.assertFalse(seen.intersection(refs))
                seen.update(refs)
            self.assertEqual(seen, {str(row["ref"]) for row in records})

            second_assets: dict[str, dict] = {}
            second = write_awarded_index(
                out,
                second_assets,
                list(reversed(records)),
                partial=True,
                completeness_basis=["fixture"],
            )
            second_hashes = {
                name: descriptor["sha256"]
                for name, descriptor in second_assets.items()
            }
            self.assertEqual(first_hashes, second_hashes)
            self.assertEqual(first, second)

    def test_descriptor_is_exactly_versioned_and_complete(self):
        config = awarded_index_part_config()
        self.assertEqual(
            config,
            {
                "formatVersion": AWARDED_INDEX_PART_FORMAT_VERSION,
                "count": AWARDED_INDEX_PART_COUNT,
                "pathTemplate": "awarded_index_parts/{part}.json",
                "algorithm": AWARDED_INDEX_PART_ALGORITHM,
            },
        )
        parts = [
            {
                "part": f"{part:02d}",
                "file": f"awarded_index_parts/{part:02d}.json",
                "count": 0,
                "bytes": 100,
                "sha256": "a" * 64,
            }
            for part in range(AWARDED_INDEX_PART_COUNT)
        ]
        descriptor = {
            "meta": {
                "schemaVersion": SCHEMA_VERSION,
                "dataset": "awarded",
                "detailShards": 64,
                "indexParts": config,
            },
            "count": 0,
            "parts": parts,
        }
        self.assertEqual(
            set(validate_awarded_index_descriptor(descriptor)),
            {part["file"] for part in parts},
        )
        descriptor["parts"] = parts[:-1]
        with self.assertRaisesRegex(AssertionError, "part count mismatch"):
            validate_awarded_index_descriptor(descriptor)

    def test_part_validator_rejects_wrong_bucket_and_unsorted_rows(self):
        ref = "MISPLACED"
        correct = index_part_for_ref(ref)
        wrong = (correct + 1) % AWARDED_INDEX_PART_COUNT
        part = f"{wrong:02d}"
        payload = {
            "meta": {
                "schemaVersion": SCHEMA_VERSION,
                "dataset": "awarded_index_part",
                "part": part,
                "partCount": AWARDED_INDEX_PART_COUNT,
                "formatVersion": AWARDED_INDEX_PART_FORMAT_VERSION,
                "algorithm": AWARDED_INDEX_PART_ALGORITHM,
            },
            "count": 1,
            "records": [index_row(ref)],
        }
        with self.assertRaisesRegex(AssertionError, "wrong part"):
            validate_awarded_index_part(
                f"awarded_index_parts/{part}.json",
                payload,
                as_of="2026-07-18T12:00:00+00:00",
            )

        same_bucket = [
            candidate
            for candidate in (f"SORT-{number:04d}" for number in range(10_000))
            if index_part_for_ref(candidate) == correct
        ][:2]
        self.assertEqual(len(same_bucket), 2)
        correct_part = f"{correct:02d}"
        payload["meta"]["part"] = correct_part
        payload["records"] = [index_row(ref) for ref in sorted(same_bucket, reverse=True)]
        payload["count"] = 2
        with self.assertRaisesRegex(AssertionError, "not sorted"):
            validate_awarded_index_part(
                f"awarded_index_parts/{correct_part}.json",
                payload,
                as_of="2026-07-18T12:00:00+00:00",
            )


if __name__ == "__main__":
    unittest.main()
