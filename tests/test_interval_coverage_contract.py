from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

from check_data_contract import (  # noqa: E402
    assert_active_date_scan_contract,
    assert_active_interval_coverage_contract,
    assert_active_missing_truth,
    assert_active_scan_progress_contract,
)
from export_warehouse import (  # noqa: E402
    active_refresh_sweep_complete,
    load_active_scan_authority,
    selected_cardinality_authority,
)
from schema5_fixtures import (  # noqa: E402
    interval_coverage_progress,
    outer_active_scan,
)
from test_cardinality_seal_contract import cardinality_fixture  # noqa: E402


class IntervalCoverageContractTests(unittest.TestCase):
    def test_complete_progressive_coverage_is_not_snapshot_authority(self) -> None:
        last_authority, _ = cardinality_fixture()
        progress = interval_coverage_progress(last_authority=last_authority)

        assert_active_interval_coverage_contract(progress)
        assert_active_date_scan_contract(progress)
        self.assertTrue(active_refresh_sweep_complete(progress))
        self.assertIsNone(selected_cardinality_authority(progress))
        self.assertEqual(progress["last_authority"], last_authority)
        self.assertFalse(progress["complete"])
        self.assertTrue(progress["coverage"]["complete"])
        self.assertFalse(progress["union_authoritative"])
        self.assertFalse(progress["absence_authoritative"])

    def test_partial_and_terminal_gap_progress_are_honest(self) -> None:
        initial = interval_coverage_progress((), raw_replay_valid=False)
        partial = interval_coverage_progress(("covered",), raw_replay_valid=False)
        terminal_gap = interval_coverage_progress(
            ("covered", "terminal_gap"), raw_replay_valid=True
        )

        for progress in (initial, partial, terminal_gap):
            with self.subTest(phase=progress["phase"]):
                assert_active_interval_coverage_contract(progress)
                self.assertFalse(active_refresh_sweep_complete(progress))
                self.assertFalse(progress["complete"])
                self.assertNotIn(
                    "pending",
                    {row["state"] for row in progress["coverage"]["intervals"]},
                )
        self.assertEqual(partial["phase"], "sweeping")
        self.assertFalse(partial["cycle_terminal"])
        self.assertEqual(terminal_gap["phase"], "complete_with_gaps")
        self.assertTrue(terminal_gap["cycle_terminal"])

    def test_interval_geometry_and_arithmetic_fail_closed(self) -> None:
        cases = []

        overlap = interval_coverage_progress()
        overlap["coverage"]["intervals"][1]["from_day"] = "1999-12-31"
        cases.append(("overlap", overlap, "overlap or are out of order"))

        unsorted = interval_coverage_progress()
        unsorted["coverage"]["intervals"].reverse()
        cases.append(("ordering", unsorted, "overlap or are out of order"))

        reversed_interval = interval_coverage_progress()
        reversed_interval["coverage"]["intervals"][0]["to_day_exclusive"] = (
            "1900-01-01"
        )
        cases.append(("reversed", reversed_interval, "empty or reversed"))

        duplicate_id = interval_coverage_progress()
        duplicate_id["coverage"]["intervals"][1]["interval_id"] = "cell-0"
        cases.append(("duplicate id", duplicate_id, "id is duplicated"))

        bad_units = interval_coverage_progress()
        bad_units["coverage"]["units_covered"] -= 1
        cases.append(("unit arithmetic", bad_units, "covered-unit arithmetic"))

        bad_percent = interval_coverage_progress()
        bad_percent["coverage"]["coverage_percent"] = 99.0
        cases.append(("percent arithmetic", bad_percent, "coverage_percent arithmetic"))

        for name, progress, message in cases:
            with self.subTest(name=name), self.assertRaisesRegex(
                AssertionError, message
            ):
                assert_active_interval_coverage_contract(progress)

    def test_terminal_leaf_shape_fails_closed_but_accepts_extra_fields(self) -> None:
        with_extra = interval_coverage_progress()
        with_extra["coverage"]["intervals"][0]["future_field"] = {
            "accepted": True
        }
        assert_active_interval_coverage_contract(with_extra)

        cases = []
        invalid_state = interval_coverage_progress()
        invalid_state["coverage"]["intervals"][0]["state"] = "pending"
        cases.append(("state", invalid_state, "state is invalid"))

        invalid_count = interval_coverage_progress()
        invalid_count["coverage"]["intervals"][0]["total_count"] = -1
        cases.append(("total_count", invalid_count, "total_count is invalid"))

        invalid_attempt = interval_coverage_progress()
        invalid_attempt["coverage"]["intervals"][0]["attempt_no"] = 0
        cases.append(("attempt", invalid_attempt, "attempt_no is invalid"))

        reversed_observation = interval_coverage_progress()
        reversed_observation["coverage"]["intervals"][0][
            "first_observed_at"
        ] = "2026-07-19T02:00:00+00:00"
        cases.append(
            ("observation", reversed_observation, "observation window is reversed")
        )

        missing_gap_reason = interval_coverage_progress(
            ("covered", "terminal_gap")
        )
        missing_gap_reason["coverage"]["intervals"][1]["terminal_reason"] = None
        cases.append(
            ("gap reason", missing_gap_reason, "terminal gap reason is missing")
        )

        for name, progress, message in cases:
            with self.subTest(name=name), self.assertRaisesRegex(
                AssertionError, message
            ):
                assert_active_interval_coverage_contract(progress)

    def test_schema5_cannot_smuggle_authority_or_an_authority_asset(self) -> None:
        forged_authority = interval_coverage_progress()
        forged_authority["union_authoritative"] = True

        forged_completion = interval_coverage_progress(raw_replay_valid=False)
        forged_completion["coverage"]["complete"] = True
        forged_completion["phase"] = "complete"

        forged_top_level = interval_coverage_progress()
        forged_top_level["complete"] = True

        forged_asset = interval_coverage_progress()
        forged_asset["evidence_asset"] = {
            "file": "active_scan_authority.json",
            "sha256": "f" * 64,
        }

        forged_absence = interval_coverage_progress()
        forged_absence["targets"].update({"absent": 1, "resolved": 1})

        forged_instantaneous = interval_coverage_progress()
        forged_instantaneous["instantaneous_snapshot_authoritative"] = True

        cases = (
            (forged_authority, "cannot claim union_authoritative"),
            (forged_completion, "completion arithmetic mismatch"),
            (forged_top_level, "top-level complete must remain false"),
            (forged_asset, "cannot own an authority evidence asset"),
            (forged_absence, "cannot claim target absence"),
            (
                forged_instantaneous,
                "cannot claim instantaneous_snapshot_authoritative",
            ),
        )
        for progress, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(
                AssertionError, message
            ):
                assert_active_interval_coverage_contract(progress)

        with self.assertRaisesRegex(AssertionError, "cannot publish authority evidence"):
            assert_active_interval_coverage_contract(
                interval_coverage_progress(), {"forged": True}
            )

    def test_historical_schema4_authority_is_summary_only(self) -> None:
        authority, _ = cardinality_fixture()
        recursive = interval_coverage_progress(last_authority=authority)
        recursive["last_authority"]["evidence_asset"] = {
            "file": "active_scan_authority.json"
        }
        with self.assertRaisesRegex(AssertionError, "historical authority cannot"):
            assert_active_interval_coverage_contract(recursive)

        same_cycle = interval_coverage_progress(last_authority=authority)
        same_cycle["last_authority"]["cycle_id"] = same_cycle["cycle_id"]
        with self.assertRaisesRegex(AssertionError, "current interval cycle"):
            assert_active_interval_coverage_contract(same_cycle)

    def test_outer_scan_and_still_missing_use_verified_coverage_completion(self) -> None:
        complete = interval_coverage_progress()
        active_scan = outer_active_scan(complete)
        assert_active_scan_progress_contract(active_scan)
        assert_active_missing_truth(active_scan, {})

        partial = interval_coverage_progress(("covered",), raw_replay_valid=False)
        partial_scan = outer_active_scan(partial)
        assert_active_scan_progress_contract(partial_scan)
        assert_active_missing_truth(
            partial_scan,
            {"active_refresh_sweep": {"complete": False}},
        )
        with self.assertRaisesRegex(AssertionError, "verified scan completion"):
            assert_active_missing_truth(partial_scan, {})

    def test_partial_schema5_accepts_larger_outer_historical_scan_balance(self) -> None:
        partial = interval_coverage_progress(("covered",), raw_replay_valid=True)
        partial["targets"].update(
            {"total": 3, "observed": 1, "resolved": 1, "absent": 0}
        )
        active_scan = outer_active_scan(partial)
        active_scan.update(
            {
                "targets_scanned_unique": 3,
                "targets_resolved_unique": 3,
                "targets_remaining": 0,
                "scanned_percent": 100.0,
                "coverage_percent": 100.0,
                "complete": False,
            }
        )

        assert_active_scan_progress_contract(active_scan)

        forged = outer_active_scan(partial)
        forged.update(
            {
                "targets_scanned_unique": 0,
                "targets_resolved_unique": 0,
                "targets_remaining": 3,
                "scanned_percent": 0.0,
                "coverage_percent": 0.0,
            }
        )
        with self.assertRaisesRegex(
            AssertionError, "exceed outer historical scan balance"
        ):
            assert_active_scan_progress_contract(forged)

    def test_schema5_dispatch_never_opens_or_exports_authority(self) -> None:
        active_scan = outer_active_scan(interval_coverage_progress())
        self.assertIsNone(
            load_active_scan_authority(
                Path("/database/does/not/exist.sqlite3"), active_scan
            )
        )


if __name__ == "__main__":
    unittest.main()
