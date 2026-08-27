---
name: precise-symbol-locate
description: Locating any named Python symbol (class / function / method /
  variable). Use `find_symbol` FIRST when you know a likely symbol name; it
  asks Pyright's workspace-symbol search, which may also return related
  prefix, suffix, or substring matches.
---

# Precise Symbol Locate

## Default

**When you know (or can infer) a Python symbol name, `find_symbol` is your
first move — before grep, before reading files, before "let me look at the
directory structure."** It runs a Pyright `workspace/symbol` query and
returns LSP-precise locations (file, line, container class), source snippets,
and a relevance score.

Prefer:
- ✅ `find_symbol({ name: "Foo" })` for symbol lookup
- ❌ `grep "class Foo"` / `grep "def bar"` when a symbol name is what you have

## Turn the query into a real identifier fragment first

Pyright indexes actual source identifiers. Prose placeholders never live in
the index — translate them before you call.

Common placeholders to unfold:
- Docs/ticket conventions: `FOO`, `BAR`, `X`, `<field>`, `MyModel` —
  these are *examples*, not source strings.
- Field/method templates: `get_FOO_display` → try `get_field_display` or
  `_get_FIELD_display` (both are real fragments in Django);
  `<Model>Serializer` → try `Serializer` or a concrete example.
- If unsure what to unfold to, drop back to the shortest **real** fragment:
  `get_FOO_display` → `get_` + inspect returned names.

Pyright's match is fuzzy (prefix / suffix / substring), so even a fragment
usually pulls the right symbol back. What it will not do is invent a symbol
for a name that never appears in source.

If the first query returns 0 results, don't retry the same string — reshape
it: strip the placeholder, try a nearby real identifier, or narrow to a
distinctive fragment you *did* see in the codebase.

## Call it again as you go deeper

**One call at the start is not enough.** Bugs often live several call-hops
away from the name in the error message. Every time you learn a *new*
identifier while reading (a function being called, a class being subclassed,
a helper referenced in a comment), that is another `find_symbol` moment —
not a "let me grep it" moment.

Rule of thumb: if you're about to read a file just to find where `X` is
defined or where it's called, call `find_symbol({ name: "X" })` first.

Example flow (real sympy bug: `PolynomialError` triggered from `subs`):

1. `find_symbol({ name: "subs" })` → lands on `Basic.subs`, `Piecewise._eval_subs`
2. Read those, notice control flows through `_ask("real")` → `sinh._eval_is_real`
3. **`find_symbol({ name: "sinh" })`** ← don't grep, ask again
4. Read `sinh._eval_is_real`, see it calls `(im % pi).is_zero` → `Mod.eval`
5. **`find_symbol({ name: "Mod" })`** ← again
6. Land on `Mod.eval`, see `gcd(p, q)` — bug is here.

Skipping steps 3 and 5 means grepping through a large repo by hand. Each
`find_symbol` call is ranked and returns snippets, so it's usually cheaper
than opening 3–4 candidate files yourself.

Cost is low: results are cached per `(workspace, name, context)`, so
repeated calls with the same name in the same session are instant.

## When `find_symbol` is the wrong tool

Reach for grep (or another tool) when the task is:

- **Regex / pattern searches** — "all classes ending in `Config`", "every
  `post_*` method". Pyright doesn't take regex and doesn't guarantee an
  exhaustive pattern sweep.
- **Free-text / string-literal searches inside function bodies** — "who
  writes this log line", "where is this URL constructed". The string may
  never appear as an identifier.
- **Metaprogramming lookups** — methods added via `setattr`, `type()`,
  metaclass `__new__`, decorators that rewrite the class. Those names only
  exist at runtime; grep the *generation site* (`setattr(cls, 'get_%s_...'`)
  instead.
- **File-name searches** — Pyright returns symbol definitions, not files
  whose names happen to contain the query.
- **Non-Python code** — `find_symbol` is Python-only.

## Workflow

1. **Call `find_symbol`** with the (unfolded) name and a short `context`:

   ```
   find_symbol({ name: "ModelForm", context: "understanding form save flow" })
   ```

2. **Inspect the candidates** — each has `score` (0–100), file, line,
   container class, and a source snippet. Pick the right one from the
   returned names, don't assume the input string must equal any result.

3. **If the right candidate isn't in the top-k**, call `more_symbols` with
   the same `name` **and the same `context`**:

   ```
   more_symbols({ name: "ModelForm", context: "understanding form save flow" })
   ```

   Cheap — no re-indexing, no re-scoring. Pages the cached list.

4. **Narrow by kind if the name is common**:

   ```
   find_symbol({ name: "save", kind_filter: [6] })   // methods only
   ```

   `5` = class, `6` = method, `12` = function, `13` = variable.

## Tips

- **`context` matters.** Describe what you're trying to do in one line.
  The scorer uses it to rank the right candidate to the top.
- **A fuzzy result is normal.** Read the returned symbol names.
- **Results are cached** per `(workspace, name, context)` — repeat calls
  are instant.
- **First call in a workspace is slow** (~10–30s while pyright indexes).
  Subsequent calls are fast. If it times out, retry once.
