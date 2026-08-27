import { describe, expect, it, vi } from "vitest";

const poolInstances = vi.hoisted(() => [] as Array<{
  shutdownAll: ReturnType<typeof vi.fn>;
  shutdownWorkspace: ReturnType<typeof vi.fn>;
}>);

vi.mock("../src/lsp/manager.js", () => ({
  WorkspacePool: class {
    shutdownAll = vi.fn(async () => undefined);
    shutdownWorkspace = vi.fn(async () => undefined);

    constructor() {
      poolInstances.push(this);
    }
  },
}));

import { registerSymbolLocatorPlugin } from "../src/plugin.js";

function registerPlugin() {
  const hooks = new Map<string, (...args: any[]) => unknown>();
  let runtimeCleanup: (() => Promise<void>) | undefined;
  const api = {
    pluginConfig: {},
    logger: { info: vi.fn(), debug: vi.fn() },
    registerTool: vi.fn(),
    registerHttpRoute: vi.fn(),
    on: vi.fn((name: string, handler: (...args: any[]) => unknown) => {
      hooks.set(name, handler);
    }),
    lifecycle: {
      registerRuntimeLifecycle: vi.fn((registration: { cleanup?: () => Promise<void> }) => {
        runtimeCleanup = registration.cleanup;
      }),
    },
  };

  registerSymbolLocatorPlugin(api as any);
  return {
    hooks,
    pool: poolInstances.at(-1)!,
    httpRoutes: api.registerHttpRoute,
    runtimeCleanup: () => runtimeCleanup?.(),
  };
}

describe("symbol locator agent lifecycle", () => {
  it("registers an authenticated warmup route", () => {
    const { httpRoutes } = registerPlugin();
    expect(httpRoutes).toHaveBeenCalledWith(
      expect.objectContaining({
        path: "/api/v1/symbol-locator/warmup",
        auth: "gateway",
        match: "exact",
      }),
    );
  });

  it("adds system guidance that prefers find_symbol but permits grep fallback", async () => {
    const { hooks } = registerPlugin();
    const beforePromptBuild = hooks.get("before_prompt_build");

    expect(beforePromptBuild).toBeTypeOf("function");
    const result = beforePromptBuild?.({}, {}) as { appendSystemContext: string };
    expect(result.appendSystemContext).toMatch(/ambiguous or common names/i);
    expect(result.appendSystemContext).toMatch(/call `more_symbols`/i);
    expect(result.appendSystemContext).toMatch(/unique\/rare name/i);
    expect(result.appendSystemContext).toMatch(/query_timeout/);
  });

  it("closes a workspace after its last concurrent agent run ends", async () => {
    const { hooks, pool } = registerPlugin();
    const beforeRun = hooks.get("before_agent_run");
    const agentEnd = hooks.get("agent_end");

    expect(beforeRun).toBeTypeOf("function");
    expect(agentEnd).toBeTypeOf("function");

    await beforeRun?.({}, { runId: "run-a", workspaceDir: "/repo", sessionId: "s1" });
    await beforeRun?.({}, { runId: "run-b", workspaceDir: "/repo", sessionId: "s1" });
    await agentEnd?.({ runId: "run-a" }, { workspaceDir: "/repo", sessionId: "s1" });
    expect(pool.shutdownWorkspace).not.toHaveBeenCalled();

    await agentEnd?.({ runId: "run-b" }, { workspaceDir: "/repo", sessionId: "s1" });
    expect(pool.shutdownWorkspace).toHaveBeenCalledOnce();
    expect(pool.shutdownWorkspace).toHaveBeenCalledWith("/repo", "s1");
  });

  it("isolates concurrent sessions sharing a workspace", async () => {
    const { hooks, pool } = registerPlugin();
    const beforeRun = hooks.get("before_agent_run");
    const agentEnd = hooks.get("agent_end");

    await beforeRun?.({}, { runId: "run-a", workspaceDir: "/repo", sessionId: "s1" });
    await beforeRun?.({}, { runId: "run-b", workspaceDir: "/repo", sessionId: "s2" });

    await agentEnd?.({ runId: "run-a" }, { workspaceDir: "/repo", sessionId: "s1" });
    expect(pool.shutdownWorkspace).toHaveBeenCalledWith("/repo", "s1");

    await agentEnd?.({ runId: "run-b" }, { workspaceDir: "/repo", sessionId: "s2" });
    expect(pool.shutdownWorkspace).toHaveBeenCalledWith("/repo", "s2");
    expect(pool.shutdownWorkspace).toHaveBeenCalledTimes(2);
  });

  it("still closes every worker during plugin runtime cleanup", async () => {
    const { pool, runtimeCleanup } = registerPlugin();
    await runtimeCleanup();
    expect(pool.shutdownAll).toHaveBeenCalledOnce();
  });
});
