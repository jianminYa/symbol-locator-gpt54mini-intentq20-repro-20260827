# Command preflight (redacted)

- PROJECT_ROOT: `/data/workspace/symbol-locator-repair-20260826-083933/experiments/temperature_trace_pathfix_20260827-232041/source/SWE-Explore-Bench`
- LOCAGENT_PATH: `/data/workspace/symbol-locator-repair-20260826-083933/experiments/temperature_trace_pathfix_20260827-232041/source/SWE-Explore-Bench/third_party/LocAgent`
- SYMBOL_LOCATOR_PATH: `/data/workspace/symbol-locator-repair-20260826-083933/experiments/temperature_trace_pathfix_20260827-232041/source/symbol-locator-locagent`
- `--repos` is exactly PROJECT_ROOT, never PROJECT_ROOT/repos.
- `--academic-api-key` is absent. API credentials are environment-only.
- child environment exports absolute CONDA_EXE and LOCAGENT_PATH; TMPDIR uses a short alias whose resolved target is the per-attempt RUN_ROOT tmp directory.
- model: `openai/gpt-5.4-mini`; scorer model env: `openai/gpt-5.4-mini`; `LOCAGENT_TEMPERATURE=0.0`; top-k: `5`; workers: `1`
- output/cache/tmp paths are per-face and under RUN_ROOT.

## A1

```text
python eval_runner.py --bench /data/workspace/symbol-locator-repair-20260826-083933/experiments/temperature_trace_pathfix_20260827-232041/t0_x2_fresh/attempt_1/data/intentq20.jsonl --repos /data/workspace/symbol-locator-repair-20260826-083933/experiments/temperature_trace_pathfix_20260827-232041/source/SWE-Explore-Bench --issue-map /data/workspace/symbol-locator-repair-20260826-083933/experiments/temperature_trace_pathfix_20260827-232041/t0_x2_fresh/attempt_1/data/issue_map.json --explorers locagent --top-k 5 --workers 1 --academic-model openai/gpt-5.4-mini --academic-timeout 3600 --no-skip-empty-core --resume --output /data/workspace/symbol-locator-repair-20260826-083933/experiments/temperature_trace_pathfix_20260827-232041/t0_x2_fresh/attempt_1/faces/A1/output/locagent_top5.jsonl
```
- tmp: `/data/workspace/symbol-locator-repair-20260826-083933/experiments/temperature_trace_pathfix_20260827-232041/t0_x2_fresh/attempt_1/tmp/A1/attempt_<iid>_<n>`
- cache: `/data/workspace/symbol-locator-repair-20260826-083933/experiments/temperature_trace_pathfix_20260827-232041/t0_x2_fresh/attempt_1/cache/A1/attempt_<iid>_<n>`

## B1

```text
python eval_runner.py --bench /data/workspace/symbol-locator-repair-20260826-083933/experiments/temperature_trace_pathfix_20260827-232041/t0_x2_fresh/attempt_1/data/intentq20.jsonl --repos /data/workspace/symbol-locator-repair-20260826-083933/experiments/temperature_trace_pathfix_20260827-232041/source/SWE-Explore-Bench --issue-map /data/workspace/symbol-locator-repair-20260826-083933/experiments/temperature_trace_pathfix_20260827-232041/t0_x2_fresh/attempt_1/data/issue_map.json --explorers locagent --top-k 5 --workers 1 --academic-model openai/gpt-5.4-mini --academic-timeout 3600 --no-skip-empty-core --resume --output /data/workspace/symbol-locator-repair-20260826-083933/experiments/temperature_trace_pathfix_20260827-232041/t0_x2_fresh/attempt_1/faces/B1/output/locagent_top5.jsonl
```
- tmp: `/data/workspace/symbol-locator-repair-20260826-083933/experiments/temperature_trace_pathfix_20260827-232041/t0_x2_fresh/attempt_1/tmp/B1/attempt_<iid>_<n>`
- cache: `/data/workspace/symbol-locator-repair-20260826-083933/experiments/temperature_trace_pathfix_20260827-232041/t0_x2_fresh/attempt_1/cache/B1/attempt_<iid>_<n>`

## A2

```text
python eval_runner.py --bench /data/workspace/symbol-locator-repair-20260826-083933/experiments/temperature_trace_pathfix_20260827-232041/t0_x2_fresh/attempt_1/data/intentq20.jsonl --repos /data/workspace/symbol-locator-repair-20260826-083933/experiments/temperature_trace_pathfix_20260827-232041/source/SWE-Explore-Bench --issue-map /data/workspace/symbol-locator-repair-20260826-083933/experiments/temperature_trace_pathfix_20260827-232041/t0_x2_fresh/attempt_1/data/issue_map.json --explorers locagent --top-k 5 --workers 1 --academic-model openai/gpt-5.4-mini --academic-timeout 3600 --no-skip-empty-core --resume --output /data/workspace/symbol-locator-repair-20260826-083933/experiments/temperature_trace_pathfix_20260827-232041/t0_x2_fresh/attempt_1/faces/A2/output/locagent_top5.jsonl
```
- tmp: `/data/workspace/symbol-locator-repair-20260826-083933/experiments/temperature_trace_pathfix_20260827-232041/t0_x2_fresh/attempt_1/tmp/A2/attempt_<iid>_<n>`
- cache: `/data/workspace/symbol-locator-repair-20260826-083933/experiments/temperature_trace_pathfix_20260827-232041/t0_x2_fresh/attempt_1/cache/A2/attempt_<iid>_<n>`

## B2

```text
python eval_runner.py --bench /data/workspace/symbol-locator-repair-20260826-083933/experiments/temperature_trace_pathfix_20260827-232041/t0_x2_fresh/attempt_1/data/intentq20.jsonl --repos /data/workspace/symbol-locator-repair-20260826-083933/experiments/temperature_trace_pathfix_20260827-232041/source/SWE-Explore-Bench --issue-map /data/workspace/symbol-locator-repair-20260826-083933/experiments/temperature_trace_pathfix_20260827-232041/t0_x2_fresh/attempt_1/data/issue_map.json --explorers locagent --top-k 5 --workers 1 --academic-model openai/gpt-5.4-mini --academic-timeout 3600 --no-skip-empty-core --resume --output /data/workspace/symbol-locator-repair-20260826-083933/experiments/temperature_trace_pathfix_20260827-232041/t0_x2_fresh/attempt_1/faces/B2/output/locagent_top5.jsonl
```
- tmp: `/data/workspace/symbol-locator-repair-20260826-083933/experiments/temperature_trace_pathfix_20260827-232041/t0_x2_fresh/attempt_1/tmp/B2/attempt_<iid>_<n>`
- cache: `/data/workspace/symbol-locator-repair-20260826-083933/experiments/temperature_trace_pathfix_20260827-232041/t0_x2_fresh/attempt_1/cache/B2/attempt_<iid>_<n>`
