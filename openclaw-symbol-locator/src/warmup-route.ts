import { execFile } from "node:child_process";
import { realpath } from "node:fs/promises";
import type { IncomingMessage, ServerResponse } from "node:http";
import { promisify } from "node:util";
const execFileAsync = promisify(execFile);

type WarmupRequest = {
  workspaceDir?: unknown;
  expectedRepo?: unknown;
  expectedBaseCommit?: unknown;
};

// ponytail: verify-only endpoint. Pyright is warmed lazily per session on the
// first tool call — warming here would produce a `dir::global` client that
// never matches the `dir::<sessionId>` key tools use.
export function createWarmupHandler() {
  return async (req: IncomingMessage, res: ServerResponse): Promise<boolean> => {
    if (req.method !== "POST") {
      respond(res, 405, { error: "method_not_allowed" });
      return true;
    }

    try {
      const body = await readJson(req);
      const workspaceDir = readString(body.workspaceDir, "workspaceDir");
      const expectedRepo = readString(body.expectedRepo, "expectedRepo");
      const expectedBaseCommit = readString(body.expectedBaseCommit, "expectedBaseCommit");
      const verified = await verifyWorkspace(workspaceDir, expectedRepo, expectedBaseCommit);
      // ponytail: verify-only; keep the pre-fix-C `warmup` shape so eval tool
      // v5 (which reads warmup.filesIndexed etc.) doesn't KeyError. Zeros are
      // truthful — pyright is warmed lazily per session on first tool call.
      respond(res, 200, {
        ok: true,
        verified,
        warmup: { filesIndexed: 0, filesFound: 0, sampledSymbols: 0, retrievableSymbols: 0 },
      });
    } catch (error) {
      respond(res, 400, {
        ok: false,
        error: error instanceof Error ? error.message : String(error),
      });
    }
    return true;
  };
}

async function verifyWorkspace(
  workspaceDir: string,
  expectedRepo: string,
  expectedBaseCommit: string,
) {
  const root = await git(workspaceDir, ["rev-parse", "--show-toplevel"]);
  const head = await git(root, ["rev-parse", "HEAD"]);
  const origin = await git(root, ["remote", "get-url", "origin"]);
  const dirty = await git(root, ["status", "--porcelain"]);
  const realWorkspace = await realpath(workspaceDir);

  if (head !== expectedBaseCommit) {
    throw new Error(`base_commit mismatch: expected ${expectedBaseCommit}, got ${head}`);
  }
  if (normalizeRepo(origin) !== normalizeRepo(expectedRepo)) {
    throw new Error(`repo mismatch: expected ${expectedRepo}, got ${origin}`);
  }
  if (dirty) throw new Error("workspace is dirty before agent start");

  return { workspaceDir, realWorkspace, root, repo: normalizeRepo(origin), head, worktreeClean: true };
}

async function git(cwd: string, args: string[]): Promise<string> {
  const { stdout } = await execFileAsync("git", args, { cwd, timeout: 30_000 });
  return stdout.trim();
}

function normalizeRepo(value: string): string {
  return value
    .trim()
    .replace(/^git@github\.com:/, "")
    .replace(/^https?:\/\/github\.com\//, "")
    .replace(/\.git$/, "")
    .toLowerCase();
}

function readString(value: unknown, name: string): string {
  if (typeof value !== "string" || !value.trim()) throw new Error(`invalid ${name}`);
  return value.trim();
}

async function readJson(req: IncomingMessage): Promise<WarmupRequest> {
  const chunks: Buffer[] = [];
  for await (const chunk of req) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf-8")) as WarmupRequest;
  } catch {
    throw new Error("invalid JSON body");
  }
}

function respond(res: ServerResponse, status: number, body: unknown): void {
  res.statusCode = status;
  res.setHeader("content-type", "application/json; charset=utf-8");
  res.end(JSON.stringify(body));
}
