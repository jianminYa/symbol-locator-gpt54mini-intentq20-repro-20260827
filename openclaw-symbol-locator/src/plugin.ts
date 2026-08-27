// Symbol Locator — plugin registration. Wires pool, cache, tools, and lifecycle.
import type { OpenClawPluginApi } from "../api.js";
import {
  resolveCacheConfig,
  resolveLspConfig,
  resolveScorerConfig,
  resolveScorerLlmConfig,
} from "./config.js";
import { WorkspacePool } from "./lsp/manager.js";
import { CandidateCache } from "./cache/cache.js";
import { createFindSymbolTool } from "./tools/find-symbol.js";
import { createMoreSymbolsTool } from "./tools/more-symbols.js";
import { createResetSymbolsTool } from "./tools/reset-symbols.js";
import { createWarmupHandler } from "./warmup-route.js";

const SYMBOL_LOOKUP_GUIDANCE =
  "For Python symbol lookup, `find_symbol` is best for ambiguous or common names " +
  "where grep would return many false hits — e.g. `save`, `_subs`, `expand`, `get`. " +
  "For a unique/rare name, filename, regex, free text, or non-Python code, grep is fine. " +
  "When top candidates don't match, call `more_symbols` (same name + context) to page " +
  "through the rest — cheap, no LSP re-query. " +
  "On `query_timeout` from `find_symbol`, switch to grep immediately.";

export function registerSymbolLocatorPlugin(api: OpenClawPluginApi): void {
  const startupConfig = (api.pluginConfig as Record<string, unknown>) ?? {};
  const scorerConfig = resolveScorerConfig(startupConfig);
  const independentScorer = resolveScorerLlmConfig(startupConfig);

  api.logger.info(
    `symbol-locator: scorer=${independentScorer ? `independent:${independentScorer.model}` : "host-agent-model"}, ` +
      `lsp maxWorkspaces=${resolveLspConfig(startupConfig).maxWorkspaces}, ` +
      `cache maxEntries=${resolveCacheConfig(startupConfig).maxEntries}`,
  );
  if (!independentScorer && scorerConfig.model) {
    api.logger.warn?.(
      "symbol-locator: scorer.model is ignored when scorerLlm.enabled=false; host scoring uses the agent model.",
    );
  }

  const lspCfg = resolveLspConfig(startupConfig);
  const pool = new WorkspacePool({
    maxWorkspaces: lspCfg.maxWorkspaces,
    idleTimeoutMs: lspCfg.idleTimeoutMs,
    initTimeoutMs: lspCfg.initTimeoutMs,
    queryTimeoutMs: lspCfg.queryTimeoutMs,
    // ponytail: route pyright/timeout logs through info so the platform's
    // structured `[sl-diag]` collector catches them (debug is dropped).
    onLog: (m) => {
      if (m.startsWith("[sl-diag]")) api.logger.info?.(m);
      else api.logger.debug?.(m);
    },
  });

  const cache = new CandidateCache({
    maxEntries: resolveCacheConfig(startupConfig).maxEntries,
    ttlMs: resolveCacheConfig(startupConfig).ttlMs,
  });
  // runId -> { workspaceDir, sessionId }. Shutdown targets the (dir, session)
  // pair used by tools so we tear down that PyrightClient, not a sibling
  // session's client that happens to share the workspace.
  const runWorkspaces = new Map<string, { workspaceDir: string; sessionId?: string }>();

  api.registerHttpRoute({
    path: "/api/v1/symbol-locator/warmup",
    auth: "gateway",
    match: "exact",
    handler: createWarmupHandler(),
  });

  api.on("before_prompt_build", () => ({
    appendSystemContext: SYMBOL_LOOKUP_GUIDANCE,
  }));

  api.on("before_agent_run", (_event, ctx) => {
    const runId = ctx.runId;
    const workspaceDir = ctx.workspaceDir;
    if (runId && workspaceDir && !runWorkspaces.has(runId)) {
      runWorkspaces.set(runId, { workspaceDir, sessionId: ctx.sessionId ?? ctx.sessionKey });
    }
    return { outcome: "pass" };
  });

  api.on("agent_end", async (event, ctx) => {
    const runId = event.runId ?? ctx.runId;
    if (!runId) return;
    const entry = runWorkspaces.get(runId);
    if (!entry) return;

    runWorkspaces.delete(runId);
    // Sibling run using the same (dir, session)? Skip shutdown.
    for (const other of runWorkspaces.values()) {
      if (other.workspaceDir === entry.workspaceDir && other.sessionId === entry.sessionId) return;
    }

    await pool.shutdownWorkspace(entry.workspaceDir, entry.sessionId);
  });

  // Per-tool-call factory — grants access to ctx.workspaceDir for cache keys.
  api.registerTool(
    (ctx) =>
      createFindSymbolTool({
        api,
        pool,
        cache,
        resolveConfig: () => api.pluginConfig as Record<string, unknown> ?? {},
        resolveWorkspaceDir: () => ctx.workspaceDir ?? process.cwd(),
        resolveSessionId: () => ctx.sessionId ?? ctx.sessionKey,
      }),
    { name: "find_symbol" },
  );

  api.registerTool(
    (ctx) =>
      createMoreSymbolsTool({
        api,
        pool,
        cache,
        resolveConfig: () => (api.pluginConfig as Record<string, unknown>) ?? {},
        resolveWorkspaceDir: () => ctx.workspaceDir ?? process.cwd(),
        resolveSessionId: () => ctx.sessionId ?? ctx.sessionKey,
      }),
    { name: "more_symbols" },
  );

  api.registerTool(() => createResetSymbolsTool(cache), { name: "reset_symbols" });

  api.lifecycle.registerRuntimeLifecycle({
    id: "symbol-locator/pool",
    description: "Shut down pyright workers (SIGTERM → SIGKILL).",
    cleanup: async () => {
      await pool.shutdownAll();
    },
  });
}
