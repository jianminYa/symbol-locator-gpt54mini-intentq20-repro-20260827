"""LLM scorer — batch prompt, parses INDEX=SCORE lines.

Uses litellm.completion so we ride on LocAgent's existing LLM setup
(OPENAI_API_KEY / OPENAI_API_BASE). If litellm is missing or the call
fails, everything gets score=50 (neutral). The neutral fallback is the
whole point — the tool must never crash the agent because scoring failed.
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional

SYSTEM_PROMPT = (
    "You are a Python code expert. Your job is to score how likely a piece of code "
    "is what the user is looking for, given their current task context."
)

_LINE_RE = re.compile(r"^\s*(\d+)\s*=\s*(\d{1,3})\s*$", re.MULTILINE)

# ponytail: module-level counter, single-process — scorer runs inside LocAgent's
# child process. pop_usage() reads-and-clears; install.py atexit dumps it.
_USAGE = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}


def pop_usage() -> dict:
    """Return accumulated LLM usage since last pop, then zero it."""
    snapshot = dict(_USAGE)
    _USAGE.update({"prompt_tokens": 0, "completion_tokens": 0, "calls": 0})
    return snapshot


def _flush_sidecar(on_log=lambda _: None) -> None:
    """Write _USAGE to SYMBOL_LOCATOR_USAGE_OUT synchronously.
    ponytail: atexit doesn't fire in LocAgent's fork child (os._exit path);
    call after each score_batch so the sidecar is always current."""
    p = os.environ.get("SYMBOL_LOCATOR_USAGE_OUT")
    if not p:
        return
    try:
        tmp = p + ".tmp"
        with open(tmp, "w") as f:
            json.dump(_USAGE, f)
        os.replace(tmp, p)
    except Exception as e:
        on_log(f"[scorer] sidecar write failed: {e}")


def build_batch_prompt(context: Optional[str], candidates: list[dict]) -> str:
    parts: list[str] = []
    if context:
        parts.append(f"<task_context>{context}</task_context>")
    parts.append(f"You will score {len(candidates)} candidates below.")
    for i, c in enumerate(candidates, start=1):
        parts.append(f'<candidate index="{i}">')
        parts.append(f"file: {c['file']}:{c['line']}")
        parts.append(f"container: {c.get('container') or '(module)'}")
        parts.append("code:")
        parts.append(c["snippet"])
        parts.append("</candidate>")
    parts.append(
        f"Score each candidate 0-100 (higher = more likely to be what the user needs).\n"
        f"Output ONE LINE PER CANDIDATE in the exact form INDEX=SCORE (no spaces around =).\n"
        f"Example:\n1=85\n2=30\n3=70\n"
        f"Emit every candidate 1..{len(candidates)}. Output nothing else."
    )
    return "\n".join(parts)


def parse_batch(text: str, expected: int) -> list[Optional[int]]:
    out: list[Optional[int]] = [None] * expected
    for m in _LINE_RE.finditer(text):
        idx = int(m.group(1)) - 1
        score = int(m.group(2))
        if 0 <= idx < expected and 0 <= score <= 100:
            out[idx] = score
    return out


def score_batch(
    candidates: list[dict],
    context: Optional[str],
    model: Optional[str] = None,
    timeout_s: float = 60.0,
    on_log=lambda _: None,
) -> list[int]:
    """Return one int score per candidate. Never raises; failures → all 50s."""
    n = len(candidates)
    if n == 0:
        return []

    model = model or os.environ.get("SYMBOL_LOCATOR_SCORER_MODEL") or "openai/gpt-4o-mini"

    try:
        import litellm  # type: ignore
    except ImportError:
        on_log("[scorer] litellm not installed — using score=50")
        return [50] * n

    prompt = build_batch_prompt(context, candidates)
    try:
        resp = litellm.completion(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=2048,
            temperature=0,
            timeout=timeout_s,
        )
        text = resp["choices"][0]["message"]["content"] or ""
        try:
            _USAGE["prompt_tokens"]     += int(resp.usage.prompt_tokens)
            _USAGE["completion_tokens"] += int(resp.usage.completion_tokens)
            _USAGE["calls"]             += 1
            on_log(f"[scorer] usage pid={os.getpid()} calls={_USAGE['calls']} p={_USAGE['prompt_tokens']} c={_USAGE['completion_tokens']}")
        except Exception as _u_e:
            on_log(f"[scorer] usage-read failed: {_u_e}")
    except Exception as e:
        on_log(f"[scorer] LLM call failed type={type(e).__name__} — using score=50")
        return [50] * n

    parsed = parse_batch(text, n)
    hits = sum(1 for p in parsed if p is not None)
    on_log(f"[scorer] parsed {hits}/{n} scores from LLM")
    _flush_sidecar(on_log)
    return [p if p is not None else 50 for p in parsed]


def demo() -> None:
    """ponytail self-check — no network needed."""
    text = "1=85\n2=30\n3=70"
    assert parse_batch(text, 3) == [85, 30, 70]
    assert parse_batch("garbage 1=42 stuff\n2=100", 3) == [None, 100, None]  # inline "1=42" not on own line
    assert parse_batch("1=999", 1) == [None]  # out-of-range
    prompt = build_batch_prompt("fix save bug", [
        {"file": "a.py", "line": 1, "container": None, "snippet": "def foo(): pass"},
    ])
    assert "<task_context>fix save bug</task_context>" in prompt
    assert "1=" in prompt  # example present
    # usage counter starts clean; pop is idempotent
    u0 = pop_usage(); assert u0["calls"] == 0
    _USAGE["prompt_tokens"] = 42
    u1 = pop_usage(); assert u1["prompt_tokens"] == 42
    u2 = pop_usage(); assert u2["prompt_tokens"] == 0   # reset happened
    print("scorer demo OK")


if __name__ == "__main__":
    demo()
