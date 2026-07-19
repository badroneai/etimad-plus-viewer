"""Audit-focused checks for interval coverage refinement artifacts.

These checks are intentionally separated from the primary acceptance contract so
acceptance can depend on the fundamental interval ledger invariant while keeping
historical-path diagnostics optional.
"""

from __future__ import annotations

from pathlib import Path
import re

from export_warehouse import parse_iso_datetime


SINGLE_DAY_REFINEMENT_VERSION = 1
SINGLE_DAY_REFINEMENT_STRATEGY = "single_day_type_area_cover_v1"
TEMPORAL_RECONCILIATION_VERSION = 2
TEMPORAL_RECONCILIATION_GENERATIONS = {2, 3}
TEMPORAL_DAY_CLOSE_REFRESH_VERSION = 1
TEMPORAL_DAY_CLOSE_REFRESH_REASON = "temporal_reconciliation_day_close_refresh"
TEMPORAL_DAY_CLOSE_REFRESH_QUERY_SHA256 = (
    "59607bfe1815e5956264a40f47814420ebfa47002e3811ee9b82afae945102ba"
)
TEMPORAL_DAY_CLOSE_REFRESH_PAGE_SIZE = 24
SINGLE_DAY_REFINEMENT_QUERY_SHA256 = (
    "d078ee4040ba11bcea31164ee9cef853db2e39e77563e92a81ffbb27b1498eb8"
)
SINGLE_DAY_REFINEMENT_RAW_PREFIX = (
    "data",
    "official_warehouse",
    "raw",
    "priority_save",
)
SINGLE_DAY_REFINEMENT_TAXONOMIES = {
    "type": {
        "values": 13,
        "sha256": "9985e4bc429dfad5503375de846a5823f815e9b55f4bb0f8a8bc7fdc5dd2e4eb",
    },
    "area": {
        "values": 13,
        "sha256": "5cd180eab2ba28b97e17a8ca9c3c49f5aef18837bbc08587b0c121fa12546da1",
    },
}
SINGLE_DAY_REFINEMENT_COVERED_REASON = "enumerated_single_day_type_partition"
SINGLE_DAY_REFINEMENT_BLOCKED_PREFIXES = (
    "single_day_type_refinement_blocked:",
    "single_day_type_refinement_failed:",
)


def _nonnegative_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _sha256(value: object, *, label: str) -> str:
    assert isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value, flags=re.I), (
        f"{label} SHA-256 is invalid"
    )
    return value.lower()


def _assert_temporal_path_audit_failure(
    message: str,
    strict: bool,
    diagnostics: list[str] | None,
) -> None:
    """Either fail the contract path, or downgrade to a deployment warning."""

    if strict:
        raise AssertionError(message)
    if diagnostics is not None:
        diagnostics.append(message)


def _assert_temporal_day_close_refreshes(
    reconciliation: dict,
) -> dict[tuple[int, str, str], dict]:
    """Validate optional additive history that justifies no drift transitions."""

    extension_keys = {
        "day_close_refreshes_total",
        "day_close_refreshes_valid",
        "day_close_refreshes_entries",
    }
    if not extension_keys.intersection(reconciliation):
        return {}
    assert extension_keys.issubset(reconciliation), (
        "schema-5 temporal day-close refresh extension is incomplete"
    )
    total = reconciliation["day_close_refreshes_total"]
    valid = reconciliation["day_close_refreshes_valid"]
    entries = reconciliation["day_close_refreshes_entries"]
    assert _nonnegative_integer(total), (
        "schema-5 temporal day-close refresh total is invalid"
    )
    assert _nonnegative_integer(valid), (
        "schema-5 temporal day-close refresh valid count is invalid"
    )
    assert isinstance(entries, list) and len(entries) == total, (
        "schema-5 temporal day-close refresh entry count mismatch"
    )
    assert valid == total, (
        "schema-5 temporal day-close refresh ledger contains invalid evidence"
    )

    by_source: dict[tuple[int, str, str], dict] = {}
    raw_paths: set[str] = set()
    raw_hashes: set[str] = set()
    dependencies: set[str] = set()
    for entry in entries:
        assert isinstance(entry, dict), (
            "schema-5 temporal day-close refresh entry is invalid"
        )
        assert entry.get("version") == TEMPORAL_DAY_CLOSE_REFRESH_VERSION, (
            "schema-5 temporal day-close refresh version mismatch"
        )
        assert entry.get("from_generation") == 2 and entry.get("to_generation") == 3, (
            "schema-5 temporal day-close refresh generation transition mismatch"
        )
        assert entry.get("reason") == TEMPORAL_DAY_CLOSE_REFRESH_REASON, (
            "schema-5 temporal day-close refresh reason mismatch"
        )
        source_unique = entry.get("source_union_unique")
        assert (
            isinstance(source_unique, int)
            and not isinstance(source_unique, bool)
            and source_unique > 0
        ), "schema-5 temporal day-close refresh source cardinality is invalid"
        source_union = _sha256(
            entry.get("source_union_sha256"),
            label="schema-5 temporal day-close refresh source union",
        )
        source_bijection = _sha256(
            entry.get("source_bijection_sha256"),
            label="schema-5 temporal day-close refresh source bijection",
        )
        anchor_total = entry.get("anchor_total_count")
        anchor_records = entry.get("anchor_records")
        assert (
            isinstance(anchor_total, int)
            and not isinstance(anchor_total, bool)
            and anchor_total > 0
        ), "schema-5 temporal day-close refresh anchor cardinality is invalid"
        assert (
            isinstance(anchor_records, int)
            and not isinstance(anchor_records, bool)
            and anchor_records == min(TEMPORAL_DAY_CLOSE_REFRESH_PAGE_SIZE, anchor_total)
        ), "schema-5 temporal day-close refresh anchor record count mismatch"
        assert anchor_total != source_unique, (
            "schema-5 temporal day-close refresh does not prove an anchor transition"
        )
        _sha256(
            entry.get("anchor_head_sha256"),
            label="schema-5 temporal day-close refresh anchor head",
        )
        raw_path = str(entry.get("raw_path") or "").strip()
        path = Path(raw_path)
        assert (
            raw_path
            and not path.is_absolute()
            and ".." not in path.parts
            and path.parts[: len(SINGLE_DAY_REFINEMENT_RAW_PREFIX)]
            == SINGLE_DAY_REFINEMENT_RAW_PREFIX
            and path.suffix == ".bin"
        ), "schema-5 temporal day-close refresh RAW path is unsafe or missing"
        raw_sha = _sha256(
            entry.get("sha256"),
            label="schema-5 temporal day-close refresh RAW",
        )
        query_hash = _sha256(
            entry.get("query_hash"),
            label="schema-5 temporal day-close refresh query",
        )
        assert query_hash == TEMPORAL_DAY_CLOSE_REFRESH_QUERY_SHA256, (
            "schema-5 temporal day-close refresh query contract mismatch"
        )
        dependency = _sha256(
            entry.get("dependency_sha256"),
            label="schema-5 temporal day-close refresh dependency",
        )
        capture_epoch_id = str(entry.get("capture_epoch_id") or "").strip()
        assert re.fullmatch(r"[0-9a-f]{32}", capture_epoch_id, re.IGNORECASE), (
            "schema-5 temporal day-close refresh capture epoch is invalid"
        )
        run_id = str(entry.get("run_id") or "").strip()
        assert run_id.startswith("official_") and run_id in path.parts, (
            "schema-5 temporal day-close refresh run binding mismatch"
        )
        evidence_cutoff = parse_iso_datetime(entry.get("evidence_cutoff"))
        captured_at = parse_iso_datetime(entry.get("captured_at"))
        accepted_at = parse_iso_datetime(entry.get("accepted_at"))
        assert (
            evidence_cutoff is not None
            and captured_at is not None
            and accepted_at is not None
            and evidence_cutoff < captured_at <= accepted_at
        ), "schema-5 temporal day-close refresh chronology is invalid"

        source_key = (source_unique, source_union, source_bijection)
        assert source_key not in by_source, (
            "schema-5 temporal day-close refresh source binding is ambiguous"
        )
        assert raw_path not in raw_paths and raw_sha not in raw_hashes, (
            "schema-5 temporal day-close refresh reuses RAW evidence"
        )
        assert dependency not in dependencies, (
            "schema-5 temporal day-close refresh reuses a dependency proof"
        )
        by_source[source_key] = entry
        raw_paths.add(raw_path)
        raw_hashes.add(raw_sha)
        dependencies.add(dependency)
    return by_source


def assert_single_day_refinement_contract(
    refinement: object,
    *,
    covered_interval_count: int,
    blocked_interval_count: int,
    refined_covered_interval_ids: set[str],
    refined_blocked_interval_ids: set[str],
    refined_blocked_interval_reasons: dict[str, str],
    coverage_complete: bool,
    cycle_terminal: bool,
    strict_temporal_path_audit: bool = True,
    diagnostics: list[str] | None = None,
) -> None:
    """Validate the bounded type/area cover fallback for dense single days."""

    if refinement is None:
        assert covered_interval_count == 0 and blocked_interval_count == 0, (
            "schema-5 refined interval is missing single-day refinement status"
        )
        return

    assert isinstance(refinement, dict), (
        "schema-5 single-day refinement status is invalid"
    )
    version = refinement.get("version")
    assert (
        isinstance(version, int)
        and not isinstance(version, bool)
        and version == SINGLE_DAY_REFINEMENT_VERSION
    ), "schema-5 single-day refinement version mismatch"
    assert refinement.get("strategy") == SINGLE_DAY_REFINEMENT_STRATEGY, (
        "schema-5 single-day refinement strategy mismatch"
    )
    query_hash = _sha256(
        refinement.get("query_hash"),
        label="schema-5 single-day refinement query",
    )
    assert query_hash == SINGLE_DAY_REFINEMENT_QUERY_SHA256, (
        "schema-5 single-day refinement query contract mismatch"
    )

    taxonomy = refinement.get("taxonomy")
    assert isinstance(taxonomy, dict), (
        "schema-5 single-day refinement taxonomy is missing"
    )
    entries = taxonomy.get("entries")
    assert isinstance(entries, list), (
        "schema-5 single-day refinement taxonomy entries are missing"
    )
    taxonomy_kinds = [
        entry.get("kind") if isinstance(entry, dict) else None
        for entry in entries
    ]
    assert taxonomy_kinds == ["type", "area"], (
        "schema-5 single-day refinement taxonomy kinds mismatch"
    )
    for entry in entries:
        assert isinstance(entry, dict)
        kind = str(entry["kind"])
        expected = SINGLE_DAY_REFINEMENT_TAXONOMIES[kind]
        assert entry.get("values") == expected["values"] and _nonnegative_integer(
            entry.get("values")
        ), f"schema-5 single-day refinement taxonomy value count mismatch: {kind}"
        assert entry.get("sha256") == expected["sha256"], (
            f"schema-5 single-day refinement taxonomy SHA mismatch: {kind}"
        )
        raw_path = str(entry.get("raw_path") or "").strip()
        path = Path(raw_path)
        assert (
            raw_path
            and not path.is_absolute()
            and ".." not in path.parts
            and path.parts[: len(SINGLE_DAY_REFINEMENT_RAW_PREFIX)]
            == SINGLE_DAY_REFINEMENT_RAW_PREFIX
            and path.suffix == ".bin"
        ), f"schema-5 single-day refinement taxonomy RAW path is unsafe: {kind}"
        assert entry.get("source_mode") == "locked_official_seed", (
            f"schema-5 single-day refinement taxonomy source mode mismatch: {kind}"
        )
        assert isinstance(entry.get("raw_replay_valid"), bool), (
            f"schema-5 single-day refinement taxonomy RAW replay flag is invalid: {kind}"
        )
    assert isinstance(taxonomy.get("raw_replay_valid"), bool), (
        "schema-5 single-day refinement taxonomy RAW replay flag is invalid"
    )
    assert taxonomy["raw_replay_valid"] is all(
        entry["raw_replay_valid"] for entry in entries
    ), "schema-5 single-day refinement taxonomy RAW replay arithmetic mismatch"

    count_keys = (
        "cells_total",
        "cells_refining",
        "cells_covered",
        "cells_blocked",
        "nodes_total",
        "nodes_pending",
        "nodes_pending_page2",
        "nodes_exact",
        "nodes_blocked",
        "accepted_pages",
        "probe_pages",
        "max_page_requested",
        "seals_total",
        "seals_valid",
        "raw_replay_error_count",
        "identity_conflict_count",
        "duplicate_observations",
        "overlap_count",
    )
    for key in count_keys:
        assert _nonnegative_integer(refinement.get(key)), (
            f"schema-5 single-day refinement {key} is invalid"
        )
    mirror_pending = refinement.get("nodes_mirror_pending")
    assert mirror_pending is None or _nonnegative_integer(mirror_pending), (
        "schema-5 single-day refinement nodes_mirror_pending is invalid"
    )

    assert refinement["cells_total"] > 0, (
        "schema-5 single-day refinement has no refinement cells"
    )
    assert refinement["cells_total"] == (
        refinement["cells_refining"]
        + refinement["cells_covered"]
        + refinement["cells_blocked"]
    ), "schema-5 single-day refinement cell arithmetic mismatch"
    assert refinement["nodes_total"] == (
        refinement["nodes_pending"]
        + refinement["nodes_pending_page2"]
        + refinement["nodes_exact"]
        + refinement["nodes_blocked"]
        + (mirror_pending or 0)
    ), "schema-5 single-day refinement node arithmetic mismatch"
    assert refinement["nodes_total"] >= 13 * refinement["cells_total"], (
        "schema-5 single-day refinement node geometry is impossible"
    )
    assert refinement["cells_covered"] == covered_interval_count, (
        "schema-5 single-day refinement covered-cell marker mismatch"
    )
    assert refinement["cells_blocked"] == blocked_interval_count, (
        "schema-5 single-day refinement blocked-cell marker mismatch"
    )

    max_page_requested = refinement["max_page_requested"]
    assert max_page_requested <= 2, (
        "schema-5 single-day refinement exceeds the page-2 ceiling"
    )
    pages_requested = refinement["accepted_pages"] + refinement["probe_pages"]
    assert (max_page_requested == 0) == (pages_requested == 0), (
        "schema-5 single-day refinement page metrics are inconsistent"
    )
    assert pages_requested >= refinement["nodes_exact"], (
        "schema-5 single-day refinement has fewer page proofs than exact nodes"
    )
    assert pages_requested <= 4 * refinement["nodes_total"], (
        "schema-5 single-day refinement page metrics exceed bounded retries"
    )

    assert refinement["seals_valid"] <= refinement["seals_total"], (
        "schema-5 single-day refinement valid seal count exceeds total"
    )
    assert refinement["seals_total"] <= refinement["cells_total"], (
        "schema-5 single-day refinement seal count exceeds cells"
    )
    assert refinement["seals_valid"] == refinement["cells_covered"], (
        "schema-5 single-day refinement covered cell lacks a valid seal"
    )

    raw_errors = refinement.get("raw_replay_errors")
    assert isinstance(raw_errors, list) and all(
        isinstance(error, str) and error.strip() for error in raw_errors
    ), "schema-5 single-day refinement RAW replay errors are invalid"
    assert refinement["raw_replay_error_count"] == len(raw_errors), (
        "schema-5 single-day refinement RAW replay error count mismatch"
    )
    identity_conflicts = refinement.get("identity_conflicts")
    assert isinstance(identity_conflicts, list) and all(
        isinstance(conflict, str) and conflict.strip()
        for conflict in identity_conflicts
    ), "schema-5 single-day refinement identity conflicts are invalid"
    assert refinement["identity_conflict_count"] == len(identity_conflicts), (
        "schema-5 single-day refinement identity conflict count mismatch"
    )
    assert isinstance(refinement.get("raw_replay_valid"), bool), (
        "schema-5 single-day refinement RAW replay flag is invalid"
    )
    if taxonomy["raw_replay_valid"] is False:
        assert refinement["raw_replay_valid"] is False, (
            "schema-5 single-day refinement replays invalid taxonomy as valid"
        )
    if refinement["raw_replay_error_count"] > 0:
        assert refinement["raw_replay_valid"] is False, (
            "schema-5 single-day refinement ignores RAW replay errors"
        )

    reconciliation = refinement.get("temporal_reconciliation")
    assert isinstance(reconciliation, dict), (
        "schema-5 temporal reconciliation status is missing or invalid"
    )
    if reconciliation is not None:
        reconciliation_version = reconciliation.get("version")
        assert (
            isinstance(reconciliation_version, int)
            and not isinstance(reconciliation_version, bool)
            and reconciliation_version == TEMPORAL_RECONCILIATION_VERSION
        ), "schema-5 temporal reconciliation version mismatch"
        reconciliation_generation = reconciliation.get("generation")
        assert (
            isinstance(reconciliation_generation, int)
            and not isinstance(reconciliation_generation, bool)
            and reconciliation_generation in TEMPORAL_RECONCILIATION_GENERATIONS
        ), "schema-5 temporal reconciliation generation is invalid"
        maximum_generation = reconciliation.get("max_generation")
        assert (
            isinstance(maximum_generation, int)
            and not isinstance(maximum_generation, bool)
            and maximum_generation == max(TEMPORAL_RECONCILIATION_GENERATIONS)
        ), "schema-5 temporal reconciliation maximum generation mismatch"
        reconciliation_count_keys = (
            "cells_total",
            "cells_generation_2",
            "cells_generation_3",
            "cells_collecting",
            "cells_awaiting_day_close",
            "cells_sealed",
            "cells_blocked",
            "closing_proofs_total",
            "closing_proofs_valid",
        )
        for key in reconciliation_count_keys:
            assert _nonnegative_integer(reconciliation.get(key)), (
                f"schema-5 temporal reconciliation {key} is invalid"
            )
        assert reconciliation["cells_total"] == (
            reconciliation["cells_collecting"]
            + reconciliation["cells_awaiting_day_close"]
            + reconciliation["cells_sealed"]
            + reconciliation["cells_blocked"]
        ), "schema-5 temporal reconciliation cell arithmetic mismatch"
        assert reconciliation["cells_total"] == (
            reconciliation["cells_generation_2"] + reconciliation["cells_generation_3"]
        ), "schema-5 temporal reconciliation generation arithmetic mismatch"
        assert reconciliation["cells_total"] <= refinement["cells_total"], (
            "schema-5 temporal reconciliation exceeds refinement cells"
        )
        assert reconciliation["cells_sealed"] <= refinement["cells_covered"], (
            "schema-5 temporal reconciliation seal lacks covered cell"
        )
        assert reconciliation["closing_proofs_valid"] <= reconciliation["closing_proofs_total"], (
            "schema-5 temporal reconciliation proof arithmetic mismatch"
        )
        entries = reconciliation.get("entries")
        assert isinstance(entries, list) and len(entries) == reconciliation["cells_total"], (
            "schema-5 temporal reconciliation entries mismatch"
        )
        _assert_temporal_day_close_refreshes(reconciliation)
        states = {
            "collecting_generation": 0,
            "awaiting_day_close": 0,
            "sealed": 0,
            "blocked": 0,
        }
        cell_ids: set[str] = set()
        entry_generations: list[int] = []
        entry_generation_counts = {2: 0, 3: 0}
        minimum_closing_proofs = 0
        for entry in entries:
            assert isinstance(entry, dict), "schema-5 temporal reconciliation entry is invalid"
            cell_id = entry.get("cell_id")
            state = entry.get("state")
            assert isinstance(cell_id, str) and cell_id and cell_id not in cell_ids, (
                "schema-5 temporal reconciliation cell id is invalid"
            )
            assert state in states, "schema-5 temporal reconciliation state is invalid"
            entry_generation = entry.get("generation")
            assert (
                isinstance(entry_generation, int)
                and not isinstance(entry_generation, bool)
                and entry_generation in TEMPORAL_RECONCILIATION_GENERATIONS
            ), "schema-5 temporal reconciliation entry generation is invalid"
            entry_generations.append(entry_generation)
            entry_generation_counts[entry_generation] += 1
            minimum_closing_proofs += entry_generation - 2
            if state == "awaiting_day_close":
                minimum_closing_proofs += 1
            elif state == "sealed":
                minimum_closing_proofs += 2
            if state == "sealed":
                assert cell_id in refined_covered_interval_ids, (
                    "schema-5 sealed temporal reconciliation cell is not a "
                    "refined covered interval"
                )
            elif state == "blocked":
                assert cell_id in refined_blocked_interval_ids, (
                    "schema-5 blocked temporal reconciliation cell is not a "
                    "refined terminal-gap interval"
                )
            cell_ids.add(cell_id)
            states[str(state)] += 1
            baseline_unique = entry.get("baseline_union_unique")
            assert (
                isinstance(baseline_unique, int)
                and not isinstance(baseline_unique, bool)
                and baseline_unique > 0
            ), "schema-5 temporal reconciliation baseline cardinality is invalid"
            baseline_union = _sha256(
                entry.get("baseline_union_sha256"),
                label="schema-5 temporal reconciliation baseline union",
            )
            baseline_bijection = _sha256(
                entry.get("baseline_bijection_sha256"),
                label="schema-5 temporal reconciliation baseline bijection",
            )
            generation_history = entry.get("generation_history")
            assert isinstance(generation_history, list), (
                "schema-5 temporal reconciliation generation history is missing or invalid"
            )
            expected_history_generations = list(range(1, entry_generation))
            actual_history_generations: list[int] = []
            for historical in generation_history:
                assert isinstance(historical, dict), (
                    "schema-5 temporal reconciliation generation history entry is invalid"
                )
                historical_generation = historical.get("generation")
                assert (
                    isinstance(historical_generation, int)
                    and not isinstance(historical_generation, bool)
                    and historical_generation > 0
                ), "schema-5 temporal reconciliation historical generation is invalid"
                actual_history_generations.append(historical_generation)
                historical_unique = historical.get("union_unique")
                assert (
                    isinstance(historical_unique, int)
                    and not isinstance(historical_unique, bool)
                    and historical_unique > 0
                ), "schema-5 temporal reconciliation historical cardinality is invalid"
                _sha256(
                    historical.get("union_sha256"),
                    label="schema-5 temporal reconciliation historical union",
                )
                _sha256(
                    historical.get("bijection_sha256"),
                    label="schema-5 temporal reconciliation historical bijection",
                )
            assert actual_history_generations == expected_history_generations, (
                "schema-5 temporal reconciliation generation history sequence mismatch"
            )
            latest_history = generation_history[-1]
            assert (
                latest_history["union_unique"] == baseline_unique
                and latest_history["union_sha256"] == baseline_union
                and latest_history["bijection_sha256"] == baseline_bijection
            ), "schema-5 temporal reconciliation baseline does not match generation history"
            generation_values = (
                entry.get("generation_union_unique"),
                entry.get("generation_union_sha256"),
                entry.get("generation_bijection_sha256"),
            )
            if state in {"awaiting_day_close", "sealed"}:
                assert (
                    isinstance(generation_values[0], int)
                    and not isinstance(generation_values[0], bool)
                    and generation_values[0] > 0
                ), "schema-5 temporal reconciliation generation cardinality is invalid"
                assert generation_values[0] == baseline_unique, (
                    "schema-5 temporal reconciliation cardinality did not converge"
                )
                assert (
                    _sha256(
                        generation_values[1],
                        label="schema-5 temporal reconciliation generation union",
                    )
                    == baseline_union
                ), "schema-5 temporal reconciliation union did not converge"
                assert (
                    _sha256(
                        generation_values[2],
                        label="schema-5 temporal reconciliation generation bijection",
                    )
                    == baseline_bijection
                ), "schema-5 temporal reconciliation bijection did not converge"
                assert entry.get("failure_reason") is None, (
                    "schema-5 temporal reconciliation reports failure"
                )
            elif state == "collecting_generation":
                assert generation_values == (None, None, None), (
                    "schema-5 collecting reconciliation claims a frozen generation"
                )
                assert entry.get("failure_reason") is None, (
                    "schema-5 collecting reconciliation reports failure"
                )
            else:
                reason = entry.get("failure_reason")
                assert isinstance(reason, str) and reason.strip(), (
                    "schema-5 blocked temporal reconciliation lacks reason"
                )
                assert reason == refined_blocked_interval_reasons.get(cell_id), (
                    "schema-5 blocked temporal reconciliation failure reason "
                    "does not match terminal interval"
                )
                if generation_values != (None, None, None):
                    assert all(value is not None for value in generation_values), (
                        "schema-5 blocked temporal reconciliation has partial "
                        "generation values"
                    )
                    assert (
                        isinstance(generation_values[0], int)
                        and not isinstance(generation_values[0], bool)
                        and generation_values[0] > 0
                    ), "schema-5 blocked temporal reconciliation cardinality is invalid"
                    assert generation_values[0] == baseline_unique, (
                        "schema-5 blocked temporal reconciliation cardinality did not converge"
                    )
                    assert (
                        _sha256(
                            generation_values[1],
                            label="schema-5 blocked temporal reconciliation generation union",
                        )
                        == baseline_union
                    ), "schema-5 blocked temporal reconciliation union did not converge"
                    assert (
                        _sha256(
                            generation_values[2],
                            label=(
                                "schema-5 blocked temporal reconciliation "
                                "generation bijection"
                            ),
                        )
                        == baseline_bijection
                    ), "schema-5 blocked temporal reconciliation bijection did not converge"
        assert states["collecting_generation"] == reconciliation["cells_collecting"], (
            "schema-5 temporal reconciliation has fewer collecting cells"
        )
        assert states["awaiting_day_close"] == reconciliation["cells_awaiting_day_close"], (
            "schema-5 temporal reconciliation has fewer awaiting-day-close cells"
        )
        assert states["sealed"] == reconciliation["cells_sealed"], (
            "schema-5 temporal reconciliation has fewer sealed cells"
        )
        assert states["blocked"] == reconciliation["cells_blocked"], (
            "schema-5 temporal reconciliation has fewer blocked cells"
        )
        assert reconciliation["generation"] == max(entry_generations, default=2), (
            "schema-5 temporal reconciliation generation maximum mismatch"
        )
        assert reconciliation["cells_generation_2"] == entry_generation_counts[2], (
            "schema-5 temporal reconciliation generation-2 count mismatch"
        )
        assert reconciliation["cells_generation_3"] == entry_generation_counts[3], (
            "schema-5 temporal reconciliation generation-3 count mismatch"
        )
        assert reconciliation["closing_proofs_total"] >= minimum_closing_proofs, (
            "schema-5 temporal reconciliation has fewer closing proofs than "
            "its cell generations and states require"
        )
        if reconciliation["cells_total"] == 0:
            assert reconciliation["closing_proofs_total"] == 0, (
                "schema-5 temporal reconciliation has orphan closing proofs"
            )
        if refinement["raw_replay_valid"]:
            assert (
                reconciliation["closing_proofs_valid"] == reconciliation["closing_proofs_total"]
            ), "schema-5 temporal reconciliation has invalid closing proofs"

    if coverage_complete or cycle_terminal:
        assert refinement["cells_refining"] == 0, (
            "schema-5 terminal refinement still has refining cells"
        )
        assert refinement["cells_blocked"] == 0, (
            "schema-5 terminal refinement still has blocked cells"
        )
        assert refinement["nodes_pending"] == 0, (
            "schema-5 terminal refinement still has pending nodes"
        )
        assert refinement["nodes_pending_page2"] == 0, (
            "schema-5 terminal refinement still has pending page-2 nodes"
        )
        assert refinement["nodes_blocked"] == 0, (
            "schema-5 terminal refinement still has blocked nodes"
        )
        assert refinement["nodes_exact"] == refinement["nodes_total"], (
            "schema-5 terminal refinement has non-exact nodes"
        )
        assert taxonomy["raw_replay_valid"] is True, (
            "schema-5 terminal refinement taxonomy RAW replay is invalid"
        )
        assert refinement["raw_replay_valid"] is True, (
            "schema-5 terminal refinement RAW replay is invalid"
        )
        assert refinement["raw_replay_error_count"] == 0, (
            "schema-5 terminal refinement has RAW replay errors"
        )
        assert refinement["identity_conflict_count"] == 0, (
            "schema-5 terminal refinement has identity conflicts"
        )
        assert refinement["overlap_count"] == 0, (
            "schema-5 terminal refinement has overlap conflicts"
        )
        assert refinement["cells_covered"] == refinement["cells_total"], (
            "schema-5 terminal refinement has uncovered cells"
        )
        assert refinement["seals_total"] == refinement["cells_total"], (
            "schema-5 terminal refinement has missing seals"
        )
        assert refinement["seals_valid"] == refinement["seals_total"], (
            "schema-5 terminal refinement has invalid seals"
        )
        if reconciliation is not None:
            assert reconciliation["cells_collecting"] == 0, (
                "schema-5 terminal temporal reconciliation is still collecting"
            )
            assert reconciliation["cells_awaiting_day_close"] == 0, (
                "schema-5 terminal temporal reconciliation awaits day close"
            )
            assert reconciliation["cells_blocked"] == 0, (
                "schema-5 terminal temporal reconciliation is blocked"
            )
            assert reconciliation["cells_sealed"] == reconciliation["cells_total"], (
                "schema-5 terminal temporal reconciliation cells_sealed mismatch"
            )
