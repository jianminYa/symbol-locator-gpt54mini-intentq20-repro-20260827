// Raw JSON-RPC framing over child-process stdio.
// Replaces vscode-jsonrpc — that library was silently losing messages from
// pyright after initialize (see spike-pyright.ts history). Framing per LSP
// spec: `Content-Length: N\r\n\r\n<body>` then N bytes of UTF-8 JSON.

import type { Readable, Writable } from "node:stream";

type PendingRequest = {
  resolve: (value: unknown) => void;
  reject: (err: Error) => void;
};

type NotificationHandler = (params: unknown) => void;
type ServerRequestHandler = (params: unknown) => unknown;

export interface RpcClient {
  request(method: string, params?: unknown): Promise<unknown>;
  notify(method: string, params?: unknown): void;
  onNotification(method: string, handler: NotificationHandler): void;
  onAnyNotification(handler: (method: string, params: unknown) => void): void;
  onRequest(method: string, handler: ServerRequestHandler): void;
  close(): void;
}

export class RpcError extends Error {
  code: number;
  data: unknown;
  constructor(code: number, message: string, data: unknown) {
    super(message);
    this.name = "RpcError";
    this.code = code;
    this.data = data;
  }
}

/**
 * Create an RPC client over a child process' stdio.
 *
 * The client owns:
 * - outgoing request id counter
 * - pending-request map keyed by id
 * - stdout parser (framing state)
 * - server-initiated request/notification handlers
 *
 * It does NOT own the stdio streams themselves; caller closes those.
 */
export function createRpcClient(stdin: Writable, stdout: Readable): RpcClient {
  let nextId = 0;
  const pending = new Map<number, PendingRequest>();
  const notifHandlers = new Map<string, NotificationHandler[]>();
  const reqHandlers = new Map<string, ServerRequestHandler>();
  const anyNotifHandlers: ((m: string, p: unknown) => void)[] = [];

  // Buffer for partial frames arriving across chunks.
  let buf = Buffer.alloc(0);
  let closed = false;

  function ackServerRequest(id: number, result: unknown): void {
    write({ jsonrpc: "2.0", id, result });
  }
  function ackServerRequestError(id: number, err: RpcError): void {
    write({
      jsonrpc: "2.0",
      id,
      error: { code: err.code, message: err.message, data: err.data },
    });
  }

  function handleMessage(msg: {
    jsonrpc: "2.0";
    id?: number | string;
    method?: string;
    params?: unknown;
    result?: unknown;
    error?: { code: number; message: string; data?: unknown };
  }): void {
    // Server -> client request — check BEFORE response discriminator
    // because some server requests have a null `result` field.
    if (msg.method && msg.id !== undefined) {
      const handler = reqHandlers.get(msg.method);
      if (!handler) {
        // ponytail: default-ack any unregistered server→client request
        // with null rather than -32601. pyright aborts on error responses;
        // it sends client/registerCapability and other requests that are
        // safe to ignore.
        ackServerRequest(msg.id as number, null);
        return;
      }
      try {
        const result = handler(msg.params);
        // Support sync or Promise handlers.
        if (result && typeof (result as Promise<unknown>).then === "function") {
          (result as Promise<unknown>).then(
            (r) => ackServerRequest(msg.id as number, r),
            (e) =>
              ackServerRequestError(
                msg.id as number,
                new RpcError(-32000, String(e), undefined),
              ),
          );
        } else {
          ackServerRequest(msg.id as number, result);
        }
      } catch (e) {
        ackServerRequestError(
          msg.id as number,
          new RpcError(-32000, e instanceof Error ? e.message : String(e), undefined),
        );
      }
      return;
    }

    // Response to our request
    if (typeof msg.id === "number" && (msg.result !== undefined || msg.error)) {
      const p = pending.get(msg.id);
      if (!p) return;
      pending.delete(msg.id);
      if (msg.error) {
        p.reject(new RpcError(msg.error.code, msg.error.message, msg.error.data));
      } else {
        p.resolve(msg.result);
      }
      return;
    }

    // Server -> client notification (no id)
    if (msg.method) {
      for (const h of anyNotifHandlers) h(msg.method, msg.params);
      const list = notifHandlers.get(msg.method);
      if (list) {
        for (const h of list) h(msg.params);
      }
    }
  }

  function processBuffer(): void {
    while (true) {
      // Find header/body boundary.
      const sep = buf.indexOf("\r\n\r\n");
      if (sep === -1) return;
      const header = buf.subarray(0, sep).toString("utf-8");
      const clMatch = header.match(/Content-Length:\s*(\d+)/i);
      if (!clMatch) {
        // Malformed frame — drop up to boundary and continue.
        buf = buf.subarray(sep + 4);
        continue;
      }
      const contentLength = Number.parseInt(clMatch[1]!, 10);
      const totalLen = sep + 4 + contentLength;
      if (buf.length < totalLen) return; // wait for more
      const body = buf.subarray(sep + 4, totalLen).toString("utf-8");
      buf = buf.subarray(totalLen);
      let parsed: unknown;
      try {
        parsed = JSON.parse(body);
      } catch {
        continue; // skip invalid JSON silently
      }
      try {
        handleMessage(parsed as Parameters<typeof handleMessage>[0]);
      } catch (e) {
        // Never let a single malformed message kill the parser loop.
        if (notifHandlers.has("window/logMessage")) {
          for (const h of notifHandlers.get("window/logMessage")!) {
            try { h({ message: `rpc protocol: handler error: ${e}` }); } catch {}
          }
        }
      }
    }
  }

  stdout.on("data", (chunk: Buffer) => {
    if (closed) return;
    buf = buf.length === 0 ? chunk : Buffer.concat([buf, chunk]);
    processBuffer();
  });

  function write(msg: object): void {
    if (closed) return;
    const body = JSON.stringify(msg);
    const bodyBytes = Buffer.byteLength(body, "utf-8");
    stdin.write(`Content-Length: ${bodyBytes}\r\n\r\n${body}`);
  }

  return {
    request(method, params) {
      if (closed) return Promise.reject(new Error("rpc client closed"));
      const id = ++nextId;
      return new Promise<unknown>((resolve, reject) => {
        pending.set(id, { resolve, reject });
        write({ jsonrpc: "2.0", id, method, params });
      });
    },
    notify(method, params) {
      write({ jsonrpc: "2.0", method, params });
    },
    onNotification(method, handler) {
      const list = notifHandlers.get(method) ?? [];
      list.push(handler);
      notifHandlers.set(method, list);
    },
    onAnyNotification(handler) {
      anyNotifHandlers.push(handler);
    },
    onRequest(method, handler) {
      reqHandlers.set(method, handler);
    },
    close() {
      closed = true;
      // Reject all pending
      for (const p of pending.values()) {
        p.reject(new Error("rpc client closed"));
      }
      pending.clear();
    },
  };
}
