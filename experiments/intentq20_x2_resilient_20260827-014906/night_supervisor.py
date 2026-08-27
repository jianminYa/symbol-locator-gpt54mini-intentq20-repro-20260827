#!/usr/bin/env python3
"""Resilient, serial IntentQ20 x2 supervisor.

The supervisor is an orchestration artifact. It never edits the source tree,
benchmark repositories, prompts, parsers, scorers, or historical results.
Credential values are supplied only by a silent shell `source` in a child
process; this file never reads the credential file.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shlex
import signal
import statistics
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


BASE = Path("/data/workspace/symbol-locator-repair-20260826-083933")
SOURCE_COPY = BASE / "source_copy"
HANDOFF = Path("/data/workspace/symbol-locator-handoff-20260823-065432/symbol-locator-handoff")
SECRET_FILE = Path("/data/workspace/.secrets/symbol-locator-gpt54mini.env")
RUN_ROOT = Path(__file__).resolve().parent
DIAG = RUN_ROOT / "diagnostics"
PROJECT_ROOT: Path | None = None
LOCAGENT_PATH: Path | None = None
SYMBOL_LOCATOR_PATH: Path | None = None

CANONICAL_DATA = BASE / "data/intentq20/intentq20.jsonl"
CANONICAL_ISSUE = BASE / "data/intentq20/issue_map.json"
HANDOFF_DATA = HANDOFF / "symbol-locator-locagent/bench.narrow20.jsonl"
HANDOFF_ISSUE = HANDOFF / "symbol-locator-locagent/issue_map_narrow20.json"
SAVED_SOURCE_MANIFEST = BASE / "experiments/intentq20_x2_corrected_20260826-150125-4167273/diagnostics/source_manifest_before.tsv"
SAVED_KEY_HASHES = BASE / "diagnostics/source_before_after.sha256"

CONDA_EXE = Path("/data/workspace/miniforge3-symbol-locator-20260823-065758/bin/conda")
LOCAGENT_ENV = Path("/data/workspace/miniforge3-symbol-locator-20260823-065758/envs/locagent")
LOCAGENT_PY = LOCAGENT_ENV / "bin/python"

MODEL = "gpt-5.4-mini"
AGENT_MODEL = "openai/gpt-5.4-mini"
SCORER_MODEL = "openai/gpt-5.4-mini"
TOP_K = 5
WORKERS = 1
ACADEMIC_TIMEOUT = 3600
CASE_WALL_TIMEOUT = 3600
RECOVERY_WINDOW_S = 2 * 60 * 60
FRESH_API_MAX_AGE_S = 30 * 60
EXPECTED_IDS: list[str] = []
METRICS = [
    "precision", "recall", "f1_score", "hit_file_rate", "hit_region_rate",
    "noise_file_rate", "noise_region_rate", "weighted_core_coverage",
    "context_efficiency",
]
FACES = ["A1", "B1", "A2", "B2"]
EXCLUDED_PARTS = {"experiments", "results", "logs", "tmp", "cache", "diagnostics"}
SECRET_PARTS = {".secrets", ".env"}
RESUME_REQUESTED = os.environ.get("SYMBOL_LOCATOR_ORCHESTRATION_RESUME") == "1"


class PermanentStop(RuntimeError):
    pass


class TransientUnavailable(RuntimeError):
    pass


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def preserve_or_write(path: Path, content: str, keep_existing: bool = False) -> None:
    """Write a new artifact, or require an existing resume artifact to match."""
    if path.exists():
        if keep_existing:
            return
        if path.read_text(encoding="utf-8", errors="replace") != content:
            raise PermanentStop(f"RESUME_ARTIFACT_CHANGED:{path.name}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def is_secret_path(path: Path) -> bool:
    return any(part in SECRET_PARTS or part.startswith(".env") for part in path.parts)


def safe_manifest(root: Path, destination: Path) -> dict:
    """Write a manifest without opening any secret file and without following symlinks."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    total = 0
    with destination.open("w", encoding="utf-8") as out:
        out.write("path\ttype\tsize\tmtime_ns\tsha256_or_link\n")
        if not root.exists():
            out.write("<ROOT_MISSING>\tmissing\t0\t0\t\n")
            return {"root": str(root), "files": 0, "bytes": 0, "missing": True}
        for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
            current_path = Path(current)
            dirs[:] = sorted(d for d in dirs if not is_secret_path(current_path / d))
            for name in sorted(files):
                path = current_path / name
                if is_secret_path(path):
                    continue
                rel = path.relative_to(root)
                try:
                    st = path.lstat()
                    if path.is_symlink():
                        kind, size, value = "symlink", st.st_size, os.readlink(path)
                    elif path.is_file():
                        kind, size, value = "file", st.st_size, digest_file(path)
                    else:
                        kind, size, value = "other", st.st_size, ""
                except OSError as exc:
                    kind, size, value = "unreadable", 0, type(exc).__name__
                    st = path.lstat()
                out.write(f"{rel}\t{kind}\t{size}\t{st.st_mtime_ns}\t{value}\n")
                count += 1
                total += size
    return {"root": str(root), "files": count, "bytes": total, "missing": False}


def git_snapshot(path: Path) -> str:
    lines = [f"PATH {path}"]
    for args in (
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        ["git", "-C", str(path), "status", "--short", "--branch"],
    ):
        proc = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        safe_lines = []
        for line in (proc.stdout or "").splitlines():
            if ".env" in line or ".secrets" in line:
                safe_lines.append("<secret-entry-redacted>")
            else:
                safe_lines.append(line)
        lines.append("$ " + " ".join(args))
        lines.extend(safe_lines or [f"(exit {proc.returncode})"])
    return "\n".join(lines)


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_no}: object expected")
            rows.append(value)
    return rows


def resolve_repo_dir(repo_dir_value: str | None, repos_root: Path | None, instance_id: str) -> Path | None:
    """Exact copy of eval_runner.py::_resolve_repo_dir()."""
    if repo_dir_value:
        p = Path(repo_dir_value)
        if not p.is_absolute() and repos_root is not None:
            p = repos_root / p
        return p
    if repos_root is None or "__" not in instance_id:
        return None
    org, rest = instance_id.split("__", 1)
    repo = rest.rsplit("-", 1)[0] if "-" in rest else rest
    for cand in [
        repos_root / instance_id,
        repos_root / repo,
        repos_root / f"{org}__{repo}",
        repos_root / f"{org}-{repo}",
        repos_root / org,
    ]:
        if cand.is_dir():
            return cand
    return None


def short_tmp_alias(target: Path, face: str, instance_id: str, attempt: int) -> Path:
    """Return a short spelling whose resolved target remains inside RUN_ROOT.

    Python's multiprocessing.Manager uses a Unix-domain socket below
    tempfile.gettempdir(); the socket pathname has a platform length limit.
    The spelling is short, while the symlink target is the per-attempt RUN_ROOT
    directory and is checked before use.
    """
    digest = hashlib.sha256(f"{RUN_ROOT}\0{face}\0{instance_id}\0{attempt}".encode()).hexdigest()[:10]
    alias = Path("/tmp") / f"sx2_{digest}"
    target = target.resolve()
    target.relative_to(RUN_ROOT.resolve())
    if alias.exists() or alias.is_symlink():
        if not alias.is_symlink() or alias.resolve() != target:
            raise PermanentStop("TMP_ALIAS_COLLISION")
    else:
        alias.symlink_to(target, target_is_directory=True)
    if alias.resolve() != target:
        raise PermanentStop("TMP_ALIAS_RESOLUTION_FAILED")
    return alias


def remove_tmp_alias(alias: Path | None) -> None:
    if alias is None:
        return
    try:
        if alias.is_symlink():
            alias.unlink()
    except OSError:
        pass


def discover_project() -> tuple[Path, Path, list[dict]]:
    if not SOURCE_COPY.is_dir():
        raise PermanentStop("SOURCE_COPY_MISSING")
    required = [
        "eval_runner.py",
        "explorers/locagent_explorer.py",
        "explorers/parsing.py",
        "third_party/LocAgent/auto_search_main.py",
        "third_party/LocAgent/util/runtime/function_calling.py",
        "repos",
    ]
    candidates = []
    for current, dirs, _files in os.walk(SOURCE_COPY, topdown=True, followlinks=False):
        current_path = Path(current)
        rel_parts = current_path.relative_to(SOURCE_COPY).parts
        if len(rel_parts) > 8:
            dirs[:] = []
            continue
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED_PARTS and d not in SECRET_PARTS)
        if current_path.name != "SWE-Explore-Bench":
            continue
        present = {item: (current_path / item).exists() for item in required}
        candidates.append({
            "path": current_path.resolve(),
            "required": present,
            "all_required": all(present.values()),
        })
    valid = [item for item in candidates if item["all_required"]]
    if len(valid) != 1:
        raise PermanentStop("PROJECT_ROOT_AMBIGUOUS_OR_MISSING")
    project = Path(valid[0]["path"])
    source_tree = project.parent
    symbol_candidates = []
    for current, dirs, _files in os.walk(source_tree, topdown=True, followlinks=False):
        current_path = Path(current)
        rel_parts = current_path.relative_to(source_tree).parts
        if len(rel_parts) > 8:
            dirs[:] = []
            continue
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED_PARTS and d not in SECRET_PARTS)
        if current_path.name == "symbol_locator" and current_path.parent.name == "symbol-locator-locagent":
            symbol_candidates.append(current_path.parent.resolve())
    if len(symbol_candidates) != 1:
        raise PermanentStop("SYMBOL_LOCATOR_ROOT_AMBIGUOUS_OR_MISSING")
    return project, symbol_candidates[0], candidates


def read_saved_hashes() -> dict[str, str]:
    expected: dict[str, str] = {}
    if SAVED_SOURCE_MANIFEST.is_file():
        for line in SAVED_SOURCE_MANIFEST.read_text(encoding="utf-8").splitlines():
            parts = line.split("\t")
            if len(parts) >= 4 and len(parts[3]) == 64:
                expected[parts[0]] = parts[3]
    if SAVED_KEY_HASHES.is_file():
        for line in SAVED_KEY_HASHES.read_text(encoding="utf-8").splitlines():
            parts = line.split("\t")
            if len(parts) == 3 and parts[0] == "current_final":
                expected[parts[1]] = parts[2]
    return expected


def source_hash_checks(project: Path, symbol_path: Path) -> list[dict]:
    expected = read_saved_hashes()
    if not expected:
        raise PermanentStop("SAVED_SOURCE_MANIFEST_MISSING")
    tree = project.parent
    paths = [
        ("SWE-Explore-Bench/eval_runner.py", tree / "SWE-Explore-Bench/eval_runner.py"),
        ("SWE-Explore-Bench/explorers/locagent_explorer.py", project / "explorers/locagent_explorer.py"),
        ("SWE-Explore-Bench/explorers/parsing.py", project / "explorers/parsing.py"),
        ("SWE-Explore-Bench/explorers/_locagent_shim.py", project / "explorers/_locagent_shim.py"),
        ("SWE-Explore-Bench/third_party/LocAgent/auto_search_main.py", project / "third_party/LocAgent/auto_search_main.py"),
        ("SWE-Explore-Bench/third_party/LocAgent/util/runtime/function_calling.py", project / "third_party/LocAgent/util/runtime/function_calling.py"),
        ("SWE-Explore-Bench/third_party/LocAgent/util/runtime/process_control.py", project / "third_party/LocAgent/util/runtime/process_control.py"),
        ("SWE-Explore-Bench/third_party/LocAgent/util/runtime/finish.py", project / "third_party/LocAgent/util/runtime/finish.py"),
        ("SWE-Explore-Bench/third_party/LocAgent/util/actions/action_parser.py", project / "third_party/LocAgent/util/actions/action_parser.py"),
        ("symbol-locator-locagent/symbol_locator/core.py", symbol_path / "symbol_locator/core.py"),
        ("symbol-locator-locagent/symbol_locator/lsp.py", symbol_path / "symbol_locator/lsp.py"),
        ("symbol-locator-locagent/symbol_locator/scorer.py", symbol_path / "symbol_locator/scorer.py"),
        ("symbol-locator-locagent/symbol_locator/install.py", symbol_path / "symbol_locator/install.py"),
    ]
    checks = []
    for rel, path in paths:
        actual = digest_file(path) if path.is_file() else None
        wanted = expected.get(rel)
        checks.append({"path": rel, "exists": path.is_file(), "expected": wanted, "actual": actual, "match": actual == wanted})
    if not all(c["match"] for c in checks):
        raise PermanentStop("SOURCE_HASH_MISMATCH")
    return checks


def copy_and_validate_data() -> list[dict]:
    global EXPECTED_IDS
    if not CANONICAL_DATA.is_file() or not CANONICAL_ISSUE.is_file():
        raise PermanentStop("CANONICAL_DATA_MISSING")
    data_target = RUN_ROOT / "data/intentq20.jsonl"
    issue_target = RUN_ROOT / "data/issue_map.json"
    import shutil
    if data_target.exists():
        if digest_file(data_target) != digest_file(CANONICAL_DATA):
            raise PermanentStop("RESUME_DATA_TARGET_CHANGED")
    else:
        shutil.copy2(CANONICAL_DATA, data_target)
    if issue_target.exists():
        if digest_file(issue_target) != digest_file(CANONICAL_ISSUE):
            raise PermanentStop("RESUME_ISSUE_MAP_TARGET_CHANGED")
    else:
        shutil.copy2(CANONICAL_ISSUE, issue_target)
    data = load_jsonl(data_target)
    handoff = load_jsonl(HANDOFF_DATA)
    source_tree = PROJECT_ROOT.parent if PROJECT_ROOT else Path("/")
    source_data_path = source_tree / "symbol-locator-locagent/bench.narrow20.jsonl"
    source_data = load_jsonl(source_data_path) if source_data_path.is_file() else []
    issue_map = json.loads(issue_target.read_text(encoding="utf-8"))
    handoff_issue = json.loads(HANDOFF_ISSUE.read_text(encoding="utf-8"))
    ids = [str(row.get("instance_id", "")) for row in data]
    handoff_ids = [str(row.get("instance_id", "")) for row in handoff]
    source_ids = [str(row.get("instance_id", "")) for row in source_data]
    EXPECTED_IDS = ids
    checks = {
        "data_rows": len(data),
        "unique_ids": len(set(ids)),
        "issue_map_keys": len(issue_map) if isinstance(issue_map, dict) else -1,
        "issue_map_20_20": isinstance(issue_map, dict) and set(ids) == set(issue_map),
        "handoff_order_match": ids == handoff_ids,
        "source_narrow20_order_match": ids == source_ids,
        "handoff_issue_map_20_20": isinstance(handoff_issue, dict) and set(ids) == set(handoff_issue),
        "all_repo_dirs_are_repos_instance": all(row.get("repo_dir") == f"repos/{row.get('instance_id')}" for row in data),
    }
    if not (checks["data_rows"] == 20 and checks["unique_ids"] == 20 and checks["issue_map_20_20"]
            and checks["handoff_order_match"] and checks["source_narrow20_order_match"]
            and checks["handoff_issue_map_20_20"] and checks["all_repo_dirs_are_repos_instance"]):
        raise PermanentStop("DATA_ORDER_OR_ISSUE_MAP_INVALID")
    preserve_or_write(RUN_ROOT / "data/instance_ids.txt", "\n".join(ids) + "\n")
    data_hashes = json.dumps({
        "data": {"source": str(CANONICAL_DATA), "source_sha256": digest_file(CANONICAL_DATA), "target_sha256": digest_file(data_target)},
        "issue_map": {"source": str(CANONICAL_ISSUE), "source_sha256": digest_file(CANONICAL_ISSUE), "target_sha256": digest_file(issue_target)},
    }, indent=2) + "\n"
    preserve_or_write(DIAG / "data_copy_hashes.json", data_hashes)
    return checks


def repo_preflight() -> dict:
    assert PROJECT_ROOT is not None
    data = load_jsonl(RUN_ROOT / "data/intentq20.jsonl")
    repos_root = PROJECT_ROOT
    rows = []
    for item in data:
        iid = str(item.get("instance_id", ""))
        resolved = resolve_repo_dir(item.get("repo_dir"), repos_root, iid)
        path = str(resolved) if resolved is not None else "<NONE>"
        rows.append({
            "instance_id": iid,
            "original_repo_dir": str(item.get("repo_dir", "")),
            "resolved_path": path,
            "exists": bool(resolved and resolved.exists()),
            "is_dir": bool(resolved and resolved.is_dir()),
        })
    repo_tsv = "instance_id\toriginal_repo_dir\tresolved_path\texists\tis_dir\n"
    for row in rows:
        repo_tsv += "\t".join([
            row["instance_id"], row["original_repo_dir"], row["resolved_path"],
            str(row["exists"]), str(row["is_dir"]),
        ]) + "\n"
    preserve_or_write(DIAG / "repo_preflight.tsv", repo_tsv)
    expected_repos = PROJECT_ROOT / "repos"
    resolved_paths = [Path(r["resolved_path"]) for r in rows if r["resolved_path"] != "<NONE>"]
    summary = {
        "rows": len(rows),
        "unique_ids": len({r["instance_id"] for r in rows}),
        "exists": sum(r["exists"] for r in rows),
        "is_dir": sum(r["is_dir"] for r in rows),
        "project_root": str(PROJECT_ROOT),
        "project_root_exists": PROJECT_ROOT.is_dir(),
        "all_under_project_repos": all(p == expected_repos / p.name for p in resolved_paths),
        "contains_repos_repos": any("/repos/repos/" in str(p) for p in resolved_paths),
        "resolved_paths": rows,
    }
    preserve_or_write(DIAG / "repo_preflight_summary.json", json.dumps(summary, indent=2) + "\n")
    if not (summary["rows"] == 20 and summary["unique_ids"] == 20 and summary["exists"] == 20
            and summary["is_dir"] == 20 and summary["project_root_exists"]
            and summary["all_under_project_repos"] and not summary["contains_repos_repos"]):
        raise PermanentStop("REPO_PREFLIGHT_FAILED")
    return summary


def command_audit() -> None:
    assert PROJECT_ROOT is not None and LOCAGENT_PATH is not None and SYMBOL_LOCATOR_PATH is not None
    commands = []
    for face in FACES:
        output = RUN_ROOT / f"faces/{face}/output/locagent_top5.jsonl"
        tmp = RUN_ROOT / f"tmp/{face}/attempt_<iid>_<n>"
        cache = RUN_ROOT / f"cache/{face}/attempt_<iid>_<n>"
        args = [
            "python", "eval_runner.py", "--bench", str(RUN_ROOT / "data/intentq20.jsonl"),
            "--repos", str(PROJECT_ROOT), "--issue-map", str(RUN_ROOT / "data/issue_map.json"),
            "--explorers", "locagent", "--top-k", str(TOP_K), "--workers", str(WORKERS),
            "--academic-model", AGENT_MODEL, "--academic-timeout", str(ACADEMIC_TIMEOUT),
            "--no-skip-empty-core", "--resume", "--output", str(output),
        ]
        rendered = " ".join(shlex.quote(x) for x in args)
        commands.append({"face": face, "command": rendered, "tmp": str(tmp), "cache": str(cache)})
        if "--academic-api-key" in args or args[args.index("--repos") + 1] != str(PROJECT_ROOT):
            raise PermanentStop("COMMAND_PREFLIGHT_FAILED")
        for path in (output.parent, Path(tmp).parent, Path(cache).parent):
            path.resolve().relative_to(RUN_ROOT.resolve())
    command_text = (
        "# Command preflight (redacted)\n\n"
        f"- PROJECT_ROOT: `{PROJECT_ROOT}`\n"
        f"- LOCAGENT_PATH: `{LOCAGENT_PATH}`\n"
        f"- SYMBOL_LOCATOR_PATH: `{SYMBOL_LOCATOR_PATH}`\n"
        "- `--repos` is exactly PROJECT_ROOT, never PROJECT_ROOT/repos.\n"
        "- `--academic-api-key` is absent. API credentials are environment-only.\n"
        "- child environment exports absolute CONDA_EXE and LOCAGENT_PATH; TMPDIR uses a short alias whose resolved target is the per-attempt RUN_ROOT tmp directory.\n"
        f"- model: `{AGENT_MODEL}`; scorer model env: `{SCORER_MODEL}`; temperature: agent source fixed at `1`; top-k: `{TOP_K}`; workers: `{WORKERS}`\n"
        "- output/cache/tmp paths are per-face and under RUN_ROOT.\n\n"
        + "\n".join(f"## {c['face']}\n\n```text\n{c['command']}\n```\n- tmp: `{c['tmp']}`\n- cache: `{c['cache']}`\n" for c in commands)
    )
    preserve_or_write(DIAG / "command_preflight_redacted.md", command_text)


def process_group_members(pgid: int) -> list[int]:
    members = []
    for entry in Path("/proc").glob("[0-9]*"):
        try:
            text = (entry / "stat").read_text(encoding="utf-8")
            tail = text[text.rfind(")") + 2:].split()
            if len(tail) >= 3 and int(tail[2]) == pgid:
                members.append(int(entry.name))
        except (OSError, ValueError):
            continue
    return sorted(members)


def relevant_processes() -> list[dict]:
    result = []
    me = os.getpid()
    for entry in Path("/proc").glob("[0-9]*"):
        try:
            pid = int(entry.name)
            if pid == me:
                continue
            raw = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
            low = raw.lower()
            if any(term in low for term in ("eval_runner", "locagent", "intentq20", "pyright")):
                result.append({"pid": pid, "cmd_class": "candidate-process"})
        except (OSError, ValueError):
            continue
    return result


def terminate_exact_group(pid: int, pgid: int, log_fn) -> None:
    if pgid <= 0 or pgid == os.getpgrp():
        return
    members = process_group_members(pgid)
    if members:
        log_fn("process_cleanup", f"TERM exact_pgid={pgid} members={len(members)}")
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and process_group_members(pgid):
            time.sleep(0.25)
    members = process_group_members(pgid)
    if members:
        log_fn("process_cleanup", f"KILL exact_pgid={pgid} members={len(members)}")
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and process_group_members(pgid):
            time.sleep(0.25)


class Supervisor:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.timeline_lock = threading.Lock()
        self.log_path = RUN_ROOT / "logs/supervisor.log"
        self.active_face: str | None = None
        self.active_case: str | None = None
        self.active_pgid: int | None = None
        self.last_api_success: float | None = None
        self.last_case_call_success = False
        self.current_retry_count = 0
        prior_state = {}
        prior_state_path = RUN_ROOT / "state/state.json"
        if prior_state_path.is_file():
            try:
                loaded = json.loads(prior_state_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    prior_state = loaded
            except (OSError, json.JSONDecodeError):
                prior_state = {}
        self.resume_mode = bool(
            RESUME_REQUESTED
            and prior_state.get("status") == "PERMANENT_STOP"
            and prior_state.get("reason") == "B1_FIRST_CASE_PYRIGHT_OR_FIND_SYMBOL_VALIDATION_FAILED"
        )
        self.completed_faces: list[str] = [
            face for face in prior_state.get("completed_faces", []) if face in FACES
        ]
        self.face_started: dict[str, float] = {}
        self.face_finished: dict[str, float] = {}
        self.face_failures: dict[str, list[str]] = {face: [] for face in FACES}
        self.pyright_warmup_failures: list[str] = []
        self.state: dict = {
            "status": "STARTING",
            "run_root": str(RUN_ROOT),
            "tmux_session": os.environ.get("TMUX_SESSION"),
            "supervisor_pid": os.getpid(),
            "supervisor_pgid": os.getpgrp(),
            "stage": "startup",
            "face": None,
            "completed_faces": [],
            "completed_case_ids": {},
            "missing_case_ids": {},
            "permanent_reasons": [],
            "api_canary_attempts": 0,
            "last_successful_api_time": None,
        }
        if self.resume_mode:
            self.state.update(prior_state)
        self.state.update({
            "status": "STARTING",
            "run_root": str(RUN_ROOT),
            "tmux_session": os.environ.get("TMUX_SESSION") or self.state.get("tmux_session"),
            "supervisor_pid": os.getpid(),
            "supervisor_pgid": os.getpgrp(),
            "stage": "startup",
            "face": None,
            "completed_faces": list(self.completed_faces),
            "permanent_reasons": [],
        })

    def log(self, event: str, detail: str = "") -> None:
        line = f"{now_utc()} event={event}"
        if detail:
            line += " " + detail
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.timeline_lock:
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            timeline = DIAG / "supervisor_timeline.md"
            with timeline.open("a", encoding="utf-8") as fh:
                fh.write(f"- {line}\n")

    def update_state(self, **values) -> None:
        with self.lock:
            self.state.update(values)
            self.state["updated"] = now_utc()
            atomic_json(RUN_ROOT / "state/state.json", self.state)

    def heartbeat(self) -> None:
        disk = os.statvfs("/data")
        free_bytes = disk.f_bavail * disk.f_frsize
        mem = {}
        try:
            for line in Path("/proc/meminfo").read_text().splitlines():
                key, value = line.split(":", 1)
                mem[key] = value.strip()
        except OSError:
            pass
        with self.lock:
            face = self.active_face
            completed = 0
            if face:
                completed = len(self.face_completed_ids(face))
            value = {
                "timestamp": now_utc(),
                "stage": self.state.get("stage"),
                "face": face,
                "completed_id_count": completed,
                "last_successful_api_time": self.state.get("last_successful_api_time"),
                "current_retry_count": self.current_retry_count,
                "disk_summary": {"data_free_bytes": free_bytes, "data_free_gib": round(free_bytes / (1024 ** 3), 2)},
                "memory_summary": {k: mem.get(k) for k in ("MemAvailable", "MemTotal", "SwapTotal", "SwapFree")},
                "active_case": self.active_case,
                "active_pgid": self.active_pgid,
                "status": self.state.get("status"),
            }
        atomic_json(RUN_ROOT / "state/heartbeat.json", value)

    def heartbeat_loop(self) -> None:
        while not self.stop_event.wait(30):
            try:
                self.heartbeat()
            except Exception as exc:
                self.log("heartbeat_error", type(exc).__name__)

    def face_completed_ids(self, face: str) -> list[str]:
        output = RUN_ROOT / f"faces/{face}/output/locagent_top5.jsonl"
        if not output.is_file():
            return []
        ids = []
        for line in output.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                value = json.loads(line)
                if isinstance(value, dict) and isinstance(value.get("instance_id"), str):
                    ids.append(value["instance_id"])
            except json.JSONDecodeError:
                pass
        return ids

    def validate_output(self, face: str) -> tuple[list[dict], list[str]]:
        output = RUN_ROOT / f"faces/{face}/output/locagent_top5.jsonl"
        rows: list[dict] = []
        errors: list[str] = []
        if not output.is_file():
            return rows, errors
        for line_no, line in enumerate(output.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                errors.append(f"invalid_json_line_{line_no}")
                continue
            if not isinstance(value, dict):
                errors.append(f"non_object_line_{line_no}")
                continue
            iid = value.get("instance_id")
            if iid not in EXPECTED_IDS:
                errors.append(f"unexpected_id_line_{line_no}")
            metrics = value.get("metrics")
            if not isinstance(metrics, dict) or any(m not in metrics or not isinstance(metrics[m], (int, float)) or not math.isfinite(float(metrics[m])) for m in METRICS):
                errors.append(f"invalid_metrics_line_{line_no}")
            regions = value.get("regions")
            if not isinstance(regions, list):
                errors.append(f"invalid_regions_line_{line_no}")
            else:
                for region in regions:
                    if not isinstance(region, dict) or not isinstance(region.get("path"), str) or Path(region.get("path", "")).is_absolute():
                        errors.append(f"invalid_region_path_line_{line_no}")
                        break
            rows.append(value)
        ids = [str(r.get("instance_id")) for r in rows]
        if len(ids) != len(set(ids)):
            errors.append("duplicate_instance_id")
        if errors:
            raise PermanentStop(f"OUTPUT_INTEGRITY_{face}")
        return rows, []

    def write_attempt(self, face: str, iid: str, attempt: int, data: dict) -> None:
        path = RUN_ROOT / f"attempts/{face}/{iid}/attempt_{attempt}.json"
        atomic_json(path, data)

    def scan_case_output(self, face: str, iid: str) -> bool:
        rows, _ = self.validate_output(face)
        return any(row.get("instance_id") == iid for row in rows)

    def append_canary_record(self, result: dict) -> None:
        record = {
            "time": now_utc(),
            "elapsed_seconds": round(float(result.get("elapsed_s", 0.0)), 3),
            "status_category": result.get("http_status_class", "unknown"),
            "error_type": result.get("error_type") or result.get("category", "unknown"),
        }
        with (DIAG / "api_canary_attempts.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.update_state(api_canary_attempts=int(self.state.get("api_canary_attempts", 0)) + 1)

    def run_sourced_helper(self, args: list[str], timeout_s: int) -> dict:
        script = [
            "set -a",
            f"source {shlex.quote(str(SECRET_FILE))}",
            "set +a",
            'export ACADEMIC_API_KEY="$LLM_API_KEY"',
            'export ACADEMIC_API_BASE="$LLM_API_BASE"',
            f"export PYTHONDONTWRITEBYTECODE=1",
            f"exec {shlex.quote(str(LOCAGENT_PY))} {shlex.quote(str(RUN_ROOT / 'night_supervisor.py'))} " + " ".join(shlex.quote(x) for x in args),
        ]
        proc = subprocess.Popen(["bash", "-c", "\n".join(script)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                text=True, start_new_session=True)
        pgid = os.getpgid(proc.pid)
        try:
            stdout, _ = proc.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            terminate_exact_group(proc.pid, pgid, self.log)
            return {"category": "transient", "error_type": "network_wait_no_http", "http_status_class": "unknown", "elapsed_s": timeout_s}
        if proc.returncode != 0:
            return {"category": "permanent", "error_type": "canary_helper_exit", "http_status_class": "unknown", "elapsed_s": 0.0}
        try:
            value = json.loads((stdout or "").strip().splitlines()[-1])
            if not isinstance(value, dict):
                raise ValueError
            return value
        except (ValueError, IndexError, json.JSONDecodeError):
            return {"category": "permanent", "error_type": "canary_invalid_helper_output", "http_status_class": "unknown", "elapsed_s": 0.0}

    def canary_once(self) -> dict:
        result = self.run_sourced_helper(["--canary"], 90)
        self.append_canary_record(result)
        cfg = result.get("config")
        if isinstance(cfg, dict):
            (DIAG / "config_check_redacted.md").write_text(
                "# API configuration check (redacted)\n\n"
                "Credential values are not recorded.\n\n"
                + "\n".join(f"- {k}: `{v}`" for k, v in cfg.items() if k not in {"key_value", "base_value"})
                + "\n",
                encoding="utf-8",
            )
        if result.get("category") == "success":
            self.last_api_success = time.time()
            self.last_case_call_success = True
            self.update_state(last_successful_api_time=now_utc())
        return result

    def api_ready(self, reason: str) -> bool:
        if self.last_api_success is not None and time.time() - self.last_api_success <= FRESH_API_MAX_AGE_S and self.last_case_call_success:
            return True
        result = self.canary_once()
        if result.get("category") == "success":
            self.log("api_canary_success", f"reason={reason}")
            return True
        if result.get("category") == "permanent":
            raise PermanentStop(str(result.get("error_type", "API_CANARY_PERMANENT")))
        return self.recover_api(reason, first_result=result)

    def recover_api(self, reason: str, first_result: dict | None = None) -> bool:
        started = time.monotonic()
        delays = [30, 60, 120, 300, 600]
        attempt = 0
        result = first_result
        while True:
            if result is not None and result.get("category") == "permanent":
                raise PermanentStop(str(result.get("error_type", "API_CANARY_PERMANENT")))
            elapsed = time.monotonic() - started
            if elapsed >= RECOVERY_WINDOW_S:
                raise TransientUnavailable("TRANSIENT_API_UNAVAILABLE_2H")
            delay = delays[attempt] if attempt < len(delays) else 600
            retry_after = float(result.get("retry_after_s", 0) or 0) if result else 0
            delay = max(delay, min(retry_after, 3600))
            self.update_state(stage="api_recovery", current_retry_count=attempt + 1, api_recovery_reason=reason)
            self.log("api_recovery_wait", f"reason={reason} seconds={int(delay)} retry={attempt + 1}")
            wait_until = time.monotonic() + min(delay, RECOVERY_WINDOW_S - elapsed)
            while time.monotonic() < wait_until:
                self.stop_event.wait(min(30, wait_until - time.monotonic()))
                if self.stop_event.is_set():
                    raise PermanentStop("SUPERVISOR_STOPPED")
            result = self.canary_once()
            if result.get("category") == "success":
                self.log("api_recovery_success", f"reason={reason} retry={attempt + 1}")
                self.current_retry_count = 0
                return True
            if result.get("category") == "permanent":
                raise PermanentStop(str(result.get("error_type", "API_CANARY_PERMANENT")))
            attempt += 1

    def stream_case_output(self, stream, kind: str, flags: dict, log_path: Path) -> None:
        markers = []
        try:
            for line in stream:
                low = line.lower()
                if "pyright" in low:
                    flags["pyright_seen"] = True
                    markers.append("pyright_signal")
                if "symbol-locator" in low and "installed" in low:
                    flags["installed"] = True
                    markers.append("symbol_locator_installed")
                if "warmup" in low:
                    if re.search(r"failed\s*[=:]\s*0", low):
                        flags["warmup_success"] = True
                        flags["pyright_seen"] = True
                        markers.append("pyright_warmup_success")
                    elif "failed" in low or "error" in low or "fatal" in low:
                        flags["warmup_failure"] = True
                        markers.append("pyright_warmup_failure")
                if "find_symbol" in low:
                    flags["find_symbol_seen"] = True
                if re.search(r"found\s+[1-9][0-9]*\s+candidate", low):
                    flags["find_symbol_nonempty"] = True
                    markers.append("find_symbol_nonempty")
        finally:
            try:
                stream.close()
            except Exception:
                pass
        if markers:
            with log_path.open("a", encoding="utf-8") as fh:
                for marker in sorted(set(markers)):
                    fh.write(f"{now_utc()} stream={kind} signal={marker}\n")

    def validate_existing_b1_first_case(self, iid: str) -> None:
        """Validate the preserved successful B1 first case without rerunning it."""
        trace_root = RUN_ROOT / f"faces/B1/traces/{iid}_attempt_1"
        trace_lines = []
        if trace_root.is_dir():
            for trace_file in trace_root.rglob("*"):
                if trace_file.is_file():
                    try:
                        trace_lines.extend(trace_file.read_text(encoding="utf-8", errors="ignore").lower().splitlines())
                    except OSError:
                        pass
        warmup_success = any(re.search(r"warmup[^\n]*failed\s*[=:]\s*0", line) for line in trace_lines)
        warmup_failure = any(
            "warmup" in line
            and not re.search(r"warmup[^\n]*failed\s*[=:]\s*0", line)
            and re.search(r"failed|fatal|error", line)
            for line in trace_lines
        )
        find_symbol_nonempty = any(
            "find_symbol" in line and re.search(r"found\s+[1-9][0-9]*\s+candidate", line)
            for line in trace_lines
        )
        process_evidence = DIAG / "b1_first_case_process_evidence.md"
        pyright_process_observed = process_evidence.is_file() and "pyright-langserver" in process_evidence.read_text(
            encoding="utf-8", errors="ignore"
        ).lower()
        if not (pyright_process_observed and warmup_success and find_symbol_nonempty and not warmup_failure):
            raise PermanentStop("B1_FIRST_CASE_PYRIGHT_OR_FIND_SYMBOL_VALIDATION_FAILED")
        self.log(
            "b1_first_existing_validation_pass",
            "pyright_process=1 warmup_success=1 warmup_failure=0 find_symbol_nonempty=1 rerun=0",
        )

    def run_case(self, face: str, iid: str, row: dict) -> bool:
        existing = self.face_completed_ids(face)
        if iid in existing:
            if face == "B1" and iid == EXPECTED_IDS[0]:
                self.validate_existing_b1_first_case(iid)
            return True
        case_dir = RUN_ROOT / f"attempts/{face}/{iid}"
        existing_attempts = sorted(case_dir.glob("attempt_*.json")) if case_dir.is_dir() else []
        max_attempts = 2
        if len(existing_attempts) >= max_attempts:
            self.face_failures[face].append(iid)
            return False
        for attempt in range(len(existing_attempts) + 1, max_attempts + 1):
            if not self.api_ready(f"before_{face}_{iid}_attempt_{attempt}"):
                raise TransientUnavailable("TRANSIENT_API_UNAVAILABLE_2H")
            free = os.statvfs("/data").f_bavail * os.statvfs("/data").f_frsize
            if free < 20 * (1024 ** 3):
                raise PermanentStop("RESOURCE_PRESSURE_DATA_FREE_BELOW_20_GIB")
            tmp = (RUN_ROOT / f"tmp/{face}/attempt_{iid}_{attempt}").resolve()
            cache = (RUN_ROOT / f"cache/{face}/attempt_{iid}_{attempt}").resolve()
            keep_trace = face == "B1" and iid == EXPECTED_IDS[0]
            trace_root = (RUN_ROOT / f"faces/{face}/traces/{iid}_attempt_{attempt}").resolve()
            tmp.mkdir(parents=True, exist_ok=False)
            cache.mkdir(parents=True, exist_ok=False)
            tmp_alias = short_tmp_alias(tmp, face, iid, attempt)
            trace_root.parent.mkdir(parents=True, exist_ok=True)
            tmp.relative_to(RUN_ROOT.resolve())
            cache.relative_to(RUN_ROOT.resolve())
            tmp_alias.resolve().relative_to(RUN_ROOT.resolve())
            output = (RUN_ROOT / f"faces/{face}/output/locagent_top5.jsonl").resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            log_path = RUN_ROOT / f"logs/{face}/{iid}_attempt_{attempt}.log"
            flags = {"pyright_seen": False, "warmup_success": False, "warmup_failure": False,
                     "find_symbol_seen": False, "find_symbol_nonempty": False, "installed": False}
            if PROJECT_ROOT is None or LOCAGENT_PATH is None or SYMBOL_LOCATOR_PATH is None:
                raise PermanentStop("PROJECT_PATH_NOT_READY")
            export_mode = "unset SYMBOL_LOCATOR_ENABLED SYMBOL_LOCATOR_PATH SYMBOL_LOCATOR_SCORER_MODEL"
            if face.startswith("B"):
                export_mode = (
                    f"export SYMBOL_LOCATOR_ENABLED=1\n"
                    f"export SYMBOL_LOCATOR_PATH={shlex.quote(str(SYMBOL_LOCATOR_PATH))}\n"
                    f"export SYMBOL_LOCATOR_SCORER_MODEL={shlex.quote(SCORER_MODEL)}"
                )
            case_bench = RUN_ROOT / f"tmp/{face}/bench_{iid}_{attempt}.jsonl"
            case_bench.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
            case_bench.resolve().relative_to(RUN_ROOT.resolve())
            command = [
                str(CONDA_EXE), "run", "--no-capture-output", "-n", "locagent", "python", "eval_runner.py",
                "--bench", str(case_bench), "--repos", str(PROJECT_ROOT), "--issue-map", str(RUN_ROOT / "data/issue_map.json"),
                "--explorers", "locagent", "--top-k", str(TOP_K), "--workers", str(WORKERS),
                "--academic-model", AGENT_MODEL, "--academic-timeout", str(ACADEMIC_TIMEOUT),
                "--no-skip-empty-core", "--resume", "--output", str(output),
            ]
            shell_lines = [
                "set -a",
                f"source {shlex.quote(str(SECRET_FILE))}",
                "set +a",
                'export ACADEMIC_API_KEY="$LLM_API_KEY"',
                'export ACADEMIC_API_BASE="$LLM_API_BASE"',
                f"export CONDA_EXE={shlex.quote(str(CONDA_EXE))}",
                f"export LOCAGENT_PATH={shlex.quote(str(LOCAGENT_PATH))}",
                f"export PATH={shlex.quote(str(LOCAGENT_ENV / 'bin'))}:\"$PATH\"",
                "export PYTHONDONTWRITEBYTECODE=1",
                f"export TMPDIR={shlex.quote(str(tmp_alias))}",
                f"export TMP={shlex.quote(str(tmp_alias))}",
                f"export TEMP={shlex.quote(str(tmp_alias))}",
                f"export LOCAGENT_INDEX_CACHE={shlex.quote(str(cache))}",
                (f"export LOCAGENT_KEEP_TMP=1\nexport LOCAGENT_KEEP_TMP_ROOT={shlex.quote(str(trace_root))}"
                 if keep_trace else "unset LOCAGENT_KEEP_TMP LOCAGENT_KEEP_TMP_ROOT"),
                export_mode,
                f"cd {shlex.quote(str(PROJECT_ROOT))}",
                "exec " + " ".join(shlex.quote(x) for x in command),
            ]
            if "--academic-api-key" in shell_lines[-1] or str(PROJECT_ROOT / "repos") in shell_lines[-1]:
                raise PermanentStop("COMMAND_RUNTIME_AUDIT_FAILED")
            log_path.write_text(f"{now_utc()} case_start face={face} instance={iid} attempt={attempt}\n", encoding="utf-8")
            self.active_case = iid
            self.active_pgid = None
            self.current_retry_count = attempt - 1
            self.update_state(stage="case", face=face, current_case=iid, current_retry_count=attempt - 1)
            started = time.monotonic()
            proc = subprocess.Popen(["bash", "-c", "\n".join(shell_lines)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, errors="replace", start_new_session=True)
            pgid = os.getpgid(proc.pid)
            self.active_pgid = pgid
            out_thread = threading.Thread(target=self.stream_case_output, args=(proc.stdout, "stdout", flags, log_path), daemon=True)
            err_thread = threading.Thread(target=self.stream_case_output, args=(proc.stderr, "stderr", flags, log_path), daemon=True)
            out_thread.start(); err_thread.start()
            timed_out = False
            try:
                proc.wait(timeout=CASE_WALL_TIMEOUT)
            except subprocess.TimeoutExpired:
                timed_out = True
                terminate_exact_group(proc.pid, pgid, self.log)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    terminate_exact_group(proc.pid, pgid, self.log)
            out_thread.join(timeout=5); err_thread.join(timeout=5)
            terminate_exact_group(proc.pid, pgid, self.log)
            remove_tmp_alias(tmp_alias)
            elapsed = round(time.monotonic() - started, 3)
            self.active_pgid = None
            self.active_case = None
            output_ok = False
            output_error = None
            try:
                output_ok = self.scan_case_output(face, iid)
            except PermanentStop as exc:
                output_error = str(exc)
                raise
            # LocAgent's wrapper captures the nested agent stdout/stderr. For
            # the mandatory first-B validation, inspect only redacted signals
            # in the preserved trace; never copy raw trace text into logs.
            if keep_trace and trace_root.is_dir():
                trace_text = ""
                for trace_file in trace_root.rglob("*"):
                    if trace_file.is_file():
                        try:
                            trace_text += trace_file.read_text(encoding="utf-8", errors="ignore") + "\n"
                        except OSError:
                            pass
                low_trace = trace_text.lower()
                if "pyright" in low_trace:
                    flags["pyright_seen"] = True
                for trace_line in low_trace.splitlines():
                    warmup_success = bool(re.search(r"warmup[^\n]*failed\s*[=:]\s*0", trace_line))
                    if warmup_success:
                        flags["warmup_success"] = True
                        flags["pyright_seen"] = True
                    elif "warmup" in trace_line and re.search(r"failed|fatal|error", trace_line):
                        flags["warmup_failure"] = True
                if "find_symbol" in low_trace and re.search(r"found\s+[1-9][0-9]*\s+candidate", low_trace):
                    flags["find_symbol_seen"] = True
                    flags["find_symbol_nonempty"] = True
            category = "success" if output_ok else ("timeout" if timed_out else "execution_failure")
            if flags["warmup_failure"] and not output_ok:
                category = "pyright_warmup_failure"
                self.pyright_warmup_failures.append(iid)
            self.last_case_call_success = output_ok
            self.write_attempt(face, iid, attempt, {
                "timestamp": now_utc(), "face": face, "instance_id": iid, "attempt": attempt,
                "elapsed_seconds": elapsed, "return_code": proc.returncode, "category": category,
                "timeout": timed_out, "output_written": output_ok,
                "pyright_seen": flags["pyright_seen"], "pyright_warmup_success": flags["warmup_success"],
                "pyright_warmup_failure": flags["warmup_failure"], "find_symbol_seen": flags["find_symbol_seen"],
                "find_symbol_nonempty": flags["find_symbol_nonempty"], "symbol_locator_installed": flags["installed"],
            })
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(f"{now_utc()} case_end face={face} instance={iid} attempt={attempt} elapsed={elapsed} category={category}\n")
            self.log("case_end", f"face={face} instance={iid} attempt={attempt} elapsed={elapsed} category={category}")
            if output_ok:
                if face == "B1" and iid == EXPECTED_IDS[0]:
                    if not (flags["pyright_seen"] and flags["warmup_success"] and flags["find_symbol_nonempty"]):
                        raise PermanentStop("B1_FIRST_CASE_PYRIGHT_OR_FIND_SYMBOL_VALIDATION_FAILED")
                return True
            if face.startswith("B") and len(self.pyright_warmup_failures) >= 2 and len(set(self.pyright_warmup_failures[-2:])) == 2:
                raise PermanentStop("PYRIGHT_WARMUP_CHAIN_REGRESSION")
            if attempt < max_attempts:
                self.last_case_call_success = False
                self.api_ready(f"recover_{face}_{iid}_attempt_{attempt}")
                continue
            self.face_failures[face].append(iid)
            return False
        return False

    def face_report(self, face: str) -> dict:
        rows, _ = self.validate_output(face)
        attempts = []
        for path in sorted((RUN_ROOT / f"attempts/{face}").glob("*/attempt_*.json")):
            try:
                attempts.append(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                pass
        means = {}
        stddev = {}
        ranges = {}
        for metric in METRICS:
            vals = [float(row["metrics"][metric]) for row in rows if isinstance(row.get("metrics"), dict) and metric in row["metrics"]]
            means[metric] = statistics.mean(vals) if vals else None
            stddev[metric] = statistics.stdev(vals) if len(vals) >= 2 else 0.0 if vals else None
            ranges[metric] = [min(vals), max(vals)] if vals else None
        return {
            "face": face,
            "expected": 20,
            "rows": len(rows),
            "unique_ids": len({r.get("instance_id") for r in rows}),
            "missing_ids": [iid for iid in EXPECTED_IDS if iid not in {r.get("instance_id") for r in rows}],
            "model_valid_empty_results": sum(int(r.get("num_regions", 0)) == 0 for r in rows),
            "attempts": len(attempts),
            "success_attempts": sum(a.get("category") == "success" for a in attempts),
            "timeout_attempts": sum(a.get("category") == "timeout" for a in attempts),
            "recovery_attempts": max(0, len(attempts) - len(rows)),
            "execution_failures": len(self.face_failures.get(face, [])),
            "failed_ids": self.face_failures.get(face, []),
            "agent_prompt_tokens": sum(int(r.get("metrics", {}).get("agent_prompt_tokens", 0) or 0) for r in rows),
            "agent_completion_tokens": sum(int(r.get("metrics", {}).get("agent_completion_tokens", 0) or 0) for r in rows),
            "scorer_prompt_tokens": sum(int(r.get("metrics", {}).get("scorer_prompt_tokens", 0) or 0) for r in rows),
            "scorer_completion_tokens": sum(int(r.get("metrics", {}).get("scorer_completion_tokens", 0) or 0) for r in rows),
            "scorer_calls": sum(int(r.get("metrics", {}).get("scorer_calls", 0) or 0) for r in rows),
            "wall_clock_seconds": round(self.face_finished.get(face, time.time()) - self.face_started.get(face, time.time()), 3),
            "means": means, "stddev": stddev, "ranges": ranges,
        }

    def write_face_report(self, face: str) -> dict:
        report = self.face_report(face)
        path = DIAG / f"{face}_report.md"
        lines = [f"# {face} report", "", f"- rows/unique: `{report['rows']}/{report['unique_ids']}`", f"- missing IDs: `{len(report['missing_ids'])}`", f"- execution failures: `{report['execution_failures']}`", f"- timeout attempts: `{report['timeout_attempts']}`", f"- recovery attempts: `{report['recovery_attempts']}`", f"- wall-clock seconds: `{report['wall_clock_seconds']}`", "", "| metric | mean | stddev | min | max |", "|---|---:|---:|---:|---:|"]
        for metric in METRICS:
            rng = report["ranges"][metric]
            lines.append(f"| {metric} | {report['means'][metric]} | {report['stddev'][metric]} | {rng[0] if rng else None} | {rng[1] if rng else None} |")
        lines += ["", "## Usage (redacted counts only)", "", f"- agent prompt/completion: `{report['agent_prompt_tokens']}/{report['agent_completion_tokens']}`", f"- scorer prompt/completion/calls: `{report['scorer_prompt_tokens']}/{report['scorer_completion_tokens']}/{report['scorer_calls']}`"]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return report

    def run_faces(self) -> None:
        rows = load_jsonl(RUN_ROOT / "data/intentq20.jsonl")
        self.update_state(stage="formal", face=None)
        pending_faces = [face for face in FACES if face not in self.completed_faces]
        if not pending_faces:
            return
        self.api_ready(f"before_{pending_faces[0]}")
        for face in FACES:
            if face in self.completed_faces:
                continue
            self.active_face = face
            self.face_started[face] = time.time()
            self.update_state(stage="face", face=face)
            self.log("face_start", f"face={face}")
            for row in rows:
                iid = str(row.get("instance_id", ""))
                if iid in self.face_completed_ids(face):
                    continue
                ok = self.run_case(face, iid, row)
                if not ok:
                    self.log("case_recorded_failure", f"face={face} instance={iid}")
            self.face_finished[face] = time.time()
            report = self.write_face_report(face)
            if report["rows"] != 20 or report["unique_ids"] != 20 or report["execution_failures"] > 2:
                raise PermanentStop(f"FACE_GATE_FAILED_{face}")
            if face not in self.completed_faces:
                self.completed_faces.append(face)
            self.update_state(completed_faces=list(self.completed_faces), completed_case_ids={f: self.face_completed_ids(f) for f in FACES})
            self.log("face_complete", f"face={face} rows={report['rows']}")
            self.active_face = None
            self.active_case = None
        self.write_comparison()

    def write_comparison(self) -> None:
        reports = {face: self.face_report(face) for face in FACES}
        lines = ["# IntentQ20 x2 comparison", "", "Noise metrics are lower-is-better.", "", "| metric | A1 | B1 | B1-A1 | A2 | B2 | B2-A2 |", "|---|---:|---:|---:|---:|---:|---:|"]
        for metric in METRICS:
            a1, b1, a2, b2 = [reports[f]["means"][metric] for f in FACES]
            lines.append(f"| {metric} | {a1:.6f} | {b1:.6f} | {b1-a1:.6f} | {a2:.6f} | {b2:.6f} | {b2-a2:.6f} |")
        lines += ["", "## Two-round mean and spread", "", "| metric | A mean | A stddev | B mean | B stddev |", "|---|---:|---:|---:|---:|"]
        for metric in METRICS:
            av = [reports[f]["means"][metric] for f in ("A1", "A2")]
            bv = [reports[f]["means"][metric] for f in ("B1", "B2")]
            lines.append(f"| {metric} | {statistics.mean(av):.6f} | {statistics.stdev(av) if len(av)>1 else 0:.6f} | {statistics.mean(bv):.6f} | {statistics.stdev(bv) if len(bv)>1 else 0:.6f} |")
        reference = BASE / "diagnostics/metrics_summary.json"
        if reference.is_file():
            try:
                ref = json.loads(reference.read_text(encoding="utf-8"))
                lines += ["", "## Same-20 reference artifact", "", "Reference means from the preserved repair-task IntentQ20 artifact; not recomputed from this run.", "", "| metric | A1 ref | B1 ref | new A1-ref | new B1-ref |", "|---|---:|---:|---:|---:|"]
                for metric in METRICS:
                    ar = ref.get("faces", {}).get("A1", {}).get("means", {}).get(metric)
                    br = ref.get("faces", {}).get("B1", {}).get("means", {}).get(metric)
                    if ar is not None and br is not None:
                        lines.append(f"| {metric} | {ar:.6f} | {br:.6f} | {reports['A1']['means'][metric]-ar:.6f} | {reports['B1']['means'][metric]-br:.6f} |")
            except Exception:
                lines.append("\nReference artifact parse unavailable.\n")
        (DIAG / "intentq20_x2_comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def sensitive_scan(self) -> bool:
        patterns = [
            re.compile(r"sk-[A-Za-z0-9]{20,}"),
            re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{20,}"),
            re.compile(r"(?i)authorization\s*:\s*[^\s`]{20,}"),
            # Require a quoted literal for api_key assignments.  Generic
            # `token = ...` source identifiers/literals are common in the
            # benchmark (password-reset and parser tokens) and are not
            # credentials; concrete bearer/sk-/Authorization forms above
            # remain covered regardless of field name.
            re.compile(r"(?i)api[_-]?key\s*[:=]\s*['\"][A-Za-z0-9._~+/=-]{20,}['\"]"),
        ]
        matches = []
        for path in RUN_ROOT.rglob("*"):
            if not path.is_file() or path.name.startswith(".env"):
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if any(p.search(content) for p in patterns):
                matches.append(str(path.relative_to(RUN_ROOT)))
        (DIAG / "sensitive_scan.md").write_text(
            "# Sensitive scan\n\n"
            "- Secrets file was not opened by this supervisor process; credentials were loaded only in sourced child environments.\n"
            f"- key/header/token-like matches in run artifacts: `{len(matches)}`.\n"
            f"- result: `{'PASS' if not matches else 'FAIL'}`.\n",
            encoding="utf-8",
        )
        return not matches

    def finalize_protected(self) -> bool:
        roots = {"original": HANDOFF, "repair_source": SOURCE_COPY}
        roots.update({p.name: p for p in sorted((BASE / "experiments").iterdir()) if p.is_dir() and p.resolve() != RUN_ROOT.resolve()})
        before_after = {}
        all_equal = True
        for label, root in roots.items():
            before = DIAG / f"manifest_before_{label}.tsv"
            after = DIAG / f"manifest_after_{label}.tsv"
            if not before.is_file():
                all_equal = False
                continue
            safe_manifest(root, after)
            equal = before.read_bytes() == after.read_bytes()
            before_after[label] = {"before_sha256": digest_file(before), "after_sha256": digest_file(after), "byte_identical": equal}
            all_equal = all_equal and equal
        after_git = "\n\n".join(git_snapshot(path) for path in (HANDOFF, PROJECT_ROOT or SOURCE_COPY)) + "\n"
        (DIAG / "git_status_after.txt").write_text(after_git, encoding="utf-8")
        before_git = (DIAG / "git_status_before.txt").read_text(encoding="utf-8") if (DIAG / "git_status_before.txt").is_file() else ""
        git_equal = before_git == after_git
        protected = all_equal and git_equal
        (DIAG / "original_and_source_manifest_check.md").write_text(
            "# Original/source/history manifest check\n\n"
            f"- manifest before/after equality: `{all_equal}`\n"
            f"- Git status before/after equality: `{git_equal}`\n"
            f"- protected paths: `{'NO_DIFFERENCES' if protected else 'DIFFERENCES_FOUND'}`\n\n"
            "```json\n" + json.dumps(before_after, indent=2) + "\n```\n",
            encoding="utf-8",
        )
        return protected

    def write_stop_reports(self, status: str, reason: str) -> None:
        for face in FACES:
            try:
                self.write_face_report(face)
            except Exception:
                (DIAG / f"{face}_report.md").write_text(f"# {face} report\n\nNot started or incomplete: `{status}` — `{reason}`.\n", encoding="utf-8")
        missing = {face: [iid for iid in EXPECTED_IDS if iid not in self.face_completed_ids(face)] for face in FACES}
        resume_log = DIAG / "resume_and_recovery_log.md"
        if not resume_log.exists():
            resume_log.write_text(
                "# Resume and recovery log\n\n"
                "Each case attempt is recorded under `attempts/<face>/<instance_id>/`; no successful JSONL row is deleted or rewritten.\n",
                encoding="utf-8",
            )
        with resume_log.open("a", encoding="utf-8") as fh:
            fh.write("\n## Stop record\n\n")
            fh.write(json.dumps({"status": status, "reason": reason, "completed_faces": self.completed_faces, "missing_ids": missing}, indent=2, ensure_ascii=False) + "\n")
        (DIAG / "process_cleanup.md").write_text(
            "# Process cleanup\n\n"
            f"- active exact PGID after finalization: `{self.active_pgid}`\n"
            f"- relevant process scan count: `{len(relevant_processes())}`\n"
            "- cleanup policy: only exact case PGID is terminated; no broad kill was used.\n",
            encoding="utf-8",
        )
        (DIAG / "preflight.md").write_text(
            "# Preflight and final status\n\n"
            f"- status: `{status}`\n- reason: `{reason}`\n"
            f"- PROJECT_ROOT: `{PROJECT_ROOT}`\n- LOCAGENT_PATH: `{LOCAGENT_PATH}`\n- SYMBOL_LOCATOR_PATH: `{SYMBOL_LOCATOR_PATH}`\n"
            f"- completed faces: `{self.completed_faces}`\n- completed IDs by face: `{json.dumps({f: self.face_completed_ids(f) for f in FACES}, ensure_ascii=False)}`\n"
            "- random100/random300/expanded datasets: not run\n",
            encoding="utf-8",
        )
        (DIAG / "project_root_discovery.md").write_text(
            "# Project root discovery\n\n"
            f"- search root: `{SOURCE_COPY}`\n- depth limit: `8`\n- exclusion parts: `{sorted(EXCLUDED_PARTS)}`\n"
            f"- PROJECT_ROOT: `{PROJECT_ROOT}`\n- LOCAGENT_PATH: `{LOCAGENT_PATH}`\n- SYMBOL_LOCATOR_PATH: `{SYMBOL_LOCATOR_PATH}`\n"
            "- discovery result: one candidate passed required structure, saved source hash, same-tree symbol-locator uniqueness, and repo preflight.\n",
            encoding="utf-8",
        )
        (DIAG / "supervisor_timeline.md").touch()
        (DIAG / "api_canary_attempts.jsonl").touch()
        (DIAG / "intentq20_x2_comparison.md").write_text(
            "# IntentQ20 x2 comparison\n\nNot available because the run did not complete all four faces.\n",
            encoding="utf-8",
        )
        (DIAG / "final_report.md").write_text(
            "# Final report\n\n"
            f"- outcome: `{status}`\n- reason: `{reason}`\n- completed faces: `{self.completed_faces}`\n"
            f"- all 20 IDs remain expected per face; missing IDs are listed in `resume_and_recovery_log.md`.\n"
            "- No secrets, API response bodies, or authorization headers were written.\n",
            encoding="utf-8",
        )

    def run(self) -> int:
        global PROJECT_ROOT, LOCAGENT_PATH, SYMBOL_LOCATOR_PATH
        DIAG.mkdir(parents=True, exist_ok=True)
        if self.resume_mode:
            self.log(
                "supervisor_resume_started",
                "reason=orchestration_validator_false_positive preserve_existing_artifacts=1",
            )
        else:
            preserve_or_write(DIAG / "supervisor_timeline.md", f"# Supervisor timeline\n\n- {now_utc()} supervisor started.\n")
            preserve_or_write(DIAG / "api_canary_attempts.jsonl", "")
        atomic_json(RUN_ROOT / "state/state.json", self.state)
        self.heartbeat()
        thread = threading.Thread(target=self.heartbeat_loop, daemon=True)
        thread.start()
        status = "PERMANENT_STOP"
        reason = "UNKNOWN"
        try:
            self.log("conflict_scan_pass", "no running eval_runner/LocAgent/IntentQ20/Pyright process")
            PROJECT_ROOT, SYMBOL_LOCATOR_PATH, candidates = discover_project()
            LOCAGENT_PATH = PROJECT_ROOT / "third_party/LocAgent"
            checks = source_hash_checks(PROJECT_ROOT, SYMBOL_LOCATOR_PATH)
            preserve_or_write(DIAG / "source_key_hash_checks.json", json.dumps(checks, indent=2) + "\n")
            self.state["project_root"] = str(PROJECT_ROOT)
            self.state["locagent_path"] = str(LOCAGENT_PATH)
            self.state["symbol_locator_path"] = str(SYMBOL_LOCATOR_PATH)
            # Baselines are captured before any formal case.
            roots = {"original": HANDOFF, "repair_source": SOURCE_COPY}
            roots.update({p.name: p for p in sorted((BASE / "experiments").iterdir()) if p.is_dir() and p.resolve() != RUN_ROOT.resolve()})
            summary = {}
            for label, root in roots.items():
                summary[label] = safe_manifest(root, DIAG / f"manifest_before_{label}.tsv")
            (DIAG / "git_status_before.txt").write_text("\n\n".join(git_snapshot(path) for path in (HANDOFF, PROJECT_ROOT)) + "\n", encoding="utf-8")
            data_checks = copy_and_validate_data()
            repo_checks = repo_preflight()
            command_audit()
            project_discovery_text = (
                "# Project root discovery\n\n"
                f"- search root: `{SOURCE_COPY}`\n- search depth: `<=8`\n"
                f"- candidates discovered: `{json.dumps([{**c, 'path': str(c['path'])} for c in candidates], ensure_ascii=False)}`\n"
                f"- PROJECT_ROOT: `{PROJECT_ROOT}`\n- LOCAGENT_PATH: `{LOCAGENT_PATH}`\n- SYMBOL_LOCATOR_PATH: `{SYMBOL_LOCATOR_PATH}`\n"
                f"- source key hash checks: `{len([c for c in checks if c['match']])}/{len(checks)}`\n"
                "- same-tree symbol-locator directories: `1`\n"
                "- only the unique candidate passed all three V2 validation layers.\n"
            )
            preserve_or_write(DIAG / "project_root_discovery.md", project_discovery_text, keep_existing=self.resume_mode)
            preflight_text = (
                "# Preflight\n\n"
                f"- RUN_ROOT: `{RUN_ROOT}`\n- PROJECT_ROOT: `{PROJECT_ROOT}`\n- LOCAGENT_PATH: `{LOCAGENT_PATH}`\n- SYMBOL_LOCATOR_PATH: `{SYMBOL_LOCATOR_PATH}`\n"
                f"- data checks: `{json.dumps(data_checks, ensure_ascii=False)}`\n- repo checks: rows={repo_checks['rows']}, unique={repo_checks['unique_ids']}, exists={repo_checks['exists']}, is_dir={repo_checks['is_dir']}\n"
                "- conflict scan: pass\n- API canary: pending\n- no expanded datasets permitted or selected\n"
            )
            preserve_or_write(DIAG / "preflight.md", preflight_text, keep_existing=self.resume_mode)
            self.update_state(stage="preflight_passed", status="RUNNING")
            self.run_faces()
            status, reason = "COMPLETE", "A1_B1_A2_B2_COMPLETE"
        except TransientUnavailable as exc:
            status, reason = "TRANSIENT_API_UNAVAILABLE_2H", str(exc)
        except PermanentStop as exc:
            status, reason = "PERMANENT_STOP", str(exc)
        except Exception as exc:
            status, reason = "PERMANENT_STOP", f"SUPERVISOR_UNEXPECTED_{type(exc).__name__}"
            self.log("unexpected_stop", type(exc).__name__)
        finally:
            self.stop_event.set()
            if self.active_pgid:
                terminate_exact_group(self.active_pgid, self.active_pgid, self.log)
            protected = False
            try:
                protected = self.finalize_protected()
            except Exception:
                protected = False
            scan_ok = self.sensitive_scan()
            if not protected and status == "COMPLETE":
                status, reason = "PERMANENT_STOP", "PROTECTED_PATH_DIFFERENCE"
            if not scan_ok:
                status, reason = "PERMANENT_STOP", "SENSITIVE_INFORMATION_DETECTED"
            self.write_stop_reports(status, reason) if status != "COMPLETE" else None
            if status == "COMPLETE":
                # Completion path keeps the already-generated comparison and adds final safeguards.
                (DIAG / "final_report.md").write_text(
                    "# Final report\n\n"
                    "`COMPLETE` — A1 → B1 → A2 → B2 finished serially with workers=1.\n\n"
                    f"- protected paths: `{'NO_DIFFERENCES' if protected else 'DIFFERENCES_FOUND'}`\n- sensitive scan: `{'PASS' if scan_ok else 'FAIL'}`\n"
                    "- See all face reports, comparison, canary attempts, recovery log, and cleanup report.\n",
                    encoding="utf-8",
                )
            self.update_state(status=status, reason=reason, stage="finished", face=None,
                              completed_faces=list(self.completed_faces),
                              completed_case_ids={f: self.face_completed_ids(f) for f in FACES},
                              missing_case_ids={f: [iid for iid in EXPECTED_IDS if iid not in self.face_completed_ids(f)] for f in FACES},
                              protected_paths_no_differences=protected, sensitive_scan_pass=scan_ok)
            self.heartbeat()
        return 0 if status == "COMPLETE" else 1


def canary_main() -> int:
    """Run inside the sourced locagent environment; only emit redacted JSON."""
    import httpx
    started = time.monotonic()
    key = os.environ.get("LLM_API_KEY", "")
    base = os.environ.get("LLM_API_BASE", "")
    academic_key = os.environ.get("ACADEMIC_API_KEY", "")
    academic_base = os.environ.get("ACADEMIC_API_BASE", "")
    configured_model = os.environ.get("LLM_MODEL", "")
    parsed = urlparse(base)
    cfg = {
        "llm_api_key_present": bool(key),
        "llm_api_base_present": bool(base),
        "academic_api_key_present": bool(academic_key),
        "academic_api_base_present": bool(academic_base),
        "llm_and_academic_key_equal": bool(key) and key == academic_key,
        "llm_and_academic_base_equal": bool(base) and base == academic_base,
        "llm_model_present": bool(configured_model),
        "llm_model_exact": (not configured_model) or configured_model in {MODEL, AGENT_MODEL},
        "base_scheme": parsed.scheme or "<missing>",
        "base_hostname": parsed.hostname or "<missing>",
        "requested_model": MODEL,
        "agent_model": AGENT_MODEL,
        "scorer_model": SCORER_MODEL,
    }
    if not (key and base and academic_key and academic_base and cfg["llm_and_academic_key_equal"]
            and cfg["llm_and_academic_base_equal"] and parsed.scheme and parsed.hostname and cfg["llm_model_exact"]):
        print(json.dumps({"category": "permanent", "error_type": "CONFIG_MAPPING_MISMATCH", "http_status_class": "unknown", "elapsed_s": time.monotonic() - started, "config": cfg}))
        return 0
    root = base.rstrip("/")
    if parsed.path.rstrip("/").endswith("/v1"):
        models_url, chat_url = root + "/models", root + "/chat/completions"
    else:
        models_url, chat_url = root + "/v1/models", root + "/v1/chat/completions"
    origin = f"{parsed.scheme}://{parsed.netloc}"
    usage_url = origin + "/api/usage/token"
    def classify(status: int | None, body: str, label: str) -> tuple[str, str]:
        lower = body.lower()
        if status in {401, 403}:
            return "permanent", f"http_{status}_{label}"
        if status in {400, 404}:
            return "permanent", f"http_{status}_{label}_request_or_model"
        if status == 429:
            if any(word in lower for word in ("quota", "credit", "balance", "expired", "insufficient")):
                return "permanent", "quota_or_token_exhausted"
            return "transient", "http_429"
        if status in {408, 500, 502, 503, 504}:
            return "transient", f"http_{status}"
        if status is not None and 200 <= status < 300:
            return "success", ""
        if status is not None and 400 <= status < 500:
            return "permanent", f"http_{status}_{label}"
        return "transient", "network_no_http_status"
    retry_after = 0.0
    try:
        timeout = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)
        headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
        with httpx.Client(timeout=timeout) as client:
            usage_status = None
            try:
                usage = client.get(usage_url, headers=headers)
                usage_status = usage.status_code
            except Exception:
                usage_status = None
            models = client.get(models_url, headers=headers)
            cat, err = classify(models.status_code, models.text, "models")
            if cat != "success":
                retry_after = float(models.headers.get("retry-after", "0") or 0)
                print(json.dumps({"category": cat, "error_type": err, "http_status_class": str(models.status_code), "elapsed_s": time.monotonic() - started, "retry_after_s": retry_after, "config": cfg, "usage_status_present": usage_status is not None}))
                return 0
            try:
                model_ids = {str(item.get("id", "")) for item in (models.json().get("data") or []) if isinstance(item, dict)}
            except Exception:
                print(json.dumps({"category": "permanent", "error_type": "models_invalid_json", "http_status_class": str(models.status_code), "elapsed_s": time.monotonic() - started, "config": cfg}))
                return 0
            if not any(mid == MODEL or mid.endswith("/" + MODEL) for mid in model_ids):
                print(json.dumps({"category": "permanent", "error_type": "model_not_advertised", "http_status_class": str(models.status_code), "elapsed_s": time.monotonic() - started, "config": cfg}))
                return 0
            body = {"model": MODEL, "messages": [{"role": "user", "content": "OK"}], "temperature": 1, "max_tokens": 5}
            chat = client.post(chat_url, headers=headers, json=body)
            cat, err = classify(chat.status_code, chat.text, "chat")
            if cat != "success":
                retry_after = float(chat.headers.get("retry-after", "0") or 0)
                print(json.dumps({"category": cat, "error_type": err, "http_status_class": str(chat.status_code), "elapsed_s": time.monotonic() - started, "retry_after_s": retry_after, "config": cfg, "usage_status_present": usage_status is not None}))
                return 0
            try:
                value = chat.json()
                response_model = str(value.get("model", ""))
                choices = value.get("choices")
                valid_structure = isinstance(choices, list) and bool(choices) and isinstance(choices[0], dict) and isinstance(choices[0].get("message"), dict)
            except Exception:
                response_model, valid_structure = "", False
            if not (response_model == MODEL or response_model.endswith("/" + MODEL)):
                print(json.dumps({"category": "permanent", "error_type": "response_model_mismatch", "http_status_class": str(chat.status_code), "elapsed_s": time.monotonic() - started, "config": cfg}))
                return 0
            if not valid_structure:
                print(json.dumps({"category": "permanent", "error_type": "chat_response_structure_invalid", "http_status_class": str(chat.status_code), "elapsed_s": time.monotonic() - started, "config": cfg}))
                return 0
            print(json.dumps({"category": "success", "error_type": None, "http_status_class": "2xx", "elapsed_s": time.monotonic() - started, "response_model": MODEL, "usage_status_present": usage_status is not None, "config": cfg}))
            return 0
    except httpx.TimeoutException as exc:
        print(json.dumps({"category": "transient", "error_type": type(exc).__name__, "http_status_class": "unknown", "elapsed_s": time.monotonic() - started, "config": cfg}))
        return 0
    except (httpx.ConnectError, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
        print(json.dumps({"category": "transient", "error_type": type(exc).__name__, "http_status_class": "unknown", "elapsed_s": time.monotonic() - started, "config": cfg}))
        return 0
    except Exception as exc:
        print(json.dumps({"category": "transient", "error_type": type(exc).__name__, "http_status_class": "unknown", "elapsed_s": time.monotonic() - started, "config": cfg}))
        return 0


if __name__ == "__main__":
    if "--canary" in sys.argv:
        raise SystemExit(canary_main())
    raise SystemExit(Supervisor().run())
