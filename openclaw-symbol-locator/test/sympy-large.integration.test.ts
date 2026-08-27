import { describe, expect, it } from "vitest";
import { PyrightClient } from "../src/lsp/client.js";

const workspaceDir = process.env.SYMPY_TESTBED;

describe.runIf(Boolean(workspaceDir))("PyrightClient large SymPy workspace", () => {
  it(
    "warms the real repository and resolves a known symbol without OOM",
    async () => {
      const client = new PyrightClient({
        workspaceDir: workspaceDir!,
        initTimeoutMs: 180_000,
        onLog: (message) => console.error(message),
      });
      try {
        const warmup = await client.warmup();
        expect(warmup.failedFiles).toEqual([]);
        expect(warmup.unretrievableSamples).toEqual([]);
        const symbols = await client.workspaceSymbol("_parallel_dict_from_expr");
        expect(client.isDead).toBe(false);
        expect(symbols.some((symbol) => symbol.name.includes("_parallel_dict_from_expr"))).toBe(
          true,
        );
      } finally {
        await client.shutdown();
      }
    },
    10 * 60_000,
  );
});
