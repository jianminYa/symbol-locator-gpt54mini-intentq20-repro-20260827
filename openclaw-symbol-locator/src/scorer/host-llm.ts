// Symbol Locator — score one candidate via the openclaw host LLM.
import {
  CODE_SCORER_SYSTEM_PROMPT,
  buildBatchUserPrompt,
  buildUserPrompt,
  parseBatchScores,
  parseScore,
} from "./prompt.js";
import { emitDiagnostic, type DiagnosticSink } from "../diagnostics.js";

// Minimal shape of api.runtime.llm — kept structural so tests can inject.
type LlmCompleteHandle = {
  complete: (params: {
    messages: { role: "system" | "user" | "assistant"; content: string }[];
    systemPrompt?: string;
    model?: string;
    maxTokens?: number;
    temperature?: number;
    signal?: AbortSignal;
    purpose?: string;
  }) => Promise<{
    text: string;
    usage?: {
      inputTokens?: number;
      outputTokens?: number;
      totalTokens?: number;
    };
  }>;
};

export type ScoreCandidateInput = {
  file: string;
  line: number;
  container?: string;
  snippet: string;
};

export type HostLlmScorerParams = {
  llm: LlmCompleteHandle;
  candidate: ScoreCandidateInput;
  context?: string;
  model?: string;
  signal?: AbortSignal;
  diag?: DiagnosticSink;
};

/**
 * Score a single candidate through the host LLM. Throws if the LLM errors or
 * returns text we cannot parse — caller decides whether to fall back.
 */
export async function scoreWithHostLlm(params: HostLlmScorerParams): Promise<number> {
  const { llm, candidate, context, model, signal, diag } = params;
  const userPrompt = buildUserPrompt(
    context,
    candidate.file,
    candidate.line,
    candidate.container,
    candidate.snippet,
  );
  // ponytail: do NOT pass `model` — openclaw host rejects Plugin LLM model
  // overrides ("cannot override the target model"). The independent-LLM path
  // is the correct way for users who want a dedicated scorer model.
  const result = await llm.complete({
    messages: [{ role: "user", content: userPrompt }],
    systemPrompt: CODE_SCORER_SYSTEM_PROMPT,
    maxTokens: 2048,
    temperature: 0,
    signal,
    purpose: "symbol-locator.score",
  });
  const usage = result.usage ?? {};
  emitDiagnostic(
    diag,
    `scorer-usage prompt=${usage.inputTokens ?? "?"} ` +
      `completion=${usage.outputTokens ?? "?"} total=${usage.totalTokens ?? "?"}`,
  );
  const score = parseScore(result.text);
  if (score === null) {
    throw new Error(`host LLM returned unparseable score: ${JSON.stringify(result.text)}`);
  }
  return score;
}

/** Score a batch through OpenClaw's host LLM using the shared batch prompt. */
export async function scoreBatchWithHostLlm(params: {
  llm: LlmCompleteHandle;
  candidates: ScoreCandidateInput[];
  context?: string;
  signal?: AbortSignal;
  diag?: DiagnosticSink;
}): Promise<Array<number | null>> {
  const { llm, candidates, context, signal, diag } = params;
  if (candidates.length === 0) return [];

  const result = await llm.complete({
    messages: [{ role: "user", content: buildBatchUserPrompt(context, candidates) }],
    systemPrompt: CODE_SCORER_SYSTEM_PROMPT,
    maxTokens: 2048,
    temperature: 0,
    signal,
    purpose: "symbol-locator.score",
  });
  const usage = result.usage ?? {};
  emitDiagnostic(
    diag,
    `scorer-batch-usage prompt=${usage.inputTokens ?? "?"} ` +
      `completion=${usage.outputTokens ?? "?"} total=${usage.totalTokens ?? "?"}`,
  );
  return parseBatchScores(result.text, candidates.length);
}
