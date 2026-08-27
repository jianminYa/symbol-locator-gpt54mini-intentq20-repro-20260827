// PyrightClient integration test — real pyright, real fixture.
// Not in default `vitest run` scope; run explicitly.
import { appendFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { PyrightClient } from "../src/lsp/client.js";

const LOG = "/tmp/sl-debug.log";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const fixtureDir = resolve(__dirname, "fixtures/minirepo");

describe("PyrightClient integration (real pyright)", () => {
  let client: PyrightClient;

  beforeAll(async () => {
    client = new PyrightClient({
      workspaceDir: fixtureDir,
      initTimeoutMs: 30_000,
      onLog: (m) => appendFileSync(LOG, `${new Date().toISOString()} ${m}\n`),
    });
    await client.whenReady();
  });

  afterAll(async () => {
    if (client) await client.shutdown();
  });

  it("initialize completes and workspaceSymbolProvider is advertised", () => {
    // If whenReady() resolved, both are true — see client.ts doInit.
    expect(client.isDead).toBe(false);
  });

  it("documentSymbol on forms.py returns BaseForm class with save method", async () => {
    const ds = await client.documentSymbol(`${fixtureDir}/forms.py`);
    expect(ds).toBeInstanceOf(Array);
    expect(ds.length).toBeGreaterThan(0);
    const baseForm = ds.find((s) => s.name === "BaseForm");
    expect(baseForm).toBeDefined();
    const save = baseForm!.children?.find((c) => c.name === "save");
    expect(save).toBeDefined();
    expect(save!.kind).toBe(6); // Method
  });

  it("workspaceSymbol('save') finds save definitions across files", async () => {
    // Open all fixture files first — pyright is lazy about non-opened files
    for (const f of ["forms.py", "models.py", "modelforms.py", "util.py"]) {
      await client.documentSymbol(`${fixtureDir}/${f}`);
    }
    const symbols = await client.workspaceSymbol("save");
    expect(symbols.length).toBeGreaterThanOrEqual(3);
    const files = new Set(symbols.map((s) => s.file.split("/").pop()));
    expect(files.has("forms.py")).toBe(true);
    // At least one non-forms hit
    expect(files.size).toBeGreaterThan(1);

    // All hits should be named "save"
    expect(symbols.every((s) => s.name === "save")).toBe(true);

    // Container present for methods (save inside BaseForm / Model / ModelForm)
    const withContainer = symbols.filter((s) => s.container);
    expect(withContainer.length).toBeGreaterThan(0);
  });

  it("getSourceSnippet returns a source window", async () => {
    const snippet = await client.getSourceSnippet(`${fixtureDir}/forms.py`, 8, 2);
    expect(snippet).toContain("def save");
  });
});
