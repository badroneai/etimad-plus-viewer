from __future__ import annotations

import hashlib
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from export_warehouse import (  # noqa: E402
    SHARD_COUNT,
    add_money_projection,
    build,
    load_official_database,
    official_overlay,
    searchable_award,
    seed_record,
    shard_for_ref,
    to_halalas,
)


class ExportContractTests(unittest.TestCase):
    def test_shard_algorithm_is_sha256_first_byte(self):
        ref = "260639009354"
        expected = hashlib.sha256(ref.encode("utf-8")).digest()[0] % SHARD_COUNT
        self.assertEqual(shard_for_ref(ref), expected)

    def test_official_metadata_wins_without_erasing_phase0_award(self):
        phase0 = seed_record(
            {
                "ref": "R-1",
                "name": "old",
                "winAmount": 97500,
                "winners": [{"company": "winner", "award": 97500}],
            },
            source_id="etimad_plus_phase0",
            fetched_at="2026-07-18T10:00:00+00:00",
            layer="81_tenders_awarded_yes.json",
        )
        official = seed_record(
            {"ref": "R-1", "name": "new", "winners": [], "winAmount": None},
            source_id="etimad_official_periodic",
            fetched_at="2026-07-18T11:00:00+00:00",
            layer="tenders",
        )
        merged = official_overlay(phase0, official)
        self.assertEqual(merged["name"], "new")
        self.assertEqual(merged["winAmount"], 97500)
        self.assertEqual(merged["winners"][0]["company"], "winner")
        self.assertEqual(merged["_provenance"]["fieldSources"]["name"], "etimad_official_periodic")

    def test_complete_official_award_can_replace_phase0_award(self):
        phase0 = seed_record(
            {"ref": "R-2", "winners": [{"company": "old"}]},
            source_id="etimad_plus_phase0",
            fetched_at=None,
            layer="phase0",
        )
        official = seed_record(
            {
                "ref": "R-2",
                "awardState": "announced",
                "awardCompleteness": "complete",
                "winners": [{"company": "official"}],
            },
            source_id="etimad_official_periodic",
            fetched_at=None,
            layer="official",
        )
        merged = official_overlay(phase0, official)
        self.assertEqual(merged["winners"][0]["company"], "official")

    def test_search_index_excludes_heavy_bid_arrays(self):
        index = searchable_award(
            {
                "ref": "R-3",
                "name": "Tender",
                "agency": "Agency",
                "allBids": [{"company": "A"}] * 100,
                "winners": [{"company": "A"}],
            }
        )
        self.assertNotIn("allBids", index)
        self.assertNotIn("winners", index)
        self.assertEqual(index["_detailShard"], f"{shard_for_ref('R-3'):02d}")

    def test_money_projection_uses_decimal_halalas_not_float_equality(self):
        self.assertEqual(to_halalas("0.29"), 29)
        self.assertEqual(to_halalas("10.105"), 1011)
        record = {
            "ref": "MONEY-1",
            "winAmount": "0.30",
            "winners": [
                {"company": "A", "award": "0.10"},
                {"company": "B", "award": "0.20"},
            ],
            "allBids": [{"company": "A", "bid": "0.29", "award": "0.10"}],
        }
        add_money_projection(record)
        self.assertEqual(record["winAmountHalalas"], 30)
        self.assertEqual(record["winners"][0]["awardHalalas"], 10)
        self.assertEqual(record["allBids"][0]["bidHalalas"], 29)
        self.assertEqual(record["currency"], "SAR")
        self.assertEqual(record["moneyConsistency"]["status"], "match")
        self.assertEqual(record["moneyConsistency"]["deltaHalalas"], 0)

    def test_database_only_baseline_record_json_loader(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "official.sqlite3"
            connection = sqlite3.connect(database)
            connection.executescript(
                """
                CREATE TABLE baseline_tenders (
                    reference_number TEXT PRIMARY KEY,
                    seed_state TEXT NOT NULL,
                    source_layer TEXT NOT NULL,
                    imported_at TEXT NOT NULL,
                    record_json TEXT
                );
                INSERT INTO baseline_tenders VALUES
                  ('OPEN-1','open','phase0/open','2026-07-18T10:00:00+00:00','{"ref":"OPEN-1","name":"Open"}'),
                  ('AWARD-1','awarded','phase0/awarded','2026-07-18T10:00:00+00:00','{"ref":"AWARD-1","name":"Award","winAmount":1}');
                """
            )
            connection.commit()
            connection.close()
            baseline, official, times = load_official_database(database)
            self.assertEqual(set(baseline["open"]), {"OPEN-1"})
            self.assertEqual(set(baseline["awarded"]), {"AWARD-1"})
            self.assertFalse(official)
            self.assertEqual(times["phase0"], "2026-07-18T10:00:00+00:00")

    def test_snapshot_identity_does_not_rewrite_content_addressed_assets(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = root / "official.sqlite3"
            connection = sqlite3.connect(database)
            connection.executescript(
                """
                CREATE TABLE baseline_tenders (
                    reference_number TEXT PRIMARY KEY,
                    seed_state TEXT NOT NULL,
                    source_layer TEXT NOT NULL,
                    imported_at TEXT NOT NULL,
                    record_json TEXT
                );
                INSERT INTO baseline_tenders VALUES
                  ('OPEN-1','open','phase0/open','2026-07-18T10:00:00+00:00','{"ref":"OPEN-1","name":"Open","days":3}'),
                  ('AWARD-1','awarded','phase0/awarded','2026-07-18T10:00:00+00:00','{"ref":"AWARD-1","name":"Award","winAmount":"0.30","winners":[{"award":"0.30"}]}');
                """
            )
            connection.commit()
            connection.close()
            args = SimpleNamespace(
                out=root / "data",
                plus_warehouse=None,
                no_plus=True,
                official_db=database,
                official_layers=None,
                snapshot_id="run_1_1",
            )
            first = build(args)
            first_hashes = {name: item["sha256"] for name, item in first["assets"].items()}
            args.snapshot_id = "run_2_1"
            second = build(args)
            second_hashes = {name: item["sha256"] for name, item in second["assets"].items()}
            self.assertEqual(first_hashes, second_hashes)
            self.assertEqual(first["snapshot_id"], "run_1_1")
            self.assertEqual(second["snapshot_id"], "run_2_1")


if __name__ == "__main__":
    unittest.main()
