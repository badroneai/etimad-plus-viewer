"""Fail closed when a Kashaf static snapshot is incomplete or unsafe to publish."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
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


def check_remote(
    base_url: str,
    expected_snapshot_id: str | None,
    wait_seconds: int = 0,
) -> dict[str, int]:
    base_manifest_url = base_url.rstrip("/") + "/data/manifest.json"
    deadline = time.monotonic() + max(0, wait_seconds)
    last_error: Exception | None = None
    manifest = None
    while True:
        url = f"{base_manifest_url}?snapshot-check={time.time_ns()}"
        try:
            request = Request(
                url,
                headers={"Cache-Control": "no-cache", "User-Agent": "kashaf-contract-check/2"},
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
            manifest = candidate
            break
        except (AssertionError, json.JSONDecodeError, URLError, TimeoutError) as error:
            last_error = error
            if time.monotonic() >= deadline:
                raise AssertionError(f"remote snapshot did not converge: {last_error}") from error
            time.sleep(min(5, max(0.1, deadline - time.monotonic())))
    assert manifest is not None
    return {
        "assets": len(manifest.get("assets") or {}),
        "awarded": next(
            (item.get("count", 0) for item in manifest.get("datasets") or [] if item.get("id") == "awarded"),
            0,
        ),
        "shards": SHARD_COUNT,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--base-url", help="deployed Kashaf base URL; verifies the remote manifest identity")
    parser.add_argument("--expect-snapshot-id", help="required local or remote snapshot identity")
    parser.add_argument(
        "--wait-seconds",
        type=int,
        default=0,
        help="poll a remote manifest until the expected snapshot appears",
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
