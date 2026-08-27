"""Minimal pyright-langserver LSP client.

One subprocess per workspace, JSON-RPC over stdio. Speaks just enough LSP
to: initialize, didOpen, documentSymbol, workspace/symbol. That's the whole
surface — everything else is caller's problem.

Threading: two background threads per client — one to drain stdout (parses
frames, dispatches to pending futures), one to drain stderr (drops on the
floor unless a logger is set). All public methods are safe to call from any
thread once __init__ returns.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import quote, unquote

# LSP SymbolKind — subset we care about
KIND_NAMES = {
    1: "file", 2: "module", 3: "namespace", 4: "package",
    5: "class", 6: "method", 7: "property", 8: "field", 9: "constructor",
    10: "enum", 11: "interface", 12: "function", 13: "variable", 14: "constant",
    22: "enum_member", 23: "struct",
}

WARMUP_SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__",
    ".venv", "venv", "env", ".env", ".tox", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "dist", "build", ".idea", ".vscode",
}


def path_to_uri(p: str) -> str:
    return "file://" + quote(str(p))


def uri_to_path(uri: str) -> str:
    return unquote(uri[len("file://"):]) if uri.startswith("file://") else uri


@dataclass
class PlainSymbol:
    name: str
    kind: int
    kind_name: str
    file: str
    line: int          # 1-based (definition start)
    column: int        # 1-based
    container: Optional[str] = None


class PyrightClient:
    """One langserver subprocess. NOT thread-safe on shutdown; safe elsewhere."""

    def __init__(
        self,
        workspace_dir: str,
        pyright_langserver: str = "pyright-langserver",
        init_timeout_s: float = 60.0,
        query_timeout_s: float = 60.0,
        warmup_file_cap: int = 2000,
        on_log: Optional[Callable[[str], None]] = None,
    ):
        self.workspace_dir = os.path.abspath(workspace_dir)
        self.workspace_uri = path_to_uri(self.workspace_dir)
        self.init_timeout_s = init_timeout_s
        self.query_timeout_s = query_timeout_s
        self.warmup_file_cap = warmup_file_cap
        self.on_log = on_log or (lambda _: None)

        self._proc: Optional[subprocess.Popen] = None
        self._next_id = 0
        self._id_lock = threading.Lock()
        self._pending: dict[int, Future] = {}
        self._pending_lock = threading.Lock()
        self._initialized = threading.Event()
        self._opened: set[str] = set()
        self._warmed = False
        self._warmup_lock = threading.Lock()
        self._ws_symbol_lock = threading.Lock()  # serialize workspace/symbol

    # ── lifecycle ──────────────────────────────────────────────────────
    def start(self) -> None:
        # Try `pyright-langserver --stdio` first; fall back to `python -m pyright.langserver`
        # if the binary isn't on PATH.
        try:
            self._proc = subprocess.Popen(
                [self.pyright_langserver_bin(), "--stdio"],
                cwd=self.workspace_dir,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
        except FileNotFoundError as e:
            raise RuntimeError(
                f"pyright-langserver not found on PATH. Install with "
                f"`pip install pyright` or `npm i -g pyright`. ({e})"
            )

        threading.Thread(target=self._reader_loop, daemon=True).start()
        threading.Thread(target=self._stderr_loop, daemon=True).start()

        # initialize
        init_result = self._request(
            "initialize",
            {
                "processId": os.getpid(),
                "rootUri": self.workspace_uri,
                "capabilities": {
                    "workspace": {"symbol": {}, "configuration": True},
                    "textDocument": {
                        "synchronization": {"didSave": True, "willSave": False, "willSaveWaitUntil": False, "dynamicRegistration": False},
                        "documentSymbol": {"hierarchicalDocumentSymbolSupport": True, "dynamicRegistration": False},
                        "publishDiagnostics": {"versionSupport": False},
                    },
                    "window": {"workDoneProgress": True},
                },
                "workspaceFolders": [{"uri": self.workspace_uri, "name": "root"}],
                "initializationOptions": {},
            },
            timeout_s=self.init_timeout_s,
        )
        if not init_result.get("capabilities", {}).get("workspaceSymbolProvider"):
            raise RuntimeError("pyright did not advertise workspaceSymbolProvider")
        self._notify("initialized", {})
        self._initialized.set()

    def pyright_langserver_bin(self) -> str:
        return os.environ.get("SYMBOL_LOCATOR_PYRIGHT_BIN", "pyright-langserver")

    def shutdown(self) -> None:
        if not self._proc or self._proc.poll() is not None:
            return
        try:
            self._request("shutdown", None, timeout_s=1.0)
        except Exception:
            pass
        try:
            self._notify("exit", None)
        except Exception:
            pass
        try:
            self._proc.wait(timeout=5.0)
        except Exception:
            self._proc.kill()

    def close_after_fork(self) -> None:
        """Drop inherited handles without signalling the parent's server.

        The child must never call ``shutdown`` on a Popen object inherited from
        its parent: the PID refers to the parent's server. Close only the
        child's duplicate stdio descriptors and discard all inherited state;
        the child will create and warm a new client.
        """
        proc = self._proc
        self._proc = None
        if proc is not None:
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                try:
                    if stream is not None:
                        stream.close()
                except Exception:
                    pass
        self._pending = {}
        self._opened = set()
        self._initialized = threading.Event()
        self._warmed = False

    # ── LSP calls ──────────────────────────────────────────────────────
    def workspace_symbol(self, query: str) -> list[PlainSymbol]:
        """Serialized — only one workspace/symbol at a time."""
        with self._ws_symbol_lock:
            raw = self._request(
                "workspace/symbol", {"query": query},
                timeout_s=self.query_timeout_s,
            ) or []
        out = []
        for s in raw:
            loc = s["location"]
            out.append(PlainSymbol(
                name=s["name"],
                kind=s["kind"],
                kind_name=KIND_NAMES.get(s["kind"], f"kind-{s['kind']}"),
                file=uri_to_path(loc["uri"]),
                line=loc["range"]["start"]["line"] + 1,
                column=loc["range"]["start"]["character"] + 1,
                container=s.get("containerName"),
            ))
        return out

    def document_symbol(self, file_path: str) -> list[dict]:
        self._ensure_open(file_path)
        return self._request(
            "textDocument/documentSymbol",
            {"textDocument": {"uri": path_to_uri(file_path)}},
            timeout_s=self.query_timeout_s,
        ) or []

    def get_source_snippet(self, file_path: str, line: int, context_lines: int = 15) -> str:
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.read().split("\n")
        except OSError:
            return "[snippet unavailable]"
        start = max(0, line - 1 - context_lines)
        end = min(len(lines), line - 1 + context_lines + 1)
        return "\n".join(lines[start:end])

    def warmup(self) -> dict:
        """Walk .py files under workspace, didOpen + documentSymbol each so
        pyright indexes them for workspace/symbol. Idempotent."""
        with self._warmup_lock:
            if self._warmed:
                return {"files_found": 0, "files_indexed": 0, "failed": 0, "note": "already-warmed"}
            files = self._collect_py_files()
            failed = 0
            for f in files:
                try:
                    self.document_symbol(f)
                except Exception:
                    failed += 1
            report = {"files_found": len(files), "files_indexed": len(files) - failed, "failed": failed}
            self._warmed = failed == 0
            return report

    # ── internals ──────────────────────────────────────────────────────
    def _collect_py_files(self) -> list[str]:
        out: list[str] = []
        for root, dirs, files in os.walk(self.workspace_dir):
            # in-place filter
            dirs[:] = [d for d in dirs if d not in WARMUP_SKIP_DIRS and not d.startswith(".")]
            for name in files:
                if name.endswith(".py"):
                    out.append(os.path.join(root, name))
                    if len(out) >= self.warmup_file_cap:
                        return out
        return out

    def _ensure_open(self, file_path: str) -> None:
        if file_path in self._opened:
            return
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            return
        self._notify("textDocument/didOpen", {
            "textDocument": {
                "uri": path_to_uri(file_path),
                "languageId": "python",
                "version": 1,
                "text": text,
            }
        })
        self._opened.add(file_path)

    def _request(self, method: str, params: Any, timeout_s: float = 60.0) -> Any:
        with self._id_lock:
            self._next_id += 1
            rid = self._next_id
        fut: Future = Future()
        with self._pending_lock:
            self._pending[rid] = fut
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        try:
            return fut.result(timeout=timeout_s)
        except Exception:
            with self._pending_lock:
                self._pending.pop(rid, None)
            raise

    def _notify(self, method: str, params: Any) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _send(self, msg: dict) -> None:
        if not self._proc or not self._proc.stdin:
            raise RuntimeError("pyright process not running")
        body = json.dumps(msg).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        try:
            self._proc.stdin.write(header + body)
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise RuntimeError(f"pyright pipe broken: {e}")

    def _reader_loop(self) -> None:
        assert self._proc and self._proc.stdout
        stream = self._proc.stdout
        buf = b""
        while True:
            chunk = stream.read(4096)
            if not chunk:
                break
            buf += chunk
            while True:
                sep = buf.find(b"\r\n\r\n")
                if sep < 0:
                    break
                header = buf[:sep].decode("ascii", errors="replace")
                m = None
                for line in header.split("\r\n"):
                    if line.lower().startswith("content-length:"):
                        try:
                            m = int(line.split(":", 1)[1].strip())
                        except ValueError:
                            m = None
                        break
                if m is None:
                    buf = buf[sep + 4:]
                    continue
                total = sep + 4 + m
                if len(buf) < total:
                    break
                body_bytes = buf[sep + 4:total]
                buf = buf[total:]
                try:
                    msg = json.loads(body_bytes.decode("utf-8"))
                except Exception:
                    continue
                self._handle_message(msg)

        # stream closed — fail any pending futures
        with self._pending_lock:
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(RuntimeError("pyright stdout closed"))
            self._pending.clear()

    def _handle_message(self, msg: dict) -> None:
        # server→client request: auto-ack with null (like the TS version)
        if "method" in msg and "id" in msg:
            method = msg["method"]
            rid = msg["id"]
            result: Any = None
            if method == "workspace/workspaceFolders":
                result = [{"uri": self.workspace_uri, "name": "root"}]
            elif method == "workspace/configuration":
                result = []
            self._send({"jsonrpc": "2.0", "id": rid, "result": result})
            return
        # response
        if "id" in msg and ("result" in msg or "error" in msg):
            rid = msg["id"]
            with self._pending_lock:
                fut = self._pending.pop(rid, None)
            if fut is None or fut.done():
                return
            if "error" in msg:
                err = msg["error"]
                fut.set_exception(RuntimeError(f"LSP error {err.get('code')}: {err.get('message')}"))
            else:
                fut.set_result(msg.get("result"))
            return
        # server→client notification: log if it's a message
        if msg.get("method") == "window/logMessage":
            params = msg.get("params") or {}
            m = params.get("message")
            if m:
                self.on_log(f"[pyright] {m}")

    def _stderr_loop(self) -> None:
        assert self._proc and self._proc.stderr
        for line in iter(self._proc.stderr.readline, b""):
            self.on_log(f"[pyright stderr] {line.decode('utf-8', 'replace').rstrip()}")
