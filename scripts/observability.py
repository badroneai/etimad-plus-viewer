#!/usr/bin/env python3
"""Evaluate Kashaf publication, convergence, Pages, and backlog SLOs."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "kashaf.operational-health.v1"
PUBLICATION_WARNING_HOURS = 12.0
PUBLICATION_CRITICAL_HOURS = 18.0
CONVERGENCE_WARNING_MINUTES = 30.0
CONVERGENCE_CRITICAL_MINUTES = 60.0
BACKLOG_MIN_ABSOLUTE_GROWTH = 10
BACKLOG_MIN_RELATIVE_GROWTH = 0.20


def parse_timestamp(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp must include timezone: {value!r}")
    return parsed.astimezone(timezone.utc)


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _run_timestamp(run: dict[str, Any]) -> datetime:
    for key in ("updated_at", "run_started_at", "created_at"):
        value = run.get(key)
        if isinstance(value, str) and value:
            return parse_timestamp(value)
    return datetime.min.replace(tzinfo=timezone.utc)


def classify_pages_run(
    runs_payload: dict[str, Any], jobs_payload: dict[str, Any]
) -> dict[str, Any]:
    raw_runs = runs_payload.get("workflow_runs")
    if not isinstance(raw_runs, list):
        raise ValueError("Pages runs JSON must contain workflow_runs")
    runs = [run for run in raw_runs if isinstance(run, dict)]
    latest = max(runs, key=_run_timestamp, default=None)
    if latest is None:
        return {"state": "alert", "incident_class": "pages_run_missing", "run_id": None}
    if latest.get("conclusion") == "success":
        return {
            "state": "healthy",
            "incident_class": "none",
            "run_id": latest.get("id"),
            "html_url": latest.get("html_url"),
        }
    raw_jobs = jobs_payload.get(str(latest.get("id")), [])
    jobs = (
        [job for job in raw_jobs if isinstance(job, dict)]
        if isinstance(raw_jobs, list)
        else []
    )
    started = [job for job in jobs if job.get("started_at")]
    failed_steps: list[str] = []
    for job in jobs:
        for step in job.get("steps") or []:
            if (
                isinstance(step, dict)
                and step.get("conclusion") == "failure"
                and isinstance(step.get("name"), str)
            ):
                failed_steps.append(step["name"])
    allocation_states = {"queued", "requested", "waiting", "pending"}
    if latest.get("status") in allocation_states and not started:
        incident_class = "platform_runner_allocation"
    elif latest.get("status") != "completed" and started and not failed_steps:
        return {
            "state": "pending",
            "incident_class": "pages_run_in_progress",
            "run_id": latest.get("id"),
            "html_url": latest.get("html_url"),
            "status": latest.get("status"),
            "conclusion": latest.get("conclusion"),
            "failed_steps": [],
        }
    elif not started and not failed_steps:
        incident_class = "platform_runner_allocation"
    else:
        incident_class = "pages_application_failure"
    return {
        "state": "alert",
        "incident_class": incident_class,
        "run_id": latest.get("id"),
        "html_url": latest.get("html_url"),
        "status": latest.get("status"),
        "conclusion": latest.get("conclusion"),
        "failed_steps": failed_steps,
    }


def _number(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))


def backlog_metrics(status: dict[str, Any]) -> dict[str, int]:
    active = status.get("active_scan") or {}
    census = status.get("active_date_scan") or active.get("date_fallback") or {}
    coverage = census.get("coverage") or {}
    frontier = census.get("frontier") or {}
    refinement = census.get("single_day_refinement") or {}
    region = status.get("region_backfill") or {}
    return {
        "coverage_units_pending": _number(coverage.get("units_pending")),
        "census_work_items": (
            _number(frontier.get("pending"))
            + _number(frontier.get("pending_page2"))
            + _number(frontier.get("refining"))
            + _number(refinement.get("nodes_pending"))
            + _number(refinement.get("nodes_mirror_pending"))
        ),
        "region_backfill_remaining": _number(region.get("remaining")),
    }


def evaluate_backlog(statuses_newest_first: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = list(reversed(statuses_newest_first[:5]))
    series = [backlog_metrics(status) for status in statuses]
    cycle_ids = {
        str((status.get("active_scan") or {}).get("cycle_id"))
        for status in statuses
        if (status.get("active_scan") or {}).get("cycle_id")
    }
    alerts: list[dict[str, Any]] = []
    if len(series) >= 3:
        for metric in series[-1]:
            if metric != "region_backfill_remaining" and len(cycle_ids) > 1:
                continue
            values = [item[metric] for item in series]
            transitions = [
                values[index] > values[index - 1] for index in range(1, len(values))
            ]
            consecutive = 0
            for grew in reversed(transitions):
                if not grew:
                    break
                consecutive += 1
            baseline_index = max(0, len(values) - consecutive - 1)
            baseline = values[baseline_index]
            growth = values[-1] - baseline
            relative = growth / max(1, baseline)
            if (
                consecutive >= 2
                and growth >= BACKLOG_MIN_ABSOLUTE_GROWTH
                and relative >= BACKLOG_MIN_RELATIVE_GROWTH
            ):
                alerts.append(
                    {
                        "metric": metric,
                        "state": "critical" if consecutive >= 3 else "warning",
                        "values_oldest_first": values,
                        "consecutive_growth_transitions": consecutive,
                        "absolute_growth": growth,
                        "relative_growth": round(relative, 4),
                    }
                )
    state = (
        "critical"
        if any(alert["state"] == "critical" for alert in alerts)
        else ("warning" if alerts else "healthy")
    )
    return {
        "state": state,
        "samples": len(series),
        "cycle_ids": sorted(cycle_ids),
        "current": series[-1] if series else {},
        "alerts": alerts,
    }


def evaluate_health(
    publication_manifest: dict[str, Any],
    live_manifest: dict[str, Any] | None,
    pages_runs: dict[str, Any],
    pages_jobs: dict[str, Any],
    statuses_newest_first: list[dict[str, Any]],
    *,
    contract_exit_code: int,
    publication_committed_at: datetime,
    now: datetime,
) -> dict[str, Any]:
    observed_at = now.astimezone(timezone.utc)
    generated_at = parse_timestamp(str(publication_manifest["generated_at"]))
    publication_age = max(0.0, (observed_at - generated_at).total_seconds() / 3600)
    if publication_age > PUBLICATION_CRITICAL_HOURS:
        freshness_state = "critical"
    elif publication_age > PUBLICATION_WARNING_HOURS:
        freshness_state = "warning"
    else:
        freshness_state = "healthy"

    expected_snapshot = publication_manifest.get("snapshot_id")
    live_snapshot = live_manifest.get("snapshot_id") if live_manifest else None
    converged = expected_snapshot == live_snapshot
    convergence_age = max(
        0.0,
        (observed_at - publication_committed_at.astimezone(timezone.utc)).total_seconds()
        / 60,
    )
    if converged and contract_exit_code == 0:
        convergence_state = "healthy"
        convergence_class = "none"
    elif converged:
        convergence_state = "critical"
        convergence_class = "data_contract_failure"
    elif convergence_age > CONVERGENCE_CRITICAL_MINUTES:
        convergence_state = "critical"
        convergence_class = "pages_convergence_timeout"
    elif convergence_age > CONVERGENCE_WARNING_MINUTES:
        convergence_state = "warning"
        convergence_class = "pages_convergence_pending"
    else:
        convergence_state = "pending"
        convergence_class = "pages_convergence_pending"

    pages = classify_pages_run(pages_runs, pages_jobs)
    backlog = evaluate_backlog(statuses_newest_first)
    critical = any(
        state == "critical"
        for state in (freshness_state, convergence_state, backlog["state"])
    )
    if pages["state"] == "alert" and pages["incident_class"] != "platform_runner_allocation":
        critical = True
    warning = any(
        state in {"warning", "pending"}
        for state in (freshness_state, convergence_state, backlog["state"])
    ) or pages["state"] != "healthy"
    return {
        "schema": SCHEMA,
        "observed_at": iso_utc(observed_at),
        "state": "critical" if critical else ("warning" if warning else "healthy"),
        "alert": critical or warning,
        "critical": critical,
        "slos": {
            "publication_freshness": {
                "warning_hours": PUBLICATION_WARNING_HOURS,
                "critical_hours": PUBLICATION_CRITICAL_HOURS,
            },
            "pages_convergence": {
                "warning_minutes": CONVERGENCE_WARNING_MINUTES,
                "critical_minutes": CONVERGENCE_CRITICAL_MINUTES,
            },
            "data_contract": {"required_success_percent": 100},
            "backlog_growth": {
                "warning_consecutive_publications": 2,
                "critical_consecutive_publications": 3,
                "minimum_absolute_growth": BACKLOG_MIN_ABSOLUTE_GROWTH,
                "minimum_relative_growth": BACKLOG_MIN_RELATIVE_GROWTH,
            },
        },
        "checks": {
            "publication_freshness": {
                "state": freshness_state,
                "age_hours": round(publication_age, 3),
                "snapshot_id": expected_snapshot,
            },
            "pages_convergence": {
                "state": convergence_state,
                "incident_class": convergence_class,
                "expected_snapshot_id": expected_snapshot,
                "live_snapshot_id": live_snapshot,
                "age_minutes": round(convergence_age, 3),
                "contract_exit_code": contract_exit_code,
            },
            "pages_workflow": pages,
            "backlog": backlog,
        },
    }


def render_summary(result: dict[str, Any]) -> str:
    checks = result["checks"]
    return (
        "## Kashaf operational watchdog\n\n"
        f"- Overall: **{result['state'].upper()}**\n"
        f"- Publication freshness: `{checks['publication_freshness']['state']}` "
        f"({checks['publication_freshness']['age_hours']} h)\n"
        f"- Pages convergence/contract: `{checks['pages_convergence']['state']}` "
        f"(`{checks['pages_convergence']['incident_class']}`)\n"
        f"- Pages workflow: `{checks['pages_workflow']['incident_class']}`\n"
        f"- Backlog trend: `{checks['backlog']['state']}`\n"
        "- Runbook: `INCIDENT_PLAYBOOK.md`\n"
    )


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publication-manifest", type=Path, required=True)
    parser.add_argument("--live-manifest", type=Path)
    parser.add_argument("--pages-runs-json", type=Path, required=True)
    parser.add_argument("--pages-jobs-json", type=Path, required=True)
    parser.add_argument("--status-history-dir", type=Path, required=True)
    parser.add_argument("--contract-exit-code", type=int, required=True)
    parser.add_argument("--publication-committed-at", required=True)
    parser.add_argument("--now")
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--summary-file", type=Path)
    parser.add_argument("--fail-on-critical", action="store_true")
    args = parser.parse_args(argv)
    statuses = [
        load_json(path) for path in sorted(args.status_history_dir.glob("*.json"))
    ]
    result = evaluate_health(
        load_json(args.publication_manifest),
        load_json(args.live_manifest) if args.live_manifest and args.live_manifest.exists() else None,
        load_json(args.pages_runs_json),
        load_json(args.pages_jobs_json),
        statuses,
        contract_exit_code=args.contract_exit_code,
        publication_committed_at=parse_timestamp(args.publication_committed_at),
        now=parse_timestamp(args.now) if args.now else datetime.now(timezone.utc),
    )
    atomic_json(args.output_json, result)
    if args.summary_file:
        with args.summary_file.open("a", encoding="utf-8") as handle:
            handle.write(render_summary(result) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if args.fail_on_critical and result["critical"] else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"state": "invalid", "error": str(exc)}, ensure_ascii=False))
        raise SystemExit(2) from exc
