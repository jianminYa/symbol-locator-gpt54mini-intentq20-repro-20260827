# symbol-locator gpt-5.4-mini — IntentQ20 temperature trace path-fix study

This branch publishes the first completed A1/B1 faces of the fresh
`temperature_trace_pathfix_20260827-232041` study. The existing `main` branch
and its earlier three-commit publication are preserved unchanged. This branch
uses the same three-commit layout:

1. original handoff snapshot;
2. execution, Pyright lifecycle, temperature, and safe repository-path repairs;
3. this study's non-sensitive A1/B1 results and complete per-case traces.

The publication checkpoint was taken after A1 and B1 passed their 20/20 gates.
A2 had just started and is intentionally not included here; this is not a
complete T0 x2 report yet. No old T0 A1 rows were copied, resumed, or merged.

## What changed in this study

The stopped predecessor exposed a parser/orchestration problem: four model
locations for `django__django-10973` were absolute `/django/...` spellings,
and the parser's entity/Fallback A/A2/B paths did not pass the repository root
to `_normalize_path`. The new public source converts real absolute paths inside
the current repository to POSIX-relative paths, accepts the established
`/testbed/` and `/workspace/` forms only when safe, and rejects external or
repo-escaping paths instead of silently truncating them.

The supervisor now writes the trace-integrity sidecar in a `finally`-equivalent
path before re-raising a structural hard stop, and retains a structured marker
for valid semantic empty results. Every case uses a unique retained trace root.
The LocAgent child temperature remains configurable through
`LOCAGENT_TEMPERATURE`; this run used `0`. Prompts, finish contract, scorer,
top-k, benchmark data, and metrics were not changed.

Offline replay of the preserved predecessor's 16 T0 A1 traces parsed all 16
sets. The path patch changed normalization in exactly one case,
`django__django-10973`; unrelated normalization changes were zero. The exact
four captured `/django/...` spellings were rejected for that snapshot because
they were not files inside its benchmark repository, preventing metric
pollution. See `experiments/temperature_trace_pathfix_20260827-232041/diagnostics/pathfix/`.

## Checkpoint configuration and results

Both faces used the same ordered 20-instance IntentQ20 data, model
`openai/gpt-5.4-mini`, temperature `0`, top-k `5`, one worker, and a 3600-second
case timeout. A1 left `SYMBOL_LOCATOR_ENABLED` unset; B1 set it to `1`. The
validated `--repos` value was the copied `SWE-Explore-Bench` project root,
not its `repos/` child.

| metric | A1 | B1 | B1-A1 |
|---|---:|---:|---:|
| precision | 0.337217 | 0.377414 | +0.040197 |
| recall | 0.494982 | 0.532784 | +0.037802 |
| F1 | 0.373605 | 0.396113 | +0.022508 |
| hit_file | 0.522500 | 0.690833 | +0.168333 |
| hit_region | 0.431667 | 0.526667 | +0.095000 |
| noise_file | 0.438333 | 0.389167 | -0.049167 |
| noise_region | 0.420000 | 0.360000 | -0.060000 |
| WCC | 0.299350 | 0.367071 | +0.067721 |
| context_efficiency | 0.561307 | 0.656501 | +0.095194 |

Noise metrics are lower-is-better. Both published faces contain 20/20 unique
rows, finite metrics, complete trace payloads, and integrity sidecars. A1 has
no scorer calls, as expected for the vanilla face; B1 recorded 69 scorer calls
with redacted usage counts in its report. The face reports contain the complete
means, spreads, and wall-clock totals.

## Published files

- `data/`: the 20-instance bench, issue map, and order list;
- `faces/A1/` and `faces/B1/`: output JSONL and per-case retained traces;
- `attempts/` and `logs/`: per-case attempt metadata and redacted execution logs;
- `diagnostics/run_checkpoint/`: preflight, face reports, canary metadata, and
  supervisor timeline;
- `diagnostics/pathfix/`: root-cause audit, parser/sidecar diffs, no-API tests,
  and offline replay comparison;
- `night_supervisor.py` and `trace_analyzer.py`: the run tooling used at the
  checkpoint.

Caches, temporary directories, benchmark repository clones, environments,
`.env`/`.secrets`, settings files, credentials, request headers, and API
responses containing sensitive headers are excluded. See
`PRIVACY_EXCLUSIONS.md` and `ARTIFACT_MANIFEST.md`.

## Review

The published A1/B1 checkpoint is descriptive, not a two-round estimate. The
full T0 x2 run remains in the local isolated study directory and will only be
published after A2 and B2 complete and pass their own gates. The branch name is
`temperature-trace-pathfix`; existing `main` was not force-pushed or amended.
