"""Fail closed when a Kashaf static snapshot is incomplete or unsafe to publish."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import time
from pathlib import Path
from urllib.parse import quote
from urllib.error import URLError
from urllib.request import Request, urlopen

from export_warehouse import SCHEMA_VERSION, SHARD_COUNT, shard_for_ref, to_halalas


LFS_HEADER = b"version https://git-lfs.github.com/spec/v1"


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
    assert index and isinstance(index.get("records"), list), "awarded index missing"
    for volatile in ("generatedAt", "sourceTimes", "exportedAt", "exported_at"):
        assert volatile not in (index.get("meta") or {}), f"volatile awarded index meta: {volatile}"
    assert (data / "awarded_index.json").stat().st_size < 50 * 1024 * 1024, "awarded index exceeds 50 MiB"
    index_by_ref = {str(row["ref"]): row for row in index["records"]}
    assert len(index_by_ref) == len(index["records"]), "duplicate refs in awarded index"

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
        refs = {}
        for row in parsed.get("records") or []:
            ref = str(row["ref"])
            if ref in refs:
                raise AssertionError(f"duplicate remote awarded index ref: {ref}")
            expected_shard = f"{shard_for_ref(ref):02d}"
            if row.get("_detailShard") != expected_shard:
                raise AssertionError(f"remote awarded index shard mismatch: {ref}")
            refs[ref] = expected_shard
        result["index_refs"] = refs
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
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(pending)))) as executor:
        futures = {
            executor.submit(
                fetch_remote_asset,
                base_url,
                name,
                expected,
                cache_key=cache_key,
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

    index_refs = (results.get("awarded_index.json") or {}).get("index_refs")
    if index_refs is None:
        raise AssertionError("remote awarded index missing")
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
