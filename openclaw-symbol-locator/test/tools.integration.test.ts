// End-to-end integration test: real pyright, real minirepo, real cache, real tools.
// Skips scoring (single/all failing LLM → fallback score=50) so it exercises the
// LSP → cache → tool pipeline without a network dep.
import { appendFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { WorkspacePool } from "../src/lsp/manager.js";
import { CandidateCache } from "../src/cache/cache.js";
import { createFindSymbolTool } from "../src/tools/find-symbol.js";
import { createMoreSymbolsTool } from "../src/tools/more-symbols.js";

const LOG = "/tmp/sl-debug.log";
const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const fixtureDir = resolve(__dirname, "fixtures/minirepo");

// Fake api: LLM fails → scorer falls back to score=50 per candidate.
function makeApi() {
  return {
    runtime: {
      llm: { complete: () => Promise.reject(new Error("no llm in this test")) },
    },
    logger: {
      info: (m: string) => appendFileSync(LOG, `INFO ${m}\n`),
      warn: (m: string) => appendFileSync(LOG, `WARN ${m}\n`),
      error: (m: string) => appendFileSync(LOG, `ERROR ${m}\n`),
      debug: (m: string) => appendFileSync(LOG, `DEBUG ${m}\n`),
    },
  } as any;
}

describe("find_symbol/more_symbols integration (real pyright + minirepo)", () => {
  let pool: WorkspacePool;
  let cache: CandidateCache;

  beforeAll(async () => {
    pool = new WorkspacePool({
      maxWorkspaces: 2,
      initTimeoutMs: 30_000,
      onLog: (m) => appendFileSync(LOG, `${new Date().toISOString()} ${m}\n`),
    });
    cache = new CandidateCache({ maxEntries: 32, ttlMs: 5 * 60_000 });
    // Prime the client + open each fixture file so workspaceSymbol sees them.
    const client = await pool.getClient(fixtureDir);
    for (const f of ["forms.py", "models.py", "modelforms.py", "util.py"]) {
      await client.documentSymbol(`${fixtureDir}/${f}`);
    }
  });

  afterAll(async () => {
    if (pool) await pool.shutdownAll();
  });

  it("find_symbol('save') returns at least 3 candidates with snippets", async () => {
    const tool = createFindSymbolTool({
      api: makeApi(),
      pool,
      cache,
      resolveConfig: () => ({ scorer: { threshold: 0 } }),
      resolveWorkspaceDir: () => fixtureDir,
    });
    const r = await tool.execute("call-1", { name: "save", top_k: 5 });
    const d = r.details as any;
    expect(d.total).toBeGreaterThanOrEqual(3);
    expect(d.candidates.length).toBeGreaterThanOrEqual(3);
    for (const c of d.candidates) {
      expect(c.file).toContain(fixtureDir);
      expect(typeof c.line).toBe("number");
      // Fallback score=50 in scorer when LLM fails.
      expect(c.score).toBe(50);
      expect(c.snippet.length).toBeGreaterThan(0);
    }
  });

  it("more_symbols advances the cursor for the same (workspace, name)", async () => {
    // Fresh cache — prior test drained the cursor.
    const c2 = new CandidateCache({ maxEntries: 32, ttlMs: 5 * 60_000 });
    const find = createFindSymbolTool({
      api: makeApi(),
      pool,
      cache: c2,
      resolveConfig: () => ({ scorer: { threshold: 0 } }),
      resolveWorkspaceDir: () => fixtureDir,
    });
    const first = await find.execute("call-1", { name: "save", top_k: 2 });
    expect((first.details as any).returned).toBe(2);

    const more = createMoreSymbolsTool({
      api: makeApi(),
      pool,
      cache: c2,
      resolveConfig: () => ({ scorer: { threshold: 0 } }),
      resolveWorkspaceDir: () => fixtureDir,
    });
    const next = await more.execute("call-2", { name: "save", count: 10 });
    expect((next.details as any).returned).toBeGreaterThan(0);
    // First-batch files should not repeat in the second batch.
    const firstFiles = new Set(
      (first.details as any).candidates.map((c: any) => c.file + ":" + c.line),
    );
    for (const c of (next.details as any).candidates) {
      expect(firstFiles.has(c.file + ":" + c.line)).toBe(false);
    }
  });

  it("100 concurrent find_symbol calls result in one pool spawn", async () => {
    // Fresh cache + pool to observe spawn count from zero.
    const p2 = new WorkspacePool({
      maxWorkspaces: 2,
      initTimeoutMs: 30_000,
      onLog: (m) => appendFileSync(LOG, `p2 ${m}\n`),
    });
    const c2 = new CandidateCache({ maxEntries: 32, ttlMs: 5 * 60_000 });
    // Pre-open files so workspaceSymbol has content, then race.
    const primer = await p2.getClient(fixtureDir);
    for (const f of ["forms.py", "models.py", "modelforms.py"]) {
      await primer.documentSymbol(`${fixtureDir}/${f}`);
    }

    const tool = createFindSymbolTool({
      api: makeApi(),
      pool: p2,
      cache: c2,
      resolveConfig: () => ({ scorer: { threshold: 0 } }),
      resolveWorkspaceDir: () => fixtureDir,
    });
    const results = await Promise.all(
      Array.from({ length: 100 }, (_, i) => tool.execute(`c${i}`, { name: "save" })),
    );
    for (const r of results) {
      expect((r.details as any).total).toBeGreaterThanOrEqual(3);
    }
    // The pool must have exactly one live client for this workspace.
    expect((p2 as any).map.size).toBe(1);
    await p2.shutdownAll();
  }, 60_000);
});
