#!/bin/bash
# 1-instance pilot from IntentQ20 (django__django-15572) to verify issue_map plumbing.
#   ./run_intentq20_pilot.sh          → A vanilla
#   SYMBOL_LOCATOR_ENABLED=1 ./run_intentq20_pilot.sh   → B with symbol-locator
set -euo pipefail
cd "$(dirname "$0")"

set -a
source .env
set +a

MODE=$([ "${SYMBOL_LOCATOR_ENABLED:-}" = "1" ] && echo "B_symloc" || echo "A_vanilla")
OUT="results/intentq20_pilot_${MODE}"
rm -rf "$OUT"; mkdir -p "$OUT"
echo "=== MODE=$MODE  model=$LLM_MODEL  out=$OUT ==="

BENCH=/tmp/bench.intentq20_pilot.jsonl
ISSUE="${HANDOFF}/symbol-locator-locagent/issue_map_narrow20.json"

exec "${CONDA_EXE}" run -n locagent --no-capture-output python eval_runner.py \
  --bench "$BENCH" \
  --repos . \
  --issue-map "$ISSUE" \
  --explorers locagent \
  --top-k 5 \
  --output "$OUT/{explorer}_top{k}.jsonl" \
  --academic-api-base "$LLM_API_BASE" \
  --academic-api-key "$LLM_API_KEY" \
  --academic-model "openai/$LLM_MODEL"
