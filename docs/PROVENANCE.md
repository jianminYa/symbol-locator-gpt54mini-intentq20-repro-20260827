# Provenance

## Selected source trees

The published source was selected from the recorded project discovery and
manifest evidence, not by choosing a convenient historical directory.

- Original handoff: `/data/workspace/symbol-locator-handoff-20260823-065432/symbol-locator-handoff/`
- Original project root: `SWE-Explore-Bench/`
- Verified repair root: `/data/workspace/symbol-locator-repair-20260826-083933/`
- Verified repair project root: `/data/workspace/symbol-locator-repair-20260826-083933/source_copy/source_copy/SWE-Explore-Bench/`
- Verified adapter: `/data/workspace/symbol-locator-repair-20260826-083933/source_copy/source_copy/symbol-locator-locagent/`
- Vendored LocAgent: `SWE-Explore-Bench/third_party/LocAgent/`

The V2 discovery searched the repair `source_copy` to depth 8, required the
recorded SWE-Explore-Bench/LocAgent structure, excluded experiment/result/
cache/log/tmp/diagnostic snapshots, and found one valid candidate. Its
20-instance repo preflight resolved every repo under that candidate's
`repos/` root without `repos/repos`.

## Hash and state evidence

- `experiments/intentq20_x2_resilient_20260827-014906/diagnostics/source_key_hash_checks.json`: 13/13 key source hashes matched the saved repair manifest.
- `data_copy_hashes.json`: canonical data and issue-map copies matched their sources.
- `repo_preflight.tsv`: 20 unique instances, all resolved directories present.
- `final_report.md`: `COMPLETE`, A1/B1/A2/B2 complete.
- `original_and_source_manifest_check.md`: protected paths `NO_DIFFERENCES` and Git status unchanged.

Commit 1 is deliberately described as a handoff snapshot. It is not an
upstream clean commit. Commit 2 is the exact source overlay selected by the
repair-tree verification; its source-only comparison is available as
`experiments/intentq20_x2_resilient_20260827-014906/diagnostics/repair_commit1_to_commit2.diff`.

## Publication boundary

This repository contains source and non-sensitive experiment artifacts only.
Benchmark repository clones, runtime environments, caches, temporary files,
local settings, and credentials are excluded. The original handoff, repair
source, and experiment directories remain untouched by publication; all
copying and documentation happened in a new staging tree.
