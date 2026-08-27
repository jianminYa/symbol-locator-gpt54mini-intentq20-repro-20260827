# symbol-locator gpt-5.4-mini IntentQ20 x2 reproduction

This private repository publishes the verified repair source and the complete
non-sensitive artifacts for the serial IntentQ20 A/B x2 experiment. It is a
functional and metric reproduction in the recorded environment, not a claim
of bit-for-bit reproducibility.

## Commit history

The repository intentionally has three linear, separately pushed commits:

1. `ac13d23ab60b475e26f02f2bc665642b3276cafd` — `chore: import original symbol-locator handoff snapshot`
2. `a26760b638444b717c5f616a77a1f281056e1447` — `fix: stabilize LocAgent execution and symbol-locator Pyright lifecycle`
3. This publication commit — `results: add complete IntentQ20 x2 A/B experiment artifacts`; resolve its SHA with `git log -1`.

The source-only repair comparison is `git diff ac13d23 a26760b6` or the
published [repair diff](experiments/intentq20_x2_resilient_20260827-014906/diagnostics/repair_commit1_to_commit2.diff).

## Contents and provenance

- `SWE-Explore-Bench/`: original handoff project code and vendored LocAgent,
  then the verified repair overlay.
- `symbol-locator-locagent/`: the corresponding symbol-locator adapter.
- `openclaw-symbol-locator/`: the companion source tree present in the handoff.
- `experiments/intentq20_x2_resilient_20260827-014906/`: final JSONL results,
  per-case traces, logs, attempts, state, diagnostics, and supervisor files.
- `docs/PROVENANCE.md`: source-root discovery and manifest evidence.
- `docs/REPAIR_NOTES.md`: file-by-file explanation based on the recorded diff.
- `ARTIFACT_MANIFEST.md`: SHA256 and size for every published file except the
  manifest itself (self-reference is explicitly marked there).
- `PRIVACY_EXCLUSIONS.md`: excluded local settings and reproducible runtime
  material.

The original handoff was `/data/workspace/symbol-locator-handoff-20260823-065432/symbol-locator-handoff/`.
The verified repair project root was discovered as the unique valid candidate
at `/data/workspace/symbol-locator-repair-20260826-083933/source_copy/source_copy/SWE-Explore-Bench/`.
The final protected-path check reported `NO_DIFFERENCES`.

## Experiment configuration

The experiment used the same canonical 20-instance IntentQ20/narrow20 order
for all faces, `gpt-5.4-mini`, Agent/scorer model
`openai/gpt-5.4-mini`, temperature `1`, top-k `5`, `workers=1`, and a 3600
second case timeout. A faces explicitly unset `SYMBOL_LOCATOR_ENABLED`; B
faces set it to `1`. API credentials were injected only through the runtime
environment and are not present here. The validated `--repos` root was the
project root itself, never its `repos/` child.

## Results

All four faces contain 20 unique, ordered IDs with complete finite metrics.
Noise metrics are lower-is-better.

| metric | A1 | B1 | B1-A1 | A2 | B2 | B2-A2 |
|---|---:|---:|---:|---:|---:|---:|
| precision | 0.308595 | 0.261655 | -0.046939 | 0.285058 | 0.395574 | 0.110516 |
| recall | 0.446951 | 0.439346 | -0.007605 | 0.478382 | 0.560267 | 0.081886 |
| F1 | 0.331544 | 0.295767 | -0.035777 | 0.331131 | 0.409896 | 0.078765 |
| hit_file | 0.451667 | 0.621667 | 0.170000 | 0.497500 | 0.663333 | 0.165833 |
| hit_region | 0.342500 | 0.481667 | 0.139167 | 0.409167 | 0.568333 | 0.159167 |
| noise_file | 0.437500 | 0.316667 | -0.120833 | 0.412500 | 0.382500 | -0.030000 |
| noise_region | 0.428333 | 0.285000 | -0.143333 | 0.404167 | 0.348333 | -0.055833 |
| WCC | 0.272745 | 0.334941 | 0.062196 | 0.314778 | 0.419379 | 0.104601 |
| context efficiency | 0.547649 | 0.582949 | 0.035300 | 0.504394 | 0.687669 | 0.183275 |

### Two-round mean and spread

| metric | A mean | A stddev | B mean | B stddev |
|---|---:|---:|---:|---:|
| precision | 0.296827 | 0.016643 | 0.328615 | 0.094695 |
| recall | 0.462666 | 0.022225 | 0.499807 | 0.085504 |
| F1 | 0.331337 | 0.000292 | 0.352831 | 0.080701 |
| hit_file | 0.474583 | 0.032409 | 0.642500 | 0.029463 |
| hit_region | 0.375833 | 0.047140 | 0.525000 | 0.061283 |
| noise_file | 0.425000 | 0.017678 | 0.349583 | 0.046551 |
| noise_region | 0.416250 | 0.017088 | 0.316667 | 0.044783 |
| WCC | 0.293761 | 0.029721 | 0.377160 | 0.059707 |
| context efficiency | 0.526022 | 0.030586 | 0.635309 | 0.074048 |

The full per-face min/max values and the preserved same-20 reference
difference table are in the [comparison report](experiments/intentq20_x2_resilient_20260827-014906/diagnostics/intentq20_x2_comparison.md).

## Takeover and recovery

The SSH takeover found the original tmux/supervisor active. It later stopped
on a false-positive B1 Pyright validator condition. Process evidence and the
existing successful first B1 result proved this was an orchestration defect,
not a source or data failure. A controlled resume used the same run root,
validated the existing `django__django-11206` result, and ran only missing
IDs. The resume preserved successful JSONL rows and used the corrected
line-oriented output validator.

There were transient API ReadTimeout, HTTP 502, and HTTP 503 canary events;
the bounded recovery window recovered them. The final run had zero case
execution failures, zero case timeouts, and zero per-case recovery attempts.
The [recovery log](experiments/intentq20_x2_resilient_20260827-014906/diagnostics/resume_and_recovery_log.md)
and [canary log](experiments/intentq20_x2_resilient_20260827-014906/diagnostics/api_canary_attempts.jsonl)
contain the redacted timeline.

## Reproduction and review

The published results can be inspected without credentials:

- [A1 output](experiments/intentq20_x2_resilient_20260827-014906/faces/A1/output/locagent_top5.jsonl)
- [B1 output](experiments/intentq20_x2_resilient_20260827-014906/faces/B1/output/locagent_top5.jsonl)
- [A2 output](experiments/intentq20_x2_resilient_20260827-014906/faces/A2/output/locagent_top5.jsonl)
- [B2 output](experiments/intentq20_x2_resilient_20260827-014906/faces/B2/output/locagent_top5.jsonl)
- [A1/B1/A2/B2 reports](experiments/intentq20_x2_resilient_20260827-014906/diagnostics/)
- [final report](experiments/intentq20_x2_resilient_20260827-014906/diagnostics/final_report.md)

To reproduce in an environment that has the benchmark repositories restored,
inject credentials through a secret manager and keep them out of the command
line. The validated command shape is:

```bash
PROJECT_ROOT=/path/to/SWE-Explore-Bench-with-repos
python "$PROJECT_ROOT/eval_runner.py" \
  --bench "$PWD/experiments/intentq20_x2_resilient_20260827-014906/data/intentq20.jsonl" \
  --repos "$PROJECT_ROOT" \
  --issue-map "$PWD/experiments/intentq20_x2_resilient_20260827-014906/data/issue_map.json" \
  --explorers locagent --top-k 5 --workers 1 \
  --academic-model openai/gpt-5.4-mini --academic-timeout 3600
```

Run A with `SYMBOL_LOCATOR_ENABLED` unset and B with it set to `1`. The
published run used a `locagent` environment with Pyright and its dependencies;
that environment is intentionally not published. Trace directories are
organized by face and instance, and each output row can be selected by
`instance_id` with a JSONL-aware tool.

## Privacy and limitations

No `.env`, `.secrets`, API key, authorization header, cookie, authenticated
URL, dependency environment, benchmark repo clone, cache, or temporary socket
is published. The local settings that triggered a credential pattern were
excluded without reading their contents. See [privacy exclusions](PRIVACY_EXCLUSIONS.md)
and the [artifact manifest](ARTIFACT_MANIFEST.md) for exact accounting.
