# symbol-locator-locagent

Python adapter that exposes the symbol-locator (LSP-backed workspace symbol
search + LLM re-ranking) as three tools LocAgent can call:

- `find_symbol(name, context?, top_k=3, kind_filter?)`
- `more_symbols(name, top_k=3, context?)`
- `reset_symbols()`

## How the wiring works

LocAgent's tool registry lives in three places; `install_into_locagent()`
patches all four points at once:

1. `plugins.location_tools.repo_ops.repo_ops.__all__` — append our names +
   attach our functions to the module (drives IPython injection +
   DOCUMENTATION-string prompt).
2. `util.runtime.function_calling.ALL_FUNCTIONS` — extend the whitelist so
   `response_to_actions()` accepts tool calls with our names.
3. `util.runtime.function_calling.get_tools()` — wrap to append our
   `ChatCompletionToolParam` specs, so the LLM SEES the tools in its schema.
4. `util.runtime.execute_ipython.execute_ipython` — inject into IPython's
   `user_ns` so `print(func(**args))` code can call our fns.

## Prerequisites

- Python 3.10+
- `pyright-langserver` on PATH (either `pip install pyright` or `npm i -g pyright`)
- `litellm` (LocAgent already depends on it — reused for scorer)

## Enable in the SWE-Explore-Bench LocAgent run

The shim already looks for these env vars:

```bash
export SYMBOL_LOCATOR_ENABLED=1
export SYMBOL_LOCATOR_PATH=/data/workspace/orcaloca_openclaw/symbol-locator-locagent
# Optional: override the scorer model (default: openai/gpt-4o-mini)
export SYMBOL_LOCATOR_SCORER_MODEL=openai/gpt-4o
# Optional: workspace override (default: LOCAL_REPO_PATH → cwd)
# export SYMBOL_LOCATOR_WORKSPACE=/repo
# Optional: raise if pyright takes long to index a big repo (default 180s)
# export SYMBOL_LOCATOR_LSP_INIT_TIMEOUT_S=300
```

Without `SYMBOL_LOCATOR_ENABLED=1` the shim is a no-op — plain LocAgent runs.
That's the A/B switch.

## Self-check (no LSP or LLM needed)

```bash
cd /data/workspace/orcaloca_openclaw/symbol-locator-locagent
python -m symbol_locator.cache
python -m symbol_locator.scorer
python -m symbol_locator.core
python -m symbol_locator.install    # skipped if litellm missing
```

skipped: unit-test framework, add when pytest ends up on the critical path.
