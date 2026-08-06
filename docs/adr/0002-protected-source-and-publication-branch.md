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

A read-only workflow on that branch emits the unprivileged
`Kashaf data publication signal`. The privileged Pages workflow is activated by
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
- A stolen deploy key can corrupt or delete the publication branch and cause
  availability failures, but cannot modify protected source through the supported
  path or replace the privileged deployment workflow.
- Quarterly orphan rotation from ADR-0001 is deferred. Rotation requires a trusted
  pointer mechanism and is not needed to establish the immediate security boundary.
