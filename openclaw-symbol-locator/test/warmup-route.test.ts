import { execFileSync } from "node:child_process";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Readable } from "node:stream";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createWarmupHandler } from "../src/warmup-route.js";

const dirs: string[] = [];

afterEach(async () => {
  await Promise.all(dirs.splice(0).map((dir) => rm(dir, { recursive: true, force: true })));
});

describe("symbol locator warmup route", () => {
  it("rejects a repo or commit mismatch", async () => {
    const dir = await createRepo();
    const handler = createWarmupHandler();
    const res = response();

    await handler(
      request({ workspaceDir: dir, expectedRepo: "other/repo", expectedBaseCommit: head(dir) }) as any,
      res as any,
    );

    expect(res.statusCode).toBe(400);
  });

  it("verifies a matching repository", async () => {
    const dir = await createRepo();
    const handler = createWarmupHandler();
    const res = response();

    await handler(
      request({ workspaceDir: dir, expectedRepo: "sympy/sympy", expectedBaseCommit: head(dir) }) as any,
      res as any,
    );

    expect(res.statusCode).toBe(200);
    expect(JSON.parse(res.body)).toMatchObject({
      ok: true,
      verified: { repo: "sympy/sympy" },
      warmup: { filesIndexed: 0, filesFound: 0, sampledSymbols: 0, retrievableSymbols: 0 },
    });
  });
});

async function createRepo() {
  const dir = await mkdtemp(join(tmpdir(), "sl-warmup-"));
  dirs.push(dir);
  await writeFile(join(dir, "sample.py"), "def sample():\n    return 1\n");
  execFileSync("git", ["init", "-q"], { cwd: dir });
  execFileSync("git", ["config", "user.email", "test@example.com"], { cwd: dir });
  execFileSync("git", ["config", "user.name", "Test"], { cwd: dir });
  execFileSync("git", ["remote", "add", "origin", "https://github.com/sympy/sympy.git"], { cwd: dir });
  execFileSync("git", ["add", "."], { cwd: dir });
  execFileSync("git", ["commit", "-qm", "base"], { cwd: dir });
  return dir;
}

function head(dir: string) {
  return execFileSync("git", ["rev-parse", "HEAD"], { cwd: dir }).toString().trim();
}

function request(body: unknown) {
  const req = Readable.from([JSON.stringify(body)]) as Readable & { method: string };
  req.method = "POST";
  return req;
}

function response() {
  return {
    statusCode: 0,
    body: "",
    setHeader: vi.fn(),
    end(body: string) {
      this.body = body;
    },
  };
}
