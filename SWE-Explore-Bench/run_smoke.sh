#!/bin/bash
# Smoke-run LocAgent on one bench record. A/B switch = SYMBOL_LOCATOR_ENABLED.
#   ./run_smoke.sh          → A 面 (原版 LocAgent)
#   SYMBOL_LOCATOR_ENABLED=1 ./run_smoke.sh   → B 面 (加插件)
set -euo pipefail
cd "$(dirname "$0")"

# 加载 .env (export 所有变量,让 conda run 子进程也拿到)
set -a
source .env
set +a

MODE=$([ "${SYMBOL_LOCATOR_ENABLED:-}" = "1" ] && echo "B_symloc" || echo "A_vanilla")
OUT="results/smoke_${MODE}"
mkdir -p "$OUT"
echo "=== MODE=$MODE  model=$LLM_MODEL  out=$OUT ==="

exec "${CONDA_EXE}" run -n locagent --no-capture-output python eval_runner.py \
  --bench bench.smoke.jsonl \
  --repos . \
  --issue-map issue_map.json \
  --explorers locagent \
  --top-k 5 \
  --output "$OUT/{explorer}_top{k}.jsonl" \
  --academic-api-base "$LLM_API_BASE" \
  --academic-api-key "$LLM_API_KEY" \
  --academic-model "openai/$LLM_MODEL" \
  --limit 1
