#!/bin/bash
# Smoke-run LocAgent on 3 narrow-GT Python bench records. A/B switch = SYMBOL_LOCATOR_ENABLED.
#   ./run_smoke3.sh          → A 面 (原版 LocAgent)
#   SYMBOL_LOCATOR_ENABLED=1 ./run_smoke3.sh   → B 面 (加插件)
set -euo pipefail
cd "$(dirname "$0")"

set -a
source .env
set +a

MODE=$([ "${SYMBOL_LOCATOR_ENABLED:-}" = "1" ] && echo "B_symloc" || echo "A_vanilla")
OUT="results/smoke3_${MODE}"
mkdir -p "$OUT"
echo "=== MODE=$MODE  model=$LLM_MODEL  out=$OUT ==="

# 从 handoff 根目录派生 (.env 里的 HANDOFF)
BENCH="${HANDOFF}/symbol-locator-locagent/bench.smoke3.jsonl"
ISSUE="${HANDOFF}/symbol-locator-locagent/issue_map_smoke3.json"

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
