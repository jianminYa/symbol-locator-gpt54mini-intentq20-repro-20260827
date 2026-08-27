# Original handoff snapshot

This tree is an archival handoff snapshot used as the baseline for the
three-commit reproduction repository. It is not represented as an upstream
clean commit and makes no claim about upstream history.

## Source and provenance

- Source root: `/data/workspace/symbol-locator-handoff-20260823-065432/symbol-locator-handoff/`
- Discovered project root: `SWE-Explore-Bench/`
- Vendored LocAgent: `SWE-Explore-Bench/third_party/LocAgent/`
- Adapter: `symbol-locator-locagent/`
- The source location and the corresponding repair tree were selected from
  the recorded project-root discovery, source manifest, and 13-file key-hash
  verification in the experiment diagnostics.

## Staging exclusions

The baseline intentionally excludes Git metadata, environment/secrets,
dependency or runtime material, benchmark repository clones, generated
results/logs/cache/tmp/diagnostics, Python bytecode, and process/lock files.
The exact staging exclusions and hashes are recorded in the final artifact
manifest and privacy exclusions document in Commit 3.
