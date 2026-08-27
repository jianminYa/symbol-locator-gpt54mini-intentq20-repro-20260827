# Symbol Locator — OpenClaw Plugin

Precise Python symbol location for AI agents. Given a symbol name (function,
class, method, variable), returns ranked candidates with source snippets so
the agent can pick the right one without grep-guessing.

- **LSP-powered** — pyright indexes the workspace; queries return exact locations.
- **LLM-ranked** — a scorer prompt ranks candidates against the caller's stated intent.
- **Paginated** — top-k first, then `more_symbols` walks the cache cursor.

## Tools

- `find_symbol({ name, context?, top_k?, kind_filter?, rescore? })` — full pipeline. First call for a name.
- `more_symbols({ name, context?, count? })` — cache-only continuation of the previous `find_symbol`.

## Skill

The `precise-symbol-locate` skill (in `skills/`) tells the agent when and how
to reach for these tools instead of grep.

## Install

```
openclaw plugins install /path/to/openclaw-symbol-locator
```

Enable in `openclaw.json`:
```json
{
  "plugins": {
    "entries": {
      "symbol-locator": {
        "enabled": true,
        "hooks": {
          "allowConversationAccess": true,
          "allowPromptInjection": true
        },
        "config": {}
      }
    },
    "allow": ["symbol-locator"]
  },
  "tools": {
    "alsoAllow": ["find_symbol", "more_symbols", "reset_symbols"]
  }
}
```

The hook permissions are required for the plugin to add its symbol-first
navigation guidance to the agent prompt and to observe agent lifecycle events.

## Config

All fields optional — defaults shown.

```jsonc
{
  "scorer": {
    "model": "anthropic/claude-sonnet-4-6",  // omit → use host default LLM
    "concurrency": 10,                       // parallel score batches
    "threshold": 75,                         // drop candidates below this score
    "snippetLines": 15                       // lines of source per candidate
  },
  "scorerLlm": {
    "enabled": false,                        // true → use independent LLM (below) instead of host
    "baseUrl": "https://api.openai.com/v1",
    "apiKey": "sk-...",
    "model": "gpt-4o-mini",
    "timeoutMs": 30000
  },
  "lsp": {
    "maxWorkspaces": 4,                      // max concurrent pyright workers
    "idleTimeoutMs": 1800000,                // fallback if agent_end is not emitted
    "initTimeoutMs": 60000                   // 60 s — pyright startup deadline
  },
  "cache": {
    "maxEntries": 256,                       // LRU capacity
    "ttlMs": 1800000                         // 30 min — entry TTL
  }
}
```

Pyright workers are closed after the last concurrent agent run for a workspace
finishes. The idle timeout is only a fallback for interrupted/nonstandard runs.

## Requirements

- Python workspace with `pyright` reachable on `PATH` (bundled binary preferred).
- OpenClaw host with LLM runtime (for scoring). Without it, candidates return with `score=50` — still useful, just unranked.

## Development

```
pnpm install
pnpm test              # unit tests
pnpm test:integration  # integration tests (spawns real pyright)
```
