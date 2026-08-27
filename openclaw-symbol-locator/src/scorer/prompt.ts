// Symbol Locator — scorer prompt (adapted from OrcaLoca code_scorer.py).

export const CODE_SCORER_SYSTEM_PROMPT =
  "You are a Python code expert. Your job is to score how likely a piece of code " +
  "is what the user is looking for, given their current task context.";

export function buildUserPrompt(
  context: string | undefined,
  file: string,
  line: number,
  container: string | undefined,
  snippet: string,
): string {
  const parts: string[] = [];
  if (context) {
    parts.push(`<task_context>${context}</task_context>`);
  }
  parts.push("<candidate>");
  parts.push(`file: ${file}:${line}`);
  parts.push(`container: ${container ?? "(module)"}`);
  parts.push("code:");
  parts.push(snippet);
  parts.push("</candidate>");
  parts.push(
    "Please score how likely this piece of code is the one the user needs. " +
      "Score 0-100, higher = more likely. Output ONLY a single integer.",
  );
  return parts.join("\n");
}

/**
 * Parse an LLM text response into a 0-100 integer score.
 * Handles: "92", " 92\n", "Score: 92", "I'd rate this 92 out of 100".
 * Returns null when no parseable score is found.
 */
export function parseScore(text: string): number | null {
  const trimmed = text.trim();

  // Ideal: a bare integer
  const direct = Number.parseInt(trimmed, 10);
  if (Number.isInteger(direct) && direct >= 0 && direct <= 100) return direct;

  // Fallback: first 1-3 digit number ≤ 100
  const match = trimmed.match(/\b(\d{1,3})\b/);
  if (match) {
    const n = Number.parseInt(match[1]!, 10);
    if (n >= 0 && n <= 100) return n;
  }

  return null;
}

/**
 * Batch scoring prompt — send N candidates in one call. Output format is one
 * "INDEX=SCORE" line per candidate; loose parsing tolerates surrounding chatter.
 * ponytail: line format is easier to parse than JSON when LLM emits stray text.
 */
export function buildBatchUserPrompt(
  context: string | undefined,
  candidates: Array<{
    file: string;
    line: number;
    container?: string;
    snippet: string;
  }>,
): string {
  const parts: string[] = [];
  if (context) {
    parts.push(`<task_context>${context}</task_context>`);
  }
  parts.push(`You will score ${candidates.length} candidates below.`);
  for (let i = 0; i < candidates.length; i++) {
    const c = candidates[i]!;
    parts.push(`<candidate index="${i + 1}">`);
    parts.push(`file: ${c.file}:${c.line}`);
    parts.push(`container: ${c.container ?? "(module)"}`);
    parts.push("code:");
    parts.push(c.snippet);
    parts.push("</candidate>");
  }
  parts.push(
    `Score each candidate 0-100 (higher = more likely to be what the user needs).\n` +
      `Output ONE LINE PER CANDIDATE in the exact form INDEX=SCORE (no spaces around =).\n` +
      `Example:\n1=85\n2=30\n3=70\n` +
      `Emit every candidate 1..${candidates.length}. Output nothing else.`,
  );
  return parts.join("\n");
}

/**
 * Parse batch scorer output. Extracts every "INDEX=SCORE" line where score is
 * 0-100. Returns a length-`expectedCount` array; missing indices get `null`
 * (caller decides fallback — usually 50 neutral).
 */
export function parseBatchScores(
  text: string,
  expectedCount: number,
): Array<number | null> {
  const out: Array<number | null> = new Array(expectedCount).fill(null);
  const re = /^\s*(\d+)\s*=\s*(\d{1,3})\s*$/gm;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    const idx = Number.parseInt(m[1]!, 10) - 1;
    const score = Number.parseInt(m[2]!, 10);
    if (idx >= 0 && idx < expectedCount && score >= 0 && score <= 100) {
      out[idx] = score;
    }
  }
  return out;
}
