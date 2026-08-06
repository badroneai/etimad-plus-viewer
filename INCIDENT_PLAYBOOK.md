# Kashaf publication incident playbook

Start with the failed `Kashaf observability watchdog` summary and its
`kashaf-health-*` artifact. Preserve `health.json`, `contract.log`, the
publication commit, and the Pages run URL. Do not rerun source acquisition to
repair a presentation or convergence incident.

## Publication freshness

1. Confirm the official collector has a recent verified snapshot.
2. If the collector is healthy, inspect the data-only push and publication
   branch tip. A missing signal after a deploy-key push is expected in the
   observed setup; inspect the next protected-main Pages poll instead.
3. Confirm the scheduled selector ran within its ten-minute target cadence and
   recorded either `publication_already_live` or a deployment reason.
4. Repair the first failed stage and rerun only that stage where possible.
5. Close after publication age is at most 12 hours and the live contract passes.

## Pages convergence and contract

1. If the expected snapshot is not live for 30–60 minutes, keep the state as
   pending and rerun only the remote contract.
2. After 60 minutes, inspect the trusted Pages workflow and deployment.
3. If the expected snapshot is live but SHA, bytes, count, index partition, or
   detail-shard validation fails, stop publication and treat it as a data
   contract incident. Do not edit an individual JSON asset.
4. Roll back with a new commit on `publication/kashaf-data`; never force-push.

## Runner allocation versus application failure

`platform_runner_allocation` means the latest relevant run has no started job.
Confirm GitHub Actions status and wait for capacity; code changes are not
evidence-based. `pages_application_failure` means a job started and a step
failed; inspect the named step and its logs.

## Queue/backlog growth

1. Read the metric and five-snapshot series under `checks.backlog.alerts`.
2. Verify all samples belong to valid publication commits and look for a cycle
   reset before changing code.
3. Two material growth transitions are a warning; three are critical.
4. Diagnose stalled cursors, replay/integrity errors, or repeated throttling.
   Do not raise page/detail/award budgets as incident response.
5. Close after two consecutive publications stop growth and all integrity and
   live-contract gates remain green.
