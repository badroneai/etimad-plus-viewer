# ADR-0001: Partition the awarded index and rotate derived-data history

- Status: Accepted
- Date: 2026-07-18
- Scope: Kashaf static awarded index and long-term Git growth

## Context

`data/awarded_index.json` had reached about 27 MiB, more than half of the
50 MiB publication guard. Replacing that monolith on every projection also
causes Git to retain another large derived object even when only a small share
of records changes. The detailed award records are already split into 64 stable
SHA-256 buckets, and deep links use those detail buckets directly.

The official warehouse, its raw evidence, the state Release, and the secondary
backup are the durable sources of truth. The files in this repository are a
reproducible read model for Kashaf, not the only archive of the acquired data.

## Decision: deterministic searchable-index parts

Schema version 3 replaces the inline records in `awarded_index.json` with a
small descriptor. The descriptor declares exactly 16 part files:

```text
data/awarded_index.json
data/awarded_index_parts/00.json
...
data/awarded_index_parts/15.json
```

The assignment rule is versioned as `sha256_first_byte_mod_16`: hash the UTF-8
tender reference with SHA-256 and take the first byte modulo 16. Records inside
each part are sorted by their string reference. All 16 files are emitted even
when a part is empty, and files outside the declared set are removed.

The descriptor records the part id, path, record count, byte count, and SHA-256
for every part. The top-level manifest also lists every path in `assets`, so the
browser can append the matching digest prefix to each request and load parts
progressively. The data contract verifies locally and remotely that:

- the descriptor, algorithm, format version, and part count are exact;
- every reference occurs once, is in its computed part, and the union equals
  the descriptor and dataset counts;
- descriptor bytes, SHA-256, and counts match the top-level manifest and the
  downloaded files;
- each index row points to its independently computed one of 64 detail shards;
- each part remains below 5 MiB and the root descriptor below 1 MiB;
- no stale part file remains.

The existing 64 `awarded_details` shards do not change. Therefore `#t/<ref>`
continues to compute and fetch the detail shard directly; it does not depend on
loading any or all searchable-index parts.

## Decision: quarterly orphan publication branches

Generated data history will move off the source-code branch to a dedicated
publication branch. At the start of each calendar quarter the publisher will:

1. create a new orphan branch containing the current generated `data/` tree and
   its manifest as the root commit;
2. make Pages deployments from a shallow checkout of that active publication
   branch plus the source-code checkout;
3. retain the previous publication branch for one quarter as an operational
   rollback window, then delete it after verifying the Release and secondary
   backup copies;
4. keep immutable run ids, manifests, SHA-256 values, and restoration evidence
   in the official state/backup repositories rather than retaining every
   derived Pages commit forever.

The branch switch must be atomic and `force-with-lease` is required for any
pointer update. The active branch must never be replaced until the local data
contract, deployed Pages contract, and deep-link smoke test all pass.

This bounds normal clones to source history plus at most two quarters of derived
publication history. It also preserves a rollback window without treating Git
objects as the authoritative data archive.

## Consequences

- Initial awarded browsing can render after the descriptor and first part;
  complete search/facet results become available as remaining parts arrive.
- A single updated reference rewrites one index part and one detail shard rather
  than a 27 MiB monolith.
- Sixteen requests replace one large request, but they are immutable,
  independently cacheable, and can be fetched concurrently or progressively.
- Changing the part count or assignment algorithm is a contract migration and
  requires another schema/format decision; it cannot be changed silently.
- Publication-branch rotation is a later pipeline migration. Until it is
  implemented, this ADR is the governing strategy and generated data continues
  to use the current branch workflow.

## Rejected alternatives

- **Ordinal pages:** insertion near the start cascades changes through every
  later page and defeats content-addressed caching.
- **Keep the monolith until 50 MiB:** leaves no safe migration margin and makes
  every publication increasingly expensive.
- **Increase the limit:** postpones the failure while retaining the same
  bandwidth and Git-growth problem.
- **Retain all quarterly branches forever:** bounds a checkout but not repository
  object growth; durable history already belongs to the state and backup stores.
