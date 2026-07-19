from __future__ import annotations

from copy import deepcopy
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
    attach_covered_single_day_refinement,
    interval_coverage_progress,
    outer_active_scan,
    single_day_refinement_status,
)
from test_cardinality_seal_contract import cardinality_fixture  # noqa: E402


class IntervalCoverageContractTests(unittest.TestCase):
    @staticmethod
    def _sealed_temporal_reconciliation(*, generation: int = 2) -> dict:
        history = [
            {
                "generation": historical_generation,
                "union_unique": 121 + historical_generation,
                "union_sha256": chr(96 + historical_generation) * 64,
                "bijection_sha256": chr(100 + historical_generation) * 64,
            }
            for historical_generation in range(1, generation)
        ]
        baseline = history[-1]
        return {
            "version": 2,
            "generation": generation,
            "max_generation": 3,
            "cells_total": 1,
            "cells_generation_2": int(generation == 2),
            "cells_generation_3": int(generation == 3),
            "cells_collecting": 0,
            "cells_awaiting_day_close": 0,
            "cells_sealed": 1,
            "cells_blocked": 0,
            "closing_proofs_total": generation,
            "closing_proofs_valid": generation,
            "entries": [
                {
                    "cell_id": "cell-0-refined",
                    "state": "sealed",
                    "generation": generation,
                    "generation_history": history,
                    "baseline_union_unique": baseline["union_unique"],
                    "baseline_union_sha256": baseline["union_sha256"],
                    "baseline_bijection_sha256": baseline["bijection_sha256"],
                    "generation_union_unique": baseline["union_unique"],
                    "generation_union_sha256": baseline["union_sha256"],
                    "generation_bijection_sha256": baseline["bijection_sha256"],
                    "failure_reason": None,
                }
            ],
        }

    def test_type_area_single_day_refinement_contract_is_replayable(self) -> None:
        progress = attach_covered_single_day_refinement(
            interval_coverage_progress()
        )

        assert_active_interval_coverage_contract(progress)
        refinement = progress["single_day_refinement"]
        self.assertEqual(refinement["strategy"], "single_day_type_area_cover_v1")
        self.assertEqual(
            [entry["kind"] for entry in refinement["taxonomy"]["entries"]],
            ["type", "area"],
        )
        self.assertEqual(refinement["cells_covered"], refinement["seals_valid"])
        self.assertEqual(refinement["max_page_requested"], 2)

        future_compatible = deepcopy(progress)
        future_compatible["single_day_refinement"]["future_metric"] = {
            "accepted": True
        }
        assert_active_interval_coverage_contract(future_compatible)

    def test_refinement_is_optional_until_a_refined_interval_exists(self) -> None:
        initial = interval_coverage_progress((), raw_replay_valid=False)
        partial = interval_coverage_progress(("covered",), raw_replay_valid=True)
        historical_complete = interval_coverage_progress()
        for progress in (initial, partial, historical_complete):
            assert_active_interval_coverage_contract(progress)

        refining = interval_coverage_progress(("covered",), raw_replay_valid=True)
        refining["single_day_refinement"] = single_day_refinement_status(
            state="refining"
        )
        assert_active_interval_coverage_contract(refining)

        replay_pending = deepcopy(refining)
        replay_pending["single_day_refinement"]["taxonomy"]["entries"][1][
            "raw_replay_valid"
        ] = False
        replay_pending["single_day_refinement"]["taxonomy"][
            "raw_replay_valid"
        ] = False
        replay_pending["single_day_refinement"]["raw_replay_valid"] = False
        assert_active_interval_coverage_contract(replay_pending)

        missing = attach_covered_single_day_refinement(
            interval_coverage_progress()
        )
        del missing["single_day_refinement"]
        with self.assertRaisesRegex(AssertionError, "missing single-day refinement"):
            assert_active_interval_coverage_contract(missing)

    def test_refinement_shape_and_replay_metrics_fail_closed(self) -> None:
        valid = attach_covered_single_day_refinement(
            interval_coverage_progress()
        )
        cases: list[tuple[str, dict, str]] = []

        bad_version = deepcopy(valid)
        bad_version["single_day_refinement"]["version"] = True
        cases.append(("version", bad_version, "version mismatch"))

        bad_strategy = deepcopy(valid)
        bad_strategy["single_day_refinement"]["strategy"] = (
            "single_day_type_partition_v1"
        )
        cases.append(("strategy", bad_strategy, "strategy mismatch"))

        bad_query = deepcopy(valid)
        bad_query["single_day_refinement"]["query_hash"] = "not-a-sha"
        cases.append(("query", bad_query, "query SHA-256 is invalid"))

        forged_query = deepcopy(valid)
        forged_query["single_day_refinement"]["query_hash"] = "0" * 64
        cases.append(
            ("forged query", forged_query, "query contract mismatch")
        )

        missing_area = deepcopy(valid)
        missing_area["single_day_refinement"]["taxonomy"]["entries"].pop()
        cases.append(("taxonomy kinds", missing_area, "taxonomy kinds mismatch"))

        bad_area_sha = deepcopy(valid)
        bad_area_sha["single_day_refinement"]["taxonomy"]["entries"][1][
            "sha256"
        ] = "0" * 64
        cases.append(("taxonomy SHA", bad_area_sha, "taxonomy SHA mismatch: area"))

        for unsafe_path in ("../../etc/passwd", "/etc/passwd", "unrelated.bin"):
            bad_path = deepcopy(valid)
            bad_path["single_day_refinement"]["taxonomy"]["entries"][0][
                "raw_path"
            ] = unsafe_path
            cases.append(
                (
                    f"taxonomy path {unsafe_path}",
                    bad_path,
                    "taxonomy RAW path is unsafe: type",
                )
            )

        bad_taxonomy_replay = deepcopy(valid)
        bad_taxonomy_replay["single_day_refinement"]["taxonomy"]["entries"][1][
            "raw_replay_valid"
        ] = False
        cases.append(
            (
                "taxonomy replay",
                bad_taxonomy_replay,
                "taxonomy RAW replay arithmetic",
            )
        )

        bad_cells = deepcopy(valid)
        bad_cells["single_day_refinement"]["cells_total"] = 2
        cases.append(("cells", bad_cells, "cell arithmetic mismatch"))

        bad_nodes = deepcopy(valid)
        bad_nodes["single_day_refinement"]["nodes_total"] += 1
        cases.append(("nodes", bad_nodes, "node arithmetic mismatch"))

        impossible_nodes = deepcopy(valid)
        impossible_nodes["single_day_refinement"].update(
            {"nodes_total": 1, "nodes_exact": 1}
        )
        cases.append(
            ("node geometry", impossible_nodes, "node geometry is impossible")
        )

        bad_max_page = deepcopy(valid)
        bad_max_page["single_day_refinement"]["max_page_requested"] = 3
        cases.append(("page ceiling", bad_max_page, "page-2 ceiling"))

        bad_pages = deepcopy(valid)
        bad_pages["single_day_refinement"].update(
            {"accepted_pages": 0, "probe_pages": 0}
        )
        cases.append(("page metrics", bad_pages, "page metrics are inconsistent"))

        impossible_pages = deepcopy(valid)
        impossible_pages["single_day_refinement"]["accepted_pages"] = 10**9
        cases.append(
            (
                "unbounded page metrics",
                impossible_pages,
                "page metrics exceed bounded retries",
            )
        )

        missing_seal = deepcopy(valid)
        missing_seal["single_day_refinement"].update(
            {"seals_total": 0, "seals_valid": 0}
        )
        cases.append(("seal", missing_seal, "covered cell lacks a valid seal"))

        bad_error_count = deepcopy(valid)
        bad_error_count["single_day_refinement"]["raw_replay_errors"] = [
            "fixture:raw"
        ]
        cases.append(("RAW error count", bad_error_count, "error count mismatch"))

        ignored_raw_error = deepcopy(valid)
        ignored_raw_error["single_day_refinement"].update(
            {
                "raw_replay_error_count": 1,
                "raw_replay_errors": ["fixture:raw"],
            }
        )
        cases.append(("RAW error flag", ignored_raw_error, "ignores RAW replay errors"))

        bad_duplicate_count = deepcopy(valid)
        bad_duplicate_count["single_day_refinement"]["duplicate_observations"] = True
        cases.append(("duplicates", bad_duplicate_count, "duplicate_observations"))

        bad_identity_count = deepcopy(valid)
        bad_identity_count["single_day_refinement"]["identity_conflicts"] = [
            "fixture:identity"
        ]
        cases.append(
            ("identity count", bad_identity_count, "identity conflict count mismatch")
        )

        for name, progress, message in cases:
            with self.subTest(name=name), self.assertRaisesRegex(
                AssertionError, message
            ):
                assert_active_interval_coverage_contract(progress)

    def test_temporal_reconciliation_requires_converged_generation_and_proofs(
        self,
    ) -> None:
        valid = attach_covered_single_day_refinement(interval_coverage_progress())
        valid["single_day_refinement"]["temporal_reconciliation"] = (
            self._sealed_temporal_reconciliation()
        )
        assert_active_interval_coverage_contract(valid)

        valid_generation_three = deepcopy(valid)
        valid_generation_three["single_day_refinement"]["temporal_reconciliation"] = (
            self._sealed_temporal_reconciliation(generation=3)
        )
        assert_active_interval_coverage_contract(valid_generation_three)

        cases: list[tuple[str, dict, str]] = []
        legacy_version = deepcopy(valid)
        legacy_version["single_day_refinement"]["temporal_reconciliation"][
            "version"
        ] = 1
        cases.append(("legacy version", legacy_version, "version mismatch"))

        missing = deepcopy(valid)
        del missing["single_day_refinement"]["temporal_reconciliation"]
        cases.append(("missing", missing, "status is missing or invalid"))

        missing_proofs = deepcopy(valid)
        missing_proofs["single_day_refinement"]["temporal_reconciliation"].update(
            {"closing_proofs_total": 0, "closing_proofs_valid": 0}
        )
        cases.append(("missing proofs", missing_proofs, "fewer closing proofs"))

        awaiting_without_proof = deepcopy(valid)
        awaiting_reconciliation = awaiting_without_proof["single_day_refinement"][
            "temporal_reconciliation"
        ]
        awaiting_reconciliation.update(
            {
                "cells_awaiting_day_close": 1,
                "cells_sealed": 0,
                "closing_proofs_total": 0,
                "closing_proofs_valid": 0,
            }
        )
        awaiting_reconciliation["entries"][0]["state"] = "awaiting_day_close"
        cases.append(
            (
                "awaiting without proof",
                awaiting_without_proof,
                "fewer closing proofs",
            )
        )

        generation_three_sealed_without_history_proof = deepcopy(
            valid_generation_three
        )
        generation_three_sealed_without_history_proof["single_day_refinement"][
            "temporal_reconciliation"
        ].update({"closing_proofs_total": 2, "closing_proofs_valid": 2})
        cases.append(
            (
                "generation three sealed without historical proof",
                generation_three_sealed_without_history_proof,
                "fewer closing proofs",
            )
        )

        generation_three_awaiting_without_history_proof = deepcopy(
            valid_generation_three
        )
        generation_three_awaiting = generation_three_awaiting_without_history_proof[
            "single_day_refinement"
        ]["temporal_reconciliation"]
        generation_three_awaiting.update(
            {
                "cells_awaiting_day_close": 1,
                "cells_sealed": 0,
                "closing_proofs_total": 1,
                "closing_proofs_valid": 1,
            }
        )
        generation_three_awaiting["entries"][0]["state"] = "awaiting_day_close"
        cases.append(
            (
                "generation three awaiting without historical proof",
                generation_three_awaiting_without_history_proof,
                "fewer closing proofs",
            )
        )

        generation_three_collecting_without_history_proof = deepcopy(
            valid_generation_three
        )
        generation_three_collecting = generation_three_collecting_without_history_proof[
            "single_day_refinement"
        ]["temporal_reconciliation"]
        generation_three_collecting.update(
            {
                "cells_collecting": 1,
                "cells_sealed": 0,
                "closing_proofs_total": 0,
                "closing_proofs_valid": 0,
            }
        )
        generation_three_collecting["entries"][0].update(
            {
                "state": "collecting_generation",
                "generation_union_unique": None,
                "generation_union_sha256": None,
                "generation_bijection_sha256": None,
            }
        )
        cases.append(
            (
                "generation three collecting without historical proof",
                generation_three_collecting_without_history_proof,
                "fewer closing proofs",
            )
        )

        missing_history = deepcopy(valid)
        del missing_history["single_day_refinement"]["temporal_reconciliation"][
            "entries"
        ][0]["generation_history"]
        cases.append(
            ("missing history", missing_history, "history is missing or invalid")
        )

        malformed_history = deepcopy(valid_generation_three)
        malformed_history["single_day_refinement"]["temporal_reconciliation"][
            "entries"
        ][0]["generation_history"].pop()
        cases.append(
            ("malformed history", malformed_history, "history sequence mismatch")
        )

        driftless_generation_three = deepcopy(valid_generation_three)
        driftless_history = driftless_generation_three["single_day_refinement"][
            "temporal_reconciliation"
        ]["entries"][0]["generation_history"]
        driftless_history[1].update(
            {
                key: driftless_history[0][key]
                for key in ("union_unique", "union_sha256", "bijection_sha256")
            }
        )
        cases.append(
            (
                "driftless generation three",
                driftless_generation_three,
                "history has no drift",
            )
        )

        stale_baseline = deepcopy(valid_generation_three)
        stale_baseline["single_day_refinement"]["temporal_reconciliation"][
            "entries"
        ][0]["baseline_union_sha256"] = "f" * 64
        cases.append(
            ("stale baseline", stale_baseline, "does not match generation history")
        )

        forged_identity = deepcopy(valid)
        forged_identity["single_day_refinement"]["temporal_reconciliation"][
            "entries"
        ][0]["cell_id"] = "forged-covered-cell"
        cases.append(
            (
                "forged sealed identity",
                forged_identity,
                "not a refined covered interval",
            )
        )

        forged_blocked_identity = deepcopy(valid)
        blocked_interval = forged_blocked_identity["coverage"]["intervals"][0]
        blocked_interval.update(
            {
                "state": "terminal_gap",
                "terminal_reason": "single_day_type_refinement_blocked:fixture",
            }
        )
        blocked_coverage = forged_blocked_identity["coverage"]
        blocked_coverage["units_covered"] -= 1
        blocked_coverage["units_gap"] += 1
        blocked_coverage["leaves_covered"] -= 1
        blocked_coverage["leaves_gap"] += 1
        blocked_coverage["coverage_percent"] = round(
            100.0
            * blocked_coverage["units_covered"]
            / (
                blocked_coverage["units_covered"]
                + blocked_coverage["units_gap"]
                + blocked_coverage["units_pending"]
            ),
            6,
        )
        blocked_coverage["complete"] = False
        forged_blocked_identity["phase"] = "complete_with_gaps"
        blocked_refinement = single_day_refinement_status(state="blocked")
        blocked_refinement["temporal_reconciliation"] = {
            "version": 2,
            "generation": 2,
            "max_generation": 3,
            "cells_total": 1,
            "cells_generation_2": 1,
            "cells_generation_3": 0,
            "cells_collecting": 0,
            "cells_awaiting_day_close": 0,
            "cells_sealed": 0,
            "cells_blocked": 1,
            "closing_proofs_total": 0,
            "closing_proofs_valid": 0,
            "entries": [
                {
                    "cell_id": "forged-blocked-cell",
                    "state": "blocked",
                    "generation": 2,
                    "generation_history": [
                        {
                            "generation": 1,
                            "union_unique": 122,
                            "union_sha256": "a" * 64,
                            "bijection_sha256": "b" * 64,
                        }
                    ],
                    "baseline_union_unique": 122,
                    "baseline_union_sha256": "a" * 64,
                    "baseline_bijection_sha256": "b" * 64,
                    "generation_union_unique": None,
                    "generation_union_sha256": None,
                    "generation_bijection_sha256": None,
                    "failure_reason": "fixture",
                }
            ],
        }
        forged_blocked_identity["single_day_refinement"] = blocked_refinement
        cases.append(
            (
                "forged blocked identity",
                forged_blocked_identity,
                "not a refined terminal-gap interval",
            )
        )

        forged_blocked_reason = deepcopy(forged_blocked_identity)
        forged_blocked_reason_entry = forged_blocked_reason[
            "single_day_refinement"
        ]["temporal_reconciliation"]["entries"][0]
        forged_blocked_reason_entry["cell_id"] = "cell-0-refined"
        forged_blocked_reason_entry["failure_reason"] = "forged_reason"
        cases.append(
            (
                "forged blocked reason only",
                forged_blocked_reason,
                "failure reason does not match terminal interval",
            )
        )

        partial_blocked_generation = deepcopy(forged_blocked_identity)
        partial_blocked_entry = partial_blocked_generation["single_day_refinement"][
            "temporal_reconciliation"
        ]["entries"][0]
        partial_blocked_entry["cell_id"] = "cell-0-refined"
        partial_blocked_entry["generation_union_unique"] = 122
        cases.append(
            (
                "partial blocked generation",
                partial_blocked_generation,
                "has partial generation values",
            )
        )

        garbage_blocked_generation = deepcopy(forged_blocked_identity)
        garbage_blocked_entry = garbage_blocked_generation["single_day_refinement"][
            "temporal_reconciliation"
        ]["entries"][0]
        garbage_blocked_entry.update(
            {
                "cell_id": "cell-0-refined",
                "generation_union_unique": "122",
                "generation_union_sha256": "not-a-sha",
                "generation_bijection_sha256": "not-a-sha",
            }
        )
        cases.append(
            (
                "garbage blocked generation",
                garbage_blocked_generation,
                "cardinality is invalid",
            )
        )

        generation_three_blocked_without_history_proof = deepcopy(
            forged_blocked_identity
        )
        generation_three_blocked = generation_three_blocked_without_history_proof[
            "single_day_refinement"
        ]["temporal_reconciliation"]
        generation_three_blocked.update(
            {
                "generation": 3,
                "cells_generation_2": 0,
                "cells_generation_3": 1,
            }
        )
        generation_three_blocked_entry = generation_three_blocked["entries"][0]
        generation_three_blocked_entry.update(
            {
                "cell_id": "cell-0-refined",
                "generation": 3,
                "generation_history": [
                    *generation_three_blocked_entry["generation_history"],
                    {
                        "generation": 2,
                        "union_unique": 123,
                        "union_sha256": "c" * 64,
                        "bijection_sha256": "d" * 64,
                    },
                ],
                "baseline_union_unique": 123,
                "baseline_union_sha256": "c" * 64,
                "baseline_bijection_sha256": "d" * 64,
            }
        )
        cases.append(
            (
                "generation three blocked without historical proof",
                generation_three_blocked_without_history_proof,
                "fewer closing proofs",
            )
        )

        wrong_generation = deepcopy(valid)
        wrong_generation["single_day_refinement"]["temporal_reconciliation"]["generation"] = 3
        cases.append(("generation", wrong_generation, "generation maximum mismatch"))

        changed_union = deepcopy(valid)
        changed_union["single_day_refinement"]["temporal_reconciliation"]["entries"][0][
            "generation_union_sha256"
        ] = "c" * 64
        cases.append(("union", changed_union, "union did not converge"))

        changed_bijection = deepcopy(valid)
        changed_bijection["single_day_refinement"]["temporal_reconciliation"]["entries"][0][
            "generation_bijection_sha256"
        ] = "c" * 64
        cases.append(("bijection", changed_bijection, "bijection did not converge"))

        invalid_proof = deepcopy(valid)
        invalid_proof["single_day_refinement"]["temporal_reconciliation"][
            "closing_proofs_valid"
        ] = 1
        cases.append(("proof", invalid_proof, "invalid closing proofs"))

        unfinished = deepcopy(valid)
        reconciliation = unfinished["single_day_refinement"]["temporal_reconciliation"]
        reconciliation.update(
            {
                "cells_collecting": 1,
                "cells_sealed": 0,
                "closing_proofs_total": 0,
                "closing_proofs_valid": 0,
            }
        )
        reconciliation["entries"][0].update(
            {
                "state": "collecting_generation",
                "generation_union_unique": None,
                "generation_union_sha256": None,
                "generation_bijection_sha256": None,
            }
        )
        cases.append(("unfinished", unfinished, "still collecting"))

        for name, progress, message in cases:
            with self.subTest(name=name), self.assertRaisesRegex(AssertionError, message):
                assert_active_interval_coverage_contract(progress)

    def test_terminal_refinement_rejects_unfinished_or_conflicting_state(
        self,
    ) -> None:
        valid = attach_covered_single_day_refinement(
            interval_coverage_progress()
        )
        cases: list[tuple[str, dict, str]] = []

        refining = deepcopy(valid)
        refining["single_day_refinement"].update(
            {"cells_total": 2, "cells_refining": 1}
        )
        cases.append(("refining", refining, "still has refining cells"))

        pending = deepcopy(valid)
        pending["single_day_refinement"].update(
            {"nodes_total": 170, "nodes_pending": 1}
        )
        cases.append(("pending", pending, "still has pending nodes"))

        pending_page2 = deepcopy(valid)
        pending_page2["single_day_refinement"].update(
            {"nodes_total": 170, "nodes_pending_page2": 1}
        )
        cases.append(
            ("pending page2", pending_page2, "still has pending page-2 nodes")
        )

        blocked = deepcopy(valid)
        blocked["single_day_refinement"].update(
            {"nodes_total": 170, "nodes_blocked": 1}
        )
        cases.append(("blocked", blocked, "still has blocked nodes"))

        blocked_cell = deepcopy(valid)
        blocked_interval = blocked_cell["coverage"]["intervals"][0]
        blocked_interval.update(
            {
                "state": "terminal_gap",
                "terminal_reason": "single_day_type_refinement_blocked:fixture",
            }
        )
        blocked_coverage = blocked_cell["coverage"]
        blocked_coverage["units_covered"] -= 1
        blocked_coverage["units_gap"] += 1
        blocked_coverage["leaves_covered"] -= 1
        blocked_coverage["leaves_gap"] += 1
        blocked_coverage["coverage_percent"] = round(
            100.0
            * blocked_coverage["units_covered"]
            / (
                blocked_coverage["units_covered"]
                + blocked_coverage["units_gap"]
                + blocked_coverage["units_pending"]
            ),
            6,
        )
        blocked_coverage["complete"] = False
        blocked_cell["phase"] = "complete_with_gaps"
        blocked_cell["single_day_refinement"] = single_day_refinement_status(
            state="blocked"
        )
        cases.append(("blocked cell", blocked_cell, "still has blocked cells"))

        invalid_replay = deepcopy(valid)
        invalid_replay["single_day_refinement"]["raw_replay_valid"] = False
        cases.append(("RAW replay", invalid_replay, "RAW replay is invalid"))

        identity = deepcopy(valid)
        identity["single_day_refinement"].update(
            {
                "identity_conflict_count": 1,
                "identity_conflicts": ["fixture:identity"],
            }
        )
        cases.append(("identity", identity, "has identity conflicts"))

        overlap = deepcopy(valid)
        overlap["single_day_refinement"]["overlap_count"] = 1
        cases.append(("overlap", overlap, "has overlap conflicts"))

        for name, progress, message in cases:
            with self.subTest(name=name), self.assertRaisesRegex(
                AssertionError, message
            ):
                assert_active_interval_coverage_contract(progress)

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

    def test_competition_percentage_uses_only_the_non_authoritative_opening_total(
        self,
    ) -> None:
        initial = interval_coverage_progress(())
        self.assertIsNone(initial["competition_progress"]["opening_total"])
        self.assertIsNone(initial["official_active_scanned_percent"])
        assert_active_interval_coverage_contract(initial)

        progress = interval_coverage_progress(("covered",))
        self.assertEqual(progress["competition_progress"]["scanned_percent"], 100.0)
        self.assertFalse(
            progress["competition_progress"]["denominator_authoritative"]
        )
        assert_active_interval_coverage_contract(progress)

        forged = deepcopy(progress)
        forged["competition_progress"]["scanned_percent"] = 50.0
        with self.assertRaisesRegex(AssertionError, "competition scanned percent"):
            assert_active_interval_coverage_contract(forged)

        forged_authority = deepcopy(progress)
        forged_authority["competition_progress"][
            "denominator_authoritative"
        ] = True
        with self.assertRaisesRegex(AssertionError, "cannot be authoritative"):
            assert_active_interval_coverage_contract(forged_authority)

        missing_terminal_denominator = interval_coverage_progress()
        missing_terminal_denominator["competition_progress"].update(
            {
                "opening_total": None,
                "opening_evidence": None,
                "observed_against_opening_total": 0,
                "arrivals_or_drift_beyond_opening_total": 0,
                "scanned_percent": None,
            }
        )
        missing_terminal_denominator["official_active_scanned_percent"] = None
        with self.assertRaisesRegex(AssertionError, "requires a replayed opening"):
            assert_active_interval_coverage_contract(
                missing_terminal_denominator
            )

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
