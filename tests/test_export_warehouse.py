from __future__ import annotations

import hashlib
import json
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
    award_is_announced,
    build,
    classify_tender,
    load_official_database,
    official_overlay,
    official_projection_record,
    resolve_awarded_truth,
    searchable_award,
    seed_record,
    shard_for_ref,
    to_halalas,
)
from check_data_contract import assert_awarded_lifecycle_contract, check  # noqa: E402


def write_phase0_lock(path: Path, *, has_more: bool = True) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "assets": [
                    {
                        "state": "awarded",
                        "records": 1,
                        "source_fetched_at": "2026-07-18T10:00:00+00:00",
                        "source_has_more": has_more,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def build_args(root: Path, database: Path, lock: Path, **overrides):
    values = {
        "out": root / "data",
        "plus_warehouse": None,
        "no_plus": True,
        "phase0_lock": lock,
        "official_db": database,
        "official_layers": None,
        "snapshot_id": "test_snapshot",
        "as_of": "2026-07-18T12:00:00+00:00",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class ExportContractTests(unittest.TestCase):
    def test_future_null_zero_bid_record_cannot_be_awarded(self):
        as_of = __import__("datetime").datetime.fromisoformat(
            "2026-07-18T12:00:00+00:00"
        )
        record = seed_record(
            {
                "ref": "260639008661",
                "statusId": 4,
                "deadline": "2026-08-01T09:59:00",
                "winAmount": None,
                "bids": 0,
                "awardState": "pending",
                "awardCompleteness": True,
            },
            source_id="etimad_official_periodic",
            fetched_at="2026-07-18T11:00:00+00:00",
            layer="tenders",
        )
        self.assertFalse(award_is_announced(record, as_of=as_of))
        self.assertEqual(classify_tender(record, as_of=as_of)[0], "open")

    def test_contract_rejects_synthetic_future_null_award(self):
        with self.assertRaisesRegex(
            AssertionError,
            "awarded row has null amount and future deadline: REGRESSION-1",
        ):
            assert_awarded_lifecycle_contract(
                [
                    {
                        "ref": "REGRESSION-1",
                        "winAmount": None,
                        "deadline": "2026-08-01T09:59:00",
                    }
                ],
                as_of="2026-07-18T12:00:00+00:00",
            )

    def test_awarded_truth_sources_fail_closed_when_lock_and_db_disagree(self):
        with self.assertRaisesRegex(RuntimeError, "sources disagree"):
            resolve_awarded_truth(
                plus_layers={},
                database_metadata={
                    "baseline_awarded": {"source_has_more": False}
                },
                phase0_lock={
                    "assets": [
                        {"state": "awarded", "source_has_more": True}
                    ]
                },
            )

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

    def test_source_fetched_at_and_real_official_observation_drive_source_times(self):
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
                    record_json TEXT,
                    source_fetched_at TEXT
                );
                INSERT INTO baseline_tenders VALUES
                  ('OPEN-1','open','phase0/open','2026-07-18T18:00:00+00:00',
                   '{"ref":"OPEN-1","name":"Open"}','2026-07-18T05:42:06+00:00');
                CREATE TABLE tenders (
                    reference_number TEXT PRIMARY KEY,
                    official_json TEXT,
                    seed_json TEXT,
                    source_kind TEXT,
                    last_seen_at TEXT,
                    baseline_linked INTEGER
                );
                INSERT INTO tenders VALUES
                  ('OPEN-1',NULL,'{"ref":"OPEN-1"}','phase0_baseline','2026-07-18T18:00:00+00:00',1),
                  ('OFFICIAL-1','{"referenceNumber":"OFFICIAL-1","tenderName":"Observed"}',NULL,
                   'official_list','2026-07-18T17:30:00+00:00',0);
                """
            )
            connection.commit()
            connection.close()

            baseline, _, times = load_official_database(database)
            source = baseline["open"]["OPEN-1"]["_provenance"]["sources"][0]
            self.assertEqual(source["fetchedAt"], "2026-07-18T05:42:06+00:00")
            self.assertEqual(times["phase0"], "2026-07-18T05:42:06+00:00")
            self.assertEqual(times["official"], "2026-07-18T17:30:00+00:00")
            self.assertEqual(
                baseline["open"]["OPEN-1"]["_freshness"]["baselineImportedAt"],
                "2026-07-18T18:00:00+00:00",
            )

    def test_no_plus_fails_closed_without_phase0_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = root / "official.sqlite3"
            connection = sqlite3.connect(database)
            connection.executescript(
                """
                CREATE TABLE baseline_tenders (
                  reference_number TEXT PRIMARY KEY,seed_state TEXT,source_layer TEXT,
                  imported_at TEXT,record_json TEXT
                );
                INSERT INTO baseline_tenders VALUES
                  ('A','awarded','phase0','2026-07-18T10:00:00+00:00','{"ref":"A"}');
                """
            )
            connection.commit()
            connection.close()
            args = build_args(root, database, root / "missing.json")
            args.phase0_lock = None
            with self.assertRaisesRegex(RuntimeError, "requires --phase0-lock"):
                build(args)

    def test_db_only_awarded_partial_is_proven_by_lock_and_reflected_in_status(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = root / "official.sqlite3"
            connection = sqlite3.connect(database)
            connection.executescript(
                """
                CREATE TABLE baseline_tenders (
                  reference_number TEXT PRIMARY KEY,seed_state TEXT,source_layer TEXT,
                  imported_at TEXT,record_json TEXT,source_fetched_at TEXT
                );
                INSERT INTO baseline_tenders VALUES
                  ('OPEN','open','phase0/open','2026-07-18T11:00:00+00:00',
                   '{"ref":"OPEN","deadline":"2026-07-19T12:00:00+00:00"}',
                   '2026-07-18T10:00:00+00:00'),
                  ('AWARD','awarded','phase0/awarded','2026-07-18T11:00:00+00:00',
                   '{"ref":"AWARD","winAmount":1}',
                   '2026-07-18T10:00:00+00:00');
                CREATE TABLE meta (key TEXT PRIMARY KEY,value TEXT);
                INSERT INTO meta VALUES
                  ('baseline_awarded','{"source_has_more":true,"source_partial":true,"source_complete":false,"source_fetched_at":"2026-07-18T10:00:00+00:00"}');
                """
            )
            connection.commit()
            connection.close()
            lock = write_phase0_lock(root / "PHASE0_BASELINE.lock.json", has_more=True)
            data = root / "data"
            data.mkdir()
            (data / "fetch_status.json").write_text(
                json.dumps(
                    {
                        "phase": "FETCH_ONLY",
                        "updated_at": "2026-07-18T09:00:00+00:00",
                        "mode": "playwright_single_session_raw_first",
                        "gate": {"meterRemaining": 600, "winnerfacet_status": 200},
                        "canonical_projection": {"stale": True},
                    }
                )
            )
            manifest = build(build_args(root, database, lock))
            awarded = json.loads((root / "data/awarded_index.json").read_text())
            status = json.loads((root / "data/fetch_status.json").read_text())
            self.assertTrue(awarded["meta"]["partial"])
            self.assertTrue(manifest["completeness"]["phase0Awarded"]["partial"])
            self.assertIn(
                "phase0_baseline_lock",
                manifest["completeness"]["phase0Awarded"]["validatedBy"],
            )
            self.assertIn(
                "official_db_meta_baseline_awarded",
                manifest["completeness"]["phase0Awarded"]["validatedBy"],
            )
            self.assertTrue(
                status["canonical_projection"]["completeness"]["phase0Awarded"]["partial"]
            )
            self.assertEqual(status["phase"], "CANONICAL_PERIODIC")
            self.assertEqual(
                status["mode"], "official_periodic_raw_first_projection"
            )
            self.assertEqual(
                status["phase0_acquisition"]["phase"], "FETCH_ONLY"
            )
            self.assertEqual(
                status["phase0_acquisition"]["gate"]["winnerfacet_status"], 200
            )
            self.assertNotIn(
                "canonical_projection", status["phase0_acquisition"]
            )
            self.assertNotIn("updated_at", status["phase0_acquisition"])
            self.assertFalse(status["phase0_acquisition"]["current"])
            self.assertEqual(status["source"], "etimad_official_periodic")
            phase0_status = status["phase0_acquisition"]
            build(build_args(root, database, lock))
            repeated_status = json.loads(
                (root / "data/fetch_status.json").read_text()
            )
            self.assertEqual(
                repeated_status["phase0_acquisition"], phase0_status
            )
            self.assertNotIn("gate", repeated_status)
            self.assertNotIn("winnerfacet", repeated_status)
            contract = check(root)
            self.assertEqual(contract["awarded"], 1)

    def test_lifecycle_and_deadline_windows_are_recomputed_from_snapshot_time(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = root / "official.sqlite3"
            connection = sqlite3.connect(database)
            rows = [
                ("W7", "{\"ref\":\"W7\",\"deadline\":\"2026-07-23T12:00:00+00:00\",\"days\":99}"),
                ("W30", "{\"ref\":\"W30\",\"deadline\":\"2026-08-07T12:00:00+00:00\",\"days\":1}"),
                ("PAST", "{\"ref\":\"PAST\",\"deadline\":\"2026-07-17T12:00:00+00:00\"}"),
                ("CANCEL", "{\"ref\":\"CANCEL\",\"status\":\"ملغاة\",\"deadline\":\"2026-07-25T12:00:00+00:00\"}"),
                ("AWARDING", "{\"ref\":\"AWARDING\",\"status\":\"مرحلة الترسية\",\"deadline\":\"2026-07-17T12:00:00+00:00\"}"),
                ("UNKNOWN", "{\"ref\":\"UNKNOWN\"}"),
            ]
            connection.execute(
                """CREATE TABLE baseline_tenders (
                reference_number TEXT PRIMARY KEY,seed_state TEXT,source_layer TEXT,
                imported_at TEXT,record_json TEXT,source_fetched_at TEXT)"""
            )
            connection.executemany(
                "INSERT INTO baseline_tenders VALUES (?, 'open','phase0/open',?,?,?)",
                [
                    (ref, "2026-07-18T11:00:00+00:00", payload, "2026-07-18T10:00:00+00:00")
                    for ref, payload in rows
                ],
            )
            connection.execute(
                "INSERT INTO baseline_tenders VALUES ('AWARD','awarded','phase0/awarded',?,?,?)",
                (
                    "2026-07-18T11:00:00+00:00",
                    '{"ref":"AWARD","winAmount":1}',
                    "2026-07-18T10:00:00+00:00",
                ),
            )
            connection.commit()
            connection.close()
            lock = write_phase0_lock(root / "PHASE0_BASELINE.lock.json")
            manifest = build(build_args(root, database, lock))

            def refs(name):
                payload = json.loads((root / f"data/{name}.json").read_text())
                return {row["ref"] for row in payload["records"]}

            self.assertEqual(refs("open"), {"W7", "W30"})
            self.assertEqual(refs("within_7"), {"W7"})
            self.assertEqual(refs("within_30"), {"W7", "W30"})
            self.assertEqual(refs("awarding"), {"AWARDING"})
            self.assertEqual(refs("examination"), {"PAST"})
            self.assertEqual(refs("cancelled"), {"CANCEL"})
            self.assertEqual(refs("unknown"), {"UNKNOWN"})
            open_payload = json.loads((root / "data/open.json").read_text())
            w7 = open_payload["records"][0]
            self.assertEqual(w7["tenderCategory"], "open")
            self.assertNotEqual(w7["days"], 99)
            self.assertTrue(open_payload["meta"]["partial"])
            self.assertFalse(open_payload["meta"]["coverageComplete"])
            open_dataset = next(
                item for item in manifest["datasets"] if item["id"] == "open"
            )
            self.assertTrue(open_dataset["partial"])
            self.assertFalse(manifest["completeness"]["officialUniverseComplete"])
            self.assertFalse(
                manifest["still_missing"]["active_refresh_sweep"]["complete"]
            )
            self.assertFalse(
                manifest["still_missing"]["entity_alias_registry"]["complete"]
            )

    def test_db_projection_preserves_components_freshness_evidence_without_duplicate_sources(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "official.sqlite3"
            connection = sqlite3.connect(database)
            connection.executescript(
                """
                CREATE TABLE baseline_tenders (
                  reference_number TEXT PRIMARY KEY,seed_state TEXT,source_layer TEXT,
                  imported_at TEXT,record_json TEXT,source_fetched_at TEXT
                );
                INSERT INTO baseline_tenders VALUES
                  ('RICH','open','phase0/open','2026-07-18T11:00:00+00:00',
                   '{"ref":"RICH","name":"Seed","deadline":"2026-07-20T12:00:00+00:00"}',
                   '2026-07-18T10:00:00+00:00');
                CREATE TABLE tenders (
                  reference_number TEXT PRIMARY KEY,official_tender_id INTEGER,tender_id_string TEXT,
                  tender_name TEXT,tender_number TEXT,agency_name TEXT,branch_name TEXT,
                  tender_type_id INTEGER,tender_type_name TEXT,tender_status_id INTEGER,
                  tender_status_name TEXT,activity_id INTEGER,activity_name TEXT,region TEXT,
                  submitted_at TEXT,deadline TEXT,expected_award_at TEXT,official_url TEXT,
                  stable_hash TEXT,official_json TEXT,seed_json TEXT,source_kind TEXT,
                  first_seen_at TEXT,last_seen_at TEXT,baseline_linked INTEGER,award_state TEXT,
                  next_award_check_at TEXT,last_award_checked_at TEXT,award_json TEXT,award_mode TEXT
                );
                INSERT INTO tenders VALUES (
                  'RICH',7,'token','Official name','T-7','Agency','Branch',1,'Type',4,'نشط',
                  2,'Activity','Riyadh','2026-07-18T09:00:00+00:00','2026-07-20T12:00:00+00:00',
                  NULL,'https://example.test/tender','payload-hash',
                  '{"referenceNumber":"RICH","tenderName":"Official name","tenderStatusName":"نشط","isSMEs":true}',
                  NULL,'official_list','2026-07-18T09:00:00+00:00','2026-07-18T12:00:00+00:00',
                  1,'not_due',NULL,'2026-07-18T12:10:00+00:00',NULL,'direct'
                );
                CREATE TABLE components (
                  reference_number TEXT,component TEXT,raw_path TEXT,sha256 TEXT,parsed_json TEXT,
                  checked_at TEXT,success_checked_at TEXT,error TEXT,parser_version INTEGER
                );
                INSERT INTO components VALUES
                  ('RICH','dates','raw/dates.bin','dates-sha','{"offersOpening":"2026-07-21"}',
                   '2026-07-18T12:05:00+00:00','2026-07-18T12:01:00+00:00','http_429',3),
                  ('RICH','relations','raw/relations.bin','relations-sha','{"executionLocation":"Riyadh"}',
                   '2026-07-18T12:02:00+00:00','2026-07-18T12:02:00+00:00',NULL,3);
                CREATE TABLE tender_versions (
                  id INTEGER PRIMARY KEY,reference_number TEXT,raw_path TEXT
                );
                INSERT INTO tender_versions VALUES (1,'RICH','raw/list.bin');
                CREATE TABLE raw_manifest (raw_path TEXT,sha256 TEXT);
                INSERT INTO raw_manifest VALUES ('raw/list.bin','list-sha');
                """
            )
            connection.commit()
            connection.close()
            baseline, official, _ = load_official_database(database)
            merged = official_overlay(baseline["open"]["RICH"], official["RICH"])
            self.assertEqual(merged["componentDetails"]["dates"]["offersOpening"], "2026-07-21")
            self.assertEqual(merged["_freshness"]["baselineFetchedAt"], "2026-07-18T10:00:00+00:00")
            self.assertEqual(merged["_freshness"]["datesCheckedAt"], "2026-07-18T12:01:00+00:00")
            self.assertEqual(merged["_evidence"]["list"]["sha256"], "list-sha")
            self.assertEqual(merged["_evidence"]["dates"]["parserVersion"], 3)
            self.assertEqual(merged["_evidence"]["dates"]["lastAttemptedAt"], "2026-07-18T12:05:00+00:00")
            self.assertEqual(merged["_evidence"]["dates"]["lastError"], "http_429")
            self.assertTrue(merged["flags"]["isSMEs"])
            markers = [
                (item["id"], item.get("fetchedAt"), item.get("layer"))
                for item in merged["_provenance"]["sources"]
            ]
            self.assertEqual(len(markers), len(set(markers)))
            root = Path(temp)
            lock = write_phase0_lock(root / "PHASE0_BASELINE.lock.json")
            build(build_args(root, database, lock))
            projected = json.loads((root / "data/open.json").read_text())["records"][0]
            status = json.loads((root / "data/fetch_status.json").read_text())
            self.assertEqual(status["official_periodic"]["warehouse_records"], 1)
            self.assertEqual(
                status["official_periodic"]["official_observed_records"], 1
            )
            self.assertEqual(
                projected["componentDetails"]["relations"]["executionLocation"],
                "Riyadh",
            )
            self.assertEqual(projected["_evidence"]["relations"]["sha256"], "relations-sha")

    def test_cold_component_failure_is_evidence_not_official_detail(self):
        projected = official_projection_record(
            {
                "reference_number": "COLD",
                "official_json": '{"referenceNumber":"COLD"}',
                "official_url": "https://example.test/cold",
                "first_seen_at": "2026-07-18T10:00:00+00:00",
                "last_seen_at": "2026-07-18T10:00:00+00:00",
            },
            baseline_info=None,
            component_rows={
                "dates": {
                    "parsed_json": '{"text":"Request Rejected"}',
                    "checked_at": "2026-07-18T12:05:00+00:00",
                    "success_checked_at": None,
                    "error": "http_status_429",
                    "raw_path": "raw/cold-429.bin",
                    "sha256": "failure-sha",
                    "parser_version": 3,
                }
            },
            groups=[],
            latest_version=None,
            raw_sha_by_path={},
        )
        self.assertNotIn("dates", projected["componentDetails"])
        self.assertIsNone(projected["_freshness"]["datesCheckedAt"])
        self.assertEqual(
            projected["_evidence"]["dates"]["lastError"], "http_status_429"
        )

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
            lock = write_phase0_lock(root / "PHASE0_BASELINE.lock.json")
            args = build_args(
                root,
                database,
                lock,
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
            stable_awarded = {
                name: digest
                for name, digest in second_hashes.items()
                if name == "awarded_index.json" or name.startswith("awarded_details/")
            }
            args.as_of = "2026-07-25T12:00:00+00:00"
            third = build(args)
            later_awarded = {
                name: item["sha256"]
                for name, item in third["assets"].items()
                if name == "awarded_index.json" or name.startswith("awarded_details/")
            }
            self.assertEqual(stable_awarded, later_awarded)


if __name__ == "__main__":
    unittest.main()
