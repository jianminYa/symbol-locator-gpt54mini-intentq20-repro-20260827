// WorkspacePool unit tests — fake PyrightClient stub.
import { beforeEach, describe, expect, it, vi } from "vitest";

const spawnLog: string[] = [];
const spawnedClients: any[] = [];

// Minimal event emitter — avoids EventEmitter + vi.mock hoisting conflict.
function makeEmitter() {
  const handlers = new Map<string, Array<() => void>>();
  return {
    on(event: string, fn: () => void) {
      const list = handlers.get(event) ?? [];
      list.push(fn);
      handlers.set(event, list);
    },
    emit(event: string) {
      handlers.get(event)?.forEach((fn) => fn());
    },
  };
}

vi.mock("../src/lsp/client.js", () => {
  function FakePyrightClient(opts: { workspaceDir: string; initTimeoutMs?: number }) {
    const emitter = makeEmitter();
    let ready = false;
    spawnLog.push(`spawn:${opts.workspaceDir}`);
    const client = {
      workspaceDir: opts.workspaceDir,
      isDead: false,
      shutdownCalled: false,
      on: emitter.on,
      whenReady(): Promise<void> {
        if (ready) return Promise.resolve();
        return new Promise((r) => setTimeout(() => { ready = true; r(); }, 5));
      },
      shutdown(): Promise<void> {
        client.shutdownCalled = true;
        client.isDead = true;
        emitter.emit("die");
        return Promise.resolve();
      },
      simulateDeath(): void {
        client.isDead = true;
        emitter.emit("die");
      },
    };
    spawnedClients.push(client);
    return client;
  }
  return { PyrightClient: FakePyrightClient };
});

import { WorkspacePool } from "../src/lsp/manager.js";

beforeEach(() => {
  spawnLog.length = 0;
  spawnedClients.length = 0;
});

describe("WorkspacePool", () => {
  it("spawns once and returns same client for repeat getClient", async () => {
    const pool = new WorkspacePool({ maxWorkspaces: 4 });
    const c1 = await pool.getClient("/tmp/foo");
    const c2 = await pool.getClient("/tmp/foo");
    expect(c1).toBe(c2);
    expect(spawnLog).toEqual(["spawn:/tmp/foo"]);
    await pool.shutdownAll();
  });

  it("concurrent getClient calls share single spawn (singleton-init)", async () => {
    const pool = new WorkspacePool({ maxWorkspaces: 4 });
    const promises = Array.from({ length: 20 }, () => pool.getClient("/tmp/foo"));
    const clients = await Promise.all(promises);
    expect(new Set(clients).size).toBe(1);
    expect(spawnLog.filter((s) => s.includes("/tmp/foo")).length).toBe(1);
    await pool.shutdownAll();
  });

  it("different workspaces get different clients", async () => {
    const pool = new WorkspacePool({ maxWorkspaces: 4 });
    const a = await pool.getClient("/tmp/a");
    const b = await pool.getClient("/tmp/b");
    expect(a).not.toBe(b);
    expect(pool.size).toBe(2);
    await pool.shutdownAll();
  });

  it("same workspace but different sessions get different clients", async () => {
    const pool = new WorkspacePool({ maxWorkspaces: 4 });
    const a = await pool.getClient("/tmp/shared", "session-1");
    const b = await pool.getClient("/tmp/shared", "session-2");
    expect(a).not.toBe(b);
    expect(pool.size).toBe(2);

    await pool.shutdownWorkspace("/tmp/shared", "session-1");
    expect((a as any).shutdownCalled).toBe(true);
    expect((b as any).shutdownCalled).toBe(false);
    expect(pool.size).toBe(1);
    await pool.shutdownAll();
  });

  it("evicts LRU when maxWorkspaces exceeded", async () => {
    const pool = new WorkspacePool({ maxWorkspaces: 2 });
    await pool.getClient("/tmp/a");
    const clientA = spawnedClients[spawnedClients.length - 1];
    await new Promise((r) => setTimeout(r, 15));
    await pool.getClient("/tmp/b");
    await new Promise((r) => setTimeout(r, 15));
    await pool.getClient("/tmp/c");
    expect(pool.size).toBe(2);
    expect(clientA.shutdownCalled).toBe(true);
    await pool.shutdownAll();
  });

  it("dead client auto-removed from pool", async () => {
    const pool = new WorkspacePool({ maxWorkspaces: 4 });
    const c = (await pool.getClient("/tmp/foo")) as any;
    expect(pool.size).toBe(1);
    c.simulateDeath();
    const c2 = await pool.getClient("/tmp/foo");
    expect(c2).not.toBe(c);
    expect(spawnLog.length).toBe(2);
    await pool.shutdownAll();
  });

  it("shutdownAll kills all clients and empties pool", async () => {
    const pool = new WorkspacePool({ maxWorkspaces: 4 });
    await pool.getClient("/tmp/a");
    await pool.getClient("/tmp/b");
    expect(pool.size).toBe(2);
    await pool.shutdownAll();
    expect(pool.size).toBe(0);
    for (const c of spawnedClients) {
      expect(c.shutdownCalled).toBe(true);
    }
  });

  it("shutdownWorkspace only kills the requested workspace", async () => {
    const pool = new WorkspacePool({ maxWorkspaces: 4 });
    const a = (await pool.getClient("/tmp/a")) as any;
    const b = (await pool.getClient("/tmp/b")) as any;

    await pool.shutdownWorkspace("/tmp/a");

    expect(a.shutdownCalled).toBe(true);
    expect(b.shutdownCalled).toBe(false);
    expect(pool.size).toBe(1);
    await pool.shutdownAll();
  });

  it("waits for workspace shutdown before creating a replacement client", async () => {
    const pool = new WorkspacePool({ maxWorkspaces: 4 });
    const first = (await pool.getClient("/tmp/a")) as any;
    let finishShutdown!: () => void;
    first.shutdown = vi.fn(
      () => new Promise<void>((resolve) => {
        finishShutdown = () => {
          first.shutdownCalled = true;
          first.isDead = true;
          resolve();
        };
      }),
    );

    const closing = pool.shutdownWorkspace("/tmp/a");
    const replacement = pool.getClient("/tmp/a");
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(spawnLog).toEqual(["spawn:/tmp/a"]);

    finishShutdown();
    await closing;
    const second = await replacement;
    expect(second).not.toBe(first);
    expect(spawnLog).toEqual(["spawn:/tmp/a", "spawn:/tmp/a"]);
    await pool.shutdownAll();
  });

  it("idle sweep shuts down stale clients", async () => {
    const pool = new WorkspacePool({ maxWorkspaces: 4, idleTimeoutMs: 10 });
    await pool.getClient("/tmp/a");
    await new Promise((r) => setTimeout(r, 50));
    pool.sweep();
    expect(pool.size).toBe(0);
    for (const c of spawnedClients) {
      expect(c.shutdownCalled).toBe(true);
    }
    await pool.shutdownAll();
  });

  it("sweep leaves recently-used clients alone", async () => {
    const pool = new WorkspacePool({ maxWorkspaces: 4, idleTimeoutMs: 10_000 });
    await pool.getClient("/tmp/a");
    pool.sweep();
    expect(pool.size).toBe(1);
    await pool.shutdownAll();
  });
});
