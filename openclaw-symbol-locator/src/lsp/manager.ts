// WorkspacePool — mange PyrightClient instances per workspace directory.
// Key invariants:
// - first caller per workspace does spawn+init; concurrent callers await the
//   same in-flight promise (singleton-init pattern, no double-spawn)
// - LRU eviction when maxWorkspaces exceeded
// - agent lifecycle can close one workspace immediately; a background sweep
//   remains as a fallback for runs that do not emit agent_end
// - dead clients (emit "die") auto-removed from pool
// - shutdownAll() on plugin lifecycle cleanup, kills everything, resolves
//   only after each process actually exited
import { PyrightClient, type PyrightClientOptions } from "./client.js";

type PoolClient = {
  client: PyrightClient;
  workspaceDir: string; // absolute, normalized
  lastUsedAt: number;
};

interface WorkspacePoolOptions {
  maxWorkspaces?: number; // default 4
  idleTimeoutMs?: number; // default 30 min
  initTimeoutMs?: number; // default 60s
  queryTimeoutMs?: number; // default 60s — per workspace/symbol RPC
  onLog?: (message: string) => void;
}

export class WorkspacePool {
  private maxWorkspaces: number;
  private idleTimeoutMs: number;
  private initTimeoutMs: number;
  private queryTimeoutMs: number;
  private onLog?: (m: string) => void;
  private map = new Map<string, PoolClient>();
  private inflight = new Map<string, Promise<PyrightClient>>();
  private closing = new Map<string, Promise<void>>();
  private sweepTimer?: ReturnType<typeof setInterval>;
  private sweeping = false;

  constructor(opts: WorkspacePoolOptions = {}) {
    this.maxWorkspaces = opts.maxWorkspaces ?? 4;
    this.idleTimeoutMs = opts.idleTimeoutMs ?? 30 * 60 * 1000;
    this.initTimeoutMs = opts.initTimeoutMs ?? 60_000;
    this.queryTimeoutMs = opts.queryTimeoutMs ?? 60_000;
    this.onLog = opts.onLog;
  }

  /**
   * Get or create a client for (workspaceDir, sessionId). Idempotent across
   * callers — only the first caller does the work, the rest await the same
   * promise. sessionId isolates concurrent sessions that share a workspace
   * (e.g. SWE-bench pods reusing /data/worker-0/testbed for many instances):
   * without it, cross-session state (indexed files, symbol tables) leaks.
   */
  async getClient(workspaceDir: string, sessionId?: string): Promise<PyrightClient> {
    const key = this.normalizeKey(workspaceDir, sessionId);
    await this.closing.get(key);

    // Already live
    const existing = this.map.get(key);
    if (existing && !existing.client.isDead) {
      existing.lastUsedAt = Date.now();
      return existing.client;
    }

    // Dead or evicted — remove it
    if (existing) {
      this.map.delete(key);
    }

    // Concurrent caller — wait for in-flight init
    const inflightPromise = this.inflight.get(key);
    if (inflightPromise) return inflightPromise;

    // We are the first caller — spawn
    const promise = this.doCreateClient(workspaceDir, key);
    this.inflight.set(key, promise);
    try {
      const client = await promise;
      // Register die listener to auto-remove
      client.on("die", () => {
        const p = this.map.get(key);
        if (p?.client === client) this.map.delete(key);
      });
      return client;
    } finally {
      this.inflight.delete(key);
    }
  }

  private async doCreateClient(workspaceDir: string, key: string): Promise<PyrightClient> {
    // Enforce maxWorkspaces via LRU before creating.
    this.evictIfNeeded();

    const client = new PyrightClient({
      workspaceDir,
      initTimeoutMs: this.initTimeoutMs,
      queryTimeoutMs: this.queryTimeoutMs,
      onLog: this.onLog,
    });
    await client.whenReady();

    this.map.set(key, { client, workspaceDir, lastUsedAt: Date.now() });
    this.ensureSweep();
    return client;
  }

  /**
   * Shut down every client in the pool. Resolves only after each process has
   * exited. Idempotent — safe to call multiple times.
   */
  async shutdownAll(): Promise<void> {
    this.stopSweep();
    await Promise.allSettled(this.closing.values());
    const clients = [...this.map.values()].map((e) => e.client);
    const inflightClients = [...this.inflight.values()];
    this.map.clear();
    this.inflight.clear();
    await Promise.allSettled([
      ...clients.map((c) => c.shutdown()),
      ...(await Promise.allSettled(inflightClients)).map((r) =>
        r.status === "fulfilled" ? (r.value as PyrightClient).shutdown() : Promise.resolve(),
      ),
    ]);
  }

  /** Shut down the client for one (workspaceDir, sessionId), if it exists. */
  async shutdownWorkspace(workspaceDir: string, sessionId?: string): Promise<void> {
    const key = this.normalizeKey(workspaceDir, sessionId);
    const existingClose = this.closing.get(key);
    if (existingClose) return existingClose;

    const close = this.doShutdownWorkspace(key);
    this.closing.set(key, close);
    try {
      await close;
    } finally {
      if (this.closing.get(key) === close) this.closing.delete(key);
    }
  }

  private async doShutdownWorkspace(key: string): Promise<void> {
    const client = this.map.get(key)?.client;
    const inflight = this.inflight.get(key);
    this.map.delete(key);
    this.inflight.delete(key);

    const settledInflight = inflight ? await Promise.allSettled([inflight]) : [];
    const inflightClient =
      settledInflight[0]?.status === "fulfilled" ? settledInflight[0].value : undefined;
    if (inflightClient && this.map.get(key)?.client === inflightClient) {
      this.map.delete(key);
    }

    await Promise.allSettled(
      [...new Set([client, inflightClient].filter((c): c is PyrightClient => Boolean(c)))].map((c) =>
        c.shutdown(),
      ),
    );
    if (this.map.size === 0 && this.inflight.size === 0) this.stopSweep();
  }

  /** Force a sweep pass — exposed for tests. */
  sweep(): void {
    if (this.sweeping) return;
    this.sweeping = true;
    try {
      const now = Date.now();
      for (const [key, entry] of this.map) {
        if (now - entry.lastUsedAt >= this.idleTimeoutMs) {
          void entry.client.shutdown(); // start shutdown, don't block
          this.map.delete(key);
        }
      }
    } finally {
      this.sweeping = false;
    }
  }

  get size(): number {
    return this.map.size;
  }

  // ---- internal helpers ----

  private normalizeKey(dir: string, sessionId?: string): string {
    // ponytail: pair dir with sessionId so concurrent sessions sharing a
    // workspace (SWE-bench per-instance) get isolated PyrightClients.
    return `${dir}::${sessionId ?? "global"}`;
  }

  private evictIfNeeded(): void {
    if (this.map.size < this.maxWorkspaces) return;
    // Find LRU entry and kill it.
    let oldestKey: string | undefined;
    let oldestTime = Infinity;
    for (const [key, entry] of this.map) {
      if (entry.lastUsedAt < oldestTime) {
        oldestTime = entry.lastUsedAt;
        oldestKey = key;
      }
    }
    if (oldestKey) {
      const entry = this.map.get(oldestKey);
      if (entry) {
        void entry.client.shutdown();
        this.map.delete(oldestKey);
      }
    }
  }

  private ensureSweep(): void {
    if (this.sweepTimer) return;
    // ponytail: 5 min sweep interval, aligned to the start of the minute so
    // deterministic in tests.
    this.sweepTimer = setInterval(() => this.sweep(), 5 * 60 * 1000);
    if (this.sweepTimer.unref) this.sweepTimer.unref();
  }

  private stopSweep(): void {
    if (this.sweepTimer) {
      clearInterval(this.sweepTimer);
      this.sweepTimer = undefined;
    }
  }
}
