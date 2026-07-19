from __future__ import annotations

import hashlib
import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlencode


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from check_data_contract import (  # noqa: E402
    assert_active_cardinality_scan_contract,
    assert_active_date_scan_contract,
    assert_active_missing_truth,
)
from export_warehouse import (  # noqa: E402
    _resolve_official_raw_file,
    attach_active_scan_authority_descriptor,
    selected_cardinality_authority,
)


def sha_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def ref_sha(references: list[str]) -> str:
    payload = "".join(f"{reference}\n" for reference in sorted(set(references)))
    return hashlib.sha256(payload.encode()).hexdigest()


def list_url(page: int = 1, **filters: str) -> str:
    params = {
        "TenderCategory": "2",
        "PublishDateId": "1",
        "SortDirection": "DESC",
        "Sort": "SubmitionDate",
        "IsSearch": "true",
        "PageSize": "24",
        "PageNumber": str(page),
        **filters,
    }
    return (
        "https://tenders.etimad.sa/Tender/AllSupplierTendersForVisitorAsync?"
        + urlencode(params)
    )


def cardinality_fixture() -> tuple[dict, dict]:
    reference = "100"
    tender_id = "T-100"
    union_sha = ref_sha([reference])
    bijection_sha = sha_text(f"{reference}\t{tender_id}\n")
    taxonomy_values = {
        "agency": [{"value": "AG", "label": "Agency"}],
        "activity": [{"value": "AC", "label": "Activity"}],
        "area": [{"value": "AR", "label": "Area"}],
        "booklet": [
            {"value": str(value), "label": str(value)} for value in range(7)
        ],
        "type": [{"value": "TY", "label": "Type"}],
    }
    taxonomy_sha = hashlib.sha256(
        json.dumps(
            taxonomy_values,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    taxonomy_endpoints = {
        "type": "/Qualification/GetTenderTypes",
        "area": "/Tender/GetAreasAsync",
        "activity": "/Tender/GetMainActivitiesAsync",
        "agency": "/Tender/GetAllAgenciesAsync",
    }
    taxonomy_captures = []
    raw: dict[str, dict] = {}

    def register(path: str) -> None:
        raw[path] = {"raw_path": path, "sha256": sha_text(path), "bytes": 1}

    for kind, endpoint in taxonomy_endpoints.items():
        path = f"raw/taxonomy-{kind}.bin"
        register(path)
        taxonomy_captures.append(
            {
                "kind": kind,
                "endpoint": endpoint,
                "values": taxonomy_values[kind],
                "raw_path": path,
                "sha256": sha_text(path),
                "url": f"https://tenders.etimad.sa{endpoint}",
                "status": 200,
                "content_type": "application/json",
                "bytes": 1,
                "captured_at": "2026-07-19T01:00:00+00:00",
            }
        )

    def boundary(path: str) -> dict:
        register(path)
        return {
            "raw_path": path,
            "sha256": sha_text(path),
            "status": 200,
            "url": list_url(),
            "content_type": "application/json",
            "bytes": 1,
            "total_count": 1,
            "records": 1,
            "references": [reference],
            "reference_sha256": union_sha,
        }

    def page(path: str) -> dict:
        register(path)
        return {
            "node_id": "root",
            "page_number": 1,
            "total_count": 1,
            "records": 1,
            "references": [reference],
            "mappings": [
                {"reference_number": reference, "tender_id": tender_id}
            ],
            "raw_path": path,
            "sha256": sha_text(path),
            "url": list_url(),
        }

    node = {
        "node_id": "root",
        "parent_node_id": None,
        "depth": 0,
        "lens_name": "root",
        "filters": {},
        "state": "exact",
        "total_count": 1,
        "page_count": 1,
        "supersession": {
            "reason": None,
            "union_sha256": None,
            "generation": None,
            "boundary_total_count": None,
        },
    }

    def superseded(generation: int) -> dict:
        return {
            "cycle_id": "cycle-seal",
            "generation": generation,
            "reference_number": "999",
            "source": "active_scan_target",
            "state": "superseded_by_cardinality",
            "status_id": None,
            "tender_id": None,
            "raw_path": None,
            "sha256": None,
            "url": None,
            "attempts": 0,
            "error": "union_reached_boundary_cardinality",
            "checked_at": None,
            "run_id": None,
        }

    def proof(generation: int, ordinal: int) -> dict:
        opening = boundary(f"raw/proof-{generation}-opening.bin")
        closing = boundary(f"raw/proof-{generation}-closing.bin")
        proof_page = page(f"raw/proof-{generation}-page.bin")
        proof_node = deepcopy(node)
        return {
            "generation": generation,
            "convergence_ordinal": ordinal,
            "chain_number": 1,
            "superseded_at": None,
            "superseded_reason": None,
            "boundary_total_count": 1,
            "boundary_head_ref_sha256": union_sha,
            "union_unique": 1,
            "union_sha256": union_sha,
            "bijection_sha256": bijection_sha,
            "references": [reference],
            "mappings": [
                {"reference_number": reference, "tender_id": tender_id}
            ],
            "boundary_evidence": {"opening": opening, "closing": closing},
            "taxonomy_sha256": taxonomy_sha,
            "node_evidence": {"nodes": [proof_node], "pages": [proof_page]},
            "candidate_evidence": {
                "checks": [],
                "superseded_by_cardinality_count": 1,
                "superseded_reference_sha256": ref_sha(["999"]),
                "superseded": [superseded(generation)],
            },
            "run_id": f"run-{generation}",
            "closed_at": f"2026-07-19T0{generation + 1}:00:00+00:00",
        }

    proofs = [proof(1, 1), proof(2, 2)]
    current_page = proofs[1]["node_evidence"]["pages"][0]
    current_node = deepcopy(proofs[1]["node_evidence"]["nodes"][0])
    current_opening = proofs[1]["boundary_evidence"]["opening"]
    current_closing = proofs[1]["boundary_evidence"]["closing"]
    progress = {
        "schema_version": 4,
        "strategy": "cardinality_seal_v1",
        "mode": "official_active_cardinality_seal",
        "cycle_id": "cycle-seal",
        "generation": 2,
        "phase": "authoritative",
        "targets": {"total": 1, "observed": 1, "absent": 0, "resolved": 1},
        "boundary": {
            "total_count": 1,
            "head_ref_sha256": union_sha,
            "opening_evidence": current_opening,
            "closing_evidence": current_closing,
            "stable": True,
        },
        "membership": {
            "observed_unique": 1,
            "unexplained_unique": 0,
            "pending_candidates": 0,
            "union_sha256": union_sha,
            "bijection_sha256": bijection_sha,
            "duplicate_references": 0,
            "duplicate_tender_ids": 0,
            "integrity_error_count": 0,
            "integrity_errors": [],
            "complete": True,
        },
        "frontier": {
            "nodes_total": 1,
            "pending": 0,
            "split": 0,
            "exact": 1,
            "blocked": 0,
            "superseded_by_cardinality": 0,
            "clear_for_authority": True,
            "accepted_pages": 1,
            "page_ceiling_switches": 0,
            "by_lens": {},
        },
        "taxonomy": {
            "complete": True,
            "sha256": taxonomy_sha,
            "kinds": {
                kind: len(values) for kind, values in taxonomy_values.items()
            },
            "evidence": True,
        },
        "generation_proofs": {
            "required": 2,
            "recorded": 2,
            "recorded_total": 2,
            "superseded": 0,
            "chain_number": 1,
            "matching_current_union": 2,
            "distinct_generations": 2,
            "generations": [1, 2],
            "ordinals": [1, 2],
            "authoritative": True,
        },
        "union_authoritative": True,
        "partition_authoritative": True,
        "absence_authoritative": True,
        "completion_authoritative": True,
        "complete": True,
    }
    files = [raw[path] for path in sorted(raw)]
    evidence = {
        "schema_version": 4,
        "strategy": "cardinality_seal_v1",
        "mode": "official_active_cardinality_seal",
        "cycle_id": "cycle-seal",
        "generation": 2,
        "phase": "authoritative",
        "union_authoritative": True,
        "partition_authoritative": True,
        "absence_authoritative": True,
        "completion_authoritative": True,
        "complete": True,
        "raw_verification": {
            "mode": "export_time_official_warehouse_bytes",
            "verified_files": len(files),
            "verified_bytes": len(files),
            "files": files,
        },
        "boundary": {"opening": current_opening, "closing": current_closing},
        "taxonomy": {
            "sha256": taxonomy_sha,
            "values": taxonomy_values,
            "captures": taxonomy_captures,
        },
        "frontier": {"nodes": [current_node], "pages": [current_page]},
        "candidates": [superseded(2)],
        "membership": {
            "references": [reference],
            "mappings": [
                {"reference_number": reference, "tender_id": tender_id}
            ],
            "union_sha256": union_sha,
            "bijection_sha256": bijection_sha,
        },
        "generation_proofs": proofs,
        "generation_proof_ledger": [
            {
                "generation": proof_row["generation"],
                "convergence_ordinal": proof_row["convergence_ordinal"],
                "chain_number": proof_row["chain_number"],
                "superseded_at": proof_row["superseded_at"],
                "superseded_reason": proof_row["superseded_reason"],
            }
            for proof_row in proofs
        ],
    }
    return progress, evidence


class CardinalitySealContractTests(unittest.TestCase):
    def test_two_independent_generations_seal_cardinality(self) -> None:
        progress, evidence = cardinality_fixture()
        assert_active_cardinality_scan_contract(progress, evidence)

    def test_forged_raw_pointer_fails_closed(self) -> None:
        progress, evidence = cardinality_fixture()
        forged = deepcopy(evidence)
        forged["frontier"]["pages"][0]["sha256"] = "f" * 64
        with self.assertRaisesRegex(AssertionError, "RAW verification hash mismatch"):
            assert_active_cardinality_scan_contract(progress, forged)

    def test_bound_superseded_nodes_preserve_pages_and_union(self) -> None:
        progress, evidence = cardinality_fixture()
        progress["frontier"].update(
            {"exact": 0, "superseded_by_cardinality": 1}
        )
        current_node = evidence["frontier"]["nodes"][0]
        current_node["state"] = "superseded_by_cardinality"
        current_node["supersession"] = {
            "reason": "union_reached_boundary_cardinality",
            "union_sha256": evidence["membership"]["union_sha256"],
            "generation": 2,
            "boundary_total_count": 1,
        }
        for proof in evidence["generation_proofs"]:
            proof_node = proof["node_evidence"]["nodes"][0]
            proof_node["state"] = "superseded_by_cardinality"
            proof_node["supersession"] = {
                "reason": "union_reached_boundary_cardinality",
                "union_sha256": proof["union_sha256"],
                "generation": proof["generation"],
                "boundary_total_count": proof["boundary_total_count"],
            }

        assert_active_cardinality_scan_contract(progress, evidence)

        wrong_binding = deepcopy(evidence)
        wrong_binding["frontier"]["nodes"][0]["supersession"]["generation"] = 1
        with self.assertRaisesRegex(AssertionError, "generation binding mismatch"):
            assert_active_cardinality_scan_contract(progress, wrong_binding)

    def test_malicious_authority_matrix_fails_closed(self) -> None:
        progress, evidence = cardinality_fixture()

        gap = deepcopy(evidence)
        gap["frontier"]["nodes"][0].update({"total_count": 25, "page_count": 2})
        gap["frontier"]["pages"][0].update(
            {"page_number": 2, "total_count": 25, "url": list_url(2)}
        )

        overcount = deepcopy(evidence)
        overcount["frontier"]["pages"][0]["records"] = 2

        forged_refs = deepcopy(evidence)
        forged_refs["membership"]["references"] = ["999"]

        missing_raw = deepcopy(evidence)
        missing_path = missing_raw["frontier"]["pages"][0]["raw_path"]
        missing_raw["raw_verification"]["files"] = [
            row
            for row in missing_raw["raw_verification"]["files"]
            if row["raw_path"] != missing_path
        ]
        missing_raw["raw_verification"]["verified_files"] -= 1
        missing_raw["raw_verification"]["verified_bytes"] -= 1

        duplicate_id = deepcopy(evidence)
        duplicate_page = duplicate_id["frontier"]["pages"][0]
        duplicate_page.update(
            {
                "total_count": 2,
                "records": 2,
                "references": ["100", "101"],
                "mappings": [
                    {"reference_number": "100", "tender_id": "T-100"},
                    {"reference_number": "101", "tender_id": "T-100"},
                ],
            }
        )
        duplicate_id["frontier"]["nodes"][0]["total_count"] = 2

        repeated_generation = deepcopy(evidence)
        repeated_generation["generation_proofs"][1]["generation"] = 1
        repeated_generation["generation_proofs"][1]["candidate_evidence"][
            "superseded"
        ][0]["generation"] = 1

        skipped_generation = deepcopy(evidence)
        skipped_generation["generation_proofs"][1]["generation"] = 3
        skipped_generation["generation_proofs"][1]["candidate_evidence"][
            "superseded"
        ][0]["generation"] = 3

        interleaved_chain = deepcopy(evidence)
        interleaved_chain["generation_proofs"][1]["chain_number"] = 2

        boundary_drift = deepcopy(evidence)
        closing = boundary_drift["generation_proofs"][1]["boundary_evidence"][
            "closing"
        ]
        closing["references"] = ["999"]
        closing["reference_sha256"] = ref_sha(["999"])

        pending_candidate = deepcopy(evidence)
        pending_candidate["generation_proofs"][1]["candidate_evidence"]["checks"] = [
            {
                "reference_number": "998",
                "source": "root_head",
                "state": "pending",
                "status_id": None,
                "tender_id": None,
                "raw_path": None,
                "sha256": None,
                "url": None,
            }
        ]

        blocked = deepcopy(evidence)
        blocked["frontier"]["nodes"][0]["state"] = "blocked"

        wrong_supersession_reason = deepcopy(evidence)
        wrong_supersession_reason["candidates"][0]["error"] = "forged_reason"

        wrong_supersession_generation = deepcopy(evidence)
        wrong_supersession_generation["candidates"][0]["generation"] = 1

        cases = (
            ("page gap", gap, "page gap"),
            ("overcount", overcount, "record/reference count mismatch"),
            ("forged refs", forged_refs, "references differ from RAW replay"),
            ("missing RAW", missing_raw, "lacks export-time byte verification"),
            ("duplicate id", duplicate_id, "duplicate tender id within page"),
            ("same generation", repeated_generation, "not adjacent generations"),
            ("skipped [1,3] generation", skipped_generation, "not adjacent generations"),
            ("interleaved proof chain", interleaved_chain, "interleaved"),
            ("boundary drift", boundary_drift, "head reference drift"),
            ("pending candidate", pending_candidate, "pending candidate"),
            ("blocked frontier", blocked, ".*"),
            (
                "wrong supersession reason",
                wrong_supersession_reason,
                "superseded candidate reason mismatch",
            ),
            (
                "wrong supersession generation",
                wrong_supersession_generation,
                "generation binding mismatch",
            ),
        )
        for name, malicious, message in cases:
            with self.subTest(name=name), self.assertRaisesRegex(
                AssertionError, message
            ):
                assert_active_cardinality_scan_contract(progress, malicious)

    def test_partial_new_cycle_preserves_last_authority(self) -> None:
        authority, evidence = cardinality_fixture()
        partial = deepcopy(authority)
        partial.update(
            {
                "cycle_id": "cycle-next",
                "generation": 1,
                "phase": "opening",
                "union_authoritative": False,
                "partition_authoritative": False,
                "absence_authoritative": False,
                "completion_authoritative": False,
                "complete": False,
            }
        )
        partial["boundary"] = {
            "total_count": 1,
            "head_ref_sha256": authority["boundary"]["head_ref_sha256"],
            "opening_evidence": authority["boundary"]["opening_evidence"],
            "closing_evidence": None,
            "stable": False,
        }
        partial["membership"].update(
            {
                "observed_unique": 0,
                "unexplained_unique": 1,
                "pending_candidates": 1,
                "union_sha256": ref_sha([]),
                "bijection_sha256": ref_sha([]),
                "complete": False,
            }
        )
        partial["frontier"].update(
            {
                "nodes_total": 1,
                "pending": 1,
                "exact": 0,
                "clear_for_authority": False,
                "accepted_pages": 0,
            }
        )
        partial["generation_proofs"].update(
            {
                "recorded": 0,
                "recorded_total": 0,
                "matching_current_union": 0,
                "distinct_generations": 0,
                "generations": [],
                "ordinals": [],
                "authoritative": False,
            }
        )
        partial["last_authority"] = deepcopy(authority)

        self.assertIs(selected_cardinality_authority(partial), partial["last_authority"])
        assert_active_date_scan_contract(partial, evidence)
        active_scan = {"date_fallback": partial}
        descriptor_owner = attach_active_scan_authority_descriptor(
            active_scan,
            evidence,
            {"bytes": 42, "sha256": "d" * 64},
        )
        self.assertIs(descriptor_owner, partial["last_authority"])
        self.assertNotIn("evidence_asset", partial)
        self.assertEqual(
            partial["last_authority"]["evidence_asset"],
            {
                "schema_version": 4,
                "file": "active_scan_authority.json",
                "bytes": 42,
                "sha256": "d" * 64,
            },
        )
        assert_active_missing_truth(active_scan, {})

        mismatched = deepcopy(evidence)
        mismatched["cycle_id"] = "cycle-next"
        with self.assertRaisesRegex(RuntimeError, "cycle_id mismatch"):
            attach_active_scan_authority_descriptor(
                active_scan,
                mismatched,
                {"bytes": 42, "sha256": "d" * 64},
            )

    def test_raw_path_escape_absolute_and_symlink_are_rejected(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "warehouse"
            root.mkdir()
            outside = Path(temp) / "outside.bin"
            outside.write_bytes(b"outside")
            (root / "escape.bin").symlink_to(outside)
            for raw_path in ("../outside.bin", str(outside), "escape.bin"):
                with self.subTest(raw_path=raw_path), self.assertRaisesRegex(
                    RuntimeError, "unsafe|escapes"
                ):
                    _resolve_official_raw_file(root, raw_path, label="malicious")


if __name__ == "__main__":
    unittest.main()
