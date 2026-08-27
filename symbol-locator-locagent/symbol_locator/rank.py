"""Cheap pre-rank — ported from src/ranker/cheap-rank.ts."""
from __future__ import annotations

import re
from typing import Optional

from .lsp import PlainSymbol


def rank_candidates(
    candidates: list[PlainSymbol],
    query: str,
    context: Optional[str] = None,
) -> list[PlainSymbol]:
    q = query.lower()
    q_bare = q.lstrip("_")
    ctx_tokens = _tokens(context or "")
    scored = []
    for i, c in enumerate(candidates):
        s = _name_score(c, q, q_bare) + _context_score(c, ctx_tokens) + _path_prior(c, ctx_tokens)
        scored.append((-s, i, c))  # -s so higher scores sort first
    scored.sort()
    return [c for _, _, c in scored]


def _name_score(c: PlainSymbol, q: str, q_bare: str) -> int:
    name = c.name.lower()
    bare = name.lstrip("_")
    container = c.container.lower() if c.container else None
    if name == q or bare == q_bare or container == q:
        return 100
    if name.endswith("_" + q) or bare.endswith(q_bare):
        return 65
    if name.startswith(q + "_") or bare.startswith(q_bare):
        return 55
    return 20 if (q in name or q_bare in bare) else 0


def _context_score(c: PlainSymbol, ctx_tokens: list[str]) -> int:
    cand_tokens = _tokens(f"{c.file} {c.container or ''} {c.name}")
    score = 0
    for t in ctx_tokens:
        if any(_related(t, ct) for ct in cand_tokens):
            score += 12
    return min(score, 48)


def _path_prior(c: PlainSymbol, ctx_tokens: list[str]) -> int:
    f = c.file.lower()
    test_ctx = any(t in ("test", "regression") for t in ctx_tokens)
    if not test_ctx and ("/tests/" in f or re.search(r"(^|[/_])test_", f)):
        return -50
    if not test_ctx and ("/docs/" in f or "/examples/" in f):
        return -25
    return 0


_CAMEL_RE = re.compile(r"([a-z])([A-Z])")
_SPLIT_RE = re.compile(r"[^a-z0-9]+")


def _tokens(value: str) -> list[str]:
    expanded = _CAMEL_RE.sub(r"\1 \2", value).lower()
    expanded = expanded.replace("polynomial", "polynomial poly").replace("expression", "expression expr")
    parts = [p for p in _SPLIT_RE.split(expanded) if len(p) >= 3]
    return list(dict.fromkeys(parts))  # dedupe, preserve order


def _related(a: str, b: str) -> bool:
    return a == b or a.startswith(b) or b.startswith(a)
