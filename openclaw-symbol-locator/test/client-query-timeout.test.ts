// workspaceSymbol query timeout: a single stuck RPC must not wedge the
// client. On timeout we log [sl-diag] workspaceSymbol timeout, shut the
// client down (proc.kill("SIGTERM")), and throw WorkspaceSymbolTimeoutError.
// find-symbol then surfaces `query_timeout` to the Agent so it can fall
// back to grep/rg.
import { EventEmitter } from "node:events";
import { afterEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => {
  const wsResolvers: Array<(v: unknown) => void> = [];
  const rpc = {
    request: vi.fn((method: string) => {
      if (method === "initialize") {
        return Promise.resolve({ capabilities: { workspaceSymbolProvider: true } });
      }
      if (method === "workspace/symbol") {
        return new Promise((resolve) => {
          wsResolvers.push(resolve);
        });
      }
      return new Promise(() => {});
    }),
    notify: vi.fn(),
    onNotification: vi.fn(),
    onAnyNotification: vi.fn(),
    onRequest: vi.fn(),
    close: vi.fn(),
  };
  return { rpc, wsResolvers, proc: undefined as any, spawn: vi.fn() };
});

vi.mock("node:child_process", () => ({
  spawn: mocks.spawn.mockImplementation(() => {
    const proc = new EventEmitter() as any;
    proc.stdin = {};
    proc.stdout = {};
    proc.stderr = { on: vi.fn() };
    proc.kill = vi.fn((signal: NodeJS.Signals) => {
      if (signal === "SIGTERM") proc.emit("exit", null, signal);
      return true;
    });
    mocks.proc = proc;
    return proc;
  }),
}));

vi.mock("../src/lsp/protocol.js", () => ({
  createRpcClient: () => mocks.rpc,
}));

vi.mock("../src/lsp/pyright-bin.js", () => ({
  resolvePyrightLangserverBin: () => "/fake/pyright-langserver.js",
}));

import { PyrightClient, WorkspaceSymbolTimeoutError } from "../src/lsp/client.js";

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
  mocks.wsResolvers.length = 0;
});

describe("PyrightClient workspaceSymbol timeout", () => {
  it("times out a stuck query, logs diag, kills the process, throws structured error", async () => {
    vi.useFakeTimers();
    const logs: string[] = [];
    const client = new PyrightClient({
      workspaceDir: "/repo",
      queryTimeoutMs: 5_000,
      onLog: (m) => logs.push(m),
    });
    await client.whenReady();

    const q = client.workspaceSymbol("_subs");
    // Fire the query timer.
    await vi.advanceTimersByTimeAsync(5_001);
    await expect(q).rejects.toBeInstanceOf(WorkspaceSymbolTimeoutError);
    await expect(q).rejects.toMatchObject({ query: "_subs" });

    expect(logs.some((m) => m.startsWith("[sl-diag] workspaceSymbol timeout query=\"_subs\""))).toBe(
      true,
    );

    // shutdown() was fire-and-forgot; wait for its 1s shutdown-request race
    // to fall back to SIGTERM.
    await vi.advanceTimersByTimeAsync(1_000);
    await vi.advanceTimersByTimeAsync(10);
    expect(mocks.proc.kill).toHaveBeenCalledWith("SIGTERM");
    expect(client.isDead).toBe(true);
  });

  it("serializes concurrent queries — q2 does not hit rpc until q1 resolves", async () => {
    const client = new PyrightClient({
      workspaceDir: "/repo",
      queryTimeoutMs: 60_000,
    });
    await client.whenReady();

    // First call issues rpc synchronously after whenReady.
    const q1 = client.workspaceSymbol("first");
    // Second call must NOT issue rpc until q1 resolves.
    const q2 = client.workspaceSymbol("second");
    // Flush microtasks — enough for q1 to reach rpc but not more.
    for (let i = 0; i < 20; i++) await Promise.resolve();

    const wsCalls = mocks.rpc.request.mock.calls.filter((c) => c[0] === "workspace/symbol");
    expect(wsCalls).toEqual([["workspace/symbol", { query: "first" }]]);

    // Resolve q1 — q2 should now issue its rpc.
    mocks.wsResolvers[0]!([]);
    await expect(q1).resolves.toEqual([]);
    for (let i = 0; i < 20; i++) await Promise.resolve();
    const wsCalls2 = mocks.rpc.request.mock.calls.filter((c) => c[0] === "workspace/symbol");
    expect(wsCalls2).toEqual([
      ["workspace/symbol", { query: "first" }],
      ["workspace/symbol", { query: "second" }],
    ]);

    // Resolve q2 to keep the test tidy.
    mocks.wsResolvers[1]!([]);
    await expect(q2).resolves.toEqual([]);
  });
});
