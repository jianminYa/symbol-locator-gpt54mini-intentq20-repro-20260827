"""install_into_locagent() — monkey-patch LocAgent to expose our tools.

Must run AFTER `sys.path` includes LocAgent root, but BEFORE `auto_search_main.py`
runs. The _locagent_shim.py in SWE-Explore-Bench is the right place to call
this — it already does path setup + other patches, and it calls `runpy.run_path`
on `auto_search_main.py` at the very end.

Four patch points:
1. `plugins.location_tools.repo_ops.repo_ops` — add our functions to the module
   and extend `__all__` so `import_functions(...)` copies them out.
2. `util.runtime.function_calling.ALL_FUNCTIONS` — extend the whitelist so
   `response_to_actions()` doesn't raise on our tool names.
3. `util.runtime.function_calling.get_tools()` — wrap to append our
   ChatCompletionToolParam objects, so the LLM SEES the tools.
4. `util.runtime.execute_ipython.execute_ipython` — inject our functions
   into IPython's `user_ns` so the `print(func(**args))` code can call them.
"""
from __future__ import annotations

import os
import sys

from . import core

# List of tool names we own. Order matters only for stable printing.
_OUR_TOOL_NAMES = ["find_symbol", "more_symbols", "reset_symbols"]


def _build_tool_specs():
    """Build the ChatCompletionToolParam entries LocAgent's function-calling
    path expects. Kept inside a function so litellm is imported lazily —
    lets `install.py` be imported in test contexts without litellm."""
    from litellm import (
        ChatCompletionToolParam,
        ChatCompletionToolParamFunctionChunk,
    )

    find_symbol_spec = ChatCompletionToolParam(
        type="function",
        function=ChatCompletionToolParamFunctionChunk(
            name="find_symbol",
            description=(
                "Locate any Python symbol (class, function, method, variable) by name. "
                "ALWAYS call this FIRST before grep or file read when you need to find "
                "where something is defined. Returns LSP-precise file:line locations with "
                "source snippets and relevance scores. Substring matching supported — "
                "you don't need the exact name. `context` improves ranking. "
                "If more candidates exist, call `more_symbols`."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Symbol to find (class, function, method, or variable name).",
                    },
                    "context": {
                        "type": "string",
                        "description": "What you are trying to do — improves ranking. e.g. 'fixing form save error'.",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "How many top candidates to return.",
                        "default": 3,
                        "minimum": 1,
                        "maximum": 10,
                    },
                    "kind_filter": {
                        "type": ["array", "null"],
                        "items": {"type": "integer"},
                        "description": "Only these LSP SymbolKinds. 5=class, 6=method, 12=function, 13=variable.",
                        "default": None,
                    },
                },
                "required": ["name"],
            },
        ),
    )

    more_symbols_spec = ChatCompletionToolParam(
        type="function",
        function=ChatCompletionToolParamFunctionChunk(
            name="more_symbols",
            description=(
                "Return the next batch of candidates from a previous `find_symbol` call. "
                "Use when the first batch didn't contain the symbol you needed."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Same name as passed to find_symbol."},
                    "top_k": {"type": "integer", "default": 3, "minimum": 1, "maximum": 10},
                    "context": {"type": "string", "description": "Same context as passed to find_symbol."},
                },
                "required": ["name"],
            },
        ),
    )

    reset_symbols_spec = ChatCompletionToolParam(
        type="function",
        function=ChatCompletionToolParamFunctionChunk(
            name="reset_symbols",
            description="Clear the symbol-locator candidate cache. Use if the workspace changed on disk.",
            parameters={"type": "object", "properties": {}},
        ),
    )

    return [find_symbol_spec, more_symbols_spec, reset_symbols_spec]


def install_into_locagent(warmup: bool = True) -> None:
    """Wire our tools into LocAgent. Idempotent.
    warmup= kept for API-compat; actual warmup runs in the set_current_issue
    hook once we know the real repo dir."""
    _patch_repo_ops()
    _patch_function_calling()
    _patch_execute_ipython()
    _register_scorer_usage_dump()


def _register_scorer_usage_dump() -> None:
    """No-op: scorer.score_batch writes SYMBOL_LOCATOR_USAGE_OUT synchronously
    after every call. atexit is unreliable across LocAgent's fork children
    (os._exit path), and firing pop_usage() in a non-accumulating child would
    clobber the sidecar with zeros. Keeping the function so callers don't need
    to change."""
    return


def _patch_repo_ops() -> None:
    """Inject find_symbol/more_symbols/reset_symbols into
    plugins.location_tools.repo_ops.repo_ops and extend __all__."""
    import plugins.location_tools.repo_ops.repo_ops as repo_ops_mod

    if getattr(repo_ops_mod, "_symbol_locator_installed", False):
        return

    for fname in _OUR_TOOL_NAMES:
        setattr(repo_ops_mod, fname, getattr(core, fname))

    existing = list(repo_ops_mod.__all__)
    for fname in _OUR_TOOL_NAMES:
        if fname not in existing:
            existing.append(fname)
    repo_ops_mod.__all__ = existing
    repo_ops_mod._symbol_locator_installed = True

    # Also touch the parent package's globals — plugins.location_tools.repo_ops
    # re-runs import_functions on module import, but if it was imported BEFORE
    # our patch, its cached copies won't include us. Fix that here.
    try:
        import plugins.location_tools.repo_ops as parent_pkg
        for fname in _OUR_TOOL_NAMES:
            setattr(parent_pkg, fname, getattr(core, fname))
        if hasattr(parent_pkg, "__all__"):
            for fname in _OUR_TOOL_NAMES:
                if fname not in parent_pkg.__all__:
                    parent_pkg.__all__.append(fname)
    except ImportError:
        pass

    # Same for the top aggregator plugins.location_tools if already imported.
    try:
        import plugins.location_tools as top_pkg
        for fname in _OUR_TOOL_NAMES:
            setattr(top_pkg, fname, getattr(core, fname))
    except ImportError:
        pass

    # And the specific `locationtools` submodule which builds DOCUMENTATION
    try:
        import plugins.location_tools.locationtools as loc_mod
        for fname in _OUR_TOOL_NAMES:
            setattr(loc_mod, fname, getattr(core, fname))
        if hasattr(loc_mod, "__all__"):
            for fname in _OUR_TOOL_NAMES:
                if fname not in loc_mod.__all__:
                    loc_mod.__all__.append(fname)
        # Rebuild DOCUMENTATION string so the (non-function-calling) prompt
        # includes our tools too.
        _rebuild_documentation(loc_mod)
    except ImportError:
        pass

    # Single source of truth for workspace = whatever setup_repo returns.
    # Cold path: wrap setup_repo, capture its return value (the real repo_dir).
    # Hot path (graph_index cached, setup_repo skipped): fall back to LOCAL_REPO_PATH
    # — the shim's Patch 6 already treats it as the same repo (would symlink to it).
    import util.benchmark.setup_repo as _sr_mod
    _orig_setup_repo = _sr_mod.setup_repo

    import logging
    _log = logging.getLogger("symbol_locator")

    def _emit(msg: str) -> None:
        _log.info(msg)
        sys.stderr.write(f"[symbol-locator] {msg}\n")
        sys.stderr.flush()

    def _resolve_local_repo():
        """Multi-candidate LOCAL_REPO_PATH → abs dir; eval_runner sets it
        relative to ITS cwd but LocAgent chdir'd elsewhere."""
        local = os.environ.get("LOCAL_REPO_PATH")
        if not local:
            return None, []
        candidates = [local, os.path.abspath(local)]
        oldpwd = os.environ.get("OLDPWD")
        if oldpwd:
            candidates.append(os.path.join(oldpwd, local))
        lroot = os.environ.get("LOCAGENT_ROOT", "")
        for up in range(1, 4):
            candidates.append(os.path.join(os.path.abspath(os.path.join(lroot, *([".."] * up))), local))
        resolved = next((os.path.abspath(c) for c in candidates if c and os.path.isdir(c)), None)
        return resolved, candidates

    def _patched_setup_repo(*args, **kwargs):
        repo_dir = _orig_setup_repo(*args, **kwargs)
        try:
            # Shim Patch 6 makes setup_repo return a symlink whose target is a
            # RELATIVE path (LOCAL_REPO_PATH from eval_runner's cwd). realpath
            # resolves from the symlink's parent → dangling. Prefer resolving
            # LOCAL_REPO_PATH ourselves against LOCAGENT_ROOT/OLDPWD.
            abs_dir = os.path.realpath(repo_dir)
            if not os.path.isdir(abs_dir):
                fallback, tried = _resolve_local_repo()
                if fallback:
                    _emit(f"setup_repo returned dangling {repo_dir}; using LOCAL_REPO_PATH -> {fallback}")
                    abs_dir = fallback
                else:
                    _emit(f"setup_repo returned dangling {repo_dir}; no fallback (tried: {tried})")
            else:
                _emit(f"setup_repo returned: {repo_dir} -> {abs_dir}")
            core.set_active_workspace(abs_dir)
            if os.environ.get("SYMBOL_LOCATOR_SKIP_WARMUP") != "1":
                _emit(core.warmup_workspace(abs_dir))
        except Exception as e:
            import traceback
            _emit(f"WARN setup_repo hook failed: {e}\n{traceback.format_exc()}")
        return repo_dir

    _sr_mod.setup_repo = _patched_setup_repo
    # repo_ops imports `setup_repo` by name at module load — patch that binding too.
    if hasattr(repo_ops_mod, "setup_repo"):
        repo_ops_mod.setup_repo = _patched_setup_repo

    # Wrap set_current_issue only for the hot path (graph_index cached →
    # setup_repo never runs → hook above never fires). Use LOCAL_REPO_PATH.
    _orig_set = repo_ops_mod.set_current_issue

    def _patched_set_current_issue(*args, **kwargs):
        _emit("hook: set_current_issue fired")
        # Snapshot BEFORE _orig_set — else build_graph writes the pkl during
        # this call and cold path gets misdetected as hot.
        iid_hint = None
        if kwargs.get("instance_data"):
            iid_hint = kwargs["instance_data"].get("instance_id")
        elif kwargs.get("instance_id"):
            iid_hint = kwargs["instance_id"]
        elif args:
            first = args[0]
            if isinstance(first, str):
                iid_hint = first
            elif isinstance(first, dict):
                iid_hint = first.get("instance_id")
        graph_index_dir = getattr(repo_ops_mod, "GRAPH_INDEX_DIR", None)
        graph_pkl = os.path.join(graph_index_dir or "", f"{iid_hint}.pkl") if (graph_index_dir and iid_hint) else ""
        hot_path = bool(graph_pkl) and os.path.exists(graph_pkl)
        result = _orig_set(*args, **kwargs)
        try:
            # On cold path _patched_setup_repo already set the workspace; nothing to do.
            if hot_path:
                resolved, tried = _resolve_local_repo()
                if resolved:
                    _emit(f"hot path (graph_index cached); using LOCAL_REPO_PATH -> {resolved}")
                    core.set_active_workspace(resolved)
                    if os.environ.get("SYMBOL_LOCATOR_SKIP_WARMUP") != "1":
                        _emit(core.warmup_workspace(resolved))
                else:
                    _emit(f"WARN hot path but no LOCAL_REPO_PATH; tried: {tried}")
        except Exception as e:
            import traceback
            _emit(f"WARN set_current_issue hook failed: {e}\n{traceback.format_exc()}")
        return result

    repo_ops_mod.set_current_issue = _patched_set_current_issue


def _rebuild_documentation(loc_mod) -> None:
    from inspect import signature
    doc = ""
    for fname in loc_mod.__all__:
        func = getattr(loc_mod, fname, None)
        if func is None or not callable(func) or not func.__doc__:
            continue
        cur_doc = "\n".join(filter(None, (line.strip() for line in func.__doc__.split("\n"))))
        cur_doc = "\n".join(" " * 4 + line for line in cur_doc.split("\n"))
        sig = f"{func.__name__}{signature(func)}"
        doc += f"{sig}:\n{cur_doc}\n\n"
    loc_mod.DOCUMENTATION = doc


def _patch_function_calling() -> None:
    """Extend ALL_FUNCTIONS whitelist + wrap get_tools() so our specs go to LLM."""
    import util.runtime.function_calling as fc

    if getattr(fc, "_symbol_locator_installed", False):
        return

    for fname in _OUR_TOOL_NAMES:
        if fname not in fc.ALL_FUNCTIONS:
            fc.ALL_FUNCTIONS.append(fname)

    _our_specs = _build_tool_specs()
    _orig_get_tools = fc.get_tools

    def _patched_get_tools(*args, **kwargs):
        tools = list(_orig_get_tools(*args, **kwargs))
        tools.extend(_our_specs)
        return tools

    fc.get_tools = _patched_get_tools
    fc._symbol_locator_installed = True


def _patch_execute_ipython() -> None:
    """Wrap execute_ipython so our functions are in IPython's user_ns.

    LocAgent's execute_ipython() rebuilds user_ns on every call. We wrap the
    function (not the shell) so the injection happens on every call, matching
    the existing pattern in the file.
    """
    import util.runtime.execute_ipython as ei

    if getattr(ei, "_symbol_locator_installed", False):
        return

    _orig = ei.execute_ipython

    def _patched(code_to_execute):
        # Ensure the IPython instance has our fns in scope BEFORE the code runs.
        from IPython.terminal.interactiveshell import TerminalInteractiveShell
        shell = TerminalInteractiveShell.instance()
        for fname in _OUR_TOOL_NAMES:
            shell.user_ns[fname] = getattr(core, fname)
        return _orig(code_to_execute)

    ei.execute_ipython = _patched
    ei._symbol_locator_installed = True


def demo() -> None:
    """Basic import sanity — no LocAgent needed."""
    # Just confirm the tool specs build without litellm import blowing up
    # when litellm IS available.
    try:
        specs = _build_tool_specs()
        assert len(specs) == 3
        names = [s["function"]["name"] for s in specs]
        assert names == _OUR_TOOL_NAMES
        print("install demo OK")
    except ImportError:
        print("install demo SKIPPED (litellm missing)")


if __name__ == "__main__":
    demo()
