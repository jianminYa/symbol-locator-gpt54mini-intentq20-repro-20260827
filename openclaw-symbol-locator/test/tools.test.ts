// find_symbol / more_symbols unit tests with mocked pool + scorer.
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createFindSymbolTool } from "../src/tools/find-symbol.js";
import { createMoreSymbolsTool } from "../src/tools/more-symbols.js";
import { CandidateCache } from "../src/cache/cache.js";
import type { PlainSymbol } from "../src/lsp/types.js";

function makeSymbol(overrides: Partial<PlainSymbol> = {}): PlainSymbol {
  return {
    name: "save",
    kind: 6,
    kindName: "method",
    file: "/tmp/forms.py",
    line: 10,
    column: 5,
    container: "BaseForm",
    ...overrides,
  };
}

// Fake pyright client — returns preset symbol list + snippet
function makeFakeClient(
  symbols: PlainSymbol[],
  snippet: string = "def save(self):\n    pass",
) {
  return {
    warmup: vi.fn().mockResolvedValue(undefined),
    workspaceSymbol: vi.fn().mockResolvedValue(symbols),
    getSourceSnippet: vi.fn().mockResolvedValue(snippet),
    isDead: false,
    lastUsedAt: Date.now(),
  } as any;
}

// Fake pool — returns the fake client
function makeFakePool(client: any) {
  return {
    getClient: vi.fn().mockResolvedValue(client),
  } as any;
}

function makeApi(llmText: string = "90") {
  return {
    runtime: {
      llm: {
        complete: vi.fn().mockResolvedValue({ text: llmText }),
      },
    },
    logger: { info: vi.fn(), error: vi.fn(), warn: vi.fn(), debug: vi.fn() },
  } as any;
}

describe("find_symbol tool", () => {
  let cache: CandidateCache;

  beforeEach(() => {
    cache = new CandidateCache({ maxEntries: 10, ttlMs: 100_000 });
  });

  it("returns friendly text when nothing is found", async () => {
    const client = makeFakeClient([]);
    const pool = makeFakePool(client);
    const tool = createFindSymbolTool({
      api: makeApi(),
      pool,
      cache,
      resolveConfig: () => ({}),
    });
    const r = await tool.execute("call-1", { name: "nonexistent" });
    expect(r.content[0]!.type).toBe("text");
    expect((r.content[0] as any).text).toContain("No symbols named `nonexistent`");
    expect((r.details as any).total).toBe(0);
    expect(client.workspaceSymbol).toHaveBeenCalledOnce();
  });

  it("rejects obvious error messages before starting Pyright", async () => {
    const pool = makeFakePool(makeFakeClient([]));
    const tool = createFindSymbolTool({
      api: makeApi(),
      pool,
      cache,
      resolveConfig: () => ({}),
    });

    const r = await tool.execute("call-1", {
      name: "Piecewise generators do not make sense",
    });

    expect(pool.getClient).not.toHaveBeenCalled();
    expect((r.details as any).error).toBe("invalid_symbol_query");
    expect((r.content[0] as any).text).toContain("single Python symbol");
  });

  it("runs full pipeline on cache miss", async () => {
    const client = makeFakeClient([
      makeSymbol({ file: "a.py", container: "A" }),
      makeSymbol({ file: "b.py", container: "B" }),
      makeSymbol({ file: "c.py", container: "C" }),
      makeSymbol({ file: "d.py", container: "D" }),
    ]);
    const pool = makeFakePool(client);
    const tool = createFindSymbolTool({
      api: makeApi("90"),
      pool,
      cache,
      resolveConfig: () => ({ scorer: { threshold: 50 } }),
    });
    const r = await tool.execute("call-1", { name: "save", context: "form bug", top_k: 2 });
    expect((r.details as any).total).toBe(4);
    expect((r.details as any).returned).toBe(2);
    expect((r.details as any).remaining).toBe(2);
    expect((r.details as any).candidates).toHaveLength(2);
    expect((r.content[0] as any).text).toContain("Found 4 candidates");
    expect((r.content[0] as any).text).toContain("Call `more_symbols");
  });

  it("second call with same name+context hits cache (no re-spawn)", async () => {
    const client = makeFakeClient([
      makeSymbol({ file: "a.py" }),
      makeSymbol({ file: "b.py" }),
      makeSymbol({ file: "c.py" }),
    ]);
    const pool = makeFakePool(client);
    const tool = createFindSymbolTool({
      api: makeApi("90"),
      pool,
      cache,
      resolveConfig: () => ({ scorer: { threshold: 50 } }),
    });
    await tool.execute("call-1", { name: "save", context: "x", top_k: 1 });
    expect(client.workspaceSymbol).toHaveBeenCalledTimes(1);

    // Second call — should hit cache, not re-query
    const r = await tool.execute("call-2", { name: "save", context: "x", top_k: 1 });
    expect(client.workspaceSymbol).toHaveBeenCalledTimes(1); // unchanged
    expect((r.details as any).returned).toBe(1);
  });

  it("does not reuse cached candidates across sessions", async () => {
    const client = makeFakeClient([
      makeSymbol({ file: "a.py" }),
      makeSymbol({ file: "b.py" }),
    ]);
    const pool = makeFakePool(client);
    const toolA = createFindSymbolTool({
      api: makeApi("90"),
      pool,
      cache,
      resolveConfig: () => ({ scorer: { threshold: 50 } }),
      resolveWorkspaceDir: () => "/ws",
      resolveSessionId: () => "session-a",
    });
    const toolB = createFindSymbolTool({
      api: makeApi("90"),
      pool,
      cache,
      resolveConfig: () => ({ scorer: { threshold: 50 } }),
      resolveWorkspaceDir: () => "/ws",
      resolveSessionId: () => "session-b",
    });

    await toolA.execute("call-a", { name: "save", context: "x" });
    await toolB.execute("call-b", { name: "save", context: "x" });

    expect(client.workspaceSymbol).toHaveBeenCalledTimes(2);
  });

  it("scores the next ranked page after cached results are exhausted", async () => {
    const client = makeFakeClient([]);
    const pool = makeFakePool(client);
    const key = cache.key(process.cwd(), "save", "x");
    cache.set(
      key,
      [{ ...makeSymbol({ file: "first.py" }), score: 90, snippet: "first" }],
      [makeSymbol({ file: "second.py" }), makeSymbol({ file: "third.py" })],
    );
    cache.advance(key, 1);
    const tool = createFindSymbolTool({
      api: makeApi("1=88\n2=80"),
      pool,
      cache,
      resolveConfig: () => ({ scorer: { threshold: 0 } }),
    });

    const r = await tool.execute("call-2", { name: "save", context: "x", top_k: 2 });

    expect(client.workspaceSymbol).not.toHaveBeenCalled();
    expect((r.details as any).returned).toBe(2);
    expect((r.details as any).candidates.map((c: any) => c.file)).toEqual([
      "second.py",
      "third.py",
    ]);
  });

  it("includes tool call and session IDs in diagnostic logs", async () => {
    const api = makeApi();
    const tool = createFindSymbolTool({
      api,
      pool: makeFakePool(makeFakeClient([])),
      cache,
      resolveConfig: () => ({}),
      resolveWorkspaceDir: () => "/ws",
      resolveSessionId: () => "session-a",
    });

    await tool.execute("call-123", { name: "missing" });
    expect(api.logger.info).toHaveBeenCalledWith(
      expect.stringMatching(/\[sl-diag\] call name=.*call_id="call-123".*session_id="session-a"/),
    );
  });

  it("applies kind_filter", async () => {
    const client = makeFakeClient([
      makeSymbol({ file: "a.py", kind: 6 }), // method
      makeSymbol({ file: "b.py", kind: 12 }), // function
      makeSymbol({ file: "c.py", kind: 6 }),
    ]);
    const pool = makeFakePool(client);
    const tool = createFindSymbolTool({
      api: makeApi("90"),
      pool,
      cache,
      resolveConfig: () => ({ scorer: { threshold: 50 } }),
    });
    const r = await tool.execute("call-1", {
      name: "save",
      context: "x",
      kind_filter: [6], // methods only
      top_k: 10,
    });
    expect((r.details as any).total).toBe(2);
    expect((r.details as any).candidates.every((c: any) => c.kind === "method")).toBe(true);
  });

  it("returns friendly text when kind_filter matches nothing", async () => {
    const client = makeFakeClient([makeSymbol({ kind: 6 })]);
    const pool = makeFakePool(client);
    const tool = createFindSymbolTool({
      api: makeApi("90"),
      pool,
      cache,
      resolveConfig: () => ({}),
    });
    const r = await tool.execute("call-1", { name: "save", kind_filter: [5] });
    expect((r.content[0] as any).text).toContain("none match the kind_filter");
  });

  it("catches getClient failures without throwing", async () => {
    const pool = { getClient: vi.fn().mockRejectedValue(new Error("pyright dead")) };
    const tool = createFindSymbolTool({
      api: makeApi(),
      pool: pool as any,
      cache,
      resolveConfig: () => ({}),
    });
    const r = await tool.execute("call-1", { name: "save" });
    expect((r.content[0] as any).text).toContain("pyright dead");
    expect((r.details as any).error).toBe("pyright dead");
  });

  it("falls back to unscored candidates when scorer totally fails", async () => {
    const client = makeFakeClient([
      makeSymbol({ file: "a.py" }),
      makeSymbol({ file: "b.py" }),
    ]);
    const pool = makeFakePool(client);
    const brokenApi = {
      runtime: {
        llm: {
          complete: vi.fn().mockRejectedValue(new Error("llm dead")),
        },
      },
      logger: { info: vi.fn(), error: vi.fn(), warn: vi.fn(), debug: vi.fn() },
    } as any;
    const tool = createFindSymbolTool({
      api: brokenApi,
      pool,
      cache,
      resolveConfig: () => ({ scorer: { threshold: 0 } }),
    });
    const r = await tool.execute("call-1", { name: "save", context: "x", top_k: 5 });
    // Both candidates should come back (fallback score=50 in scorer)
    expect((r.details as any).returned).toBeGreaterThanOrEqual(2);
  });
});

describe("more_symbols tool", () => {
  let cache: CandidateCache;

  beforeEach(() => {
    cache = new CandidateCache({ maxEntries: 10, ttlMs: 100_000 });
  });

  function makeMoreSymbolsTool(opts: { client?: any; api?: any } = {}) {
    const client = opts.client ?? makeFakeClient([], "rough-snippet");
    const pool = makeFakePool(client);
    return createMoreSymbolsTool({
      api: opts.api ?? makeApi(),
      pool,
      cache,
      resolveConfig: () => ({}),
    });
  }

  it("returns cache_miss when nothing cached", async () => {
    const tool = makeMoreSymbolsTool();
    const r = await tool.execute("call-1", { name: "save" });
    expect((r.details as any).error).toBe("cache_miss");
    expect((r.content[0] as any).text).toContain("Call `find_symbol");
  });

  it("advances cursor when scored candidates exist", async () => {
    const key = cache.key(process.cwd(), "save");
    cache.set(key, [
      { ...makeSymbol({ file: "a.py" }), score: 90, snippet: "..." },
      { ...makeSymbol({ file: "b.py" }), score: 80, snippet: "..." },
      { ...makeSymbol({ file: "c.py" }), score: 70, snippet: "..." },
      { ...makeSymbol({ file: "d.py" }), score: 60, snippet: "..." },
    ]);
    cache.advance(key, 2);

    const tool = makeMoreSymbolsTool();
    const r = await tool.execute("call-1", { name: "save", count: 2 });
    expect((r.details as any).returned).toBe(2);
    expect((r.details as any).remaining).toBe(0);
    expect(
      (r.details as any).candidates.map((c: any) => c.file),
    ).toEqual(["c.py", "d.py"]);
  });

  it("drains pending rough-ranked when scored page exhausted", async () => {
    const key = cache.key(process.cwd(), "save");
    cache.set(
      key,
      [{ ...makeSymbol({ file: "a.py" }), score: 90, snippet: "..." }],
      [
        makeSymbol({ file: "p1.py" }),
        makeSymbol({ file: "p2.py" }),
        makeSymbol({ file: "p3.py" }),
        makeSymbol({ file: "p4.py" }),
      ],
    );
    cache.advance(key, 5); // exhaust scored

    const client = makeFakeClient([], "rough body");
    const tool = makeMoreSymbolsTool({ client });
    const r = await tool.execute("call-1", { name: "save", count: 2 });

    expect((r.details as any).returned).toBe(2);
    expect((r.details as any).rough_ranked).toBe(true);
    expect((r.details as any).remaining).toBe(2); // 2 pending left
    expect(
      (r.details as any).candidates.map((c: any) => c.file),
    ).toEqual(["p1.py", "p2.py"]);
    expect((r.content[0] as any).text).toContain("rough-ranked");
    expect((r.content[0] as any).text).toContain("no LLM score");
    // Snippet fetched via pool client
    expect(client.getSourceSnippet).toHaveBeenCalledTimes(2);
  });

  it("puts pending back when pool.getClient fails", async () => {
    const key = cache.key(process.cwd(), "save");
    cache.set(key, [], [makeSymbol({ file: "p1.py" }), makeSymbol({ file: "p2.py" })]);

    const failingPool = { getClient: vi.fn().mockRejectedValue(new Error("pyright dead")) } as any;
    const tool = createMoreSymbolsTool({
      api: makeApi(),
      pool: failingPool,
      cache,
      resolveConfig: () => ({}),
    });
    const r = await tool.execute("call-1", { name: "save", count: 2 });

    expect((r.details as any).error).toBe("pool_unavailable");
    expect(cache.get(key)!.pending.length).toBe(2); // put back
  });

  it("returns 'all returned' when both scored and pending exhausted", async () => {
    const key = cache.key(process.cwd(), "save");
    cache.set(key, [{ ...makeSymbol(), score: 90, snippet: "..." }]);
    cache.advance(key, 5);

    const tool = makeMoreSymbolsTool();
    const r = await tool.execute("call-1", { name: "save" });
    expect((r.details as any).returned).toBe(0);
    expect((r.content[0] as any).text).toContain("have been returned");
    expect((r.content[0] as any).text).toContain("grep");
  });
});
