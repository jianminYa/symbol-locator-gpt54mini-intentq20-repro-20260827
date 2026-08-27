// CandidateCache unit tests — LRU, TTL, advance cursor, clear.
import { beforeEach, describe, expect, it } from "vitest";
import { CandidateCache } from "../src/cache/cache.js";
import type { ScoredCandidate } from "../src/scorer/scorer.js";
import type { PlainSymbol } from "../src/lsp/types.js";

function sc(overrides: Partial<ScoredCandidate> = {}): ScoredCandidate {
  return {
    name: "save",
    kind: 6,
    kindName: "method",
    file: "/tmp/a.py",
    line: 10,
    column: 5,
    container: "MyClass",
    score: 90,
    snippet: "def save(self): pass",
    ...overrides,
  };
}

describe("CandidateCache", () => {
  let clock: number;
  let cache: CandidateCache;

  // Reset clock + cache before each test
  beforeEach(() => {
    clock = 1_000_000;
    cache = new CandidateCache({
      maxEntries: 256,
      ttlMs: 100_000,
      now: () => clock,
    });
  });

  it("get returns undefined for unknown key", () => {
    expect(cache.get(cache.key("/ws", "save"))).toBeUndefined();
  });

  it("set + get round-trip", () => {
    const key = cache.key("/ws", "save");
    cache.set(key, [sc(), sc({ name: "save", container: "Other" })]);
    const entry = cache.get(key);
    expect(entry!.candidates).toHaveLength(2);
    expect(entry!.cursor).toBe(0);
  });

  it("get returns undefined after TTL expiry", () => {
    const key = cache.key("/ws", "save");
    cache.set(key, [sc()]);
    clock += 100_001; // past TTL
    expect(cache.get(key)).toBeUndefined();
  });

  it("evicts LRU when cap reached", () => {
    cache = new CandidateCache({ maxEntries: 2, ttlMs: 100_000, now: () => clock });
    const a = cache.key("/ws", "a");
    const b = cache.key("/ws", "b");
    const c = cache.key("/ws", "c");
    cache.set(a, [sc()]);
    cache.set(b, [sc()]);
    // Touch a so b is older
    cache.get(a);
    clock += 1;
    cache.set(c, [sc()]);
    // b should be evicted (LRU), a should survive
    expect(cache.get(b)).toBeUndefined();
    expect(cache.get(a)!.candidates).toHaveLength(1);
    expect(cache.get(c)!.candidates).toHaveLength(1);
  });

  it("advance pages through candidates", () => {
    const key = cache.key("/ws", "save");
    const cs = [sc({ score: 1 }), sc({ score: 2 }), sc({ score: 3 }), sc({ score: 4 })];
    cache.set(key, cs);

    const batch1 = cache.advance(key, 2);
    expect(batch1).toHaveLength(2);
    expect(batch1.map((c) => c.score)).toEqual([1, 2]);

    const batch2 = cache.advance(key, 2);
    expect(batch2).toHaveLength(2);
    expect(batch2.map((c) => c.score)).toEqual([3, 4]);

    // Cursor exhausted
    const batch3 = cache.advance(key, 2);
    expect(batch3).toHaveLength(0);
  });

  it("advance returns [] for unknown key", () => {
    expect(cache.advance(cache.key("/ws", "nope"), 3)).toEqual([]);
  });

  it("clear(name) removes all entries for that symbol name", () => {
    const saveA = cache.key("/ws-a", "save");
    const saveB = cache.key("/ws-b", "save");
    const init = cache.key("/ws-a", "__init__");
    cache.set(saveA, [sc()]);
    cache.set(saveB, [sc()]);
    cache.set(init, [sc()]);
    cache.clear("save");
    expect(cache.get(saveA)).toBeUndefined();
    expect(cache.get(saveB)).toBeUndefined();
    expect(cache.get(init)).toBeDefined();
  });

  it("clear() wipes everything", () => {
    cache.set(cache.key("/ws", "save"), [sc()]);
    cache.set(cache.key("/ws", "other"), [sc()]);
    cache.clear();
    expect(cache.size).toBe(0);
  });

  it("context change causes different key", () => {
    const k1 = cache.key("/ws", "save", "fixing form failure");
    const k2 = cache.key("/ws", "save", "adding new api");
    expect(k1).not.toBe(k2);
  });

  it("session change causes different key", () => {
    const k1 = cache.key("/ws", "save", "fixing form failure", "session-a");
    const k2 = cache.key("/ws", "save", "fixing form failure", "session-b");
    expect(k1).not.toBe(k2);
  });

  it("same context normalizes to same key", () => {
    const k1 = cache.key("/ws", "save", "  FIXING FORM failure  ");
    const k2 = cache.key("/ws", "save", "fixing form failure");
    expect(k1).toBe(k2);
  });

  it("empty context maps to no-ctx sentinel", () => {
    const k = cache.key("/ws", "save");
    expect(k).toContain("::no-ctx");
  });

  it("keeps unscored ranked candidates for later pages", () => {
    const key = cache.key("/ws", "save");
    const pending: PlainSymbol[] = [
      { ...sc(), score: undefined } as unknown as PlainSymbol,
      { ...sc({ file: "/tmp/b.py" }), score: undefined } as unknown as PlainSymbol,
    ];
    cache.set(key, [sc()], pending);

    expect(cache.takePending(key, 1)).toHaveLength(1);
    expect(cache.takePending(key, 10)).toHaveLength(1);
  });
});
