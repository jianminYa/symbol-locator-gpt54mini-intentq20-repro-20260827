// config.test.ts
import { describe, it, expect } from "vitest";
import {
  resolveScorerConfig,
  resolveScorerLlmConfig,
  resolveLspConfig,
  resolveCacheConfig,
} from "../src/config.js";

describe("config resolvers — defaults", () => {
  it("resolveScorerConfig fills defaults when empty", () => {
    expect(resolveScorerConfig(undefined)).toEqual({
      model: undefined,
      concurrency: 10,
      threshold: 75,
      snippetLines: 15,
    });
  });

  it("resolveScorerConfig fills partial overrides", () => {
    expect(resolveScorerConfig({ scorer: { model: "custom/model" } })).toMatchObject({
      model: "custom/model",
      concurrency: 10,
    });
  });

  it("resolveScorerConfig fills full overrides", () => {
    const cfg = resolveScorerConfig({ scorer: { model: "a/b", concurrency: 4, threshold: 60, snippetLines: 8 } });
    expect(cfg).toEqual({ model: "a/b", concurrency: 4, threshold: 60, snippetLines: 8 });
  });
});

describe("scorerLlm config", () => {
  it("returns undefined when not enabled", () => {
    expect(resolveScorerLlmConfig(undefined)).toBeUndefined();
  });

  it("returns undefined when enabled is false", () => {
    expect(resolveScorerLlmConfig({ scorerLlm: { enabled: false } })).toBeUndefined();
  });

  it("returns config with defaults when enabled", () => {
    const cfg = resolveScorerLlmConfig({ scorerLlm: { enabled: true, apiKey: "sk-xxx" } });
    expect(cfg).not.toBeUndefined();
    expect(cfg!.baseUrl).toBe("https://api.openai.com/v1");
    expect(cfg!.model).toBe("gpt-4o-mini");
    expect(cfg!.apiKey).toBe("sk-xxx");
    expect(cfg!.timeoutMs).toBe(30_000);
  });

  it("overrides all fields", () => {
    const cfg = resolveScorerLlmConfig({
      scorerLlm: {
        enabled: true,
        baseUrl: "https://custom.example.com/v1",
        apiKey: "key",
        model: "custom-model",
        timeoutMs: 5000,
      },
    });
    expect(cfg).toEqual({
      enabled: true,
      baseUrl: "https://custom.example.com/v1",
      apiKey: "key",
      model: "custom-model",
      timeoutMs: 5000,
    });
  });
});

describe("lsp config", () => {
  it("defaults", () => {
    expect(resolveLspConfig(undefined)).toEqual({
      maxWorkspaces: 4,
      idleTimeoutMs: 30 * 60 * 1000,
      initTimeoutMs: 60_000,
      queryTimeoutMs: 60_000,
    });
  });

  it("override maxWorkspaces", () => {
    expect(resolveLspConfig({ lsp: { maxWorkspaces: 2 } }).maxWorkspaces).toBe(2);
  });
});

describe("cache config", () => {
  it("defaults", () => {
    expect(resolveCacheConfig(undefined)).toEqual({
      maxEntries: 256,
      ttlMs: 30 * 60 * 1000,
    });
  });

  it("override", () => {
    const cfg = resolveCacheConfig({ cache: { maxEntries: 64, ttlMs: 60_000 } });
    expect(cfg).toEqual({ maxEntries: 64, ttlMs: 60_000 });
  });
});
