"""Build the Kashaf static data contract from the Phase-0 and official warehouses.

The awarded catalogue is deliberately split into a compact searchable index and
64 deterministic detail shards.  No generated asset requires Git LFS.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from collections import OrderedDict, defaultdict
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 2
SHARD_COUNT = 64
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


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    existing = {(s.get("id"), s.get("fetchedAt"), s.get("layer")) for s in sources}
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
    for source in official_provenance.get("sources", []):
        marker = (source.get("id"), source.get("fetchedAt"), source.get("layer"))
        known = {
            (item.get("id"), item.get("fetchedAt"), item.get("layer"))
            for item in result["_provenance"]["sources"]
        }
        if marker not in known:
            result["_provenance"]["sources"].append(deepcopy(source))

    official_award_complete = official.get("awardState") == "announced" or official.get(
        "awardCompleteness"
    ) in (True, "complete", "announced", "all_groups_announced")
    for key, value in official.items():
        if key.startswith("_") or not meaningful(value):
            continue
        if key in AWARD_FIELDS and meaningful(result.get(key)) and not official_award_complete:
            continue
        result[key] = deepcopy(value)
        result["_provenance"]["fieldSources"][key] = source_id

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


def database_times(connection: sqlite3.Connection) -> dict[str, str | None]:
    result: dict[str, str | None] = {"official": None, "phase0": None}
    try:
        result["official"] = connection.execute("SELECT MAX(last_seen_at) FROM tenders").fetchone()[0]
    except sqlite3.Error:
        pass
    try:
        result["phase0"] = connection.execute(
            "SELECT MAX(imported_at) FROM baseline_tenders"
        ).fetchone()[0]
    except sqlite3.Error:
        pass
    return result


def load_official_database(
    path: Path,
) -> tuple[
    dict[str, OrderedDict[str, dict[str, Any]]],
    OrderedDict[str, dict[str, Any]],
    dict[str, str | None],
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
        baseline_count = connection.execute("SELECT COUNT(*) FROM baseline_tenders").fetchone()[0]
        if "record_json" in baseline_columns:
            rows = connection.execute(
                "SELECT reference_number, seed_state, source_layer, imported_at, record_json "
                "FROM baseline_tenders ORDER BY rowid"
            )
            for row in rows:
                payload = parse_json_cell(row["record_json"])
                if not payload:
                    continue
                state = "awarded" if row["seed_state"] == "awarded" else "open"
                record = seed_record(
                    payload,
                    source_id="phase0_baseline",
                    fetched_at=row["imported_at"],
                    layer=row["source_layer"],
                )
                record["ref"] = str(row["reference_number"])
                baseline[state][record["ref"]] = record
        loaded_baseline = sum(len(values) for values in baseline.values())
        if baseline_count and loaded_baseline not in (0, baseline_count):
            raise RuntimeError(
                f"baseline record_json incomplete: loaded {loaded_baseline}/{baseline_count}"
            )

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
                seed = parse_json_cell(raw.get("seed_json")) or {}
                payload = parse_json_cell(raw.get("official_json")) or {}
                record = seed_record(
                    seed,
                    source_id="etimad_official_periodic",
                    fetched_at=raw.get("last_seen_at"),
                    layer="tenders.seed_json",
                )
                curated = {
                    "ref": ref,
                    "officialTenderId": raw.get("official_tender_id"),
                    "officialTenderIdString": raw.get("tender_id_string"),
                    "name": raw.get("tender_name"),
                    "num": raw.get("tender_number"),
                    "agency": raw.get("agency_name"),
                    "branch": raw.get("branch_name"),
                    "type": raw.get("tender_type_name"),
                    "status": raw.get("tender_status_name"),
                    "activity": raw.get("activity_name"),
                    "submit": raw.get("submitted_at"),
                    "deadline": raw.get("deadline"),
                    "url": raw.get("official_url"),
                    "region": raw.get("region"),
                    "expectedAwardAt": raw.get("expected_award_at"),
                    "awardMode": raw.get("award_mode"),
                    "awardState": raw.get("award_state"),
                    "firstSeen": raw.get("first_seen_at"),
                    "lastSeen": raw.get("last_seen_at"),
                    "lastAwardCheckedAt": raw.get("last_award_checked_at"),
                    "nextAwardCheckAt": raw.get("next_award_check_at"),
                    "sourceKind": raw.get("source_kind"),
                    "baselineLinked": bool(raw.get("baseline_linked")),
                }
                curated.update(official_payload_fields(payload))
                curated.update(award_fields(parse_json_cell(raw.get("award_json"))))
                if groups.get(ref):
                    curated["groups"] = groups[ref]
                    curated.setdefault(
                        "awardCompleteness",
                        all(bool(group.get("complete")) for group in groups[ref]),
                    )
                official_record = seed_record(
                    curated,
                    source_id="etimad_official_periodic",
                    fetched_at=raw.get("last_seen_at"),
                    layer="official_periodic.sqlite3:tenders",
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


def build_datasets(out: Path, awarded_partial: bool) -> list[dict[str, Any]]:
    definitions = [
        ("open", "open.json", "منافسات مفتوحة", "tenders", False),
        ("within_7", "within_7.json", "خلال 7 أيام", "tenders", False),
        ("within_30", "within_30.json", "خلال 30 يوماً", "tenders", False),
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
    plus_root = None if args.no_plus else find_plus_root(args.plus_warehouse)

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
    if args.official_db:
        baseline_maps, official, db_times = load_official_database(args.official_db)
        source_times.update(
            {
                "phase0Baseline": db_times.get("phase0"),
                "officialPeriodic": db_times.get("official"),
            }
        )
    if args.official_layers:
        layer_overlays, layer_times = load_official_layers(args.official_layers)
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

    open_map = merge_source_maps(plus_maps["open"], official)
    for ref in list(open_map):
        if open_map[ref].get("awardState") == "announced":
            del open_map[ref]

    awarded_official = OrderedDict(
        (ref, record)
        for ref, record in official.items()
        if record.get("awardState") == "announced"
        or meaningful(record.get("winners"))
        or meaningful(record.get("winAmount"))
    )
    awarded_map = merge_source_maps(plus_maps["awarded"], awarded_official)
    for record in awarded_map.values():
        add_money_projection(record)

    if plus_maps["within_7"]:
        within_7 = overlay_existing(plus_maps["within_7"], official)
    else:
        within_7 = OrderedDict()
    if plus_maps["within_30"]:
        within_30 = overlay_existing(plus_maps["within_30"], official)
    else:
        within_30 = OrderedDict()
    for ref, record in open_map.items():
        days = record.get("days")
        if isinstance(days, (int, float)) and 0 <= days <= 7:
            within_7.setdefault(ref, deepcopy(record))
        if isinstance(days, (int, float)) and 0 <= days <= 30:
            within_30.setdefault(ref, deepcopy(record))

    all_records = list(open_map.values()) + list(awarded_map.values())
    apply_name_cache(all_records, out)
    generated_at = utcnow()
    assets: dict[str, dict[str, Any]] = {}

    tender_payloads = {
        "open.json": pack(
            list(open_map.values()),
            dataset="open",
            partial=False,
        ),
        "within_7.json": pack(
            list(within_7.values()),
            dataset="within_7",
            partial=False,
        ),
        "within_30.json": pack(
            list(within_30.values()),
            dataset="within_30",
            partial=False,
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

    awarded_partial = False
    if plus_layers:
        awarded_layer = plus_layers["awarded"]
        awarded_partial = bool(awarded_layer.get("hasMore")) or not bool(
            awarded_layer.get("complete")
        )
    index_records = [searchable_award(record) for record in awarded_map.values()]
    index_payload = pack(
        index_records,
        dataset="awarded",
        partial=awarded_partial,
        detailShards=SHARD_COUNT,
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

    datasets = build_datasets(out, awarded_partial)
    obtained = dict(fetch_status.get("obtained") or {})
    obtained["open_tenders_complete"] = len(open_map)
    obtained["awarded_yes_partial" if awarded_partial else "awarded_yes_complete"] = len(
        awarded_map
    )

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
        help="disable local Plus auto-detection and prove the DB-only path",
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
