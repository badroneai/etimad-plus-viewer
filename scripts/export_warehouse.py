"""Build the Kashaf static data contract from the Phase-0 and official warehouses.

The awarded catalogue is deliberately split into a compact searchable index
descriptor, 16 deterministic searchable-index parts, and 64 deterministic
detail shards.  No generated asset requires Git LFS.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import unicodedata
from collections import OrderedDict, defaultdict
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, urlsplit


SCHEMA_VERSION = 3
SHARD_COUNT = 64
AWARDED_INDEX_PART_COUNT = 16
AWARDED_INDEX_PART_FORMAT_VERSION = 1
AWARDED_INDEX_PART_ALGORITHM = "sha256_first_byte_mod_16"
ACTIVE_SCAN_AUTHORITY_FILE = "active_scan_authority.json"
ACTIVE_SCAN_AUTHORITY_SCHEMA_VERSION = 1
CARDINALITY_SEAL_SCHEMA_VERSION = 4
CARDINALITY_SEAL_STRATEGY = "cardinality_seal_v1"
CARDINALITY_SEAL_MODE = "official_active_cardinality_seal"
INTERVAL_COVERAGE_SCHEMA_VERSION = 5
INTERVAL_COVERAGE_STRATEGY = "deadline_interval_coverage_v1"
INTERVAL_COVERAGE_MODE = "official_active_interval_sweep"
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
ACTIVE_CENSUS_FILTER_KEYS = (
    "TenderTypeId",
    "TenderAreasIdString",
    "ConditionaBookletRange",
    "TenderActivityId",
    "AgencyCode",
)
ACTIVE_CENSUS_TAXONOMY_ENDPOINTS = {
    "type": "/Qualification/GetTenderTypes",
    "area": "/Tender/GetAreasAsync",
    "activity": "/Tender/GetMainActivitiesAsync",
    "agency": "/Tender/GetAllAgenciesAsync",
}
SAUDI_TIMEZONE = timezone(timedelta(hours=3))
HERE = Path(__file__).resolve().parents[1]
DEFAULT_OUT = HERE / "data"
PLUS_CANDIDATES = tuple(
    Path(value)
    for value in (
        os.environ.get("ETIMAD_WAREHOUSE"),
    )
    if value
)

AWARD_FIELDS = {
    "winners",
    "allBids",
    "bids",
    "winAmount",
    "groups",
    "awardGroups",
    "awardCompleteness",
    "awardState",
    "awardMode",
    "awardAnnouncedAt",
}
INDEX_FIELDS = (
    "name",
    "ref",
    "agency",
    "region",
    "activity",
    "type",
    "status",
    "tenderCategory",
    "deadline",
    "winAmount",
    "winAmountHalalas",
    "currency",
    "bids",
)
NON_TENDER_FILES = (
    "winnerfacet.json",
    "ssr_tenders.json",
    "activities.json",
    "agencies.json",
    "types.json",
    "facets_open.json",
    "agencies_api.json",
    "companies_api.json",
    "companies_ssr.json",
    "companies.json",
    "agencies_ssr.json",
    "companies_sitemap.json",
    "agencies_sitemap.json",
    "tender_refs_sitemap.json",
    "activities_sitemap.json",
    "taxonomy_observed.json",
    "fetch_status.json",
    "inventory.json",
)
PHASE0_STATUS_KEYS = (
    "phase",
    "mode",
    "single_writer",
    "session_reused",
    "gate",
    "winnerfacet",
    "public_company_wins",
    "awarded",
    "all",
    "analysis_phase",
)

OFFICIAL_FLAG_FIELDS = {
    "hasInvitations",
    "isManagedByEtimad",
    "isLocalization",
    "isSMEs",
    "isPreQualification",
    "isReverseAuction",
    "isFrameworkAgreement",
}

OFFICIAL_COMPONENT_SOURCE_ID = "etimad_official_components"
OFFICIAL_REGION_LABELS = (
    "منطقة الرياض",
    "منطقة مكة المكرمة",
    "منطقة المدينة المنورة",
    "منطقة القصيم",
    "المنطقة الشرقية",
    "منطقة عسير",
    "منطقة تبوك",
    "منطقة حائل",
    "منطقة الحدود الشمالية",
    "منطقة جازان",
    "منطقة نجران",
    "منطقة الباحة",
    "منطقة الجوف",
)

AWARDED_STATUS_TERMS = (
    "awarded",
    "award announced",
    "تمت الترسية",
    "تم الترسية",
    "مرساة",
)
AWARDING_STATUS_TERMS = (
    "awarding stage",
    "award stage",
    "مرحلة الترسية",
    "تحت الترسية",
)
CANCELLED_STATUS_TERMS = (
    "cancelled",
    "canceled",
    "withdrawn",
    "ملغ",
    "الغيت",
    "سحبت",
)
EXAMINATION_STATUS_TERMS = (
    "closed",
    "expired",
    "evaluation",
    "examination",
    "under review",
    "مغلق",
    "منته",
    "فحص",
    "تقييم",
    "فتح العروض",
    "تحت المراجعة",
)
OPEN_STATUS_TERMS = (
    "active",
    "open",
    "published",
    "نشط",
    "مفتوح",
    "متاح",
)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def first_present(*values: Any) -> Any:
    return next((value for value in values if value is not None), None)


def parse_iso_datetime(value: Any, *, date_end_of_day: bool = False) -> datetime | None:
    """Parse Etimad timestamps, treating naive values as Saudi local time.

    Phase-0 sometimes stores a date without a time.  A tender whose deadline is
    only a date remains open through the end of that Saudi calendar day.
    """
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed: datetime | None = None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for pattern in ("%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
            try:
                parsed = datetime.strptime(text, pattern)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    date_only = not re.search(r"[T\s]\d{1,2}:\d{2}", text)
    if date_only and date_end_of_day:
        parsed = datetime.combine(parsed.date(), time.max)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SAUDI_TIMEZONE)
    return parsed.astimezone(timezone.utc)


def canonical_iso(value: Any) -> str | None:
    parsed = parse_iso_datetime(value)
    return parsed.isoformat() if parsed else None


def normalized_status(value: Any) -> str:
    text = unicodedata.normalize("NFC", str(value or "")).lower()
    text = re.sub(r"[\u064b-\u065f\u0670]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def has_status_term(status: str, terms: Iterable[str]) -> bool:
    return any(term in status for term in terms)


def bid_count_is_zero(record: dict[str, Any]) -> bool:
    """Return true only when the projection contains no bid evidence."""
    value = record.get("bids")
    if isinstance(value, list):
        return len(value) == 0
    if isinstance(value, bool):
        return False
    if value not in (None, ""):
        try:
            return Decimal(str(value).strip()) == 0
        except (InvalidOperation, ValueError):
            return False
    return not meaningful(record.get("allBids")) and not meaningful(record.get("winners"))


def future_deadline_without_award_proof(
    record: dict[str, Any],
    *,
    as_of: datetime,
) -> bool:
    """Identify the forbidden null-award/future-deadline lifecycle combination."""
    deadline = parse_iso_datetime(record.get("deadline"), date_end_of_day=True)
    return bool(
        deadline is not None
        and deadline >= as_of
        and not meaningful(record.get("winAmount"))
        and bid_count_is_zero(record)
    )


def award_is_announced(
    record: dict[str, Any],
    *,
    as_of: datetime | None = None,
) -> bool:
    status = normalized_status(record.get("status"))
    if as_of is not None and future_deadline_without_award_proof(record, as_of=as_of):
        return False
    return bool(
        record.get("awardState") == "announced"
        or meaningful(record.get("winners"))
        or meaningful(record.get("winAmount"))
        or has_status_term(status, AWARDED_STATUS_TERMS)
    )


def classify_tender(
    record: dict[str, Any],
    *,
    as_of: datetime,
) -> tuple[str, str, datetime | None]:
    """Return a truthful lifecycle category, its evidence basis, and deadline."""
    status = normalized_status(record.get("status"))
    status_id = record.get("statusId")
    deadline = parse_iso_datetime(record.get("deadline"), date_end_of_day=True)
    if award_is_announced(record, as_of=as_of):
        return "awarded", "award_evidence", deadline
    if has_status_term(status, CANCELLED_STATUS_TERMS):
        return "cancelled", "official_status_cancelled", deadline
    if has_status_term(status, AWARDED_STATUS_TERMS):
        return "awarded", "official_status_awarded", deadline
    if has_status_term(status, AWARDING_STATUS_TERMS):
        return "awarding", "official_status_awarding_stage", deadline
    if deadline is not None:
        if deadline >= as_of and not has_status_term(status, EXAMINATION_STATUS_TERMS):
            return "open", "deadline_not_elapsed", deadline
        return "examination", "deadline_elapsed_or_terminal_status", deadline
    if has_status_term(status, EXAMINATION_STATUS_TERMS):
        return "examination", "official_status_terminal", None
    if has_status_term(status, OPEN_STATUS_TERMS) or status_id in (4, "4"):
        return "open", "official_active_status_without_deadline", None
    return "unknown", "insufficient_lifecycle_evidence", None


def apply_lifecycle(
    record: dict[str, Any],
    *,
    as_of: datetime,
) -> tuple[str, datetime | None]:
    category, basis, deadline = classify_tender(record, as_of=as_of)
    record["tenderCategory"] = category
    record["tenderCategoryBasis"] = basis
    freshness = record.setdefault("_freshness", {})
    freshness["lifecycleClassifiedAt"] = as_of.isoformat()
    if deadline is not None:
        remaining_seconds = max(0, int((deadline - as_of).total_seconds()))
        record["days"] = remaining_seconds // 86_400
        record["hoursLeft"] = remaining_seconds // 3_600
        record["deadlineWindowHours"] = remaining_seconds / 3_600
        freshness["deadlineParsedAt"] = deadline.isoformat()
    else:
        record.pop("deadlineWindowHours", None)
        if category != "open":
            record.pop("days", None)
            record.pop("hoursLeft", None)
    return category, deadline


def meaningful(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def to_halalas(value: Any) -> int | None:
    """Convert a decimal-like value to exact integer halalas via Decimal(str(value))."""
    if value in (None, "", "****") or isinstance(value, bool):
        return None
    try:
        decimal_value = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, AttributeError, ValueError):
        return None
    if not decimal_value.is_finite():
        return None
    quantized = decimal_value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return int(quantized * 100)


def add_offer_money(offer: dict[str, Any]) -> None:
    for legacy, exact in (("bid", "bidHalalas"), ("award", "awardHalalas")):
        halalas = to_halalas(offer.get(legacy))
        if halalas is not None:
            offer[exact] = halalas
    if "bidHalalas" in offer or "awardHalalas" in offer:
        offer["currency"] = "SAR"


def add_money_projection(record: dict[str, Any]) -> None:
    """Keep legacy numbers while adding exact, auditable SAR projections."""
    win_halalas = to_halalas(record.get("winAmount"))
    if win_halalas is not None:
        record["winAmountHalalas"] = win_halalas
        record["currency"] = "SAR"

    for field in ("winners", "allBids"):
        for offer in record.get(field) or []:
            if isinstance(offer, dict):
                add_offer_money(offer)

    winner_awards: list[int] = []
    for offer in record.get("winners") or []:
        if not isinstance(offer, dict):
            continue
        award_halalas = offer.get("awardHalalas")
        if isinstance(award_halalas, int):
            winner_awards.append(award_halalas)
    if win_halalas is None or not winner_awards:
        record["moneyConsistency"] = {
            "status": "unverifiable",
            "winAmountHalalas": win_halalas,
            "winnerAwardsHalalasSum": sum(winner_awards) if winner_awards else None,
            "deltaHalalas": None,
            "method": "decimal_str_halalas",
        }
        return
    winner_sum = sum(winner_awards)
    delta = winner_sum - win_halalas
    record["moneyConsistency"] = {
        "status": "match" if delta == 0 else "mismatch",
        "winAmountHalalas": win_halalas,
        "winnerAwardsHalalasSum": winner_sum,
        "deltaHalalas": delta,
        "method": "decimal_str_halalas",
    }


def record_ref(record: dict[str, Any]) -> str | None:
    value = (
        record.get("ref")
        or record.get("referenceNumber")
        or record.get("reference_number")
    )
    return str(value) if value not in (None, "") else None


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        if default is not None:
            return deepcopy(default)
        raise FileNotFoundError(path)
    raw = path.read_bytes()
    if raw.startswith(b"version https://git-lfs.github.com/spec/v1"):
        raise ValueError(f"Git LFS pointer is not usable JSON: {path}")
    return json.loads(raw.decode("utf-8"))


def json_bytes(payload: Any, *, pretty: bool = False) -> bytes:
    if pretty:
        text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False)
    else:
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return text.encode("utf-8")


def write_asset(
    out: Path,
    name: str,
    payload: Any,
    *,
    count: int | None = None,
    role: str | None = None,
) -> dict[str, Any]:
    data = json_bytes(payload)
    path = out / name
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_bytes(data)
    temp.replace(path)
    descriptor: dict[str, Any] = {
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "contentType": "application/json",
    }
    if count is not None:
        descriptor["records"] = count
    if role:
        descriptor["role"] = role
    return descriptor


def describe_existing(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if raw.startswith(b"version https://git-lfs.github.com/spec/v1"):
        raise ValueError(f"retained asset is a Git LFS pointer: {path}")
    parsed = json.loads(raw.decode("utf-8"))
    descriptor: dict[str, Any] = {
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "contentType": "application/json",
        "role": "retained_phase0_asset",
    }
    if isinstance(parsed, dict) and isinstance(parsed.get("count"), int):
        descriptor["records"] = parsed["count"]
    return descriptor


def add_source(
    record: dict[str, Any],
    source_id: str,
    *,
    fetched_at: str | None,
    layer: str | None,
) -> None:
    provenance = record.setdefault("_provenance", {"sources": [], "fieldSources": {}})
    sources = provenance.setdefault("sources", [])
    key = (source_id, fetched_at, layer)
    existing = {
        (
            s.get("id"),
            s.get("fetchedAt"),
            s.get("layer"),
        )
        if isinstance(s, dict)
        else (str(s), None, None)
        for s in sources
    }
    if key not in existing:
        sources.append(
            {
                "id": source_id,
                "fetchedAt": fetched_at,
                "layer": layer,
            }
        )


def seed_record(
    raw: dict[str, Any],
    *,
    source_id: str,
    fetched_at: str | None,
    layer: str | None,
) -> dict[str, Any]:
    record = deepcopy(raw)
    ref = record_ref(record)
    if ref:
        record["ref"] = ref
    record["_source"] = source_id
    add_source(record, source_id, fetched_at=fetched_at, layer=layer)
    record["_provenance"]["defaultFieldSource"] = source_id
    return record


def official_overlay(
    base: dict[str, Any] | None,
    official: dict[str, Any],
    *,
    source_id: str = "etimad_official_periodic",
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Overlay authoritative non-null metadata without erasing Phase-0 awards."""
    result = deepcopy(base) if base else {}
    old_provenance = result.get("_provenance") or {"sources": [], "fieldSources": {}}
    official_provenance = official.get("_provenance") or {"sources": [], "fieldSources": {}}
    result["_provenance"] = deepcopy(old_provenance)
    result["_provenance"].setdefault("sources", [])
    result["_provenance"].setdefault("fieldSources", {})
    for source_value in official_provenance.get("sources", []):
        source = (
            source_value
            if isinstance(source_value, dict)
            else {"id": str(source_value), "fetchedAt": None, "layer": None}
        )
        marker = (source.get("id"), source.get("fetchedAt"), source.get("layer"))
        known = {
            (
                item.get("id"),
                item.get("fetchedAt"),
                item.get("layer"),
            )
            if isinstance(item, dict)
            else (str(item), None, None)
            for item in result["_provenance"]["sources"]
        }
        if marker not in known:
            result["_provenance"]["sources"].append(deepcopy(source))

    for key in ("primary", "baselineLayer", "defaultFieldSource"):
        if meaningful(official_provenance.get(key)):
            result["_provenance"][key] = deepcopy(official_provenance[key])

    award_complete_marker = official.get("awardCompleteness") in (
        True,
        "complete",
        "announced",
        "all_groups_announced",
    )
    official_award_complete = bool(
        (official.get("awardState") == "announced" or award_complete_marker)
        and award_is_announced(
            official,
            as_of=as_of or datetime.now(timezone.utc),
        )
    )
    for key, value in official.items():
        if key.startswith("_") or not meaningful(value):
            continue
        if key in AWARD_FIELDS and meaningful(result.get(key)) and not official_award_complete:
            continue
        result[key] = deepcopy(value)
        result["_provenance"]["fieldSources"][key] = (
            official_provenance.get("fieldSources", {}).get(key) or source_id
        )

    for meta_key in ("_freshness", "_evidence"):
        incoming = official.get(meta_key)
        if not isinstance(incoming, dict):
            continue
        current = result.setdefault(meta_key, {})
        for key, value in incoming.items():
            if value is not None or key not in current:
                current[key] = deepcopy(value)

    result["ref"] = record_ref(result) or record_ref(official)
    result["_source"] = "official_plus_merged" if base else source_id
    return result


def merge_source_maps(
    primary: OrderedDict[str, dict[str, Any]],
    overlays: dict[str, dict[str, Any]],
    *,
    as_of: datetime | None = None,
) -> OrderedDict[str, dict[str, Any]]:
    merged = OrderedDict((ref, deepcopy(record)) for ref, record in primary.items())
    for ref, official in overlays.items():
        merged[ref] = official_overlay(merged.get(ref), official, as_of=as_of)
    return merged


def overlay_existing(
    primary: OrderedDict[str, dict[str, Any]],
    overlays: dict[str, dict[str, Any]],
) -> OrderedDict[str, dict[str, Any]]:
    """Refresh a derived subset without admitting every record from the overlay."""
    merged = OrderedDict((ref, deepcopy(record)) for ref, record in primary.items())
    for ref in list(merged):
        if ref in overlays:
            merged[ref] = official_overlay(merged[ref], overlays[ref])
    return merged


def phase0_map(
    records: Iterable[Any],
    *,
    fetched_at: str | None,
    layer: str,
    source_id: str = "etimad_plus_phase0",
) -> OrderedDict[str, dict[str, Any]]:
    result: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for raw in records:
        if not isinstance(raw, dict):
            continue
        ref = record_ref(raw)
        if not ref:
            continue
        result[ref] = seed_record(
            raw,
            source_id=source_id,
            fetched_at=fetched_at,
            layer=layer,
        )
    return result


def find_plus_root(explicit: Path | None) -> Path | None:
    if explicit:
        if not (explicit / "layers").is_dir():
            raise FileNotFoundError(f"Plus warehouse has no layers directory: {explicit}")
        return explicit
    return next((path for path in PLUS_CANDIDATES if (path / "layers").is_dir()), None)


def read_plus_layer(root: Path, name: str, *, required: bool = True) -> dict[str, Any]:
    path = root / "layers" / name
    if not path.exists() and not required:
        return {}
    value = load_json(path)
    if not isinstance(value, dict):
        raise ValueError(f"expected object layer: {path}")
    return value


def load_plus_tenders(
    root: Path,
) -> tuple[
    dict[str, OrderedDict[str, dict[str, Any]]],
    dict[str, dict[str, Any]],
]:
    layers: dict[str, dict[str, Any]] = {}
    maps: dict[str, OrderedDict[str, dict[str, Any]]] = {}
    names = {
        "open": "81_tenders_open.json",
        "within_7": "81_tenders_within_7.json",
        "within_30": "81_tenders_within_30.json",
        "awarded": "81_tenders_awarded_yes.json",
    }
    for dataset, filename in names.items():
        layer = read_plus_layer(root, filename)
        layers[dataset] = layer
        maps[dataset] = phase0_map(
            layer.get("records") or [],
            fetched_at=layer.get("fetched_at"),
            layer=filename,
        )
    return maps, layers


def awarded_truth_from_payload(payload: dict[str, Any] | None, *, source: str) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    assets = payload.get("assets")
    awarded_asset = None
    if isinstance(assets, dict):
        awarded_asset = assets.get("awarded")
    elif isinstance(assets, list):
        awarded_asset = next(
            (
                item
                for item in assets
                if isinstance(item, dict) and item.get("state") == "awarded"
            ),
            None,
        )
    candidates = [
        payload.get("baseline_awarded"),
        awarded_asset,
        payload.get("awarded"),
        payload,
    ]
    item = next(
        (
            candidate
            for candidate in candidates
            if isinstance(candidate, dict)
            and any(
                key in candidate
                for key in (
                    "source_has_more",
                    "hasMore",
                    "source_partial",
                    "partial",
                    "source_complete",
                    "complete",
                )
            )
        ),
        None,
    )
    if item is None:
        return None
    has_more = first_present(item.get("source_has_more"), item.get("hasMore"))
    partial = first_present(item.get("source_partial"), item.get("partial"))
    complete = first_present(item.get("source_complete"), item.get("complete"))
    if has_more is not None:
        has_more = bool(has_more)
    if partial is not None:
        partial = bool(partial)
    if complete is not None:
        complete = bool(complete)
    claims = [
        has_more is True,
        partial is True,
        complete is False,
    ]
    complete_claims = [
        has_more is False,
        partial is False,
        complete is True,
    ]
    if any(claims) and any(complete_claims):
        raise RuntimeError(f"conflicting awarded completeness truth in {source}")
    if any(claims):
        resolved_partial = True
    elif any(complete_claims):
        resolved_partial = False
    else:
        raise RuntimeError(f"awarded completeness is not explicit in {source}")
    source_records = first_present(
        item.get("source_count"), item.get("records"), item.get("count")
    )
    if isinstance(source_records, list):
        source_records = len(source_records)
    return {
        "partial": resolved_partial,
        "sourceHasMore": has_more,
        "sourcePartial": partial,
        "sourceComplete": complete,
        "sourceFetchedAt": canonical_iso(
            first_present(item.get("source_fetched_at"), item.get("fetched_at"))
        ),
        "sourceRecords": source_records,
        "basis": source,
    }


def resolve_awarded_truth(
    *,
    plus_layers: dict[str, Any],
    database_metadata: dict[str, Any],
    phase0_lock: dict[str, Any] | None,
) -> dict[str, Any]:
    truths: list[dict[str, Any]] = []
    if plus_layers:
        truth = awarded_truth_from_payload(
            plus_layers.get("awarded"), source="plus_layer_81_tenders_awarded_yes"
        )
        if truth:
            truths.append(truth)
    truth = awarded_truth_from_payload(database_metadata, source="official_db_meta_baseline_awarded")
    if truth:
        truths.append(truth)
    truth = awarded_truth_from_payload(phase0_lock, source="phase0_baseline_lock")
    if truth:
        truths.append(truth)
    if not truths:
        raise RuntimeError(
            "awarded completeness has no trusted proof; provide --phase0-lock or hydrated DB meta"
        )
    expected = truths[0]
    for candidate in truths[1:]:
        for field in (
            "partial",
            "sourceHasMore",
            "sourcePartial",
            "sourceComplete",
            "sourceFetchedAt",
            "sourceRecords",
        ):
            left = expected.get(field)
            right = candidate.get(field)
            if left is not None and right is not None and left != right:
                raise RuntimeError(
                    "awarded completeness sources disagree: "
                    f"{expected['basis']} vs {candidate['basis']} on {field}"
                )
    return {
        **expected,
        "validatedBy": [truth["basis"] for truth in truths],
    }


def parse_json_cell(value: Any) -> dict[str, Any] | None:
    if not value:
        return None
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def parse_json_value(value: Any) -> Any:
    if value in (None, ""):
        return None
    if isinstance(value, (dict, list, int, float, bool)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None


def official_payload_fields(payload: dict[str, Any]) -> dict[str, Any]:
    mapping = {
        "referenceNumber": "ref",
        "tenderName": "name",
        "tenderNumber": "num",
        "agencyName": "agency",
        "branchName": "branch",
        "tenderTypeName": "type",
        "tenderActivityName": "activity",
        "tenderStatusName": "status",
        "submitionDate": "submit",
        "lastOfferPresentationDate": "deadline",
        "remainingDays": "days",
        "remainingHours": "hoursLeft",
        "remainingMins": "minutesLeft",
        "tenderId": "officialTenderId",
        "tenderIdString": "officialTenderIdString",
        "tenderTypeId": "tenderTypeId",
        "tenderActivityId": "activityId",
        "tenderStatusId": "statusId",
        "buyingCost": "buyingCost",
        "condetionalBookletPrice": "bookletPrice",
        "financialFees": "financialFees",
        "invitationCost": "invitationCost",
        "lastEnqueriesDate": "lastEnquiriesAt",
        "offersOpeningDate": "offersOpeningAt",
        "hasInvitations": "hasInvitations",
        "insideKSA": "insideKSA",
    }
    result: dict[str, Any] = {}
    for source, target in mapping.items():
        if meaningful(payload.get(source)) or payload.get(source) in (0, False):
            result[target] = payload[source]
    return result


def award_fields(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return {}
    result: dict[str, Any] = {}
    aliases = {
        "winners": ("winners", "winnerCompanies"),
        "allBids": ("allBids", "bidsList", "offers"),
        "bids": ("bids", "bidCount", "offersCount"),
        "winAmount": ("winAmount", "awardAmount", "totalAwardValue"),
        "groups": ("groups", "awardGroups"),
        "awardCompleteness": ("awardCompleteness", "complete", "allGroupsAnnounced"),
        "awardAnnouncedAt": ("awardAnnouncedAt", "announcedAt"),
    }
    for target, candidates in aliases.items():
        for candidate in candidates:
            value = payload.get(candidate)
            if meaningful(value) or value in (0, False):
                result[target] = value
                break
    if payload.get("announced") is True:
        result["awardState"] = "announced"
    return result


def table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def database_meta(connection: sqlite3.Connection) -> dict[str, Any]:
    if not table_columns(connection, "meta"):
        return {}
    result: dict[str, Any] = {}
    for key, value in connection.execute("SELECT key,value FROM meta"):
        parsed = parse_json_value(value)
        result[str(key)] = parsed if parsed is not None else value
    return result


def official_progress_metadata(
    metadata: dict[str, Any],
    key: str,
) -> dict[str, Any]:
    """Copy official progress as-is while making legacy DB absence explicit."""
    if key not in metadata:
        return {
            "available": False,
            "reason": "official_database_metadata_absent",
        }
    value = metadata[key]
    if not isinstance(value, dict):
        raise RuntimeError(f"official database meta {key!r} must be a JSON object")
    return deepcopy(value)


def reference_union_sha256(references: Iterable[str]) -> str:
    """Hash one canonical sorted reference set using the official ledger format."""

    canonical = sorted({str(reference) for reference in references})
    payload = ("\n".join(canonical) + ("\n" if canonical else "")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _reference_list(value: Any, *, label: str) -> list[str]:
    parsed = parse_json_value(value)
    if not isinstance(parsed, list) or any(
        reference in (None, "") or isinstance(reference, (dict, list, bool))
        for reference in parsed
    ):
        raise RuntimeError(f"{label} references_json must be a JSON scalar list")
    return [str(reference) for reference in parsed]


def _json_list(value: Any, *, label: str) -> list[Any]:
    parsed = parse_json_value(value)
    if not isinstance(parsed, list):
        raise RuntimeError(f"{label} must be a JSON list")
    return parsed


def _json_object(value: Any, *, label: str) -> dict[str, Any]:
    parsed = parse_json_value(value)
    if not isinstance(parsed, dict):
        raise RuntimeError(f"{label} must be a JSON object")
    return parsed


def _resolve_official_raw_file(
    warehouse_root: Path,
    raw_path: Any,
    *,
    label: str,
) -> tuple[str, Path]:
    raw_text = str(raw_path or "").strip()
    relative = Path(raw_text)
    if not raw_text or relative.is_absolute() or ".." in relative.parts:
        raise RuntimeError(f"{label} RAW path is unsafe or missing")
    prefix = ("data", "official_warehouse")
    relative_parts = relative.parts
    if relative_parts[: len(prefix)] == prefix:
        relative = Path(*relative_parts[len(prefix) :])
    resolved_root = warehouse_root.resolve()
    resolved = (resolved_root / relative).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise RuntimeError(f"{label} RAW path escapes the official warehouse") from exc
    if not resolved.is_file():
        raise RuntimeError(f"{label} RAW file is missing: {raw_text}")
    return raw_text, resolved


def _verify_official_raw_pointer(
    warehouse_root: Path,
    raw_path: Any,
    expected_sha256: Any,
    *,
    label: str,
) -> dict[str, Any]:
    """Resolve one official pointer under the warehouse and hash its real bytes."""

    raw_text, resolved = _resolve_official_raw_file(
        warehouse_root,
        raw_path,
        label=label,
    )
    sha_text = str(expected_sha256 or "").strip().lower()
    digest = hashlib.sha256()
    byte_count = 0
    with resolved.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
            byte_count += len(chunk)
    if not re.fullmatch(r"[0-9a-f]{64}", sha_text) or digest.hexdigest() != sha_text:
        raise RuntimeError(f"{label} RAW SHA-256 mismatch: {raw_text}")
    return {
        "raw_path": raw_text,
        "sha256": sha_text,
        "bytes": byte_count,
    }


def _verify_boundary_raw_payload(
    warehouse_root: Path,
    capture: dict[str, Any],
    *,
    label: str,
) -> None:
    """Parse the verified boundary RAW body and replay its page-1 semantics."""

    _, resolved = _resolve_official_raw_file(
        warehouse_root,
        capture.get("raw_path"),
        label=label,
    )
    try:
        payload = json.loads(resolved.read_bytes().decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} RAW body is not valid JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise RuntimeError(f"{label} RAW body has no data list")
    rows = payload["data"]
    references: list[str] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("referenceNumber") in (None, ""):
            raise RuntimeError(f"{label} RAW row has no referenceNumber")
        references.append(str(row["referenceNumber"]))
    if (
        payload.get("totalCount") != capture.get("total_count")
        or payload.get("currentPage") != 1
        or payload.get("pageSize") != 24
        or len(rows) != capture.get("records")
        or references != capture.get("references")
    ):
        raise RuntimeError(f"{label} RAW body differs from its boundary descriptor")


def _canonical_bijection_sha256(mappings: Iterable[dict[str, Any]]) -> str:
    """Hash a one-to-one reference/tender-id mapping using the official format."""

    canonical: dict[str, str] = {}
    reference_by_tender_id: dict[str, str] = {}
    for row in mappings:
        if not isinstance(row, dict):
            raise RuntimeError("active census mapping must be an object")
        reference = str(row.get("reference_number") or "").strip()
        tender_id = str(row.get("tender_id") or "").strip()
        if not reference or not tender_id:
            raise RuntimeError("active census mapping has a blank reference or tender id")
        known_id = canonical.get(reference)
        known_reference = reference_by_tender_id.get(tender_id)
        if known_id is not None and known_id != tender_id:
            raise RuntimeError("active census reference maps to two tender ids")
        if known_reference is not None and known_reference != reference:
            raise RuntimeError("active census tender id maps to two references")
        canonical.setdefault(reference, tender_id)
        reference_by_tender_id.setdefault(tender_id, reference)
    payload = "".join(
        f"{reference}\t{canonical[reference]}\n" for reference in sorted(canonical)
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _normalise_mapping_list(value: Any, *, label: str) -> list[dict[str, str]]:
    parsed = parse_json_value(value)
    if isinstance(parsed, dict):
        rows: list[Any] = [
            {"reference_number": reference, "tender_id": tender_id}
            for reference, tender_id in parsed.items()
        ]
    elif isinstance(parsed, list):
        rows = parsed
    else:
        raise RuntimeError(f"{label} mappings must be a JSON list")
    result: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError(f"{label} mapping row must be an object")
        reference = row.get("reference_number", row.get("referenceNumber", row.get("ref")))
        tender_id = row.get("tender_id", row.get("tenderId", row.get("id")))
        reference_text = str(reference or "").strip()
        tender_text = str(tender_id or "").strip()
        if not reference_text or not tender_text:
            raise RuntimeError(f"{label} mapping has a blank reference or tender id")
        result.append(
            {"reference_number": reference_text, "tender_id": tender_text}
        )
    return result


def _read_official_json(
    warehouse_root: Path,
    raw_path: Any,
    *,
    label: str,
) -> Any:
    _, resolved = _resolve_official_raw_file(warehouse_root, raw_path, label=label)
    try:
        return json.loads(resolved.read_bytes().decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} RAW body is not valid JSON") from exc


def _list_payload_rows(payload: Any, *, label: str) -> tuple[list[dict[str, Any]], int]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise RuntimeError(f"{label} RAW body has no data list")
    if any(not isinstance(row, dict) for row in payload["data"]):
        raise RuntimeError(f"{label} RAW data contains a non-object row")
    total = payload.get("totalCount")
    if not isinstance(total, int) or isinstance(total, bool) or total < 0:
        raise RuntimeError(f"{label} RAW totalCount is invalid")
    return payload["data"], total


def _row_reference_mapping(row: dict[str, Any], *, label: str) -> dict[str, str]:
    reference = str(row.get("referenceNumber") or "").strip()
    tender_id = str(row.get("tenderId") or "").strip()
    if not reference or not tender_id:
        raise RuntimeError(f"{label} RAW row lacks referenceNumber or tenderId")
    return {"reference_number": reference, "tender_id": tender_id}


def _assert_active_list_url(
    url: Any,
    *,
    page_number: int,
    filters: dict[str, str],
    label: str,
) -> None:
    actual = urlsplit(str(url or ""))
    expected = urlsplit(ACTIVE_LIST_ENDPOINT)
    if (actual.scheme, actual.netloc, actual.path) != (
        expected.scheme,
        expected.netloc,
        expected.path,
    ):
        raise RuntimeError(f"{label} endpoint is not the official active-list endpoint")
    query = parse_qs(actual.query, keep_blank_values=True)
    expected_query = {
        **ACTIVE_LIST_REQUIRED_PARAMS,
        "PageSize": "24",
        "PageNumber": str(page_number),
        **filters,
    }
    unexpected = set(query) - set(expected_query) - {"_"}
    if unexpected or any(query.get(key) != [value] for key, value in expected_query.items()):
        raise RuntimeError(f"{label} active-list query semantics mismatch")


def _verify_active_list_capture(
    warehouse_root: Path,
    capture: dict[str, Any],
    *,
    label: str,
    page_number: int,
    filters: dict[str, str],
    require_mappings: bool,
) -> tuple[list[str], list[dict[str, str]], int]:
    content_type = str(capture.get("content_type") or "")
    if capture.get("status") != 200 or (content_type and "json" not in content_type.lower()):
        raise RuntimeError(f"{label} is not a saved official JSON 200")
    _assert_active_list_url(
        capture.get("url"),
        page_number=page_number,
        filters=filters,
        label=label,
    )
    payload = _read_official_json(
        warehouse_root,
        capture.get("raw_path"),
        label=label,
    )
    rows, total = _list_payload_rows(payload, label=label)
    if payload.get("currentPage") != page_number or payload.get("pageSize") != 24:
        raise RuntimeError(f"{label} RAW pagination metadata mismatch")
    mappings = [_row_reference_mapping(row, label=label) for row in rows]
    if any(str(row.get("tenderStatusId")) != "4" for row in rows):
        raise RuntimeError(f"{label} RAW page contains a non-active row")
    submitted = [parse_iso_datetime(row.get("submitionDate")) for row in rows]
    if any(value is None for value in submitted) or any(
        newer is not None and older is not None and newer > older
        for older, newer in zip(submitted, submitted[1:])
    ):
        raise RuntimeError(f"{label} RAW page submission ordering is invalid")
    references = [row["reference_number"] for row in mappings]
    if len(references) != len(set(references)):
        raise RuntimeError(f"{label} RAW page contains duplicate references")
    if len({row["tender_id"] for row in mappings}) != len(mappings):
        raise RuntimeError(f"{label} RAW page contains duplicate tender ids")
    expected_records = min(24, max(0, total - 24 * (page_number - 1)))
    if len(rows) != expected_records:
        raise RuntimeError(f"{label} RAW page cardinality differs from totalCount")
    expected_references = [str(item) for item in capture.get("references") or []]
    if (
        capture.get("total_count") != total
        or capture.get("records") != len(rows)
        or capture.get("bytes") != _resolve_official_raw_file(
            warehouse_root, capture.get("raw_path"), label=label
        )[1].stat().st_size
        or expected_references != references
        or (
            capture.get("reference_sha256") is not None
            and capture.get("reference_sha256") != reference_union_sha256(references)
        )
    ):
        raise RuntimeError(f"{label} RAW body differs from its descriptor")
    if require_mappings:
        expected_mappings = _normalise_mapping_list(
            capture.get("mappings"), label=label
        )
        if expected_mappings != mappings:
            raise RuntimeError(f"{label} RAW mappings differ from their descriptor")
    return references, mappings, total


def _taxonomy_payload_rows(payload: Any, *, label: str) -> list[dict[str, Any]]:
    rows: list[Any] | None = None
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        for key in ("data", "items", "result", "results"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                rows = candidate
                break
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise RuntimeError(f"{label} RAW taxonomy list is missing")
    return rows


def _taxonomy_values_from_raw(
    payload: Any,
    *,
    kind: str,
    label: str,
) -> list[dict[str, str]]:
    shapes = {
        "type": ("tenderTypeId", "tenderTypeName"),
        "area": ("id", "name"),
        "activity": ("value", "text"),
        "agency": ("agencyCode", "nameArabic"),
    }
    if kind not in shapes:
        raise RuntimeError(f"{label} taxonomy kind is unsupported")
    value_key, label_key = shapes[kind]
    result: list[dict[str, str]] = []
    for row in _taxonomy_payload_rows(payload, label=label):
        value = str(row.get(value_key) or "").strip()
        raw_display = row.get(label_key)
        display = (
            "__unknown_label__"
            if raw_display in (None, "")
            else str(raw_display).strip()
        )
        if not value or not display:
            raise RuntimeError(f"{label} RAW taxonomy row lacks a value")
        result.append({"value": value, "label": display})
    return sorted(result, key=lambda row: row["value"])


def _cardinality_taxonomy_sha256(
    taxonomy: dict[str, list[dict[str, str]]],
) -> str:
    canonical = json.dumps(
        taxonomy,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _load_hybrid_active_scan_authority(
    database: Path,
    active_scan: dict[str, Any],
) -> dict[str, Any] | None:
    """Build independently checkable hybrid-union evidence from SQLite ledgers."""

    date_progress = active_scan.get("date_fallback")
    if not isinstance(date_progress, dict) or date_progress.get("schema_version") != 3:
        return None
    if date_progress.get("mode") != "official_active_hybrid_union":
        raise RuntimeError("schema-3 active scan mode must be official_active_hybrid_union")
    cycle_id = str(date_progress.get("cycle_id") or "")
    generation = date_progress.get("generation")
    bootstrap = date_progress.get("bootstrap")
    if not cycle_id:
        raise RuntimeError("schema-3 active scan cycle_id is missing")
    if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
        raise RuntimeError("schema-3 active scan generation is invalid")
    if not isinstance(bootstrap, dict):
        raise RuntimeError("schema-3 active scan bootstrap status is missing")
    pass_number = bootstrap.get("pass_number")
    if not isinstance(pass_number, int) or isinstance(pass_number, bool) or pass_number < 1:
        raise RuntimeError("schema-3 active scan bootstrap pass_number is invalid")

    uri = f"file:{database.resolve()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        required_columns = {
            "cycle_id",
            "pass_number",
            "page_number",
            "sha256",
            "raw_path",
            "records",
            "total_count",
            "references_json",
        }
        if not required_columns.issubset(table_columns(connection, "active_scan_pages")):
            raise RuntimeError(
                "schema-3 active scan requires active_scan_pages.references_json ledger"
            )
        bootstrap_rows = connection.execute(
            """
            SELECT page_number,records,total_count,raw_path,sha256,references_json
            FROM active_scan_pages
            WHERE cycle_id=? AND pass_number=?
            ORDER BY page_number,sha256
            """,
            (cycle_id, pass_number),
        ).fetchall()
        bootstrap_refs: list[str] = []
        bootstrap_pages: list[dict[str, Any]] = []
        for row in bootstrap_rows:
            references = _reference_list(
                row["references_json"],
                label=f"active bootstrap page {row['page_number']}",
            )
            bootstrap_refs.extend(references)
            bootstrap_pages.append(
                {
                    "page_number": int(row["page_number"]),
                    "records": int(row["records"]),
                    "total_count": int(row["total_count"]),
                    "raw_path": str(row["raw_path"]),
                    "sha256": str(row["sha256"]),
                    "references": references,
                }
            )

        date_page_columns = {
            "cycle_id",
            "range_id",
            "generation",
            "page_number",
            "total_count",
            "records",
            "raw_path",
            "sha256",
            "references_json",
        }
        if not date_page_columns.issubset(
            table_columns(connection, "active_scan_date_pages")
        ):
            raise RuntimeError("schema-3 active scan date-page ledger is incomplete")
        date_range_columns = {
            "cycle_id",
            "range_id",
            "from_day",
            "to_day",
            "parent_range_id",
            "depth",
            "state",
            "next_page",
            "total_count",
            "generation",
            "boundary_total_count",
            "boundary_ref_sha256",
            "domain_matches_boundary",
            "closing_boundary_total_count",
            "closing_boundary_ref_sha256",
            "closing_boundary_generation",
            "closing_boundary_matches",
            "scanned_high_watermark",
            "convergence_union_sha256",
            "convergence_passes",
            "convergence_last_generation",
            "bootstrap_pass_number",
            "opening_filtered_ref_sha256",
            "closing_filtered_ref_sha256",
        }
        if date_range_columns.issubset(
            table_columns(connection, "active_scan_date_ranges")
        ):
            range_rows = connection.execute(
                """
                SELECT range_id,from_day,to_day,parent_range_id,depth,state,next_page,
                       total_count,generation,boundary_total_count,boundary_ref_sha256,
                       domain_matches_boundary,closing_boundary_total_count,
                       closing_boundary_ref_sha256,closing_boundary_generation,
                       closing_boundary_matches,scanned_high_watermark,
                       convergence_union_sha256,convergence_passes,
                       convergence_last_generation,bootstrap_pass_number,
                       opening_filtered_ref_sha256,closing_filtered_ref_sha256
                FROM active_scan_date_ranges
                WHERE cycle_id=?
                ORDER BY from_day,to_day,depth,range_id
                """,
                (cycle_id,),
            ).fetchall()
            date_rows = connection.execute(
                """
                SELECT p.range_id,p.page_number,p.records,p.total_count,p.raw_path,p.sha256,
                       p.references_json,r.state AS range_state
                FROM active_scan_date_pages p
                JOIN active_scan_date_ranges r
                  ON r.cycle_id=p.cycle_id AND r.range_id=p.range_id
                 AND r.generation=p.generation
                WHERE p.cycle_id=?
                ORDER BY p.range_id,p.page_number
                """,
                (cycle_id,),
            ).fetchall()
        else:
            raise RuntimeError("schema-3 active scan date-range ledger is incomplete")
        date_ranges = [
            {
                "range_id": str(row["range_id"]),
                "from_day": str(row["from_day"]),
                "to_day": str(row["to_day"]),
                "parent_range_id": row["parent_range_id"],
                "depth": int(row["depth"]),
                "state": str(row["state"]),
                "next_page": int(row["next_page"]),
                "total_count": (
                    int(row["total_count"])
                    if row["total_count"] is not None
                    else None
                ),
                "generation": int(row["generation"]),
                "boundary_total_count": (
                    int(row["boundary_total_count"])
                    if row["boundary_total_count"] is not None
                    else None
                ),
                "boundary_ref_sha256": row["boundary_ref_sha256"],
                "domain_matches_boundary": bool(row["domain_matches_boundary"]),
                "closing_boundary_total_count": (
                    int(row["closing_boundary_total_count"])
                    if row["closing_boundary_total_count"] is not None
                    else None
                ),
                "closing_boundary_ref_sha256": row["closing_boundary_ref_sha256"],
                "closing_boundary_generation": (
                    int(row["closing_boundary_generation"])
                    if row["closing_boundary_generation"] is not None
                    else None
                ),
                "closing_boundary_matches": bool(row["closing_boundary_matches"]),
                "scanned_high_watermark": int(row["scanned_high_watermark"]),
                "convergence_union_sha256": row["convergence_union_sha256"],
                "convergence_passes": int(row["convergence_passes"]),
                "convergence_last_generation": (
                    int(row["convergence_last_generation"])
                    if row["convergence_last_generation"] is not None
                    else None
                ),
                "bootstrap_pass_number": (
                    int(row["bootstrap_pass_number"])
                    if row["bootstrap_pass_number"] is not None
                    else None
                ),
                "opening_filtered_ref_sha256": row["opening_filtered_ref_sha256"],
                "closing_filtered_ref_sha256": row["closing_filtered_ref_sha256"],
            }
            for row in range_rows
        ]
        root_ranges = [
            row for row in date_ranges if row["parent_range_id"] is None
        ]
        if len(root_ranges) != 1:
            raise RuntimeError("schema-3 active scan must have exactly one date root")
        date_root = root_ranges[0]
        date_refs: list[str] = []
        date_pages: list[dict[str, Any]] = []
        for row in date_rows:
            references = _reference_list(
                row["references_json"],
                label=(
                    f"active date range {row['range_id']} page {row['page_number']}"
                ),
            )
            if row["range_state"] == "leaf_exact":
                date_refs.extend(references)
            date_pages.append(
                {
                    "range_id": str(row["range_id"]),
                    "range_state": str(row["range_state"]),
                    "page_number": int(row["page_number"]),
                    "records": int(row["records"]),
                    "total_count": int(row["total_count"]),
                    "raw_path": str(row["raw_path"]),
                    "sha256": str(row["sha256"]),
                    "references": references,
                }
            )

        residual_columns = {
            "cycle_id",
            "generation",
            "reference_number",
            "state",
            "status_id",
            "raw_path",
            "sha256",
            "run_id",
            "checked_at",
            "attempts",
            "error",
        }
        if not residual_columns.issubset(
            table_columns(connection, "active_scan_residual_checks")
        ):
            raise RuntimeError("schema-3 active scan residual-check ledger is incomplete")
        residual_rows = connection.execute(
            """
            SELECT reference_number,state,status_id,raw_path,sha256,run_id,
                   checked_at,attempts,error
            FROM active_scan_residual_checks
            WHERE cycle_id=? AND generation=?
            ORDER BY reference_number
            """,
            (cycle_id, generation),
        ).fetchall()
        residual_checks = [
            {
                "reference_number": str(row["reference_number"]),
                "state": str(row["state"]),
                "status_id": (
                    int(row["status_id"]) if row["status_id"] is not None else None
                ),
                "raw_path": row["raw_path"],
                "sha256": row["sha256"],
                "run_id": row["run_id"],
                "checked_at": row["checked_at"],
                "attempts": int(row["attempts"]),
                "error": row["error"],
            }
            for row in residual_rows
        ]

        proof_columns = {
            "cycle_id",
            "bootstrap_pass_number",
            "generation",
            "convergence_ordinal",
            "date_unique",
            "date_union_sha256",
            "residual_unique",
            "residual_union_sha256",
            "union_unique",
            "union_sha256",
            "bootstrap_union_sha256",
            "opening_filtered_total_count",
            "opening_filtered_ref_sha256",
            "closing_filtered_total_count",
            "closing_filtered_ref_sha256",
            "opening_boundary_total_count",
            "opening_boundary_ref_sha256",
            "closing_boundary_total_count",
            "closing_boundary_ref_sha256",
            "date_references_json",
            "residual_references_json",
            "union_references_json",
            "range_generations_json",
            "page_evidence_json",
            "residual_evidence_json",
            "boundary_evidence_json",
            "run_id",
            "closed_at",
        }
        if not proof_columns.issubset(
            table_columns(connection, "active_scan_date_generation_proofs")
        ):
            raise RuntimeError(
                "schema-3 active scan generation-proof ledger is incomplete"
            )
        proof_rows = connection.execute(
            """
            SELECT * FROM active_scan_date_generation_proofs
            WHERE cycle_id=? AND bootstrap_pass_number=?
            ORDER BY generation,convergence_ordinal
            """,
            (cycle_id, pass_number),
        ).fetchall()
        generation_proofs = [
            {
                "bootstrap_pass_number": int(row["bootstrap_pass_number"]),
                "generation": int(row["generation"]),
                "convergence_ordinal": int(row["convergence_ordinal"]),
                "date_unique": int(row["date_unique"]),
                "date_union_sha256": str(row["date_union_sha256"]),
                "residual_unique": int(row["residual_unique"]),
                "residual_union_sha256": str(row["residual_union_sha256"]),
                "union_unique": int(row["union_unique"]),
                "union_sha256": str(row["union_sha256"]),
                "bootstrap_union_sha256": str(row["bootstrap_union_sha256"]),
                "opening_filtered_total_count": int(
                    row["opening_filtered_total_count"]
                ),
                "opening_filtered_ref_sha256": str(
                    row["opening_filtered_ref_sha256"]
                ),
                "closing_filtered_total_count": int(
                    row["closing_filtered_total_count"]
                ),
                "closing_filtered_ref_sha256": str(
                    row["closing_filtered_ref_sha256"]
                ),
                "opening_boundary_total_count": int(
                    row["opening_boundary_total_count"]
                ),
                "opening_boundary_ref_sha256": str(
                    row["opening_boundary_ref_sha256"]
                ),
                "closing_boundary_total_count": int(
                    row["closing_boundary_total_count"]
                ),
                "closing_boundary_ref_sha256": str(
                    row["closing_boundary_ref_sha256"]
                ),
                "date_references": _reference_list(
                    row["date_references_json"],
                    label="active generation proof date",
                ),
                "residual_references": _reference_list(
                    row["residual_references_json"],
                    label="active generation proof residual",
                ),
                "union_references": _reference_list(
                    row["union_references_json"],
                    label="active generation proof union",
                ),
                "range_generations": _json_list(
                    row["range_generations_json"],
                    label="active generation proof ranges",
                ),
                "page_evidence": _json_list(
                    row["page_evidence_json"],
                    label="active generation proof pages",
                ),
                "residual_evidence": _json_list(
                    row["residual_evidence_json"],
                    label="active generation proof residual evidence",
                ),
                "boundary_evidence": _json_object(
                    row["boundary_evidence_json"],
                    label="active generation proof boundary evidence",
                ),
                "run_id": str(row["run_id"]),
                "closed_at": str(row["closed_at"]),
            }
            for row in proof_rows
        ]
    finally:
        connection.close()

    warehouse_root = database.resolve().parent
    raw_verification_by_path: dict[str, dict[str, Any]] = {}

    def verify_raw(raw_path: Any, sha256: Any, *, label: str) -> None:
        descriptor = _verify_official_raw_pointer(
            warehouse_root,
            raw_path,
            sha256,
            label=label,
        )
        previous = raw_verification_by_path.get(descriptor["raw_path"])
        if previous is not None and previous != descriptor:
            raise RuntimeError(f"{label} RAW pointer has conflicting evidence")
        raw_verification_by_path[descriptor["raw_path"]] = descriptor

    for page in bootstrap_pages:
        verify_raw(
            page["raw_path"],
            page["sha256"],
            label=f"active bootstrap page {page['page_number']}",
        )
    for page in date_pages:
        verify_raw(
            page["raw_path"],
            page["sha256"],
            label=(
                f"active date range {page['range_id']} page {page['page_number']}"
            ),
        )
    for row in residual_checks:
        if row["raw_path"] not in (None, "") or row["sha256"] not in (None, ""):
            verify_raw(
                row["raw_path"],
                row["sha256"],
                label=f"active residual {row['reference_number']}",
            )
    for proof in generation_proofs:
        proof_generation = proof["generation"]
        proof_pages = proof["page_evidence"]
        proof_residual = proof["residual_evidence"]
        boundary = proof["boundary_evidence"]
        if not isinstance(proof_pages, list) or not isinstance(proof_residual, list):
            raise RuntimeError("active generation proof evidence must be a list")
        if not isinstance(boundary, dict):
            raise RuntimeError("active generation boundary proof must be an object")
        for page in proof_pages:
            if not isinstance(page, dict):
                raise RuntimeError("active generation proof page must be an object")
            verify_raw(
                page.get("raw_path"),
                page.get("sha256"),
                label=f"active generation {proof_generation} page",
            )
        for row in proof_residual:
            if not isinstance(row, dict):
                raise RuntimeError("active generation residual proof must be an object")
            verify_raw(
                row.get("raw_path"),
                row.get("sha256"),
                label=f"active generation {proof_generation} residual",
            )
        for phase in ("opening", "closing"):
            phase_evidence = boundary.get(phase)
            if not isinstance(phase_evidence, dict):
                raise RuntimeError(
                    f"active generation {proof_generation} {phase} boundary is missing"
                )
            for lane in ("filtered", "unfiltered"):
                capture = phase_evidence.get(lane)
                if not isinstance(capture, dict):
                    raise RuntimeError(
                        f"active generation {proof_generation} {phase} {lane} "
                        "boundary is missing"
                    )
                verify_raw(
                    capture.get("raw_path"),
                    capture.get("sha256"),
                    label=(
                        f"active generation {proof_generation} {phase} {lane} boundary"
                    ),
                )
                _verify_boundary_raw_payload(
                    warehouse_root,
                    capture,
                    label=(
                        f"active generation {proof_generation} {phase} {lane} boundary"
                    ),
                )

    active_residual_refs = [
        row["reference_number"]
        for row in residual_checks
        if row["state"] == "verified_active"
        and row["status_id"] == 4
        and row["error"] in (None, "")
    ]
    union_refs = sorted(set(date_refs).union(active_residual_refs))
    bootstrap_head_refs: list[str] = next(
        (
            page["references"]
            for page in bootstrap_pages
            if page["page_number"] == 1
        ),
        [],
    )
    raw_verification_files = [
        raw_verification_by_path[path]
        for path in sorted(raw_verification_by_path)
    ]
    return {
        "schema_version": ACTIVE_SCAN_AUTHORITY_SCHEMA_VERSION,
        "cycle_id": cycle_id,
        "generation": generation,
        "raw_verification": {
            "mode": "export_time_official_warehouse_bytes",
            "verified_files": len(raw_verification_files),
            "verified_bytes": sum(item["bytes"] for item in raw_verification_files),
            "files": raw_verification_files,
        },
        "bootstrap": {
            "pass_number": pass_number,
            "pages": bootstrap_pages,
            "references": sorted(bootstrap_refs),
            "head_ref_sha256": reference_union_sha256(bootstrap_head_refs),
            "union_sha256": reference_union_sha256(bootstrap_refs),
        },
        "date_partition": {
            "generation": generation,
            "root": date_root,
            "ranges": date_ranges,
            "pages": date_pages,
            "references": sorted(date_refs),
            "union_sha256": reference_union_sha256(date_refs),
        },
        "residual_checks": residual_checks,
        "generation_proofs": generation_proofs,
        "authoritative_union": {
            "references": union_refs,
            "union_sha256": reference_union_sha256(union_refs),
        },
    }


def _load_cardinality_seal_authority(
    database: Path,
    active_scan: dict[str, Any],
    progress: dict[str, Any],
) -> dict[str, Any]:
    """Export schema-4 cardinality-seal evidence after replaying real RAW bytes."""

    if progress.get("strategy") != CARDINALITY_SEAL_STRATEGY:
        raise RuntimeError("schema-4 active census strategy is invalid")
    if progress.get("mode") != CARDINALITY_SEAL_MODE:
        raise RuntimeError("schema-4 active census mode is invalid")
    cycle_id = str(progress.get("cycle_id") or active_scan.get("cycle_id") or "")
    generation = progress.get("generation")
    if not cycle_id:
        raise RuntimeError("schema-4 active census cycle_id is missing")
    if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
        raise RuntimeError("schema-4 active census generation is invalid")

    uri = f"file:{database.resolve()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        state_columns = {
            "cycle_id",
            "strategy",
            "generation",
            "phase",
            "taxonomy_sha256",
            "boundary_total_count",
            "boundary_head_ref_sha256",
            "opening_evidence_json",
            "closing_evidence_json",
            "observed_unique",
            "unexplained_unique",
            "pending_candidates",
            "union_sha256",
            "bijection_sha256",
            "integrity_errors_json",
            "last_reset_reason",
            "page_ceiling_switches",
            "proof_chain",
            "created_at",
            "updated_at",
            "last_run_id",
        }
        if not state_columns.issubset(table_columns(connection, "active_census_state")):
            raise RuntimeError("schema-4 active census state ledger is incomplete")
        state_row = connection.execute(
            "SELECT * FROM active_census_state WHERE cycle_id=?",
            (cycle_id,),
        ).fetchone()
        if state_row is None:
            raise RuntimeError("schema-4 active census state row is missing")
        state = {key: state_row[key] for key in state_row.keys()}
        if state["strategy"] != CARDINALITY_SEAL_STRATEGY:
            raise RuntimeError("schema-4 active census state strategy mismatch")
        if int(state["generation"]) != generation:
            raise RuntimeError("schema-4 active census state generation mismatch")
        state["generation"] = int(state["generation"])
        state["boundary_total_count"] = (
            int(state["boundary_total_count"])
            if state["boundary_total_count"] is not None
            else None
        )
        state["observed_unique"] = int(state["observed_unique"])
        state["unexplained_unique"] = (
            int(state["unexplained_unique"])
            if state["unexplained_unique"] is not None
            else None
        )
        state["pending_candidates"] = int(state["pending_candidates"])
        state["page_ceiling_switches"] = int(state["page_ceiling_switches"])
        state["proof_chain"] = int(state["proof_chain"])
        state["integrity_errors"] = _json_list(
            state.pop("integrity_errors_json"), label="active census integrity errors"
        )
        state["opening_evidence"] = (
            _json_object(
                state.pop("opening_evidence_json"),
                label="active census opening boundary",
            )
            if state["opening_evidence_json"] not in (None, "")
            else None
        )
        state["closing_evidence"] = (
            _json_object(
                state.pop("closing_evidence_json"),
                label="active census closing boundary",
            )
            if state["closing_evidence_json"] not in (None, "")
            else None
        )

        taxonomy_columns = {
            "cycle_id",
            "kind",
            "endpoint",
            "values_json",
            "raw_path",
            "sha256",
            "url",
            "status",
            "content_type",
            "bytes",
            "captured_at",
        }
        if not taxonomy_columns.issubset(
            table_columns(connection, "active_census_taxonomy")
        ):
            raise RuntimeError("schema-4 active census taxonomy ledger is incomplete")
        taxonomy_rows_raw = connection.execute(
            "SELECT * FROM active_census_taxonomy WHERE cycle_id=? ORDER BY kind",
            (cycle_id,),
        ).fetchall()
        taxonomy_rows = []
        for row in taxonomy_rows_raw:
            taxonomy_rows.append(
                {
                    "kind": str(row["kind"]),
                    "endpoint": str(row["endpoint"]),
                    "values": _json_list(
                        row["values_json"], label=f"active census taxonomy {row['kind']}"
                    ),
                    "raw_path": str(row["raw_path"]),
                    "sha256": str(row["sha256"]),
                    "url": str(row["url"]),
                    "status": int(row["status"]),
                    "content_type": row["content_type"],
                    "bytes": int(row["bytes"]),
                    "captured_at": str(row["captured_at"]),
                }
            )

        node_columns = {
            "cycle_id",
            "generation",
            "node_id",
            "parent_node_id",
            "depth",
            "lens_name",
            "filters_json",
            "state",
            "next_page",
            "total_count",
            "page_count",
            "last_error",
            "superseded_reason",
            "superseded_union_sha256",
            "superseded_generation",
            "superseded_boundary_total_count",
            "created_at",
            "updated_at",
            "last_run_id",
        }
        if not node_columns.issubset(table_columns(connection, "active_census_nodes")):
            raise RuntimeError("schema-4 active census node ledger is incomplete")
        node_rows = connection.execute(
            "SELECT * FROM active_census_nodes WHERE cycle_id=? AND generation=? "
            "ORDER BY depth,node_id",
            (cycle_id, generation),
        ).fetchall()
        nodes = [
            {
                "node_id": str(row["node_id"]),
                "parent_node_id": row["parent_node_id"],
                "depth": int(row["depth"]),
                "lens_name": row["lens_name"],
                "filters": _json_object(
                    row["filters_json"], label=f"active census node {row['node_id']}"
                ),
                "state": str(row["state"]),
                "next_page": int(row["next_page"]),
                "total_count": (
                    int(row["total_count"]) if row["total_count"] is not None else None
                ),
                "page_count": int(row["page_count"]),
                "last_error": row["last_error"],
                "supersession": {
                    "reason": row["superseded_reason"],
                    "union_sha256": row["superseded_union_sha256"],
                    "generation": (
                        int(row["superseded_generation"])
                        if row["superseded_generation"] is not None
                        else None
                    ),
                    "boundary_total_count": (
                        int(row["superseded_boundary_total_count"])
                        if row["superseded_boundary_total_count"] is not None
                        else None
                    ),
                },
                "created_at": str(row["created_at"]),
                "updated_at": str(row["updated_at"]),
                "last_run_id": row["last_run_id"],
            }
            for row in node_rows
        ]
        node_by_id = {row["node_id"]: row for row in nodes}

        page_columns = {
            "cycle_id",
            "generation",
            "node_id",
            "page_number",
            "total_count",
            "records",
            "references_json",
            "mappings_json",
            "raw_path",
            "sha256",
            "url",
            "run_id",
            "accepted_at",
        }
        if not page_columns.issubset(table_columns(connection, "active_census_pages")):
            raise RuntimeError("schema-4 active census page ledger is incomplete")
        pages_raw = connection.execute(
            "SELECT * FROM active_census_pages WHERE cycle_id=? AND generation=? "
            "ORDER BY node_id,page_number",
            (cycle_id, generation),
        ).fetchall()
        pages = [
            {
                "node_id": str(row["node_id"]),
                "page_number": int(row["page_number"]),
                "total_count": int(row["total_count"]),
                "records": int(row["records"]),
                "references": _reference_list(
                    row["references_json"],
                    label=f"active census page {row['node_id']}:{row['page_number']}",
                ),
                "mappings": _normalise_mapping_list(
                    row["mappings_json"],
                    label=f"active census page {row['node_id']}:{row['page_number']}",
                ),
                "raw_path": str(row["raw_path"]),
                "sha256": str(row["sha256"]),
                "url": str(row["url"]),
                "run_id": str(row["run_id"]),
                "accepted_at": str(row["accepted_at"]),
            }
            for row in pages_raw
        ]

        candidate_columns = {
            "cycle_id",
            "generation",
            "reference_number",
            "source",
            "state",
            "status_id",
            "tender_id",
            "raw_path",
            "sha256",
            "url",
            "attempts",
            "error",
            "checked_at",
            "run_id",
        }
        if not candidate_columns.issubset(
            table_columns(connection, "active_census_candidates")
        ):
            raise RuntimeError("schema-4 active census candidate ledger is incomplete")
        candidates_raw = connection.execute(
            "SELECT * FROM active_census_candidates WHERE cycle_id=? AND generation=? "
            "ORDER BY reference_number",
            (cycle_id, generation),
        ).fetchall()

        def candidate_descriptor(
            row: sqlite3.Row, *, bound_generation: int
        ) -> dict[str, Any]:
            return {
                "cycle_id": cycle_id,
                "generation": bound_generation,
                "reference_number": str(row["reference_number"]),
                "source": str(row["source"]),
                "state": str(row["state"]),
                "status_id": (
                    int(row["status_id"]) if row["status_id"] is not None else None
                ),
                "tender_id": row["tender_id"],
                "raw_path": row["raw_path"],
                "sha256": row["sha256"],
                "url": row["url"],
                "attempts": int(row["attempts"]),
                "error": row["error"],
                "checked_at": row["checked_at"],
                "run_id": row["run_id"],
            }

        candidates = [
            candidate_descriptor(row, bound_generation=generation)
            for row in candidates_raw
        ]

        proof_columns = {
            "cycle_id",
            "generation",
            "convergence_ordinal",
            "boundary_total_count",
            "boundary_head_ref_sha256",
            "union_unique",
            "union_sha256",
            "bijection_sha256",
            "references_json",
            "mappings_json",
            "boundary_evidence_json",
            "taxonomy_sha256",
            "node_evidence_json",
            "candidate_evidence_json",
            "chain_number",
            "superseded_at",
            "superseded_reason",
            "run_id",
            "closed_at",
        }
        if not proof_columns.issubset(
            table_columns(connection, "active_census_generation_proofs")
        ):
            raise RuntimeError("schema-4 active census proof ledger is incomplete")
        all_proof_rows = connection.execute(
            "SELECT * FROM active_census_generation_proofs WHERE cycle_id=? "
            "ORDER BY chain_number,convergence_ordinal,generation",
            (cycle_id,),
        ).fetchall()
        proof_rows = [
            row
            for row in all_proof_rows
            if row["superseded_at"] is None
            and int(row["chain_number"] or 1) == state["proof_chain"]
        ]
        proofs: list[dict[str, Any]] = []
        for row in proof_rows:
            proof_generation = int(row["generation"])
            candidate_evidence = _json_object(
                row["candidate_evidence_json"],
                label="active census proof candidates",
            )
            generation_candidate_rows = connection.execute(
                "SELECT * FROM active_census_candidates WHERE cycle_id=? "
                "AND generation=? ORDER BY reference_number",
                (cycle_id, proof_generation),
            ).fetchall()
            unresolved = [
                item
                for item in generation_candidate_rows
                if item["state"] in {"pending", "error"}
            ]
            if unresolved:
                raise RuntimeError(
                    "active census proof generation still has pending candidates"
                )
            superseded = [
                candidate_descriptor(item, bound_generation=proof_generation)
                for item in generation_candidate_rows
                if item["state"] == "superseded_by_cardinality"
            ]
            superseded_references = [
                str(item["reference_number"]) for item in superseded
            ]
            if (
                candidate_evidence.get("superseded_by_cardinality_count")
                != len(superseded)
                or candidate_evidence.get("superseded_reference_sha256")
                != reference_union_sha256(superseded_references)
            ):
                raise RuntimeError(
                    "active census proof superseded-candidate summary mismatch"
                )
            candidate_evidence["superseded"] = superseded
            proofs.append(
                {
                    "generation": int(row["generation"]),
                    "convergence_ordinal": int(row["convergence_ordinal"]),
                    "chain_number": int(row["chain_number"]),
                    "superseded_at": row["superseded_at"],
                    "superseded_reason": row["superseded_reason"],
                    "boundary_total_count": int(row["boundary_total_count"]),
                    "boundary_head_ref_sha256": str(
                        row["boundary_head_ref_sha256"]
                    ),
                    "union_unique": int(row["union_unique"]),
                    "union_sha256": str(row["union_sha256"]),
                    "bijection_sha256": str(row["bijection_sha256"]),
                    "references": _reference_list(
                        row["references_json"], label="active census proof"
                    ),
                    "mappings": _normalise_mapping_list(
                        row["mappings_json"], label="active census proof"
                    ),
                    "boundary_evidence": _json_object(
                        row["boundary_evidence_json"],
                        label="active census proof boundary",
                    ),
                    "taxonomy_sha256": str(row["taxonomy_sha256"]),
                    "node_evidence": _json_object(
                        row["node_evidence_json"], label="active census proof nodes"
                    ),
                    "candidate_evidence": candidate_evidence,
                    "run_id": str(row["run_id"]),
                    "closed_at": str(row["closed_at"]),
                }
            )
        proof_ledger = [
            {
                "generation": int(row["generation"]),
                "convergence_ordinal": int(row["convergence_ordinal"]),
                "chain_number": int(row["chain_number"]),
                "superseded_at": row["superseded_at"],
                "superseded_reason": row["superseded_reason"],
            }
            for row in all_proof_rows
        ]
        expected_generations = [generation - 1, generation]
        if (
            len(proofs) != 2
            or [proof["generation"] for proof in proofs] != expected_generations
            or [proof["convergence_ordinal"] for proof in proofs] != [1, 2]
            or any(proof["chain_number"] != state["proof_chain"] for proof in proofs)
            or len({proof["run_id"] for proof in proofs}) != 2
        ):
            raise RuntimeError(
                "active census authority lacks the adjacent current two-proof chain"
            )
        superseded_ledger_rows = 0
        for ledger_row in proof_ledger:
            superseded_at = ledger_row["superseded_at"]
            superseded_reason = ledger_row["superseded_reason"]
            ledger_chain = ledger_row["chain_number"]
            if superseded_at is None:
                if superseded_reason is not None or ledger_chain != state["proof_chain"]:
                    raise RuntimeError(
                        "active census proof ledger has an interleaved active chain"
                    )
            else:
                if (
                    not str(superseded_at)
                    or not str(superseded_reason or "")
                    or ledger_chain >= state["proof_chain"]
                ):
                    raise RuntimeError(
                        "active census proof ledger supersession is invalid"
                    )
                superseded_ledger_rows += 1
        proof_status = progress.get("generation_proofs")
        if not isinstance(proof_status, dict) or any(
            (
                proof_status.get("recorded") != len(proofs),
                proof_status.get("recorded_total") != len(proof_ledger),
                proof_status.get("superseded") != superseded_ledger_rows,
                proof_status.get("chain_number") != state["proof_chain"],
                proof_status.get("generations") != expected_generations,
                proof_status.get("ordinals") != [1, 2],
                proof_status.get("authoritative") is not True,
            )
        ):
            raise RuntimeError("active census proof status disagrees with its ledger")
    finally:
        connection.close()

    warehouse_root = database.resolve().parent
    raw_by_path: dict[str, dict[str, Any]] = {}

    def verify_raw(raw_path: Any, sha256: Any, *, label: str) -> dict[str, Any]:
        descriptor = _verify_official_raw_pointer(
            warehouse_root, raw_path, sha256, label=label
        )
        prior = raw_by_path.get(descriptor["raw_path"])
        if prior is not None and prior != descriptor:
            raise RuntimeError(f"{label} RAW pointer has conflicting evidence")
        raw_by_path[descriptor["raw_path"]] = descriptor
        return descriptor

    def verify_boundary(capture: Any, *, label: str) -> None:
        if not isinstance(capture, dict):
            raise RuntimeError(f"{label} boundary evidence is missing")
        descriptor = verify_raw(capture.get("raw_path"), capture.get("sha256"), label=label)
        if capture.get("bytes") != descriptor["bytes"]:
            raise RuntimeError(f"{label} RAW byte count mismatch")
        _verify_active_list_capture(
            warehouse_root,
            capture,
            label=label,
            page_number=1,
            filters={},
            require_mappings=False,
        )

    def verify_page(page: dict[str, Any], *, filters: dict[str, str], label: str) -> None:
        descriptor = verify_raw(page.get("raw_path"), page.get("sha256"), label=label)
        capture = {
            **page,
            "status": 200,
            "content_type": "application/json",
            "bytes": descriptor["bytes"],
        }
        references, mappings, total = _verify_active_list_capture(
            warehouse_root,
            capture,
            label=label,
            page_number=int(page["page_number"]),
            filters=filters,
            require_mappings=True,
        )
        if references != page["references"] or mappings != page["mappings"]:
            raise RuntimeError(f"{label} page membership mismatch")
        if total != page["total_count"]:
            raise RuntimeError(f"{label} page total mismatch")

    def verify_candidate(candidate: dict[str, Any], *, label: str) -> None:
        state_name = str(candidate.get("state") or "")
        raw_path = candidate.get("raw_path")
        sha256 = candidate.get("sha256")
        if state_name == "superseded_by_cardinality":
            if (
                candidate.get("status_id") is not None
                or candidate.get("tender_id") is not None
                or raw_path is not None
                or sha256 is not None
                or candidate.get("url") is not None
                or candidate.get("error") != "union_reached_boundary_cardinality"
            ):
                raise RuntimeError(f"{label} superseded candidate is not provenance-safe")
            return
        if raw_path in (None, "") and sha256 in (None, ""):
            if candidate.get("state") not in {"pending", "error"}:
                raise RuntimeError(f"{label} checked candidate lacks RAW evidence")
            return
        verify_raw(raw_path, sha256, label=label)
        reference = str(candidate.get("reference_number") or "")
        if not reference.isdigit():
            raise RuntimeError(f"{label} candidate reference is not numeric")
        _assert_active_list_url(
            candidate.get("url"),
            page_number=1,
            filters={"ReferenceNumber": reference},
            label=label,
        )
        payload = _read_official_json(warehouse_root, raw_path, label=label)
        rows, _ = _list_payload_rows(payload, label=label)
        matching = [
            row for row in rows if str(row.get("referenceNumber") or "") == reference
        ]
        active_matches = [row for row in matching if row.get("tenderStatusId") == 4]
        if state_name in {"included", "verified_active"}:
            if len(active_matches) != 1:
                raise RuntimeError(f"{label} included candidate is not exactly one status-4 row")
            mapping = _row_reference_mapping(active_matches[0], label=label)
            if str(candidate.get("status_id")) != "4" or str(
                candidate.get("tender_id") or ""
            ) != mapping["tender_id"]:
                raise RuntimeError(f"{label} included candidate descriptor mismatch")
        elif state_name in {"excluded", "verified_nonactive"}:
            if active_matches:
                raise RuntimeError(f"{label} excluded candidate still has status 4")

    def verify_node_supersession(
        node: dict[str, Any],
        *,
        expected_generation: int,
        expected_union_sha256: str,
        expected_boundary_total: int,
        label: str,
    ) -> None:
        supersession = node.get("supersession")
        if not isinstance(supersession, dict) or set(supersession) != {
            "reason",
            "union_sha256",
            "generation",
            "boundary_total_count",
        }:
            raise RuntimeError(f"{label} supersession binding is incomplete")
        if node.get("state") == "superseded_by_cardinality":
            if supersession != {
                "reason": "union_reached_boundary_cardinality",
                "union_sha256": expected_union_sha256,
                "generation": expected_generation,
                "boundary_total_count": expected_boundary_total,
            }:
                raise RuntimeError(f"{label} cardinality supersession binding mismatch")
        elif any(value is not None for value in supersession.values()):
            raise RuntimeError(f"{label} unexpected supersession binding")

    verify_boundary(state["opening_evidence"], label="active census opening")
    if state["closing_evidence"] is not None:
        verify_boundary(state["closing_evidence"], label="active census closing")

    taxonomy: dict[str, list[dict[str, str]]] = {
        "booklet": [
            {"value": str(value), "label": str(value)} for value in range(7)
        ]
    }
    for row in taxonomy_rows:
        kind = row["kind"]
        expected_endpoint = ACTIVE_CENSUS_TAXONOMY_ENDPOINTS.get(kind)
        endpoint_value = urlsplit(row["endpoint"])
        endpoint_path = endpoint_value.path if endpoint_value.scheme else row["endpoint"]
        if expected_endpoint is None or endpoint_path != expected_endpoint:
            raise RuntimeError(f"active census taxonomy endpoint mismatch: {kind}")
        actual_url = urlsplit(row["url"])
        if (
            actual_url.scheme != "https"
            or actual_url.netloc != "tenders.etimad.sa"
            or actual_url.path != expected_endpoint
            or set(parse_qs(actual_url.query, keep_blank_values=True)) - {"_"}
        ):
            raise RuntimeError(f"active census taxonomy URL mismatch: {kind}")
        descriptor = verify_raw(
            row["raw_path"], row["sha256"], label=f"active census taxonomy {kind}"
        )
        if (
            row["status"] != 200
            or (
                row["content_type"]
                and "json" not in str(row["content_type"]).lower()
            )
            or row["bytes"] != descriptor["bytes"]
        ):
            raise RuntimeError(f"active census taxonomy response metadata mismatch: {kind}")
        values = _taxonomy_values_from_raw(
            _read_official_json(
                warehouse_root,
                row["raw_path"],
                label=f"active census taxonomy {kind}",
            ),
            kind=kind,
            label=f"active census taxonomy {kind}",
        )
        expected_values = [
            {"value": str(item.get("value") or ""), "label": str(item.get("label") or "")}
            for item in row["values"]
            if isinstance(item, dict)
        ]
        expected_values.sort(key=lambda item: item["value"])
        if values != expected_values or any(
            not item["value"] or not item["label"] for item in expected_values
        ):
            raise RuntimeError(f"active census taxonomy RAW values mismatch: {kind}")
        taxonomy[kind] = expected_values
    if set(taxonomy) != {"type", "area", "activity", "agency", "booklet"}:
        raise RuntimeError("active census taxonomy kinds are incomplete")
    taxonomy_sha = _cardinality_taxonomy_sha256(taxonomy)
    if taxonomy_sha != state["taxonomy_sha256"]:
        raise RuntimeError("active census taxonomy SHA-256 mismatch")

    for page in pages:
        node = node_by_id.get(page["node_id"])
        if node is None:
            raise RuntimeError("active census page has no frontier node")
        filters = {str(key): str(value) for key, value in node["filters"].items()}
        verify_page(
            page,
            filters=filters,
            label=f"active census page {page['node_id']}:{page['page_number']}",
        )
    for candidate in candidates:
        verify_candidate(
            candidate,
            label=f"active census candidate {candidate['reference_number']}",
        )

    for proof in proofs:
        boundary = proof["boundary_evidence"]
        verify_boundary(
            boundary.get("opening"),
            label=f"active census generation {proof['generation']} opening",
        )
        verify_boundary(
            boundary.get("closing"),
            label=f"active census generation {proof['generation']} closing",
        )
        node_evidence = proof["node_evidence"]
        proof_nodes = node_evidence.get("nodes")
        proof_pages = node_evidence.get("pages")
        if not isinstance(proof_nodes, list) or not isinstance(proof_pages, list):
            raise RuntimeError("active census proof node evidence is incomplete")
        proof_node_by_id: dict[str, dict[str, Any]] = {}
        for item in proof_nodes:
            if not isinstance(item, dict) or not item.get("node_id"):
                raise RuntimeError("active census proof node row is invalid")
            proof_node_by_id[str(item["node_id"])] = item
            verify_node_supersession(
                item,
                expected_generation=int(proof["generation"]),
                expected_union_sha256=str(proof["union_sha256"]),
                expected_boundary_total=int(proof["boundary_total_count"]),
                label=(
                    f"active census generation {proof['generation']} node "
                    f"{item['node_id']}"
                ),
            )
        for item in proof_pages:
            if not isinstance(item, dict):
                raise RuntimeError("active census proof page row is invalid")
            proof_node = proof_node_by_id.get(str(item.get("node_id") or ""))
            if proof_node is None:
                raise RuntimeError("active census proof page has no exact node")
            proof_filters_raw = proof_node.get("filters")
            if not isinstance(proof_filters_raw, dict):
                raise RuntimeError("active census proof node filters are invalid")
            proof_filters = {
                str(key): str(value) for key, value in proof_filters_raw.items()
            }
            verify_page(
                item,
                filters=proof_filters,
                label=(
                    f"active census generation {proof['generation']} page "
                    f"{item.get('node_id')}:{item.get('page_number')}"
                ),
            )
        candidate_evidence = proof["candidate_evidence"].get("checks")
        if not isinstance(candidate_evidence, list):
            raise RuntimeError("active census proof candidate evidence is incomplete")
        for item in candidate_evidence:
            if not isinstance(item, dict):
                raise RuntimeError("active census proof candidate row is invalid")
            verify_candidate(
                item,
                label=(
                    f"active census generation {proof['generation']} candidate "
                    f"{item.get('reference_number')}"
                ),
            )
        proof_membership_mappings: list[dict[str, Any]] = []
        for item in proof_pages:
            if not isinstance(item, dict):
                raise RuntimeError("active census proof page row is invalid")
            proof_membership_mappings.extend(
                _normalise_mapping_list(
                    item.get("mappings"), label="active census proof page"
                )
            )
        proof_membership_mappings.extend(
            {
                "reference_number": str(item["reference_number"]),
                "tender_id": str(item["tender_id"]),
            }
            for item in candidate_evidence
            if isinstance(item, dict)
            and item.get("state") in {"included", "verified_active"}
            and item.get("status_id") == 4
            and item.get("tender_id") not in (None, "")
        )
        _canonical_bijection_sha256(proof_membership_mappings)
        proof_mapping_by_ref = {
            str(item["reference_number"]): str(item["tender_id"])
            for item in proof_membership_mappings
        }
        proof_references = sorted(proof_mapping_by_ref)
        proof_mapping_rows = [
            {
                "reference_number": reference,
                "tender_id": proof_mapping_by_ref[reference],
            }
            for reference in proof_references
        ]
        if (
            proof_references != proof["references"]
            or proof_mapping_rows != proof["mappings"]
            or len(proof_references) != proof["boundary_total_count"]
            or reference_union_sha256(proof_references) != proof["union_sha256"]
            or _canonical_bijection_sha256(proof_membership_mappings)
            != proof["bijection_sha256"]
            or any(
                node.get("state") in {"pending", "blocked", "error"}
                for node in proof_nodes
                if isinstance(node, dict)
            )
        ):
            raise RuntimeError("active census proof membership replay is inconsistent")

    membership_mappings: list[dict[str, str]] = []
    for page in pages:
        page_mappings = page.get("mappings")
        if not isinstance(page_mappings, list):
            raise RuntimeError("active census current page mappings are invalid")
        for mapping in page_mappings:
            if not isinstance(mapping, dict):
                raise RuntimeError("active census current page mapping is invalid")
            reference = mapping.get("reference_number")
            tender_id = mapping.get("tender_id")
            if not isinstance(reference, str) or not isinstance(tender_id, str):
                raise RuntimeError("active census current page mapping is incomplete")
            membership_mappings.append(
                {"reference_number": reference, "tender_id": tender_id}
            )
    membership_mappings.extend(
        {
            "reference_number": candidate["reference_number"],
            "tender_id": str(candidate["tender_id"]),
        }
        for candidate in candidates
        if candidate["state"] in {"included", "verified_active"}
        and candidate["status_id"] == 4
        and candidate["tender_id"] not in (None, "")
    )
    canonical_membership: dict[str, str] = {}
    tender_to_ref: dict[str, str] = {}
    for mapping in membership_mappings:
        reference = mapping["reference_number"]
        tender_id = mapping["tender_id"]
        known_id = canonical_membership.get(reference)
        known_ref = tender_to_ref.get(tender_id)
        if known_id is not None and known_id != tender_id:
            raise RuntimeError("active census current reference mapping conflicts")
        if known_ref is not None and known_ref != reference:
            raise RuntimeError("active census current tender-id mapping conflicts")
        canonical_membership.setdefault(reference, tender_id)
        tender_to_ref.setdefault(tender_id, reference)
    membership_refs = sorted(canonical_membership)
    union_sha = reference_union_sha256(membership_refs)
    bijection_sha = _canonical_bijection_sha256(membership_mappings)
    if (
        state["observed_unique"] != len(membership_refs)
        or state["union_sha256"] != union_sha
        or state["bijection_sha256"] != bijection_sha
    ):
        raise RuntimeError("active census current membership ledger is inconsistent")
    for node in nodes:
        verify_node_supersession(
            node,
            expected_generation=generation,
            expected_union_sha256=union_sha,
            expected_boundary_total=len(membership_refs),
            label=f"active census node {node['node_id']}",
        )
    if (
        state["phase"] != "authoritative"
        or progress.get("union_authoritative") is not True
        or state["boundary_total_count"] != len(membership_refs)
        or state["pending_candidates"] != 0
        or state["integrity_errors"]
        or any(
            node.get("state") in {"pending", "blocked", "error"}
            for node in nodes
        )
    ):
        raise RuntimeError("active census authority is not an exact closed cardinality seal")

    raw_files = [raw_by_path[path] for path in sorted(raw_by_path)]
    return {
        "schema_version": CARDINALITY_SEAL_SCHEMA_VERSION,
        "strategy": CARDINALITY_SEAL_STRATEGY,
        "mode": CARDINALITY_SEAL_MODE,
        "cycle_id": cycle_id,
        "generation": generation,
        "phase": state["phase"],
        "union_authoritative": bool(progress.get("union_authoritative")),
        "partition_authoritative": bool(progress.get("partition_authoritative")),
        "absence_authoritative": bool(progress.get("absence_authoritative")),
        "completion_authoritative": bool(progress.get("completion_authoritative")),
        "complete": bool(progress.get("complete")),
        "raw_verification": {
            "mode": "export_time_official_warehouse_bytes",
            "verified_files": len(raw_files),
            "verified_bytes": sum(item["bytes"] for item in raw_files),
            "files": raw_files,
        },
        "state": state,
        "boundary": {
            "opening": state["opening_evidence"],
            "closing": state["closing_evidence"],
        },
        "taxonomy": {
            "sha256": taxonomy_sha,
            "values": taxonomy,
            "captures": taxonomy_rows,
        },
        "frontier": {"nodes": nodes, "pages": pages},
        "candidates": candidates,
        "membership": {
            "references": membership_refs,
            "mappings": sorted(
                (
                    {
                        "reference_number": reference,
                        "tender_id": canonical_membership[reference],
                    }
                    for reference in canonical_membership
                ),
                key=lambda row: row["reference_number"],
            ),
            "union_sha256": union_sha,
            "bijection_sha256": bijection_sha,
        },
        "generation_proofs": proofs,
        "generation_proof_ledger": proof_ledger,
    }


def selected_cardinality_authority(
    progress: Any,
) -> dict[str, Any] | None:
    """Select current schema-4 authority or its immutable last-authority fallback."""

    if not isinstance(progress, dict) or progress.get("schema_version") != 4:
        return None
    if progress.get("union_authoritative") is True:
        return progress
    last_authority = progress.get("last_authority")
    if (
        isinstance(last_authority, dict)
        and last_authority.get("schema_version") == 4
        and last_authority.get("union_authoritative") is True
    ):
        return last_authority
    return None


def active_refresh_sweep_complete(progress: Any) -> bool:
    """Return process completion without promoting interval coverage to authority."""

    if not isinstance(progress, dict):
        return False
    if progress.get("schema_version") == CARDINALITY_SEAL_SCHEMA_VERSION:
        authority = selected_cardinality_authority(progress)
        return bool(
            authority is not None
            and authority.get("completion_authoritative") is True
        )
    if progress.get("schema_version") != INTERVAL_COVERAGE_SCHEMA_VERSION:
        return False
    coverage = progress.get("coverage")
    authority_flags = (
        "instantaneous_snapshot_authoritative",
        "snapshot_authoritative",
        "union_authoritative",
        "partition_authoritative",
        "absence_authoritative",
        "completion_authoritative",
    )
    return bool(
        isinstance(coverage, dict)
        and coverage.get("complete") is True
        and coverage.get("raw_replay_valid") is True
        and all(progress.get(key) is False for key in authority_flags)
    )


def attach_active_scan_authority_descriptor(
    active_scan: dict[str, Any],
    authority_payload: dict[str, Any],
    authority_descriptor: dict[str, Any],
) -> dict[str, Any]:
    """Bind an authority asset to the status object that actually owns its seal."""

    date_fallback = active_scan.get("date_fallback")
    if not isinstance(date_fallback, dict):
        raise RuntimeError("active authority asset has no date-scan status owner")
    descriptor_owner = (
        selected_cardinality_authority(date_fallback)
        if date_fallback.get("schema_version") == CARDINALITY_SEAL_SCHEMA_VERSION
        else date_fallback
    )
    if descriptor_owner is None:
        raise RuntimeError("active authority asset has no status owner")
    for key in ("cycle_id", "generation"):
        if (
            authority_payload.get(key) is not None
            and descriptor_owner.get(key) != authority_payload.get(key)
        ):
            raise RuntimeError(f"active authority asset/status {key} mismatch")
    descriptor_owner["evidence_asset"] = {
        "schema_version": authority_payload["schema_version"],
        "file": ACTIVE_SCAN_AUTHORITY_FILE,
        "bytes": authority_descriptor["bytes"],
        "sha256": authority_descriptor["sha256"],
    }
    return descriptor_owner


def load_active_scan_authority(
    database: Path,
    active_scan: dict[str, Any],
) -> dict[str, Any] | None:
    """Dispatch authority export without letting legacy schemas claim schema-4 seal."""

    progress = active_scan.get("date_fallback")
    if not isinstance(progress, dict):
        return None
    if progress.get("schema_version") == CARDINALITY_SEAL_SCHEMA_VERSION:
        authority_progress = selected_cardinality_authority(progress)
        return (
            _load_cardinality_seal_authority(
                database, active_scan, authority_progress
            )
            if authority_progress is not None
            else None
        )
    if progress.get("schema_version") == INTERVAL_COVERAGE_SCHEMA_VERSION:
        # A progressive interval sweep spans multiple observation times.  It is
        # useful coverage evidence, but it cannot be serialized as an
        # instantaneous union/absence authority asset.
        return None
    return _load_hybrid_active_scan_authority(database, active_scan)


def database_times(connection: sqlite3.Connection) -> dict[str, Any]:
    result: dict[str, Any] = {
        "official": None,
        "phase0": None,
        "phase0_open": None,
        "phase0_awarded": None,
        "phase0_basis": None,
        "meta": database_meta(connection),
    }
    tender_columns = table_columns(connection, "tenders")
    official_conditions: list[str] = []
    if "official_json" in tender_columns:
        official_conditions.append(
            "(official_json IS NOT NULL AND TRIM(official_json) NOT IN ('','{}'))"
        )
    if "source_kind" in tender_columns:
        official_conditions.append("LOWER(COALESCE(source_kind,'')) LIKE 'official%'")
    if "last_seen_at" in tender_columns and official_conditions:
        result["official"] = connection.execute(
            "SELECT MAX(last_seen_at) FROM tenders WHERE "
            + " OR ".join(official_conditions)
        ).fetchone()[0]
    baseline_columns = table_columns(connection, "baseline_tenders")
    if "source_fetched_at" in baseline_columns:
        result["phase0"] = connection.execute(
            "SELECT MAX(COALESCE(source_fetched_at,imported_at)) FROM baseline_tenders"
        ).fetchone()[0]
        result["phase0_basis"] = "source_fetched_at_with_null_import_fallback"
        for state in ("open", "awarded"):
            result[f"phase0_{state}"] = connection.execute(
                "SELECT MAX(COALESCE(source_fetched_at,imported_at)) "
                "FROM baseline_tenders WHERE seed_state=?",
                (state,),
            ).fetchone()[0]
    elif "imported_at" in baseline_columns:
        result["phase0"] = connection.execute(
            "SELECT MAX(imported_at) FROM baseline_tenders"
        ).fetchone()[0]
        result["phase0_basis"] = "imported_at_legacy_schema_fallback"
        for state in ("open", "awarded"):
            result[f"phase0_{state}"] = connection.execute(
                "SELECT MAX(imported_at) FROM baseline_tenders WHERE seed_state=?",
                (state,),
            ).fetchone()[0]
    return result


def component_success_at(row: dict[str, Any]) -> Any:
    """Return the last verified-success timestamp, including legacy schemas."""
    if not row:
        return None
    if "success_checked_at" in row:
        return row.get("success_checked_at")
    return row.get("checked_at") if not row.get("error") else None


def canonical_official_region(value: Any) -> str | None:
    """Accept only the 13 official parser labels, joined by an Arabic comma."""
    text = str(value or "").strip()
    if not text:
        return None
    labels = [label.strip() for label in text.split("،")]
    if not labels or any(label not in OFFICIAL_REGION_LABELS for label in labels):
        return None
    return "، ".join(labels)


def official_relations_region_overlay(
    row: dict[str, Any],
) -> dict[str, Any] | None:
    """Project an evidence-backed official region from one successful relation row."""
    success_checked_at = component_success_at(row)
    parsed = parse_json_value(row.get("parsed_json"))
    if not success_checked_at or not isinstance(parsed, dict):
        return None
    region = canonical_official_region(parsed.get("region"))
    raw_path = str(row.get("raw_path") or "").strip()
    sha256 = str(row.get("sha256") or "").strip()
    parser_version = row.get("parser_version")
    if (
        region is None
        or not raw_path
        or re.fullmatch(r"[0-9a-fA-F]{64}", sha256) is None
        or not isinstance(parser_version, int)
        or isinstance(parser_version, bool)
    ):
        return None
    if parser_version < 1:
        return None
    return {
        "region": region,
        "_provenance": {
            "sources": [
                {
                    "id": OFFICIAL_COMPONENT_SOURCE_ID,
                    "fetchedAt": success_checked_at,
                    "layer": "official_periodic.sqlite3:components",
                }
            ],
            "fieldSources": {"region": OFFICIAL_COMPONENT_SOURCE_ID},
        },
        "_freshness": {"relationsCheckedAt": success_checked_at},
        "_evidence": {
            "relations": {
                "rawPath": raw_path,
                "sha256": sha256,
                "parserVersion": parser_version,
                "lastAttemptedAt": row.get("checked_at"),
                "lastError": row.get("error"),
            }
        },
    }


def official_projection_record(
    raw: dict[str, Any],
    *,
    baseline_info: dict[str, Any] | None,
    component_rows: dict[str, dict[str, Any]],
    groups: list[dict[str, Any]],
    latest_version: dict[str, Any] | None,
    raw_sha_by_path: dict[str, str],
) -> dict[str, Any]:
    """Mirror the official warehouse export contract without importing its repo."""
    baseline_info = baseline_info or {}
    official_payload = parse_json_cell(raw.get("official_json")) or {}
    seed = (
        parse_json_cell(raw.get("seed_json"))
        or parse_json_cell(baseline_info.get("record_json"))
        or {}
    )
    dates = component_rows.get("dates") or {}
    relations = component_rows.get("relations") or {}
    awards = component_rows.get("awards") or {}
    award_payload = parse_json_cell(raw.get("award_json")) or {}
    official_observed = bool(official_payload)
    baseline_source_fetched_at = baseline_info.get("source_fetched_at")
    baseline_fetched_at = first_present(
        baseline_source_fetched_at, baseline_info.get("imported_at")
    )
    baseline_layer = baseline_info.get("source_layer")

    component_details = {
        component: parsed
        for component, row in (("dates", dates), ("relations", relations))
        if component_success_at(row)
        if (parsed := parse_json_value(row.get("parsed_json"))) is not None
    }
    flags = {
        key: value
        for key, value in sorted(official_payload.items())
        if isinstance(value, bool)
        or key in OFFICIAL_FLAG_FIELDS
        and value is not None
    }
    field_sources = {
        field: (
            "etimad_official_visitor"
            if official_payload.get(official_key) is not None
            else "phase0_baseline"
        )
        for field, official_key in {
            "name": "tenderName",
            "num": "tenderNumber",
            "agency": "agencyName",
            "branch": "branchName",
            "type": "tenderTypeName",
            "activity": "tenderActivityName",
            "deadline": "lastOfferPresentationDate",
            "status": "tenderStatusName",
        }.items()
    }
    relations_region_overlay = official_relations_region_overlay(relations)
    if relations_region_overlay:
        field_sources["region"] = OFFICIAL_COMPONENT_SOURCE_ID
    elif meaningful(seed.get("region")):
        field_sources["region"] = "phase0_baseline"
    sources: list[dict[str, Any]] = []
    if official_observed:
        sources.append(
            {
                "id": "etimad_official_visitor",
                "fetchedAt": raw.get("last_seen_at"),
                "layer": "official_periodic.sqlite3:tenders",
            }
        )
    component_checked_at = max(
        filter(
            None,
            (
                component_success_at(dates),
                component_success_at(relations),
                component_success_at(awards),
            ),
        ),
        default=None,
    )
    if dates or relations or awards:
        sources.append(
            {
                "id": "etimad_official_components",
                "fetchedAt": component_checked_at,
                "layer": "official_periodic.sqlite3:components",
            }
        )
        if component_details:
            field_sources["componentDetails"] = "etimad_official_components"
    if raw.get("baseline_linked") or baseline_info:
        sources.append(
            {
                "id": "phase0_baseline",
                "fetchedAt": baseline_fetched_at,
                "layer": baseline_layer,
            }
        )

    list_raw_path = (latest_version or {}).get("raw_path")
    record: dict[str, Any] = {
        "name": official_payload.get("tenderName") or raw.get("tender_name") or seed.get("name"),
        "url": raw.get("official_url") or seed.get("url"),
        "num": official_payload.get("tenderNumber") or raw.get("tender_number") or seed.get("num"),
        "ref": str(raw["reference_number"]),
        "agency": official_payload.get("agencyName") or raw.get("agency_name") or seed.get("agency"),
        "branch": official_payload.get("branchName") or raw.get("branch_name") or seed.get("branch"),
        "type": official_payload.get("tenderTypeName") or raw.get("tender_type_name") or seed.get("type"),
        "activity": official_payload.get("tenderActivityName") or raw.get("activity_name") or seed.get("activity"),
        "region": (
            (relations_region_overlay or {}).get("region")
            or raw.get("region")
            or seed.get("region")
        ),
        "deadline": first_present(
            official_payload.get("lastOfferPresentationDate"),
            raw.get("deadline"),
            seed.get("deadline"),
        ),
        "deadlineHijri": first_present(
            official_payload.get("lastOfferPresentationDateHijri"),
            official_payload.get("lastOfferPresentationHijriDate"),
            seed.get("deadlineHijri"),
        ),
        "expectedAwardAt": raw.get("expected_award_at"),
        "submit": raw.get("submitted_at"),
        "status": official_payload.get("tenderStatusName") or raw.get("tender_status_name") or seed.get("status"),
        "statusId": first_present(official_payload.get("tenderStatusId"), raw.get("tender_status_id")),
        "tenderTypeId": first_present(official_payload.get("tenderTypeId"), raw.get("tender_type_id")),
        "activityId": first_present(official_payload.get("tenderActivityId"), raw.get("activity_id")),
        "days": official_payload.get("remainingDays"),
        "hoursLeft": official_payload.get("remainingHours"),
        "minutesLeft": official_payload.get("remainingMins"),
        "buyingCost": first_present(official_payload.get("buyingCost"), seed.get("buyingCost")),
        "condetionalBookletPrice": first_present(
            official_payload.get("condetionalBookletPrice"),
            seed.get("condetionalBookletPrice"),
            seed.get("bookletPrice"),
        ),
        "financialFees": first_present(official_payload.get("financialFees"), seed.get("financialFees")),
        "invitationCost": first_present(official_payload.get("invitationCost"), seed.get("invitationCost")),
        "lastEnqueriesDate": first_present(
            official_payload.get("lastEnqueriesDate"), seed.get("lastEnqueriesDate")
        ),
        "lastEnqueriesDateHijri": first_present(
            official_payload.get("lastEnqueriesDateHijri"), seed.get("lastEnqueriesDateHijri")
        ),
        "offersOpeningDate": first_present(
            official_payload.get("offersOpeningDate"), seed.get("offersOpeningDate")
        ),
        "offersOpeningDateHijri": first_present(
            official_payload.get("offersOpeningDateHijri"), seed.get("offersOpeningDateHijri")
        ),
        "flags": flags,
        "componentDetails": component_details,
        "firstSeen": raw.get("first_seen_at"),
        "lastSeen": raw.get("last_seen_at"),
        "officialTenderId": raw.get("official_tender_id"),
        "officialTenderIdString": raw.get("tender_id_string"),
        "source": "etimad_official_periodic",
        "sourceKind": raw.get("source_kind"),
        "baselineLinked": bool(raw.get("baseline_linked")),
        "awardState": raw.get("award_state"),
        "awardMode": raw.get("award_mode"),
        "lastAwardCheckedAt": raw.get("last_award_checked_at"),
        "nextAwardCheckAt": raw.get("next_award_check_at"),
        "_source": "etimad_official_periodic",
        "_provenance": {
            "primary": (
                "etimad_official_visitor" if official_observed else "phase0_baseline"
            ),
            "sources": sources,
            "baselineLayer": baseline_layer,
            "fieldSources": field_sources,
            "defaultFieldSource": (
                "etimad_official_visitor" if official_observed else "phase0_baseline"
            ),
        },
        "_freshness": {
            "firstObservedAt": raw.get("first_seen_at"),
            "lastOfficialObservedAt": raw.get("last_seen_at") if official_observed else None,
            "baselineFetchedAt": baseline_fetched_at,
            "baselineSourceFetchedAt": baseline_source_fetched_at,
            "baselineImportedAt": baseline_info.get("imported_at"),
            "datesCheckedAt": component_success_at(dates),
            "relationsCheckedAt": component_success_at(relations),
            "awardCheckedAt": raw.get("last_award_checked_at"),
        },
        "_evidence": {
            "list": {
                "rawPath": list_raw_path,
                "sha256": raw_sha_by_path.get(str(list_raw_path)) if list_raw_path else None,
                "payloadHash": raw.get("stable_hash"),
            },
            "dates": {
                "rawPath": dates.get("raw_path"),
                "sha256": dates.get("sha256"),
                "parserVersion": dates.get("parser_version"),
                "lastAttemptedAt": dates.get("checked_at"),
                "lastError": dates.get("error"),
            },
            "relations": {
                "rawPath": relations.get("raw_path"),
                "sha256": relations.get("sha256"),
                "parserVersion": relations.get("parser_version"),
                "lastAttemptedAt": relations.get("checked_at"),
                "lastError": relations.get("error"),
            },
            "awards": {
                "rawPath": awards.get("raw_path"),
                "sha256": awards.get("sha256"),
                "parserVersion": awards.get("parser_version"),
                "lastAttemptedAt": awards.get("checked_at"),
                "lastError": awards.get("error"),
            },
        },
    }
    record.update(award_fields(award_payload))
    award_groups = award_payload.get("groups") or groups
    if award_groups:
        record["groups"] = deepcopy(award_groups)
        record["groupCount"] = len(award_groups)
        record["pendingGroupCount"] = sum(
            1
            for group in award_groups
            if not group.get("announced") and not group.get("complete")
        )
    if award_payload:
        record["awardComplete"] = bool(award_payload.get("complete", True))
        record["allGroupsAnnounced"] = bool(
            award_payload.get("allGroupsAnnounced", award_payload.get("announced"))
        )
    return record


def load_official_database(
    path: Path,
) -> tuple[
    dict[str, OrderedDict[str, dict[str, Any]]],
    OrderedDict[str, dict[str, Any]],
    dict[str, Any],
]:
    """Read Phase-0 record_json and current official overlays from one SQLite DB."""
    uri = f"file:{path.resolve()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    baseline: dict[str, OrderedDict[str, dict[str, Any]]] = {
        "open": OrderedDict(),
        "awarded": OrderedDict(),
    }
    official: OrderedDict[str, dict[str, Any]] = OrderedDict()
    try:
        baseline_columns = table_columns(connection, "baseline_tenders")
        baseline_count = (
            connection.execute("SELECT COUNT(*) FROM baseline_tenders").fetchone()[0]
            if baseline_columns
            else 0
        )
        baseline_info: dict[str, dict[str, Any]] = {}
        if "record_json" in baseline_columns:
            fetched_expression = (
                "source_fetched_at"
                if "source_fetched_at" in baseline_columns
                else "NULL AS source_fetched_at"
            )
            rows = connection.execute(
                "SELECT reference_number,seed_state,source_layer,imported_at,record_json,"
                + fetched_expression
                + " FROM baseline_tenders ORDER BY rowid"
            )
            for row in rows:
                payload = parse_json_cell(row["record_json"])
                if not payload:
                    continue
                info = dict(row)
                baseline_info[str(row["reference_number"])] = info
                state = "awarded" if row["seed_state"] == "awarded" else "open"
                if (
                    "source_fetched_at" in baseline_columns
                    and row["source_fetched_at"] is not None
                ):
                    freshness_basis = "source_fetched_at"
                    fetched_at = row["source_fetched_at"]
                else:
                    freshness_basis = "imported_at_null_or_legacy_fallback"
                    fetched_at = row["imported_at"]
                record = seed_record(
                    payload,
                    source_id="phase0_baseline",
                    fetched_at=fetched_at,
                    layer=row["source_layer"],
                )
                record["ref"] = str(row["reference_number"])
                record["_freshness"] = {
                    "baselineFetchedAt": fetched_at,
                    "baselineSourceFetchedAt": row["source_fetched_at"],
                    "baselineImportedAt": row["imported_at"],
                    "baselineFreshnessBasis": freshness_basis,
                }
                baseline[state][record["ref"]] = record
        loaded_baseline = sum(len(values) for values in baseline.values())
        if baseline_count and loaded_baseline not in (0, baseline_count):
            raise RuntimeError(
                f"baseline record_json incomplete: loaded {loaded_baseline}/{baseline_count}"
            )

        components: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        if table_columns(connection, "components"):
            for row in connection.execute("SELECT * FROM components"):
                value = dict(row)
                components[str(value["reference_number"])][str(value["component"])] = value

        for records in baseline.values():
            for ref, record in list(records.items()):
                region_overlay = official_relations_region_overlay(
                    components.get(ref, {}).get("relations") or {}
                )
                if region_overlay:
                    records[ref] = official_overlay(
                        record,
                        region_overlay,
                        source_id=OFFICIAL_COMPONENT_SOURCE_ID,
                    )

        latest_versions: dict[str, dict[str, Any]] = {}
        if table_columns(connection, "tender_versions"):
            for row in connection.execute("SELECT * FROM tender_versions ORDER BY id"):
                value = dict(row)
                latest_versions[str(value["reference_number"])] = value

        raw_sha_by_path: dict[str, str] = {}
        if table_columns(connection, "raw_manifest"):
            for row in connection.execute("SELECT raw_path,sha256 FROM raw_manifest"):
                raw_sha_by_path[str(row["raw_path"])] = str(row["sha256"])

        tender_columns = table_columns(connection, "tenders")
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        if table_columns(connection, "award_groups"):
            for row in connection.execute(
                "SELECT * FROM award_groups WHERE active=1 ORDER BY reference_number, ordinal"
            ):
                parsed = parse_json_cell(row["parsed_json"]) or {}
                group = deepcopy(parsed)
                group.update(
                    {
                        "groupId": row["group_id"],
                        "groupKey": row["group_key"],
                        "label": row["group_label"],
                        "status": row["last_status"],
                        "complete": bool(row["success_cycle_id"]),
                        "checkedAt": row["last_checked_at"],
                    }
                )
                groups[str(row["reference_number"])].append(group)

        if tender_columns:
            for row in connection.execute("SELECT * FROM tenders ORDER BY rowid"):
                raw = dict(row)
                ref = str(raw["reference_number"])
                official_record = official_projection_record(
                    raw,
                    baseline_info=baseline_info.get(ref),
                    component_rows=components.get(ref, {}),
                    groups=groups.get(ref, []),
                    latest_version=latest_versions.get(ref),
                    raw_sha_by_path=raw_sha_by_path,
                )
                official[ref] = official_record
        return baseline, official, database_times(connection)
    finally:
        connection.close()


def load_official_layers(directory: Path) -> tuple[OrderedDict[str, dict[str, Any]], dict[str, str | None]]:
    overlays: OrderedDict[str, dict[str, Any]] = OrderedDict()
    times: dict[str, str | None] = {"official": None}
    list_path = directory / "layers" / "01_official_tender_delta.json"
    awards_path = directory / "layers" / "02_periodic_awards.json"
    if list_path.exists():
        layer = load_json(list_path)
        fetched_at = layer.get("fetched_at")
        times["official"] = fetched_at
        overlays.update(
            phase0_map(
                layer.get("records") or [],
                fetched_at=fetched_at,
                layer=list_path.name,
                source_id="etimad_official_periodic",
            )
        )
    if awards_path.exists():
        layer = load_json(awards_path)
        fetched_at = layer.get("fetched_at")
        times["official"] = max(filter(None, (times.get("official"), fetched_at)), default=None)
        for raw in layer.get("records") or []:
            ref = record_ref(raw)
            if not ref:
                continue
            award = seed_record(
                raw,
                source_id="etimad_official_periodic",
                fetched_at=fetched_at,
                layer=awards_path.name,
            )
            overlays[ref] = official_overlay(overlays.get(ref), award)
    return overlays, times


def apply_name_cache(records: Iterable[dict[str, Any]], out: Path) -> None:
    cache_path = out / "open_name_ar_cache.json"
    if not cache_path.exists():
        return
    cache = load_json(cache_path, {})
    for record in records:
        translation = cache.get(str(record.get("ref")))
        if not isinstance(translation, dict) or not translation.get("name_ar"):
            continue
        record["name_en"] = translation.get("name_en") or record.get("name")
        record["name_ar"] = translation["name_ar"]


def shard_for_ref(ref: str, count: int = SHARD_COUNT) -> int:
    return hashlib.sha256(str(ref).encode("utf-8")).digest()[0] % count


def index_part_for_ref(
    ref: str,
    count: int = AWARDED_INDEX_PART_COUNT,
) -> int:
    """Return the stable searchable-index part for a tender reference."""
    return hashlib.sha256(str(ref).encode("utf-8")).digest()[0] % count


def awarded_index_part_config() -> dict[str, Any]:
    """Return the versioned index-part contract shared with the static UI."""
    return {
        "formatVersion": AWARDED_INDEX_PART_FORMAT_VERSION,
        "count": AWARDED_INDEX_PART_COUNT,
        "pathTemplate": "awarded_index_parts/{part}.json",
        "algorithm": AWARDED_INDEX_PART_ALGORITHM,
    }


def searchable_award(record: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        key: record.get(key)
        for key in INDEX_FIELDS
        if meaningful(record.get(key))
    }
    result["ref"] = str(record["ref"])
    result["_detailShard"] = f"{shard_for_ref(result['ref']):02d}"
    if meaningful(record.get("_source")):
        result["_source"] = record["_source"]
    return result


def pack(records: list[dict[str, Any]], **meta: Any) -> dict[str, Any]:
    return {
        "meta": {"schemaVersion": SCHEMA_VERSION, **meta},
        "count": len(records),
        "records": records,
    }


def write_awarded_index(
    out: Path,
    assets: dict[str, dict[str, Any]],
    records: list[dict[str, Any]],
    *,
    partial: bool,
    completeness_basis: list[str],
) -> dict[str, Any]:
    """Write a small descriptor plus stable, independently checksummed parts."""
    config = awarded_index_part_config()
    buckets: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        buckets[index_part_for_ref(str(record["ref"]))].append(record)

    part_dir = out / "awarded_index_parts"
    part_dir.mkdir(parents=True, exist_ok=True)
    expected_parts: set[str] = set()
    part_descriptors: list[dict[str, Any]] = []
    for part in range(AWARDED_INDEX_PART_COUNT):
        part_id = f"{part:02d}"
        filename = config["pathTemplate"].replace("{part}", part_id)
        expected_parts.add(Path(filename).name)
        rows = sorted(buckets.get(part, []), key=lambda row: str(row["ref"]))
        payload = pack(
            rows,
            dataset="awarded_index_part",
            part=part_id,
            partCount=AWARDED_INDEX_PART_COUNT,
            formatVersion=AWARDED_INDEX_PART_FORMAT_VERSION,
            algorithm=AWARDED_INDEX_PART_ALGORITHM,
        )
        descriptor = write_asset(
            out,
            filename,
            payload,
            count=len(rows),
            role="awarded_search_index_part",
        )
        assets[filename] = descriptor
        part_descriptors.append(
            {
                "part": part_id,
                "file": filename,
                "count": len(rows),
                "bytes": descriptor["bytes"],
                "sha256": descriptor["sha256"],
            }
        )

    for stale in part_dir.glob("*.json"):
        if stale.name not in expected_parts:
            stale.unlink()

    root_payload = {
        "meta": {
            "schemaVersion": SCHEMA_VERSION,
            "dataset": "awarded",
            "partial": partial,
            "detailShards": SHARD_COUNT,
            "completenessBasis": completeness_basis,
            "indexParts": config,
        },
        "count": len(records),
        "parts": part_descriptors,
    }
    assets["awarded_index.json"] = write_asset(
        out,
        "awarded_index.json",
        root_payload,
        role="awarded_search_index_descriptor",
    )
    return root_payload


def asset_count(out: Path, filename: str) -> int | None:
    value = load_json(out / filename)
    if isinstance(value, dict) and isinstance(value.get("count"), int):
        return value["count"]
    if isinstance(value, dict) and isinstance(value.get("records"), list):
        return len(value["records"])
    return None


def export_plus_catalogue(root: Path, out: Path, assets: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Refresh non-tender Phase-0 assets when the Plus warehouse is available."""
    layers = root / "layers"

    def layer(name: str) -> dict[str, Any]:
        return load_json(layers / name)

    winner = layer("82_winnerfacet.json") if (layers / "82_winnerfacet.json").exists() else {}
    if winner:
        payload = pack(
            winner.get("records") or [],
            sourceLayer="82_winnerfacet.json",
            sourceFetchedAt=winner.get("fetched_at"),
            partial=bool(winner.get("partial")),
        )
        assets["winnerfacet.json"] = write_asset(
            out, "winnerfacet.json", payload, count=payload["count"], role="entity_catalogue"
        )

    mappings = (
        ("62_ssr_tenders.json", "ssr_tenders.json", "ssr_sample"),
        ("83_activities_from_open_facets.json", "activities.json", "taxonomy"),
        ("86_agencies_from_open_facets.json", "agencies.json", "taxonomy"),
        ("87_types_from_open_facets.json", "types.json", "taxonomy"),
        ("08_companies_from_sitemap.json", "companies_sitemap.json", "sitemap"),
        ("09_agencies_from_sitemap.json", "agencies_sitemap.json", "sitemap"),
        ("10_tender_urls_from_sitemap.json", "tender_refs_sitemap.json", "sitemap"),
        ("07_activities_from_sitemap.json", "activities_sitemap.json", "sitemap"),
    )
    for source, target, role in mappings:
        value = layer(source)
        payload = pack(value.get("records") or [], sourceLayer=source, sourceFetchedAt=value.get("fetched_at"))
        assets[target] = write_asset(out, target, payload, count=payload["count"], role=role)

    facets = layer("80_facets_open.json")
    facet_payload = {
        "meta": {
            "schemaVersion": SCHEMA_VERSION,
            "sourceLayer": "80_facets_open.json",
            "sourceFetchedAt": facets.get("fetched_at"),
        },
        "grand": facets.get("grand"),
        "active": facets.get("active"),
        "soon": facets.get("soon"),
        "types": facets.get("types") or [],
        "activities_count": len(facets.get("activities") or []),
        "agencies_count": len(facets.get("agencies") or []),
    }
    assets["facets_open.json"] = write_asset(out, "facets_open.json", facet_payload, role="facets")

    api_agencies = layer("60_api_agencies.json").get("records") or []
    payload = pack(api_agencies, sourceLayer="60_api_agencies.json")
    assets["agencies_api.json"] = write_asset(out, "agencies_api.json", payload, count=len(api_agencies), role="entity_catalogue")

    api_companies = []
    for row in layer("61_api_companies.json").get("records") or []:
        api_companies.append(
            {
                "name": row.get("display") or row.get("key"),
                "key": row.get("key"),
                "wins": row.get("wins"),
                "bids": row.get("bids"),
                "total": row.get("total"),
            }
        )
    payload = pack(api_companies, sourceLayer="61_api_companies.json")
    assets["companies_api.json"] = write_asset(out, "companies_api.json", payload, count=len(api_companies), role="entity_catalogue")

    companies = []
    for row in layer("63_ssr_companies.json").get("records") or []:
        if not isinstance(row, dict):
            continue
        companies.append(
            {
                "name": row.get("name") or row.get("company") or row.get("display") or "",
                "wins": row.get("wins") or row.get("awards") or row.get("col1"),
                "bids": row.get("bids") or row.get("participations") or row.get("col2"),
                "value": row.get("value") or row.get("total") or row.get("col3"),
            }
        )
    for target in ("companies_ssr.json", "companies.json"):
        payload = pack(companies, sourceLayer="63_ssr_companies.json")
        assets[target] = write_asset(out, target, payload, count=len(companies), role="entity_catalogue")

    agencies = []
    for row in layer("64_ssr_agencies.json").get("records") or []:
        if isinstance(row, dict):
            agencies.append(
                {
                    "name": row.get("name") or "",
                    "count": row.get("count") or row.get("n") or row.get("tenders"),
                }
            )
    payload = pack(agencies, sourceLayer="64_ssr_agencies.json")
    assets["agencies_ssr.json"] = write_asset(out, "agencies_ssr.json", payload, count=len(agencies), role="entity_catalogue")

    taxonomy = layer("72_taxonomy_observed.json")
    taxonomy_payload = {
        "meta": {
            "schemaVersion": SCHEMA_VERSION,
            "sourceLayer": "72_taxonomy_observed.json",
            "sourceFetchedAt": taxonomy.get("fetched_at"),
        },
        "counts": taxonomy.get("counts") or {},
        "activities": taxonomy.get("activities") or [],
        "types": taxonomy.get("types") or [],
        "agencies": taxonomy.get("agencies") or [],
        "branches": taxonomy.get("branches") or [],
        "winner_companies": taxonomy.get("winner_companies") or [],
    }
    assets["taxonomy_observed.json"] = write_asset(out, "taxonomy_observed.json", taxonomy_payload, role="taxonomy")

    meta = root / "meta"
    fetch_status = load_json(meta / "FETCH_STATUS.json", {})
    inventory = load_json(meta / "INVENTORY_AUDIT.json", {})
    assets["fetch_status.json"] = write_asset(out, "fetch_status.json", fetch_status, role="fetch_status")
    inventory_payload = {
        "summary": inventory.get("summary") or {},
        "inventory": inventory.get("inventory") or [],
        "audited_at": inventory.get("audited_at"),
    }
    assets["inventory.json"] = write_asset(out, "inventory.json", inventory_payload, role="inventory")
    return facets, fetch_status


def retain_catalogue(out: Path, assets: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    for filename in NON_TENDER_FILES:
        path = out / filename
        if path.exists():
            assets[filename] = describe_existing(path)
    facets = load_json(out / "facets_open.json", {})
    status = load_json(out / "fetch_status.json", {})
    return facets, status


def phase0_acquisition_status(
    status: dict[str, Any], source_times: dict[str, str | None]
) -> dict[str, Any]:
    """Keep the historical Plus fetch state separate from the current projection."""
    existing = status.get("phase0_acquisition")
    if isinstance(existing, dict):
        phase0 = deepcopy(existing)
    else:
        phase0 = {
            key: deepcopy(status[key])
            for key in PHASE0_STATUS_KEYS
            if key in status
        }
        if "canonical_projection" not in status and status.get("updated_at"):
            phase0["status_updated_at"] = status["updated_at"]
    source_fetched_at = max(
        filter(
            None,
            (
                source_times.get("phase0Awarded"),
                source_times.get("phase0Open"),
                source_times.get("phase0Baseline"),
            ),
        ),
        default=None,
    )
    if source_fetched_at:
        phase0["source_fetched_at"] = source_fetched_at
    phase0["current"] = False
    return phase0


def build_datasets(out: Path, awarded_partial: bool) -> list[dict[str, Any]]:
    definitions = [
        ("open", "open.json", "منافسات مفتوحة ضمن التغطية", "tenders", True),
        ("within_7", "within_7.json", "خلال 7 أيام ضمن التغطية", "tenders", True),
        ("within_30", "within_30.json", "خلال 30 يوماً ضمن التغطية", "tenders", True),
        ("awarding", "awarding.json", "في مرحلة الترسية ضمن التغطية", "tenders", True),
        ("examination", "examination.json", "تحت الفحص أو مغلقة ضمن التغطية", "tenders", True),
        ("cancelled", "cancelled.json", "منافسات ملغاة ضمن التغطية", "tenders", True),
        ("unknown", "unknown.json", "حالة دورة الحياة غير معروفة ضمن التغطية", "tenders", True),
        ("awarded", "awarded_index.json", "مرساة" + (" (جزئي)" if awarded_partial else ""), "tenders", awarded_partial),
        ("winnerfacet", "winnerfacet.json", "فائزون (winnerfacet)", "entities", True),
        ("ssr_tenders", "ssr_tenders.json", "عيّنة SSR للمنافسات", "tenders", False),
        ("tender_refs_sitemap", "tender_refs_sitemap.json", "مراجع خريطة الموقع", "sitemap", False),
        ("activities", "activities.json", "أنشطة (facets)", "taxonomy", False),
        ("types", "types.json", "أنواع المنافسات", "taxonomy", False),
        ("agencies", "agencies.json", "جهات (facets)", "taxonomy", False),
        ("activities_sitemap", "activities_sitemap.json", "أنشطة الخريطة", "sitemap", False),
        ("agencies_api", "agencies_api.json", "جهات API", "entities", False),
        ("agencies_ssr", "agencies_ssr.json", "جهات SSR", "entities", False),
        ("agencies_sitemap", "agencies_sitemap.json", "جهات الخريطة", "sitemap", False),
        ("companies_api", "companies_api.json", "شركات API", "entities", False),
        ("companies_ssr", "companies_ssr.json", "شركات SSR", "entities", False),
        ("companies_sitemap", "companies_sitemap.json", "شركات الخريطة", "sitemap", False),
        ("taxonomy_observed", "taxonomy_observed.json", "تصنيف مرصود SSR", "meta", False),
        ("fetch_status", "fetch_status.json", "حالة الجلب", "meta", False),
        ("inventory", "inventory.json", "تدقيق المخزون", "meta", False),
    ]
    datasets = []
    for dataset_id, filename, title, group, partial in definitions:
        if not (out / filename).exists():
            continue
        item: dict[str, Any] = {
            "id": dataset_id,
            "file": filename,
            "title": title,
            "group": group,
            "count": asset_count(out, filename),
        }
        if partial:
            item["partial"] = True
        if dataset_id == "awarded":
            item["indexParts"] = awarded_index_part_config()
            item["detailShards"] = {
                "count": SHARD_COUNT,
                "pathTemplate": "awarded_details/{shard}.json",
                "algorithm": "sha256_first_byte_mod_64",
            }
        datasets.append(item)
    return datasets


def build(args: argparse.Namespace) -> dict[str, Any]:
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    authority_path = out / ACTIVE_SCAN_AUTHORITY_FILE
    no_plus = bool(getattr(args, "no_plus", False))
    phase0_lock_path = getattr(args, "phase0_lock", None)
    if no_plus and phase0_lock_path is None:
        raise RuntimeError("--no-plus requires --phase0-lock for awarded completeness truth")
    phase0_lock = load_json(phase0_lock_path) if phase0_lock_path else None
    if phase0_lock is not None and not isinstance(phase0_lock, dict):
        raise RuntimeError("Phase-0 lock must contain a JSON object")
    plus_root = (
        None
        if no_plus
        else find_plus_root(getattr(args, "plus_warehouse", None))
    )

    plus_maps: dict[str, OrderedDict[str, dict[str, Any]]] = {
        "open": OrderedDict(),
        "within_7": OrderedDict(),
        "within_30": OrderedDict(),
        "awarded": OrderedDict(),
    }
    plus_layers: dict[str, Any] = {}
    if plus_root:
        plus_maps, plus_layers = load_plus_tenders(plus_root)

    baseline_maps: dict[str, OrderedDict[str, dict[str, Any]]] = {
        "open": OrderedDict(),
        "awarded": OrderedDict(),
    }
    official: OrderedDict[str, dict[str, Any]] = OrderedDict()
    source_times: dict[str, str | None] = {}
    database_metadata: dict[str, Any] = {}
    phase0_freshness_basis: str | None = None
    official_db = getattr(args, "official_db", None)
    if official_db:
        baseline_maps, official, db_times = load_official_database(official_db)
        database_metadata = db_times.get("meta") or {}
        phase0_freshness_basis = db_times.get("phase0_basis")
        source_times.update(
            {
                "phase0Baseline": db_times.get("phase0"),
                "phase0Open": db_times.get("phase0_open"),
                "phase0Awarded": db_times.get("phase0_awarded"),
                "officialPeriodic": db_times.get("official"),
            }
        )
    official_layers = getattr(args, "official_layers", None)
    if official_layers:
        layer_overlays, layer_times = load_official_layers(official_layers)
        for ref, record in layer_overlays.items():
            official[ref] = official_overlay(official.get(ref), record)
        source_times["officialPeriodic"] = max(
            filter(None, (source_times.get("officialPeriodic"), layer_times.get("official"))),
            default=None,
        )

    for state in ("open", "awarded"):
        for ref, record in baseline_maps[state].items():
            plus_maps[state].setdefault(ref, record)
    if not plus_maps["open"] and not plus_maps["awarded"]:
        raise RuntimeError(
            "no Phase-0 records available; provide --plus-warehouse or a DB with baseline_tenders.record_json"
        )

    if plus_layers:
        source_times["phase0Open"] = plus_layers["open"].get("fetched_at")
        source_times["phase0Awarded"] = plus_layers["awarded"].get("fetched_at")

    awarded_truth = resolve_awarded_truth(
        plus_layers=plus_layers,
        database_metadata=database_metadata,
        phase0_lock=phase0_lock,
    )
    awarded_partial = bool(awarded_truth["partial"])

    as_of_value = getattr(args, "as_of", None) or source_times.get("officialPeriodic")
    if not as_of_value:
        as_of_value = max(
            (value for value in source_times.values() if value),
            default=None,
        )
    as_of = parse_iso_datetime(as_of_value) if as_of_value else None
    if as_of is None:
        as_of = datetime.now(timezone.utc)
    as_of_iso = as_of.isoformat()

    lifecycle_candidates = merge_source_maps(plus_maps["open"], official, as_of=as_of)
    lifecycle_maps: dict[str, OrderedDict[str, dict[str, Any]]] = {
        "open": OrderedDict(),
        "awarding": OrderedDict(),
        "examination": OrderedDict(),
        "cancelled": OrderedDict(),
        "unknown": OrderedDict(),
    }
    for ref, record in lifecycle_candidates.items():
        category, _ = apply_lifecycle(record, as_of=as_of)
        if category in lifecycle_maps:
            lifecycle_maps[category][ref] = record
    open_map = lifecycle_maps["open"]

    awarded_official = OrderedDict(
        (ref, record)
        for ref, record in official.items()
        if award_is_announced(record, as_of=as_of)
    )
    awarded_map = merge_source_maps(
        plus_maps["awarded"],
        awarded_official,
        as_of=as_of,
    )
    for record in awarded_map.values():
        record["tenderCategory"] = "awarded"
        record["tenderCategoryBasis"] = "phase0_or_official_award_evidence"
        add_money_projection(record)

    within_7: OrderedDict[str, dict[str, Any]] = OrderedDict()
    within_30: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for ref, record in open_map.items():
        hours = record.get("deadlineWindowHours")
        if not isinstance(hours, (int, float)):
            continue
        if 0 <= hours <= 7 * 24:
            within_7[ref] = deepcopy(record)
        if 0 <= hours <= 30 * 24:
            within_30[ref] = deepcopy(record)

    all_records = (
        list(open_map.values())
        + list(lifecycle_maps["awarding"].values())
        + list(lifecycle_maps["examination"].values())
        + list(lifecycle_maps["cancelled"].values())
        + list(lifecycle_maps["unknown"].values())
        + list(awarded_map.values())
    )
    apply_name_cache(all_records, out)
    generated_at = utcnow()
    assets: dict[str, dict[str, Any]] = {}

    tender_payloads = {
        "open.json": pack(
            list(open_map.values()),
            dataset="open",
            partial=True,
            asOf=as_of_iso,
            coverageComplete=False,
        ),
        "within_7.json": pack(
            list(within_7.values()),
            dataset="within_7",
            partial=True,
            asOf=as_of_iso,
            derivedFrom="deadline",
            coverageComplete=False,
        ),
        "within_30.json": pack(
            list(within_30.values()),
            dataset="within_30",
            partial=True,
            asOf=as_of_iso,
            derivedFrom="deadline",
            coverageComplete=False,
        ),
        "awarding.json": pack(
            list(lifecycle_maps["awarding"].values()),
            dataset="awarding",
            partial=True,
            asOf=as_of_iso,
            coverageComplete=False,
        ),
        "examination.json": pack(
            list(lifecycle_maps["examination"].values()),
            dataset="examination",
            partial=True,
            asOf=as_of_iso,
            coverageComplete=False,
        ),
        "cancelled.json": pack(
            list(lifecycle_maps["cancelled"].values()),
            dataset="cancelled",
            partial=True,
            asOf=as_of_iso,
            coverageComplete=False,
        ),
        "unknown.json": pack(
            list(lifecycle_maps["unknown"].values()),
            dataset="unknown",
            partial=True,
            asOf=as_of_iso,
            explicitUnknown=True,
            coverageComplete=False,
        ),
    }
    for filename, payload in tender_payloads.items():
        assets[filename] = write_asset(
            out,
            filename,
            payload,
            count=payload["count"],
            role="tender_dataset",
        )

    index_records = [searchable_award(record) for record in awarded_map.values()]
    write_awarded_index(
        out,
        assets,
        index_records,
        partial=awarded_partial,
        completeness_basis=awarded_truth["validatedBy"],
    )

    shards: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for record in awarded_map.values():
        ref = str(record["ref"])
        detail = deepcopy(record)
        detail["_detailShard"] = f"{shard_for_ref(ref):02d}"
        shards[shard_for_ref(ref)].append(detail)
    shard_dir = out / "awarded_details"
    shard_dir.mkdir(parents=True, exist_ok=True)
    expected_shards = set()
    for shard in range(SHARD_COUNT):
        filename = f"awarded_details/{shard:02d}.json"
        expected_shards.add(f"{shard:02d}.json")
        rows = sorted(shards.get(shard, []), key=lambda row: str(row["ref"]))
        payload = pack(
            rows,
            dataset="awarded_detail",
            shard=f"{shard:02d}",
            shardCount=SHARD_COUNT,
        )
        assets[filename] = write_asset(
            out,
            filename,
            payload,
            count=len(rows),
            role="awarded_detail_shard",
        )
    for stale in shard_dir.glob("*.json"):
        if stale.name not in expected_shards:
            stale.unlink()

    if plus_root:
        facets, fetch_status = export_plus_catalogue(plus_root, out, assets)
    else:
        facets, fetch_status = retain_catalogue(out, assets)

    old_awarded = out / "awarded.json"
    if old_awarded.exists():
        old_awarded.unlink()

    obtained = dict(fetch_status.get("obtained") or {})
    obtained.pop("open_tenders_complete", None)
    obtained.pop("awarded_yes_complete", None)
    obtained.pop("awarded_yes_partial", None)
    obtained["open_tenders_current_snapshot"] = len(open_map)
    obtained["within_7"] = len(within_7)
    obtained["within_30"] = len(within_30)
    obtained["awarded_yes_partial" if awarded_partial else "awarded_yes_complete"] = len(
        awarded_map
    )

    lifecycle_counts = {
        "open": len(open_map),
        "within7": len(within_7),
        "within30": len(within_30),
        "awarding": len(lifecycle_maps["awarding"]),
        "examination": len(lifecycle_maps["examination"]),
        "cancelled": len(lifecycle_maps["cancelled"]),
        "unknown": len(lifecycle_maps["unknown"]),
        "awarded": len(awarded_map),
    }
    official_observed_records = sum(
        1
        for record in official.values()
        if (record.get("_provenance") or {}).get("primary")
        == "etimad_official_visitor"
    )
    completeness = {
        "officialUniverseComplete": False,
        "phase0Awarded": awarded_truth,
        "phase0FreshnessBasis": phase0_freshness_basis,
    }
    phase0_status = phase0_acquisition_status(fetch_status, source_times)
    active_scan_status = official_progress_metadata(database_metadata, "active_scan")
    canonical_status = {
        "schema_version": SCHEMA_VERSION,
        "source": "etimad_official_periodic",
        "phase": "CANONICAL_PERIODIC",
        "updated_at": as_of_iso,
        "mode": "official_periodic_raw_first_projection",
        "single_writer": True,
        "source_times": source_times,
        "official_periodic": {
            "warehouse_records": len(official),
            "official_observed_records": official_observed_records,
            "announced_award_records": len(awarded_official),
            "source_fetched_at": source_times.get("officialPeriodic"),
        },
        "phase0_acquisition": phase0_status,
        "obtained": obtained,
        "active_scan": active_scan_status,
        "region_backfill": official_progress_metadata(
            database_metadata,
            "region_backfill",
        ),
    }
    canonical_status["canonical_projection"] = {
        "schemaVersion": SCHEMA_VERSION,
        "asOf": as_of_iso,
        "sourceTimes": source_times,
        "completeness": completeness,
        "lifecycle": lifecycle_counts,
        "temporalWindows": {
            "basis": "parsed_deadline_relative_to_asOf",
            "unknownDeadlineExcluded": True,
        },
    }
    authority_payload = (
        load_active_scan_authority(Path(official_db), active_scan_status)
        if official_db
        else None
    )
    if authority_payload is not None:
        authority_descriptor = write_asset(
            out,
            ACTIVE_SCAN_AUTHORITY_FILE,
            authority_payload,
            role="active_scan_authority_evidence",
        )
        assets[ACTIVE_SCAN_AUTHORITY_FILE] = authority_descriptor
        attach_active_scan_authority_descriptor(
            active_scan_status,
            authority_payload,
            authority_descriptor,
        )
    elif authority_path.exists():
        authority_path.unlink()

    still_missing = dict(fetch_status.get("still_missing") or {})
    date_fallback = active_scan_status.get("date_fallback")
    active_scan_complete = active_refresh_sweep_complete(date_fallback)
    still_missing.update(
        {
            "official_universe_backfill": {
                "complete": False,
                "required": "status/date partitioned official backfill",
            },
            "active_refresh_sweep": {
                "complete": False,
                "required": "resumable official active-tender sweep",
            },
            "entity_alias_registry": {
                "complete": False,
                "required": "versioned agency/company aliases and merges",
            },
        }
    )
    if active_scan_complete:
        still_missing.pop("active_refresh_sweep", None)
    canonical_status["still_missing"] = still_missing
    fetch_status = canonical_status
    assets["fetch_status.json"] = write_asset(
        out,
        "fetch_status.json",
        fetch_status,
        role="fetch_status",
    )
    datasets = build_datasets(out, awarded_partial)

    snapshot_input = {
        "schemaVersion": SCHEMA_VERSION,
        "sourceTimes": source_times,
        "assets": {name: value["sha256"] for name, value in sorted(assets.items())},
    }
    snapshot_id = args.snapshot_id or hashlib.sha256(json_bytes(snapshot_input)).hexdigest()
    manifest = {
        "schema": "kashaf.static-warehouse",
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": snapshot_id,
        "generated_at": generated_at,
        "as_of": as_of_iso,
        "source_times": source_times,
        "source": "Kashaf canonical merge: official Etimad periodic + Phase-0 baseline",
        "note": "البيانات الرسمية الأحدث تتقدم في الحقول غير الفارغة، مع حفظ تاريخ وعروض وترسيات خط الأساس.",
        "provenance": {
            "precedence": ["etimad_official_periodic", "phase0_baseline", "etimad_plus_phase0"],
            "officialNonNullWins": True,
            "phase0AwardsPreservedUntilOfficialComplete": True,
            "rawEvidenceOwnedBy": "etimad-official-periodic",
        },
        "money": {
            "currency": "SAR",
            "unit": "halala",
            "conversion": "Decimal(str(value))",
            "rounding": "ROUND_HALF_UP_2DP",
        },
        "facets": {
            "grand": facets.get("grand"),
            "active": facets.get("active"),
            "soon": facets.get("soon"),
        },
        "completeness": completeness,
        "lifecycle": lifecycle_counts,
        "obtained": obtained,
        "still_missing": fetch_status.get("still_missing") or {},
        "datasets": datasets,
        "assets": dict(sorted(assets.items())),
        "files": {name: value["bytes"] for name, value in sorted(assets.items())},
    }
    manifest_path = out / "manifest.json"
    temp_manifest = manifest_path.with_suffix(".json.tmp")
    temp_manifest.write_bytes(json_bytes(manifest, pretty=True))
    temp_manifest.replace(manifest_path)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--plus-warehouse",
        type=Path,
        help="Phase-0 plus_warehouse root; defaults only to ETIMAD_WAREHOUSE when set",
    )
    parser.add_argument(
        "--no-plus",
        action="store_true",
        help="disable local Plus auto-detection; requires --phase0-lock",
    )
    parser.add_argument(
        "--phase0-lock",
        type=Path,
        help="trusted PHASE0_BASELINE.lock.json used to validate completeness truth",
    )
    parser.add_argument(
        "--official-db",
        type=Path,
        help="official_periodic.sqlite3; supports DB-only export via baseline_tenders.record_json",
    )
    parser.add_argument(
        "--official-layers",
        type=Path,
        help="official warehouse root containing layers/01_*.json and layers/02_*.json",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="viewer data directory")
    parser.add_argument(
        "--snapshot-id",
        help="explicit deployment identity, e.g. run_${GITHUB_RUN_ID}_${GITHUB_RUN_ATTEMPT}",
    )
    parser.add_argument(
        "--as-of",
        help="ISO snapshot time for lifecycle/deadline classification; defaults to source time",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = build(args)
    print(
        "exported snapshot",
        manifest["snapshot_id"][:12],
        "datasets",
        len(manifest["datasets"]),
        "assets",
        len(manifest["assets"]),
    )
    for name, descriptor in sorted(
        manifest["assets"].items(), key=lambda item: item[1]["bytes"], reverse=True
    )[:12]:
        print(f"  {name:42} {descriptor['bytes']:12,d}  {descriptor['sha256'][:12]}")


if __name__ == "__main__":
    main()
