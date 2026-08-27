# Repair notes

These notes summarize the actual Commit 1 → Commit 2 source diff in
`experiments/intentq20_x2_resilient_20260827-014906/diagnostics/repair_commit1_to_commit2.diff`.
The original recorded patch is preserved beside it as
`repair_source_patch.diff`. The 13-file saved key-hash check passed 13/13.

## File-by-file changes

- `SWE-Explore-Bench/explorers/_locagent_shim.py`: replaces an existing,
  dangling, directory, or file repo link safely before installing the local
  repository symlink, preventing stale-link failures.
- `SWE-Explore-Bench/explorers/subprocess_utils.py`: stops propagating captured
  child stdout/stderr on timeout or nonzero exit, preventing provider response
  text from being copied into exception logs.
- `SWE-Explore-Bench/third_party/LocAgent/auto_search_main.py`: adds bounded
  agent iteration, child-process supervision, structured response-safe errors,
  exact child cleanup, retry classification, Pyright prepare/reset hooks, and
  metadata-only response/final-output logging. It also rejects an exhausted
  agent loop rather than silently producing an incomplete result.
- `SWE-Explore-Bench/third_party/LocAgent/util/actions/action_parser.py`:
  preserves structured tool-call parse failures instead of silently treating
  malformed calls as ordinary prose.
- `SWE-Explore-Bench/third_party/LocAgent/util/runtime/finish.py`: defines a
  structured finish schema requiring a non-empty `locations` string containing
  ordered file-qualified candidates.
- `SWE-Explore-Bench/third_party/LocAgent/util/runtime/function_calling.py`:
  validates JSON/object tool arguments, enforces the finish contract, avoids
  adding ordinary thought text to a finish action, and uses a dedicated
  `FinishContractError`.
- `SWE-Explore-Bench/third_party/LocAgent/util/runtime/process_control.py`:
  adds bounded child waiting/termination and response-safe error metadata that
  carries categories and traceback coordinates without exception text.
- `symbol-locator-locagent/symbol_locator/core.py`: tracks Pyright client
  ownership by PID, closes parent clients before fork, resets inherited state
  in children, clears the candidate cache after fork, and raises on failed
  warmup.
- `symbol-locator-locagent/symbol_locator/lsp.py`: safely drops inherited LSP
  descriptors after fork and marks a workspace warmed only when all warmup
  files succeed.
- `symbol-locator-locagent/symbol_locator/scorer.py`: logs only exception type
  on scorer failure instead of provider exception text.

## Validation recorded before publication

The repair tree was used by the completed run with `gpt-5.4-mini`,
`openai/gpt-5.4-mini`, temperature 1, top-k 5, workers 1, and a 3600-second
case timeout. Existing no-API static/control-flow tests passed before the
controlled resume. The final run produced four valid 20/20 JSONL outputs and
the B1 child-owned Pyright/find-symbol validation passed.
