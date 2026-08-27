import { EventEmitter } from "node:events";
import { afterEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => {
  const rpc = {
    request: vi.fn((method: string) => {
      if (method === "initialize") {
        return Promise.resolve({ capabilities: { workspaceSymbolProvider: true } });
      }
      return new Promise(() => {});
    }),
    notify: vi.fn(),
    onNotification: vi.fn(),
    onAnyNotification: vi.fn(),
    onRequest: vi.fn(),
    close: vi.fn(),
  };
  return { rpc, proc: undefined as any, spawn: vi.fn() };
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

import { PyrightClient } from "../src/lsp/client.js";

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe("PyrightClient shutdown", () => {
  it("starts Pyright with a 4 GiB Node heap", async () => {
    const client = new PyrightClient({ workspaceDir: "/repo" });
    await client.whenReady();

    expect(mocks.spawn).toHaveBeenCalledWith(
      "node",
      ["/fake/pyright-langserver.js", "--stdio"],
      expect.objectContaining({
        env: expect.objectContaining({
          NODE_OPTIONS: expect.stringContaining("--max-old-space-size=4096"),
        }),
      }),
    );

    await client.shutdown();
  });

  it("falls back to SIGTERM when the LSP shutdown request never replies", async () => {
    vi.useFakeTimers();
    const client = new PyrightClient({ workspaceDir: "/repo" });
    await client.whenReady();

    const shutdown = client.shutdown();
    await vi.advanceTimersByTimeAsync(1000);
    await shutdown;

    expect(mocks.proc.kill).toHaveBeenCalledWith("SIGTERM");
    expect(mocks.rpc.close).toHaveBeenCalledOnce();
    expect(client.isDead).toBe(true);
  });
});
