// pyright-bin.test.ts — L1 sanity
import { existsSync } from "node:fs";
import { describe, it, expect } from "vitest";
import { resolvePyrightLangserverBin } from "../src/lsp/pyright-bin.js";

describe("resolvePyrightLangserverBin", () => {
  it("returns a path that exists on disk", () => {
    const bin = resolvePyrightLangserverBin();
    expect(bin).toMatch(/langserver\.index\.js$/);
    expect(existsSync(bin)).toBe(true);
  });
});
