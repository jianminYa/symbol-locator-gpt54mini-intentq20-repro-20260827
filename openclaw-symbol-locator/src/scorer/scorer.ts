// Symbol Locator — scorer pipeline: batch LLM scoring, threshold, sort.
import type { PlainSymbol } from "../lsp/types.js";
import type { PyrightClient } from "../lsp/client.js";
import { emitDiagnostic, type DiagnosticSink } from "../diagnostics.js";
import {
  scoreBatchWithHostLlm,
  scoreWithHostLlm,
  type HostLlmScorerParams,
} from "./host-llm.js";
import { scoreWithIndependentLlm, scoreBatchWithIndependentLlm } from "./independent-llm.js";
import { buildUserPrompt, parseScore } from "./prompt.js";
import { CODE_SCORER_SYSTEM_PROMPT } from "./prompt.js"; // re-export for tests

// Re-export for tool layer
export { CODE_SCORER_SYSTEM_PROMPT, buildUserPrompt, parseScore };
export { scoreWithHostLlm } from "./host-llm.js";
export { scoreWithIndependentLlm } from "./independent-llm.js";

export type ScoredCandidate = PlainSymbol & {
  score: number;
  snippet: string;
};

export type ScorePipelineParams = {
  candidates: PlainSymbol[];
  context?: string;
  workspaceClient: PyrightClient;
  /* Host LLM path */
  hostLlm?: HostLlmScorerParams["llm"];
  /* Independent LLM path */
  independentCfg?: {
    enabled: boolean;
    baseUrl: string;
    apiKey?: string;
    model: string;
    timeoutMs: number;
  };
  concurrency?: number; // batch concurrency for independent LLM path
  threshold?: number; // default 75
  snippetLines?: number; // default 15
  rescore?: boolean;
  signal?: AbortSignal;
  logger?: { warn?: (msg: string) => void; error?: (msg: string) => void };
  diag?: DiagnosticSink;
};

// ponytail: 100 candidates → ~20k tokens input per batch, one API call each.
const BATCH_SIZE = 100;

export async function scoreCandidates(
  params: ScorePipelineParams,
): Promise<ScoredCandidate[]> {
  const {
    candidates,
    context,
    workspaceClient,
    hostLlm,
    independentCfg,
    concurrency = 3,
    threshold = 75,
    snippetLines = 15,
    rescore,
    signal,
  } = params;

  // Bail out: ≤1 candidate OR caller says skip scoring
  if (candidates.length <= 1 || rescore === false) {
    return Promise.all(
      candidates.map(async (c) => ({
        ...c,
        score: 100,
        snippet: await workspaceClient.getSourceSnippet(c.file, c.line, snippetLines),
      })),
    );
  }

  // Pre-fetch all snippets
  const withSnippets = await Promise.all(
    candidates.map(async (c) => ({
      ...c,
      snippet: await workspaceClient.getSourceSnippet(c.file, c.line, snippetLines),
    })),
  );

  let scored: Array<{ symbol: typeof withSnippets[number]; score: number }> = [];

  if (independentCfg?.enabled) {
    // Batch path: chunk into BATCH_SIZE, score each chunk in one LLM call
    const chunks: Array<typeof withSnippets> = [];
    for (let i = 0; i < withSnippets.length; i += BATCH_SIZE) {
      chunks.push(withSnippets.slice(i, i + BATCH_SIZE));
    }

    emitDiagnostic(
      params.diag,
      `batch-scorer source=independent chunks=${chunks.length} total=${withSnippets.length} batchSize=${BATCH_SIZE}`,
    );

    const cfg = {
      enabled: true,
      baseUrl: independentCfg.baseUrl,
      apiKey: independentCfg.apiKey,
      model: independentCfg.model,
      timeoutMs: independentCfg.timeoutMs,
    };

    const gate = makeGate(concurrency);

    const chunkResults = await Promise.all(
      chunks.map((chunk) =>
        gate(async () => {
          try {
            const scores = await scoreBatchWithIndependentLlm({
              cfg,
              candidates: chunk.map((c) => ({
                file: c.file,
                line: c.line,
                container: c.container,
                snippet: c.snippet,
              })),
              context,
              signal,
              diag: params.diag,
            });
            return chunk.map((symbol, i) => ({
              symbol,
              score: scores[i] ?? 50,
            }));
          } catch (err) {
            const msg = err instanceof Error ? err.message : String(err);
            params.logger?.warn?.(`symbol-locator batch scorer failed for chunk: ${msg}`);
            return chunk.map((symbol) => ({ symbol, score: 50 }));
          }
        }),
      ),
    );
    scored = chunkResults.flat();
  } else if (hostLlm) {
    const chunks: Array<typeof withSnippets> = [];
    for (let i = 0; i < withSnippets.length; i += BATCH_SIZE) {
      chunks.push(withSnippets.slice(i, i + BATCH_SIZE));
    }

    emitDiagnostic(
      params.diag,
      `batch-scorer source=host chunks=${chunks.length} total=${withSnippets.length} batchSize=${BATCH_SIZE}`,
    );

    const gate = makeGate(concurrency);

    const chunkResults = await Promise.all(
      chunks.map((chunk) =>
        gate(async () => {
          try {
            const scores = await scoreBatchWithHostLlm({
              llm: hostLlm,
              candidates: chunk.map((c) => ({
                file: c.file,
                line: c.line,
                container: c.container,
                snippet: c.snippet,
              })),
              context,
              signal,
              diag: params.diag,
            });
            return chunk.map((symbol, i) => ({
              symbol,
              score: scores[i] ?? 50,
            }));
          } catch (err) {
            const msg = err instanceof Error ? err.message : String(err);
            params.logger?.warn?.(
              `symbol-locator host batch scorer failed for ${chunk.length} candidates, ` +
                `score=50 fallback: ${msg}`,
            );
            return chunk.map((symbol) => ({ symbol, score: 50 }));
          }
        }),
      ),
    );
    scored = chunkResults.flat();
  } else {
    throw new Error("no LLM configured for scoring");
  }

  // Filter below threshold, sort descending by score
  return scored
    .filter((c) => c.score >= threshold)
    .sort((a, b) => b.score - a.score)
    .map((c) => ({ ...c.symbol, score: c.score, snippet: c.symbol.snippet }));
}

// ponytail: 20-line semaphore — no p-limit dependency.
function makeGate(max: number) {
  let inflight = 0;
  const queue: Array<() => void> = [];
  return async function gate<T>(fn: () => Promise<T>): Promise<T> {
    if (inflight >= max) await new Promise<void>((r) => queue.push(r));
    inflight++;
    try {
      return await fn();
    } finally {
      inflight--;
      queue.shift()?.();
    }
  };
}
