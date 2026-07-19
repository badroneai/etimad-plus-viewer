from __future__ import annotations

import hashlib
from copy import deepcopy
from datetime import date
from typing import Any, Sequence


DOMAIN_START = date(1900, 1, 1)
DOMAIN_SPLIT = date(2000, 1, 1)
DOMAIN_END = date(2101, 1, 1)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def interval_coverage_progress(
    states: Sequence[str] = ("covered", "covered"),
    *,
    raw_replay_valid: bool = True,
    last_authority: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if len(states) > 2 or any(
        state not in {"covered", "terminal_gap"} for state in states
    ):
        raise ValueError("the fixture accepts up to two terminal leaves")
    bounds = (
        (DOMAIN_START, DOMAIN_SPLIT),
        (DOMAIN_SPLIT, DOMAIN_END),
    )
    intervals: list[dict[str, Any]] = [
        {
            "interval_id": f"cell-{index}",
            "from_day": start.isoformat(),
            "to_day_exclusive": end.isoformat(),
            "state": state,
            "units": (end - start).days,
            "total_count": 1 if state == "covered" else 49,
            "attempt_no": 1,
            "first_observed_at": "2026-07-19T00:05:00+00:00",
            "last_observed_at": "2026-07-19T01:05:00+00:00",
            "terminal_reason": (
                "enumerated_single_page"
                if state == "covered"
                else "page_ceiling_single_day"
            ),
        }
        for index, ((start, end), state) in enumerate(zip(bounds, states))
    ]
    units = {
        state: sum(
            interval["units"]
            for interval in intervals
            if interval["state"] == state
        )
        for state in ("covered", "terminal_gap")
    }
    leaves = {
        state: sum(interval["state"] == state for interval in intervals)
        for state in ("covered", "terminal_gap")
    }
    units_total = (DOMAIN_END - DOMAIN_START).days
    units_pending = units_total - units["covered"] - units["terminal_gap"]
    terminal = units_pending == 0
    complete = bool(
        terminal
        and units["terminal_gap"] == 0
        and raw_replay_valid
    )
    observation_records = leaves["covered"]
    unique_references = min(1, observation_records)
    progress: dict[str, Any] = {
        "schema_version": 5,
        "strategy": "deadline_interval_coverage_v1",
        "mode": "official_active_interval_sweep",
        "partition_version": 1,
        "cycle_id": "interval-cycle",
        "phase": (
            "complete"
            if complete
            else "complete_with_gaps"
            if terminal
            else "sweeping"
        ),
        "cycle_terminal": terminal,
        "complete": False,
        "coverage_domain": {
            "field": "lastOfferPresentationDate",
            "from_day": DOMAIN_START.isoformat(),
            "to_day_exclusive": DOMAIN_END.isoformat(),
            "timezone": "Asia/Riyadh",
            "units": "calendar_days",
            "units_total": units_total,
            "query_hash": _sha("lastOfferPresentationDate|1900-01-01|2101-01-01"),
        },
        "coverage": {
            "model": "exhaustive_partition_interval_v1",
            "complete": complete,
            "units_covered": units["covered"],
            "units_gap": units["terminal_gap"],
            "units_pending": units_pending,
            "coverage_percent": round(100.0 * units["covered"] / units_total, 6),
            "traversal_percent": round(
                100.0
                * (units["covered"] + units["terminal_gap"])
                / units_total,
                6,
            ),
            "leaves_covered": leaves["covered"],
            "leaves_gap": leaves["terminal_gap"],
            "leaves_pending": 0 if terminal else 1,
            "geometry_complete": terminal,
            "geometry_error_count": 0,
            "geometry_errors": [],
            "identity_conflict_count": 0,
            "identity_conflicts": [],
            "raw_replay_valid": raw_replay_valid,
            "raw_replay_error_count": 0 if raw_replay_valid else 1,
            "raw_replay_errors": [] if raw_replay_valid else ["fixture:raw"],
            "intervals": intervals,
        },
        "frontier": {
            "cells_total": max(1, len(intervals) + (0 if terminal else 1)),
            "pending": 0 if terminal else 1,
            "pending_page2": 0,
            "split": max(0, len(intervals) - 1),
            "covered": leaves["covered"],
            "gap": leaves["terminal_gap"],
            "accepted_pages": leaves["covered"],
            "probe_pages": 0,
            "max_page_requested": 1 if intervals else 0,
        },
        "observation_window": {
            "started_at": "2026-07-19T00:00:00+00:00",
            "first_observed_at": (
                "2026-07-19T00:05:00+00:00"
                if observation_records
                else None
            ),
            "last_observed_at": (
                "2026-07-19T01:05:00+00:00"
                if observation_records
                else None
            ),
            "completed_at": (
                "2026-07-19T01:10:00+00:00" if terminal else None
            ),
        },
        "observations": {
            "unique_references": unique_references,
            "observation_records": observation_records,
            "duplicate_observations": observation_records - unique_references,
            "union_sha256": _sha("100\n" if unique_references else ""),
            "semantics": (
                "observed_at_least_once_during_cell_observation_intervals"
            ),
        },
        "targets": {
            "total": 1,
            "observed": unique_references,
            "absent": 0,
            "resolved": unique_references,
        },
        "snapshot_authoritative": False,
        "instantaneous_snapshot_authoritative": False,
        "union_authoritative": False,
        "partition_authoritative": False,
        "absence_authoritative": False,
        "completion_authoritative": False,
    }
    if last_authority is not None:
        progress["last_authority"] = deepcopy(last_authority)
    return progress


def outer_active_scan(progress: dict[str, Any]) -> dict[str, Any]:
    targets = progress["targets"]
    denominator = int(targets["total"])
    observed = int(targets["observed"])
    resolved = int(targets["resolved"])
    absent = int(targets["absent"])
    remaining = denominator - resolved
    return {
        "cycle_id": progress["cycle_id"],
        "denominator": denominator,
        "targets_scanned_unique": observed,
        "targets_resolved_unique": resolved,
        "targets_absent_after_full_pass": absent,
        "targets_remaining": remaining,
        "scanned_percent": round(
            100.0 * observed / denominator if denominator else 0.0,
            6,
        ),
        "coverage_percent": round(
            100.0 * resolved / denominator if denominator else 0.0,
            6,
        ),
        "absence_confirmation_passes": 2,
        "complete": bool(progress["coverage"]["complete"] and remaining == 0),
        "date_fallback": deepcopy(progress),
    }
