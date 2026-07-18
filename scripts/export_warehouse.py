"""Build the Kashaf static data contract from the Phase-0 and official warehouses.

The awarded catalogue is deliberately split into a compact searchable index and
64 deterministic detail shards.  No generated asset requires Git LFS.
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


SCHEMA_VERSION = 2
SHARD_COUNT = 64
SAUDI_TIMEZONE = timezone(timedelta(hours=3))
HERE = Path(__file__).resolve().parents[1]
DEFAULT_OUT = HERE / "data"
PLUS_CANDIDATES = tuple(
    Path(value)
    for value in (
        os.environ.get("ETIMAD_WAREHOUSE"),
        "/Users/baderalsalman/code/etimad-platform-wt/etimad-platform/data/plus_warehouse",
        "/Users/baderalsalman/code/ksa-coffee-atlas/etimad-platform/data/plus_warehouse",
        r"C:\Users\hp\ksa-coffee-atlas\etimad-platform\data\plus_warehouse",
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


def award_is_announced(record: dict[str, Any]) -> bool:
    status = normalized_status(record.get("status"))
    return bool(
        record.get("awardState") == "announced"
        or record.get("awardCompleteness")
        in (True, "complete", "announced", "all_groups_announced")
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
    if award_is_announced(record):
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

    winner_awards = [
        offer.get("awardHalalas")
        for offer in record.get("winners") or []
        if isinstance(offer, dict) and offer.get("awardHalalas") is not None
    ]
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

    official_award_complete = official.get("awardState") == "announced" or official.get(
        "awardCompleteness"
    ) in (True, "complete", "announced", "all_groups_announced")
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
) -> OrderedDict[str, dict[str, Any]]:
    merged = OrderedDict((ref, deepcopy(record)) for ref, record in primary.items())
    for ref, official in overlays.items():
        merged[ref] = official_overlay(merged.get(ref), official)
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
    records: Iterable[dict[str, Any]],
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


def load_plus_tenders(root: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
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

    def component_success_at(row: dict[str, Any]) -> Any:
        if not row:
            return None
        if "success_checked_at" in row:
            return row.get("success_checked_at")
        return row.get("checked_at") if not row.get("error") else None

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
        "region": raw.get("region") or seed.get("region"),
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


def searchable_award(record: dict[str, Any]) -> dict[str, Any]:
    result = {key: record.get(key) for key in INDEX_FIELDS if meaningful(record.get(key))}
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

    lifecycle_candidates = merge_source_maps(plus_maps["open"], official)
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
        if award_is_announced(record)
    )
    awarded_map = merge_source_maps(plus_maps["awarded"], awarded_official)
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
    index_payload = pack(
        index_records,
        dataset="awarded",
        partial=awarded_partial,
        detailShards=SHARD_COUNT,
        completenessBasis=awarded_truth["validatedBy"],
    )
    assets["awarded_index.json"] = write_asset(
        out,
        "awarded_index.json",
        index_payload,
        count=len(index_records),
        role="awarded_search_index",
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
    still_missing = dict(fetch_status.get("still_missing") or {})
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
        help="Phase-0 plus_warehouse root; auto-detected locally when omitted",
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
