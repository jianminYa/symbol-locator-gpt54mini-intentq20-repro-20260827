// Symbol Locator — more_symbols tool.
// Two branches:
//   1) scored page has leftovers → advance cursor, return next batch (cheap)
//   2) scored page exhausted + pending non-empty → consume top_k from pending,
//      attach snippet, return as rough-ranked (no LSP re-query, no LLM score)
import { Type } from "typebox";
import type { Static } from "typebox";
import type { AnyAgentTool, OpenClawPluginApi } from "../../api.js";
import type { CandidateCache } from "../cache/cache.js";
import type { WorkspacePool } from "../lsp/manager.js";
import { resolveScorerConfig } from "../config.js";

const MoreSymbolsSchema = Type.Object(
  {
    name: Type.String({ description: "Same symbol name as the previous find_symbol call." }),
    context: Type.Optional(
      Type.String({
        description:
          "Must match the context from the previous find_symbol call to hit the same cache entry.",
      }),
    ),
    count: Type.Optional(
      Type.Number({
        default: 3,
        minimum: 1,
        maximum: 10,
        description: "How many more candidates to return.",
      }),
    ),
  },
  { additionalProperties: false },
);

type MoreSymbolsParams = Static<typeof MoreSymbolsSchema>;

export type MoreSymbolsDeps = {
  cache: CandidateCache;
  pool: WorkspacePool;
  api: OpenClawPluginApi;
  resolveConfig: () => Record<string, unknown>;
  resolveWorkspaceDir?: () => string;
  resolveSessionId?: () => string | undefined;
};

export function createMoreSymbolsTool(deps: MoreSymbolsDeps): AnyAgentTool {
  const { cache, pool, resolveConfig } = deps;
  return {
    name: "more_symbols",
    label: "More Symbols",
    description:
      "Get the next batch of candidates from a previous `find_symbol` call. " +
      "Cheap — no LSP re-query. Returns LLM-scored batches while available, " +
      "then falls back to rough-ranked (rule-based) candidates from the same query.",
    parameters: MoreSymbolsSchema,
    execute: async (_toolCallId, rawParams) => {
      const params = rawParams as MoreSymbolsParams;
      const count = params.count ?? 3;
      const workspaceDir = deps.resolveWorkspaceDir?.() ?? process.cwd();
      const sessionId = deps.resolveSessionId?.();
      const cacheKey = cache.key(workspaceDir, params.name, params.context, sessionId);

      const entry = cache.get(cacheKey);
      if (!entry) {
        return {
          content: [
            {
              type: "text",
              text:
                `No cached result for \`${params.name}\`. ` +
                `Call \`find_symbol({ name: "${params.name}" })\` first.`,
            },
          ],
          details: {
            error: "cache_miss",
            message: `No cached result for "${params.name}". Call find_symbol first.`,
          },
        };
      }

      // Branch 1: scored page still has candidates
      const batch = cache.advance(cacheKey, count);
      if (batch.length > 0) {
        const remainingScored = entry.candidates.length - entry.cursor;
        const pendingLeft = entry.pending.length;
        return {
          content: [
            {
              type: "text",
              text:
                `${batch.length} more candidate${batch.length !== 1 ? "s" : ""} for \`${params.name}\`:\n\n` +
                batch
                  .map(
                    (c) =>
                      `[score=${c.score}] ${c.container ? c.container + "." : ""}${c.name}\n` +
                      `   ${c.file}:${c.line}\n` +
                      `${indent(c.snippet, "   ")}`,
                  )
                  .join("\n\n") +
                (remainingScored > 0
                  ? `\n\n${remainingScored} scored + ${pendingLeft} rough-ranked more cached. ` +
                    `Call \`more_symbols({ name: "${params.name}" })\` again.`
                  : pendingLeft > 0
                    ? `\n\n${pendingLeft} rough-ranked more cached. ` +
                      `Call \`more_symbols({ name: "${params.name}" })\` to see them.`
                    : ""),
            },
          ],
          details: {
            total: entry.candidates.length + pendingLeft,
            returned: batch.length,
            remaining: remainingScored + pendingLeft,
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

      // Branch 2: scored page exhausted; drain pending rough-ranked
      if (entry.pending.length === 0) {
        return {
          content: [
            {
              type: "text",
              text:
                `All ${entry.candidates.length} candidates for \`${params.name}\` have been returned. ` +
                `Consider grep/rg for a broader search.`,
            },
          ],
          details: {
            total: entry.candidates.length,
            returned: 0,
            remaining: 0,
            candidates: [],
            cache_key: cacheKey,
          },
        };
      }

      const scorerCfg = resolveScorerConfig(resolveConfig());
      const rough = cache.takePending(cacheKey, count);
      let client;
      try {
        client = await pool.getClient(workspaceDir, sessionId);
      } catch (err) {
        // Put pending back so a later retry still has them.
        entry.pending.unshift(...rough);
        return {
          content: [
            {
              type: "text",
              text:
                `Failed to attach snippets for rough-ranked candidates: ` +
                `${err instanceof Error ? err.message : String(err)}. ` +
                `Consider grep/rg instead.`,
            },
          ],
          details: {
            error: "pool_unavailable",
            candidates: [],
            cache_key: cacheKey,
          },
        };
      }

      const withSnippets = await Promise.all(
        rough.map(async (c) => ({
          ...c,
          snippet: await client
            .getSourceSnippet(c.file, c.line, scorerCfg.snippetLines)
            .catch(() => "[snippet unavailable]"),
        })),
      );
      const pendingLeft = entry.pending.length;

      return {
        content: [
          {
            type: "text",
            text:
              `${withSnippets.length} rough-ranked candidate${withSnippets.length !== 1 ? "s" : ""} ` +
              `for \`${params.name}\` (no LLM score, rule-based order only):\n\n` +
              withSnippets
                .map(
                  (c) =>
                    `[rough] ${c.container ? c.container + "." : ""}${c.name}\n` +
                    `   ${c.file}:${c.line}\n` +
                    `${indent(c.snippet, "   ")}`,
                )
                .join("\n\n") +
              (pendingLeft > 0
                ? `\n\n${pendingLeft} rough-ranked more cached. ` +
                  `Call \`more_symbols({ name: "${params.name}" })\` again, or switch to grep.`
                : `\n\nAll candidates exhausted. Consider grep/rg for a broader search.`),
          },
        ],
        details: {
          total: entry.candidates.length + pendingLeft + withSnippets.length,
          returned: withSnippets.length,
          remaining: pendingLeft,
          rough_ranked: true,
          candidates: withSnippets.map((c) => ({
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
    },
  };
}

function indent(text: string, prefix: string): string {
  return text
    .split("\n")
    .map((l) => prefix + l)
    .join("\n");
}
