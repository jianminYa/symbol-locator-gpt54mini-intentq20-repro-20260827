// PyrightClient — one pyright-langserver process per workspace.
// Handles: spawn, LSP handshake, symbol queries, snippet reads, shutdown.
import { spawn, type ChildProcess } from "node:child_process";
import { EventEmitter } from "node:events";
import { readdir, readFile } from "node:fs/promises";
import { join } from "node:path";
import { createRpcClient, type RpcClient } from "./protocol.js";
import { resolvePyrightLangserverBin } from "./pyright-bin.js";
import {
  fileUriToPath,
  pathToFileUri,
  symbolKindName,
  type LspDocumentSymbol,
  type LspWorkspaceSymbol,
  type PlainSymbol,
} from "./types.js";

const SNIPPET_LINES_CACHE_CAP = 1024;
const SHUTDOWN_REQUEST_TIMEOUT_MS = 1000;
const SHUTDOWN_KILL_TIMEOUT_MS = 5000;
// ponytail: cap for one-shot warmup so we don't try to didOpen a 100k-file repo.
// Above this, first workspace/symbol falls back to pyright's lazy index — worse
// recall on huge repos, but keeps warmup bounded. Raise if needed per config.
const WARMUP_FILE_CAP = 2000;
const WARMUP_SKIP_DIRS = new Set([
  ".git",
  ".hg",
  ".svn",
  "node_modules",
  "__pycache__",
  ".venv",
  "venv",
  "env",
  ".env",
  ".tox",
  ".pytest_cache",
  ".mypy_cache",
  ".ruff_cache",
  "dist",
  "build",
  ".idea",
  ".vscode",
]);

export type PyrightClientOptions = {
  workspaceDir: string;
  initTimeoutMs?: number;
  queryTimeoutMs?: number;
  onLog?: (message: string) => void;
};

/**
 * Thrown when a single `workspace/symbol` RPC exceeds queryTimeoutMs. The
 * client shuts itself down before throwing — pool auto-removes it via the
 * `die` event, so the next call spawns a fresh Pyright.
 */
export class WorkspaceSymbolTimeoutError extends Error {
  readonly query: string;
  readonly elapsedMs: number;
  constructor(query: string, elapsedMs: number) {
    super(`workspace/symbol query ${JSON.stringify(query)} timed out after ${elapsedMs}ms`);
    this.name = "WorkspaceSymbolTimeoutError";
    this.query = query;
    this.elapsedMs = elapsedMs;
  }
}

export type WarmupReport = {
  filesFound: number;
  filesIndexed: number;
  failedFiles: string[];
  sampledSymbols: number;
  retrievableSymbols: number;
  unretrievableSamples: Array<{ name: string; file: string }>;
};

/**
 * One langserver per workspace. Emits "die" when the underlying process exits.
 */
export class PyrightClient extends EventEmitter {
  readonly workspaceDir: string;
  private readonly workspaceUri: string;
  private readonly initTimeoutMs: number;
  private readonly queryTimeoutMs: number;
  private readonly onLog?: (m: string) => void;
  private proc?: ChildProcess;
  private rpc?: RpcClient;
  private initPromise?: Promise<void>;
  private shutdownPromise?: Promise<void>;
  private opened = new Set<string>(); // set of file paths we've didOpen'd
  private snippetCache = new Map<string, string[]>(); // file -> lines
  private snippetCacheOrder: string[] = [];
  private warmed = false;
  private warmupPromise?: Promise<WarmupReport>;
  // ponytail: serialize workspace/symbol so one stuck query can't pile up more
  // stuck queries behind it. If throughput ever matters, key this per query.
  private wsSymbolQueue: Promise<unknown> = Promise.resolve();
  lastUsedAt = Date.now();
  isDead = false;

  constructor(opts: PyrightClientOptions) {
    super();
    this.workspaceDir = opts.workspaceDir;
    this.workspaceUri = pathToFileUri(opts.workspaceDir);
    this.initTimeoutMs = opts.initTimeoutMs ?? 60_000;
    this.queryTimeoutMs = opts.queryTimeoutMs ?? 60_000;
    this.onLog = opts.onLog;
  }

  /**
   * Idempotent: first caller does the work, everyone else awaits the same
   * promise. Rejects on init timeout or spawn failure.
   */
  whenReady(): Promise<void> {
    if (!this.initPromise) {
      this.initPromise = this.doInit();
    }
    return this.initPromise;
  }

  private async doInit(): Promise<void> {
    const bin = resolvePyrightLangserverBin();
    const maxOldSpaceMb = process.env.SYMBOL_LOCATOR_PYRIGHT_MAX_OLD_SPACE_MB ?? "4096";
    const proc = spawn("node", [bin, "--stdio"], {
      cwd: this.workspaceDir,
      stdio: ["pipe", "pipe", "pipe"],
      env:
        maxOldSpaceMb === "0"
          ? process.env
          : {
              ...process.env,
              NODE_OPTIONS:
                `${process.env.NODE_OPTIONS ?? ""} --max-old-space-size=${maxOldSpaceMb}`.trim(),
            },
    });
    this.proc = proc;

    proc.stderr?.on("data", (chunk: Buffer) => {
      this.onLog?.(`[pyright stderr] ${chunk.toString().trim()}`);
    });
    proc.on("exit", (code, sig) => {
      this.isDead = true;
      this.emit("die", { code, signal: sig });
    });

    if (!proc.stdin || !proc.stdout) {
      throw new Error("pyright process has no stdio streams");
    }
    const rpc = createRpcClient(proc.stdin, proc.stdout);
    this.rpc = rpc;

    // ponytail: pyright may send workspace/configuration server->client;
    // return empty array so it uses its own defaults.
    // workspace/workspaceFolders — must return the actual folder list, not
    // null; returning null causes pyright to treat the workspace as empty.
    rpc.onRequest("workspace/configuration", () => []);
    rpc.onRequest("workspace/workspaceFolders", () => [
      { uri: this.workspaceUri, name: "root" },
    ]);
    rpc.onRequest("client/registerCapability", () => null);
    rpc.onRequest("window/workDoneProgress/create", () => null);

    rpc.onNotification("window/logMessage", (params) => {
      const p = params as { message?: string; type?: number } | undefined;
      if (p?.message) this.onLog?.(`[pyright] ${p.message}`);
    });

    // Send initialize with an init timeout.
    const initResult = (await Promise.race([
      rpc.request("initialize", {
        processId: process.pid,
        rootUri: this.workspaceUri,
        capabilities: {
          workspace: {
            symbol: {},
            configuration: true,
          },
          textDocument: {
            synchronization: {
              didSave: true,
              willSave: false,
              willSaveWaitUntil: false,
              dynamicRegistration: false,
            },
            documentSymbol: {
              hierarchicalDocumentSymbolSupport: true,
              dynamicRegistration: false,
            },
            publishDiagnostics: { versionSupport: false },
          },
          window: { workDoneProgress: true },
        },
        workspaceFolders: [{ uri: this.workspaceUri, name: "root" }],
        initializationOptions: {},
      }),
      new Promise<never>((_, reject) => {
        setTimeout(
          () => reject(new Error(`pyright initialize timeout after ${this.initTimeoutMs}ms`)),
          this.initTimeoutMs,
        );
      }),
    ])) as { capabilities?: Record<string, unknown> };

    // Sanity: capability must be present, otherwise workspace/symbol won't work.
    if (!initResult.capabilities?.workspaceSymbolProvider) {
      throw new Error("pyright did not advertise workspaceSymbolProvider");
    }
    rpc.notify("initialized", {});
  }

  /**
   * Query the workspace for symbols matching `query`. Empty string returns
   * pyright's index in its natural order; a name returns filtered matches.
   *
   * ponytail: pyright indexes lazily. Cold `workspace/symbol` on a large repo
   * can be empty on the first call because analysis happens per-file as they
   * are touched. Callers wanting a full-workspace query should `openAllPython`
   * first.
   */
  workspaceSymbol(query: string): Promise<PlainSymbol[]> {
    // Serialize: chain onto whatever's already running/queued. The caller
    // awaits `run`; a no-op catch on the queue chain keeps the queue slot
    // from becoming an unhandled rejection.
    const run = this.wsSymbolQueue
      .catch(() => {})
      .then(() => this.whenReady())
      .then(() => this.doWorkspaceSymbol(query));
    this.wsSymbolQueue = run.catch(() => {});
    return run;
  }

  private doWorkspaceSymbol(query: string): Promise<PlainSymbol[]> {
    this.lastUsedAt = Date.now();
    if (this.isDead) return Promise.reject(new Error("pyright client is dead"));
    const start = Date.now();
    return new Promise<PlainSymbol[]>((resolve, reject) => {
      let settled = false;
      const timer = setTimeout(() => {
        if (settled) return;
        settled = true;
        const err = new WorkspaceSymbolTimeoutError(query, Date.now() - start);
        this.onLog?.(
          `[sl-diag] workspaceSymbol timeout query=${JSON.stringify(err.query)} ` +
            `elapsedMs=${err.elapsedMs}`,
        );
        // Kill the client — proc.on("exit") sets isDead + emits "die", pool
        // auto-removes on that, so the next getClient() spawns fresh Pyright.
        void this.shutdown().catch(() => {});
        reject(err);
      }, this.queryTimeoutMs);
      timer.unref?.();

      this.rpc!.request("workspace/symbol", { query }).then(
        (raw) => {
          if (settled) return;
          settled = true;
          clearTimeout(timer);
          const list = (raw ?? []) as LspWorkspaceSymbol[];
          resolve(list.map((s) => this.normalizeWorkspaceSymbol(s)));
        },
        (err) => {
          if (settled) return;
          settled = true;
          clearTimeout(timer);
          reject(err);
        },
      );
    });
  }

  /**
   * One-shot: discover all .py files in workspaceDir and didOpen them so
   * pyright indexes them for workspace/symbol queries. Idempotent across
   * callers — second and subsequent calls are no-ops.
   *
   * ponytail: bounded to WARMUP_FILE_CAP files; above that we stop opening
   * and fall back to pyright's lazy index (first cold workspace/symbol may
   * miss low-priority files). Walk uses a simple recursive .py scan with a
   * skip-list for common non-source dirs — fast enough for repos up to a
   * few thousand files, which is the target audience.
   */
  async warmup(): Promise<WarmupReport> {
    if (!this.warmupPromise) this.warmupPromise = this.doWarmup();
    return this.warmupPromise;
  }

  private async doWarmup(): Promise<WarmupReport> {
    this.warmed = true;
    await this.whenReady();

    const files: string[] = [];
    await collectPythonFiles(this.workspaceDir, files);
    const failedFiles: string[] = [];
    const symbols: Array<{ name: string; file: string }> = [];
    for (const file of files) {
      try {
        // documentSymbol forces pyright to actually parse & index the file
        // (didOpen alone is fire-and-forget; pyright may defer processing).
        // The integration test proves this: it calls documentSymbol per file
        // before workspaceSymbol to guarantee the index is populated.
        const documentSymbols = await this.documentSymbol(file);
        for (const symbol of documentSymbols) {
          if (/^[A-Za-z_][A-Za-z0-9_]{2,}$/.test(symbol.name) && !symbol.name.startsWith("test_")) {
            symbols.push({ name: symbol.name, file });
          }
        }
      } catch {
        failedFiles.push(file);
      }
    }

    const filesByName = new Map<string, Set<string>>();
    for (const symbol of symbols) {
      const filesForName = filesByName.get(symbol.name) ?? new Set<string>();
      filesForName.add(symbol.file);
      filesByName.set(symbol.name, filesForName);
    }
    const uniqueSymbols = symbols.filter((symbol) => filesByName.get(symbol.name)?.size === 1);
    const samples = sampleSymbols(uniqueSymbols, 12);
    let retrievableSymbols = 0;
    const unretrievableSamples: Array<{ name: string; file: string }> = [];
    for (const sample of samples) {
      try {
        const matches = await this.workspaceSymbol(sample.name);
        if (matches.some((candidate) => candidate.file === sample.file)) {
          retrievableSymbols++;
        } else {
          unretrievableSamples.push(sample);
        }
      } catch {
        unretrievableSamples.push(sample);
      }
    }
    return {
      filesFound: files.length,
      filesIndexed: files.length - failedFiles.length,
      failedFiles,
      sampledSymbols: samples.length,
      retrievableSymbols,
      unretrievableSamples,
    };
  }

  /** Document-level hierarchical symbols. Requires the file to be didOpen'd. */
  async documentSymbol(filePath: string): Promise<LspDocumentSymbol[]> {
    await this.whenReady();
    this.lastUsedAt = Date.now();
    await this.ensureOpen(filePath);
    const raw = (await this.rpc!.request("textDocument/documentSymbol", {
      textDocument: { uri: pathToFileUri(filePath) },
    })) as LspDocumentSymbol[] | null;
    return raw ?? [];
  }

  /**
   * Read a source window around `line` (1-based). Caches file contents by
   * path with a bounded LRU.
   */
  async getSourceSnippet(
    filePath: string,
    line: number,
    contextLines = 15,
  ): Promise<string> {
    const lines = await this.getFileLines(filePath);
    const start = Math.max(0, line - 1 - contextLines);
    const end = Math.min(lines.length, line - 1 + contextLines + 1);
    return lines.slice(start, end).join("\n");
  }

  private async getFileLines(filePath: string): Promise<string[]> {
    const cached = this.snippetCache.get(filePath);
    if (cached) return cached;
    const text = await readFile(filePath, "utf-8");
    const lines = text.split("\n");
    this.snippetCache.set(filePath, lines);
    this.snippetCacheOrder.push(filePath);
    if (this.snippetCacheOrder.length > SNIPPET_LINES_CACHE_CAP) {
      const evict = this.snippetCacheOrder.shift();
      if (evict) this.snippetCache.delete(evict);
    }
    return lines;
  }

  /**
   * Ensure a file is didOpen'd with pyright — required to populate its
   * per-file analysis. Only sends didOpen once per path per client lifetime.
   */
  private async ensureOpen(filePath: string): Promise<void> {
    if (this.opened.has(filePath)) return;
    const text = await readFile(filePath, "utf-8");
    this.rpc!.notify("textDocument/didOpen", {
      textDocument: {
        uri: pathToFileUri(filePath),
        languageId: "python",
        version: 1,
        text,
      },
    });
    this.opened.add(filePath);
  }

  private normalizeWorkspaceSymbol(s: LspWorkspaceSymbol): PlainSymbol {
    return {
      name: s.name,
      kind: s.kind,
      kindName: symbolKindName(s.kind),
      file: fileUriToPath(s.location.uri),
      line: s.location.range.start.line + 1,
      column: s.location.range.start.character + 1,
      container: s.containerName,
    };
  }

  /**
   * Graceful shutdown: LSP shutdown → exit → SIGTERM → 5s → SIGKILL.
   * Follows extensions/signal/src/daemon.ts:179-217 double-stage pattern —
   * never resolve until the process actually exits, so callers cannot start a
   * replacement while the old one is still holding resources.
   */
  async shutdown(): Promise<void> {
    if (!this.shutdownPromise) this.shutdownPromise = this.doShutdown();
    await this.shutdownPromise;
  }

  private async doShutdown(): Promise<void> {
    if (this.isDead) return;
    const proc = this.proc;
    if (!proc) return;

    const exited = new Promise<void>((resolve) => {
      if (this.isDead) resolve();
      else proc.once("exit", () => resolve());
    });

    let shutdownTimer: ReturnType<typeof setTimeout> | undefined;
    try {
      await Promise.race([
        this.rpc?.request("shutdown", null),
        new Promise<void>((resolve) => {
          shutdownTimer = setTimeout(resolve, SHUTDOWN_REQUEST_TIMEOUT_MS);
          shutdownTimer.unref?.();
        }),
      ]);
      this.rpc?.notify("exit");
    } catch {
      // ignore — we're killing the process anyway
    } finally {
      if (shutdownTimer) clearTimeout(shutdownTimer);
    }

    proc.kill("SIGTERM");
    const killTimer = setTimeout(() => {
      if (!this.isDead) proc.kill("SIGKILL");
    }, SHUTDOWN_KILL_TIMEOUT_MS);
    killTimer.unref?.();
    await exited;
    clearTimeout(killTimer);
    this.rpc?.close();
  }
}

function sampleSymbols(
  symbols: Array<{ name: string; file: string }>,
  count: number,
): Array<{ name: string; file: string }> {
  if (symbols.length <= count) return symbols;
  const out: Array<{ name: string; file: string }> = [];
  for (let i = 0; i < count; i++) {
    out.push(symbols[Math.floor((i * (symbols.length - 1)) / (count - 1))]!);
  }
  return out;
}

async function collectPythonFiles(root: string, out: string[]): Promise<void> {
  if (out.length >= WARMUP_FILE_CAP) return;
  let entries;
  try {
    entries = await readdir(root, { withFileTypes: true });
  } catch {
    return;
  }
  for (const entry of entries) {
    if (out.length >= WARMUP_FILE_CAP) return;
    if (entry.name.startsWith(".")) continue;
    const full = join(root, entry.name);
    if (entry.isDirectory()) {
      if (WARMUP_SKIP_DIRS.has(entry.name)) continue;
      await collectPythonFiles(full, out);
    } else if (entry.isFile() && entry.name.endsWith(".py")) {
      out.push(full);
    }
  }
}
