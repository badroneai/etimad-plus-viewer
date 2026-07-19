"""Fail closed when a Kashaf static snapshot is incomplete or unsafe to publish."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit
from urllib.error import URLError
from urllib.request import Request, urlopen

from export_warehouse import (
    ACTIVE_CENSUS_TAXONOMY_ENDPOINTS,
    AWARDED_INDEX_PART_ALGORITHM,
    AWARDED_INDEX_PART_COUNT,
    AWARDED_INDEX_PART_FORMAT_VERSION,
    CARDINALITY_SEAL_MODE,
    CARDINALITY_SEAL_SCHEMA_VERSION,
    CARDINALITY_SEAL_STRATEGY,
    INTERVAL_COVERAGE_MODE,
    INTERVAL_COVERAGE_SCHEMA_VERSION,
    INTERVAL_COVERAGE_STRATEGY,
    OFFICIAL_REGION_LABELS,
    SCHEMA_VERSION,
    SHARD_COUNT,
    active_refresh_sweep_complete,
    awarded_index_part_config,
    index_part_for_ref,
    parse_iso_datetime,
    selected_cardinality_authority,
    shard_for_ref,
    to_halalas,
)


LFS_HEADER = b"version https://git-lfs.github.com/spec/v1"
AWARDED_INDEX_DESCRIPTOR_MAX_BYTES = 1024 * 1024
AWARDED_INDEX_PART_MAX_BYTES = 5 * 1024 * 1024
OFFICIAL_COMPONENT_SOURCE_ID = "etimad_official_components"
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}", re.IGNORECASE)
ACTIVE_DATE_DOMAIN_START = "1900-01-01"
ACTIVE_DATE_DOMAIN_END = "2100-12-31"
ACTIVE_INTERVAL_DOMAIN_START = "1900-01-01"
ACTIVE_INTERVAL_DOMAIN_END_EXCLUSIVE = "2101-01-01"
SINGLE_DAY_REFINEMENT_VERSION = 1
SINGLE_DAY_REFINEMENT_STRATEGY = "single_day_type_area_cover_v1"
TEMPORAL_RECONCILIATION_VERSION = 2
TEMPORAL_RECONCILIATION_GENERATIONS = {2, 3}
SINGLE_DAY_MIRROR_COVER_VERSION = 1
SINGLE_DAY_MIRROR_COVER_STRATEGY = (
    "single_day_bidirectional_submission_cover_v1"
)
SINGLE_DAY_MIRROR_COVER_QUERY_SHA256 = (
    "fb4de883da302089b8f30490a62431af6aea76ac3de86e3714105cee1d628d48"
)
SINGLE_DAY_MIRROR_COVER_GENERATIONS = {2, 3}
SINGLE_DAY_MIRROR_COVER_MAX_GENERATION = 3
SINGLE_DAY_MIRROR_COVER_MIN_TOTAL = 49
SINGLE_DAY_MIRROR_COVER_MAX_TOTAL = 96
SINGLE_DAY_MIRROR_COVER_MAX_PAGES_PER_NODE = 12
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
SINGLE_DAY_REFINEMENT_COVERED_REASON = (
    "enumerated_single_day_type_partition"
)
SINGLE_DAY_REFINEMENT_BLOCKED_PREFIXES = (
    "single_day_type_refinement_blocked:",
    "single_day_type_refinement_failed:",
)
ACTIVE_LIST_ENDPOINT = (
    "https://tenders.etimad.sa/Tender/AllSupplierTendersForVisitorAsync"
)
ACTIVE_LIST_REQUIRED_PARAMS = {
    "TenderCategory": "2",
    "PublishDateId": "1",
    "SortDirection": "DESC",
    "Sort": "SubmitionDate",
    "IsSearch": "true",
}


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


def assert_active_scan_progress_contract(
    progress: object,
    evidence: object = None,
) -> None:
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
        assert_active_date_scan_contract(date_fallback, evidence)
        assert isinstance(date_fallback, dict)
        if date_fallback.get("schema_version") == CARDINALITY_SEAL_SCHEMA_VERSION:
            targets = date_fallback.get("targets")
            assert isinstance(targets, dict), "active census target status is missing"
            assert targets.get("total") == denominator
            assert targets.get("observed") == scanned
            assert targets.get("resolved") == resolved
            assert targets.get("absent") == absent
        elif date_fallback.get("schema_version") == INTERVAL_COVERAGE_SCHEMA_VERSION:
            targets = date_fallback.get("targets")
            assert isinstance(targets, dict), "active scan target status is missing"
            assert targets.get("total") == denominator
            assert targets.get("absent") == 0
            assert targets.get("observed") == targets.get("resolved")
            assert _nonnegative_integer(targets.get("observed"))
            assert targets["observed"] <= scanned, (
                "schema-5 cycle observations exceed outer historical scan balance"
            )
        else:
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
        if date_fallback.get("schema_version") == 3:
            assert progress.get("bootstrap") == date_fallback.get("bootstrap"), (
                "active bootstrap differs between scan ledgers"
            )
            assert progress.get("bootstrap_complete") == date_fallback[
                "bootstrap"
            ].get("complete"), "active bootstrap completion aliases disagree"
    date_scan_complete = bool(
        isinstance(date_fallback, dict)
        and (
            active_refresh_sweep_complete(date_fallback)
            if date_fallback.get("schema_version")
            == INTERVAL_COVERAGE_SCHEMA_VERSION
            else date_fallback.get("completion_authoritative", False)
        )
    )
    awaiting_date_completion = bool(
        isinstance(date_fallback, dict)
        and not date_scan_complete
    )
    if progress["complete"]:
        if (
            isinstance(date_fallback, dict)
            and date_fallback.get("schema_version")
            == INTERVAL_COVERAGE_SCHEMA_VERSION
        ):
            assert not awaiting_date_completion, (
                "active_scan completed before its active traversal"
            )
        else:
            assert not awaiting_date_completion, (
                "active_scan completed before date partition authority"
            )
    if denominator and remaining == 0 and not awaiting_date_completion:
        assert progress["complete"], "active_scan reached full coverage without completion"


def assert_active_missing_truth(progress: object, still_missing: object) -> None:
    """Clear the process gap without confusing interval coverage with authority."""

    assert isinstance(still_missing, dict), "still_missing is invalid"
    date_fallback = progress.get("date_fallback") if isinstance(progress, dict) else None
    completed = active_refresh_sweep_complete(date_fallback)
    assert ("active_refresh_sweep" not in still_missing) == completed, (
        "active_refresh_sweep still_missing disagrees with verified scan completion"
    )


def _sha256(value: object, *, label: str) -> str:
    assert isinstance(value, str) and SHA256_PATTERN.fullmatch(value), (
        f"{label} SHA-256 is invalid"
    )
    return value.lower()


def _reference_union_sha256(references: set[str]) -> str:
    payload = (
        ("\n".join(sorted(references)) + "\n") if references else ""
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _evidence_references(value: object, *, label: str) -> list[str]:
    assert isinstance(value, list), f"{label} references are missing"
    assert all(
        isinstance(reference, str) and reference
        for reference in value
    ), f"{label} contains an invalid reference"
    assert value == sorted(value), f"{label} references are not sorted"
    return value


def _assert_active_boundary_capture(
    evidence: object,
    *,
    verification_by_path: dict[str, dict],
    label: str,
    expected_total: int,
    expected_reference_sha: str,
    page_size: int,
    filtered: bool,
) -> set[str]:
    """Replay one page-1 opening/closing RAW-capture descriptor."""

    assert isinstance(evidence, dict), f"{label} evidence is missing"
    assert evidence.get("status") == 200, f"{label} status is not 200"
    assert str(evidence.get("content_type") or "").startswith("application/json"), (
        f"{label} content type is invalid"
    )
    assert _nonnegative_integer(evidence.get("bytes")) and evidence["bytes"] > 0, (
        f"{label} byte count is invalid"
    )
    assert evidence.get("total_count") == expected_total, (
        f"{label} total count mismatch"
    )
    references_value = evidence.get("references")
    assert isinstance(references_value, list) and all(
        isinstance(reference, str) and reference
        for reference in references_value
    ), f"{label} references are invalid"
    references = [str(reference) for reference in references_value]
    reference_set = set(references)
    assert len(references) == len(reference_set), (
        f"{label} contains duplicate references"
    )
    assert evidence.get("records") == len(references), (
        f"{label} record/reference count mismatch"
    )
    assert len(references) == min(page_size, expected_total), (
        f"{label} page-1 cardinality mismatch"
    )
    capture_reference_sha = _reference_union_sha256(reference_set)
    assert capture_reference_sha == expected_reference_sha, (
        f"{label} references do not match the boundary head"
    )
    assert _sha256(
        evidence.get("reference_sha256"), label=f"{label} reference"
    ) == expected_reference_sha, f"{label} reference hash mismatch"
    assert isinstance(evidence.get("raw_path"), str) and evidence["raw_path"], (
        f"{label} raw path is missing"
    )
    _assert_raw_verification_pointer(
        verification_by_path,
        evidence.get("raw_path"),
        evidence.get("sha256"),
        label=label,
        expected_bytes=evidence.get("bytes"),
    )

    capture_url = evidence.get("url")
    assert isinstance(capture_url, str) and capture_url, f"{label} URL is missing"
    actual = urlsplit(capture_url)
    expected_endpoint = urlsplit(ACTIVE_LIST_ENDPOINT)
    assert (actual.scheme, actual.netloc, actual.path) == (
        expected_endpoint.scheme,
        expected_endpoint.netloc,
        expected_endpoint.path,
    ), f"{label} endpoint mismatch"
    query = parse_qs(actual.query, keep_blank_values=True)
    expected_query = {
        **ACTIVE_LIST_REQUIRED_PARAMS,
        "PageSize": str(page_size),
        "PageNumber": "1",
    }
    if filtered:
        expected_query.update(
            {
                "FromLastOfferPresentationDateString": "01/01/1900",
                "ToLastOfferPresentationDateString": "31/12/2100",
            }
        )
    unexpected = set(query) - set(expected_query) - {"_"}
    assert not unexpected, f"{label} query has unexpected parameters"
    for key, value in expected_query.items():
        assert query.get(key) == [value], f"{label} query mismatch: {key}"
    return reference_set


def _assert_raw_verification_pointer(
    verification_by_path: dict[str, dict],
    raw_path: object,
    sha256: object,
    *,
    label: str,
    expected_bytes: object = None,
) -> None:
    raw_text = str(raw_path or "").strip()
    path = Path(raw_text)
    assert raw_text and not path.is_absolute() and ".." not in path.parts, (
        f"{label} RAW path is unsafe or missing"
    )
    descriptor = verification_by_path.get(raw_text)
    assert isinstance(descriptor, dict), f"{label} lacks export-time byte verification"
    assert descriptor.get("sha256") == _sha256(sha256, label=f"{label} RAW"), (
        f"{label} RAW verification hash mismatch"
    )
    assert _nonnegative_integer(descriptor.get("bytes")), (
        f"{label} RAW verification byte count is invalid"
    )
    if expected_bytes is not None:
        assert descriptor["bytes"] == expected_bytes, (
            f"{label} RAW verification byte count mismatch"
        )


def _assert_active_generation_proof(
    proof: object,
    *,
    bootstrap_refs: set[str],
    bootstrap_sha: str,
    bootstrap_head_sha: str,
    bootstrap_total: int,
    bootstrap_pass: int,
    page_size: int,
    verification_by_path: dict[str, dict],
) -> tuple[int, int, str]:
    """Replay one append-only closed-generation proof from its embedded evidence."""

    assert isinstance(proof, dict), "active generation proof row is invalid"
    for key in (
        "bootstrap_pass_number",
        "generation",
        "convergence_ordinal",
        "date_unique",
        "residual_unique",
        "union_unique",
        "opening_filtered_total_count",
        "closing_filtered_total_count",
        "opening_boundary_total_count",
        "closing_boundary_total_count",
    ):
        assert _nonnegative_integer(proof.get(key)), (
            f"active generation proof {key} is invalid"
        )
    assert proof["bootstrap_pass_number"] == bootstrap_pass, (
        "active generation proof bootstrap pass mismatch"
    )
    assert proof["generation"] >= 1, "active generation proof generation is invalid"
    assert proof["convergence_ordinal"] >= 1, (
        "active generation proof convergence ordinal is invalid"
    )
    for key in (
        "date_union_sha256",
        "residual_union_sha256",
        "union_sha256",
        "bootstrap_union_sha256",
        "opening_filtered_ref_sha256",
        "closing_filtered_ref_sha256",
        "opening_boundary_ref_sha256",
        "closing_boundary_ref_sha256",
    ):
        _sha256(proof.get(key), label=f"active generation proof {key}")

    date_refs = _evidence_references(
        proof.get("date_references"), label="active generation proof date"
    )
    residual_refs = _evidence_references(
        proof.get("residual_references"), label="active generation proof residual"
    )
    union_refs = _evidence_references(
        proof.get("union_references"), label="active generation proof union"
    )
    date_set = set(date_refs)
    residual_set = set(residual_refs)
    union_set = set(union_refs)
    assert len(date_refs) == len(date_set), (
        "active generation proof date references contain duplicates"
    )
    assert len(residual_refs) == len(residual_set), (
        "active generation proof residual references contain duplicates"
    )
    assert len(union_refs) == len(union_set), (
        "active generation proof union references contain duplicates"
    )
    assert proof["date_unique"] == len(date_set), (
        "active generation proof date count mismatch"
    )
    assert proof["opening_filtered_total_count"] == proof["date_unique"], (
        "active generation proof filtered total differs from date union"
    )
    assert proof["residual_unique"] == len(residual_set), (
        "active generation proof residual count mismatch"
    )
    assert proof["union_unique"] == len(union_set), (
        "active generation proof union count mismatch"
    )
    assert _reference_union_sha256(date_set) == proof["date_union_sha256"], (
        "active generation proof date hash mismatch"
    )
    assert _reference_union_sha256(residual_set) == proof["residual_union_sha256"], (
        "active generation proof residual hash mismatch"
    )
    proof_union_sha = _reference_union_sha256(union_set)
    assert proof_union_sha == proof["union_sha256"], (
        "active generation proof union hash mismatch"
    )
    assert date_set.isdisjoint(residual_set), (
        "active generation proof date/residual sets overlap"
    )
    assert date_set | residual_set == union_set, (
        "active generation proof union is not D plus R"
    )
    assert residual_set == bootstrap_refs - date_set, (
        "active generation proof residual is not U minus D"
    )
    assert union_set == bootstrap_refs, (
        "active generation proof union does not replay to U"
    )
    assert proof["bootstrap_union_sha256"] == bootstrap_sha, (
        "active generation proof bootstrap hash mismatch"
    )

    assert proof["opening_filtered_total_count"] == proof[
        "closing_filtered_total_count"
    ], "active generation proof filtered total changed at close"
    assert proof["opening_filtered_ref_sha256"] == proof[
        "closing_filtered_ref_sha256"
    ], "active generation proof filtered head changed at close"
    assert proof["opening_boundary_total_count"] == bootstrap_total, (
        "active generation proof opening boundary total mismatch"
    )
    assert proof["closing_boundary_total_count"] == bootstrap_total, (
        "active generation proof closing boundary total mismatch"
    )
    assert proof["opening_boundary_ref_sha256"] == bootstrap_head_sha, (
        "active generation proof opening boundary head mismatch"
    )
    assert proof["closing_boundary_ref_sha256"] == bootstrap_head_sha, (
        "active generation proof closing boundary head mismatch"
    )
    boundary_evidence = proof.get("boundary_evidence")
    assert isinstance(boundary_evidence, dict), (
        "active generation proof boundary evidence is missing"
    )
    opening_evidence = boundary_evidence.get("opening")
    closing_evidence = boundary_evidence.get("closing")
    assert isinstance(opening_evidence, dict) and isinstance(closing_evidence, dict), (
        "active generation proof opening/closing evidence is missing"
    )
    opening_filtered_refs = _assert_active_boundary_capture(
        opening_evidence.get("filtered"),
        verification_by_path=verification_by_path,
        label="active generation opening filtered boundary",
        expected_total=proof["opening_filtered_total_count"],
        expected_reference_sha=proof["opening_filtered_ref_sha256"],
        page_size=page_size,
        filtered=True,
    )
    closing_filtered_refs = _assert_active_boundary_capture(
        closing_evidence.get("filtered"),
        verification_by_path=verification_by_path,
        label="active generation closing filtered boundary",
        expected_total=proof["closing_filtered_total_count"],
        expected_reference_sha=proof["closing_filtered_ref_sha256"],
        page_size=page_size,
        filtered=True,
    )
    opening_unfiltered_refs = _assert_active_boundary_capture(
        opening_evidence.get("unfiltered"),
        verification_by_path=verification_by_path,
        label="active generation opening unfiltered boundary",
        expected_total=proof["opening_boundary_total_count"],
        expected_reference_sha=proof["opening_boundary_ref_sha256"],
        page_size=page_size,
        filtered=False,
    )
    closing_unfiltered_refs = _assert_active_boundary_capture(
        closing_evidence.get("unfiltered"),
        verification_by_path=verification_by_path,
        label="active generation closing unfiltered boundary",
        expected_total=proof["closing_boundary_total_count"],
        expected_reference_sha=proof["closing_boundary_ref_sha256"],
        page_size=page_size,
        filtered=False,
    )
    assert opening_filtered_refs == closing_filtered_refs, (
        "active generation filtered boundary reference sets changed"
    )
    assert opening_filtered_refs <= date_set, (
        "active generation filtered boundary references are outside D"
    )
    assert opening_unfiltered_refs == closing_unfiltered_refs, (
        "active generation unfiltered boundary reference sets changed"
    )
    assert opening_unfiltered_refs <= bootstrap_refs, (
        "active generation unfiltered boundary references are outside U"
    )

    range_rows = proof.get("range_generations")
    assert isinstance(range_rows, list), "active generation proof ranges are missing"
    range_by_id: dict[str, dict] = {}
    geometry_cursor = date.fromisoformat(ACTIVE_DATE_DOMAIN_START)
    geometry_end = date.fromisoformat(ACTIVE_DATE_DOMAIN_END)
    for range_row in range_rows:
        assert isinstance(range_row, dict), "active generation proof range is invalid"
        range_id = range_row.get("range_id")
        assert isinstance(range_id, str) and range_id and range_id not in range_by_id, (
            "active generation proof range id is invalid or duplicated"
        )
        range_by_id[range_id] = range_row
        assert _nonnegative_integer(range_row.get("generation")) and range_row[
            "generation"
        ] >= 1, "active generation proof range generation is invalid"
        assert _nonnegative_integer(range_row.get("total_count")), (
            "active generation proof range total is invalid"
        )
        try:
            from_day = date.fromisoformat(str(range_row.get("from_day")))
            to_day = date.fromisoformat(str(range_row.get("to_day")))
        except ValueError as exc:
            raise AssertionError("active generation proof range date is invalid") from exc
        assert from_day == geometry_cursor and from_day <= to_day, (
            "active generation proof range geometry has a gap or overlap"
        )
        geometry_cursor = to_day + timedelta(days=1)
    assert geometry_cursor == geometry_end + timedelta(days=1), (
        "active generation proof ranges do not cover the fixed domain"
    )

    page_rows = proof.get("page_evidence")
    assert isinstance(page_rows, list), "active generation proof pages are missing"
    pages_by_range: dict[str, list[dict]] = {}
    replayed_date_refs: list[str] = []
    page_keys: set[tuple[str, int]] = set()
    for page in page_rows:
        assert isinstance(page, dict), "active generation proof page is invalid"
        range_id = page.get("range_id")
        assert isinstance(range_id, str) and range_id in range_by_id, (
            "active generation proof page has an unknown range"
        )
        assert page.get("generation") == range_by_id[range_id]["generation"], (
            "active generation proof page generation mismatch"
        )
        page_number_value = page.get("page_number")
        records_value = page.get("records")
        assert (
            isinstance(page_number_value, int)
            and not isinstance(page_number_value, bool)
            and page_number_value >= 1
        ), (
            "active generation proof page number is invalid"
        )
        assert (
            isinstance(records_value, int)
            and not isinstance(records_value, bool)
            and records_value >= 0
        ), (
            "active generation proof page records are invalid"
        )
        page_number = int(page_number_value)
        records = int(records_value)
        assert page.get("total_count") == range_by_id[range_id]["total_count"], (
            "active generation proof page total mismatch"
        )
        page_key = (range_id, page_number)
        assert page_key not in page_keys, "active generation proof page is duplicated"
        page_keys.add(page_key)
        references = _evidence_references(
            page.get("references"),
            label=f"active generation proof page {range_id}/{page_number}",
        )
        assert len(references) == records and len(references) == len(set(references)), (
            "active generation proof page reference count is invalid"
        )
        assert isinstance(page.get("raw_path"), str) and page["raw_path"], (
            "active generation proof page raw path is missing"
        )
        _assert_raw_verification_pointer(
            verification_by_path,
            page.get("raw_path"),
            page.get("sha256"),
            label="active generation proof page",
        )
        replayed_date_refs.extend(references)
        pages_by_range.setdefault(range_id, []).append(page)
    assert len(replayed_date_refs) == len(set(replayed_date_refs)), (
        "active generation proof pages contain duplicate references"
    )
    assert set(replayed_date_refs) == date_set, (
        "active generation proof pages do not replay to D"
    )
    assert set(pages_by_range) == set(range_by_id), (
        "active generation proof range has no page evidence"
    )
    for range_id, pages in pages_by_range.items():
        total = range_by_id[range_id]["total_count"]
        expected_pages = max(1, (total + page_size - 1) // page_size)
        assert [page["page_number"] for page in pages] == list(
            range(1, expected_pages + 1)
        ), "active generation proof page sequence is incomplete"
        for page in pages:
            expected_records = min(
                page_size,
                max(0, total - (page["page_number"] - 1) * page_size),
            )
            assert page["records"] == expected_records, (
                "active generation proof page cardinality mismatch"
            )

    residual_rows = proof.get("residual_evidence")
    assert isinstance(residual_rows, list), (
        "active generation proof residual evidence is missing"
    )
    replayed_residual: list[str] = []
    for row in residual_rows:
        assert isinstance(row, dict), "active generation proof residual row is invalid"
        ref = row.get("reference_number")
        assert isinstance(ref, str) and ref, (
            "active generation proof residual reference is invalid"
        )
        assert row.get("state") == "verified_active" and row.get("status_id") == 4, (
            "active generation proof residual is not verified status 4"
        )
        assert isinstance(row.get("raw_path"), str) and row["raw_path"], (
            "active generation proof residual raw path is missing"
        )
        _assert_raw_verification_pointer(
            verification_by_path,
            row.get("raw_path"),
            row.get("sha256"),
            label="active generation proof residual",
        )
        assert isinstance(row.get("run_id"), str) and row["run_id"], (
            "active generation proof residual run id is missing"
        )
        assert isinstance(row.get("checked_at"), str) and row["checked_at"], (
            "active generation proof residual checked_at is missing"
        )
        replayed_residual.append(ref)
    assert len(replayed_residual) == len(set(replayed_residual)), (
        "active generation proof residual evidence is duplicated"
    )
    assert set(replayed_residual) == residual_set, (
        "active generation proof residual evidence does not replay to R"
    )
    assert isinstance(proof.get("run_id"), str) and proof["run_id"], (
        "active generation proof run id is missing"
    )
    assert isinstance(proof.get("closed_at"), str) and proof["closed_at"], (
        "active generation proof closed_at is missing"
    )
    return proof["generation"], proof["convergence_ordinal"], proof_union_sha


def _cardinality_bijection_sha256(mappings: dict[str, str]) -> str:
    assert len(mappings) == len(set(mappings.values())), (
        "active census mappings are not a ref/tender-id bijection"
    )
    payload = "".join(
        f"{reference}\t{mappings[reference]}\n" for reference in sorted(mappings)
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _cardinality_taxonomy_sha256(
    taxonomy: dict[str, list[dict[str, str]]],
) -> str:
    return hashlib.sha256(
        json.dumps(
            taxonomy,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _assert_cardinality_list_url(
    value: object,
    *,
    page_number: int,
    filters: dict[str, str],
    label: str,
) -> None:
    actual = urlsplit(str(value or ""))
    expected = urlsplit(ACTIVE_LIST_ENDPOINT)
    assert (actual.scheme, actual.netloc, actual.path) == (
        expected.scheme,
        expected.netloc,
        expected.path,
    ), f"{label} endpoint mismatch"
    query = parse_qs(actual.query, keep_blank_values=True)
    expected_query = {
        **ACTIVE_LIST_REQUIRED_PARAMS,
        "PageSize": "24",
        "PageNumber": str(page_number),
        **filters,
    }
    assert not set(query) - set(expected_query) - {"_"}, (
        f"{label} query has unexpected parameters"
    )
    for key, expected_value in expected_query.items():
        assert query.get(key) == [expected_value], f"{label} query mismatch: {key}"


def _cardinality_mapping_list(
    value: object,
    *,
    label: str,
) -> list[dict[str, str]]:
    assert isinstance(value, list), f"{label} mappings are missing"
    result: list[dict[str, str]] = []
    for row in value:
        assert isinstance(row, dict), f"{label} mapping row is invalid"
        reference = row.get("reference_number")
        tender_id = row.get("tender_id")
        assert isinstance(reference, str) and reference, (
            f"{label} mapping reference is invalid"
        )
        assert isinstance(tender_id, str) and tender_id, (
            f"{label} mapping tender id is invalid"
        )
        result.append({"reference_number": reference, "tender_id": tender_id})
    return result


def _assert_cardinality_boundary_capture(
    capture: object,
    *,
    expected_total: int,
    expected_head_sha: str,
    verification_by_path: dict[str, dict],
    label: str,
) -> tuple[set[str], str]:
    assert isinstance(capture, dict), f"{label} capture is missing"
    assert capture.get("status") == 200, f"{label} status is not 200"
    assert not capture.get("content_type") or "json" in str(
        capture.get("content_type")
    ).lower(), (
        f"{label} content type is invalid"
    )
    assert capture.get("total_count") == expected_total, f"{label} total drift"
    references = capture.get("references")
    assert isinstance(references, list) and all(
        isinstance(reference, str) and reference for reference in references
    ), f"{label} references are invalid"
    assert len(references) == len(set(references)), f"{label} has duplicate references"
    assert capture.get("records") == len(references), f"{label} record count mismatch"
    assert len(references) == min(24, expected_total), f"{label} page cardinality mismatch"
    head_sha = _reference_union_sha256(set(references))
    assert head_sha == expected_head_sha, f"{label} head reference drift"
    assert _sha256(capture.get("reference_sha256"), label=f"{label} reference") == head_sha, (
        f"{label} reference hash mismatch"
    )
    assert _nonnegative_integer(capture.get("bytes")) and capture["bytes"] > 0, (
        f"{label} byte count is invalid"
    )
    _assert_raw_verification_pointer(
        verification_by_path,
        capture.get("raw_path"),
        capture.get("sha256"),
        label=label,
        expected_bytes=capture.get("bytes"),
    )
    _assert_cardinality_list_url(
        capture.get("url"), page_number=1, filters={}, label=label
    )
    return set(references), str(capture["raw_path"])


def _assert_cardinality_taxonomy(
    taxonomy_evidence: object,
    *,
    verification_by_path: dict[str, dict],
) -> tuple[dict[str, list[dict[str, str]]], str, set[str]]:
    assert isinstance(taxonomy_evidence, dict), "active census taxonomy evidence is missing"
    values = taxonomy_evidence.get("values")
    captures = taxonomy_evidence.get("captures")
    assert isinstance(values, dict), "active census taxonomy values are missing"
    assert isinstance(captures, list), "active census taxonomy captures are missing"
    expected_kinds = {"type", "area", "activity", "agency", "booklet"}
    assert set(values) == expected_kinds, "active census taxonomy kinds are incomplete"
    canonical: dict[str, list[dict[str, str]]] = {}
    for kind, entries in values.items():
        assert isinstance(entries, list), f"active census taxonomy {kind} is invalid"
        normalised: list[dict[str, str]] = []
        seen_values: set[str] = set()
        for entry in entries:
            assert isinstance(entry, dict), f"active census taxonomy {kind} row is invalid"
            value = entry.get("value")
            label = entry.get("label")
            assert isinstance(value, str) and value and value not in seen_values, (
                f"active census taxonomy {kind} value is blank or duplicated"
            )
            assert isinstance(label, str) and label, (
                f"active census taxonomy {kind} label is blank"
            )
            seen_values.add(value)
            normalised.append({"value": value, "label": label})
        assert normalised == sorted(normalised, key=lambda row: row["value"]), (
            f"active census taxonomy {kind} is not sorted"
        )
        canonical[kind] = normalised
    assert canonical["booklet"] == [
        {"value": str(value), "label": str(value)} for value in range(7)
    ], "active census booklet taxonomy differs from fixed 0..6"
    taxonomy_sha = _cardinality_taxonomy_sha256(canonical)
    assert _sha256(
        taxonomy_evidence.get("sha256"), label="active census taxonomy"
    ) == taxonomy_sha, "active census taxonomy hash mismatch"

    captures_by_kind: dict[str, dict] = {}
    raw_paths: set[str] = set()
    for capture in captures:
        assert isinstance(capture, dict), "active census taxonomy capture is invalid"
        kind = capture.get("kind")
        assert kind in ACTIVE_CENSUS_TAXONOMY_ENDPOINTS and kind not in captures_by_kind, (
            "active census taxonomy capture kind is invalid or duplicated"
        )
        captures_by_kind[str(kind)] = capture
        endpoint_path = ACTIVE_CENSUS_TAXONOMY_ENDPOINTS[str(kind)]
        declared_endpoint = urlsplit(str(capture.get("endpoint") or ""))
        declared_path = (
            declared_endpoint.path
            if declared_endpoint.scheme
            else str(capture.get("endpoint") or "")
        )
        assert declared_path == endpoint_path, (
            f"active census taxonomy endpoint mismatch: {kind}"
        )
        actual = urlsplit(str(capture.get("url") or ""))
        assert actual.scheme == "https" and actual.netloc == "tenders.etimad.sa", (
            f"active census taxonomy host mismatch: {kind}"
        )
        assert actual.path == endpoint_path, f"active census taxonomy URL mismatch: {kind}"
        assert not set(parse_qs(actual.query, keep_blank_values=True)) - {"_"}, (
            f"active census taxonomy query mismatch: {kind}"
        )
        assert capture.get("status") == 200, f"active census taxonomy status: {kind}"
        assert not capture.get("content_type") or "json" in str(
            capture.get("content_type")
        ).lower(), (
            f"active census taxonomy content type: {kind}"
        )
        assert capture.get("values") == canonical[str(kind)], (
            f"active census taxonomy capture/value mismatch: {kind}"
        )
        _assert_raw_verification_pointer(
            verification_by_path,
            capture.get("raw_path"),
            capture.get("sha256"),
            label=f"active census taxonomy {kind}",
            expected_bytes=capture.get("bytes"),
        )
        raw_paths.add(str(capture["raw_path"]))
    assert set(captures_by_kind) == set(ACTIVE_CENSUS_TAXONOMY_ENDPOINTS), (
        "active census taxonomy RAW captures are incomplete"
    )
    return canonical, taxonomy_sha, raw_paths


def _assert_cardinality_page(
    page: object,
    *,
    filters: dict[str, str],
    verification_by_path: dict[str, dict],
    label: str,
) -> tuple[list[dict[str, str]], str]:
    assert isinstance(page, dict), f"{label} page is invalid"
    page_number = page.get("page_number")
    assert (
        isinstance(page_number, int)
        and not isinstance(page_number, bool)
        and 1 <= page_number <= 2
    ), (
        f"{label} page number exceeds the cardinality ceiling"
    )
    total_count = page.get("total_count")
    records = page.get("records")
    assert _nonnegative_integer(total_count), f"{label} total count is invalid"
    assert _nonnegative_integer(records), f"{label} record count is invalid"
    references = page.get("references")
    assert isinstance(references, list) and all(
        isinstance(reference, str) and reference for reference in references
    ), f"{label} references are invalid"
    assert records == len(references), f"{label} record/reference count mismatch"
    assert len(references) == len(set(references)), f"{label} duplicate within page"
    mappings = _cardinality_mapping_list(page.get("mappings"), label=label)
    assert [row["reference_number"] for row in mappings] == references, (
        f"{label} mapping/reference order mismatch"
    )
    assert len({row["tender_id"] for row in mappings}) == len(mappings), (
        f"{label} duplicate tender id within page"
    )
    _assert_raw_verification_pointer(
        verification_by_path,
        page.get("raw_path"),
        page.get("sha256"),
        label=label,
    )
    _assert_cardinality_list_url(
        page.get("url"), page_number=page_number, filters=filters, label=label
    )
    return mappings, str(page["raw_path"])


def _assert_cardinality_nodes(
    node_evidence: object,
    *,
    taxonomy: dict[str, list[dict[str, str]]],
    verification_by_path: dict[str, dict],
    exact_only: bool,
    label: str,
    generation: int,
    boundary_total: int,
    union_sha256: str,
) -> tuple[dict[str, str], int, int, set[str], dict[str, int]]:
    assert isinstance(node_evidence, dict), f"{label} node evidence is missing"
    nodes = node_evidence.get("nodes")
    pages = node_evidence.get("pages")
    assert isinstance(nodes, list) and isinstance(pages, list), (
        f"{label} node/page evidence is incomplete"
    )
    lens_order = (
        ("type", "TenderTypeId"),
        ("area", "TenderAreasIdString"),
        ("booklet", "ConditionaBookletRange"),
        ("activity", "TenderActivityId"),
        ("agency", "AgencyCode"),
    )
    values_by_filter = {
        filter_key: {row["value"] for row in taxonomy[kind]}
        for kind, filter_key in lens_order
    }
    nodes_by_id: dict[str, dict] = {}
    state_counts: dict[str, int] = {}
    for node in nodes:
        assert isinstance(node, dict), f"{label} node is invalid"
        node_id = node.get("node_id")
        assert isinstance(node_id, str) and node_id and node_id not in nodes_by_id, (
            f"{label} node id is missing or duplicated"
        )
        depth = node.get("depth")
        assert (
            isinstance(depth, int)
            and not isinstance(depth, bool)
            and 0 <= depth <= len(lens_order)
        ), (
            f"{label} node depth is invalid"
        )
        filters = node.get("filters")
        assert isinstance(filters, dict), f"{label} node filters are invalid"
        assert list(filters) == [key for _, key in lens_order[:depth]], (
            f"{label} filter hierarchy has a gap"
        )
        for key, raw_value in filters.items():
            assert isinstance(raw_value, str) and raw_value in values_by_filter[key], (
                f"{label} filter value is outside taxonomy: {key}"
            )
        expected_lens = lens_order[depth - 1][0] if depth else "root"
        assert node.get("lens_name") == expected_lens, f"{label} lens/depth mismatch"
        state = node.get("state")
        allowed_states = {
            "pending",
            "split",
            "exact",
            "blocked",
            "error",
            "superseded_by_cardinality",
        }
        assert state in allowed_states, f"{label} node state is invalid"
        if exact_only:
            assert state == "exact", f"{label} proof contains a non-exact node"
        supersession = node.get("supersession")
        assert isinstance(supersession, dict) and set(supersession) == {
            "reason",
            "union_sha256",
            "generation",
            "boundary_total_count",
        }, f"{label} node supersession binding is missing"
        if state == "superseded_by_cardinality":
            assert (
                supersession.get("reason")
                == "union_reached_boundary_cardinality"
            ), f"{label} node supersession reason mismatch"
            assert supersession.get("union_sha256") == union_sha256, (
                f"{label} node supersession union binding mismatch"
            )
            assert supersession.get("generation") == generation, (
                f"{label} node supersession generation binding mismatch"
            )
            assert supersession.get("boundary_total_count") == boundary_total, (
                f"{label} node supersession boundary binding mismatch"
            )
        else:
            assert all(value is None for value in supersession.values()), (
                f"{label} non-superseded node carries a supersession binding"
            )
        state_counts[str(state)] = state_counts.get(str(state), 0) + 1
        nodes_by_id[node_id] = node

    if not exact_only:
        roots = [
            node
            for node in nodes_by_id.values()
            if node.get("parent_node_id") is None
        ]
        assert len(roots) == 1 and roots[0].get("depth") == 0, (
            f"{label} frontier must have exactly one root"
        )
        for node in nodes_by_id.values():
            parent_id = node.get("parent_node_id")
            if parent_id is None:
                continue
            parent = nodes_by_id.get(str(parent_id))
            assert isinstance(parent, dict), f"{label} frontier parent is missing"
            assert node["depth"] == parent["depth"] + 1, (
                f"{label} frontier depth gap"
            )
            assert list(node["filters"].items())[:-1] == list(parent["filters"].items()), (
                f"{label} child does not inherit parent filters"
            )

    pages_by_node: dict[str, list[dict]] = {}
    for page in pages:
        assert isinstance(page, dict), f"{label} page row is invalid"
        node_id = str(page.get("node_id") or "")
        assert node_id in nodes_by_id, f"{label} page has no node"
        pages_by_node.setdefault(node_id, []).append(page)

    membership: dict[str, str] = {}
    tender_to_ref: dict[str, str] = {}
    duplicate_occurrences = 0
    duplicate_tender_occurrences = 0
    raw_paths: set[str] = set()
    for node_id, node in nodes_by_id.items():
        node_pages = sorted(
            pages_by_node.get(node_id, []), key=lambda row: row.get("page_number", 0)
        )
        page_numbers: list[int] = []
        for page_row in node_pages:
            page_number = page_row.get("page_number")
            assert (
                isinstance(page_number, int)
                and not isinstance(page_number, bool)
                and page_number >= 1
            ), f"{label} node page number is invalid"
            page_numbers.append(page_number)
        if node["state"] == "exact":
            total = node.get("total_count")
            page_count = node.get("page_count")
            assert isinstance(total, int) and not isinstance(total, bool) and total >= 0, (
                f"{label} exact node total is invalid"
            )
            assert (
                isinstance(page_count, int)
                and not isinstance(page_count, bool)
                and page_count >= 0
            ), f"{label} exact node pages invalid"
            expected_pages = max(1, (total + 23) // 24)
            assert expected_pages <= 2, f"{label} exact node exceeds two-page ceiling"
            assert page_count == expected_pages, f"{label} exact node page count mismatch"
            assert page_numbers == list(range(1, expected_pages + 1)), (
                f"{label} exact node has a page gap"
            )
        elif page_numbers:
            assert page_numbers == sorted(set(page_numbers)), (
                f"{label} non-exact node page ledger is duplicated or unsorted"
            )
        node_seen: set[str] = set()
        for page in node_pages:
            mappings, raw_path = _assert_cardinality_page(
                page,
                filters={str(key): str(value) for key, value in node["filters"].items()},
                verification_by_path=verification_by_path,
                label=f"{label} page {node_id}:{page.get('page_number')}",
            )
            raw_paths.add(raw_path)
            if node["state"] == "exact":
                assert page.get("total_count") == node.get("total_count"), (
                    f"{label} exact page/node total mismatch"
                )
                expected_records = min(
                    24,
                    max(
                        0,
                        int(node["total_count"])
                        - 24 * (int(page["page_number"]) - 1),
                    ),
                )
                assert page.get("records") == expected_records, (
                    f"{label} exact page overcount or undercount"
                )
            for mapping in mappings:
                reference = mapping["reference_number"]
                tender_id = mapping["tender_id"]
                assert reference not in node_seen, f"{label} duplicate across node pages"
                node_seen.add(reference)
                existing = membership.get(reference)
                if existing is not None:
                    assert existing == tender_id, f"{label} reference maps to two tender ids"
                    duplicate_occurrences += 1
                    duplicate_tender_occurrences += 1
                    continue
                existing_ref = tender_to_ref.get(tender_id)
                assert existing_ref is None, f"{label} tender id maps to two references"
                membership[reference] = tender_id
                tender_to_ref[tender_id] = reference
        if node["state"] == "exact":
            assert len(node_seen) == node["total_count"], (
                f"{label} exact leaf union cardinality mismatch"
            )
    return (
        membership,
        duplicate_occurrences,
        duplicate_tender_occurrences,
        raw_paths,
        state_counts,
    )


def _assert_cardinality_candidates(
    evidence: object,
    *,
    verification_by_path: dict[str, dict],
    label: str,
    cycle_id: str,
    generation: int,
) -> tuple[dict[str, str], int, set[str], list[str]]:
    if isinstance(evidence, dict):
        checks = evidence.get("checks")
        superseded_rows = evidence.get("superseded")
        assert isinstance(superseded_rows, list), (
            f"{label} superseded candidate evidence is missing"
        )
        checks = (
            sorted(
                [*checks, *superseded_rows],
                key=lambda row: str(row.get("reference_number") or "")
                if isinstance(row, dict)
                else "",
            )
            if isinstance(checks, list)
            else checks
        )
    else:
        checks = evidence
    assert isinstance(checks, list), f"{label} candidate evidence is missing"
    included: dict[str, str] = {}
    tender_ids: set[str] = set()
    pending = 0
    superseded: list[str] = []
    raw_paths: set[str] = set()
    previous_ref: str | None = None
    for check_row in checks:
        assert isinstance(check_row, dict), f"{label} candidate row is invalid"
        reference = check_row.get("reference_number")
        assert isinstance(reference, str) and reference.isdigit(), (
            f"{label} candidate reference is invalid"
        )
        assert previous_ref is None or previous_ref < reference, (
            f"{label} candidates are duplicate or unsorted"
        )
        previous_ref = reference
        state = check_row.get("state")
        assert state in {
            "included",
            "verified_active",
            "excluded",
            "verified_nonactive",
            "pending",
            "error",
            "superseded_by_cardinality",
        }, f"{label} candidate state is invalid"
        raw_path = check_row.get("raw_path")
        if state == "superseded_by_cardinality":
            assert check_row.get("cycle_id") == cycle_id, (
                f"{label} superseded candidate cycle binding mismatch"
            )
            assert check_row.get("generation") == generation, (
                f"{label} superseded candidate generation binding mismatch"
            )
            assert check_row.get("error") == "union_reached_boundary_cardinality", (
                f"{label} superseded candidate reason mismatch"
            )
            for key in ("status_id", "tender_id", "raw_path", "sha256", "url"):
                assert check_row.get(key) is None, (
                    f"{label} superseded candidate fabricated {key} evidence"
                )
            superseded.append(reference)
            continue
        if state in {"pending", "error"}:
            pending += 1
            if raw_path in (None, ""):
                continue
        _assert_raw_verification_pointer(
            verification_by_path,
            raw_path,
            check_row.get("sha256"),
            label=f"{label} candidate {reference}",
        )
        raw_paths.add(str(raw_path))
        _assert_cardinality_list_url(
            check_row.get("url"),
            page_number=1,
            filters={"ReferenceNumber": reference},
            label=f"{label} candidate {reference}",
        )
        if state in {"included", "verified_active"}:
            assert check_row.get("status_id") == 4, (
                f"{label} included candidate is not status 4"
            )
            tender_id = check_row.get("tender_id")
            assert isinstance(tender_id, str) and tender_id, (
                f"{label} included candidate tender id is missing"
            )
            assert reference not in included and tender_id not in tender_ids, (
                f"{label} included candidate mapping is duplicated"
            )
            included[reference] = tender_id
            tender_ids.add(tender_id)
        elif state in {"excluded", "verified_nonactive"}:
            status_id = check_row.get("status_id")
            assert status_id is None or (
                isinstance(status_id, int)
                and not isinstance(status_id, bool)
                and status_id != 4
            ), f"{label} excluded candidate still claims status 4"
    if isinstance(evidence, dict):
        assert evidence.get("superseded_by_cardinality_count") == len(superseded), (
            f"{label} superseded candidate count mismatch"
        )
        assert evidence.get("superseded_reference_sha256") == (
            _reference_union_sha256(set(superseded))
        ), f"{label} superseded candidate hash mismatch"
    return included, pending, raw_paths, superseded


def assert_active_cardinality_scan_contract(
    progress: dict,
    evidence: object,
) -> None:
    """Replay schema-4 cardinality seal from independently checksummed evidence."""

    assert_active_cardinality_progress_summary(progress)
    assert progress.get("schema_version") == CARDINALITY_SEAL_SCHEMA_VERSION
    assert progress.get("strategy") == CARDINALITY_SEAL_STRATEGY, (
        "schema-4 active census strategy mismatch"
    )
    assert progress.get("mode") == CARDINALITY_SEAL_MODE, (
        "schema-4 active census mode mismatch"
    )
    assert isinstance(evidence, dict), "schema-4 active census evidence is missing"
    for key in ("schema_version", "strategy", "mode", "cycle_id", "generation"):
        assert evidence.get(key) == progress.get(key), (
            f"schema-4 active census evidence mismatch: {key}"
        )
    generation = progress.get("generation")
    assert isinstance(generation, int) and not isinstance(generation, bool) and generation >= 1

    raw_verification = evidence.get("raw_verification")
    assert isinstance(raw_verification, dict), "active census RAW verification is missing"
    assert raw_verification.get("mode") == "export_time_official_warehouse_bytes"
    raw_files = raw_verification.get("files")
    assert isinstance(raw_files, list), "active census RAW verification files are missing"
    verification_by_path: dict[str, dict] = {}
    for descriptor in raw_files:
        assert isinstance(descriptor, dict), "active census RAW descriptor is invalid"
        raw_path = descriptor.get("raw_path")
        assert isinstance(raw_path, str) and raw_path, "active census RAW path is missing"
        path = Path(raw_path)
        assert not path.is_absolute() and ".." not in path.parts, (
            "active census RAW path is unsafe"
        )
        assert raw_path not in verification_by_path, "active census RAW path is duplicated"
        _sha256(descriptor.get("sha256"), label="active census RAW")
        assert _nonnegative_integer(descriptor.get("bytes")), (
            "active census RAW byte count is invalid"
        )
        verification_by_path[raw_path] = descriptor
    assert list(verification_by_path) == sorted(verification_by_path), (
        "active census RAW verification is not sorted"
    )
    assert raw_verification.get("verified_files") == len(raw_files)
    assert raw_verification.get("verified_bytes") == sum(
        row["bytes"] for row in raw_files
    )

    taxonomy, taxonomy_sha, referenced_paths = _assert_cardinality_taxonomy(
        evidence.get("taxonomy"), verification_by_path=verification_by_path
    )
    boundary_status = progress.get("boundary")
    boundary_evidence = evidence.get("boundary")
    assert isinstance(boundary_status, dict), "active census boundary status is missing"
    assert isinstance(boundary_evidence, dict), "active census boundary evidence is missing"
    boundary_total = boundary_status.get("total_count")
    assert (
        isinstance(boundary_total, int)
        and not isinstance(boundary_total, bool)
        and boundary_total >= 0
    ), "active census boundary total is invalid"
    boundary_head = _sha256(
        boundary_status.get("head_ref_sha256"), label="active census boundary head"
    )
    opening_refs, opening_path = _assert_cardinality_boundary_capture(
        boundary_evidence.get("opening"),
        expected_total=boundary_total,
        expected_head_sha=boundary_head,
        verification_by_path=verification_by_path,
        label="active census opening boundary",
    )
    referenced_paths.add(opening_path)
    closing_capture = boundary_evidence.get("closing")
    closing_stable = False
    if closing_capture is not None:
        closing_refs, closing_path = _assert_cardinality_boundary_capture(
            closing_capture,
            expected_total=boundary_total,
            expected_head_sha=boundary_head,
            verification_by_path=verification_by_path,
            label="active census closing boundary",
        )
        referenced_paths.add(closing_path)
        closing_stable = opening_refs == closing_refs
    assert boundary_status.get("opening_evidence") == boundary_evidence.get("opening")
    assert boundary_status.get("closing_evidence") == closing_capture
    assert boundary_status.get("stable") == closing_stable, (
        "active census boundary stability mismatch"
    )
    membership_status = progress.get("membership")
    assert isinstance(membership_status, dict), "active census membership status missing"
    declared_union_sha = _sha256(
        membership_status.get("union_sha256"),
        label="active census declared membership union",
    )

    frontier = evidence.get("frontier")
    (
        current_mappings,
        duplicate_occurrences,
        duplicate_tender_occurrences,
        current_paths,
        state_counts,
    ) = (
        _assert_cardinality_nodes(
            frontier,
            taxonomy=taxonomy,
            verification_by_path=verification_by_path,
            exact_only=False,
            label="active census current frontier",
            generation=generation,
            boundary_total=boundary_total,
            union_sha256=declared_union_sha,
        )
    )
    referenced_paths.update(current_paths)
    candidate_mappings, pending_candidates, candidate_paths, _ = (
        _assert_cardinality_candidates(
            evidence.get("candidates"),
            verification_by_path=verification_by_path,
            label="active census current",
            cycle_id=str(progress["cycle_id"]),
            generation=generation,
        )
    )
    referenced_paths.update(candidate_paths)
    tender_to_ref = {tender_id: ref for ref, tender_id in current_mappings.items()}
    for reference, tender_id in candidate_mappings.items():
        existing = current_mappings.get(reference)
        assert existing is None or existing == tender_id, (
            "active census candidate reference mapping conflicts with a facet page"
        )
        existing_ref = tender_to_ref.get(tender_id)
        assert existing_ref is None or existing_ref == reference, (
            "active census candidate tender id maps to a second reference"
        )
        current_mappings[reference] = tender_id
        tender_to_ref[tender_id] = reference

    membership_evidence = evidence.get("membership")
    assert isinstance(membership_evidence, dict), "active census membership evidence missing"
    current_refs = sorted(current_mappings)
    assert membership_evidence.get("references") == current_refs, (
        "active census membership references differ from RAW replay"
    )
    expected_mapping_rows = [
        {"reference_number": ref, "tender_id": current_mappings[ref]}
        for ref in current_refs
    ]
    assert membership_evidence.get("mappings") == expected_mapping_rows, (
        "active census membership mapping differs from RAW replay"
    )
    union_sha = _reference_union_sha256(set(current_refs))
    bijection_sha = _cardinality_bijection_sha256(current_mappings)
    assert membership_evidence.get("union_sha256") == union_sha
    assert membership_evidence.get("bijection_sha256") == bijection_sha
    integrity_errors = membership_status.get("integrity_errors")
    assert isinstance(integrity_errors, list), "active census integrity errors are missing"
    unexplained = max(0, boundary_total - len(current_refs))
    assert membership_status.get("observed_unique") == len(current_refs)
    assert membership_status.get("unexplained_unique") == unexplained
    assert membership_status.get("pending_candidates") == pending_candidates
    assert membership_status.get("union_sha256") == union_sha
    assert membership_status.get("bijection_sha256") == bijection_sha
    assert membership_status.get("duplicate_references") == duplicate_occurrences
    assert membership_status.get("duplicate_tender_ids") == duplicate_tender_occurrences
    assert membership_status.get("integrity_error_count") == len(integrity_errors)
    membership_reported_complete = bool(
        len(current_refs) == boundary_total
        and unexplained == 0
        and not integrity_errors
    )
    assert membership_status.get("complete") == membership_reported_complete
    membership_sealed = bool(membership_reported_complete and pending_candidates == 0)

    taxonomy_status = progress.get("taxonomy")
    assert isinstance(taxonomy_status, dict), "active census taxonomy status is missing"
    assert taxonomy_status.get("complete") is True
    assert taxonomy_status.get("sha256") == taxonomy_sha
    assert taxonomy_status.get("kinds") == {
        kind: len(values) for kind, values in taxonomy.items()
    }
    frontier_status = progress.get("frontier")
    assert isinstance(frontier_status, dict), "active census frontier status is missing"
    assert isinstance(frontier, dict), "active census current frontier is missing"
    nodes = frontier.get("nodes")
    pages = frontier.get("pages")
    assert isinstance(nodes, list) and isinstance(pages, list), (
        "active census current frontier node/page ledger is missing"
    )
    assert frontier_status.get("nodes_total") == len(nodes)
    for status_key, node_state in (
        ("pending", "pending"),
        ("split", "split"),
        ("exact", "exact"),
        ("blocked", "blocked"),
        ("superseded_by_cardinality", "superseded_by_cardinality"),
    ):
        assert frontier_status.get(status_key) == state_counts.get(node_state, 0)
    assert frontier_status.get("accepted_pages") == len(pages)
    assert frontier_status.get("clear_for_authority") == (
        not any(state_counts.get(state, 0) for state in ("pending", "blocked"))
    ), "active census frontier clear-for-authority mismatch"

    proofs = evidence.get("generation_proofs")
    proof_status = progress.get("generation_proofs")
    assert isinstance(proofs, list), "active census generation proofs are missing"
    assert isinstance(proof_status, dict), "active census proof status is missing"
    proof_generations: list[int] = []
    proof_ordinals: list[int] = []
    proof_chains: list[int] = []
    proof_run_ids: list[str] = []
    proof_paths_by_generation: list[set[str]] = []
    matching: list[tuple[int, int]] = []
    for proof in proofs:
        assert isinstance(proof, dict), "active census proof row is invalid"
        proof_generation = proof.get("generation")
        proof_ordinal = proof.get("convergence_ordinal")
        proof_chain = proof.get("chain_number")
        assert isinstance(proof_generation, int) and proof_generation >= 1
        assert isinstance(proof_ordinal, int) and proof_ordinal >= 1
        assert isinstance(proof_chain, int) and not isinstance(proof_chain, bool)
        assert proof_chain >= 1, "active census proof chain is invalid"
        assert proof.get("superseded_at") is None, (
            "active census active proof is marked superseded"
        )
        assert proof.get("superseded_reason") is None, (
            "active census active proof has a supersession reason"
        )
        proof_run_id = proof.get("run_id")
        assert isinstance(proof_run_id, str) and proof_run_id, (
            "active census proof run id is missing"
        )
        assert proof.get("taxonomy_sha256") == taxonomy_sha
        proof_boundary_total = proof.get("boundary_total_count")
        assert (
            isinstance(proof_boundary_total, int)
            and not isinstance(proof_boundary_total, bool)
            and proof_boundary_total >= 0
        ), "active census proof boundary total is invalid"
        proof_boundary_head = proof.get("boundary_head_ref_sha256")
        assert isinstance(proof_boundary_head, str), (
            "active census proof boundary head is invalid"
        )
        proof_boundary = proof.get("boundary_evidence")
        assert isinstance(proof_boundary, dict), "active census proof boundary missing"
        proof_paths: set[str] = set()
        proof_open_refs, proof_open_path = _assert_cardinality_boundary_capture(
            proof_boundary.get("opening"),
            expected_total=proof_boundary_total,
            expected_head_sha=proof_boundary_head,
            verification_by_path=verification_by_path,
            label=f"active census proof {proof_generation} opening",
        )
        proof_close_refs, proof_close_path = _assert_cardinality_boundary_capture(
            proof_boundary.get("closing"),
            expected_total=proof_boundary_total,
            expected_head_sha=proof_boundary_head,
            verification_by_path=verification_by_path,
            label=f"active census proof {proof_generation} closing",
        )
        assert proof_open_refs == proof_close_refs, "active census proof boundary drift"
        proof_paths.update((proof_open_path, proof_close_path))
        proof_mapping, _, _, node_paths, _ = _assert_cardinality_nodes(
            proof.get("node_evidence"),
            taxonomy=taxonomy,
            verification_by_path=verification_by_path,
            exact_only=False,
            label=f"active census proof {proof_generation}",
            generation=proof_generation,
            boundary_total=proof_boundary_total,
            union_sha256=_sha256(
                proof.get("union_sha256"),
                label=f"active census proof {proof_generation} union",
            ),
        )
        proof_paths.update(node_paths)
        proof_candidates, proof_pending, proof_candidate_paths, _ = (
            _assert_cardinality_candidates(
                proof.get("candidate_evidence"),
                verification_by_path=verification_by_path,
                label=f"active census proof {proof_generation}",
                cycle_id=str(progress["cycle_id"]),
                generation=proof_generation,
            )
        )
        assert proof_pending == 0, "active census proof has a pending candidate"
        proof_paths.update(proof_candidate_paths)
        proof_tender_ids = {value: key for key, value in proof_mapping.items()}
        for reference, tender_id in proof_candidates.items():
            known_id = proof_mapping.get(reference)
            known_ref = proof_tender_ids.get(tender_id)
            assert known_id is None or known_id == tender_id, (
                "active census proof candidate reference mapping conflicts"
            )
            assert known_ref is None or known_ref == reference, (
                "active census proof candidate tender-id mapping conflicts"
            )
            proof_mapping[reference] = tender_id
            proof_tender_ids[tender_id] = reference
        proof_refs = sorted(proof_mapping)
        assert proof.get("references") == proof_refs, (
            "active census proof references differ from RAW replay"
        )
        assert proof.get("mappings") == [
            {"reference_number": ref, "tender_id": proof_mapping[ref]}
            for ref in proof_refs
        ], "active census proof mappings differ from RAW replay"
        proof_union_sha = _reference_union_sha256(set(proof_refs))
        proof_bijection_sha = _cardinality_bijection_sha256(proof_mapping)
        assert proof.get("union_unique") == len(proof_refs)
        assert proof.get("union_unique") == proof.get("boundary_total_count"), (
            "active census proof union does not equal N"
        )
        assert proof.get("union_sha256") == proof_union_sha
        assert proof.get("bijection_sha256") == proof_bijection_sha
        proof_generations.append(proof_generation)
        proof_ordinals.append(proof_ordinal)
        proof_chains.append(proof_chain)
        proof_run_ids.append(proof_run_id)
        proof_paths_by_generation.append(proof_paths)
        referenced_paths.update(proof_paths)
        if (
            proof.get("boundary_total_count") == boundary_total
            and proof.get("boundary_head_ref_sha256") == boundary_head
            and proof_union_sha == union_sha
            and proof_bijection_sha == bijection_sha
        ):
            matching.append((proof_generation, proof_ordinal))

    expected_generations = [generation - 1, generation]
    assert len(proofs) == 2, "active census authority requires exactly two proofs"
    assert proof_generations == expected_generations, (
        "active census authority proofs are not adjacent generations ending current"
    )
    assert proof_ordinals == [1, 2], (
        "active census authority proof ordinals must be exactly [1, 2]"
    )
    chain_number = proof_status.get("chain_number")
    assert isinstance(chain_number, int) and not isinstance(chain_number, bool)
    assert chain_number >= 1, "active census proof status chain is invalid"
    assert proof_chains == [chain_number, chain_number], (
        "active census authority proofs are interleaved across proof chains"
    )
    assert len(set(proof_run_ids)) == 2, (
        "active census authority proofs reuse a run id"
    )
    for index, proof_paths in enumerate(proof_paths_by_generation):
        for earlier_paths in proof_paths_by_generation[:index]:
            assert proof_paths.isdisjoint(earlier_paths), (
                "active census proof generations reuse RAW paths"
            )
    matching_generations = [item[0] for item in matching]
    matching_ordinals = [item[1] for item in matching]
    proof_authoritative = bool(
        matching_generations == expected_generations
        and matching_ordinals == [1, 2]
    )
    ledger = evidence.get("generation_proof_ledger")
    assert isinstance(ledger, list), "active census generation proof ledger is missing"
    active_ledger: list[dict] = []
    superseded_count = 0
    ledger_order: list[tuple[int, int, int]] = []
    for row in ledger:
        assert isinstance(row, dict), "active census proof ledger row is invalid"
        row_generation = row.get("generation")
        row_ordinal = row.get("convergence_ordinal")
        row_chain = row.get("chain_number")
        assert isinstance(row_generation, int) and row_generation >= 1
        assert isinstance(row_ordinal, int) and row_ordinal >= 1
        assert isinstance(row_chain, int) and row_chain >= 1
        ledger_order.append((row_chain, row_ordinal, row_generation))
        superseded_at = row.get("superseded_at")
        superseded_reason = row.get("superseded_reason")
        if superseded_at is None:
            assert superseded_reason is None, (
                "active census unsuperseded proof has a supersession reason"
            )
            assert row_chain == chain_number, (
                "active census unsuperseded proof belongs to a stale chain"
            )
            active_ledger.append(row)
        else:
            assert isinstance(superseded_at, str) and superseded_at
            assert isinstance(superseded_reason, str) and superseded_reason
            assert row_chain < chain_number, (
                "active census superseded proof is not from an older chain"
            )
            superseded_count += 1
    assert ledger_order == sorted(ledger_order), (
        "active census proof ledger is not in canonical order"
    )
    assert [
        (
            row.get("generation"),
            row.get("convergence_ordinal"),
            row.get("chain_number"),
        )
        for row in active_ledger
    ] == list(zip(proof_generations, proof_ordinals, proof_chains)), (
        "active census proof ledger disagrees with active proof payloads"
    )
    assert proof_status.get("required") == 2
    assert proof_status.get("recorded") == len(proofs)
    assert proof_status.get("recorded_total") == len(ledger)
    assert proof_status.get("superseded") == superseded_count
    assert proof_status.get("matching_current_union") == len(matching)
    assert proof_status.get("distinct_generations") == len(set(matching_generations))
    assert proof_status.get("generations") == matching_generations
    assert proof_status.get("ordinals") == matching_ordinals
    assert proof_status.get("authoritative") == proof_authoritative

    frontier_clear = not any(
        state_counts.get(state, 0) for state in ("pending", "blocked", "error")
    )
    authoritative = bool(
        progress.get("phase") == "authoritative"
        and evidence.get("phase") == "authoritative"
        and boundary_status.get("stable")
        and membership_sealed
        and frontier_clear
        and proof_authoritative
    )
    for key in (
        "union_authoritative",
        "partition_authoritative",
        "absence_authoritative",
        "complete",
    ):
        assert progress.get(key) == authoritative, f"active census {key} mismatch"
        assert evidence.get(key) == authoritative, f"active census evidence {key} mismatch"
    targets = progress.get("targets")
    assert isinstance(targets, dict), "active census target status is missing"
    for key in ("total", "observed", "absent", "resolved"):
        assert _nonnegative_integer(targets.get(key)), (
            f"active census target {key} is invalid"
        )
    assert targets["resolved"] == targets["observed"] + targets["absent"]
    assert targets["resolved"] <= targets["total"]
    completion_authoritative = bool(
        authoritative and targets["resolved"] == targets["total"]
    )
    assert progress.get("completion_authoritative") == completion_authoritative
    assert evidence.get("completion_authoritative") == completion_authoritative
    assert set(verification_by_path) == referenced_paths, (
        "active census RAW verification has stale or missing pointers"
    )


def assert_active_cardinality_progress_summary(progress: object) -> None:
    """Validate schema-4 progress even while its current cycle is still partial."""

    assert isinstance(progress, dict), "schema-4 active census progress is missing"
    assert progress.get("schema_version") == CARDINALITY_SEAL_SCHEMA_VERSION
    assert progress.get("strategy") == CARDINALITY_SEAL_STRATEGY
    assert progress.get("mode") == CARDINALITY_SEAL_MODE
    assert isinstance(progress.get("cycle_id"), str) and progress["cycle_id"]
    generation = progress.get("generation")
    assert isinstance(generation, int) and not isinstance(generation, bool) and generation >= 1
    assert progress.get("phase") in {
        "taxonomy",
        "opening",
        "frontier",
        "candidates",
        "closing",
        "authoritative",
    }
    boundary = progress.get("boundary")
    membership = progress.get("membership")
    frontier = progress.get("frontier")
    taxonomy = progress.get("taxonomy")
    proof_status = progress.get("generation_proofs")
    targets = progress.get("targets")
    assert isinstance(boundary, dict), "active census boundary status is missing"
    assert isinstance(membership, dict), "active census membership status is missing"
    assert isinstance(frontier, dict), "active census frontier status is missing"
    assert isinstance(taxonomy, dict), "active census taxonomy status is missing"
    assert isinstance(proof_status, dict), (
        "active census generation proofs status is missing"
    )
    assert isinstance(targets, dict), "active census targets status is missing"
    total = boundary.get("total_count")
    assert total is None or _nonnegative_integer(total)
    head_sha = boundary.get("head_ref_sha256")
    if head_sha is not None:
        _sha256(head_sha, label="active census boundary head")
    assert isinstance(boundary.get("stable"), bool)
    for key in (
        "observed_unique",
        "pending_candidates",
        "duplicate_references",
        "duplicate_tender_ids",
        "integrity_error_count",
    ):
        assert _nonnegative_integer(membership.get(key)), f"active census {key} is invalid"
    unexplained = membership.get("unexplained_unique")
    assert unexplained is None or _nonnegative_integer(unexplained)
    assert isinstance(membership.get("complete"), bool)
    for key in ("union_sha256", "bijection_sha256"):
        if membership.get(key) is not None:
            _sha256(membership[key], label=f"active census {key}")
    for key in (
        "nodes_total",
        "pending",
        "split",
        "exact",
        "blocked",
        "superseded_by_cardinality",
        "accepted_pages",
        "page_ceiling_switches",
    ):
        assert _nonnegative_integer(frontier.get(key)), f"active census frontier {key}"
    assert isinstance(frontier.get("clear_for_authority"), bool), (
        "active census frontier clear-for-authority is invalid"
    )
    assert frontier["clear_for_authority"] == bool(
        frontier["pending"] == 0 and frontier["blocked"] == 0
    ), "active census frontier clear-for-authority arithmetic mismatch"
    assert isinstance(taxonomy.get("complete"), bool)
    if taxonomy.get("sha256") is not None:
        _sha256(taxonomy["sha256"], label="active census taxonomy")
    for key in (
        "required",
        "recorded",
        "recorded_total",
        "superseded",
        "chain_number",
        "matching_current_union",
        "distinct_generations",
    ):
        assert _nonnegative_integer(proof_status.get(key)), (
            f"active census proof status {key} is invalid"
        )
    assert proof_status.get("required") == 2
    assert isinstance(proof_status.get("authoritative"), bool)
    for key in ("total", "observed", "absent", "resolved"):
        assert _nonnegative_integer(targets.get(key)), f"active census target {key}"
    assert targets["resolved"] == targets["observed"] + targets["absent"]
    assert targets["resolved"] <= targets["total"]
    for key in (
        "union_authoritative",
        "partition_authoritative",
        "absence_authoritative",
        "completion_authoritative",
        "complete",
    ):
        assert isinstance(progress.get(key), bool), f"active census {key} is invalid"
    assert progress["partition_authoritative"] == progress["union_authoritative"]
    assert progress["absence_authoritative"] == progress["union_authoritative"]
    assert progress["complete"] == progress["union_authoritative"]
    if progress["union_authoritative"]:
        assert progress["phase"] == "authoritative"
        assert boundary["stable"]
        assert membership["complete"]
        assert membership["pending_candidates"] == 0
        assert membership["integrity_error_count"] == 0
        assert proof_status["authoritative"]
        assert frontier["clear_for_authority"]
    assert progress["completion_authoritative"] == bool(
        progress["union_authoritative"] and targets["resolved"] == targets["total"]
    )


def _assert_single_day_mirror_cover_contract(
    refinement: dict,
    *,
    coverage_complete: bool,
    cycle_terminal: bool,
) -> None:
    """Validate the additive, bounded two-ended cover for dense area leaves."""

    extension_keys = ("nodes_mirror_pending", "mirror_pages", "mirror_cover")
    extension_presence = [key in refinement for key in extension_keys]
    if not any(extension_presence):
        # The contract must land before the first mirror-aware producer snapshot.
        # Legacy partial progress may omit the whole additive extension, but a
        # terminal snapshot may not use that compatibility path.
        assert not coverage_complete and not cycle_terminal, (
            "schema-5 terminal refinement is missing mirror-cover status"
        )
        return
    assert all(extension_presence), (
        "schema-5 mirror-cover extension is only partially published"
    )

    nodes_mirror_pending = refinement.get("nodes_mirror_pending")
    mirror_pages = refinement.get("mirror_pages")
    assert _nonnegative_integer(nodes_mirror_pending), (
        "schema-5 mirror-cover pending-node count is invalid"
    )
    assert _nonnegative_integer(mirror_pages), (
        "schema-5 mirror-cover page count is invalid"
    )
    assert isinstance(mirror_pages, int) and not isinstance(mirror_pages, bool)
    mirror_pages_count = mirror_pages

    mirror = refinement.get("mirror_cover")
    assert isinstance(mirror, dict), "schema-5 mirror-cover status is invalid"
    version = mirror.get("version")
    assert (
        isinstance(version, int)
        and not isinstance(version, bool)
        and version == SINGLE_DAY_MIRROR_COVER_VERSION
    ), "schema-5 mirror-cover version mismatch"
    assert mirror.get("strategy") == SINGLE_DAY_MIRROR_COVER_STRATEGY, (
        "schema-5 mirror-cover strategy mismatch"
    )
    query_hash = _sha256(
        mirror.get("query_hash"),
        label="schema-5 mirror-cover query",
    )
    assert query_hash == SINGLE_DAY_MIRROR_COVER_QUERY_SHA256, (
        "schema-5 mirror-cover query contract mismatch"
    )

    count_keys = (
        "covers_total",
        "migrations_total",
        "covers_pending",
        "covers_covered",
        "covers_blocked",
        "covers_failed",
    )
    for key in count_keys:
        assert _nonnegative_integer(mirror.get(key)), (
            f"schema-5 mirror-cover {key} is invalid"
        )
    assert mirror["covers_total"] == (
        mirror["covers_pending"]
        + mirror["covers_covered"]
        + mirror["covers_blocked"]
        + mirror["covers_failed"]
    ), "schema-5 mirror-cover state arithmetic mismatch"
    assert mirror["migrations_total"] <= mirror["covers_total"], (
        "schema-5 mirror-cover migration count exceeds covers"
    )
    assert nodes_mirror_pending == mirror["covers_pending"], (
        "schema-5 mirror-cover pending-node arithmetic mismatch"
    )
    assert mirror_pages_count <= (
        SINGLE_DAY_MIRROR_COVER_MAX_PAGES_PER_NODE * mirror["covers_total"]
    ), "schema-5 mirror-cover page count exceeds bounded generations"
    assert mirror_pages_count % 2 == 0, (
        "schema-5 mirror-cover page arithmetic is not pair-aligned"
    )

    evidence = mirror.get("evidence")
    assert isinstance(evidence, dict), "schema-5 mirror-cover evidence is invalid"
    terminal_count = (
        mirror["covers_covered"]
        + mirror["covers_blocked"]
        + mirror["covers_failed"]
    )
    assert len(evidence) == terminal_count, (
        "schema-5 mirror-cover terminal evidence count mismatch"
    )
    evidence_state_counts = {state: 0 for state in ("covered", "blocked", "failed")}
    terminal_pages = 0
    for node_id, item in evidence.items():
        label = f"schema-5 mirror-cover evidence {node_id}"
        assert isinstance(node_id, str) and node_id.strip(), (
            "schema-5 mirror-cover evidence node id is invalid"
        )
        assert isinstance(item, dict), f"{label} is invalid"
        evidence_version = item.get("version")
        assert (
            isinstance(evidence_version, int)
            and not isinstance(evidence_version, bool)
            and evidence_version == SINGLE_DAY_MIRROR_COVER_VERSION
        ), (
            f"{label} version mismatch"
        )
        assert item.get("strategy") == SINGLE_DAY_MIRROR_COVER_STRATEGY, (
            f"{label} strategy mismatch"
        )
        evidence_query_hash = _sha256(
            item.get("query_hash"),
            label=f"{label} query",
        )
        assert evidence_query_hash == query_hash, f"{label} query mismatch"
        state = item.get("state")
        assert state in evidence_state_counts, f"{label} state is invalid"
        evidence_state_counts[str(state)] += 1

        generation = item.get("generation")
        assert (
            isinstance(generation, int)
            and not isinstance(generation, bool)
            and 1 <= generation <= SINGLE_DAY_MIRROR_COVER_MAX_GENERATION
        ), f"{label} generation is invalid"
        generation_hashes = item.get("generation_evidence_sha256")
        assert isinstance(generation_hashes, list), (
            f"{label} generation evidence hashes are invalid"
        )
        for evidence_hash in generation_hashes:
            _sha256(evidence_hash, label=f"{label} generation evidence")
        assert len(generation_hashes) == len(set(generation_hashes)), (
            f"{label} reuses a generation evidence hash"
        )

        if state in {"covered", "blocked"}:
            assert generation in SINGLE_DAY_MIRROR_COVER_GENERATIONS, (
                f"{label} terminal generation is invalid"
            )
            if state == "blocked":
                assert generation == SINGLE_DAY_MIRROR_COVER_MAX_GENERATION, (
                    f"{label} blocked before the maximum generation"
                )
            baseline_generation = item.get("baseline_generation")
            assert (
                isinstance(baseline_generation, int)
                and not isinstance(baseline_generation, bool)
                and baseline_generation == generation - 1
            ), (
                f"{label} baseline generation mismatch"
            )
            assert len(generation_hashes) == generation, (
                f"{label} generation evidence sequence mismatch"
            )
            final_total = item.get("final_total_count")
            assert (
                isinstance(final_total, int)
                and not isinstance(final_total, bool)
                and SINGLE_DAY_MIRROR_COVER_MIN_TOTAL
                <= final_total
                <= SINGLE_DAY_MIRROR_COVER_MAX_TOTAL
            ), f"{label} final count is invalid"
            _sha256(item.get("final_union_sha256"), label=f"{label} final union")
            _sha256(
                item.get("final_bijection_sha256"),
                label=f"{label} final bijection",
            )
            terminal_pages += 4 * generation
            continue

        assert len(generation_hashes) == generation - 1, (
            f"{label} failed-generation evidence sequence mismatch"
        )
        assert item.get("final_total_count") is None, (
            f"{label} failed cover claims a final count"
        )
        assert item.get("final_union_sha256") is None, (
            f"{label} failed cover claims a final union"
        )
        assert item.get("final_bijection_sha256") is None, (
            f"{label} failed cover claims a final bijection"
        )
        failed_direction = item.get("failed_direction")
        assert failed_direction in {"DESC", "ASC"}, (
            f"{label} failed direction is invalid"
        )
        failure_count = item.get("failure_count")
        assert (
            isinstance(failure_count, int)
            and not isinstance(failure_count, bool)
            and failure_count >= 2
        ), f"{label} failure count is invalid"
        failure_reason = item.get("failure_reason")
        assert (
            isinstance(failure_reason, str)
            and failure_reason.startswith("mirror_cover_capture_attempts_exhausted:")
        ), f"{label} failure reason is invalid"
        partial_pages = item.get("partial_pages")
        assert isinstance(partial_pages, list), f"{label} partial pages are invalid"
        expected_partial_pages = (
            [] if failed_direction == "DESC" else [("mirror_desc", 1), ("mirror_desc", 2)]
        )
        actual_partial_pages: list[tuple[str, int]] = []
        partial_epochs: set[str] = set()
        for page in partial_pages:
            assert isinstance(page, dict), f"{label} partial page is invalid"
            capture_kind = page.get("capture_kind")
            page_number = page.get("page_number")
            assert isinstance(capture_kind, str) and _nonnegative_integer(page_number), (
                f"{label} partial page identity is invalid"
            )
            assert isinstance(page_number, int) and not isinstance(page_number, bool)
            page_number_int = page_number
            assert 1 <= page_number_int <= 2, (
                f"{label} partial page exceeds page 2"
            )
            actual_partial_pages.append((capture_kind, page_number_int))
            _sha256(page.get("sha256"), label=f"{label} partial page")
            epoch = page.get("capture_epoch_id")
            assert isinstance(epoch, str) and epoch.strip(), (
                f"{label} partial page epoch is invalid"
            )
            partial_epochs.add(epoch)
            assert parse_iso_datetime(page.get("accepted_at")) is not None, (
                f"{label} partial page timestamp is invalid"
            )
        assert actual_partial_pages == expected_partial_pages, (
            f"{label} partial page sequence mismatch"
        )
        assert len(partial_epochs) <= 1, f"{label} partial page epoch mismatch"
        terminal_pages += 4 * len(generation_hashes) + len(partial_pages)

    assert evidence_state_counts["covered"] == mirror["covers_covered"], (
        "schema-5 mirror-cover covered evidence arithmetic mismatch"
    )
    assert evidence_state_counts["blocked"] == mirror["covers_blocked"], (
        "schema-5 mirror-cover blocked evidence arithmetic mismatch"
    )
    assert evidence_state_counts["failed"] == mirror["covers_failed"], (
        "schema-5 mirror-cover failed evidence arithmetic mismatch"
    )
    if mirror["covers_pending"]:
        assert mirror_pages_count >= terminal_pages, (
            "schema-5 mirror-cover page count omits terminal evidence"
        )
    else:
        assert mirror_pages_count == terminal_pages, (
            "schema-5 mirror-cover page arithmetic mismatch"
        )

    if coverage_complete or cycle_terminal:
        assert mirror["covers_pending"] == 0, (
            "schema-5 terminal mirror-cover still has pending covers"
        )
        assert mirror["covers_blocked"] == 0, (
            "schema-5 terminal mirror-cover still has blocked covers"
        )
        assert mirror["covers_failed"] == 0, (
            "schema-5 terminal mirror-cover still has failed covers"
        )
        assert mirror["covers_covered"] == mirror["covers_total"], (
            "schema-5 terminal mirror-cover has uncovered covers"
        )


def _assert_single_day_refinement_contract(
    refinement: object,
    *,
    covered_interval_count: int,
    blocked_interval_count: int,
    refined_covered_interval_ids: set[str],
    refined_blocked_interval_ids: set[str],
    refined_blocked_interval_reasons: dict[str, str],
    coverage_complete: bool,
    cycle_terminal: bool,
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

    mirror_extension_present = any(
        key in refinement
        for key in ("nodes_mirror_pending", "mirror_pages", "mirror_cover")
    )
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
    ) + (
        ("nodes_mirror_pending", "mirror_pages")
        if mirror_extension_present
        else ()
    )
    for key in count_keys:
        assert _nonnegative_integer(refinement.get(key)), (
            f"schema-5 single-day refinement {key} is invalid"
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
        + (
            refinement["nodes_mirror_pending"]
            if mirror_extension_present
            else 0
        )
        + refinement["nodes_exact"]
        + refinement["nodes_blocked"]
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
    pages_requested = (
        refinement["accepted_pages"]
        + refinement["probe_pages"]
        + (refinement["mirror_pages"] if mirror_extension_present else 0)
    )
    assert (max_page_requested == 0) == (pages_requested == 0), (
        "schema-5 single-day refinement page metrics are inconsistent"
    )
    assert pages_requested >= refinement["nodes_exact"], (
        "schema-5 single-day refinement has fewer page proofs than exact nodes"
    )
    assert (
        refinement["accepted_pages"] + refinement["probe_pages"]
        <= 4 * refinement["nodes_total"]
    ), (
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

    _assert_single_day_mirror_cover_contract(
        refinement,
        coverage_complete=coverage_complete,
        cycle_terminal=cycle_terminal,
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
            reconciliation["cells_generation_2"]
            + reconciliation["cells_generation_3"]
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
            if entry_generation == 3:
                prior_history = generation_history[-2]
                latest_history = generation_history[-1]
                assert any(
                    prior_history[key] != latest_history[key]
                    for key in (
                        "union_unique",
                        "union_sha256",
                        "bijection_sha256",
                    )
                ), "schema-5 temporal reconciliation generation-3 history has no drift"
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
                    "schema-5 converged temporal reconciliation reports failure"
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
        assert states["collecting_generation"] == reconciliation["cells_collecting"]
        assert states["awaiting_day_close"] == reconciliation["cells_awaiting_day_close"]
        assert states["sealed"] == reconciliation["cells_sealed"]
        assert states["blocked"] == reconciliation["cells_blocked"]
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
        if mirror_extension_present:
            assert refinement["nodes_mirror_pending"] == 0, (
                "schema-5 terminal refinement still has mirror-pending nodes"
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


def assert_active_interval_coverage_contract(
    progress: object,
    evidence: object = None,
) -> None:
    """Validate finite progressive interval coverage without snapshot authority."""

    assert isinstance(progress, dict), "schema-5 interval coverage is missing"
    assert progress.get("schema_version") == INTERVAL_COVERAGE_SCHEMA_VERSION
    assert progress.get("strategy") == INTERVAL_COVERAGE_STRATEGY, (
        "schema-5 interval coverage strategy mismatch"
    )
    assert progress.get("mode") == INTERVAL_COVERAGE_MODE, (
        "schema-5 interval coverage mode mismatch"
    )
    assert evidence is None, "schema-5 interval coverage cannot publish authority evidence"
    assert "evidence_asset" not in progress, (
        "schema-5 interval coverage cannot own an authority evidence asset"
    )
    assert isinstance(progress.get("cycle_id"), str) and progress["cycle_id"], (
        "schema-5 interval coverage cycle_id is missing"
    )
    assert progress.get("phase") in {
        "sweeping",
        "complete",
        "complete_with_gaps",
    }, "schema-5 interval coverage phase is invalid"
    assert isinstance(progress.get("cycle_terminal"), bool), (
        "schema-5 interval terminal flag is invalid"
    )
    assert progress.get("complete") is False, (
        "schema-5 top-level complete must remain false"
    )

    for key in (
        "instantaneous_snapshot_authoritative",
        "snapshot_authoritative",
        "union_authoritative",
        "partition_authoritative",
        "absence_authoritative",
        "completion_authoritative",
    ):
        assert progress.get(key) is False, (
            f"schema-5 interval coverage cannot claim {key}"
        )

    domain = progress.get("coverage_domain")
    assert isinstance(domain, dict), "schema-5 coverage domain is missing"
    assert domain.get("field") == "lastOfferPresentationDate", (
        "schema-5 coverage field mismatch"
    )
    assert domain.get("from_day") == ACTIVE_INTERVAL_DOMAIN_START, (
        "schema-5 coverage domain start mismatch"
    )
    assert domain.get("to_day_exclusive") == ACTIVE_INTERVAL_DOMAIN_END_EXCLUSIVE, (
        "schema-5 coverage domain end mismatch"
    )
    assert domain.get("timezone") == "Asia/Riyadh", (
        "schema-5 coverage timezone mismatch"
    )
    domain_start = date.fromisoformat(ACTIVE_INTERVAL_DOMAIN_START)
    domain_end = date.fromisoformat(ACTIVE_INTERVAL_DOMAIN_END_EXCLUSIVE)
    units_total = (domain_end - domain_start).days
    assert domain.get("units_total") == units_total, (
        "schema-5 coverage domain unit count mismatch"
    )
    _sha256(domain.get("query_hash"), label="schema-5 coverage query")

    coverage = progress.get("coverage")
    assert isinstance(coverage, dict), "schema-5 coverage summary is missing"
    intervals = coverage.get("intervals")
    assert isinstance(intervals, list), "schema-5 coverage intervals are invalid"
    seen_interval_ids: set[str] = set()
    previous_end: date | None = None
    derived_units = {"covered": 0, "terminal_gap": 0}
    derived_leaves = {"covered": 0, "terminal_gap": 0}
    refined_covered_intervals = 0
    refined_blocked_intervals = 0
    refined_covered_interval_ids: set[str] = set()
    refined_blocked_interval_ids: set[str] = set()
    refined_blocked_interval_reasons: dict[str, str] = {}
    for index, interval in enumerate(intervals):
        label = f"schema-5 coverage interval {index}"
        assert isinstance(interval, dict), f"{label} is invalid"
        interval_id = interval.get("interval_id")
        assert isinstance(interval_id, str) and interval_id, (
            f"{label} id is missing"
        )
        assert interval_id not in seen_interval_ids, (
            "schema-5 coverage interval id is duplicated"
        )
        seen_interval_ids.add(interval_id)
        try:
            from_day = date.fromisoformat(str(interval.get("from_day") or ""))
            to_day = date.fromisoformat(
                str(interval.get("to_day_exclusive") or "")
            )
        except ValueError as exc:
            raise AssertionError(f"{label} date is invalid") from exc
        assert to_day > from_day, f"{label} is empty or reversed"
        assert from_day >= domain_start, f"{label} escapes the coverage domain"
        assert to_day <= domain_end, f"{label} escapes the coverage domain"
        if previous_end is not None:
            assert from_day >= previous_end, (
                "schema-5 coverage intervals overlap or are out of order"
            )
        state = interval.get("state")
        assert state in derived_units, f"{label} state is invalid"
        units = (to_day - from_day).days
        assert _nonnegative_integer(interval.get("units")), (
            f"{label} unit count is invalid"
        )
        assert interval["units"] == units, f"{label} unit count mismatch"
        total_count = interval.get("total_count")
        assert total_count is None or _nonnegative_integer(total_count), (
            f"{label} total_count is invalid"
        )
        attempt_no = interval.get("attempt_no")
        assert (
            isinstance(attempt_no, int)
            and not isinstance(attempt_no, bool)
            and attempt_no > 0
        ), (
            f"{label} attempt_no is invalid"
        )
        first_observed_at = parse_iso_datetime(interval.get("first_observed_at"))
        last_observed_at = parse_iso_datetime(interval.get("last_observed_at"))
        assert first_observed_at is not None, (
            f"{label} first_observed_at is invalid"
        )
        assert last_observed_at is not None, (
            f"{label} last_observed_at is invalid"
        )
        assert first_observed_at <= last_observed_at, (
            f"{label} observation window is reversed"
        )
        terminal_reason = interval.get("terminal_reason")
        assert terminal_reason is None or (
            isinstance(terminal_reason, str) and terminal_reason.strip()
        ), f"{label} terminal_reason is invalid"
        if state == "terminal_gap":
            assert isinstance(terminal_reason, str) and terminal_reason.strip(), (
                f"{label} terminal gap reason is missing"
            )
        if terminal_reason == SINGLE_DAY_REFINEMENT_COVERED_REASON:
            assert state == "covered" and units == 1, (
                f"{label} refined covered marker is not a single covered day"
            )
            refined_covered_intervals += 1
            refined_covered_interval_ids.add(interval_id)
        if isinstance(terminal_reason, str) and terminal_reason.startswith(
            SINGLE_DAY_REFINEMENT_BLOCKED_PREFIXES
        ):
            assert state == "terminal_gap" and units == 1, (
                f"{label} refined blocked marker is not a single gap day"
            )
            assert any(
                terminal_reason.removeprefix(prefix).strip()
                for prefix in SINGLE_DAY_REFINEMENT_BLOCKED_PREFIXES
                if terminal_reason.startswith(prefix)
            ), f"{label} refined blocked marker has no reason"
            refined_blocked_intervals += 1
            refined_blocked_interval_ids.add(interval_id)
            refined_blocked_interval_reasons[interval_id] = next(
                terminal_reason.removeprefix(prefix).strip()
                for prefix in SINGLE_DAY_REFINEMENT_BLOCKED_PREFIXES
                if terminal_reason.startswith(prefix)
            )
        derived_units[str(state)] += units
        derived_leaves[str(state)] += 1
        previous_end = to_day

    terminal_units = sum(derived_units.values())
    assert terminal_units <= units_total, (
        "schema-5 terminal interval geometry exceeds the coverage domain"
    )
    derived_pending = units_total - terminal_units

    for key in (
        "units_covered",
        "units_gap",
        "units_pending",
        "leaves_covered",
        "leaves_gap",
    ):
        assert _nonnegative_integer(coverage.get(key)), (
            f"schema-5 coverage {key} is invalid"
        )
    assert coverage["units_covered"] == derived_units["covered"], (
        "schema-5 covered-unit arithmetic mismatch"
    )
    assert coverage["units_gap"] == derived_units["terminal_gap"], (
        "schema-5 gap-unit arithmetic mismatch"
    )
    assert coverage["units_pending"] == derived_pending, (
        "schema-5 pending-unit arithmetic mismatch"
    )
    assert coverage["leaves_covered"] == derived_leaves["covered"]
    assert coverage["leaves_gap"] == derived_leaves["terminal_gap"]
    assert _nonnegative_integer(coverage.get("leaves_pending")), (
        "schema-5 pending-leaf count is invalid"
    )
    assert (coverage["leaves_pending"] == 0) == (derived_pending == 0), (
        "schema-5 pending-leaf state disagrees with unvisited geometry"
    )
    _assert_percentage(
        coverage.get("coverage_percent"),
        100.0 * derived_units["covered"] / units_total,
        label="schema-5 interval coverage_percent",
    )
    _assert_percentage(
        coverage.get("traversal_percent"),
        100.0
        * (derived_units["covered"] + derived_units["terminal_gap"])
        / units_total,
        label="schema-5 interval traversal_percent",
    )
    assert _nonnegative_integer(coverage.get("geometry_error_count")), (
        "schema-5 interval geometry error count is invalid"
    )
    assert coverage["geometry_error_count"] == 0, (
        "schema-5 interval geometry reports unresolved errors"
    )
    expected_geometry_complete = bool(
        intervals
        and terminal_units == units_total
        and intervals[0]["from_day"] == ACTIVE_INTERVAL_DOMAIN_START
        and intervals[-1]["to_day_exclusive"]
        == ACTIVE_INTERVAL_DOMAIN_END_EXCLUSIVE
    )
    assert coverage.get("geometry_complete") is expected_geometry_complete, (
        "schema-5 interval geometry completion arithmetic mismatch"
    )
    assert _nonnegative_integer(coverage.get("identity_conflict_count")), (
        "schema-5 interval identity conflict count is invalid"
    )
    assert isinstance(coverage.get("raw_replay_valid"), bool), (
        "schema-5 interval RAW replay flag is invalid"
    )
    assert isinstance(coverage.get("complete"), bool), (
        "schema-5 coverage completion flag is invalid"
    )

    terminal = derived_pending == 0
    _assert_single_day_refinement_contract(
        progress.get("single_day_refinement"),
        covered_interval_count=refined_covered_intervals,
        blocked_interval_count=refined_blocked_intervals,
        refined_covered_interval_ids=refined_covered_interval_ids,
        refined_blocked_interval_ids=refined_blocked_interval_ids,
        refined_blocked_interval_reasons=refined_blocked_interval_reasons,
        coverage_complete=bool(coverage.get("complete")),
        cycle_terminal=terminal,
    )
    expected_complete = bool(
        terminal
        and derived_units["terminal_gap"] == 0
        and coverage["identity_conflict_count"] == 0
        and coverage["raw_replay_valid"] is True
    )
    assert progress["cycle_terminal"] == terminal, (
        "schema-5 interval terminal arithmetic mismatch"
    )
    assert coverage["complete"] == expected_complete, (
        "schema-5 interval completion arithmetic mismatch"
    )
    expected_phase = (
        "complete"
        if expected_complete
        else "complete_with_gaps"
        if terminal
        else "sweeping"
    )
    assert progress["phase"] == expected_phase, (
        "schema-5 interval phase arithmetic mismatch"
    )

    observations = progress.get("observations")
    assert isinstance(observations, dict), (
        "schema-5 interval observation summary is missing"
    )
    for key in (
        "unique_references",
        "observation_records",
        "duplicate_observations",
    ):
        assert _nonnegative_integer(observations.get(key)), (
            f"schema-5 interval observation {key} is invalid"
        )
    assert observations["observation_records"] >= observations["unique_references"]
    assert observations["duplicate_observations"] == (
        observations["observation_records"] - observations["unique_references"]
    ), "schema-5 interval duplicate observation arithmetic mismatch"
    _sha256(observations.get("union_sha256"), label="schema-5 observed set")
    assert observations.get("semantics") == (
        "observed_at_least_once_during_cell_observation_intervals"
    ), "schema-5 observation semantics are ambiguous"

    competition = progress.get("competition_progress")
    assert isinstance(competition, dict), (
        "schema-5 active competition progress is missing"
    )
    assert competition.get("basis") == (
        "cycle_opening_root_total_non_authoritative"
    ), "schema-5 active competition progress basis is invalid"
    assert competition.get("denominator_authoritative") is False, (
        "schema-5 opening competition denominator cannot be authoritative"
    )
    assert competition.get("completion_gate") == "coverage.complete", (
        "schema-5 competition percentage cannot become the completion gate"
    )
    opening_total = competition.get("opening_total")
    assert opening_total is None or _nonnegative_integer(opening_total), (
        "schema-5 opening competition total is invalid"
    )
    opening_required = bool(
        progress["cycle_terminal"]
        or coverage["units_covered"] > 0
        or coverage["units_gap"] > 0
        or observations["observation_records"] > 0
        or progress.get("frontier", {}).get("max_page_requested", 0) > 0
    )
    if opening_required:
        assert opening_total is not None, (
            "schema-5 progress requires a replayed opening competition denominator"
        )
    opening_evidence = competition.get("opening_evidence")
    if opening_total is None:
        assert opening_evidence is None, (
            "schema-5 opening competition evidence exists without a denominator"
        )
    else:
        assert isinstance(opening_evidence, dict), (
            "schema-5 opening competition evidence is missing"
        )
        assert (
            isinstance(opening_evidence.get("attempt_no"), int)
            and not isinstance(opening_evidence["attempt_no"], bool)
            and opening_evidence["attempt_no"] >= 1
        ), "schema-5 opening competition evidence attempt is invalid"
        assert opening_evidence.get("capture_kind") in {"probe", "accepted"}, (
            "schema-5 opening competition evidence kind is invalid"
        )
        assert isinstance(opening_evidence.get("raw_path"), str) and (
            opening_evidence["raw_path"]
        ), "schema-5 opening competition RAW pointer is missing"
        _sha256(
            opening_evidence.get("sha256"),
            label="schema-5 opening competition evidence",
        )
        assert parse_iso_datetime(opening_evidence.get("observed_at")) is not None, (
            "schema-5 opening competition evidence timestamp is invalid"
        )
    observed_unique = observations["unique_references"]
    assert competition.get("observed_unique") == observed_unique, (
        "schema-5 competition observation count mismatch"
    )
    expected_against_opening = (
        min(observed_unique, opening_total) if opening_total is not None else 0
    )
    expected_beyond_opening = (
        max(0, observed_unique - opening_total)
        if opening_total is not None
        else 0
    )
    assert competition.get("observed_against_opening_total") == (
        expected_against_opening
    ), "schema-5 opening-denominator observation arithmetic mismatch"
    assert competition.get("arrivals_or_drift_beyond_opening_total") == (
        expected_beyond_opening
    ), "schema-5 opening-denominator drift arithmetic mismatch"
    expected_competition_percent = (
        100.0 * expected_against_opening / opening_total
        if opening_total
        else 100.0
        if opening_total == 0
        else None
    )
    if expected_competition_percent is None:
        assert competition.get("scanned_percent") is None, (
            "schema-5 competition percentage requires an opening denominator"
        )
    else:
        _assert_percentage(
            competition.get("scanned_percent"),
            expected_competition_percent,
            label="schema-5 active competition scanned percent",
        )
    assert progress.get("official_active_scanned_unique") == observed_unique, (
        "schema-5 official active scanned count mismatch"
    )
    assert progress.get("official_active_scanned_percent") == (
        competition.get("scanned_percent")
    ), "schema-5 official active percentage mirror mismatch"
    assert progress.get("official_active_scanned_percent_basis") == (
        competition.get("basis")
    ), "schema-5 official active percentage basis mismatch"

    window = progress.get("observation_window")
    assert isinstance(window, dict), "schema-5 observation window is missing"
    started_at = parse_iso_datetime(window.get("started_at"))
    first_observed_at = parse_iso_datetime(window.get("first_observed_at"))
    last_observed_at = parse_iso_datetime(window.get("last_observed_at"))
    completed_at = parse_iso_datetime(window.get("completed_at"))
    assert started_at is not None, "schema-5 observation start is invalid"
    if first_observed_at is None or last_observed_at is None:
        assert first_observed_at is None and last_observed_at is None
        assert observations["observation_records"] == 0
    else:
        assert started_at <= first_observed_at <= last_observed_at
    assert (completed_at is not None) == terminal, (
        "schema-5 observation completion timestamp mismatch"
    )
    if completed_at is not None:
        assert completed_at >= (last_observed_at or started_at)

    targets = progress.get("targets")
    assert isinstance(targets, dict), "schema-5 interval targets are missing"
    for key in ("total", "observed", "absent", "resolved"):
        assert _nonnegative_integer(targets.get(key)), (
            f"schema-5 interval target {key} is invalid"
        )
    assert targets["absent"] == 0, (
        "schema-5 interval traversal cannot claim target absence"
    )
    assert targets["resolved"] == targets["observed"] <= targets["total"]

    last_authority = progress.get("last_authority")
    if last_authority is not None:
        assert isinstance(last_authority, dict), (
            "schema-5 historical schema-4 authority is invalid"
        )
        assert "evidence_asset" not in last_authority, (
            "schema-5 historical authority cannot publish an evidence asset"
        )
        assert "last_authority" not in last_authority, (
            "schema-5 historical authority is recursively nested"
        )
        assert selected_cardinality_authority(last_authority) is last_authority, (
            "schema-5 historical authority is not a schema-4 seal"
        )
        assert last_authority.get("cycle_id") != progress["cycle_id"], (
            "schema-5 historical authority belongs to the current interval cycle"
        )
        assert_active_cardinality_progress_summary(last_authority)


def assert_active_hybrid_scan_contract(
    progress: dict,
    evidence: object,
) -> None:
    """Validate schema-3 bootstrap + date partition + residual union authority."""

    assert progress.get("mode") == "official_active_hybrid_union", (
        "schema-3 active scan mode mismatch"
    )
    for key in (
        "target_count",
        "targets_observed_unique",
        "targets_resolved_unique",
        "targets_absent_after_full_partitions",
        "ranges_total",
        "ranges_pending",
        "ranges_split",
        "ranges_exact",
        "ranges_blocked_single_day",
        "official_active_scanned_unique",
        "official_active_scanned_lifetime_high_watermark",
        "official_active_generation_scanned_unique",
        "accepted_pages_current_generation",
        "accepted_records_current_generation",
        "partition_duplicate_records",
        "leaf_integrity_error_count",
        "range_geometry_error_count",
    ):
        assert _nonnegative_integer(progress.get(key)), (
            f"schema-3 active date scan {key} is invalid"
        )
    target_count = progress["target_count"]
    observed = progress["targets_observed_unique"]
    resolved = progress["targets_resolved_unique"]
    absent = progress["targets_absent_after_full_partitions"]
    assert observed <= resolved <= target_count, (
        "schema-3 active date target arithmetic mismatch"
    )
    assert resolved == observed + absent, (
        "schema-3 active date observed/absence arithmetic mismatch"
    )
    _assert_percentage(
        progress.get("targets_observed_percent"),
        observed * 100.0 / target_count if target_count else 0.0,
        label="schema-3 active date targets_observed_percent",
    )
    for key in (
        "root_domain_fixed",
        "domain_matches_unfiltered_boundary",
        "unfiltered_boundary_matches_bootstrap",
        "closing_boundary_matches",
        "date_partition_complete",
        "date_partition_authoritative",
        "union_authoritative",
        "absence_authoritative",
        "completion_authoritative",
    ):
        assert isinstance(progress.get(key), bool), (
            f"schema-3 active date scan {key} is invalid"
        )
    generation = progress.get("generation")
    assert (
        isinstance(generation, int)
        and not isinstance(generation, bool)
        and generation >= 1
    ), "schema-3 active scan generation is invalid"
    assert isinstance(evidence, dict), "schema-3 active scan evidence asset is missing"
    assert evidence.get("schema_version") == 1, (
        "active scan authority evidence schema mismatch"
    )
    assert evidence.get("cycle_id") == progress.get("cycle_id"), (
        "active scan authority evidence cycle mismatch"
    )
    assert evidence.get("generation") == generation, (
        "active scan authority evidence generation mismatch"
    )
    raw_verification = evidence.get("raw_verification")
    assert isinstance(raw_verification, dict), (
        "active scan export-time RAW verification is missing"
    )
    assert raw_verification.get("mode") == "export_time_official_warehouse_bytes", (
        "active scan RAW verification mode mismatch"
    )
    raw_files = raw_verification.get("files")
    assert isinstance(raw_files, list), "active scan RAW verification files are missing"
    verification_by_path: dict[str, dict] = {}
    for descriptor in raw_files:
        assert isinstance(descriptor, dict), (
            "active scan RAW verification descriptor is invalid"
        )
        raw_path = descriptor.get("raw_path")
        assert isinstance(raw_path, str) and raw_path, (
            "active scan RAW verification path is missing"
        )
        path = Path(raw_path)
        assert not path.is_absolute() and ".." not in path.parts, (
            "active scan RAW verification path is unsafe"
        )
        assert raw_path not in verification_by_path, (
            "active scan RAW verification path is duplicated"
        )
        _sha256(descriptor.get("sha256"), label="active scan RAW verification")
        assert _nonnegative_integer(descriptor.get("bytes")), (
            "active scan RAW verification byte count is invalid"
        )
        verification_by_path[raw_path] = descriptor
    assert raw_verification.get("verified_files") == len(raw_files), (
        "active scan RAW verification file count mismatch"
    )
    assert raw_verification.get("verified_bytes") == sum(
        descriptor["bytes"] for descriptor in raw_files
    ), "active scan RAW verification byte total mismatch"
    assert list(verification_by_path) == sorted(verification_by_path), (
        "active scan RAW verification descriptors are not sorted"
    )

    evidence_asset = progress.get("evidence_asset")
    assert isinstance(evidence_asset, dict), (
        "schema-3 active scan evidence descriptor is missing"
    )
    assert evidence_asset.get("schema_version") == 1, (
        "schema-3 active scan evidence descriptor schema mismatch"
    )
    assert evidence_asset.get("file") == "active_scan_authority.json", (
        "schema-3 active scan evidence descriptor path mismatch"
    )
    _sha256(
        evidence_asset.get("sha256"),
        label="active scan evidence descriptor",
    )
    assert _nonnegative_integer(evidence_asset.get("bytes")) and evidence_asset["bytes"] > 0, (
        "active scan evidence descriptor byte count is invalid"
    )

    bootstrap = progress.get("bootstrap")
    bootstrap_evidence = evidence.get("bootstrap")
    assert isinstance(bootstrap, dict), "schema-3 active bootstrap status is missing"
    assert isinstance(bootstrap_evidence, dict), (
        "schema-3 active bootstrap evidence is missing"
    )
    for key in (
        "pass_number",
        "total_count",
        "page_size",
        "expected_pages",
        "pages_committed",
        "page_hole_count",
        "records",
        "unique_refs",
        "duplicate_records",
        "integrity_error_count",
    ):
        assert _nonnegative_integer(bootstrap.get(key)), (
            f"active bootstrap {key} is invalid"
        )
    assert bootstrap["pass_number"] >= 1, "active bootstrap pass_number is invalid"
    assert bootstrap["page_size"] >= 1, "active bootstrap page_size is invalid"
    expected_pages = max(
        1,
        (bootstrap["total_count"] + bootstrap["page_size"] - 1)
        // bootstrap["page_size"],
    )
    assert bootstrap["expected_pages"] == expected_pages, (
        "active bootstrap expected page arithmetic mismatch"
    )
    assert bootstrap["pages_committed"] <= expected_pages, (
        "active bootstrap committed pages exceed boundary"
    )
    assert bootstrap.get("state") in {"scanning", "closing_boundary", "complete"}, (
        "active bootstrap state is invalid"
    )
    assert isinstance(bootstrap.get("complete"), bool), (
        "active bootstrap complete flag is invalid"
    )
    bootstrap_refs = _evidence_references(
        bootstrap_evidence.get("references"),
        label="active bootstrap",
    )
    bootstrap_ref_set = set(bootstrap_refs)
    bootstrap_duplicates = len(bootstrap_refs) - len(bootstrap_ref_set)
    assert bootstrap["records"] == len(bootstrap_refs), (
        "active bootstrap record/evidence count mismatch"
    )
    assert bootstrap["unique_refs"] == len(bootstrap_ref_set), (
        "active bootstrap unique/evidence count mismatch"
    )
    assert bootstrap["duplicate_records"] == bootstrap_duplicates, (
        "active bootstrap duplicate arithmetic mismatch"
    )
    bootstrap_sha = _reference_union_sha256(bootstrap_ref_set)
    assert _sha256(
        bootstrap_evidence.get("union_sha256"),
        label="active bootstrap evidence union",
    ) == bootstrap_sha, "active bootstrap evidence union hash mismatch"
    assert _sha256(
        bootstrap.get("union_sha256"),
        label="active bootstrap status union",
    ) == bootstrap_sha, "active bootstrap status union hash mismatch"
    assert bootstrap_evidence.get("pass_number") == bootstrap["pass_number"], (
        "active bootstrap evidence pass mismatch"
    )
    bootstrap_pages = bootstrap_evidence.get("pages")
    assert isinstance(bootstrap_pages, list), "active bootstrap page evidence is missing"
    page_numbers: list[int] = []
    page_records = 0
    page_references: list[str] = []
    first_page_references: list[str] = []
    for page in bootstrap_pages:
        assert isinstance(page, dict), "active bootstrap page evidence is invalid"
        page_number = page.get("page_number")
        records = page.get("records")
        assert (
            isinstance(page_number, int)
            and not isinstance(page_number, bool)
            and page_number >= 1
        ), (
            "active bootstrap page number is invalid"
        )
        assert isinstance(records, int) and not isinstance(records, bool) and records >= 0, (
            "active bootstrap page records are invalid"
        )
        page_number = int(page_number)
        records = int(records)
        assert page.get("total_count") == bootstrap["total_count"], (
            "active bootstrap page total changed"
        )
        expected_page_records = min(
            bootstrap["page_size"],
            max(
                0,
                bootstrap["total_count"]
                - (page_number - 1) * bootstrap["page_size"],
            ),
        )
        assert records == expected_page_records, (
            "active bootstrap page cardinality mismatch"
        )
        _assert_raw_verification_pointer(
            verification_by_path,
            page.get("raw_path"),
            page.get("sha256"),
            label=f"active bootstrap page {page_number}",
        )
        references_value = page.get("references")
        assert isinstance(references_value, list) and all(
            isinstance(reference, str) and reference
            for reference in references_value
        ), f"active bootstrap page {page_number} references are invalid"
        references = [str(reference) for reference in references_value]
        assert len(references) == records, (
            "active bootstrap page reference/record mismatch"
        )
        assert len(references) == len(set(references)), (
            "active bootstrap page contains duplicate references"
        )
        page_references.extend(references)
        if page_number == 1:
            first_page_references = references
        page_numbers.append(page_number)
        page_records += records
    assert page_numbers == sorted(page_numbers), (
        "active bootstrap page evidence is not ordered"
    )
    assert len(page_numbers) == len(set(page_numbers)), (
        "active bootstrap page evidence contains duplicate pages"
    )
    assert bootstrap["pages_committed"] == len(page_numbers), (
        "active bootstrap committed page/evidence mismatch"
    )
    assert bootstrap["records"] == page_records, (
        "active bootstrap page record arithmetic mismatch"
    )
    assert sorted(page_references) == bootstrap_refs, (
        "active bootstrap flattened page references mismatch"
    )
    bootstrap_head_sha = _reference_union_sha256(set(first_page_references))
    assert _sha256(
        bootstrap_evidence.get("head_ref_sha256"),
        label="active bootstrap evidence head",
    ) == bootstrap_head_sha, "active bootstrap evidence head hash mismatch"
    assert _sha256(
        bootstrap.get("head_ref_sha256"),
        label="active bootstrap status head",
    ) == bootstrap_head_sha, "active bootstrap status head hash mismatch"
    page_holes = bootstrap.get("page_holes")
    assert isinstance(page_holes, list) and all(
        isinstance(page, int) and not isinstance(page, bool) and page >= 1
        for page in page_holes
    ), "active bootstrap page_holes is invalid"
    assert page_holes == sorted(set(page_holes)), (
        "active bootstrap page_holes is duplicate or unsorted"
    )
    expected_holes = [
        page for page in range(1, expected_pages + 1) if page not in page_numbers
    ]
    assert page_holes == expected_holes, "active bootstrap page hole ledger mismatch"
    assert bootstrap["page_hole_count"] == len(page_holes), (
        "active bootstrap page hole count mismatch"
    )
    expected_bootstrap_complete = bool(
        bootstrap.get("state") == "complete"
        and bootstrap["pages_committed"] == expected_pages
        and page_numbers == list(range(1, expected_pages + 1))
        and bootstrap["page_hole_count"] == 0
        and bootstrap["records"] == bootstrap["total_count"]
        and bootstrap["unique_refs"] == bootstrap["total_count"]
        and bootstrap["duplicate_records"] == 0
        and bootstrap["integrity_error_count"] == 0
    )
    assert bootstrap["complete"] == expected_bootstrap_complete, (
        "active bootstrap completion arithmetic mismatch"
    )

    date_filtered_total = progress.get("root_filtered_total")
    assert date_filtered_total is None or _nonnegative_integer(date_filtered_total), (
        "schema-3 active date root total is invalid"
    )
    reported_total = bootstrap["total_count"]
    generation_scanned = progress.get("official_active_generation_scanned_unique")
    assert (
        isinstance(generation_scanned, int)
        and not isinstance(generation_scanned, bool)
        and generation_scanned >= 0
    ), (
        "schema-3 active date generation count is invalid"
    )
    assert generation_scanned <= reported_total, (
        "schema-3 active hybrid generation exceeds bootstrap total"
    )
    reported_scanned = progress["official_active_scanned_unique"]
    scanned_high_watermark = progress["official_active_scanned_lifetime_high_watermark"]
    assert reported_scanned <= reported_total, (
        "schema-3 active hybrid scanned count exceeds bootstrap total"
    )
    assert reported_scanned == min(scanned_high_watermark, reported_total), (
        "schema-3 active hybrid reported/high-watermark mismatch"
    )
    assert reported_scanned >= generation_scanned, (
        "schema-3 active date high-watermark trails its current generation"
    )
    _assert_percentage(
        progress.get("official_active_scanned_percent"),
        (
            reported_scanned * 100.0 / reported_total
            if reported_total
            else 100.0
            if progress.get("union_authoritative")
            else 0.0
        ),
        label="schema-3 active date scanned percent",
    )
    _assert_percentage(
        progress.get("official_active_generation_scanned_percent"),
        (
            generation_scanned * 100.0 / reported_total
            if reported_total
            else 100.0
            if progress.get("union_authoritative")
            else 0.0
        ),
        label="active date generation scanned percent",
    )
    date_evidence = evidence.get("date_partition")
    assert isinstance(date_evidence, dict), (
        "schema-3 active date evidence is missing"
    )
    assert date_evidence.get("generation") == generation, (
        "active date evidence generation mismatch"
    )
    date_root = date_evidence.get("root")
    date_ranges = date_evidence.get("ranges")
    assert isinstance(date_root, dict), "active date root ledger is missing"
    assert isinstance(date_ranges, list), "active date range ledger is missing"
    allowed_range_states = {
        "pending",
        "pending_page",
        "split",
        "leaf_exact",
        "blocked_single_day",
        "superseded",
    }
    range_by_id: dict[str, dict] = {}
    leaf_geometry: list[tuple[date, date]] = []
    root_rows = 0
    for range_row in date_ranges:
        assert isinstance(range_row, dict), "active date range row is invalid"
        range_id = range_row.get("range_id")
        assert isinstance(range_id, str) and range_id, (
            "active date range id is invalid"
        )
        assert range_id not in range_by_id, "active date range id is duplicated"
        range_by_id[range_id] = range_row
        state = range_row.get("state")
        assert state in allowed_range_states, "active date range state is invalid"
        depth = range_row.get("depth")
        range_generation = range_row.get("generation")
        next_page = range_row.get("next_page")
        assert _nonnegative_integer(depth), "active date range depth is invalid"
        assert (
            isinstance(range_generation, int)
            and not isinstance(range_generation, bool)
            and range_generation >= 1
        ), (
            "active date range generation is invalid"
        )
        assert (
            isinstance(next_page, int)
            and not isinstance(next_page, bool)
            and next_page >= 1
        ), (
            "active date range next page is invalid"
        )
        total_count = range_row.get("total_count")
        assert total_count is None or _nonnegative_integer(total_count), (
            "active date range total is invalid"
        )
        try:
            from_day = date.fromisoformat(str(range_row.get("from_day")))
            to_day = date.fromisoformat(str(range_row.get("to_day")))
        except ValueError as exc:
            raise AssertionError("active date range geometry date is invalid") from exc
        assert from_day <= to_day, "active date range has reversed geometry"
        if range_row.get("parent_range_id") is None:
            root_rows += 1
            assert range_id == date_root.get("range_id"), (
                "active date root/range ledger mismatch"
            )
        else:
            assert isinstance(range_row.get("parent_range_id"), str), (
                "active date parent range id is invalid"
            )
        if state == "leaf_exact":
            leaf_geometry.append((from_day, to_day))
    assert root_rows == 1, "active date range ledger must have one root"
    assert date_root in date_ranges, "active date root is not present in range ledger"
    assert len(date_ranges) == progress["ranges_total"], (
        "active date range count/evidence mismatch"
    )
    assert sum(
        row["state"] in {"pending", "pending_page"} for row in date_ranges
    ) == progress["ranges_pending"], "active date pending range count mismatch"
    assert sum(row["state"] == "split" for row in date_ranges) == progress[
        "ranges_split"
    ], "active date split range count mismatch"
    assert sum(row["state"] == "leaf_exact" for row in date_ranges) == progress[
        "ranges_exact"
    ], "active date exact range count mismatch"
    assert sum(
        row["state"] == "blocked_single_day" for row in date_ranges
    ) == progress["ranges_blocked_single_day"], (
        "active date blocked range count mismatch"
    )

    assert date_root.get("generation") == generation, (
        "active date root generation mismatch"
    )
    assert progress.get("root_from_day") == date_root.get("from_day"), (
        "active date root start/status mismatch"
    )
    assert progress.get("root_to_day") == date_root.get("to_day"), (
        "active date root end/status mismatch"
    )
    assert date_root.get("total_count") == date_filtered_total, (
        "active date root total/status mismatch"
    )
    root_domain_fixed = bool(
        date_root.get("from_day") == ACTIVE_DATE_DOMAIN_START
        and date_root.get("to_day") == ACTIVE_DATE_DOMAIN_END
    )
    assert progress["root_domain_fixed"] == root_domain_fixed, (
        "active date fixed-domain flag mismatch"
    )
    geometry_errors = 0
    geometry_cursor = date.fromisoformat(str(date_root["from_day"]))
    geometry_end = date.fromisoformat(str(date_root["to_day"]))
    for leaf_from, leaf_to in sorted(leaf_geometry):
        if leaf_from != geometry_cursor:
            geometry_errors += 1
        geometry_cursor = max(geometry_cursor, leaf_to + timedelta(days=1))
    if geometry_cursor != geometry_end + timedelta(days=1):
        geometry_errors += 1
    assert progress["range_geometry_error_count"] == geometry_errors, (
        "active date range geometry evidence mismatch"
    )

    for key in (
        "domain_matches_boundary",
        "closing_boundary_matches",
    ):
        assert isinstance(date_root.get(key), bool), (
            f"active date root {key} is invalid"
        )
    for key in (
        "boundary_ref_sha256",
        "closing_boundary_ref_sha256",
        "opening_filtered_ref_sha256",
        "closing_filtered_ref_sha256",
        "convergence_union_sha256",
    ):
        value = date_root.get(key)
        if value is not None:
            _sha256(value, label=f"active date root {key}")
    assert date_root.get("scanned_high_watermark") == scanned_high_watermark, (
        "active date root high-watermark mismatch"
    )
    assert date_root.get("bootstrap_pass_number") == bootstrap["pass_number"], (
        "active date root bootstrap pass mismatch"
    )
    boundary_matches_bootstrap = bool(
        bootstrap["complete"]
        and date_root.get("bootstrap_pass_number") == bootstrap["pass_number"]
        and date_root.get("boundary_total_count") == bootstrap["total_count"]
        and date_root.get("boundary_ref_sha256") == bootstrap_head_sha
    )
    assert progress["unfiltered_boundary_matches_bootstrap"] == (
        boundary_matches_bootstrap
    ), "active date/bootstrap boundary flag mismatch"
    domain_matches_boundary = bool(
        date_root.get("domain_matches_boundary")
        and date_root.get("boundary_total_count") is not None
        and date_filtered_total == date_root.get("boundary_total_count")
    )
    assert progress["domain_matches_unfiltered_boundary"] == domain_matches_boundary, (
        "active date/unfiltered domain match flag mismatch"
    )
    closing_boundary_matches = bool(
        date_root.get("closing_boundary_matches")
        and date_root.get("closing_boundary_generation") == generation
        and date_root.get("closing_boundary_total_count") == bootstrap["total_count"]
        and date_root.get("closing_boundary_ref_sha256") == bootstrap_head_sha
        and date_root.get("opening_filtered_ref_sha256")
        == date_root.get("closing_filtered_ref_sha256")
    )
    assert progress["closing_boundary_matches"] == closing_boundary_matches, (
        "active date closing-boundary evidence mismatch"
    )

    date_refs = _evidence_references(
        date_evidence.get("references"),
        label="active date partition",
    )
    date_ref_set = set(date_refs)
    date_sha = _reference_union_sha256(date_ref_set)
    assert _sha256(
        date_evidence.get("union_sha256"),
        label="active date evidence union",
    ) == date_sha, "active date evidence union hash mismatch"
    date_pages = date_evidence.get("pages")
    assert isinstance(date_pages, list), "active date page evidence is missing"
    assert len(date_pages) == progress.get("accepted_pages_current_generation"), (
        "active date accepted page/evidence mismatch"
    )
    date_page_records = 0
    date_page_keys: set[tuple[str, int]] = set()
    leaf_pages: dict[str, list[tuple[int, int, int, list[str]]]] = {}
    flattened_leaf_refs: list[str] = []
    date_page_evidence_valid = True
    for page in date_pages:
        assert isinstance(page, dict), "active date page evidence is invalid"
        range_id = page.get("range_id")
        assert isinstance(range_id, str) and range_id, (
            "active date page range is invalid"
        )
        range_state = page.get("range_state")
        assert range_state in allowed_range_states, "active date page range state is invalid"
        assert range_id in range_by_id, "active date page range is absent from ledger"
        assert range_by_id[range_id]["state"] == range_state, (
            "active date page/range state mismatch"
        )
        page_number = page.get("page_number")
        records = page.get("records")
        total_count = page.get("total_count")
        assert (
            isinstance(page_number, int)
            and not isinstance(page_number, bool)
            and page_number >= 1
        ), (
            "active date page number is invalid"
        )
        assert isinstance(records, int) and not isinstance(records, bool) and records >= 0, (
            "active date page records are invalid"
        )
        assert (
            isinstance(total_count, int)
            and not isinstance(total_count, bool)
            and total_count >= 0
        ), (
            "active date page total is invalid"
        )
        _assert_raw_verification_pointer(
            verification_by_path,
            page.get("raw_path"),
            page.get("sha256"),
            label=f"active date page {range_id}/{page_number}",
        )
        references = _evidence_references(
            page.get("references"),
            label=f"active date page {range_id}/{page_number}",
        )
        assert len(references) == records, (
            "active date page reference/record mismatch"
        )
        assert len(references) == len(set(references)), (
            "active date page contains duplicate references"
        )
        date_page_records += records
        page_key = (range_id, page_number)
        assert page_key not in date_page_keys, (
            "active date page evidence contains a duplicate page"
        )
        date_page_keys.add(page_key)
        if range_state == "leaf_exact":
            leaf_pages.setdefault(range_id, []).append(
                (page_number, records, total_count, references)
            )
            flattened_leaf_refs.extend(references)
    assert date_page_records == progress.get("accepted_records_current_generation"), (
        "active date accepted record/evidence mismatch"
    )
    for pages in leaf_pages.values():
        totals = {total_count for _, _, total_count, _ in pages}
        assert len(totals) == 1, "active date leaf total changed"
        leaf_total = next(iter(totals))
        expected_leaf_pages = max(
            1,
            (leaf_total + bootstrap["page_size"] - 1) // bootstrap["page_size"],
        )
        assert [page_number for page_number, _, _, _ in pages] == list(
            range(1, expected_leaf_pages + 1)
        ), "active date leaf page sequence is incomplete"
        for page_number, records, _, _ in pages:
            expected_records = min(
                bootstrap["page_size"],
                max(
                    0,
                    leaf_total - (page_number - 1) * bootstrap["page_size"],
                ),
            )
            assert records == expected_records, (
                "active date leaf page cardinality mismatch"
            )
    assert sorted(flattened_leaf_refs) == date_refs, (
        "active date flattened leaf references mismatch"
    )
    date_duplicates = len(flattened_leaf_refs) - len(set(flattened_leaf_refs))
    assert date_duplicates == progress.get("partition_duplicate_records"), (
        "active date evidence duplicate arithmetic mismatch"
    )

    date_partition_authoritative = progress.get("date_partition_authoritative")
    assert isinstance(date_partition_authoritative, bool), (
        "active date partition authority flag is invalid"
    )
    expected_date_complete = bool(
        root_domain_fixed
        and progress.get("ranges_pending") == 0
        and progress.get("ranges_blocked_single_day") == 0
        and progress.get("partition_duplicate_records") == 0
        and progress.get("leaf_integrity_error_count") == 0
        and progress.get("range_geometry_error_count") == 0
        and date_filtered_total is not None
        and len(date_ref_set) == date_filtered_total
        and date_page_evidence_valid
    )
    assert progress["date_partition_complete"] == expected_date_complete, (
        "active date partition completion arithmetic mismatch"
    )
    expected_date_authority = bool(
        expected_date_complete and closing_boundary_matches
    )
    assert date_partition_authoritative == expected_date_authority, (
        "active date partition authority arithmetic mismatch"
    )

    residual = progress.get("residual")
    residual_checks = evidence.get("residual_checks")
    assert isinstance(residual, dict), "schema-3 residual status is missing"
    assert isinstance(residual_checks, list), "schema-3 residual evidence is missing"
    assert residual.get("derivation") == "bootstrap_minus_date_partition", (
        "active residual derivation mismatch"
    )
    for key in (
        "known_unique",
        "verified_status4_unique",
        "verified_nonactive_unique",
        "pending_unique",
        "unknown_unique",
        "date_overlap_unique",
        "date_outside_bootstrap_unique",
    ):
        assert _nonnegative_integer(residual.get(key)), (
            f"active residual {key} is invalid"
        )
    assert residual.get("verification_generation") == generation, (
        "active residual verification generation mismatch"
    )
    assert isinstance(residual.get("reconciliation_required"), bool), (
        "active residual reconciliation flag is invalid"
    )
    check_refs: list[str] = []
    verified_active_refs: set[str] = set()
    verified_nonactive_refs: set[str] = set()
    previous_residual_ref: str | None = None
    for check_row in residual_checks:
        assert isinstance(check_row, dict), "active residual evidence row is invalid"
        ref = check_row.get("reference_number")
        assert isinstance(ref, str) and ref, "active residual evidence reference is invalid"
        assert previous_residual_ref is None or previous_residual_ref < ref, (
            "active residual evidence is duplicate or unsorted"
        )
        previous_residual_ref = ref
        check_refs.append(ref)
        state = check_row.get("state")
        assert state in {"verified_active", "verified_nonactive", "error"}, (
            f"active residual evidence state is invalid: {ref}"
        )
        assert _nonnegative_integer(check_row.get("attempts")) and check_row[
            "attempts"
        ] >= 1, f"active residual attempts are invalid: {ref}"
        assert isinstance(check_row.get("run_id"), str) and check_row["run_id"], (
            f"active residual run_id is missing: {ref}"
        )
        assert isinstance(check_row.get("checked_at"), str) and check_row[
            "checked_at"
        ], f"active residual checked_at is missing: {ref}"
        if state == "verified_active":
            assert check_row.get("status_id") == 4, (
                f"active residual status is not 4: {ref}"
            )
            assert check_row.get("error") in (None, ""), (
                f"active residual verified row has an error: {ref}"
            )
            assert isinstance(check_row.get("raw_path"), str) and check_row["raw_path"], (
                f"active residual raw evidence path is missing: {ref}"
            )
            _assert_raw_verification_pointer(
                verification_by_path,
                check_row.get("raw_path"),
                check_row.get("sha256"),
                label=f"active residual evidence {ref}",
            )
            verified_active_refs.add(ref)
        elif state == "verified_nonactive":
            status_id = check_row.get("status_id")
            assert (
                isinstance(status_id, int)
                and not isinstance(status_id, bool)
                and status_id != 4
            ), (
                f"nonactive residual status is missing, nonnumeric, or still 4: {ref}"
            )
            assert isinstance(check_row.get("raw_path"), str) and check_row[
                "raw_path"
            ], f"nonactive residual raw evidence path is missing: {ref}"
            _assert_raw_verification_pointer(
                verification_by_path,
                check_row.get("raw_path"),
                check_row.get("sha256"),
                label=f"nonactive residual evidence {ref}",
            )
            verified_nonactive_refs.add(ref)
        else:
            assert isinstance(check_row.get("error"), str) and check_row["error"], (
                f"active residual error detail is missing: {ref}"
            )
            assert check_row.get("status_id") is None, (
                f"failed residual unexpectedly has a status: {ref}"
            )
    check_ref_set = set(check_refs)
    derived_residual = bootstrap_ref_set - date_ref_set
    date_outside_bootstrap = date_ref_set - bootstrap_ref_set
    unexpected_checks = check_ref_set - derived_residual
    pending_residual = (
        derived_residual - verified_active_refs - verified_nonactive_refs
    )
    date_overlap = date_ref_set.intersection(verified_active_refs)
    assert residual["known_unique"] == len(derived_residual), (
        "active residual known/derived count mismatch"
    )
    assert residual["verified_status4_unique"] == len(
        verified_active_refs & derived_residual
    ), "active residual verified-status4 arithmetic mismatch"
    assert residual["verified_nonactive_unique"] == len(
        verified_nonactive_refs & derived_residual
    ), "active residual verified-nonactive arithmetic mismatch"
    assert residual["pending_unique"] == len(pending_residual), (
        "active residual pending arithmetic mismatch"
    )
    assert residual["unknown_unique"] == len(unexpected_checks), (
        "active residual unknown arithmetic mismatch"
    )
    residual_sha = _reference_union_sha256(derived_residual)
    assert _sha256(
        residual.get("set_sha256"),
        label="active residual set",
    ) == residual_sha, "active residual set hash mismatch"
    assert residual["date_outside_bootstrap_unique"] == len(date_outside_bootstrap), (
        "active residual date-outside-bootstrap arithmetic mismatch"
    )
    assert residual["date_overlap_unique"] == len(date_overlap), (
        "active residual/date overlap arithmetic mismatch"
    )
    expected_reconciliation = bool(
        date_outside_bootstrap
        or unexpected_checks
        or verified_nonactive_refs
    )
    assert residual["reconciliation_required"] == expected_reconciliation, (
        "active residual reconciliation arithmetic mismatch"
    )

    union = progress.get("authoritative_union")
    union_evidence = evidence.get("authoritative_union")
    assert isinstance(union, dict), "schema-3 authoritative union status is missing"
    assert isinstance(union_evidence, dict), (
        "schema-3 authoritative union evidence is missing"
    )
    for key in ("unique_refs", "duplicate_records"):
        assert _nonnegative_integer(union.get(key)), (
            f"active authoritative union {key} is invalid"
        )
    assert union.get("unfiltered_boundary_total") is None or _nonnegative_integer(
        union.get("unfiltered_boundary_total")
    ), "active authoritative union boundary total is invalid"
    union_refs = _evidence_references(
        union_evidence.get("references"),
        label="active authoritative union",
    )
    union_ref_set = set(union_refs)
    assert len(union_refs) == len(union_ref_set), (
        "active authoritative union evidence contains duplicate references"
    )
    expected_union = date_ref_set.union(verified_active_refs)
    assert union_ref_set == expected_union, (
        "active authoritative union evidence set mismatch"
    )
    assert union["unique_refs"] == len(union_ref_set), (
        "active authoritative union count mismatch"
    )
    expected_union_duplicates = len(date_ref_set.intersection(verified_active_refs))
    assert union["duplicate_records"] == expected_union_duplicates, (
        "active authoritative union duplicate arithmetic mismatch"
    )
    union_sha = _reference_union_sha256(union_ref_set)
    assert generation_scanned == len(union_ref_set), (
        "active hybrid generation/evidence count mismatch"
    )
    assert _sha256(
        progress.get("generation_union_sha256"),
        label="active hybrid generation union",
    ) == union_sha, "active hybrid generation union hash mismatch"
    assert _sha256(
        union_evidence.get("union_sha256"),
        label="active authoritative evidence union",
    ) == union_sha, "active authoritative evidence union hash mismatch"
    assert _sha256(
        union.get("union_sha256"),
        label="active authoritative status union",
    ) == union_sha, "active authoritative status union hash mismatch"
    assert _sha256(
        union.get("bootstrap_union_sha256"),
        label="active authoritative bootstrap union",
    ) == bootstrap_sha, "active authoritative bootstrap hash mismatch"
    matches_bootstrap = bool(
        bootstrap["complete"]
        and not date_outside_bootstrap
        and not unexpected_checks
        and not pending_residual
        and not verified_nonactive_refs
        and not date_overlap
        and union["duplicate_records"] == 0
        and len(union_ref_set) == bootstrap["total_count"]
        and union_sha == bootstrap_sha
    )
    assert union.get("matches_bootstrap") == matches_bootstrap, (
        "active authoritative bootstrap match flag mismatch"
    )
    root_unfiltered_total = progress.get("root_unfiltered_boundary_total")
    assert root_unfiltered_total == date_root.get("boundary_total_count"), (
        "active date boundary total/status mismatch"
    )
    assert root_unfiltered_total == union["unfiltered_boundary_total"], (
        "active authoritative boundary total differs from date progress"
    )
    boundary_matches = boundary_matches_bootstrap
    assert union.get("boundary_matches") == boundary_matches, (
        "active authoritative boundary match flag mismatch"
    )
    for key in ("matches_bootstrap", "boundary_matches", "matches_current", "authoritative"):
        assert isinstance(union.get(key), bool), (
            f"active authoritative union {key} flag is invalid"
        )
    convergence_passes = union.get("convergence_passes")
    convergence_generation = union.get("convergence_last_generation")
    assert (
        isinstance(convergence_passes, int)
        and not isinstance(convergence_passes, bool)
        and convergence_passes >= 0
    ), (
        "active authoritative convergence pass count is invalid"
    )
    assert convergence_passes <= generation, (
        "active authoritative convergence exceeds distinct generations"
    )
    assert convergence_generation is None or (
        _nonnegative_integer(convergence_generation)
        and 1 <= convergence_generation <= generation
    ), "active authoritative convergence generation is invalid"
    convergence_sha_value = union.get("convergence_union_sha256")
    if convergence_passes:
        convergence_sha = _sha256(
            convergence_sha_value,
            label="active authoritative convergence union",
        )
    else:
        assert convergence_sha_value is None, (
            "active authoritative convergence hash exists before a pass"
        )
        convergence_sha = None
    assert union["matches_current"] == (
        convergence_sha is not None and convergence_sha == union_sha
    ), (
        "active authoritative current-union match flag mismatch"
    )
    assert date_root.get("convergence_passes") == convergence_passes, (
        "active generation convergence pass/root mismatch"
    )
    assert date_root.get("convergence_last_generation") == convergence_generation, (
        "active generation convergence generation/root mismatch"
    )
    assert date_root.get("convergence_union_sha256") == convergence_sha_value, (
        "active generation convergence hash/root mismatch"
    )
    assert progress.get("convergence_passes") == convergence_passes, (
        "active generation convergence pass/status mismatch"
    )
    assert progress.get("convergence_last_generation") == convergence_generation, (
        "active generation convergence generation/status mismatch"
    )
    assert progress.get("convergence_union_sha256") == convergence_sha_value, (
        "active generation convergence hash/status mismatch"
    )
    assert progress.get("convergence_matches_current_union") == union[
        "matches_current"
    ], "active generation convergence-current flag mismatch"

    proof_status = progress.get("generation_proofs")
    proof_evidence = evidence.get("generation_proofs")
    assert isinstance(proof_status, dict), (
        "active generation proof status is missing"
    )
    assert isinstance(proof_evidence, list), (
        "active generation proof evidence is missing"
    )
    assert proof_status.get("required") == 2, (
        "active generation proof policy mismatch"
    )
    replayed_proofs = [
        _assert_active_generation_proof(
            proof,
            bootstrap_refs=bootstrap_ref_set,
            bootstrap_sha=bootstrap_sha,
            bootstrap_head_sha=bootstrap_head_sha,
            bootstrap_total=bootstrap["total_count"],
            bootstrap_pass=bootstrap["pass_number"],
            page_size=bootstrap["page_size"],
            verification_by_path=verification_by_path,
        )
        for proof in proof_evidence
    ]
    referenced_raw_paths = {
        str(page["raw_path"])
        for page in [*bootstrap_pages, *date_pages]
    }
    referenced_raw_paths.update(
        str(row["raw_path"])
        for row in residual_checks
        if isinstance(row, dict) and row.get("raw_path") not in (None, "")
    )
    for proof in proof_evidence:
        assert isinstance(proof, dict)
        referenced_raw_paths.update(
            str(page["raw_path"])
            for page in proof["page_evidence"]
            if isinstance(page, dict)
        )
        referenced_raw_paths.update(
            str(row["raw_path"])
            for row in proof["residual_evidence"]
            if isinstance(row, dict)
        )
        boundary = proof["boundary_evidence"]
        referenced_raw_paths.update(
            str(boundary[phase][lane]["raw_path"])
            for phase in ("opening", "closing")
            for lane in ("filtered", "unfiltered")
        )
    assert set(verification_by_path) == referenced_raw_paths, (
        "active scan RAW verification has stale or missing pointers"
    )
    proof_generations_all = [item[0] for item in replayed_proofs]
    assert len(proof_generations_all) == len(set(proof_generations_all)), (
        "active generation proof ledger contains duplicate generations"
    )
    assert proof_generations_all == sorted(proof_generations_all), (
        "active generation proof ledger is not ordered by generation"
    )
    assert all(proof_generation <= generation for proof_generation in proof_generations_all), (
        "active generation proof is from a future generation"
    )
    proof_ordinals_all = [item[1] for item in replayed_proofs]
    assert proof_ordinals_all == list(range(1, len(replayed_proofs) + 1)), (
        "active generation proof ordinals are not distinct and contiguous"
    )
    current_generation_proofs = [
        item for item in replayed_proofs if item[0] == generation
    ]
    assert len(current_generation_proofs) <= 1, (
        "active generation proof current generation is duplicated"
    )
    if convergence_generation == generation:
        assert current_generation_proofs, (
            "active convergence has no proof for the current generation"
        )
        assert current_generation_proofs[0][1] == convergence_passes, (
            "active current proof ordinal differs from convergence passes"
        )
    matching_proofs = [item for item in replayed_proofs if item[2] == union_sha]
    matching_generations = sorted({item[0] for item in matching_proofs})
    matching_ordinals = sorted({item[1] for item in matching_proofs})
    assert proof_status.get("recorded_for_bootstrap_pass") == len(replayed_proofs), (
        "active generation proof recorded count mismatch"
    )
    assert proof_status.get("matching_current_union") == len(matching_proofs), (
        "active generation proof matching count mismatch"
    )
    assert proof_status.get("distinct_matching_generations") == len(
        matching_generations
    ), "active generation proof distinct-generation count mismatch"
    assert proof_status.get("generations") == matching_generations, (
        "active generation proof generation list mismatch"
    )
    assert proof_status.get("convergence_ordinals") == matching_ordinals, (
        "active generation proof ordinal list mismatch"
    )
    proof_authoritative = bool(
        len(matching_generations) >= 2
        and generation in matching_generations
        and max(matching_ordinals, default=0) >= 2
    )
    assert proof_status.get("authoritative") == proof_authoritative, (
        "active generation proof authority arithmetic mismatch"
    )
    expected_ready_for_closing = bool(
        expected_date_complete and boundary_matches and matches_bootstrap
    )
    assert progress.get("partition_ready_for_closing_boundary") == (
        expected_ready_for_closing
    ), "active hybrid closing-readiness arithmetic mismatch"
    residual_clean = bool(
        residual["verified_nonactive_unique"] == 0
        and residual["pending_unique"] == 0
        and residual["unknown_unique"] == 0
        and residual["date_overlap_unique"] == 0
        and residual["date_outside_bootstrap_unique"] == 0
        and not residual["reconciliation_required"]
    )
    expected_union_authority = bool(
        bootstrap["complete"]
        and date_partition_authoritative
        and residual_clean
        and matches_bootstrap
        and boundary_matches
        and union["duplicate_records"] == 0
        and convergence_passes >= 2
        and convergence_generation == generation
        and union["matches_current"]
        and proof_authoritative
    )
    assert union["authoritative"] == expected_union_authority, (
        "active hybrid union authority arithmetic mismatch"
    )
    assert progress.get("union_authoritative") == expected_union_authority, (
        "active hybrid top-level union authority mismatch"
    )
    assert progress.get("partition_authoritative") == expected_union_authority, (
        "active hybrid partition authority alias mismatch"
    )

    expected_completion = bool(expected_union_authority and resolved == target_count)
    assert progress.get("completion_authoritative") == expected_completion, (
        "active hybrid completion authority arithmetic mismatch"
    )
    expected_absence = bool(expected_union_authority and absent > 0)
    assert progress.get("absence_authoritative") == expected_absence, (
        "active hybrid absence authority arithmetic mismatch"
    )


def assert_active_date_scan_contract(
    progress: object,
    evidence: object = None,
) -> None:
    """Validate legacy date-only or schema-3 hybrid active-scan evidence."""

    assert isinstance(progress, dict), "active date scan progress is invalid"
    schema_version = progress.get("schema_version", 2)
    assert schema_version in (2, 3, 4, 5), (
        "active date scan schema version is unsupported"
    )
    if schema_version == CARDINALITY_SEAL_SCHEMA_VERSION:
        assert_active_cardinality_progress_summary(progress)
        authority_progress = selected_cardinality_authority(progress)
        if authority_progress is None:
            assert evidence is None, (
                "partial active census unexpectedly publishes authority evidence"
            )
        else:
            if authority_progress is not progress:
                assert authority_progress.get("cycle_id") != progress.get("cycle_id"), (
                    "last active census authority belongs to the current partial cycle"
                )
                assert "last_authority" not in authority_progress, (
                    "last active census authority is recursively nested"
                )
            assert_active_cardinality_scan_contract(authority_progress, evidence)
        return
    if schema_version == INTERVAL_COVERAGE_SCHEMA_VERSION:
        assert_active_interval_coverage_contract(progress, evidence)
        return
    if schema_version == 3:
        assert_active_hybrid_scan_contract(progress, evidence)
        return
    _assert_legacy_active_date_scan_contract(progress)


def _assert_legacy_active_date_scan_contract(progress: object) -> None:
    """Validate the transitional schema-2 date-only partition contract."""

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
        "root_domain_fixed",
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
        assert progress["root_domain_fixed"], (
            "authoritative active date partition does not cover the fixed domain"
        )
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
    assert fetch_status.get("still_missing") == manifest.get("still_missing"), (
        "fetch status still_missing disagrees with manifest"
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

    active_scan = fetch_status.get("active_scan")
    date_fallback = (
        active_scan.get("date_fallback") if isinstance(active_scan, dict) else None
    )
    authority_evidence = parsed_assets.get("active_scan_authority.json")
    authority_schema = (
        date_fallback.get("schema_version") if isinstance(date_fallback, dict) else None
    )
    descriptor_owner: dict | None = None
    expected_evidence_schema: int | None = None
    if authority_schema == 3 and isinstance(date_fallback, dict):
        descriptor_owner = date_fallback
        expected_evidence_schema = 1
    elif authority_schema == CARDINALITY_SEAL_SCHEMA_VERSION:
        descriptor_owner = selected_cardinality_authority(date_fallback)
        expected_evidence_schema = CARDINALITY_SEAL_SCHEMA_VERSION
    if descriptor_owner is not None:
        assert authority_evidence is not None, (
            "active scan authority status has no evidence asset"
        )
        authority_descriptor = assets.get("active_scan_authority.json") or {}
        assert authority_descriptor.get("role") == "active_scan_authority_evidence", (
            "active scan authority evidence role mismatch"
        )
        evidence_descriptor = descriptor_owner.get("evidence_asset") or {}
        assert evidence_descriptor.get("schema_version") == expected_evidence_schema, (
            "active scan authority evidence schema descriptor mismatch"
        )
        assert evidence_descriptor.get("bytes") == authority_descriptor.get("bytes"), (
            "active scan authority evidence byte descriptor mismatch"
        )
        assert evidence_descriptor.get("sha256") == authority_descriptor.get("sha256"), (
            "active scan authority evidence SHA descriptor mismatch"
        )
    else:
        assert authority_evidence is None, (
            "legacy active scan unexpectedly publishes authority evidence"
        )
    assert_active_scan_progress_contract(active_scan, authority_evidence)
    assert_active_missing_truth(active_scan, fetch_status.get("still_missing"))
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
    elif name in {"fetch_status.json", "active_scan_authority.json"}:
        result["payload"] = parsed
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

    fetch_status_result = results.get("fetch_status.json") or {}
    fetch_status = fetch_status_result.get("payload")
    assert isinstance(fetch_status, dict), "remote fetch status asset is missing"
    assert fetch_status.get("still_missing") == manifest.get("still_missing"), (
        "remote fetch status still_missing disagrees with manifest"
    )
    active_scan = fetch_status.get("active_scan")
    date_fallback = (
        active_scan.get("date_fallback") if isinstance(active_scan, dict) else None
    )
    authority_result = results.get("active_scan_authority.json") or {}
    authority_evidence = authority_result.get("payload")
    authority_schema = (
        date_fallback.get("schema_version") if isinstance(date_fallback, dict) else None
    )
    descriptor_owner = None
    expected_evidence_schema = None
    if authority_schema == 3 and isinstance(date_fallback, dict):
        descriptor_owner = date_fallback
        expected_evidence_schema = 1
    elif authority_schema == CARDINALITY_SEAL_SCHEMA_VERSION:
        descriptor_owner = selected_cardinality_authority(date_fallback)
        expected_evidence_schema = CARDINALITY_SEAL_SCHEMA_VERSION
    if descriptor_owner is not None:
        assert authority_evidence is not None, (
            "remote active scan authority status has no evidence asset"
        )
        authority_descriptor = assets.get("active_scan_authority.json") or {}
        assert authority_descriptor.get("role") == "active_scan_authority_evidence", (
            "remote active scan authority evidence role mismatch"
        )
        evidence_descriptor = descriptor_owner.get("evidence_asset") or {}
        assert evidence_descriptor.get("schema_version") == expected_evidence_schema, (
            "remote active scan authority schema descriptor mismatch"
        )
        assert evidence_descriptor.get("bytes") == authority_descriptor.get("bytes"), (
            "remote active scan authority byte descriptor mismatch"
        )
        assert evidence_descriptor.get("sha256") == authority_descriptor.get("sha256"), (
            "remote active scan authority SHA descriptor mismatch"
        )
    else:
        assert authority_evidence is None, (
            "remote legacy active scan unexpectedly publishes authority evidence"
        )
    assert_active_scan_progress_contract(active_scan, authority_evidence)
    assert_active_missing_truth(active_scan, fetch_status.get("still_missing"))
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
