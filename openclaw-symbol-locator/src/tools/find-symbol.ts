// Symbol Locator — find_symbol tool.
// Full LSP → scorer → cache pipeline.
import { Type } from "typebox";
import type { Static } from "typebox";
import type { AnyAgentTool, OpenClawPluginApi } from "../../api.js";
import type { CandidateCache } from "../cache/cache.js";
import { WorkspaceSymbolTimeoutError } from "../lsp/client.js";
import type { WorkspacePool } from "../lsp/manager.js";
import type { SymbolKindNumber } from "../lsp/types.js";
import { SymbolKind } from "../lsp/types.js";
import { scoreCandidates } from "../scorer/scorer.js";
import { emitDiagnostic } from "../diagnostics.js";
import { rankCandidates } from "../ranker/cheap-rank.js";
import {
  resolveScorerConfig,
  resolveScorerLlmConfig,
  resolveLspConfig,
  resolveCacheConfig,
} from "../config.js";

const SYMBOL_KIND_VALUES = Object.values(SymbolKind).filter(
  (v): v is SymbolKindNumber => typeof v === "number",
);
const SCORE_WINDOW_SIZE = 25;

const FindSymbolSchema = Type.Object(
  {
    name: Type.String({ description: "Symbol to find (class, function, method, or variable)." }),
    context: Type.Optional(
      Type.String({
        description:
          "What you are trying to do — improves ranking. e.g. 'fixing form save AttributeError'.",
      }),
    ),
    top_k: Type.Optional(
      Type.Number({
        default: 3,
        minimum: 1,
        maximum: 10,
        description: "How many top candidates to return.",
      }),
    ),
    rescore: Type.Optional(
      Type.Boolean({
        description:
          "Force-rescore even if cached. Default: true when >3 candidates, else false.",
      }),
    ),
    kind_filter: Type.Optional(
      Type.Array(Type.Number(), {
        description: "Only return these LSP SymbolKinds (5=class, 6=method, 12=function, 13=variable).",
      }),
    ),
  },
  { additionalProperties: false },
);

type FindSymbolParams = Static<typeof FindSymbolSchema>;

export type FindSymbolDeps = {
  api: OpenClawPluginApi;
  pool: WorkspacePool;
  cache: CandidateCache;
  resolveConfig: () => Record<string, unknown>;
  resolveWorkspaceDir?: () => string; // default: process.cwd
  resolveSessionId?: () => string | undefined;
};

export function createFindSymbolTool(deps: FindSymbolDeps): AnyAgentTool {
  const { api, pool, cache, resolveConfig } = deps;

  return {
    name: "find_symbol",
    label: "Find Symbol",
    description:
      "Locate any Python symbol (class, function, method, variable) by name or partial name. " +
      "ALWAYS call this FIRST before grep or read when you need to find where something is defined. " +
      "Returns LSP-precise file:line locations with source snippets and relevance scores.\n\n" +
      "Supports substring matching — you don't need the exact name. \"subs\" finds Basic.subs, " +
      "_eval_subs, etc. The scorer ranks the right one to the top.\n\n" +
      "`context` improves ranking. Describe what you're trying to do (\"fixing form save error\").\n\n" +
      "If more candidates exist, use `more_symbols`. Prefer this over grep or `find .` — it is " +
      "faster and more precise for symbol-level lookups.",
    parameters: FindSymbolSchema,
    execute: async (toolCallId, rawParams) => {
      const params = rawParams as FindSymbolParams;
      const config = resolveConfig();
      const scorerCfg = resolveScorerConfig(config);
      const scorerLlmCfg = resolveScorerLlmConfig(config);
      const lspCfg = resolveLspConfig(config);
      const cacheCfg = resolveCacheConfig(config);
      const topK = params.top_k ?? 3;
      const workspaceDir = deps.resolveWorkspaceDir?.() ?? process.cwd();
      const sessionId = deps.resolveSessionId?.();
      const name = params.name.trim();
      const context = params.context;
      const diagIds =
        `call_id=${JSON.stringify(toolCallId)} session_id=${JSON.stringify(sessionId ?? "unknown")}`;
      const diag = (line: string) => api.logger.info?.(line);

      try {
        emitDiagnostic(
          diag,
          `call name=${JSON.stringify(name)} context=${JSON.stringify(context)} ` +
            `top_k=${topK} rescore=${String(params.rescore)} ` +
            `kind_filter=${JSON.stringify(params.kind_filter)} ${diagIds} ` +
            `workspace=${JSON.stringify(workspaceDir)}`,
        );
        if (!name || name.length > 128 || /\s/.test(name)) {
          return {
            content: [
              {
                type: "text",
                text:
                  "`find_symbol` expects a single Python symbol or dotted path, not an error " +
                  `message. Extract one symbol first (for example \`Piecewise\` or ` +
                  "`PolynomialError`) and try again.",
              },
            ],
            details: {
              error: "invalid_symbol_query",
              message: "Expected one whitespace-free Python symbol or dotted path.",
            },
          };
        }
        const cacheKey = cache.key(workspaceDir, name, context, sessionId);

        // ---- cache lookup ----
        const cached = cache.get(cacheKey);
        emitDiagnostic(
          diag,
          `cache ${cached ? "HIT" : "MISS"} key=${cacheKey} ` +
            `size=${cache.size} cursor=${cached?.cursor ?? "-"} ${diagIds}`,
        );
        if (cached) {
          const total = cached.candidates.length + cached.pending.length;
          let batch = cache.advance(cacheKey, topK);
          if (batch.length === 0 && cached.pending.length > 0) {
            const client = await pool.getClient(workspaceDir, sessionId);
            await client.warmup();
            const page = cache.takePending(cacheKey, SCORE_WINDOW_SIZE);
            emitDiagnostic(
              diag,
              `lazy-score candidates=${page.length} pending_after=${cached.pending.length} ${diagIds}`,
            );
            const scored = await scorePage({
              candidates: page,
              client,
              context,
              scorerCfg,
              scorerLlmCfg,
              api,
              rescore: params.rescore,
              diag,
              diagIds,
            });
            cache.append(cacheKey, scored);
            batch = cache.advance(cacheKey, topK);
          }
          if (batch.length === 0) {
            return {
              content: [
                {
                  type: "text",
                  text:
                    `No more candidates for \`${name}\`. All ${total} ranked candidates ` +
                    `have been scored and returned.`,
                },
              ],
              details: {
                total,
                returned: 0,
                remaining: 0,
                candidates: [],
                cache_key: cacheKey,
              },
            };
          }
          const remaining =
            cached.candidates.length - (cached.cursor ?? cached.candidates.length) +
            cached.pending.length;
          return buildResponse(name, batch, total, remaining, cacheKey);
        }

        // ---- cache miss: full pipeline ----
        const client = await pool.getClient(workspaceDir, sessionId);
        await client.warmup();
        emitDiagnostic(
          diag,
          `workspaceSymbol query=${JSON.stringify(name)} ` +
            `workspace=${JSON.stringify(workspaceDir)} ${diagIds}`,
        );
        const rawSymbols = await client.workspaceSymbol(name);
        emitDiagnostic(diag, `workspaceSymbol raw len=${rawSymbols.length} ${diagIds}`);

        if (rawSymbols.length === 0) {
          // Full workspaceSymbol("") enumeration can exhaust Pyright on large repos.
          emitDiagnostic(
            diag,
            `[probe-B] name=${JSON.stringify(name)} empty skipped_full_index=true ${diagIds}`,
          );
          return {
            content: [
              {
                type: "text",
                text:
                  `No symbols named \`${name}\` found in workspace. ` +
                  `Try without kind_filter, check spelling, or use a partial match.`,
              },
            ],
            details: { total: 0, returned: 0, remaining: 0, candidates: [], cache_key: cacheKey },
          };
        }

        // Apply kind_filter
        let candidates = rawSymbols;
        if (params.kind_filter && params.kind_filter.length > 0) {
          const allowed = new Set(params.kind_filter);
          candidates = rawSymbols.filter((s) => allowed.has(s.kind));
        }

        if (candidates.length === 0) {
          return {
            content: [
              {
                type: "text",
                text:
                  `Found ${rawSymbols.length} symbols named \`${name}\`, ` +
                  `but none match the kind_filter. Try without kind_filter.`,
              },
            ],
            details: {
              total: rawSymbols.length,
              returned: 0,
              remaining: 0,
              candidates: [],
              cache_key: cacheKey,
            },
          };
        }

        const ranked = rankCandidates(candidates, { query: name, context });
        const page = ranked.slice(0, SCORE_WINDOW_SIZE);
        const pending = ranked.slice(SCORE_WINDOW_SIZE);
        emitDiagnostic(
          diag,
          `prerank raw=${rawSymbols.length} filtered=${candidates.length} ` +
            `first_page=${page.length} pending=${pending.length} ${diagIds}`,
        );
        const scored = await scorePage({
          candidates: page,
          client,
          context,
          scorerCfg,
          scorerLlmCfg,
          api,
          rescore: params.rescore,
          diag,
          diagIds,
        });

        // Cache + return first batch
        cache.set(cacheKey, scored, pending);
        const batch = cache.advance(cacheKey, topK);
        const remaining = scored.length - Math.min(topK, scored.length) + pending.length;
        return buildResponse(name, batch, ranked.length, remaining, cacheKey);
      } catch (err) {
        if (err instanceof WorkspaceSymbolTimeoutError) {
          emitDiagnostic(
            diag,
            `query_timeout query=${JSON.stringify(err.query)} elapsedMs=${err.elapsedMs} ${diagIds}`,
          );
          return {
            content: [
              {
                type: "text",
                text:
                  `Symbol index timed out looking up \`${name}\` after ${err.elapsedMs}ms. ` +
                  `Fall back to grep/rg for this one (e.g. ` +
                  "`rg --line-number '\\b" + name + "\\b'`), then retry `find_symbol` later.",
              },
            ],
            details: {
              error: "query_timeout",
              query: err.query,
              elapsed_ms: err.elapsedMs,
              total: 0,
              returned: 0,
              remaining: 0,
              candidates: [],
            },
          };
        }
        const message = err instanceof Error ? err.message : String(err);
        api.logger?.error?.(`find_symbol failed: ${message}`);
        return {
          content: [
            {
              type: "text",
              text: `Error looking up \`${name}\`: ${message}. Try again in a moment — if this persists, the LSP index may not be ready yet.`,
            },
          ],
          details: {
            total: 0,
            returned: 0,
            remaining: 0,
            candidates: [],
            error: message,
          },
        };
      }
    },
  };
}

async function scorePage(params: {
  candidates: import("../lsp/types.js").PlainSymbol[];
  client: import("../lsp/client.js").PyrightClient;
  context?: string;
  scorerCfg: ReturnType<typeof resolveScorerConfig>;
  scorerLlmCfg: ReturnType<typeof resolveScorerLlmConfig>;
  api: OpenClawPluginApi;
  rescore?: boolean;
  diag: (line: string) => void;
  diagIds: string;
}) {
  const {
    candidates,
    client,
    context,
    scorerCfg,
    scorerLlmCfg,
    api,
    rescore,
    diag,
    diagIds,
  } = params;
  emitDiagnostic(
    diag,
    `before-score candidates=${candidates.length} threshold=${scorerCfg.threshold} ` +
      `independent=${scorerLlmCfg ? "yes(" + scorerLlmCfg.model + ")" : "no"} ` +
      `hostLlm=${api.runtime?.llm ? "present" : "absent"} rescore=${String(rescore)} ${diagIds}`,
  );
  try {
    const scored = await scoreCandidates({
      candidates,
      context,
      workspaceClient: client,
      hostLlm: api.runtime?.llm as Parameters<typeof scoreCandidates>[0]["hostLlm"],
      independentCfg: scorerLlmCfg,
      concurrency: scorerCfg.concurrency,
      threshold: scorerCfg.threshold,
      snippetLines: scorerCfg.snippetLines,
      rescore,
      logger: api.logger,
      diag,
    });
    const scores = scored.map((s) => s.score).slice(0, 10).join(",");
    emitDiagnostic(
      diag,
      `scored len=${scored.length} passed-threshold; first-scores=[${scores}] ${diagIds}`,
    );
    return scored;
  } catch (scoreErr) {
    emitDiagnostic(
      diag,
      `scoreCandidates THREW: ${scoreErr instanceof Error ? scoreErr.message : String(scoreErr)} ${diagIds}`,
    );
    return Promise.all(
      candidates.map(async (candidate) => ({
        ...candidate,
        score: 50,
        snippet: await client
          .getSourceSnippet(candidate.file, candidate.line, scorerCfg.snippetLines)
          .catch(() => "[snippet unavailable]"),
      })),
    );
  }
}

function buildResponse(
  name: string,
  batch: ReturnType<CandidateCache["advance"]>,
  total: number,
  remaining: number,
  cacheKey: string,
) {
  const returned = batch.length;
  const textParts: string[] = [
    `Found ${total} candidate${total !== 1 ? "s" : ""} for \`${name}\`.` +
      (returned > 0 ? ` Showing ${returned === 1 ? "top match" : `top ${returned}`} by relevance:` : ""),
  ];

  for (let i = 0; i < batch.length; i++) {
    const c = batch[i]!;
    const label = c.container ? `${c.container}.${c.name}` : c.name;
    textParts.push(
      `\n${i + 1}. [score=${c.score}] ${label}\n   ${c.file}:${c.line}\n${indent(c.snippet, "   ")}`,
    );
  }

  if (remaining > 0) {
    textParts.push(
      `\n\n${remaining} more candidate${remaining !== 1 ? "s" : ""} cached. ` +
        `Call \`more_symbols({ name: "${name}" })\` to see the next batch, or refine with kind_filter.`,
    );
  }

  return {
    content: [{ type: "text" as const, text: textParts.join("") }],
    details: {
      total,
      returned,
      remaining,
      candidates: batch.map((c) => ({
        score: c.score,
        name: c.name,
        kind: c.kindName,
        container: c.container,
        file: c.file,
        line: c.line,
        snippet: c.snippet,
      })),
      cache_key: cacheKey,
    },
  };
}

function indent(text: string, prefix: string): string {
  return text
    .split("\n")
    .map((l) => prefix + l)
    .join("\n");
}
