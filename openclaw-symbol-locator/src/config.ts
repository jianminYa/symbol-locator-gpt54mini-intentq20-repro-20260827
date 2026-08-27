// Symbol Locator — TypeBox config schema + resolvers.
import { Type } from "typebox";

// --- defaults ---

const DEFAULT_SCORER_CONCURRENCY = 10;
const DEFAULT_SCORER_THRESHOLD = 75;
const DEFAULT_SCORER_SNIPPET_LINES = 15;
const DEFAULT_LSP_MAX_WORKSPACES = 4;
const DEFAULT_LSP_IDLE_TIMEOUT_MS = 30 * 60 * 1000;
const DEFAULT_LSP_INIT_TIMEOUT_MS = 60_000;
const DEFAULT_LSP_QUERY_TIMEOUT_MS = 60_000;
const DEFAULT_CACHE_MAX_ENTRIES = 256;
const DEFAULT_CACHE_TTL_MS = 30 * 60 * 1000;

// --- TypeBox schema ---

export const symbolLocatorPluginConfigSchema = Type.Object(
  {
    scorer: Type.Optional(
      Type.Object({
        model: Type.Optional(Type.String({ description: "打分模型 (provider/model)。" })),
        concurrency: Type.Optional(
          Type.Number({ default: DEFAULT_SCORER_CONCURRENCY }),
        ),
        threshold: Type.Optional(
          Type.Number({ default: DEFAULT_SCORER_THRESHOLD }),
        ),
        snippetLines: Type.Optional(
          Type.Number({ default: DEFAULT_SCORER_SNIPPET_LINES }),
        ),
      }),
    ),
    scorerLlm: Type.Optional(
      Type.Object({
        enabled: Type.Optional(Type.Boolean({ default: false })),
        baseUrl: Type.Optional(
          Type.String({ default: "https://api.openai.com/v1" }),
        ),
        apiKey: Type.Optional(Type.String()),
        model: Type.Optional(Type.String({ default: "gpt-4o-mini" })),
        timeoutMs: Type.Optional(Type.Number({ default: 30_000 })),
      }),
    ),
    lsp: Type.Optional(
      Type.Object({
        maxWorkspaces: Type.Optional(
          Type.Number({ default: DEFAULT_LSP_MAX_WORKSPACES }),
        ),
        idleTimeoutMs: Type.Optional(
          Type.Number({ default: DEFAULT_LSP_IDLE_TIMEOUT_MS }),
        ),
        initTimeoutMs: Type.Optional(
          Type.Number({ default: DEFAULT_LSP_INIT_TIMEOUT_MS }),
        ),
        queryTimeoutMs: Type.Optional(
          Type.Number({ default: DEFAULT_LSP_QUERY_TIMEOUT_MS }),
        ),
      }),
    ),
    cache: Type.Optional(
      Type.Object({
        maxEntries: Type.Optional(
          Type.Number({ default: DEFAULT_CACHE_MAX_ENTRIES }),
        ),
        ttlMs: Type.Optional(
          Type.Number({ default: DEFAULT_CACHE_TTL_MS }),
        ),
      }),
    ),
  },
  { additionalProperties: false },
);

// --- typed config structures ---

export type ScorerConfig = {
  model?: string;
  concurrency: number;
  threshold: number;
  snippetLines: number;
};

export type ScorerLlmConfig = {
  enabled: boolean;
  baseUrl: string;
  apiKey?: string;
  model: string;
  timeoutMs: number;
};

export type LspConfig = {
  maxWorkspaces: number;
  idleTimeoutMs: number;
  initTimeoutMs: number;
  queryTimeoutMs: number;
};

export type CacheConfig = {
  maxEntries: number;
  ttlMs: number;
};

// --- resolvers (fill defaults) ---

export function resolveScorerConfig(
  config: Record<string, unknown> | undefined,
): ScorerConfig {
  const s = (config?.scorer ?? {}) as Record<string, unknown>;
  return {
    model: typeof s.model === "string" ? s.model : undefined,
    concurrency: safeNumber(s.concurrency, DEFAULT_SCORER_CONCURRENCY),
    threshold: safeNumber(s.threshold, DEFAULT_SCORER_THRESHOLD),
    snippetLines: safeNumber(s.snippetLines, DEFAULT_SCORER_SNIPPET_LINES),
  };
}

export function resolveScorerLlmConfig(
  config: Record<string, unknown> | undefined,
): ScorerLlmConfig | undefined {
  const s = (config?.scorerLlm ?? {}) as Record<string, unknown>;
  if (s.enabled !== true) return undefined;
  return {
    enabled: true,
    baseUrl: typeof s.baseUrl === "string" ? s.baseUrl : "https://api.openai.com/v1",
    apiKey: typeof s.apiKey === "string" ? s.apiKey : undefined,
    model: typeof s.model === "string" ? s.model : "gpt-4o-mini",
    timeoutMs: safeNumber(s.timeoutMs, 30_000),
  };
}

export function resolveLspConfig(
  config: Record<string, unknown> | undefined,
): LspConfig {
  const l = (config?.lsp ?? {}) as Record<string, unknown>;
  return {
    maxWorkspaces: safeNumber(l.maxWorkspaces, DEFAULT_LSP_MAX_WORKSPACES),
    idleTimeoutMs: safeNumber(l.idleTimeoutMs, DEFAULT_LSP_IDLE_TIMEOUT_MS),
    initTimeoutMs: safeNumber(l.initTimeoutMs, DEFAULT_LSP_INIT_TIMEOUT_MS),
    queryTimeoutMs: safeNumber(l.queryTimeoutMs, DEFAULT_LSP_QUERY_TIMEOUT_MS),
  };
}

export function resolveCacheConfig(
  config: Record<string, unknown> | undefined,
): CacheConfig {
  const c = (config?.cache ?? {}) as Record<string, unknown>;
  return {
    maxEntries: safeNumber(c.maxEntries, DEFAULT_CACHE_MAX_ENTRIES),
    ttlMs: safeNumber(c.ttlMs, DEFAULT_CACHE_TTL_MS),
  };
}

function safeNumber(v: unknown, fallback: number): number {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  return fallback;
}
