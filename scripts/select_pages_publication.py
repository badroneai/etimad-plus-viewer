from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.error import URLError


@dataclass(frozen=True)
class ManifestIdentity:
    snapshot_id: str
    sha256: str


@dataclass(frozen=True)
class PublicationSelection:
    should_deploy: bool
    reason: str
    publication: ManifestIdentity
    live: ManifestIdentity | None


def manifest_identity(payload: bytes, *, label: str) -> ManifestIdentity:
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} manifest is not valid UTF-8 JSON") from error
    if not isinstance(document, dict):
        raise ValueError(f"{label} manifest must be a JSON object")
    snapshot_id = document.get("snapshot_id")
    if not isinstance(snapshot_id, str) or not snapshot_id.strip():
        raise ValueError(f"{label} manifest has no non-empty snapshot_id")
    return ManifestIdentity(
        snapshot_id=snapshot_id,
        sha256=hashlib.sha256(payload).hexdigest(),
    )


def compare_manifests(
    publication_payload: bytes,
    live_payload: bytes | None,
) -> PublicationSelection:
    publication = manifest_identity(publication_payload, label="publication")
    if live_payload is None:
        return PublicationSelection(
            should_deploy=True,
            reason="live_manifest_unavailable",
            publication=publication,
            live=None,
        )

    try:
        live = manifest_identity(live_payload, label="live")
    except ValueError:
        return PublicationSelection(
            should_deploy=True,
            reason="live_manifest_invalid",
            publication=publication,
            live=None,
        )

    if live.snapshot_id != publication.snapshot_id:
        return PublicationSelection(
            should_deploy=True,
            reason="snapshot_id_diverged",
            publication=publication,
            live=live,
        )
    if live.sha256 != publication.sha256:
        return PublicationSelection(
            should_deploy=True,
            reason="manifest_sha256_diverged",
            publication=publication,
            live=live,
        )
    return PublicationSelection(
        should_deploy=False,
        reason="publication_already_live",
        publication=publication,
        live=live,
    )


def fetch(url: str, *, timeout_seconds: float) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Cache-Control": "no-cache",
            "User-Agent": "kashaf-publication-selector/1",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return response.read()


def write_github_output(path: Path, selection: PublicationSelection) -> None:
    live_snapshot = selection.live.snapshot_id if selection.live else ""
    live_sha256 = selection.live.sha256 if selection.live else ""
    values = {
        "should_deploy": str(selection.should_deploy).lower(),
        "reason": selection.reason,
        "publication_snapshot_id": selection.publication.snapshot_id,
        "publication_manifest_sha256": selection.publication.sha256,
        "live_snapshot_id": live_snapshot,
        "live_manifest_sha256": live_sha256,
    }
    with path.open("a", encoding="utf-8") as output:
        for key, value in values.items():
            output.write(f"{key}={value}\n")


def write_summary(path: Path, selection: PublicationSelection) -> None:
    live_snapshot = selection.live.snapshot_id if selection.live else "unavailable"
    with path.open("a", encoding="utf-8") as summary:
        summary.write("## Kashaf publication selector\n\n")
        summary.write(f"- Decision: `{selection.reason}`\n")
        summary.write(f"- Deploy: `{str(selection.should_deploy).lower()}`\n")
        summary.write(
            f"- Publication snapshot: `{selection.publication.snapshot_id}`\n"
        )
        summary.write(f"- Live snapshot: `{live_snapshot}`\n")
        summary.write(
            f"- Publication manifest SHA-256: `{selection.publication.sha256}`\n"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Select whether the latest Kashaf publication needs Pages deployment."
    )
    parser.add_argument("--publication-url", required=True)
    parser.add_argument("--live-url", required=True)
    parser.add_argument("--github-output", type=Path, required=True)
    parser.add_argument("--summary-file", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    args = parser.parse_args()

    publication_payload = fetch(
        args.publication_url,
        timeout_seconds=args.timeout_seconds,
    )
    try:
        live_payload = fetch(args.live_url, timeout_seconds=args.timeout_seconds)
    except (OSError, URLError):
        live_payload = None

    selection = compare_manifests(publication_payload, live_payload)
    write_github_output(args.github_output, selection)
    write_summary(args.summary_file, selection)


if __name__ == "__main__":
    main()
