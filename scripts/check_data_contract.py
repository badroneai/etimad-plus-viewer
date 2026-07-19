"""Fail closed when a Kashaf static snapshot is incomplete or unsafe to publish."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import quote
from urllib.error import URLError
from urllib.request import Request, urlopen

from export_warehouse import (
    AWARDED_INDEX_PART_ALGORITHM,
    AWARDED_INDEX_PART_COUNT,
    AWARDED_INDEX_PART_FORMAT_VERSION,
    OFFICIAL_REGION_LABELS,
    SCHEMA_VERSION,
    SHARD_COUNT,
    awarded_index_part_config,
    index_part_for_ref,
    parse_iso_datetime,
    shard_for_ref,
    to_halalas,
)


LFS_HEADER = b"version https://git-lfs.github.com/spec/v1"
AWARDED_INDEX_DESCRIPTOR_MAX_BYTES = 1024 * 1024
AWARDED_INDEX_PART_MAX_BYTES = 5 * 1024 * 1024
OFFICIAL_COMPONENT_SOURCE_ID = "etimad_official_components"
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}", re.IGNORECASE)


def assert_awarded_lifecycle_contract(
    records: list[dict],
    *,
    as_of: str,
) -> None:
    """Fail closed on an awarded row that is still future and has no amount."""
    classified_at = parse_iso_datetime(as_of)
    assert classified_at is not None, "manifest as_of is missing or invalid"
    for row in records:
        deadline = parse_iso_datetime(row.get("deadline"), date_end_of_day=True)
        if (
            row.get("winAmount") in (None, "")
            and deadline is not None
            and deadline >= classified_at
        ):
            raise AssertionError(
                "awarded row has null amount and future deadline: "
                f"{row.get('ref')} deadline={deadline.isoformat()} as_of={classified_at.isoformat()}"
            )


def assert_awarded_index_asset_size(name: str, size: int) -> None:
    """Enforce growth ceilings on the descriptor and each searchable part."""
    if name == "awarded_index.json":
        assert size < AWARDED_INDEX_DESCRIPTOR_MAX_BYTES, (
            "awarded index descriptor exceeds 1 MiB"
        )
    elif name.startswith("awarded_index_parts/") and name.endswith(".json"):
        assert size < AWARDED_INDEX_PART_MAX_BYTES, (
            f"awarded index part exceeds 5 MiB: {name}"
        )


def validate_awarded_index_descriptor(index: dict) -> dict[str, dict]:
    """Validate and index the small awarded searchable-index descriptor."""
    assert isinstance(index, dict), "awarded index descriptor missing"
    meta = index.get("meta") or {}
    assert meta.get("schemaVersion") == SCHEMA_VERSION, (
        "awarded index descriptor schema mismatch"
    )
    assert meta.get("dataset") == "awarded", "awarded index dataset mismatch"
    assert meta.get("detailShards") == SHARD_COUNT, (
        "awarded index detail shard count mismatch"
    )
    assert meta.get("indexParts") == awarded_index_part_config(), (
        "awarded index part config mismatch"
    )
    assert isinstance(index.get("count"), int) and index["count"] >= 0, (
        "awarded index total count missing"
    )
    parts = index.get("parts")
    assert isinstance(parts, list), "awarded index parts missing"
    assert len(parts) == AWARDED_INDEX_PART_COUNT, "awarded index part count mismatch"

    by_file: dict[str, dict] = {}
    expected_ids = {f"{part:02d}" for part in range(AWARDED_INDEX_PART_COUNT)}
    seen_ids: set[str] = set()
    for entry in parts:
        assert isinstance(entry, dict), "invalid awarded index part descriptor"
        part = str(entry.get("part") or "")
        filename = str(entry.get("file") or "")
        expected_file = f"awarded_index_parts/{part}.json"
        assert part in expected_ids, f"invalid awarded index part id: {part}"
        assert part not in seen_ids, f"duplicate awarded index part id: {part}"
        assert filename == expected_file, f"awarded index part path mismatch: {part}"
        assert isinstance(entry.get("count"), int) and entry["count"] >= 0, (
            f"awarded index part count missing: {part}"
        )
        assert isinstance(entry.get("bytes"), int) and entry["bytes"] > 0, (
            f"awarded index part byte count missing: {part}"
        )
        assert isinstance(entry.get("sha256"), str) and len(entry["sha256"]) == 64, (
            f"awarded index part SHA-256 missing: {part}"
        )
        seen_ids.add(part)
        by_file[filename] = entry
    assert seen_ids == expected_ids, "awarded index descriptor does not cover every part"
    return by_file


def validate_awarded_index_part(
    name: str,
    payload: dict,
    *,
    as_of: str,
) -> dict[str, str]:
    """Validate one deterministic part and return ref -> detail-shard lookups."""
    part = Path(name).stem
    assert name == f"awarded_index_parts/{part}.json", (
        f"invalid awarded index part path: {name}"
    )
    assert part.isdigit() and 0 <= int(part) < AWARDED_INDEX_PART_COUNT, (
        f"invalid awarded index part id: {part}"
    )
    meta = payload.get("meta") or {}
    assert meta.get("schemaVersion") == SCHEMA_VERSION, (
        f"awarded index part schema mismatch: {part}"
    )
    assert meta.get("dataset") == "awarded_index_part", (
        f"awarded index part dataset mismatch: {part}"
    )
    assert meta.get("part") == part, f"awarded index part marker mismatch: {part}"
    assert meta.get("partCount") == AWARDED_INDEX_PART_COUNT, (
        f"awarded index part count marker mismatch: {part}"
    )
    assert meta.get("formatVersion") == AWARDED_INDEX_PART_FORMAT_VERSION, (
        f"awarded index part format mismatch: {part}"
    )
    assert meta.get("algorithm") == AWARDED_INDEX_PART_ALGORITHM, (
        f"awarded index part algorithm mismatch: {part}"
    )
    records = payload.get("records")
    assert isinstance(records, list), f"awarded index part records missing: {part}"
    assert payload.get("count") == len(records), (
        f"awarded index part internal count mismatch: {part}"
    )
    assert_awarded_lifecycle_contract(records, as_of=as_of)

    refs: dict[str, str] = {}
    previous_ref: str | None = None
    for row in records:
        ref = str(row["ref"])
        assert ref not in refs, f"duplicate awarded index ref in part {part}: {ref}"
        assert index_part_for_ref(ref) == int(part), (
            f"awarded index row in wrong part: {ref}"
        )
        expected_detail_shard = f"{shard_for_ref(ref):02d}"
        assert row.get("_detailShard") == expected_detail_shard, (
            f"awarded index detail shard mismatch: {ref}"
        )
        if previous_ref is not None:
            assert previous_ref < ref, f"awarded index part is not sorted: {part}"
        previous_ref = ref
        refs[ref] = expected_detail_shard
    return refs


def _nonnegative_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _percentage(value: object, *, label: str) -> float:
    assert isinstance(value, (int, float)) and not isinstance(value, bool), (
        f"{label} must be numeric"
    )
    result = float(value)
    assert 0.0 <= result <= 100.0, f"{label} is outside 0..100"
    return result


def _assert_percentage(value: object, expected: float, *, label: str) -> None:
    actual = _percentage(value, label=label)
    assert abs(actual - round(expected, 6)) <= 0.000001, (
        f"{label} arithmetic mismatch: {actual} != {round(expected, 6)}"
    )


def assert_active_scan_progress_contract(progress: object) -> None:
    """Validate the resumable active-scan counter arithmetic."""
    assert isinstance(progress, dict), "fetch status active_scan is missing"
    if progress.get("available") is False:
        assert progress.get("reason") == "official_database_metadata_absent", (
            "legacy active_scan absence reason is not explicit"
        )
        return

    for key in (
        "denominator",
        "targets_scanned_unique",
        "targets_resolved_unique",
        "targets_absent_after_full_pass",
        "targets_remaining",
    ):
        assert _nonnegative_integer(progress.get(key)), f"active_scan {key} is invalid"
    denominator = progress["denominator"]
    scanned = progress["targets_scanned_unique"]
    resolved = progress["targets_resolved_unique"]
    absent = progress["targets_absent_after_full_pass"]
    remaining = progress["targets_remaining"]
    assert scanned <= denominator, "active_scan scanned exceeds denominator"
    assert resolved == scanned + absent, "active_scan resolution arithmetic mismatch"
    assert resolved <= denominator, "active_scan resolved exceeds denominator"
    assert remaining == denominator - resolved, "active_scan remaining arithmetic mismatch"
    assert progress.get("absence_confirmation_passes") == 2, (
        "active_scan absence confirmation policy mismatch"
    )
    expected_scanned = (scanned * 100.0 / denominator) if denominator else 0.0
    _assert_percentage(
        progress.get("scanned_percent"),
        expected_scanned,
        label="active_scan scanned_percent",
    )
    expected_coverage = (resolved * 100.0 / denominator) if denominator else 0.0
    _assert_percentage(
        progress.get("coverage_percent"),
        expected_coverage,
        label="active_scan coverage_percent",
    )
    assert isinstance(progress.get("complete"), bool), "active_scan complete flag is invalid"
    if progress["complete"]:
        assert remaining == 0, "active_scan is complete with remaining targets"
    date_fallback = progress.get("date_fallback")
    if date_fallback is not None:
        assert_active_date_scan_contract(date_fallback)
        assert isinstance(date_fallback, dict)
        assert date_fallback["target_count"] == denominator, (
            "active date scan target cohort differs from active_scan"
        )
        assert date_fallback["targets_observed_unique"] == scanned, (
            "active date scan observed count differs from active_scan"
        )
        assert date_fallback["targets_resolved_unique"] == resolved, (
            "active date scan resolved count differs from active_scan"
        )
        assert date_fallback["targets_absent_after_full_partitions"] == absent, (
            "active date scan absence count differs from active_scan"
        )
        assert (
            isinstance(progress.get("cycle_id"), str)
            and progress["cycle_id"]
            and date_fallback.get("cycle_id") == progress["cycle_id"]
        ), "active date scan cycle differs from active_scan"
    awaiting_date_authority = bool(
        isinstance(date_fallback, dict)
        and not date_fallback.get("completion_authoritative", False)
    )
    if progress["complete"]:
        assert not awaiting_date_authority, (
            "active_scan completed before date partition authority"
        )
    if denominator and remaining == 0 and not awaiting_date_authority:
        assert progress["complete"], "active_scan reached full coverage without completion"


def assert_active_date_scan_contract(progress: object) -> None:
    """Validate the exhaustive date-partition evidence nested in active scan."""

    assert isinstance(progress, dict), "active date scan progress is invalid"
    for key in (
        "target_count",
        "targets_observed_unique",
        "targets_resolved_unique",
        "targets_absent_after_full_partitions",
        "ranges_total",
        "ranges_pending",
        "ranges_blocked_single_day",
        "official_active_scanned_unique",
        "partition_duplicate_records",
        "leaf_integrity_error_count",
        "range_geometry_error_count",
        "convergence_passes",
    ):
        assert _nonnegative_integer(progress.get(key)), (
            f"active date scan {key} is invalid"
        )
    target_count = progress["target_count"]
    observed = progress["targets_observed_unique"]
    resolved = progress["targets_resolved_unique"]
    absent = progress["targets_absent_after_full_partitions"]
    assert observed <= resolved <= target_count, (
        "active date scan target arithmetic mismatch"
    )
    assert absent <= resolved and resolved == observed + absent, (
        "active date scan observed/absence arithmetic mismatch"
    )
    generation_value = progress.get("generation")
    assert (
        isinstance(generation_value, int)
        and not isinstance(generation_value, bool)
        and generation_value >= 1
    ), (
        "active date scan generation is invalid"
    )
    generation = generation_value
    convergence_last_generation = progress.get("convergence_last_generation")
    assert convergence_last_generation is None or (
        _nonnegative_integer(convergence_last_generation)
        and 1 <= convergence_last_generation <= generation
    ), "active date scan convergence generation is invalid"
    assert progress["convergence_passes"] <= generation, (
        "active date scan convergence exceeds distinct generations"
    )
    expected_target_percent = (
        observed * 100.0 / target_count if target_count else 0.0
    )
    _assert_percentage(
        progress.get("targets_observed_percent"),
        expected_target_percent,
        label="active date scan targets_observed_percent",
    )

    official_total = progress.get("root_filtered_total")
    scanned = progress["official_active_scanned_unique"]
    if official_total is None:
        assert scanned == 0, "active date scan has rows before its root total"
        expected_scan_percent = 0.0
    else:
        assert _nonnegative_integer(official_total), (
            "active date scan root total is invalid"
        )
        assert scanned <= official_total, (
            "active date scan exceeds its official root total"
        )
        expected_scan_percent = (
            scanned * 100.0 / official_total
            if official_total
            else 100.0
            if progress["partition_authoritative"]
            else 0.0
        )
    _assert_percentage(
        progress.get("official_active_scanned_percent"),
        expected_scan_percent,
        label="active date scan official_active_scanned_percent",
    )

    for key in (
        "domain_matches_unfiltered_boundary",
        "partition_authoritative",
        "absence_authoritative",
        "completion_authoritative",
        "closing_boundary_matches",
        "convergence_matches_current_union",
    ):
        assert isinstance(progress.get(key), bool), (
            f"active date scan {key} is invalid"
        )
    if progress["partition_authoritative"]:
        assert progress["domain_matches_unfiltered_boundary"], (
            "active date partition lacks an unfiltered boundary proof"
        )
        assert progress["ranges_pending"] == 0, (
            "authoritative active date partition still has pending ranges"
        )
        assert progress["ranges_blocked_single_day"] == 0, (
            "authoritative active date partition has blocked ranges"
        )
        assert progress["partition_duplicate_records"] == 0, (
            "authoritative active date partition has duplicate records"
        )
        assert progress["leaf_integrity_error_count"] == 0, (
            "authoritative active date partition has invalid leaves"
        )
        assert progress["range_geometry_error_count"] == 0, (
            "authoritative active date partition has a range gap or overlap"
        )
        assert official_total is not None and scanned == official_total, (
            "authoritative active date partition is incomplete"
        )
        assert progress["closing_boundary_matches"], (
            "authoritative active date partition lacks a stable closing boundary"
        )
        assert progress["convergence_passes"] >= 2, (
            "authoritative active date partition lacks two converged generations"
        )
        assert convergence_last_generation == generation, (
            "authoritative active date partition closing generation is stale"
        )
        assert progress["convergence_matches_current_union"], (
            "authoritative active date partition union did not converge"
        )
    assert not progress["absence_authoritative"] or (
        progress["partition_authoritative"]
        and progress["targets_absent_after_full_partitions"] > 0
    ), "active date absence lacks partition authority"
    expected_completion_authority = bool(
        progress["partition_authoritative"] and resolved == target_count
    )
    assert progress["completion_authoritative"] == expected_completion_authority, (
        "active date completion authority arithmetic mismatch"
    )
    if progress["completion_authoritative"]:
        assert progress["partition_authoritative"], (
            "active date completion lacks partition authority"
        )


def assert_region_backfill_contract(
    progress: object,
    index_by_ref: dict[str, dict],
    detail_by_ref: dict[str, dict],
) -> None:
    """Validate region math and every official region's provenance and evidence."""
    assert isinstance(progress, dict), "fetch status region_backfill is missing"
    if progress.get("available") is False:
        assert progress.get("reason") == "official_database_metadata_absent", (
            "legacy region_backfill absence reason is not explicit"
        )
        return

    counter_keys = (
        "awarded_total",
        "initial_filled",
        "initial_missing",
        "backfilled_unique",
        "current_filled",
        "remaining",
    )
    for key in counter_keys:
        assert _nonnegative_integer(progress.get(key)), f"region_backfill {key} is invalid"
    awarded_total = progress["awarded_total"]
    initial_filled = progress["initial_filled"]
    initial_missing = progress["initial_missing"]
    backfilled_unique = progress["backfilled_unique"]
    current_filled = progress["current_filled"]
    remaining = progress["remaining"]
    assert initial_filled + initial_missing == awarded_total, (
        "region_backfill initial arithmetic mismatch"
    )
    assert backfilled_unique <= initial_missing, (
        "region_backfill unique count exceeds initial gap"
    )
    assert current_filled == initial_filled + backfilled_unique, (
        "region_backfill current arithmetic mismatch"
    )
    assert remaining == initial_missing - backfilled_unique, (
        "region_backfill remaining arithmetic mismatch"
    )
    expected_backfill = (
        backfilled_unique * 100.0 / initial_missing if initial_missing else 100.0
    )
    expected_overall = current_filled * 100.0 / awarded_total if awarded_total else 100.0
    _assert_percentage(
        progress.get("backfill_percent"),
        expected_backfill,
        label="region_backfill backfill_percent",
    )
    _assert_percentage(
        progress.get("overall_fill_percent"),
        expected_overall,
        label="region_backfill overall_fill_percent",
    )

    assert index_by_ref.keys() == detail_by_ref.keys(), (
        "region contract index/detail ref set mismatch"
    )
    filled_details = 0
    evidence_backed_official_regions = 0
    for ref, detail in detail_by_ref.items():
        index_region = str(index_by_ref[ref].get("region") or "").strip()
        detail_region = str(detail.get("region") or "").strip()
        assert index_region == detail_region, f"awarded index/detail region mismatch: {ref}"
        if detail_region:
            filled_details += 1
        provenance = detail.get("_provenance") or {}
        field_sources = provenance.get("fieldSources") or {}
        if field_sources.get("region") != OFFICIAL_COMPONENT_SOURCE_ID:
            continue
        evidence_backed_official_regions += 1
        assert detail_region, f"official region provenance has a blank value: {ref}"
        region_labels = [label.strip() for label in detail_region.split("،")]
        assert region_labels and all(
            label in OFFICIAL_REGION_LABELS for label in region_labels
        ), f"official region is outside the parser vocabulary: {ref}"
        assert detail_region == "، ".join(region_labels), (
            f"official multi-region value is not canonical: {ref}"
        )
        sources = provenance.get("sources") or []
        assert any(
            isinstance(source, dict)
            and source.get("id") == OFFICIAL_COMPONENT_SOURCE_ID
            for source in sources
        ), f"official region source marker missing: {ref}"
        relations = (detail.get("_evidence") or {}).get("relations") or {}
        raw_path = str(relations.get("rawPath") or "").strip()
        sha256 = str(relations.get("sha256") or "").strip()
        parser_version = relations.get("parserVersion")
        assert raw_path, f"official region rawPath missing: {ref}"
        assert SHA256_PATTERN.fullmatch(sha256), f"official region SHA-256 invalid: {ref}"
        assert (
            isinstance(parser_version, int)
            and not isinstance(parser_version, bool)
            and parser_version >= 1
        ), (
            f"official region parserVersion invalid: {ref}"
        )
        relations_checked_at = str(
            (detail.get("_freshness") or {}).get("relationsCheckedAt") or ""
        ).strip()
        assert relations_checked_at, f"official region freshness missing: {ref}"

    assert len(detail_by_ref) == awarded_total, (
        "published awarded dataset disagrees with region backfill cohort"
    )
    assert filled_details == current_filled, (
        "published awarded region count disagrees with region_backfill current_filled"
    )
    assert evidence_backed_official_regions >= backfilled_unique, (
        "published evidence-backed official regions trail region_backfill progress"
    )


def load_asset(path: Path):
    raw = path.read_bytes()
    if raw.startswith(LFS_HEADER):
        raise AssertionError(f"Git LFS pointer found: {path}")
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except Exception as error:
        raise AssertionError(f"invalid JSON: {path}: {error}") from error
    return raw, parsed


def check(root: Path, expected_snapshot_id: str | None = None) -> dict[str, int]:
    data = root / "data"
    _, manifest = load_asset(data / "manifest.json")
    assert manifest.get("schema") == "kashaf.static-warehouse", "unknown manifest schema"
    assert manifest.get("schema_version") == SCHEMA_VERSION, "manifest schema version mismatch"
    assert manifest.get("snapshot_id"), "snapshot_id missing"
    if expected_snapshot_id:
        assert manifest["snapshot_id"] == expected_snapshot_id, "snapshot_id mismatch"

    assets = manifest.get("assets") or {}
    assert assets, "manifest assets are empty"
    parsed_assets = {}
    for name, expected in assets.items():
        path = data / name
        assert path.is_file(), f"asset missing: {name}"
        raw, parsed = load_asset(path)
        assert len(raw) == expected.get("bytes"), f"byte count mismatch: {name}"
        assert hashlib.sha256(raw).hexdigest() == expected.get("sha256"), f"SHA-256 mismatch: {name}"
        if "records" in expected:
            assert isinstance(parsed, dict) and isinstance(parsed.get("records"), list), f"records missing: {name}"
            assert parsed.get("count") == len(parsed["records"]), f"internal count mismatch: {name}"
            assert expected["records"] == len(parsed["records"]), f"manifest count mismatch: {name}"
        parsed_assets[name] = parsed

    for dataset in manifest.get("datasets") or []:
        file_name = dataset["file"]
        assert file_name in parsed_assets, f"dataset asset not checksummed: {file_name}"
        parsed = parsed_assets[file_name]
        if dataset.get("count") is not None:
            actual = parsed.get("count") if isinstance(parsed, dict) else None
            assert actual == dataset["count"], f"dataset count mismatch: {dataset['id']}"

    datasets_by_id = {
        item["id"]: item for item in manifest.get("datasets") or []
    }
    for required in (
        "open",
        "within_7",
        "within_30",
        "awarding",
        "examination",
        "cancelled",
        "unknown",
        "awarded",
    ):
        assert required in datasets_by_id, f"canonical lifecycle dataset missing: {required}"
    completeness = manifest.get("completeness") or {}
    assert completeness.get("officialUniverseComplete") is False, (
        "official universe must not be claimed complete"
    )
    awarded_truth = completeness.get("phase0Awarded") or {}
    assert isinstance(awarded_truth.get("partial"), bool), "awarded partial truth missing"
    assert awarded_truth.get("validatedBy"), "awarded completeness has no trusted proof"
    assert datasets_by_id["awarded"].get("partial", False) == awarded_truth["partial"], (
        "awarded dataset partial flag disagrees with completeness truth"
    )
    assert datasets_by_id["awarded"].get("indexParts") == awarded_index_part_config(), (
        "awarded dataset index part config mismatch"
    )
    assert datasets_by_id["awarded"].get("detailShards") == {
        "count": SHARD_COUNT,
        "pathTemplate": "awarded_details/{shard}.json",
        "algorithm": "sha256_first_byte_mod_64",
    }, "awarded dataset detail shard config mismatch"
    awarded_index_meta = (parsed_assets.get("awarded_index.json") or {}).get("meta") or {}
    assert awarded_index_meta.get("partial") == awarded_truth["partial"], (
        "awarded index partial flag disagrees with completeness truth"
    )
    freshness_basis = completeness.get("phase0FreshnessBasis")
    assert freshness_basis != "imported_at_legacy_schema_fallback", (
        "Phase-0 source freshness is still represented by import time"
    )

    open_asset = parsed_assets["open.json"]
    assert open_asset.get("meta", {}).get("partial") is True, "open coverage must be partial"
    assert open_asset.get("meta", {}).get("coverageComplete") is False, (
        "open coverage must explicitly deny completeness"
    )
    assert datasets_by_id["open"].get("partial") is True, (
        "open dataset must be labelled within partial coverage"
    )
    expected_categories = {
        "open": "open",
        "awarding": "awarding",
        "examination": "examination",
        "cancelled": "cancelled",
        "unknown": "unknown",
    }
    for dataset_id, category in expected_categories.items():
        asset = parsed_assets[datasets_by_id[dataset_id]["file"]]
        assert asset.get("meta", {}).get("partial") is True, (
            f"lifecycle coverage must be partial: {dataset_id}"
        )
        assert asset.get("meta", {}).get("coverageComplete") is False, (
            f"lifecycle coverage must deny completeness: {dataset_id}"
        )
        assert datasets_by_id[dataset_id].get("partial") is True, (
            f"lifecycle dataset must be labelled partial: {dataset_id}"
        )
        assert all(
            row.get("tenderCategory") == category for row in asset.get("records") or []
        ), f"canonical tenderCategory mismatch: {dataset_id}"
    for dataset_id, max_hours in (("within_7", 7 * 24), ("within_30", 30 * 24)):
        asset = parsed_assets[datasets_by_id[dataset_id]["file"]]
        assert asset.get("meta", {}).get("partial") is True, (
            f"deadline-window coverage must be partial: {dataset_id}"
        )
        assert asset.get("meta", {}).get("coverageComplete") is False, (
            f"deadline-window coverage must deny completeness: {dataset_id}"
        )
        assert all(
            row.get("tenderCategory") == "open"
            and isinstance(row.get("deadlineWindowHours"), (int, float))
            and 0 <= row["deadlineWindowHours"] <= max_hours
            for row in asset.get("records") or []
        ), f"deadline window is not canonically derived: {dataset_id}"

    fetch_status = parsed_assets.get("fetch_status.json") or {}
    projection = fetch_status.get("canonical_projection") or {}
    assert projection.get("completeness") == completeness, (
        "fetch status completeness disagrees with manifest"
    )
    assert projection.get("lifecycle") == manifest.get("lifecycle"), (
        "fetch status lifecycle disagrees with manifest"
    )

    assert not (data / "awarded.json").exists(), "legacy monolithic awarded.json must not exist"
    attributes = (root / ".gitattributes").read_text(encoding="utf-8") if (root / ".gitattributes").exists() else ""
    assert not (
        "data/awarded.json" in attributes and "filter=lfs" in attributes
    ), "awarded data is still configured for Git LFS"

    index = parsed_assets.get("awarded_index.json")
    assert isinstance(index, dict), "awarded index descriptor missing"
    part_descriptors = validate_awarded_index_descriptor(index)
    for volatile in ("generatedAt", "sourceTimes", "exportedAt", "exported_at"):
        assert volatile not in (index.get("meta") or {}), f"volatile awarded index meta: {volatile}"
    assert_awarded_index_asset_size(
        "awarded_index.json",
        (data / "awarded_index.json").stat().st_size,
    )
    assert "records" not in (assets.get("awarded_index.json") or {}), (
        "awarded index descriptor must not claim inline records"
    )

    index_by_ref: dict[str, dict] = {}
    expected_part_files = set(part_descriptors)
    actual_part_files = {
        path.relative_to(data).as_posix()
        for path in (data / "awarded_index_parts").glob("*.json")
    }
    assert actual_part_files == expected_part_files, (
        "stale or missing awarded index part files"
    )
    for name, descriptor in part_descriptors.items():
        assert name in parsed_assets, f"awarded index part absent from manifest: {name}"
        manifest_descriptor = assets.get(name) or {}
        assert descriptor["bytes"] == manifest_descriptor.get("bytes"), (
            f"awarded index part byte descriptor mismatch: {name}"
        )
        assert descriptor["sha256"] == manifest_descriptor.get("sha256"), (
            f"awarded index part SHA descriptor mismatch: {name}"
        )
        assert descriptor["count"] == manifest_descriptor.get("records"), (
            f"awarded index part count descriptor mismatch: {name}"
        )
        assert manifest_descriptor.get("role") == "awarded_search_index_part", (
            f"awarded index part role mismatch: {name}"
        )
        assert_awarded_index_asset_size(name, (data / name).stat().st_size)
        payload = parsed_assets[name]
        for volatile in ("generatedAt", "sourceTimes", "exportedAt", "exported_at"):
            assert volatile not in (payload.get("meta") or {}), (
                f"volatile awarded index part meta: {name}:{volatile}"
            )
        refs = validate_awarded_index_part(
            name,
            payload,
            as_of=str(manifest.get("as_of") or ""),
        )
        overlap = set(index_by_ref) & set(refs)
        assert not overlap, f"duplicate refs across awarded index parts: {sorted(overlap)[:3]}"
        rows_by_ref = {str(row["ref"]): row for row in payload["records"]}
        for ref, detail_shard in refs.items():
            index_by_ref[ref] = {
                "_detailShard": detail_shard,
                "region": rows_by_ref[ref].get("region"),
            }
    assert len(index_by_ref) == index["count"], "awarded index union/count mismatch"

    detail_by_ref = {}
    for shard in range(SHARD_COUNT):
        name = f"awarded_details/{shard:02d}.json"
        assert name in parsed_assets, f"detail shard absent from manifest: {name}"
        assert (data / name).stat().st_size < 5 * 1024 * 1024, f"detail shard exceeds 5 MiB: {name}"
        for volatile in ("generatedAt", "sourceTimes", "exportedAt", "exported_at"):
            assert volatile not in (parsed_assets[name].get("meta") or {}), f"volatile shard meta: {name}:{volatile}"
        for row in parsed_assets[name]["records"]:
            ref = str(row["ref"])
            assert ref not in detail_by_ref, f"duplicate detail ref: {ref}"
            assert shard_for_ref(ref) == shard, f"detail in wrong shard: {ref}"
            assert row.get("_detailShard") == f"{shard:02d}", f"detail shard marker mismatch: {ref}"
            assert "lifecycleClassifiedAt" not in (row.get("_freshness") or {}), (
                f"awarded shard contains volatile lifecycle timestamp: {ref}"
            )
            expected_win = to_halalas(row.get("winAmount"))
            if expected_win is not None:
                assert row.get("winAmountHalalas") == expected_win, f"win halalas mismatch: {ref}"
                assert row.get("currency") == "SAR", f"currency missing: {ref}"
            for field in ("winners", "allBids"):
                for offer in row.get(field) or []:
                    if not isinstance(offer, dict):
                        continue
                    for legacy, exact in (("bid", "bidHalalas"), ("award", "awardHalalas")):
                        expected_offer = to_halalas(offer.get(legacy))
                        if expected_offer is not None:
                            assert offer.get(exact) == expected_offer, f"{exact} mismatch: {ref}"
                            assert offer.get("currency") == "SAR", f"offer currency missing: {ref}"
            consistency = row.get("moneyConsistency") or {}
            if expected_win is not None:
                winner_values = [
                    offer["awardHalalas"]
                    for offer in row.get("winners") or []
                    if isinstance(offer, dict) and offer.get("awardHalalas") is not None
                ]
                winner_sum = sum(winner_values)
                if winner_values:
                    delta = winner_sum - expected_win
                    assert consistency.get("deltaHalalas") == delta, f"money delta mismatch: {ref}"
                    assert consistency.get("status") == (
                        "match" if delta == 0 else "mismatch"
                    ), f"money consistency mismatch: {ref}"
            detail_by_ref[ref] = row

    assert index_by_ref.keys() == detail_by_ref.keys(), "awarded index/detail ref set mismatch"
    for ref, row in index_by_ref.items():
        expected = f"{shard_for_ref(ref):02d}"
        assert row.get("_detailShard") == expected, f"index shard lookup mismatch: {ref}"

    assert_active_scan_progress_contract(fetch_status.get("active_scan"))
    assert_region_backfill_contract(
        fetch_status.get("region_backfill"),
        index_by_ref,
        detail_by_ref,
    )

    return {
        "assets": len(assets),
        "awarded": len(index_by_ref),
        "shards": SHARD_COUNT,
    }


def fetch_remote_asset(
    base_url: str,
    name: str,
    expected: dict,
    *,
    cache_key: str,
    awarded_as_of: str | None = None,
) -> dict:
    path = Path(name)
    if path.is_absolute() or ".." in path.parts:
        raise AssertionError(f"unsafe asset path in remote manifest: {name}")
    url = f"{base_url.rstrip('/')}/data/{quote(name, safe='/')}?snapshot-check={cache_key}"
    request = Request(
        url,
        headers={"Cache-Control": "no-cache", "User-Agent": "kashaf-contract-check/3"},
    )
    with urlopen(request, timeout=60) as response:
        raw = response.read()
    if raw.startswith(LFS_HEADER):
        raise AssertionError(f"remote asset is a Git LFS pointer: {name}")
    assert_awarded_index_asset_size(name, len(raw))
    if len(raw) != expected.get("bytes"):
        raise AssertionError(
            f"remote byte count mismatch: {name}: {len(raw)} != {expected.get('bytes')}"
        )
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected.get("sha256"):
        raise AssertionError(
            f"remote SHA-256 mismatch: {name}: {digest} != {expected.get('sha256')}"
        )
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except Exception as error:
        raise AssertionError(f"remote asset is not valid JSON: {name}: {error}") from error

    count = None
    if "records" in expected:
        if not isinstance(parsed, dict) or not isinstance(parsed.get("records"), list):
            raise AssertionError(f"remote records missing: {name}")
        count = len(parsed["records"])
        if parsed.get("count") != count or expected["records"] != count:
            raise AssertionError(f"remote record count mismatch: {name}")

    result: dict = {
        "name": name,
        "count": count,
        "signature": (expected.get("bytes"), expected.get("sha256")),
    }
    if name == "awarded_index.json":
        result["count"] = parsed.get("count")
        result["index_descriptor"] = parsed
    elif name.startswith("awarded_index_parts/") and name.endswith(".json"):
        assert awarded_as_of is not None, "remote manifest as_of is missing"
        result["index_refs"] = validate_awarded_index_part(
            name,
            parsed,
            as_of=awarded_as_of,
        )
    elif name.startswith("awarded_details/") and name.endswith(".json"):
        shard = Path(name).stem
        refs = set()
        for row in parsed.get("records") or []:
            ref = str(row["ref"])
            if ref in refs:
                raise AssertionError(f"duplicate remote awarded detail ref: {ref}")
            if f"{shard_for_ref(ref):02d}" != shard or row.get("_detailShard") != shard:
                raise AssertionError(f"remote awarded detail shard mismatch: {ref}")
            refs.add(ref)
        result["detail_refs"] = refs
    return result


def verify_remote_assets(
    base_url: str,
    manifest: dict,
    *,
    cache_key: str,
    verified: dict[str, dict] | None = None,
) -> dict[str, dict]:
    assets = manifest.get("assets") or {}
    if not assets:
        raise AssertionError("remote manifest assets are empty")
    results = verified if verified is not None else {}
    pending = {
        name: expected
        for name, expected in assets.items()
        if (results.get(name) or {}).get("signature")
        != (expected.get("bytes"), expected.get("sha256"))
    }
    errors: list[str] = []
    awarded_as_of = str(manifest.get("as_of") or "")
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(pending)))) as executor:
        futures = {
            executor.submit(
                fetch_remote_asset,
                base_url,
                name,
                expected,
                cache_key=cache_key,
                awarded_as_of=awarded_as_of,
            ): name
            for name, expected in pending.items()
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception as error:
                results.pop(name, None)
                errors.append(f"{name}: {error}")
    if errors:
        preview = "; ".join(errors[:5])
        raise AssertionError(
            f"remote asset verification failed ({len(errors)} pending): {preview}"
        )

    for dataset in manifest.get("datasets") or []:
        file_name = dataset["file"]
        if file_name not in results:
            raise AssertionError(f"remote dataset asset not checksummed: {file_name}")
        if dataset.get("count") is not None and results[file_name].get("count") != dataset["count"]:
            raise AssertionError(f"remote dataset count mismatch: {dataset['id']}")
        if dataset.get("id") == "awarded" and dataset.get("indexParts") != awarded_index_part_config():
            raise AssertionError("remote awarded dataset index part config mismatch")

    index_result = results.get("awarded_index.json") or {}
    index_descriptor = index_result.get("index_descriptor")
    assert isinstance(index_descriptor, dict), "remote awarded index descriptor missing"
    part_descriptors = validate_awarded_index_descriptor(
        index_descriptor
    )
    expected_part_files = set(part_descriptors)
    manifest_part_files = {
        name
        for name in assets
        if name.startswith("awarded_index_parts/") and name.endswith(".json")
    }
    if manifest_part_files != expected_part_files:
        raise AssertionError("remote awarded index manifest has stale or missing parts")

    index_refs: dict[str, str] = {}
    for name, descriptor in part_descriptors.items():
        expected = assets.get(name) or {}
        result = results.get(name) or {}
        if not result:
            raise AssertionError(f"remote awarded index part missing: {name}")
        if descriptor["bytes"] != expected.get("bytes"):
            raise AssertionError(f"remote awarded index part byte descriptor mismatch: {name}")
        if descriptor["sha256"] != expected.get("sha256"):
            raise AssertionError(f"remote awarded index part SHA descriptor mismatch: {name}")
        if descriptor["count"] != expected.get("records"):
            raise AssertionError(f"remote awarded index part count descriptor mismatch: {name}")
        if descriptor["count"] != result.get("count"):
            raise AssertionError(f"remote awarded index part count mismatch: {name}")
        refs = result.get("index_refs") or {}
        overlap = set(index_refs) & set(refs)
        if overlap:
            raise AssertionError(
                f"duplicate refs across remote awarded index parts: {sorted(overlap)[:3]}"
            )
        index_refs.update(refs)
    if len(index_refs) != index_descriptor["count"]:
        raise AssertionError("remote awarded index union/count mismatch")
    index_result["index_refs"] = index_refs

    detail_refs: set[str] = set()
    for shard in range(SHARD_COUNT):
        name = f"awarded_details/{shard:02d}.json"
        if name not in results:
            raise AssertionError(f"remote awarded detail shard missing: {name}")
        overlap = detail_refs & results[name].get("detail_refs", set())
        if overlap:
            raise AssertionError(f"duplicate refs across remote shards: {sorted(overlap)[:3]}")
        detail_refs.update(results[name].get("detail_refs", set()))
    if set(index_refs) != detail_refs:
        raise AssertionError("remote awarded index/detail ref set mismatch")
    return results


def check_remote(
    base_url: str,
    expected_snapshot_id: str | None,
    wait_seconds: int = 0,
) -> dict[str, int]:
    base_manifest_url = base_url.rstrip("/") + "/data/manifest.json"
    deadline = time.monotonic() + max(0, wait_seconds)
    last_error: Exception | None = None
    verified_assets: dict[str, dict] = {}
    verified_manifest_sha: str | None = None
    retry_number = 0
    while True:
        cache_key = f"{time.time_ns()}"
        url = f"{base_manifest_url}?snapshot-check={cache_key}"
        try:
            request = Request(
                url,
                headers={"Cache-Control": "no-cache", "User-Agent": "kashaf-contract-check/3"},
            )
            with urlopen(request, timeout=30) as response:
                raw = response.read()
            if raw.startswith(LFS_HEADER):
                raise AssertionError(f"remote manifest is a Git LFS pointer: {base_manifest_url}")
            candidate = json.loads(raw.decode("utf-8"))
            if candidate.get("schema") != "kashaf.static-warehouse":
                raise AssertionError("unknown remote manifest schema")
            if candidate.get("schema_version") != SCHEMA_VERSION:
                raise AssertionError("remote schema version mismatch")
            if not candidate.get("snapshot_id"):
                raise AssertionError("remote snapshot_id missing")
            if expected_snapshot_id and candidate["snapshot_id"] != expected_snapshot_id:
                raise AssertionError(
                    f"remote snapshot_id mismatch: {candidate['snapshot_id']} != {expected_snapshot_id}"
                )
            manifest_sha = hashlib.sha256(raw).hexdigest()
            if manifest_sha != verified_manifest_sha:
                verified_assets.clear()
                verified_manifest_sha = manifest_sha
            results = verify_remote_assets(
                base_url,
                candidate,
                cache_key=cache_key,
                verified=verified_assets,
            )
            return {
                "assets": len(results),
                "awarded": len(results["awarded_index.json"]["index_refs"]),
                "shards": SHARD_COUNT,
            }
        except (AssertionError, json.JSONDecodeError, URLError, TimeoutError) as error:
            last_error = error
            if time.monotonic() >= deadline:
                raise AssertionError(f"remote snapshot did not converge: {last_error}") from error
            retry_delay = min(60, 30 * (2**min(retry_number, 1)))
            retry_number += 1
            time.sleep(min(retry_delay, max(0.1, deadline - time.monotonic())))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--base-url",
        help="deployed Kashaf base URL; verifies manifest identity and every declared asset",
    )
    parser.add_argument("--expect-snapshot-id", help="required local or remote snapshot identity")
    parser.add_argument(
        "--wait-seconds",
        type=int,
        default=0,
        help="wait for a coherent remote snapshot; retries only assets that have not verified",
    )
    args = parser.parse_args()
    summary = (
        check_remote(args.base_url, args.expect_snapshot_id, args.wait_seconds)
        if args.base_url
        else check(args.root.resolve(), args.expect_snapshot_id)
    )
    print(
        "KASHAF_DATA_CONTRACT_OK",
        f"assets={summary['assets']}",
        f"awarded={summary['awarded']}",
        f"shards={summary['shards']}",
    )


if __name__ == "__main__":
    main()
