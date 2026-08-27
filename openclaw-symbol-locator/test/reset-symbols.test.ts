import { afterEach, describe, expect, it } from "vitest";
import { CandidateCache } from "../src/cache/cache.js";
import { createResetSymbolsTool } from "../src/tools/reset-symbols.js";

describe("reset_symbols", () => {
  let cache: CandidateCache;

  afterEach(() => {
    cache.clear();
  });

  it("clears all entries when name is omitted", async () => {
    cache = new CandidateCache({ maxEntries: 32, ttlMs: 60_000 });
    cache.set(cache.key("/ws", "foo"), [{ name: "foo", score: 80, kind: 12, kindName: "function", file: "/ws/a.py", line: 1, snippet: "def foo(): pass" }]);
    cache.set(cache.key("/ws", "bar"), [{ name: "bar", score: 70, kind: 12, kindName: "function", file: "/ws/b.py", line: 1, snippet: "def bar(): pass" }]);
    expect(cache.size).toBe(2);

    const tool = createResetSymbolsTool(cache);
    const r = await tool.execute("c1", { confirm: "yes" });
    expect((r.details as any).cleared).toBe(2);
    expect(cache.size).toBe(0);
  });

  it("clears only named entry", async () => {
    cache = new CandidateCache({ maxEntries: 32, ttlMs: 60_000 });
    cache.set(cache.key("/ws", "foo"), [{ name: "foo", score: 80, kind: 12, kindName: "function", file: "/ws/a.py", line: 1, snippet: "def foo(): pass" }]);
    cache.set(cache.key("/ws", "bar"), [{ name: "bar", score: 70, kind: 12, kindName: "function", file: "/ws/b.py", line: 1, snippet: "def bar(): pass" }]);
    expect(cache.size).toBe(2);

    const tool = createResetSymbolsTool(cache);
    const r = await tool.execute("c1", { name: "bar", confirm: "yes" });
    expect((r.details as any).cleared).toBe(1);
    expect(cache.size).toBe(1);
    expect(cache.get(cache.key("/ws", "foo"))).toBeDefined();
  });

  it("refuses without confirm: 'yes'", async () => {
    cache = new CandidateCache({ maxEntries: 32, ttlMs: 60_000 });
    cache.set(cache.key("/ws", "foo"), [{ name: "foo", score: 80, kind: 12, kindName: "function", file: "/ws/a.py", line: 1, snippet: "def foo(): pass" }]);

    const tool = createResetSymbolsTool(cache);
    const r = await tool.execute("c1", { confirm: "nope" });
    expect((r.details as any).cleared).toBe(false);
    expect(cache.size).toBe(1);
  });
});
