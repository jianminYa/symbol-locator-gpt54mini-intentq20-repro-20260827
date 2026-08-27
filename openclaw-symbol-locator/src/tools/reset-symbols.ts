// Symbol Locator — reset_symbols tool (cache-only).
import { Type } from "typebox";
import type { Static } from "typebox";
import type { AnyAgentTool } from "../../api.js";
import type { CandidateCache } from "../cache/cache.js";

const ResetSymbolsSchema = Type.Object(
  {
    name: Type.Optional(
      Type.String({ description: "Clear cache for this symbol only. Omit to clear everything." }),
    ),
    confirm: Type.String({
      description: 'Must be the literal string "yes". Safety gate — prevents accidental cache clears.',
    }),
  },
  { additionalProperties: false },
);

type ResetSymbolsParams = Static<typeof ResetSymbolsSchema>;

export function createResetSymbolsTool(cache: CandidateCache): AnyAgentTool {
  return {
    name: "reset_symbols",
    label: "Reset Symbols",
    description:
      "Clear the symbol cache so the next `find_symbol` does a fresh LSP search + re-score. " +
      "Use when source files changed externally or cached results look stale.",
    parameters: ResetSymbolsSchema,
    execute: async (_toolCallId, rawParams) => {
      const params = rawParams as ResetSymbolsParams;
      if (params.confirm !== "yes") {
        return {
          content: [{ type: "text", text: 'Pass `confirm: "yes"` to clear the cache.' }],
          details: { cleared: false, reason: "confirm_required" },
        };
      }
      const before = cache.size;
      cache.clear(params.name);
      const after = cache.size;
      const cleared = before - after;
      return {
        content: [
          {
            type: "text",
            text: params.name
              ? `Cleared ${cleared} cache entr${cleared !== 1 ? "ies" : "y"} for \`${params.name}\`. ${after} remaining.`
              : `Cleared all ${cleared} cache entr${cleared !== 1 ? "ies" : "y"}.`,
          },
        ],
        details: { cleared, before, after, name: params.name ?? null },
      };
    },
  };
}
