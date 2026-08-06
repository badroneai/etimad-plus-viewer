from __future__ import annotations

import unittest
from datetime import datetime, timezone

from scripts.observability import evaluate_backlog, evaluate_health


UTC = timezone.utc


def status(*, pending: int, work: int, region: int) -> dict:
    return {
        "active_scan": {"cycle_id": "cycle-a"},
        "active_date_scan": {
            "coverage": {"units_pending": pending},
            "frontier": {"pending": work, "pending_page2": 0, "refining": 0},
        },
        "region_backfill": {"remaining": region},
    }


class BacklogHealthTests(unittest.TestCase):
    def test_two_growth_transitions_warn_and_three_are_critical(self) -> None:
        warning = evaluate_backlog(
            [
                status(pending=130, work=1, region=1),
                status(pending=115, work=1, region=1),
                status(pending=100, work=1, region=1),
            ]
        )
        self.assertEqual(warning["state"], "warning")

        critical = evaluate_backlog(
            [
                status(pending=145, work=1, region=1),
                status(pending=130, work=1, region=1),
                status(pending=115, work=1, region=1),
                status(pending=100, work=1, region=1),
            ]
        )
        self.assertEqual(critical["state"], "critical")

    def test_decreasing_backlog_is_healthy(self) -> None:
        result = evaluate_backlog(
            [
                status(pending=80, work=5, region=70),
                status(pending=90, work=6, region=80),
                status(pending=100, work=7, region=90),
            ]
        )
        self.assertEqual(result["state"], "healthy")

    def test_cycle_change_suppresses_census_growth_only(self) -> None:
        newest = status(pending=140, work=40, region=80)
        newest["active_scan"]["cycle_id"] = "cycle-b"
        result = evaluate_backlog(
            [
                newest,
                status(pending=120, work=30, region=80),
                status(pending=100, work=20, region=80),
            ]
        )
        self.assertEqual(result["state"], "healthy")


class PublicationHealthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 7, 0, 0, tzinfo=UTC)
        self.manifest = {
            "snapshot_id": "run_42_1",
            "generated_at": "2026-08-06T18:30:00Z",
        }
        self.pages_runs = {
            "workflow_runs": [
                {
                    "id": 7,
                    "status": "completed",
                    "conclusion": "success",
                    "updated_at": "2026-08-06T18:35:00Z",
                }
            ]
        }

    def test_converged_contract_is_healthy(self) -> None:
        result = evaluate_health(
            self.manifest,
            {"snapshot_id": "run_42_1"},
            self.pages_runs,
            {"7": []},
            [status(pending=80, work=5, region=70)],
            contract_exit_code=0,
            publication_committed_at=datetime(2026, 8, 6, 18, 31, tzinfo=UTC),
            now=self.now,
        )
        self.assertEqual(result["state"], "healthy")

    def test_matching_snapshot_with_broken_contract_is_critical(self) -> None:
        result = evaluate_health(
            self.manifest,
            {"snapshot_id": "run_42_1"},
            self.pages_runs,
            {"7": []},
            [status(pending=80, work=5, region=70)],
            contract_exit_code=1,
            publication_committed_at=datetime(2026, 8, 6, 18, 31, tzinfo=UTC),
            now=self.now,
        )
        self.assertTrue(result["critical"])
        self.assertEqual(
            result["checks"]["pages_convergence"]["incident_class"],
            "data_contract_failure",
        )

    def test_queued_pages_run_is_platform_incident(self) -> None:
        runs = {
            "workflow_runs": [
                {
                    "id": 8,
                    "status": "queued",
                    "conclusion": None,
                    "created_at": "2026-08-06T23:00:00Z",
                }
            ]
        }
        result = evaluate_health(
            self.manifest,
            {"snapshot_id": "run_42_1"},
            runs,
            {"8": []},
            [status(pending=80, work=5, region=70)],
            contract_exit_code=0,
            publication_committed_at=datetime(2026, 8, 6, 18, 31, tzinfo=UTC),
            now=self.now,
        )
        self.assertEqual(
            result["checks"]["pages_workflow"]["incident_class"],
            "platform_runner_allocation",
        )
        self.assertFalse(result["critical"])

    def test_started_pages_run_in_progress_is_not_application_failure(self) -> None:
        runs = {
            "workflow_runs": [
                {
                    "id": 9,
                    "status": "in_progress",
                    "conclusion": None,
                    "updated_at": "2026-08-06T23:00:00Z",
                }
            ]
        }
        result = evaluate_health(
            self.manifest,
            {"snapshot_id": "run_42_1"},
            runs,
            {"9": [{"started_at": "2026-08-06T23:00:01Z", "steps": []}]},
            [status(pending=80, work=5, region=70)],
            contract_exit_code=0,
            publication_committed_at=datetime(2026, 8, 6, 18, 31, tzinfo=UTC),
            now=self.now,
        )
        self.assertFalse(result["critical"])
        self.assertEqual(
            result["checks"]["pages_workflow"]["incident_class"],
            "pages_run_in_progress",
        )


if __name__ == "__main__":
    unittest.main()
