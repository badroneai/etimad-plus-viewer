from __future__ import annotations

import hashlib
from copy import deepcopy
from datetime import date, timedelta
from typing import Any, Sequence


DOMAIN_START = date(1900, 1, 1)
DOMAIN_SPLIT = date(2000, 1, 1)
DOMAIN_END = date(2101, 1, 1)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def single_day_refinement_status(
    *,
    state: str = "covered",
) -> dict[str, Any]:
    if state not in {"refining", "covered", "blocked"}:
        raise ValueError("unsupported refinement fixture state")
    covered = int(state == "covered")
    refining = int(state == "refining")
    blocked = int(state == "blocked")
    exact_nodes = 169 if covered else 0
    pending_nodes = 169 if refining else 0
    blocked_nodes = 169 if blocked else 0
    replay_valid = state != "blocked"
    return {
        "version": 1,
        "strategy": "single_day_type_area_cover_v1",
        "query_hash": (
            "d078ee4040ba11bcea31164ee9cef853db2e39e77563e92a81ffbb27b1498eb8"
        ),
        "taxonomy": {
            "entries": [
                {
                    "kind": "type",
                    "values": 13,
                    "sha256": (
                        "9985e4bc429dfad5503375de846a5823f815e9b55f4bb0f8a8bc7fdc5dd2e4eb"
                    ),
                    "raw_path": (
                        "data/official_warehouse/raw/priority_save/fixture/"
                        "taxonomy-type.bin"
                    ),
                    "source_mode": "locked_official_seed",
                    "raw_replay_valid": True,
                },
                {
                    "kind": "area",
                    "values": 13,
                    "sha256": (
                        "5cd180eab2ba28b97e17a8ca9c3c49f5aef18837bbc08587b0c121fa12546da1"
                    ),
                    "raw_path": (
                        "data/official_warehouse/raw/priority_save/fixture/"
                        "taxonomy-area.bin"
                    ),
                    "source_mode": "locked_official_seed",
                    "raw_replay_valid": True,
                },
            ],
            "raw_replay_valid": True,
        },
        "cells_total": 1,
        "cells_refining": refining,
        "cells_covered": covered,
        "cells_blocked": blocked,
        "nodes_total": 169,
        "nodes_pending": pending_nodes,
        "nodes_pending_page2": 0,
        "nodes_exact": exact_nodes,
        "nodes_blocked": blocked_nodes,
        "accepted_pages": 169 if covered else 0,
        "probe_pages": 169 if covered else 0,
        "max_page_requested": 2 if covered else 0,
        "seals_total": covered,
        "seals_valid": covered,
        "raw_replay_valid": replay_valid,
        "raw_replay_error_count": blocked,
        "raw_replay_errors": ["fixture:blocked"] if blocked else [],
        "identity_conflict_count": 0,
        "identity_conflicts": [],
        "duplicate_observations": 2,
        "overlap_count": 0,
        "temporal_reconciliation": {
            "version": 2,
            "generation": 2,
            "max_generation": 3,
            "cells_total": 0,
            "cells_generation_2": 0,
            "cells_generation_3": 0,
            "cells_collecting": 0,
            "cells_awaiting_day_close": 0,
            "cells_sealed": 0,
            "cells_blocked": 0,
            "closing_proofs_total": 0,
            "closing_proofs_valid": 0,
            "entries": [],
        },
    }


def attach_covered_single_day_refinement(
    progress: dict[str, Any],
) -> dict[str, Any]:
    """Split the first covered fixture leaf and mark its first day as refined."""

    result = deepcopy(progress)
    first = result["coverage"]["intervals"][0]
    if first["state"] != "covered":
        raise ValueError("the first fixture interval must be covered")
    start = date.fromisoformat(first["from_day"])
    end = date.fromisoformat(first["to_day_exclusive"])
    split = start + timedelta(days=1)
    refined = {
        **first,
        "interval_id": f"{first['interval_id']}-refined",
        "to_day_exclusive": split.isoformat(),
        "units": 1,
        "total_count": 49,
        "terminal_reason": "enumerated_single_day_type_partition",
    }
    remainder = {
        **first,
        "interval_id": f"{first['interval_id']}-remainder",
        "from_day": split.isoformat(),
        "units": (end - split).days,
    }
    result["coverage"]["intervals"][:1] = [refined, remainder]
    result["coverage"]["leaves_covered"] += 1
    result["frontier"]["cells_total"] += 1
    result["frontier"]["covered"] += 1
    result["frontier"]["accepted_pages"] += 1
    result["single_day_refinement"] = single_day_refinement_status()
    return result


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
    opening_total = 1 if states else None
    scanned_percent = (
        round(100.0 * min(unique_references, opening_total) / opening_total, 6)
        if opening_total
        else 100.0
        if opening_total == 0
        else None
    )
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
        "competition_progress": {
            "basis": "cycle_opening_root_total_non_authoritative",
            "opening_total": opening_total,
            "opening_evidence": (
                {
                    "attempt_no": 1,
                    "capture_kind": "accepted",
                    "raw_path": "data/official_warehouse/raw/root-page-one.bin",
                    "sha256": _sha("root-page-one"),
                    "observed_at": "2026-07-19T00:05:00+00:00",
                }
                if opening_total is not None
                else None
            ),
            "observed_unique": unique_references,
            "observed_against_opening_total": (
                min(unique_references, opening_total)
                if opening_total is not None
                else 0
            ),
            "arrivals_or_drift_beyond_opening_total": (
                max(0, unique_references - opening_total)
                if opening_total is not None
                else 0
            ),
            "scanned_percent": scanned_percent,
            "denominator_authoritative": False,
            "completion_gate": "coverage.complete",
        },
        "official_active_scanned_unique": unique_references,
        "official_active_scanned_percent": scanned_percent,
        "official_active_scanned_percent_basis": (
            "cycle_opening_root_total_non_authoritative"
        ),
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
