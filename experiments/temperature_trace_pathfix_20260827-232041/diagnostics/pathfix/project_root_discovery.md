# Project root discovery and fixed configuration

- search root: `NEW_ROOT/source/`
- depth limit: `8`
- candidate directories named `SWE-Explore-Bench`: `2`; structurally valid candidates: `1`
- invalid nested candidate was retained as a discovery observation and excluded because it lacks the required files.
- `PROJECT_ROOT`: `/data/workspace/symbol-locator-repair-20260826-083933/experiments/temperature_trace_pathfix_20260827-232041/source/SWE-Explore-Bench`
- `LOCAGENT_PATH`: `/data/workspace/symbol-locator-repair-20260826-083933/experiments/temperature_trace_pathfix_20260827-232041/source/SWE-Explore-Bench/third_party/LocAgent`
- `SYMBOL_LOCATOR_PATH`: `/data/workspace/symbol-locator-repair-20260826-083933/experiments/temperature_trace_pathfix_20260827-232041/source/symbol-locator-locagent`
- required-source hash baseline: `13/13` before any API run; the parser hash is the new path-fix hash and all other key hashes match the copied verified baseline.
- fixed argv assertion: `--repos == PROJECT_ROOT`; resolved paths are exactly `PROJECT_ROOT/repos/<instance_id>`; no resolved path contains `/repos/repos/`.
- data preflight: `20` unique IDs, `20/20` directories exist and are directories; order and issue map checks pass.
- secrets/API: not read or called during this discovery and offline phase.
