# Takeover snapshot

- UTC: `2026-08-27T02:27:13Z`
- Scope: read-only takeover verification; secrets were not loaded and no API request was made.
- RUN_ROOT: `/data/workspace/symbol-locator-repair-20260826-083933/experiments/intentq20_x2_resilient_20260827-014906`

## Decision

Situation A: the existing tmux session, supervisor, and active A1 case are alive. No restart, resume, canary, cleanup, or case rerun was performed.

## Supervisor and tmux

- tmux session: `symloc_x2_20260827-014906`
- pane: `0.0`
- pane PID / supervisor PID: `201800`
- supervisor PGID: `201800`
- pane cwd: the fixed RUN_ROOT
- pane dead: `0`; tmux session alive
- pane recent output: empty because supervisor stdout is redirected to the RUN_ROOT log; no input was sent to the pane.
- `logs/supervisor_stdout.log`: present, 0 bytes at snapshot time.

## State and heartbeat

At the snapshot:

```json
{
  "status": "RUNNING",
  "stage": "case",
  "face": "A1",
  "current_case": "django__django-15104",
  "current_retry_count": 0,
  "api_canary_attempts": 2,
  "last_successful_api_time": "2026-08-27T02:25:35.218498+00:00",
  "heartbeat_timestamp": "2026-08-27T02:26:13.436692+00:00",
  "completed_id_count": 6,
  "active_pgid": 217473,
  "data_free_gib": 82.35,
  "MemAvailable_kB": 9878300,
  "SwapFree_kB": 3314664
}
```

The current case process group had elapsed time about 1 minute 38 seconds at the snapshot, below the 3600-second case limit. The supervisor process had been alive for about 38 minutes. The state file's `completed_case_ids` field was still `{}`; the authoritative existing A1 JSONL and heartbeat independently validate six completed IDs, and no successful ID was rerun.

## Active process ownership

The active group `217473` is a child of supervisor `201800` and contains the conda wrapper, evaluator, LocAgent shim and its Python children for A1 / `django__django-15104`. The command uses the fixed project root, `--workers 1`, `openai/gpt-5.4-mini`, top-k 5, timeout 3600, and the RUN_ROOT output. No unrelated process was operated on.

## Existing output validation

Validation used `text.splitlines()` and JSON parsing per line. Required metric fields were checked inside each row's `metrics` object: `precision`, `recall`, `f1_score`, `hit_file_rate`, `hit_region_rate`, `noise_file_rate`, `noise_region_rate`, `weighted_core_coverage`, and `context_efficiency`.

| Face | Rows | Unique IDs | Invalid JSON | Unexpected IDs | Duplicate IDs | Missing/invalid metrics | Path violations |
|---|---:|---:|---:|---:|---:|---:|---:|
| A1 | 6 | 6 | 0 | 0 | 0 | 0 | 0 |
| B1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| A2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| B2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

A1 completed IDs, in canonical order: `django__django-11206`, `scikit-learn__scikit-learn-14141`, `django__django-11066`, `django__django-12304`, `django__django-10999`, `matplotlib__matplotlib-24026`. They are a unique subset of the expected 20 IDs.

## Integrity and configuration-summary checks

- `diagnostics/project_root_discovery.md` confirms the unique passing project root: `/data/workspace/symbol-locator-repair-20260826-083933/source_copy/source_copy/SWE-Explore-Bench`.
- `LOCAGENT_PATH` and `SYMBOL_LOCATOR_PATH` remain in that same source-copy tree.
- Existing source key hash check: `13/13` match; no source file was changed by takeover.
- Existing copied data and issue-map source/target SHA256 values match.
- Existing command audit confirms `--repos` equals `PROJECT_ROOT`, not `PROJECT_ROOT/repos`, and no API-key CLI parameter exists.
- Existing redacted configuration summary still records only presence/equality, HTTPS scheme/hostname, and fixed model fields; no credential value was read or recorded during takeover.
- Existing short `TMPDIR` alias, explicit absolute Conda/LocAgent paths, tightened sensitive scan, and line-based output validation are present in the RUN_ROOT supervisor orchestration.

## Action after snapshot

Continue monitoring the live supervisor and current case. Do not restart the session, issue `--resume`, load secrets, run another canary, or rerun any completed ID while the supervisor and case remain live.
