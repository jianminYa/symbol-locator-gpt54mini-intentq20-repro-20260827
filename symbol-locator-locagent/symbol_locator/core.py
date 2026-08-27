"""find_symbol / more_symbols / reset_symbols — the tools LocAgent calls.

LocAgent picks these up two ways:
1. Via `import_functions(module=repo_ops, function_names=[...], target_globals=...)`
   which just copies them into `plugins.location_tools` and `plugins.location_tools.repo_ops`
   namespaces. Then locationtools.py builds a DOCUMENTATION string from
   `signature()` and `__doc__` — so signatures and docstrings ARE the tool spec.
2. Via `util/runtime/function_calling.py::get_tools()` — that's a hardcoded whitelist
   we have to extend. See install.py.

The functions are plain Python. They print / return strings so IPython
executes them correctly (the LocAgent tool runtime does `print(func(**args))`
inside IPython).
"""
from __future__ import annotations

import os
import sys
import threading
from typing import Optional

from .cache import CandidateCache, ScoredSymbol
from .lsp import PyrightClient
from .rank import rank_candidates
from .scorer import score_batch

# ── globals: singletons per process ────────────────────────────────────
# One pyright client per workspace path. LocAgent runs single-process (we
# force num_processes=1 via the shim), so a plain dict is fine.
_CLIENTS: dict[str, PyrightClient] = {}
_CLIENTS_LOCK = threading.Lock()

_CACHE = CandidateCache(
    max_entries=int(os.environ.get("SYMBOL_LOCATOR_CACHE_MAX_ENTRIES", "512")),
    ttl_s=float(os.environ.get("SYMBOL_LOCATOR_CACHE_TTL_S", "3600")),
)

SCORE_WINDOW = 25
DEFAULT_SNIPPET_LINES = 15


def _log(msg: str) -> None:
    # Everything to stderr so it doesn't corrupt tool output that LLMs read
    sys.stderr.write(f"[symbol-locator] {msg}\n")


def _get_client(workspace: str) -> PyrightClient:
    workspace = os.path.abspath(workspace)
    with _CLIENTS_LOCK:
        c = _CLIENTS.get(workspace)
        if c is None or (c._proc and c._proc.poll() is not None):
            c = PyrightClient(
                workspace_dir=workspace,
                init_timeout_s=float(os.environ.get("SYMBOL_LOCATOR_LSP_INIT_TIMEOUT_S", "180")),
                query_timeout_s=float(os.environ.get("SYMBOL_LOCATOR_LSP_QUERY_TIMEOUT_S", "60")),
                warmup_file_cap=int(os.environ.get("SYMBOL_LOCATOR_WARMUP_FILE_CAP", "2000")),
                on_log=_log,
            )
            c.start()
            _CLIENTS[workspace] = c
        return c


# set by install.set_workspace() after LocAgent's set_current_issue picks the repo
_ACTIVE_WORKSPACE: Optional[str] = None


def set_active_workspace(path: str) -> None:
    global _ACTIVE_WORKSPACE
    _ACTIVE_WORKSPACE = os.path.abspath(path) if path else None


def _resolve_workspace() -> str:
    return (
        _ACTIVE_WORKSPACE
        or os.environ.get("SYMBOL_LOCATOR_WORKSPACE")
        or os.environ.get("LOCAL_REPO_PATH")
        or os.getcwd()
    )


def warmup_workspace(workspace: Optional[str] = None) -> str:
    """One-shot: index the whole workspace so workspace/symbol returns full results.

    Called eagerly at install_into_locagent() time so the first tool call
    doesn't pay the cold index cost inside the agent loop.
    """
    ws = workspace or _resolve_workspace()
    client = _get_client(ws)
    report = client.warmup()
    return (
        f"warmup: workspace={ws} files={report['files_found']} "
        f"indexed={report['files_indexed']} failed={report['failed']}"
    )


def _score_and_snippet(
    client: PyrightClient,
    page: list,
    context: Optional[str],
    snippet_lines: int = DEFAULT_SNIPPET_LINES,
) -> list[ScoredSymbol]:
    """Take pre-ranked candidates → read snippets → LLM batch score."""
    inputs = []
    snippets = []
    for cand in page:
        snip = client.get_source_snippet(cand.file, cand.line, snippet_lines)
        snippets.append(snip)
        inputs.append({
            "file": cand.file,
            "line": cand.line,
            "container": cand.container,
            "snippet": snip,
        })
    scores = score_batch(inputs, context, on_log=_log)
    return [ScoredSymbol(symbol=c, score=s, snippet=snip) for c, s, snip in zip(page, scores, snippets)]


def _rel_to_workspace(path: str) -> str:
    """Strip the active workspace prefix so the LLM only sees repo-relative
    paths — downstream evaluators (and LocAgent's own parser) compare paths
    verbatim against GT which is always relative. Falls back to the input
    unchanged if it's already relative or lives outside the workspace."""
    if not path or not os.path.isabs(path):
        return path
    ws = _ACTIVE_WORKSPACE or _resolve_workspace()
    try:
        ws_real = os.path.realpath(ws)
        p_real = os.path.realpath(path)
        rel = os.path.relpath(p_real, ws_real)
        # If the file isn't inside the workspace, relpath emits "../..";
        # keep the absolute path in that case rather than mislead the LLM.
        if rel.startswith(".."):
            return path
        return rel
    except (ValueError, OSError):
        return path


def _format(name: str, batch: list[ScoredSymbol], total: int, remaining: int) -> str:
    if not batch:
        return f"No candidates for `{name}`. Try a partial name or check spelling."
    out = [f"Found {total} candidate{'s' if total != 1 else ''} for `{name}`."
           f" Showing top {len(batch)} by relevance:"]
    for i, s in enumerate(batch, 1):
        label = f"{s.symbol.container}.{s.symbol.name}" if s.symbol.container else s.symbol.name
        indented = "\n".join("   " + line for line in s.snippet.split("\n"))
        out.append(
            f"\n{i}. [score={s.score}] {label}\n"
            f"   {_rel_to_workspace(s.symbol.file)}:{s.symbol.line}\n"
            f"{indented}"
        )
    if remaining > 0:
        out.append(
            f"\n\n{remaining} more candidate{'s' if remaining != 1 else ''} cached. "
            f"Call `more_symbols(name=\"{name}\")` for the next batch."
        )
    return "".join(out)


# ═══════════════════════════════════════════════════════════════════════
# TOOL FUNCTIONS — signatures and docstrings ARE the LocAgent tool spec.
# Keep types simple (str/int/list[int]/None); LocAgent's IPython path does
# `print(func_name(**arguments))` so return a str.
# ═══════════════════════════════════════════════════════════════════════


def find_symbol(
    name: str,
    context: Optional[str] = None,
    top_k: int = 3,
    kind_filter: Optional[list[int]] = None,
) -> str:
    """Locate any Python symbol (class, function, method, variable) by name.

    ALWAYS call this FIRST before grep or file read when you need to find
    where something is defined. Returns LSP-precise file:line locations with
    source snippets and relevance scores.

    Substring matching supported — you don't need the exact name.
    "subs" finds Basic.subs, _eval_subs, etc.

    Args:
        name: Symbol to find (class, function, method, or variable name).
        context: Optional — what you're trying to do. Improves ranking.
                 e.g. "fixing form save AttributeError".
        top_k: How many top candidates to return (default 3).
        kind_filter: Optional list of LSP SymbolKinds to keep.
                     5=class, 6=method, 12=function, 13=variable.

    Returns:
        A ranked, snippet-annotated candidate list as a text string. If more
        candidates exist, call `more_symbols(name=...)` for the next batch.
    """
    name = (name or "").strip()
    if not name or " " in name or len(name) > 128:
        return (
            "`find_symbol` expects a single Python symbol or dotted path. "
            "Extract one symbol name and try again."
        )

    workspace = _resolve_workspace()
    client = _get_client(workspace)
    key = _CACHE.key(workspace, name, context, None)

    # cache hit path
    cached = _CACHE.get(key)
    if cached is not None:
        batch = _CACHE.advance(key, top_k)
        if not batch and cached.pending:
            page = _CACHE.take_pending(key, SCORE_WINDOW)
            scored = _score_and_snippet(client, page, context)
            _CACHE.append(key, scored)
            batch = _CACHE.advance(key, top_k)
        total = len(cached.scored) + len(cached.pending)
        remaining = total - cached.cursor + len(cached.pending)
        # (cursor already advanced; remaining approximates unshown)
        return _format(name, batch, total, max(0, remaining))

    # cache miss
    try:
        raw = client.workspace_symbol(name)
    except Exception as e:
        return f"Error looking up `{name}`: {e}. Fall back to grep for this one."

    if not raw:
        return f"No symbols named `{name}` found in workspace. Try a partial match or check spelling."

    if kind_filter:
        allowed = set(kind_filter)
        raw = [s for s in raw if s.kind in allowed]
        if not raw:
            return f"No `{name}` matches the kind_filter. Try without it."

    ranked = rank_candidates(raw, name, context)
    page = ranked[:SCORE_WINDOW]
    pending = ranked[SCORE_WINDOW:]
    scored = _score_and_snippet(client, page, context)

    _CACHE.set(key, scored, pending)
    batch = _CACHE.advance(key, top_k)
    remaining = len(scored) - min(top_k, len(scored)) + len(pending)
    return _format(name, batch, len(ranked), remaining)


def more_symbols(name: str, top_k: int = 3, context: Optional[str] = None) -> str:
    """Return the next batch of candidates from a previous `find_symbol` call.

    Only works if `find_symbol(name=...)` was called earlier in this session
    with the same `name` and `context`. Otherwise re-runs the query.

    Args:
        name: Same name that was passed to `find_symbol`.
        top_k: How many candidates to return this batch (default 3).
        context: Same context that was passed to `find_symbol`, if any.

    Returns:
        The next `top_k` candidates by score, or a message saying the cache is empty.
    """
    workspace = _resolve_workspace()
    key = _CACHE.key(workspace, name.strip(), context, None)
    cached = _CACHE.get(key)
    if cached is None:
        # cache miss — silently degrade by rerunning
        return find_symbol(name=name, context=context, top_k=top_k)

    batch = _CACHE.advance(key, top_k)
    if not batch and cached.pending:
        client = _get_client(workspace)
        page = _CACHE.take_pending(key, SCORE_WINDOW)
        scored = _score_and_snippet(client, page, context)
        _CACHE.append(key, scored)
        batch = _CACHE.advance(key, top_k)

    total = len(cached.scored) + len(cached.pending)
    remaining = total - cached.cursor + len(cached.pending)
    if not batch:
        return f"No more candidates for `{name}`. All {total} ranked candidates have been returned."
    return _format(name, batch, total, max(0, remaining))


def reset_symbols() -> str:
    """Clear the symbol-locator candidate cache. Useful when the workspace
    has changed on disk mid-session and old cached rankings are stale.

    Returns:
        A one-line confirmation.
    """
    n = _CACHE.size
    _CACHE.clear()
    return f"cleared {n} cached queries"


def demo() -> None:
    """ponytail self-check — no LSP or LLM needed, tests plain-Python bits."""
    # find_symbol input validation
    r = find_symbol(name="")
    assert "single Python symbol" in r
    r = find_symbol(name="a b")
    assert "single Python symbol" in r
    # reset on empty cache
    _CACHE.clear()
    r = reset_symbols()
    assert "cleared 0" in r
    # _format
    from .lsp import PlainSymbol
    s = PlainSymbol("foo", 12, "function", "/a.py", 5, 1)
    out = _format("foo", [ScoredSymbol(s, 90, "def foo(): pass")], total=1, remaining=0)
    assert "[score=90] foo" in out
    assert "/a.py:5" in out
    # _rel_to_workspace: relative in-workspace path is stripped; outside stays absolute
    global _ACTIVE_WORKSPACE
    saved_ws = _ACTIVE_WORKSPACE
    try:
        _ACTIVE_WORKSPACE = "/tmp"
        assert _rel_to_workspace("/tmp/foo/bar.py") == "foo/bar.py"
        assert _rel_to_workspace("relative/path.py") == "relative/path.py"
        assert _rel_to_workspace("").endswith("")  # no crash on empty
        # outside workspace → keep absolute (relpath would emit "../...")
        assert _rel_to_workspace("/etc/passwd") == "/etc/passwd"
    finally:
        _ACTIVE_WORKSPACE = saved_ws
    print("core demo OK")


if __name__ == "__main__":
    demo()
