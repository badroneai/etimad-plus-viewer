# Kashaf publication SLOs

The existing Pages workflow and `check_data_contract.py` remain the release
gates. The independent `Kashaf observability watchdog` reads the immutable
publication branch, GitHub Actions evidence, and the public Pages site. It does
not collect from Etimad or change data semantics.

## Service-level objectives

| Signal | Objective | Warning | Critical |
| --- | --- | --- | --- |
| Publication freshness | a new verified publication within 18 hours | age greater than 12 hours | age greater than 18 hours or no publication |
| Pages convergence | live `snapshot_id` matches the publication branch within 60 minutes | mismatch after 30 minutes | mismatch after 60 minutes |
| Live data contract | every converged snapshot passes the complete remote bytes/SHA/count/partition contract | none | any failure after the expected snapshot is live |
| Queue/backlog trend | no material growth across consecutive verified publications | two growth transitions | three growth transitions |

Backlog growth is material only when both the net increase is at least 10 work
items and at least 20% of the baseline. The three independent metrics are
coverage days pending, census work items, and region-backfill records remaining.
This avoids adding unlike units and avoids alerting on one bounded-run fluctuation.

## Alert path

The watchdog runs at `03:27` and `15:27` UTC and on demand. It emits
`health.json`, a GitHub step summary, annotations, the existing full remote
contract log, and a 30-day `kashaf-health-*` artifact. Critical breaches fail
the workflow. A queued Pages run with no started job is classified as
`platform_runner_allocation`; a started failed step is
`pages_application_failure`.

Repository maintainers must enable GitHub Actions failure notifications. No
external service, issue-write permission, or paid integration is required.
