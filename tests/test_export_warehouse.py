from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from export_warehouse import (  # noqa: E402
    AWARDED_INDEX_PART_COUNT,
    SHARD_COUNT,
    add_money_projection,
    award_is_announced,
    build,
    classify_tender,
    load_official_database,
    official_overlay,
    official_projection_record,
    index_part_for_ref,
    load_active_scan_authority,
    resolve_awarded_truth,
    searchable_award,
    seed_record,
    shard_for_ref,
    to_halalas,
)
from check_data_contract import (  # noqa: E402
    assert_active_date_scan_contract,
    assert_active_scan_progress_contract,
    assert_awarded_lifecycle_contract,
    assert_region_backfill_contract,
    check,
)


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


def reference_sha(references: list[str]) -> str:
    canonical = sorted(set(references))
    payload = "\n".join(canonical) + ("\n" if canonical else "")
    return hashlib.sha256(payload.encode()).hexdigest()


def hybrid_active_fixture() -> tuple[dict, dict]:
    bootstrap_refs = ["A", "B", "R"]
    date_refs = ["A", "B"]
    union_sha = reference_sha(bootstrap_refs)
    date_sha = reference_sha(date_refs)
    residual_sha = reference_sha(["R"])

    def boundary_capture(
        references: list[str],
        *,
        filtered: bool,
        phase: str,
        generation: int,
    ) -> dict:
        params = [
            ("TenderCategory", "2"),
            ("PublishDateId", "1"),
            ("SortDirection", "DESC"),
            ("Sort", "SubmitionDate"),
            ("PageSize", "24"),
            ("IsSearch", "true"),
        ]
        if filtered:
            params.extend(
                [
                    ("FromLastOfferPresentationDateString", "01/01/1900"),
                    ("ToLastOfferPresentationDateString", "31/12/2100"),
                ]
            )
        params.append(("PageNumber", "1"))
        raw_path = f"raw/{phase}-{'filtered' if filtered else 'all'}-{generation}.bin"
        raw_bytes = json.dumps(
            {
                "data": [
                    {"referenceNumber": reference} for reference in references
                ],
                "totalCount": len(references),
                "pageSize": 24,
                "currentPage": 1,
            },
            separators=(",", ":"),
        ).encode()
        return {
            "raw_path": raw_path,
            "sha256": hashlib.sha256(raw_bytes).hexdigest(),
            "status": 200,
            "url": (
                "https://tenders.etimad.sa/Tender/"
                f"AllSupplierTendersForVisitorAsync?{urlencode(params)}"
            ),
            "content_type": "application/json",
            "bytes": len(raw_bytes),
            "total_count": len(references),
            "records": len(references),
            "references": references,
            "reference_sha256": reference_sha(references),
        }

    date_root = {
        "range_id": "range-1",
        "from_day": "1900-01-01",
        "to_day": "2100-12-31",
        "parent_range_id": None,
        "depth": 0,
        "state": "leaf_exact",
        "next_page": 1,
        "total_count": 2,
        "generation": 2,
        "boundary_total_count": 3,
        "boundary_ref_sha256": union_sha,
        "domain_matches_boundary": False,
        "closing_boundary_total_count": 3,
        "closing_boundary_ref_sha256": union_sha,
        "closing_boundary_generation": 2,
        "closing_boundary_matches": True,
        "scanned_high_watermark": 3,
        "convergence_union_sha256": union_sha,
        "convergence_passes": 2,
        "convergence_last_generation": 2,
        "bootstrap_pass_number": 1,
        "opening_filtered_ref_sha256": date_sha,
        "closing_filtered_ref_sha256": date_sha,
    }

    def generation_proof(generation: int, ordinal: int) -> dict:
        date_raw_path = f"raw/date-{generation}.bin"
        residual_raw_path = f"raw/residual-{generation}.bin"
        return {
            "bootstrap_pass_number": 1,
            "generation": generation,
            "convergence_ordinal": ordinal,
            "date_unique": 2,
            "date_union_sha256": date_sha,
            "residual_unique": 1,
            "residual_union_sha256": residual_sha,
            "union_unique": 3,
            "union_sha256": union_sha,
            "bootstrap_union_sha256": union_sha,
            "opening_filtered_total_count": 2,
            "opening_filtered_ref_sha256": date_sha,
            "closing_filtered_total_count": 2,
            "closing_filtered_ref_sha256": date_sha,
            "opening_boundary_total_count": 3,
            "opening_boundary_ref_sha256": union_sha,
            "closing_boundary_total_count": 3,
            "closing_boundary_ref_sha256": union_sha,
            "date_references": date_refs,
            "residual_references": ["R"],
            "union_references": bootstrap_refs,
            "range_generations": [
                {
                    "range_id": "range-1",
                    "from_day": "1900-01-01",
                    "to_day": "2100-12-31",
                    "generation": generation,
                    "total_count": 2,
                }
            ],
            "page_evidence": [
                {
                    "range_id": "range-1",
                    "generation": generation,
                    "page_number": 1,
                    "total_count": 2,
                    "records": 2,
                    "raw_path": date_raw_path,
                    "sha256": hashlib.sha256(date_raw_path.encode()).hexdigest(),
                    "references": date_refs,
                }
            ],
            "residual_evidence": [
                {
                    "reference_number": "R",
                    "state": "verified_active",
                    "status_id": 4,
                    "raw_path": residual_raw_path,
                    "sha256": hashlib.sha256(residual_raw_path.encode()).hexdigest(),
                    "run_id": f"run-{generation}",
                    "checked_at": f"2026-07-{17 + generation}T03:00:00+00:00",
                }
            ],
            "boundary_evidence": {
                "opening": {
                    "filtered": boundary_capture(
                        date_refs,
                        filtered=True,
                        phase="opening",
                        generation=generation,
                    ),
                    "unfiltered": boundary_capture(
                        bootstrap_refs,
                        filtered=False,
                        phase="opening",
                        generation=generation,
                    ),
                },
                "closing": {
                    "filtered": boundary_capture(
                        date_refs,
                        filtered=True,
                        phase="closing",
                        generation=generation,
                    ),
                    "unfiltered": boundary_capture(
                        bootstrap_refs,
                        filtered=False,
                        phase="closing",
                        generation=generation,
                    ),
                },
            },
            "run_id": f"run-{generation}",
            "closed_at": f"2026-07-{17 + generation}T04:00:00+00:00",
        }

    proofs = [generation_proof(1, 1), generation_proof(2, 2)]
    progress = {
        "schema_version": 3,
        "mode": "official_active_hybrid_union",
        "cycle_id": "cycle-hybrid",
        "generation": 2,
        "target_count": 2,
        "targets_observed_unique": 2,
        "targets_resolved_unique": 2,
        "targets_absent_after_full_partitions": 0,
        "targets_observed_percent": 100.0,
        "ranges_total": 1,
        "ranges_pending": 0,
        "ranges_split": 0,
        "ranges_exact": 1,
        "ranges_blocked_single_day": 0,
        "root_from_day": "1900-01-01",
        "root_to_day": "2100-12-31",
        "root_domain_fixed": True,
        "root_filtered_total": 2,
        "root_unfiltered_boundary_total": 3,
        "domain_matches_unfiltered_boundary": False,
        "unfiltered_boundary_matches_bootstrap": True,
        "closing_boundary_matches": True,
        "official_active_scanned_unique": 3,
        "official_active_scanned_lifetime_high_watermark": 3,
        "official_active_scanned_percent": 100.0,
        "official_active_generation_scanned_unique": 3,
        "official_active_generation_scanned_percent": 100.0,
        "accepted_pages_current_generation": 1,
        "accepted_records_current_generation": 2,
        "partition_duplicate_records": 0,
        "leaf_integrity_error_count": 0,
        "range_geometry_error_count": 0,
        "generation_union_sha256": union_sha,
        "convergence_union_sha256": union_sha,
        "convergence_passes": 2,
        "convergence_last_generation": 2,
        "convergence_matches_current_union": True,
        "partition_ready_for_closing_boundary": True,
        "date_partition_complete": True,
        "date_partition_authoritative": True,
        "union_authoritative": True,
        "absence_authoritative": False,
        "completion_authoritative": True,
        "bootstrap": {
            "state": "complete",
            "pass_number": 1,
            "total_count": 3,
            "page_size": 24,
            "expected_pages": 1,
            "pages_committed": 1,
            "page_holes": [],
            "page_hole_count": 0,
            "records": 3,
            "unique_refs": 3,
            "duplicate_records": 0,
            "integrity_error_count": 0,
            "head_ref_sha256": union_sha,
            "union_sha256": union_sha,
            "complete": True,
        },
        "residual": {
            "derivation": "bootstrap_minus_date_partition",
            "known_unique": 1,
            "verified_status4_unique": 1,
            "verified_nonactive_unique": 0,
            "pending_unique": 0,
            "unknown_unique": 0,
            "date_overlap_unique": 0,
            "date_outside_bootstrap_unique": 0,
            "set_sha256": residual_sha,
            "verification_generation": 2,
            "reconciliation_required": False,
        },
        "authoritative_union": {
            "unique_refs": 3,
            "duplicate_records": 0,
            "union_sha256": union_sha,
            "bootstrap_union_sha256": union_sha,
            "matches_bootstrap": True,
            "unfiltered_boundary_total": 3,
            "boundary_matches": True,
            "convergence_passes": 2,
            "convergence_last_generation": 2,
            "convergence_union_sha256": union_sha,
            "matches_current": True,
            "authoritative": True,
        },
        "generation_proofs": {
            "required": 2,
            "recorded_for_bootstrap_pass": 2,
            "matching_current_union": 2,
            "distinct_matching_generations": 2,
            "generations": [1, 2],
            "convergence_ordinals": [1, 2],
            "authoritative": True,
        },
        "partition_authoritative": True,
        "evidence_asset": {
            "schema_version": 1,
            "file": "active_scan_authority.json",
            "bytes": 1,
            "sha256": "e" * 64,
        },
    }
    evidence = {
        "schema_version": 1,
        "cycle_id": "cycle-hybrid",
        "generation": 2,
        "bootstrap": {
            "pass_number": 1,
            "pages": [
                {
                    "page_number": 1,
                    "records": 3,
                    "total_count": 3,
                    "raw_path": "raw/bootstrap.bin",
                    "sha256": hashlib.sha256(b"raw/bootstrap.bin").hexdigest(),
                    "references": ["R", "A", "B"],
                }
            ],
            "references": bootstrap_refs,
            "head_ref_sha256": union_sha,
            "union_sha256": union_sha,
        },
        "date_partition": {
            "generation": 2,
            "root": date_root,
            "ranges": [date_root],
            "pages": [
                {
                    "range_id": "range-1",
                    "range_state": "leaf_exact",
                    "page_number": 1,
                    "records": 2,
                    "total_count": 2,
                    "raw_path": "raw/date-current.bin",
                    "sha256": hashlib.sha256(b"raw/date-current.bin").hexdigest(),
                    "references": date_refs,
                }
            ],
            "references": date_refs,
            "union_sha256": date_sha,
        },
        "residual_checks": [
            {
                "reference_number": "R",
                "state": "verified_active",
                "status_id": 4,
                "raw_path": "raw/residual-R.bin",
                "sha256": hashlib.sha256(b"raw/residual-R.bin").hexdigest(),
                "run_id": "run-2",
                "checked_at": "2026-07-19T03:00:00+00:00",
                "attempts": 1,
                "error": None,
            }
        ],
        "generation_proofs": proofs,
        "authoritative_union": {
            "references": bootstrap_refs,
            "union_sha256": union_sha,
        },
    }
    raw_files: dict[str, dict] = {}

    def declare_raw(raw_path: str, sha256: str, byte_count: int = 1) -> None:
        raw_files[raw_path] = {
            "raw_path": raw_path,
            "sha256": sha256,
            "bytes": byte_count,
        }

    for page in [
        *evidence["bootstrap"]["pages"],
        *evidence["date_partition"]["pages"],
    ]:
        declare_raw(page["raw_path"], page["sha256"])
    for row in evidence["residual_checks"]:
        declare_raw(row["raw_path"], row["sha256"])
    for proof in proofs:
        for page in proof["page_evidence"]:
            declare_raw(page["raw_path"], page["sha256"])
        for row in proof["residual_evidence"]:
            declare_raw(row["raw_path"], row["sha256"])
        for phase in ("opening", "closing"):
            for lane in ("filtered", "unfiltered"):
                capture = proof["boundary_evidence"][phase][lane]
                declare_raw(capture["raw_path"], capture["sha256"], capture["bytes"])
    verification_files = [raw_files[path] for path in sorted(raw_files)]
    evidence["raw_verification"] = {
        "mode": "export_time_official_warehouse_bytes",
        "verified_files": len(verification_files),
        "verified_bytes": sum(item["bytes"] for item in verification_files),
        "files": verification_files,
    }
    return progress, evidence


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

    def test_awarded_index_part_algorithm_is_sha256_first_byte(self):
        ref = "260639009354"
        expected = (
            hashlib.sha256(ref.encode("utf-8")).digest()[0]
            % AWARDED_INDEX_PART_COUNT
        )
        self.assertEqual(index_part_for_ref(ref), expected)

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
            self.assertEqual(times["meta"], {})

    def test_baseline_only_successful_relations_overlay_region_with_evidence(self):
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
                  ('FILLED','awarded','phase0/awarded','2026-07-18T11:00:00+00:00',
                   '{"ref":"FILLED","winAmount":1}',
                   '2026-07-18T10:00:00+00:00'),
                  ('PRESERVE','awarded','phase0/awarded','2026-07-18T11:00:00+00:00',
                   '{"ref":"PRESERVE","winAmount":2,"region":"منطقة القصيم"}',
                   '2026-07-18T10:00:00+00:00'),
                  ('FAILED','awarded','phase0/awarded','2026-07-18T11:00:00+00:00',
                   '{"ref":"FAILED","winAmount":3}',
                   '2026-07-18T10:00:00+00:00'),
                  ('INVALID','awarded','phase0/awarded','2026-07-18T11:00:00+00:00',
                   '{"ref":"INVALID","winAmount":4}',
                   '2026-07-18T10:00:00+00:00');
                CREATE TABLE components (
                  reference_number TEXT,component TEXT,raw_path TEXT,sha256 TEXT,parsed_json TEXT,
                  checked_at TEXT,success_checked_at TEXT,error TEXT,parser_version INTEGER
                );
                INSERT INTO components VALUES
                  ('FILLED','relations','raw/filled-relations.bin',
                   'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                   '{"region":"منطقة الرياض،منطقة القصيم"}',
                   '2026-07-18T12:02:00+00:00','2026-07-18T12:02:00+00:00',NULL,4),
                  ('PRESERVE','relations','raw/preserve-relations.bin',
                   'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
                   '{"region":null}',
                   '2026-07-18T12:03:00+00:00','2026-07-18T12:03:00+00:00',NULL,4),
                  ('FAILED','relations','raw/failed-relations.bin',
                   'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc',
                   '{"region":"منطقة مكة المكرمة"}',
                   '2026-07-18T12:04:00+00:00',NULL,'http_403',4),
                  ('INVALID','relations','raw/invalid-relations.bin',
                   'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee',
                   '{"region":"الرياض"}',
                   '2026-07-18T12:05:00+00:00','2026-07-18T12:05:00+00:00',NULL,4);
                """
            )
            connection.commit()
            connection.close()

            baseline, official, _ = load_official_database(database)
            filled = baseline["awarded"]["FILLED"]
            self.assertFalse(official)
            self.assertEqual(filled["region"], "منطقة الرياض، منطقة القصيم")
            self.assertEqual(
                filled["_provenance"]["fieldSources"]["region"],
                "etimad_official_components",
            )
            self.assertEqual(
                filled["_freshness"]["relationsCheckedAt"],
                "2026-07-18T12:02:00+00:00",
            )
            self.assertEqual(
                filled["_evidence"]["relations"],
                {
                    "rawPath": "raw/filled-relations.bin",
                    "sha256": "a" * 64,
                    "parserVersion": 4,
                    "lastAttemptedAt": "2026-07-18T12:02:00+00:00",
                    "lastError": None,
                },
            )
            self.assertEqual(
                baseline["awarded"]["PRESERVE"]["region"],
                "منطقة القصيم",
            )
            self.assertNotIn("region", baseline["awarded"]["FAILED"])
            self.assertNotIn("region", baseline["awarded"]["INVALID"])

            read_only_check = sqlite3.connect(database)
            original = json.loads(
                read_only_check.execute(
                    "SELECT record_json FROM baseline_tenders WHERE reference_number='FILLED'"
                ).fetchone()[0]
            )
            read_only_check.close()
            self.assertNotIn("region", original)

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
            self.assertEqual(
                repeated_status["active_scan"],
                {
                    "available": False,
                    "reason": "official_database_metadata_absent",
                },
            )
            self.assertEqual(
                repeated_status["region_backfill"],
                {
                    "available": False,
                    "reason": "official_database_metadata_absent",
                },
            )

    def test_progress_metadata_is_copied_and_region_reflects_in_partitioned_assets(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = root / "official.sqlite3"
            active_scan = {
                "cycle_id": "active_fixture",
                "cohort_as_of": "2026-07-18T12:00:00+00:00",
                "denominator": 2,
                "targets_scanned_unique": 1,
                "targets_resolved_unique": 1,
                "targets_absent_after_full_pass": 0,
                "targets_remaining": 1,
                "scanned_percent": 50.0,
                "coverage_percent": 50.0,
                "absence_confirmation_passes": 2,
                "complete": False,
                "continuity_state": "anchored",
            }
            region_backfill = {
                "awarded_total": 1,
                "initial_filled": 0,
                "initial_missing": 1,
                "backfilled_unique": 1,
                "current_filled": 1,
                "remaining": 0,
                "backfill_percent": 100.0,
                "overall_fill_percent": 100.0,
            }
            connection = sqlite3.connect(database)
            connection.executescript(
                """
                CREATE TABLE baseline_tenders (
                  reference_number TEXT PRIMARY KEY,seed_state TEXT,source_layer TEXT,
                  imported_at TEXT,record_json TEXT,source_fetched_at TEXT
                );
                INSERT INTO baseline_tenders VALUES
                  ('REGION-AWARD','awarded','phase0/awarded','2026-07-18T11:00:00+00:00',
                   '{"ref":"REGION-AWARD","winAmount":1}',
                   '2026-07-18T10:00:00+00:00');
                CREATE TABLE components (
                  reference_number TEXT,component TEXT,raw_path TEXT,sha256 TEXT,parsed_json TEXT,
                  checked_at TEXT,success_checked_at TEXT,error TEXT,parser_version INTEGER
                );
                INSERT INTO components VALUES
                  ('REGION-AWARD','relations','raw/region-award-relations.bin',
                   'dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd',
                   '{"region":"منطقة الرياض"}',
                   '2026-07-18T12:02:00+00:00','2026-07-18T12:02:00+00:00',NULL,4);
                CREATE TABLE meta (key TEXT PRIMARY KEY,value TEXT);
                """
            )
            connection.executemany(
                "INSERT INTO meta VALUES (?,?)",
                (
                    (
                        "baseline_awarded",
                        json.dumps(
                            {
                                "source_has_more": True,
                                "source_partial": True,
                                "source_complete": False,
                                "source_fetched_at": "2026-07-18T10:00:00+00:00",
                            }
                        ),
                    ),
                    ("active_scan", json.dumps(active_scan)),
                    ("region_backfill", json.dumps(region_backfill)),
                ),
            )
            connection.commit()
            connection.close()

            lock = write_phase0_lock(root / "PHASE0_BASELINE.lock.json")
            (root / "data").mkdir()
            (root / "data/active_scan_authority.json").write_text("{}")
            manifest = build(build_args(root, database, lock))
            status = json.loads((root / "data/fetch_status.json").read_text())
            self.assertFalse((root / "data/active_scan_authority.json").exists())
            self.assertEqual(status["active_scan"], active_scan)
            self.assertEqual(status["region_backfill"], region_backfill)
            self.assertFalse(status["still_missing"]["active_refresh_sweep"]["complete"])

            part = f"{index_part_for_ref('REGION-AWARD'):02d}"
            index_part = json.loads(
                (root / f"data/awarded_index_parts/{part}.json").read_text()
            )
            index_row = next(
                row for row in index_part["records"] if row["ref"] == "REGION-AWARD"
            )
            detail_shard = index_row["_detailShard"]
            detail_payload = json.loads(
                (root / f"data/awarded_details/{detail_shard}.json").read_text()
            )
            detail = next(
                row for row in detail_payload["records"] if row["ref"] == "REGION-AWARD"
            )
            self.assertEqual(index_row["region"], "منطقة الرياض")
            self.assertEqual(detail["region"], "منطقة الرياض")
            self.assertEqual(
                detail["_provenance"]["fieldSources"]["region"],
                "etimad_official_components",
            )
            self.assertEqual(
                detail["_evidence"]["relations"]["sha256"],
                "d" * 64,
            )
            self.assertEqual(manifest["snapshot_id"], "test_snapshot")
            self.assertEqual(check(root)["awarded"], 1)

    def test_progress_contract_rejects_bad_arithmetic_and_missing_evidence(self):
        assert_active_scan_progress_contract(
            {
                "denominator": 2,
                "targets_scanned_unique": 1,
                "targets_resolved_unique": 1,
                "targets_absent_after_full_pass": 0,
                "targets_remaining": 1,
                "scanned_percent": 50.0,
                "coverage_percent": 50.0,
                "absence_confirmation_passes": 2,
                "complete": False,
            }
        )
        assert_active_scan_progress_contract(
            {
                "denominator": 2,
                "targets_scanned_unique": 1,
                "targets_resolved_unique": 2,
                "targets_absent_after_full_pass": 1,
                "targets_remaining": 0,
                "scanned_percent": 50.0,
                "coverage_percent": 100.0,
                "absence_confirmation_passes": 2,
                "complete": True,
            }
        )
        pending_date_fallback = {
            "cycle_id": "cycle-test",
            "target_count": 2,
            "targets_observed_unique": 2,
            "targets_resolved_unique": 2,
            "targets_absent_after_full_partitions": 0,
            "targets_observed_percent": 100.0,
            "ranges_total": 1,
            "ranges_pending": 0,
            "ranges_blocked_single_day": 0,
            "root_filtered_total": 2,
            "official_active_scanned_unique": 2,
            "official_active_scanned_percent": 100.0,
            "partition_duplicate_records": 0,
            "leaf_integrity_error_count": 0,
            "range_geometry_error_count": 0,
            "convergence_passes": 1,
            "generation": 1,
            "convergence_last_generation": 1,
            "root_domain_fixed": True,
            "domain_matches_unfiltered_boundary": True,
            "partition_authoritative": False,
            "absence_authoritative": False,
            "completion_authoritative": False,
            "closing_boundary_matches": True,
            "convergence_matches_current_union": True,
        }
        assert_active_scan_progress_contract(
            {
                "denominator": 2,
                "cycle_id": "cycle-test",
                "targets_scanned_unique": 2,
                "targets_resolved_unique": 2,
                "targets_absent_after_full_pass": 0,
                "targets_remaining": 0,
                "scanned_percent": 100.0,
                "coverage_percent": 100.0,
                "absence_confirmation_passes": 2,
                "complete": False,
                "date_fallback": pending_date_fallback,
            }
        )
        with self.assertRaisesRegex(
            AssertionError, "completed before date partition authority"
        ):
            assert_active_scan_progress_contract(
                {
                    "denominator": 2,
                    "cycle_id": "cycle-test",
                    "targets_scanned_unique": 2,
                    "targets_resolved_unique": 2,
                    "targets_absent_after_full_pass": 0,
                    "targets_remaining": 0,
                    "scanned_percent": 100.0,
                    "coverage_percent": 100.0,
                    "absence_confirmation_passes": 2,
                    "complete": True,
                    "date_fallback": pending_date_fallback,
                }
            )
        wrong_cohort = {
            **pending_date_fallback,
            "target_count": 1,
            "targets_observed_unique": 1,
            "targets_resolved_unique": 1,
        }
        with self.assertRaisesRegex(AssertionError, "target cohort differs"):
            assert_active_scan_progress_contract(
                {
                    "denominator": 2,
                    "cycle_id": "cycle-test",
                    "targets_scanned_unique": 1,
                    "targets_resolved_unique": 1,
                    "targets_absent_after_full_pass": 0,
                    "targets_remaining": 1,
                    "scanned_percent": 50.0,
                    "coverage_percent": 50.0,
                    "absence_confirmation_passes": 2,
                    "complete": False,
                    "date_fallback": wrong_cohort,
                }
            )
        with self.assertRaisesRegex(AssertionError, "remaining arithmetic"):
            assert_active_scan_progress_contract(
                {
                    "denominator": 2,
                    "targets_scanned_unique": 1,
                    "targets_resolved_unique": 1,
                    "targets_absent_after_full_pass": 0,
                    "targets_remaining": 0,
                    "scanned_percent": 50.0,
                    "coverage_percent": 50.0,
                    "absence_confirmation_passes": 2,
                    "complete": False,
                }
            )

        progress = {
            "awarded_total": 1,
            "initial_filled": 0,
            "initial_missing": 1,
            "backfilled_unique": 1,
            "current_filled": 1,
            "remaining": 0,
            "backfill_percent": 100.0,
            "overall_fill_percent": 100.0,
        }
        index = {"R": {"region": "منطقة الرياض"}}
        detail = {
            "R": {
                "region": "منطقة الرياض",
                "_provenance": {
                    "sources": [{"id": "etimad_official_components"}],
                    "fieldSources": {"region": "etimad_official_components"},
                },
                "_freshness": {"relationsCheckedAt": "2026-07-18T12:00:00+00:00"},
                "_evidence": {
                    "relations": {
                        "rawPath": "raw/relations.bin",
                        "sha256": "f" * 64,
                        "parserVersion": 4,
                    }
                },
            }
        }
        index["R"]["region"] = "الرياض"
        detail["R"]["region"] = "الرياض"
        with self.assertRaisesRegex(AssertionError, "outside the parser vocabulary"):
            assert_region_backfill_contract(progress, index, detail)

        index["R"]["region"] = "منطقة الرياض"
        detail["R"]["region"] = "منطقة الرياض"
        detail["R"]["_evidence"]["relations"]["sha256"] = "not-a-sha256"
        with self.assertRaisesRegex(AssertionError, "SHA-256 invalid"):
            assert_region_backfill_contract(progress, index, detail)

    def test_active_date_partition_contract_requires_exact_union(self):
        progress = {
            "cycle_id": "cycle-test",
            "target_count": 2,
            "targets_observed_unique": 2,
            "targets_resolved_unique": 2,
            "targets_absent_after_full_partitions": 0,
            "targets_observed_percent": 100.0,
            "ranges_total": 3,
            "ranges_pending": 0,
            "ranges_blocked_single_day": 0,
            "root_filtered_total": 2,
            "official_active_scanned_unique": 2,
            "official_active_scanned_percent": 100.0,
            "partition_duplicate_records": 0,
            "leaf_integrity_error_count": 0,
            "range_geometry_error_count": 0,
            "convergence_passes": 2,
            "generation": 2,
            "convergence_last_generation": 2,
            "root_domain_fixed": True,
            "domain_matches_unfiltered_boundary": True,
            "partition_authoritative": True,
            "absence_authoritative": False,
            "completion_authoritative": True,
            "closing_boundary_matches": True,
            "convergence_matches_current_union": True,
        }
        assert_active_date_scan_contract(progress)

        duplicate = {**progress, "partition_duplicate_records": 1}
        with self.assertRaisesRegex(AssertionError, "duplicate records"):
            assert_active_date_scan_contract(duplicate)

        pending = {**progress, "ranges_pending": 1}
        with self.assertRaisesRegex(AssertionError, "pending ranges"):
            assert_active_date_scan_contract(pending)

        narrow_domain = {**progress, "root_domain_fixed": False}
        with self.assertRaisesRegex(AssertionError, "fixed domain"):
            assert_active_date_scan_contract(narrow_domain)

        same_generation = {
            **progress,
            "generation": 1,
            "convergence_last_generation": 1,
        }
        with self.assertRaisesRegex(AssertionError, "distinct generations"):
            assert_active_date_scan_contract(same_generation)

        absence_without_authority = {
            **progress,
            "partition_authoritative": False,
            "completion_authoritative": False,
            "absence_authoritative": True,
            "targets_observed_unique": 1,
            "targets_observed_percent": 50.0,
            "targets_absent_after_full_partitions": 1,
        }
        with self.assertRaisesRegex(AssertionError, "absence lacks"):
            assert_active_date_scan_contract(absence_without_authority)

        impossible_absence = {
            **progress,
            "targets_absent_after_full_partitions": 99,
            "absence_authoritative": True,
        }
        with self.assertRaisesRegex(AssertionError, "observed/absence arithmetic"):
            assert_active_date_scan_contract(impossible_absence)

        incomplete_authority = {**progress, "completion_authoritative": False}
        with self.assertRaisesRegex(AssertionError, "completion authority arithmetic"):
            assert_active_date_scan_contract(incomplete_authority)

    def test_active_hybrid_contract_proves_bootstrap_date_and_residual_union(self):
        progress, evidence = hybrid_active_fixture()
        assert_active_date_scan_contract(progress, evidence)
        assert_active_scan_progress_contract(
            {
                "cycle_id": "cycle-hybrid",
                "denominator": 2,
                "targets_scanned_unique": 2,
                "targets_resolved_unique": 2,
                "targets_absent_after_full_pass": 0,
                "targets_remaining": 0,
                "scanned_percent": 100.0,
                "coverage_percent": 100.0,
                "absence_confirmation_passes": 2,
                "bootstrap": progress["bootstrap"],
                "bootstrap_complete": True,
                "complete": True,
                "date_fallback": progress,
            },
            evidence,
        )

    def test_active_hybrid_contract_accepts_unchecked_and_error_residual_progress(self):
        progress, evidence = hybrid_active_fixture()
        date_sha = reference_sha(["A", "B"])
        partial = deepcopy(progress)
        partial.update(
            {
                "closing_boundary_matches": False,
                "official_active_scanned_unique": 2,
                "official_active_scanned_lifetime_high_watermark": 2,
                "official_active_scanned_percent": 66.666667,
                "official_active_generation_scanned_unique": 2,
                "official_active_generation_scanned_percent": 66.666667,
                "generation_union_sha256": date_sha,
                "convergence_union_sha256": None,
                "convergence_passes": 0,
                "convergence_last_generation": None,
                "convergence_matches_current_union": False,
                "partition_ready_for_closing_boundary": False,
                "date_partition_authoritative": False,
                "union_authoritative": False,
                "partition_authoritative": False,
                "completion_authoritative": False,
            }
        )
        partial["residual"].update(
            {
                "verified_status4_unique": 0,
                "pending_unique": 1,
            }
        )
        partial["authoritative_union"].update(
            {
                "unique_refs": 2,
                "union_sha256": date_sha,
                "matches_bootstrap": False,
                "convergence_passes": 0,
                "convergence_last_generation": None,
                "convergence_union_sha256": None,
                "matches_current": False,
                "authoritative": False,
            }
        )
        partial["generation_proofs"].update(
            {
                "recorded_for_bootstrap_pass": 0,
                "matching_current_union": 0,
                "distinct_matching_generations": 0,
                "generations": [],
                "convergence_ordinals": [],
                "authoritative": False,
            }
        )
        partial_evidence = deepcopy(evidence)
        partial_evidence["authoritative_union"] = {
            "references": ["A", "B"],
            "union_sha256": date_sha,
        }
        partial_evidence["generation_proofs"] = []
        partial_raw_paths = {"raw/bootstrap.bin", "raw/date-current.bin"}
        partial_raw_files = [
            item
            for item in partial_evidence["raw_verification"]["files"]
            if item["raw_path"] in partial_raw_paths
        ]
        partial_evidence["raw_verification"].update(
            {
                "verified_files": len(partial_raw_files),
                "verified_bytes": sum(item["bytes"] for item in partial_raw_files),
                "files": partial_raw_files,
            }
        )
        root = partial_evidence["date_partition"]["root"]
        root.update(
            {
                "closing_boundary_total_count": None,
                "closing_boundary_ref_sha256": None,
                "closing_boundary_generation": None,
                "closing_boundary_matches": False,
                "scanned_high_watermark": 2,
                "convergence_union_sha256": None,
                "convergence_passes": 0,
                "convergence_last_generation": None,
                "closing_filtered_ref_sha256": None,
            }
        )

        partial_evidence["residual_checks"] = []
        assert_active_date_scan_contract(partial, partial_evidence)

        partial_evidence["residual_checks"] = [
            {
                "reference_number": "R",
                "state": "error",
                "status_id": None,
                "raw_path": None,
                "sha256": None,
                "run_id": "run-error",
                "checked_at": "2026-07-19T03:00:00+00:00",
                "attempts": 1,
                "error": "transient timeout",
            }
        ]
        assert_active_date_scan_contract(partial, partial_evidence)

    def test_active_hybrid_contract_rejects_malicious_authority_evidence(self):
        progress, evidence = hybrid_active_fixture()

        bad_hole = deepcopy(progress)
        bad_hole["bootstrap"]["pages_committed"] = 0
        with self.assertRaisesRegex(AssertionError, "committed page/evidence"):
            assert_active_date_scan_contract(bad_hole, evidence)

        bad_status = deepcopy(evidence)
        bad_status["residual_checks"][0]["status_id"] = 3
        with self.assertRaisesRegex(AssertionError, "status is not 4"):
            assert_active_date_scan_contract(progress, bad_status)

        bad_residual = deepcopy(evidence)
        bad_residual["residual_checks"][0]["reference_number"] = "A"
        bad_residual_progress = deepcopy(progress)
        bad_residual_progress["residual"]["set_sha256"] = reference_sha(["A"])
        bad_residual_progress["residual"]["date_overlap_unique"] = 1
        with self.assertRaisesRegex(AssertionError, "verified-status4 arithmetic"):
            assert_active_date_scan_contract(bad_residual_progress, bad_residual)

        bad_hash = deepcopy(progress)
        bad_hash["authoritative_union"]["union_sha256"] = "f" * 64
        with self.assertRaisesRegex(AssertionError, "status union hash mismatch"):
            assert_active_date_scan_contract(bad_hash, evidence)

        bad_proof_page = deepcopy(evidence)
        bad_proof_page["generation_proofs"][0]["page_evidence"][0][
            "references"
        ] = ["A", "X"]
        with self.assertRaisesRegex(AssertionError, "pages do not replay to D"):
            assert_active_date_scan_contract(progress, bad_proof_page)

        bad_proof_boundary = deepcopy(evidence)
        bad_proof_boundary["generation_proofs"][0][
            "closing_filtered_total_count"
        ] = 99
        with self.assertRaisesRegex(AssertionError, "filtered total changed"):
            assert_active_date_scan_contract(progress, bad_proof_boundary)

        forged_totals = deepcopy(evidence)
        forged_totals["generation_proofs"][0]["opening_filtered_total_count"] = 99
        forged_totals["generation_proofs"][0]["closing_filtered_total_count"] = 99
        with self.assertRaisesRegex(AssertionError, "filtered total differs"):
            assert_active_date_scan_contract(progress, forged_totals)

        forged_heads = deepcopy(evidence)
        forged_heads["generation_proofs"][0]["opening_filtered_ref_sha256"] = "f" * 64
        forged_heads["generation_proofs"][0]["closing_filtered_ref_sha256"] = "f" * 64
        with self.assertRaisesRegex(AssertionError, "boundary head"):
            assert_active_date_scan_contract(progress, forged_heads)

        outside_date_boundary = deepcopy(evidence)
        outside_proof = outside_date_boundary["generation_proofs"][0]
        outside_sha = reference_sha(["X", "Y"])
        outside_proof["opening_filtered_ref_sha256"] = outside_sha
        outside_proof["closing_filtered_ref_sha256"] = outside_sha
        for phase in ("opening", "closing"):
            capture = outside_proof["boundary_evidence"][phase]["filtered"]
            capture["references"] = ["X", "Y"]
            capture["reference_sha256"] = outside_sha
        with self.assertRaisesRegex(AssertionError, "references are outside D"):
            assert_active_date_scan_contract(progress, outside_date_boundary)

        future_generation = deepcopy(evidence)
        future_proof = future_generation["generation_proofs"][1]
        future_proof["generation"] = 999
        future_proof["range_generations"][0]["generation"] = 999
        future_proof["page_evidence"][0]["generation"] = 999
        with self.assertRaisesRegex(AssertionError, "future generation"):
            assert_active_date_scan_contract(progress, future_generation)

        duplicate_ordinal = deepcopy(evidence)
        duplicate_ordinal["generation_proofs"][0]["convergence_ordinal"] = 2
        with self.assertRaisesRegex(AssertionError, "ordinals are not distinct"):
            assert_active_date_scan_contract(progress, duplicate_ordinal)

        replayed_generation = deepcopy(progress)
        one_proof = deepcopy(evidence)
        one_proof["generation_proofs"] = [one_proof["generation_proofs"][1]]
        one_proof_raw_files = [
            item
            for item in one_proof["raw_verification"]["files"]
            if not item["raw_path"].endswith("-1.bin")
        ]
        one_proof["raw_verification"].update(
            {
                "verified_files": len(one_proof_raw_files),
                "verified_bytes": sum(item["bytes"] for item in one_proof_raw_files),
                "files": one_proof_raw_files,
            }
        )
        replayed_generation["generation_proofs"].update(
            {
                "recorded_for_bootstrap_pass": 1,
                "matching_current_union": 1,
                "distinct_matching_generations": 1,
                "generations": [2],
                "convergence_ordinals": [2],
                "authoritative": False,
            }
        )
        with self.assertRaisesRegex(AssertionError, "ordinals are not distinct"):
            assert_active_date_scan_contract(replayed_generation, one_proof)

    def test_export_builds_manifest_addressed_hybrid_authority_from_sqlite(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = root / "official.sqlite3"
            progress, expected_evidence = hybrid_active_fixture()
            active_scan = {
                "cycle_id": "cycle-hybrid",
                "denominator": 2,
                "targets_scanned_unique": 2,
                "targets_resolved_unique": 2,
                "targets_absent_after_full_pass": 0,
                "targets_remaining": 0,
                "scanned_percent": 100.0,
                "coverage_percent": 100.0,
                "absence_confirmation_passes": 2,
                "bootstrap": progress["bootstrap"],
                "bootstrap_complete": True,
                "complete": True,
                "date_fallback": progress,
            }
            region_backfill = {
                "awarded_total": 1,
                "initial_filled": 0,
                "initial_missing": 1,
                "backfilled_unique": 0,
                "current_filled": 0,
                "remaining": 1,
                "backfill_percent": 0.0,
                "overall_fill_percent": 0.0,
            }
            connection = sqlite3.connect(database)
            connection.executescript(
                """
                CREATE TABLE baseline_tenders (
                  reference_number TEXT PRIMARY KEY,seed_state TEXT,source_layer TEXT,
                  imported_at TEXT,record_json TEXT,source_fetched_at TEXT
                );
                INSERT INTO baseline_tenders VALUES
                  ('AWARD','awarded','phase0/awarded','2026-07-18T11:00:00+00:00',
                   '{"ref":"AWARD","winAmount":1}',
                   '2026-07-18T10:00:00+00:00');
                CREATE TABLE meta (key TEXT PRIMARY KEY,value TEXT);
                CREATE TABLE active_scan_pages (
                  cycle_id TEXT,pass_number INTEGER,page_number INTEGER,sha256 TEXT,
                  raw_path TEXT,records INTEGER,total_count INTEGER,references_json TEXT
                );
                CREATE TABLE active_scan_date_ranges (
                  cycle_id TEXT,range_id TEXT,from_day TEXT,to_day TEXT,
                  parent_range_id TEXT,depth INTEGER,state TEXT,next_page INTEGER,
                  total_count INTEGER,generation INTEGER,boundary_total_count INTEGER,
                  boundary_ref_sha256 TEXT,domain_matches_boundary INTEGER,
                  closing_boundary_total_count INTEGER,closing_boundary_ref_sha256 TEXT,
                  closing_boundary_generation INTEGER,closing_boundary_matches INTEGER,
                  scanned_high_watermark INTEGER,convergence_union_sha256 TEXT,
                  convergence_passes INTEGER,convergence_last_generation INTEGER,
                  bootstrap_pass_number INTEGER,opening_filtered_ref_sha256 TEXT,
                  closing_filtered_ref_sha256 TEXT
                );
                CREATE TABLE active_scan_date_pages (
                  cycle_id TEXT,range_id TEXT,generation INTEGER,page_number INTEGER,
                  total_count INTEGER,records INTEGER,raw_path TEXT,sha256 TEXT,
                  references_json TEXT
                );
                CREATE TABLE active_scan_residual_checks (
                  cycle_id TEXT,generation INTEGER,reference_number TEXT,state TEXT,
                  status_id INTEGER,raw_path TEXT,sha256 TEXT,run_id TEXT,
                  checked_at TEXT,attempts INTEGER,error TEXT
                );
                CREATE TABLE active_scan_date_generation_proofs (
                  cycle_id TEXT,bootstrap_pass_number INTEGER,generation INTEGER,
                  convergence_ordinal INTEGER,date_unique INTEGER,date_union_sha256 TEXT,
                  residual_unique INTEGER,residual_union_sha256 TEXT,union_unique INTEGER,
                  union_sha256 TEXT,bootstrap_union_sha256 TEXT,
                  opening_filtered_total_count INTEGER,
                  opening_filtered_ref_sha256 TEXT,
                  closing_filtered_total_count INTEGER,
                  closing_filtered_ref_sha256 TEXT,
                  opening_boundary_total_count INTEGER,
                  opening_boundary_ref_sha256 TEXT,
                  closing_boundary_total_count INTEGER,
                  closing_boundary_ref_sha256 TEXT,date_references_json TEXT,
                  residual_references_json TEXT,union_references_json TEXT,
                  range_generations_json TEXT,page_evidence_json TEXT,
                  residual_evidence_json TEXT,boundary_evidence_json TEXT,
                  run_id TEXT,closed_at TEXT
                );
                """
            )
            boundary_by_path = {
                capture["raw_path"]: capture
                for proof in expected_evidence["generation_proofs"]
                for phase in ("opening", "closing")
                for capture in proof["boundary_evidence"][phase].values()
            }
            for raw_descriptor in expected_evidence["raw_verification"]["files"]:
                raw_target = database.parent / raw_descriptor["raw_path"]
                raw_target.parent.mkdir(parents=True, exist_ok=True)
                capture = boundary_by_path.get(raw_descriptor["raw_path"])
                raw_bytes = (
                    json.dumps(
                        {
                            "data": [
                                {"referenceNumber": reference}
                                for reference in capture["references"]
                            ],
                            "totalCount": capture["total_count"],
                            "pageSize": 24,
                            "currentPage": 1,
                        },
                        separators=(",", ":"),
                    ).encode()
                    if capture is not None
                    else raw_descriptor["raw_path"].encode()
                )
                raw_target.write_bytes(raw_bytes)
            bootstrap_page = expected_evidence["bootstrap"]["pages"][0]
            connection.execute(
                "INSERT INTO active_scan_pages VALUES (?,?,?,?,?,?,?,?)",
                (
                    "cycle-hybrid",
                    1,
                    bootstrap_page["page_number"],
                    bootstrap_page["sha256"],
                    bootstrap_page["raw_path"],
                    bootstrap_page["records"],
                    bootstrap_page["total_count"],
                    json.dumps(bootstrap_page["references"]),
                ),
            )
            date_page = expected_evidence["date_partition"]["pages"][0]
            connection.execute(
                "INSERT INTO active_scan_date_pages VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    "cycle-hybrid",
                    date_page["range_id"],
                    2,
                    date_page["page_number"],
                    date_page["total_count"],
                    date_page["records"],
                    date_page["raw_path"],
                    date_page["sha256"],
                    json.dumps(date_page["references"]),
                ),
            )
            residual_row = expected_evidence["residual_checks"][0]
            connection.execute(
                "INSERT INTO active_scan_residual_checks VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "cycle-hybrid",
                    2,
                    residual_row["reference_number"],
                    residual_row["state"],
                    residual_row["status_id"],
                    residual_row["raw_path"],
                    residual_row["sha256"],
                    residual_row["run_id"],
                    residual_row["checked_at"],
                    residual_row["attempts"],
                    residual_row["error"],
                ),
            )
            root_ledger = expected_evidence["date_partition"]["root"]
            connection.execute(
                "INSERT INTO active_scan_date_ranges VALUES ("
                + ",".join("?" for _ in range(24))
                + ")",
                (
                    "cycle-hybrid",
                    root_ledger["range_id"],
                    root_ledger["from_day"],
                    root_ledger["to_day"],
                    root_ledger["parent_range_id"],
                    root_ledger["depth"],
                    root_ledger["state"],
                    root_ledger["next_page"],
                    root_ledger["total_count"],
                    root_ledger["generation"],
                    root_ledger["boundary_total_count"],
                    root_ledger["boundary_ref_sha256"],
                    int(root_ledger["domain_matches_boundary"]),
                    root_ledger["closing_boundary_total_count"],
                    root_ledger["closing_boundary_ref_sha256"],
                    root_ledger["closing_boundary_generation"],
                    int(root_ledger["closing_boundary_matches"]),
                    root_ledger["scanned_high_watermark"],
                    root_ledger["convergence_union_sha256"],
                    root_ledger["convergence_passes"],
                    root_ledger["convergence_last_generation"],
                    root_ledger["bootstrap_pass_number"],
                    root_ledger["opening_filtered_ref_sha256"],
                    root_ledger["closing_filtered_ref_sha256"],
                ),
            )
            for proof in expected_evidence["generation_proofs"]:
                connection.execute(
                    "INSERT INTO active_scan_date_generation_proofs VALUES ("
                    + ",".join("?" for _ in range(28))
                    + ")",
                    (
                        "cycle-hybrid",
                        proof["bootstrap_pass_number"],
                        proof["generation"],
                        proof["convergence_ordinal"],
                        proof["date_unique"],
                        proof["date_union_sha256"],
                        proof["residual_unique"],
                        proof["residual_union_sha256"],
                        proof["union_unique"],
                        proof["union_sha256"],
                        proof["bootstrap_union_sha256"],
                        proof["opening_filtered_total_count"],
                        proof["opening_filtered_ref_sha256"],
                        proof["closing_filtered_total_count"],
                        proof["closing_filtered_ref_sha256"],
                        proof["opening_boundary_total_count"],
                        proof["opening_boundary_ref_sha256"],
                        proof["closing_boundary_total_count"],
                        proof["closing_boundary_ref_sha256"],
                        json.dumps(proof["date_references"]),
                        json.dumps(proof["residual_references"]),
                        json.dumps(proof["union_references"]),
                        json.dumps(proof["range_generations"]),
                        json.dumps(proof["page_evidence"]),
                        json.dumps(proof["residual_evidence"]),
                        json.dumps(proof["boundary_evidence"]),
                        proof["run_id"],
                        proof["closed_at"],
                    ),
                )
            connection.executemany(
                "INSERT INTO meta VALUES (?,?)",
                (
                    (
                        "baseline_awarded",
                        json.dumps(
                            {
                                "source_has_more": True,
                                "source_partial": True,
                                "source_complete": False,
                                "source_fetched_at": "2026-07-18T10:00:00+00:00",
                            }
                        ),
                    ),
                    ("active_scan", json.dumps(active_scan)),
                    ("region_backfill", json.dumps(region_backfill)),
                ),
            )
            connection.commit()
            connection.close()

            lock = write_phase0_lock(root / "PHASE0_BASELINE.lock.json")
            manifest = build(build_args(root, database, lock))
            authority = json.loads(
                (root / "data/active_scan_authority.json").read_text()
            )
            status = json.loads((root / "data/fetch_status.json").read_text())
            descriptor = manifest["assets"]["active_scan_authority.json"]
            self.assertEqual(descriptor["role"], "active_scan_authority_evidence")
            self.assertEqual(authority["bootstrap"]["references"], ["A", "B", "R"])
            self.assertEqual(authority["date_partition"]["references"], ["A", "B"])
            self.assertEqual(
                [proof["generation"] for proof in authority["generation_proofs"]],
                [1, 2],
            )
            self.assertEqual(
                authority["generation_proofs"][1]["union_references"],
                ["A", "B", "R"],
            )
            self.assertEqual(
                status["active_scan"]["date_fallback"]["evidence_asset"]["sha256"],
                descriptor["sha256"],
            )
            self.assertNotIn("active_refresh_sweep", status["still_missing"])
            self.assertNotIn("active_refresh_sweep", manifest["still_missing"])
            self.assertEqual(check(root)["awarded"], 1)
            manifest_path = root / "data/manifest.json"
            malicious_manifest = json.loads(manifest_path.read_text())
            malicious_manifest["still_missing"]["active_refresh_sweep"] = {
                "complete": False
            }
            manifest_path.write_text(json.dumps(malicious_manifest))
            with self.assertRaisesRegex(AssertionError, "still_missing disagrees"):
                check(root)
            proof_one = expected_evidence["generation_proofs"][0]
            forged_boundary = deepcopy(proof_one["boundary_evidence"])
            forged_head = reference_sha(["X", "Y"])
            for phase in ("opening", "closing"):
                forged_boundary[phase]["filtered"]["references"] = ["X", "Y"]
                forged_boundary[phase]["filtered"]["reference_sha256"] = forged_head
            connection = sqlite3.connect(database)
            connection.execute(
                "UPDATE active_scan_date_generation_proofs SET "
                "opening_filtered_ref_sha256=?,closing_filtered_ref_sha256=?,"
                "boundary_evidence_json=? WHERE generation=1",
                (
                    forged_head,
                    forged_head,
                    json.dumps(forged_boundary),
                ),
            )
            connection.commit()
            connection.close()
            with self.assertRaisesRegex(RuntimeError, "RAW body differs"):
                load_active_scan_authority(database, active_scan)
            connection = sqlite3.connect(database)
            connection.execute(
                "UPDATE active_scan_date_generation_proofs SET "
                "opening_filtered_ref_sha256=?,closing_filtered_ref_sha256=?,"
                "boundary_evidence_json=? WHERE generation=1",
                (
                    proof_one["opening_filtered_ref_sha256"],
                    proof_one["closing_filtered_ref_sha256"],
                    json.dumps(proof_one["boundary_evidence"]),
                ),
            )
            connection.commit()
            connection.close()
            (database.parent / "raw/bootstrap.bin").write_bytes(b"tampered")
            with self.assertRaisesRegex(RuntimeError, "RAW SHA-256 mismatch"):
                load_active_scan_authority(database, active_scan)

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
                if name == "awarded_index.json"
                or name.startswith("awarded_index_parts/")
                or name.startswith("awarded_details/")
            }
            args.as_of = "2026-07-25T12:00:00+00:00"
            third = build(args)
            later_awarded = {
                name: item["sha256"]
                for name, item in third["assets"].items()
                if name == "awarded_index.json"
                or name.startswith("awarded_index_parts/")
                or name.startswith("awarded_details/")
            }
            self.assertEqual(stable_awarded, later_awarded)


if __name__ == "__main__":
    unittest.main()
