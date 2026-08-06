# ADR-0002: Separate protected source from Kashaf publication data

- Status: Accepted
- Date: 2026-08-06
- Scope: Collector-to-viewer publication and GitHub Pages deployment

## Context

The collector previously used a write-enabled deploy key to commit generated `data/`
directly to viewer `main`. That made source protection incompatible with unattended
publication: the same credential that updates the read model could also replace
application code or the privileged Pages workflow.

GitHub deploy keys can push Git data but cannot call `workflow_dispatch`. A Pages
workflow triggered directly from an unprotected publication branch would also be
unsafe because a compromised publisher could replace that branch's workflow before
triggering it.

## Decision

Generated Kashaf data is published to `publication/kashaf-data`. The branch starts
from `main` for a compatible first bootstrap and thereafter receives commits whose
staged paths are limited to `data/`.

A read-only workflow on that branch may emit the unprivileged
`Kashaf data publication signal`. The privileged Pages workflow can be activated by
`workflow_run`, so GitHub loads it from the default branch. It then:

1. rejects signals from another repository, branch, failed run, or stale branch tip;
2. checks out protected source from `main`;
3. checks out the exact signalled publication commit separately;
4. accepts only a regular, non-symlinked `data/` tree containing `manifest.json`;
5. overlays that tree, runs all quality and data-contract gates, and deploys one
   immutable Pages artifact.

The signal workflow has only `contents: read`. Only the deploy job receives
`pages: write` and `id-token: write`. The collector deploy key remains write-enabled
for the viewer repository because GitHub deploy keys cannot be path-scoped, but its
expected write target is only the publication branch.

## Production correction: trusted polling fallback

Production run `31128644985` pushed publication commit
`b4866b0d7428c74f387b97d88337740bc7e48e2e`, but that deploy-key push did not
create a signal workflow run in this repository setup. The signal path therefore
remains a best-effort fast path, not the availability mechanism.

The protected-main Pages workflow also runs at minutes
`03,13,23,33,43,53` of every UTC hour. Its lightweight selector:

1. resolves immutable `main` and publication branch SHAs;
2. fetches the selector script from that exact protected `main` SHA;
3. fetches `data/manifest.json` from the exact publication SHA and from live Pages;
4. validates a non-empty `snapshot_id` and compares both snapshot identity and
   manifest byte SHA-256;
5. skips all checkout, dependency installation, tests, packaging, and deployment
   when the live manifest is already exact;
6. executes the existing expensive verified deployment only when the manifests
   diverge or the live manifest is unavailable/invalid.

An invalid publication manifest fails the selector closed. A missing publication
branch is a successful no-op. The ten-minute cadence leaves nominal room for the
collector's 1,200-second live convergence window without adding a PAT, app, or
repository secret.

## Compatibility and migration

Until `publication/kashaf-data` exists, a `main` push or manual Pages run packages
the existing `main/data` tree. Merging this ADR and workflow therefore does not
change production data. The first collector run after its companion change creates
the publication branch from `main`, validates the projection locally, and pushes a
data-only commit. Only then does Pages consume the new path.

`main/data` remains as a compatibility snapshot during the migration. Removing it
is a later reviewed change after at least one successful scheduled publication,
Pages deployment, live contract check, and rollback drill.

## Rollback

To roll back data without changing source, move `publication/kashaf-data` forward
with a new commit restoring a previously verified `data/` tree; do not force-push.
The resulting signal runs the same contract and Pages gates. For an urgent pipeline
rollback, disable the publication signal workflow and manually dispatch Pages from
`main` with `use_main_data=true`, which packages the compatibility snapshot.

## Consequences

- `main` can require pull requests and block direct collector writes.
- Publication stays automated without adding a PAT, GitHub App, or new secret.
- Scheduled workflows are best-effort GitHub service timers and can be delayed;
  ten minutes is the target cadence, not a hard delivery guarantee.
- A stolen deploy key can corrupt or delete the publication branch and cause
  availability failures, but cannot modify protected source through the supported
  path or replace the privileged deployment workflow.
- Quarterly orphan rotation from ADR-0001 is deferred. Rotation requires a trusted
  pointer mechanism and is not needed to establish the immediate security boundary.
