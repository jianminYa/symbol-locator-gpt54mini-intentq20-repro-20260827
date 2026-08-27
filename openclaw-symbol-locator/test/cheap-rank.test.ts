import { describe, expect, it } from "vitest";
import { rankCandidates } from "../src/ranker/cheap-rank.js";
import type { PlainSymbol } from "../src/lsp/types.js";

function symbol(overrides: Partial<PlainSymbol>): PlainSymbol {
  return {
    name: "subs",
    kind: 6,
    kindName: "method",
    file: "sympy/core/basic.py",
    line: 1,
    column: 1,
    ...overrides,
  };
}

describe("cheap candidate ranking", () => {
  it("prefers an exact source definition over a test-name substring", () => {
    const ranked = rankCandidates(
      [
        symbol({ name: "test_subs", file: "sympy/core/tests/test_evalf.py" }),
        symbol({ name: "subs", container: "Basic", file: "sympy/core/basic.py" }),
        symbol({ name: "_eval_subs", container: "Expr", file: "sympy/core/expr.py" }),
      ],
      { query: "subs", context: "expression substitution polynomial error" },
    );

    expect(ranked.map((candidate) => candidate.name)).toEqual([
      "subs",
      "_eval_subs",
      "test_subs",
    ]);
  });

  it("boosts source paths that overlap task context", () => {
    const ranked = rankCandidates(
      [
        symbol({ name: "handle", file: "sympy/core/basic.py" }),
        symbol({ name: "handle", file: "sympy/polys/polytools.py" }),
      ],
      { query: "handle", context: "polynomial Piecewise error" },
    );

    expect(ranked[0]!.file).toBe("sympy/polys/polytools.py");
  });
});
