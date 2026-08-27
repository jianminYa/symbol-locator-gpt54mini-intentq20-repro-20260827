#!/bin/bash
# IntentQ20 full A/B, TWO runs each, results in isolated dirs (no overwrite).
# Sequential: A_run1 → A_run2 → B_run1 → B_run2. Est ~5-6 hrs.
set -euo pipefail
cd "$(dirname "$0")"

set -a
source .env
set +a

BENCH="${HANDOFF}/symbol-locator-locagent/bench.narrow20.jsonl"
ISSUE="${HANDOFF}/symbol-locator-locagent/issue_map_narrow20.json"
LOG_ROOT=results/intentq20_x2_logs
mkdir -p "$LOG_ROOT"

run_one() {
  local mode="$1" run_idx="$2" enable="$3"
  local out="results/intentq20_${mode}_run${run_idx}"
  local log="$LOG_ROOT/${mode}_run${run_idx}.log"
  # Never overwrite: refuse if dir already has records
  if [ -d "$out" ] && [ -n "$(ls -A "$out" 2>/dev/null)" ]; then
    echo "REFUSE: $out already has files — aborting to avoid contamination" >&2
    exit 2
  fi
  rm -rf "$out"; mkdir -p "$out"
  echo "=== [$(date '+%F %T')] START mode=$mode run=$run_idx out=$out log=$log ==="
  SYMBOL_LOCATOR_ENABLED="$enable" \
  "${CONDA_EXE}" run -n locagent --no-capture-output python eval_runner.py \
    --bench "$BENCH" \
    --repos . \
    --issue-map "$ISSUE" \
    --explorers locagent \
    --top-k 5 \
    --output "$out/{explorer}_top{k}.jsonl" \
    --academic-api-base "$LLM_API_BASE" \
    --academic-api-key "$LLM_API_KEY" \
    --academic-model "openai/$LLM_MODEL" \
    > "$log" 2>&1
  echo "=== [$(date '+%F %T')] DONE  mode=$mode run=$run_idx ==="
}

# A twice, B twice — sequential to avoid tmpdir/index-cache races
run_one A_vanilla 1 ""
run_one A_vanilla 2 ""
run_one B_symloc  1 "1"
run_one B_symloc  2 "1"

echo "=== ALL 4 RUNS COMPLETE $(date '+%F %T') ==="
