import type { PlainSymbol } from "../lsp/types.js";

export function rankCandidates(
  candidates: PlainSymbol[],
  params: { query: string; context?: string },
): PlainSymbol[] {
  const query = params.query.toLowerCase();
  const queryBare = query.replace(/^_+/, "");
  const contextTokens = tokens(params.context ?? "");

  return candidates
    .map((candidate, index) => ({
      candidate,
      index,
      score:
        nameScore(candidate, query, queryBare) +
        contextScore(candidate, contextTokens) +
        pathPrior(candidate, contextTokens),
    }))
    .sort((a, b) => b.score - a.score || a.index - b.index)
    .map(({ candidate }) => candidate);
}

function nameScore(candidate: PlainSymbol, query: string, queryBare: string): number {
  const name = candidate.name.toLowerCase();
  const bare = name.replace(/^_+/, "");
  const container = candidate.container?.toLowerCase();
  if (name === query || bare === queryBare || container === query) return 100;
  if (name.endsWith(`_${query}`) || bare.endsWith(queryBare)) return 65;
  if (name.startsWith(`${query}_`) || bare.startsWith(queryBare)) return 55;
  return name.includes(query) || bare.includes(queryBare) ? 20 : 0;
}

function contextScore(candidate: PlainSymbol, contextTokens: string[]): number {
  const candidateTokens = tokens(
    `${candidate.file} ${candidate.container ?? ""} ${candidate.name}`,
  );
  let score = 0;
  for (const token of contextTokens) {
    if (candidateTokens.some((candidateToken) => related(token, candidateToken))) score += 12;
  }
  return Math.min(score, 48);
}

function pathPrior(candidate: PlainSymbol, contextTokens: string[]): number {
  const file = candidate.file.toLowerCase();
  const testContext = contextTokens.some((token) => token === "test" || token === "regression");
  if (!testContext && (file.includes("/tests/") || /(^|[/_])test_/.test(file))) return -50;
  if (!testContext && (file.includes("/docs/") || file.includes("/examples/"))) return -25;
  return 0;
}

function tokens(value: string): string[] {
  const expanded = value
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .toLowerCase()
    .replace(/polynomial/g, "polynomial poly")
    .replace(/expression/g, "expression expr")
    .split(/[^a-z0-9]+/)
    .filter((token) => token.length >= 3);
  return [...new Set(expanded)];
}

function related(a: string, b: string): boolean {
  return a === b || a.startsWith(b) || b.startsWith(a);
}
