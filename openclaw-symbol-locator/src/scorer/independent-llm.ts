// Symbol Locator — score candidates through an OpenAI-compatible endpoint.
// Native fetch + AbortController; no SDK.
import type { ScorerLlmConfig } from "../config.js";
import { emitDiagnostic, type DiagnosticSink } from "../diagnostics.js";
import {
  CODE_SCORER_SYSTEM_PROMPT,
  buildUserPrompt,
  buildBatchUserPrompt,
  parseScore,
  parseBatchScores,
} from "./prompt.js";
import type { ScoreCandidateInput } from "./host-llm.js";

export type IndependentLlmScorerParams = {
  cfg: ScorerLlmConfig;
  candidate: ScoreCandidateInput;
  context?: string;
  signal?: AbortSignal;
  diag?: DiagnosticSink;
};

/**
 * POST /chat/completions with a short prompt, parse the integer score out.
 * Independent of the host LLM — used when scorerLlm.enabled is true.
 */
export async function scoreWithIndependentLlm(
  params: IndependentLlmScorerParams,
): Promise<number> {
  const { cfg, candidate, context, signal, diag } = params;
  if (!cfg.apiKey) {
    throw new Error("independent LLM: apiKey missing");
  }
  const url = `${cfg.baseUrl.replace(/\/$/, "")}/chat/completions`;
  const userPrompt = buildUserPrompt(
    context,
    candidate.file,
    candidate.line,
    candidate.container,
    candidate.snippet,
  );
  // Own AbortController — chained to caller signal so external abort still fires.
  const controller = new AbortController();
  const onCallerAbort = () => controller.abort();
  signal?.addEventListener("abort", onCallerAbort, { once: true });
  const timer = setTimeout(() => controller.abort(), cfg.timeoutMs);

  let resp: Response;
  try {
    resp = await fetch(url, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${cfg.apiKey}`,
        "content-type": "application/json",
      },
      body: JSON.stringify({
        model: cfg.model,
        messages: [
          { role: "system", content: CODE_SCORER_SYSTEM_PROMPT },
          { role: "user", content: userPrompt },
        ],
        max_tokens: 2048,
        temperature: 0,
      }),
      signal: controller.signal,
    });
  } catch (e) {
    if ((e as { name?: string }).name === "AbortError") {
      throw new Error(
        signal?.aborted
          ? "independent LLM: aborted by caller"
          : `independent LLM: timeout after ${cfg.timeoutMs}ms`,
      );
    }
    throw new Error(`independent LLM: fetch failed: ${(e as Error).message}`);
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", onCallerAbort);
  }

  if (!resp.ok) {
    const body = await resp.text().catch(() => "");
    throw new Error(`independent LLM: HTTP ${resp.status} ${body.slice(0, 200)}`);
  }
  const json = (await resp.json()) as {
    choices?: { message?: { content?: string } }[];
    usage?: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number };
  };
  const usage = json.usage ?? {};
  emitDiagnostic(
    diag,
    `scorer-usage prompt=${usage.prompt_tokens ?? "?"} ` +
      `completion=${usage.completion_tokens ?? "?"} total=${usage.total_tokens ?? "?"}`,
  );
  const text = json.choices?.[0]?.message?.content ?? "";
  const score = parseScore(text);
  if (score === null) {
    emitDiagnostic(diag, `indep-llm parseScore failed file=${candidate.file}:${candidate.line}`);
    throw new Error(`independent LLM: unparseable score ${JSON.stringify(text)}`);
  }
  return score;
}

/**
 * Score a batch of candidates in one LLM call. Returns (index, score) pairs;
 * missing/unparseable entries get null score (caller falls back to neutral).
 */
export async function scoreBatchWithIndependentLlm(params: {
  cfg: ScorerLlmConfig;
  candidates: ScoreCandidateInput[];
  context?: string;
  signal?: AbortSignal;
  diag?: DiagnosticSink;
}): Promise<Array<number | null>> {
  const { cfg, candidates, context, signal, diag } = params;
  if (!cfg.apiKey) throw new Error("independent LLM: apiKey missing");
  if (candidates.length === 0) return [];

  const url = `${cfg.baseUrl.replace(/\/$/, "")}/chat/completions`;
  const userPrompt = buildBatchUserPrompt(context, candidates);

  emitDiagnostic(diag, `indep-llm batch size=${candidates.length}`);

  const controller = new AbortController();
  const onCallerAbort = () => controller.abort();
  signal?.addEventListener("abort", onCallerAbort, { once: true });
  const timer = setTimeout(() => controller.abort(), cfg.timeoutMs);

  let resp: Response;
  try {
    resp = await fetch(url, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${cfg.apiKey}`,
        "content-type": "application/json",
      },
      body: JSON.stringify({
        model: cfg.model,
        messages: [
          { role: "system", content: CODE_SCORER_SYSTEM_PROMPT },
          { role: "user", content: userPrompt },
        ],
        max_tokens: 2048,
        temperature: 0,
      }),
      signal: controller.signal,
    });
  } catch (e) {
    if ((e as { name?: string }).name === "AbortError") {
      throw new Error(
        signal?.aborted
          ? "independent LLM batch: aborted by caller"
          : `independent LLM batch: timeout after ${cfg.timeoutMs}ms`,
      );
    }
    throw new Error(`independent LLM batch: fetch failed: ${(e as Error).message}`);
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", onCallerAbort);
  }

  if (!resp.ok) {
    const body = await resp.text().catch(() => "");
    throw new Error(`independent LLM batch: HTTP ${resp.status} ${body.slice(0, 200)}`);
  }
  const json = (await resp.json()) as {
    choices?: { message?: { content?: string } }[];
    usage?: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number };
  };
  const usage = json.usage ?? {};
  emitDiagnostic(
    diag,
    `scorer-batch-usage prompt=${usage.prompt_tokens ?? "?"} ` +
      `completion=${usage.completion_tokens ?? "?"} total=${usage.total_tokens ?? "?"}`,
  );
  const text = json.choices?.[0]?.message?.content ?? "";
  const scores = parseBatchScores(text, candidates.length);
  const hitCount = scores.filter((s) => s !== null).length;
  emitDiagnostic(diag, `indep-llm batch parsed ${hitCount}/${candidates.length} scores`);
  return scores;
}
