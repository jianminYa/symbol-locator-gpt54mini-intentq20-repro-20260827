// Scorer unit tests — prompt parsing, host LLM, independent LLM, pipeline.
import { beforeEach, describe, expect, it, vi } from "vitest";

// ---- parseScore ----
import { parseScore } from "../src/scorer/prompt.js";

describe("parseScore", () => {
  it("bare integer", () => {
    expect(parseScore("92")).toBe(92);
    expect(parseScore("0")).toBe(0);
    expect(parseScore("100")).toBe(100);
  });

  it("trims whitespace", () => {
    expect(parseScore(" 87\n")).toBe(87);
  });

  it("extracts trailing text like 'Score: 92'", () => {
    expect(parseScore("Score: 92")).toBe(92);
  });

  it("extracts from prose 'rate this 92 out of 100'", () => {
    expect(parseScore("I'd rate this a 92 out of 100.")).toBe(92);
  });

  it("returns null for unparseable", () => {
    expect(parseScore("low relevance")).toBeNull();
    expect(parseScore("not sure")).toBeNull();
  });

  it("rejects out-of-range integers", () => {
    expect(parseScore("150")).toBeNull();
  });
});

// ---- host LLM ----
import { scoreWithHostLlm } from "../src/scorer/host-llm.js";

describe("host LLM scorer", () => {
  it("logs normalized host scorer usage", async () => {
    const stderr = vi.spyOn(process.stderr, "write").mockImplementation(() => true);
    const llm = {
      complete: vi.fn().mockResolvedValue({
        text: "87",
        usage: {
          inputTokens: 120,
          outputTokens: 4,
          totalTokens: 124,
        },
      }),
    };

    try {
      await scoreWithHostLlm({
        llm,
        candidate: { file: "/tmp/a.py", line: 10, snippet: "def save(): pass" },
      });
      expect(stderr).toHaveBeenCalledWith(
        expect.stringContaining("scorer-usage prompt=120 completion=4 total=124"),
      );
    } finally {
      stderr.mockRestore();
    }
  });

  it("returns 87 for clean response", async () => {
    const llm = {
      complete: vi.fn().mockResolvedValue({ text: "87" }),
    };
    const score = await scoreWithHostLlm({
      llm,
      candidate: {
        file: "/tmp/a.py",
        line: 10,
        container: "MyClass",
        snippet: "def save(self):\n    pass\n",
      },
      context: "fixing save bug",
    });
    expect(score).toBe(87);
    expect(llm.complete).toHaveBeenCalledOnce();
    const call = llm.complete.mock.calls[0]?.[0];
    expect(call.maxTokens).toBe(2048);
    expect(call.temperature).toBe(0);
    expect(call.purpose).toBe("symbol-locator.score");
    expect(call.messages[0]?.content).toContain("MyClass");
    expect(call.messages[0]?.content).toContain("def save");
    expect(call.messages[0]?.content).toContain("fixing save bug");
  });

  it("parses 'Score: 92' into 92", async () => {
    const llm = { complete: vi.fn().mockResolvedValue({ text: "Score: 92" }) };
    const score = await scoreWithHostLlm({
      llm,
      candidate: { file: "/tmp/a.py", line: 1, snippet: "" },
    });
    expect(score).toBe(92);
  });

  it("throws on unparseable response", async () => {
    const llm = { complete: vi.fn().mockResolvedValue({ text: "not sure" }) };
    await expect(
      scoreWithHostLlm({
        llm,
        candidate: { file: "/tmp/a.py", line: 1, snippet: "" },
      }),
    ).rejects.toThrow("unparseable score");
  });
});

// ---- independent LLM ----
// ponytail: test via fetch mock — the simplest thing that works.
import { scoreWithIndependentLlm } from "../src/scorer/independent-llm.js";

describe("independent LLM scorer", () => {
  let origFetch: typeof globalThis.fetch;

  beforeEach(() => {
    origFetch = globalThis.fetch;
  });

  it("returns 83 on success", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () =>
        Promise.resolve({
          choices: [{ message: { content: "83" } }],
        }),
    }) as any;

    const score = await scoreWithIndependentLlm({
      cfg: {
        enabled: true,
        baseUrl: "https://api.example.com/v1",
        apiKey: "sk-test",
        model: "gpt-4o-mini",
        timeoutMs: 5_000,
      },
      candidate: { file: "/tmp/a.py", line: 1, snippet: "def save(self): pass" },
    });
    expect(score).toBe(83);
  });

  it("throws on 401", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
      text: () => Promise.resolve("unauthorized"),
    }) as any;

    await expect(
      scoreWithIndependentLlm({
        cfg: {
          enabled: true,
          baseUrl: "https://api.example.com/v1",
          apiKey: "sk-test",
          model: "gpt-4o-mini",
          timeoutMs: 5_000,
        },
        candidate: { file: "/tmp/a.py", line: 1, snippet: "x" },
      }),
    ).rejects.toThrow("HTTP 401");
  });

  it("throws on timeout", async () => {
    // Simulate abort signal as a timeout
    globalThis.fetch = vi.fn().mockRejectedValue({ name: "AbortError" }) as any;

    await expect(
      scoreWithIndependentLlm({
        cfg: {
          enabled: true,
          baseUrl: "https://api.example.com/v1",
          apiKey: "sk-test",
          model: "gpt-4o-mini",
          timeoutMs: 10,
        },
        candidate: { file: "/tmp/a.py", line: 1, snippet: "x" },
      }),
    ).rejects.toThrow("timeout");
  });

  it("throws when apiKey missing", async () => {
    await expect(
      scoreWithIndependentLlm({
        cfg: {
          enabled: true,
          baseUrl: "https://api.example.com/v1",
          apiKey: undefined,
          model: "gpt-4o-mini",
          timeoutMs: 5_000,
        },
        candidate: { file: "/tmp/a.py", line: 1, snippet: "x" },
      }),
    ).rejects.toThrow("apiKey missing");
  });
});

// ---- scoreCandidates pipeline ----
import { scoreCandidates } from "../src/scorer/scorer.js";
import type { PlainSymbol } from "../src/lsp/types.js";

function makePlainSymbol(overrides: Partial<PlainSymbol> = {}): PlainSymbol {
  return {
    name: "save",
    kind: 6,
    kindName: "method",
    file: "/tmp/test.py",
    line: 10,
    column: 5,
    container: "MyClass",
    ...overrides,
  };
}

// Fake workspace client — provides snippets without real files.
function fakeWorkspaceClient(snippets: string[] = []) {
  let idx = 0;
  return {
    getSourceSnippet: vi.fn().mockImplementation(() => {
      const s = snippets[idx] ?? "";
      idx = (idx + 1) % (snippets.length || 1);
      return Promise.resolve(s);
    }),
  } as any;
}

describe("scoreCandidates pipeline", () => {
  it("skips scoring for single candidate, returns score=100", async () => {
    const wc = fakeWorkspaceClient(["def save(self): pass"]);
    const result = await scoreCandidates({
      candidates: [makePlainSymbol()],
      workspaceClient: wc,
    });
    expect(result).toHaveLength(1);
    expect(result[0]!.score).toBe(100);
    expect(result[0]!.snippet).toBe("def save(self): pass");
  });

  it("skips scoring when rescore=false", async () => {
    const wc = fakeWorkspaceClient(["a", "b", "c"]);
    const result = await scoreCandidates({
      candidates: [
        makePlainSymbol({ file: "a.py" }),
        makePlainSymbol({ file: "b.py" }),
        makePlainSymbol({ file: "c.py" }),
      ],
      workspaceClient: wc,
      rescore: false,
    });
    expect(result).toHaveLength(3);
    expect(result.every((c) => c.score === 100)).toBe(true);
  });

  it("scores via host LLM and filters below threshold", async () => {
    const llm = {
      complete: vi.fn().mockResolvedValueOnce({ text: "1=90\n2=30\n3=78" }),
    };
    const diag = vi.fn();
    const wc = fakeWorkspaceClient([
      "def save_a(self): pass",
      "def save_b(self): pass",
      "def save_c(self): pass",
    ]);
    const result = await scoreCandidates({
      candidates: [
        makePlainSymbol({ file: "a.py" }),
        makePlainSymbol({ file: "b.py" }),
        makePlainSymbol({ file: "c.py" }),
      ],
      context: "fixing form save",
      workspaceClient: wc,
      hostLlm: llm,
      threshold: 75,
      concurrency: 3,
      diag,
    });
    expect(result).toHaveLength(2);
    expect(result.map((c) => c.score)).toEqual([90, 78]);
    expect(llm.complete).toHaveBeenCalledOnce();
    const prompt = llm.complete.mock.calls[0]?.[0].messages[0]?.content;
    expect(prompt).toContain('candidate index="1"');
    expect(prompt).toContain('candidate index="3"');
    expect(diag).toHaveBeenCalledWith(
      expect.stringContaining("[sl-diag] batch-scorer source=host"),
    );
  });

  it("falls back to 50 for missing host batch scores", async () => {
    const llm = {
      complete: vi.fn().mockResolvedValueOnce({ text: "2=88" }),
    };
    const wc = fakeWorkspaceClient(["def save_a(self): pass", "def save_b(self): pass"]);
    const result = await scoreCandidates({
      candidates: [makePlainSymbol({ file: "a.py" }), makePlainSymbol({ file: "b.py" })],
      context: "form bug",
      workspaceClient: wc,
      hostLlm: llm,
      threshold: 75,
    });
    expect(result).toHaveLength(1);
    expect(result[0]!.score).toBe(88);
    expect(llm.complete).toHaveBeenCalledOnce();
  });

  it("batches host LLM scoring in chunks of 100 with bounded concurrency", async () => {
    let maxInflight = 0;
    let inflight = 0;
    const llm = {
      complete: vi.fn().mockImplementation(async () => {
        inflight++;
        maxInflight = Math.max(maxInflight, inflight);
        await new Promise((r) => setTimeout(r, 5));
        inflight--;
        return { text: "1=50" };
      }),
    };
    const candidates = Array.from({ length: 201 }, (_, i) =>
      makePlainSymbol({ file: `/tmp/${i}.py` }),
    );
    const wc = fakeWorkspaceClient(candidates.map(() => "def save(self): pass"));
    const result = await scoreCandidates({
      candidates,
      context: "test",
      workspaceClient: wc,
      hostLlm: llm,
      concurrency: 2,
      threshold: 0, // don't filter
    });
    expect(maxInflight).toBeLessThanOrEqual(2);
    expect(llm.complete).toHaveBeenCalledTimes(3);
    expect(result).toHaveLength(201);
  });

  it("uses independent LLM path when cfg.enabled", async () => {
    globalThis.fetch = vi
      .fn()
      .mockResolvedValue({
        ok: true,
        json: () =>
          Promise.resolve({ choices: [{ message: { content: "1=65\n2=65" } }] }),
      }) as any;

    const wc = fakeWorkspaceClient(["def save_a(self): pass", "def save_b(self): pass"]);
    const result = await scoreCandidates({
      candidates: [
        makePlainSymbol({ file: "a.py" }),
        makePlainSymbol({ file: "b.py" }),
      ],
      context: "test",
      workspaceClient: wc,
      independentCfg: {
        enabled: true,
        baseUrl: "https://api.example.com/v1",
        apiKey: "sk-test",
        model: "gpt-4o-mini",
        timeoutMs: 5_000,
      },
      threshold: 0,
    });
    expect(result).toHaveLength(2);
    expect(result[0]!.score).toBe(65);
  });
});
