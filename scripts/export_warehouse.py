"""Export every usable plus_warehouse layer into the viewer data/ folder."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

# Prefer Mac warehouse (current fetch worktree), then Windows laptop path, then env override.
_CANDIDATES = [
    Path(p)
    for p in (
        __import__("os").environ.get("ETIMAD_WAREHOUSE"),
        "/Users/baderalsalman/code/etimad-platform-wt/etimad-platform/data/plus_warehouse",
        "/Users/baderalsalman/code/ksa-coffee-atlas/etimad-platform/data/plus_warehouse",
        r"C:\Users\hp\ksa-coffee-atlas\etimad-platform\data\plus_warehouse",
    )
    if p
]
ROOT = next((p for p in _CANDIDATES if (p / "layers").is_dir()), _CANDIDATES[0])
LAYERS = ROOT / "layers"
META = ROOT / "meta"
OUT = Path(__file__).resolve().parents[1] / "data"


def load(name: str):
    return json.loads((LAYERS / name).read_text(encoding="utf-8"))


def dump(name: str, payload: dict) -> int:
    path = OUT / name
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return path.stat().st_size


def pack(records, **meta):
    return {
        "meta": {"exported_at": datetime.now(timezone.utc).isoformat(), **meta},
        "count": len(records),
        "records": records,
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    files = {}

    # --- tenders ---
    print(f"warehouse ROOT={ROOT}")
    for src, dst, partial in [
        ("81_tenders_open.json", "open.json", False),
        ("81_tenders_within_7.json", "within_7.json", False),
        ("81_tenders_within_30.json", "within_30.json", False),
        ("81_tenders_awarded_yes.json", "awarded.json", True),
    ]:
        d = load(src)
        files[dst] = dump(
            dst,
            pack(
                d.get("records") or [],
                source_layer=src,
                fetched_at=d.get("fetched_at"),
                partial=partial,
                fields="full",
            ),
        )

    # winnerfacet (may be partial guest dump)
    wf_path = LAYERS / "82_winnerfacet.json"
    if wf_path.exists():
        wf = load("82_winnerfacet.json")
        files["winnerfacet.json"] = dump(
            "winnerfacet.json",
            pack(
                wf.get("records") or [],
                source_layer="82_winnerfacet.json",
                fetched_at=wf.get("fetched_at"),
                partial=bool(wf.get("partial")),
            ),
        )

    ssr = load("62_ssr_tenders.json")
    files["ssr_tenders.json"] = dump(
        "ssr_tenders.json",
        pack(ssr.get("records") or [], source_layer="62_ssr_tenders.json", note="SSR sample"),
    )

    # --- facets taxonomy ---
    files["activities.json"] = dump(
        "activities.json",
        pack(load("83_activities_from_open_facets.json").get("records") or [], source="open facets"),
    )
    files["agencies.json"] = dump(
        "agencies.json",
        pack(load("86_agencies_from_open_facets.json").get("records") or [], source="open facets"),
    )
    files["types.json"] = dump(
        "types.json",
        pack(load("87_types_from_open_facets.json").get("records") or [], source="open facets"),
    )

    facets = load("80_facets_open.json")
    files["facets_open.json"] = dump(
        "facets_open.json",
        {
            "meta": {"source": "80_facets_open.json", "fetched_at": facets.get("fetched_at")},
            "grand": facets.get("grand"),
            "active": facets.get("active"),
            "soon": facets.get("soon"),
            "types": facets.get("types") or [],
            "activities_count": len(facets.get("activities") or []),
            "agencies_count": len(facets.get("agencies") or []),
        },
    )

    # --- API entities ---
    files["agencies_api.json"] = dump(
        "agencies_api.json",
        pack(load("60_api_agencies.json").get("records") or [], source="api/agencies.php"),
    )
    api_co = load("61_api_companies.json").get("records") or []
    files["companies_api.json"] = dump(
        "companies_api.json",
        pack(
            [
                {
                    "name": r.get("display") or r.get("key"),
                    "key": r.get("key"),
                    "wins": r.get("wins"),
                    "bids": r.get("bids"),
                    "total": r.get("total"),
                }
                for r in api_co
            ],
            source="api/companies.php",
        ),
    )

    # --- SSR lists ---
    ssr_co = load("63_ssr_companies.json").get("records") or []
    # normalize possible shapes
    norm_co = []
    for r in ssr_co:
        if isinstance(r, dict):
            norm_co.append(
                {
                    "name": r.get("name") or r.get("company") or r.get("display") or "",
                    "wins": r.get("wins") or r.get("awards") or r.get("col1"),
                    "bids": r.get("bids") or r.get("participations") or r.get("col2"),
                    "value": r.get("value") or r.get("total") or r.get("col3"),
                }
            )
    files["companies_ssr.json"] = dump(
        "companies_ssr.json", pack(norm_co, source="SSR companies page")
    )
    # keep legacy name for older UI paths
    files["companies.json"] = dump("companies.json", pack(norm_co, source="SSR companies page"))

    ssr_ag = load("64_ssr_agencies.json").get("records") or []
    files["agencies_ssr.json"] = dump(
        "agencies_ssr.json",
        pack(
            [
                {
                    "name": r.get("name") or "",
                    "count": r.get("count") or r.get("n") or r.get("tenders"),
                }
                for r in ssr_ag
                if isinstance(r, dict)
            ],
            source="SSR agencies page",
        ),
    )

    # --- sitemap catalogs ---
    files["companies_sitemap.json"] = dump(
        "companies_sitemap.json",
        pack(load("08_companies_from_sitemap.json").get("records") or [], source="sitemap"),
    )
    files["agencies_sitemap.json"] = dump(
        "agencies_sitemap.json",
        pack(load("09_agencies_from_sitemap.json").get("records") or [], source="sitemap"),
    )
    files["tender_refs_sitemap.json"] = dump(
        "tender_refs_sitemap.json",
        pack(load("10_tender_urls_from_sitemap.json").get("records") or [], source="sitemap"),
    )
    files["activities_sitemap.json"] = dump(
        "activities_sitemap.json",
        pack(load("07_activities_from_sitemap.json").get("records") or [], source="sitemap"),
    )

    tax = load("72_taxonomy_observed.json")
    files["taxonomy_observed.json"] = dump(
        "taxonomy_observed.json",
        {
            "meta": {"source": "72_taxonomy_observed.json", "fetched_at": tax.get("fetched_at")},
            "counts": tax.get("counts") or {},
            "activities": tax.get("activities") or [],
            "types": tax.get("types") or [],
            "agencies": tax.get("agencies") or [],
            "branches": tax.get("branches") or [],
            "winner_companies": tax.get("winner_companies") or [],
        },
    )

    status_path = META / "FETCH_STATUS.json"
    inv_path = META / "INVENTORY_AUDIT.json"
    fetch_status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
    inventory = json.loads(inv_path.read_text(encoding="utf-8")) if inv_path.exists() else {}
    files["fetch_status.json"] = dump("fetch_status.json", fetch_status)
    files["inventory.json"] = dump(
        "inventory.json",
        {
            "summary": inventory.get("summary") or {},
            "inventory": inventory.get("inventory") or [],
            "audited_at": inventory.get("audited_at"),
        },
    )

    datasets = [
        {"id": "open", "file": "open.json", "title": "منافسات مفتوحة", "group": "tenders", "count": 1604},
        {"id": "within_7", "file": "within_7.json", "title": "خلال 7 أيام", "group": "tenders", "count": 782},
        {"id": "within_30", "file": "within_30.json", "title": "خلال 30 يوماً", "group": "tenders", "count": 1580},
        {
            "id": "awarded",
            "file": "awarded.json",
            "title": "مرساة (جزئي)",
            "group": "tenders",
            "count": 19200,
            "partial": True,
        },
        {
            "id": "winnerfacet",
            "file": "winnerfacet.json",
            "title": "فائزون (winnerfacet)",
            "group": "entities",
            "count": 300,
            "partial": True,
        },
        {"id": "ssr_tenders", "file": "ssr_tenders.json", "title": "عيّنة SSR للمنافسات", "group": "tenders", "count": 50},
        {"id": "tender_refs_sitemap", "file": "tender_refs_sitemap.json", "title": "مراجع خريطة الموقع", "group": "sitemap", "count": 13665},
        {"id": "activities", "file": "activities.json", "title": "أنشطة (facets)", "group": "taxonomy", "count": 138},
        {"id": "types", "file": "types.json", "title": "أنواع المنافسات", "group": "taxonomy", "count": 9},
        {"id": "agencies", "file": "agencies.json", "title": "جهات (facets)", "group": "taxonomy", "count": 802},
        {"id": "activities_sitemap", "file": "activities_sitemap.json", "title": "أنشطة الخريطة", "group": "sitemap", "count": 82},
        {"id": "agencies_api", "file": "agencies_api.json", "title": "جهات API", "group": "entities", "count": 600},
        {"id": "agencies_ssr", "file": "agencies_ssr.json", "title": "جهات SSR", "group": "entities", "count": 600},
        {"id": "agencies_sitemap", "file": "agencies_sitemap.json", "title": "جهات الخريطة", "group": "sitemap", "count": 696},
        {"id": "companies_api", "file": "companies_api.json", "title": "شركات API", "group": "entities", "count": 60},
        {"id": "companies_ssr", "file": "companies_ssr.json", "title": "شركات SSR", "group": "entities", "count": len(norm_co)},
        {"id": "companies_sitemap", "file": "companies_sitemap.json", "title": "شركات الخريطة", "group": "sitemap", "count": 20968},
        {"id": "taxonomy_observed", "file": "taxonomy_observed.json", "title": "تصنيف مرصود SSR", "group": "meta", "count": None},
        {"id": "fetch_status", "file": "fetch_status.json", "title": "حالة الجلب", "group": "meta", "count": None},
        {"id": "inventory", "file": "inventory.json", "title": "تدقيق المخزون", "group": "meta", "count": None},
    ]

    # refresh counts from actual files
    for ds in datasets:
        p = OUT / ds["file"]
        if not p.exists():
            continue
        raw = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and "count" in raw and isinstance(raw["count"], int):
            ds["count"] = raw["count"]
        elif isinstance(raw, dict) and "records" in raw:
            ds["count"] = len(raw["records"])

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "etimadplus.com warehouse — full usable mirror",
        "note": "كل الطبقات القابلة للعرض من المستودع مدمجة هنا. الناقص فقط ما لم يُجلب بعد (بقية المرساة / winnerfacet / all).",
        "facets": {
            "grand": facets.get("grand"),
            "active": facets.get("active"),
            "soon": facets.get("soon"),
        },
        "obtained": fetch_status.get("obtained") or {},
        "still_missing": fetch_status.get("still_missing") or {},
        "datasets": datasets,
        "files": files,
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print("exported", len(files), "files; total_bytes", sum(files.values()))
    for k, v in sorted(files.items(), key=lambda kv: -kv[1])[:15]:
        print(f"  {k:35} {v:10}")


if __name__ == "__main__":
    main()
