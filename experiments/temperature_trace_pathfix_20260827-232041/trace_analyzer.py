#!/usr/bin/env python3
"""Create redacted, structure-only summaries from LocAgent traces.

This intentionally never writes prompts, assistant text, tool arguments, tool
results, stdout, stderr, or response bodies.  It records counts, booleans,
timings, result shapes, and final region coordinates only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


METRICS = [
    "precision", "recall", "f1_score", "hit_file_rate", "noise_file_rate",
    "hit_region_rate", "noise_region_rate", "weighted_core_coverage",
    "context_efficiency", "optional_coverage", "ndcg_at_100", "ndcg_at_300",
    "ndcg_at_500", "recall_at_100", "recall_at_300", "recall_at_500",
    "first_useful_hit",
]
REQUIRED = [
    "instance.jsonl", "output/args.json", "output/loc_trajs.jsonl",
    "output/loc_outputs.jsonl", "output/localize.log",
]
SOURCE_EXTS = {
    "py", "go", "java", "js", "ts", "tsx", "jsx", "rs", "rb", "php",
    "c", "h", "cc", "cpp", "cxx", "hpp", "hh", "hxx", "scala", "kt",
    "swift", "cs", "lua", "dart", "ex", "exs", "erl", "clj", "m", "mm",
    "proto", "sh", "bash", "yml", "yaml", "sql",
}


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None


def jsonl_records(path: Path) -> tuple[list[Any], bool, int]:
    records: list[Any] = []
    valid = True
    lines = 0
    if not path.is_file():
        return records, False, lines
    try:
        with path.open(encoding="utf-8", errors="strict") as fh:
            for line in fh:
                if not line.strip():
                    continue
                lines += 1
                try:
                    records.append(json.loads(line))
                except (ValueError, TypeError):
                    valid = False
    except (OSError, UnicodeError):
        valid = False
    return records, valid, lines


def nested_count(value: Any) -> int:
    if isinstance(value, list):
        return sum(nested_count(v) for v in value)
    if isinstance(value, dict):
        return sum(nested_count(v) for v in value.values())
    return 1 if value not in (None, "") else 0


def nested_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        out: list[str] = []
        for v in value:
            out.extend(nested_strings(v))
        return out
    if isinstance(value, dict):
        out = []
        for v in value.values():
            out.extend(nested_strings(v))
        return out
    return []


def finite_number(value: Any) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(float(value)) else None


def call_names(message: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for call in message.get("tool_calls") or []:
        fn = call.get("function") if isinstance(call, dict) else None
        if isinstance(fn, dict) and isinstance(fn.get("name"), str):
            result.append(fn["name"])
        elif isinstance(call, dict) and isinstance(call.get("name"), str):
            result.append(call["name"])
    fc = message.get("function_call")
    if isinstance(fc, dict) and isinstance(fc.get("name"), str) and not result:
        result.append(fc["name"])
    return result


def content_nonempty(content: Any) -> bool:
    """Classify a tool result without retaining or emitting its content."""
    if content is None:
        return False
    if isinstance(content, (list, dict)):
        return bool(nested_count(content))
    if not isinstance(content, str):
        return True
    text = content.strip()
    if not text or text in {"[]", "{}", "null", "None"}:
        return False
    low = text.lower()
    if re.search(r"(?:no\s+(?:matching\s+)?(?:symbol|candidate|result)|not\s+found|empty\s+result)", low):
        return False
    try:
        parsed = json.loads(text)
    except ValueError:
        return True
    return bool(nested_count(parsed))


def tool_summary(messages: list[Any]) -> dict[str, Any]:
    ordered: list[str] = []
    results: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        calls = call_names(message)
        ordered.extend(calls)
        if message.get("role") == "tool" and isinstance(message.get("name"), str):
            content = message.get("content")
            results.append({
                "name": message["name"],
                "content_nonempty": content_nonempty(content),
                "content_bytes": len(content.encode("utf-8", "ignore")) if isinstance(content, str) else 0,
            })
    counts = Counter(ordered)
    finds = [r for r in results if r["name"] == "find_symbol"]
    return {
        "ordered_calls": ordered,
        "call_counts": dict(sorted(counts.items())),
        "tool_results": results,
        "first_find_symbol_nonempty": finds[0]["content_nonempty"] if finds else None,
        "later_find_symbol_nonempty": [r["content_nonempty"] for r in finds[1:]],
        "find_symbol_result_count": len(finds),
        "finish_calls": counts.get("finish", 0),
        "original_locagent_search_calls": sum(counts.get(n, 0) for n in (
            "search_code_snippets", "get_entity_contents", "explore_tree_structure"
        )),
    }


def direct_raw_line(line: str) -> bool:
    s = line.strip().strip("`").strip()
    if not s or s.startswith("#") or ":" not in s:
        return False
    fpath, ename = s.split(":", 1)
    fpath, ename = fpath.strip(), ename.strip()
    ext = fpath.rsplit(".", 1)[-1].lower() if "." in fpath else ""
    return bool(ext in SOURCE_EXTS and ename and not ename.isdigit() and not any(c.isspace() for c in ename))


def block_raw_line(line: str) -> bool:
    return bool(re.match(r"(?i)^(?:function|class|method):\s*\S", line.strip().strip("`").strip()))


def fallback_type(output: dict[str, Any]) -> str:
    entities = nested_count(output.get("found_entities", []))
    files = nested_count(output.get("found_files", []))
    raw = nested_strings(output.get("raw_output_loc", []))
    direct = any(direct_raw_line(line) for value in raw for line in value.splitlines())
    block = any(block_raw_line(line) for value in raw for line in value.splitlines())
    if entities:
        return "native_entities"
    if direct:
        return "A"
    if block:
        return "A2"
    if files:
        return "B"
    return "none"


def region_summary(regions: Any) -> tuple[list[dict[str, Any]], str]:
    out: list[dict[str, Any]] = []
    if not isinstance(regions, list):
        return out, hashlib.sha256(b"").hexdigest()[:16]
    for region in regions:
        if not isinstance(region, dict):
            out.append({"shape": type(region).__name__})
            continue
        path = region.get("path")
        start = finite_number(region.get("start"))
        end = finite_number(region.get("end"))
        item: dict[str, Any] = {"start": start, "end": end}
        if isinstance(path, str):
            item["path"] = path
        else:
            item["path_type"] = type(path).__name__
        out.append(item)
    canonical = json.dumps(out, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return out, hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def output_record(payload: Path, iid: str) -> tuple[dict[str, Any] | None, bool]:
    records, valid, _ = jsonl_records(payload)
    for record in records:
        if isinstance(record, dict) and record.get("instance_id") == iid:
            return record, valid
    return None, valid


def trace_case(trace_dir: Path, iid: str) -> dict[str, Any]:
    payload = trace_dir / iid
    integrity = read_json(trace_dir / "trace_integrity.json") or {}
    files: list[dict[str, Any]] = []
    jsonl_valid = True
    jsonl_records_count = 0
    jsonl_id_mismatches = 0
    if payload.is_dir():
        for path in sorted(payload.rglob("*")):
            if not path.is_file():
                continue
            rel = str(path.relative_to(payload))
            files.append({"path": rel, "bytes": path.stat().st_size})
            if path.suffix == ".jsonl":
                records, valid, count = jsonl_records(path)
                jsonl_valid &= valid
                jsonl_records_count += count
                for record in records:
                    if isinstance(record, dict) and record.get("instance_id") not in (None, iid):
                        jsonl_id_mismatches += 1
    required_present = all((payload / rel).is_file() for rel in REQUIRED) if payload.is_dir() else False
    return {
        "trace_dir": str(trace_dir),
        "trace_exists": trace_dir.is_dir(),
        "payload_exists": payload.is_dir(),
        "files": files,
        "file_count": len(files),
        "total_bytes": sum(int(f["bytes"]) for f in files),
        "required_files_present": required_present,
        "jsonl_valid": jsonl_valid,
        "jsonl_records": jsonl_records_count,
        "jsonl_id_mismatches": jsonl_id_mismatches,
        "trajectory_present": (payload / "output/loc_trajs.jsonl").is_file() and bool(jsonl_records(payload / "output/loc_trajs.jsonl")[0]),
        "scorer_sidecar": (payload / "output/scorer_usage.json").is_file(),
        "integrity": {
            "trace_complete": integrity.get("trace_complete"),
            "structured_result": integrity.get("structured_result", False),
            "structured_failure": integrity.get("structured_failure", False),
            "instance_id": integrity.get("instance_id"),
        },
    }


def all_trace_dirs(run_root: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    base = run_root / "faces"
    if not base.is_dir():
        return result
    for path in sorted(base.glob("*/traces/*_attempt_*")):
        if path.is_dir():
            prefix = path.name.rsplit("_attempt_", 1)[0]
            result.setdefault(prefix, path)
    return result


def case_summary(run_root: Path, iid: str, trace_dir: Path | None) -> dict[str, Any]:
    face = "unknown"
    if trace_dir is not None:
        face = trace_dir.relative_to(run_root / "faces").parts[0]
    trace = trace_case(trace_dir, iid) if trace_dir else {
        "trace_exists": False, "payload_exists": False, "files": [], "file_count": 0,
        "total_bytes": 0, "required_files_present": False, "jsonl_valid": False,
        "jsonl_records": 0, "jsonl_id_mismatches": 0, "trajectory_present": False,
        "scorer_sidecar": False, "integrity": {"trace_complete": False},
    }
    payload = trace_dir / iid if trace_dir else Path("/nonexistent")
    traj_records, traj_valid, _ = jsonl_records(payload / "output/loc_trajs.jsonl")
    output, output_valid = output_record(payload / "output/loc_outputs.jsonl", iid)
    final_output = None
    for candidate in (
        run_root / "faces" / face / "output" / "locagent_top5.jsonl",
        payload / "output/merged_loc_outputs_mrr.jsonl",
    ):
        if candidate.is_file():
            records, _, _ = jsonl_records(candidate)
            for record in records:
                if isinstance(record, dict) and record.get("instance_id") == iid:
                    final_output = record
                    break
            if final_output is not None:
                break
    if final_output is None:
        final_output = {}
    traj = traj_records[0] if traj_records and isinstance(traj_records[0], dict) else {}
    traj_list = (traj.get("loc_trajs") or {}).get("trajs", []) if isinstance(traj.get("loc_trajs"), dict) else []
    first_traj = traj_list[0] if traj_list and isinstance(traj_list[0], dict) else {}
    messages = first_traj.get("messages", []) if isinstance(first_traj, dict) else []
    tools = tool_summary(messages if isinstance(messages, list) else [])
    agent_usage = first_traj.get("usage") if isinstance(first_traj.get("usage"), dict) else {}
    if not agent_usage and isinstance(traj.get("usage"), dict):
        agent_usage = traj["usage"]
    usage = {k: int(v) for k, v in agent_usage.items() if k in {"prompt_tokens", "completion_tokens"} and isinstance(v, (int, float))}
    result = output or {}
    raw = nested_strings(result.get("raw_output_loc", []))
    raw_text = "\n".join(raw)
    regions, region_digest = region_summary(final_output.get("regions", []))
    metrics = final_output.get("metrics", {}) if isinstance(final_output.get("metrics"), dict) else {}
    scorer = read_json(payload / "output/scorer_usage.json") or {}
    scorer_counts = {
        "calls": int(scorer.get("scorer_calls", scorer.get("calls", metrics.get("scorer_calls", 0))) or 0),
        "prompt_tokens": int(scorer.get("prompt_tokens", metrics.get("scorer_prompt_tokens", 0)) or 0),
        "completion_tokens": int(scorer.get("completion_tokens", metrics.get("scorer_completion_tokens", 0)) or 0),
    }
    trace_time = finite_number(first_traj.get("time"))
    if trace_time is None:
        trace_time = finite_number(traj.get("time"))
    return {
        "face": face,
        "instance_id": iid,
        "trace_complete": bool(trace.get("integrity", {}).get("trace_complete")) and bool(trace.get("jsonl_valid")) and bool(trace.get("required_files_present")) and bool(trace.get("trajectory_present") or trace.get("integrity", {}).get("structured_result")),
        "trace": trace,
        "output_valid": output_valid,
        "final_output_present": bool(final_output),
        "trajectory_jsonl_valid": traj_valid,
        "trajectory_seconds": trace_time,
        "tool_path": tools,
        "agent_usage": usage,
        "scorer": scorer_counts,
        "found_entities_count": nested_count(result.get("found_entities", [])),
        "found_files_count": nested_count(result.get("found_files", [])),
        "raw_output_loc_count": len(raw),
        "raw_output_loc_bytes": len(raw_text.encode("utf-8", "ignore")),
        "whole_file_1_minus_1_count": len(re.findall(r"(?:^|[^\d])1\s*:\s*-1(?:$|[^\d])", raw_text, flags=re.M)),
        "raw_duplicate_line_count": sum(max(0, len(lines) - len(set(lines))) for lines in (v.splitlines() for v in raw)),
        "raw_malformed_line_count": sum(1 for v in raw for line in v.splitlines() if line.strip() and ":" not in line and not line.strip().startswith("#")),
        "parser_fallback": fallback_type(result),
        "final_region_count": len(regions),
        "final_region_digest": region_digest,
        "final_regions": regions,
        "metrics": {k: finite_number(metrics.get(k)) for k in METRICS},
        "response_model": traj.get("model") or first_traj.get("model") or None,
        "response_usage": {k: int(v) for k, v in (traj.get("usage") or {}).items() if k in {"prompt_tokens", "completion_tokens"} and isinstance(v, (int, float))},
    }


def ids_for(run_root: Path) -> list[str]:
    p = run_root / "data/instance_ids.txt"
    if p.is_file():
        return [x.strip() for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    return []


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_root", type=Path)
    args = ap.parse_args()
    run_root = args.run_root.resolve()
    diag = run_root / "diagnostics"
    diag.mkdir(parents=True, exist_ok=True)
    dirs = all_trace_dirs(run_root)
    ids = ids_for(run_root)
    for iid in sorted(dirs):
        if iid not in ids:
            ids.append(iid)
    rows: list[dict[str, Any]] = []
    for iid in ids:
        rows.append(case_summary(run_root, iid, dirs.get(iid)))
    rows.sort(key=lambda x: (x.get("face", ""), x["instance_id"]))
    write_jsonl(diag / "per_case_trace_summary.jsonl", rows)

    inv_lines = ["face\tinstance_id\ttrace_dir\ttrace_complete\tfile_count\ttotal_bytes\trequired_files\tjsonl_valid\ttrajectory_present\tscorer_sidecar\tinstance_id_match"]
    for row in rows:
        trace = row["trace"]
        inv_lines.append("\t".join(map(str, [
            row["face"], row["instance_id"], trace.get("trace_dir", ""), row["trace_complete"],
            trace.get("file_count", 0), trace.get("total_bytes", 0), trace.get("required_files_present", False),
            trace.get("jsonl_valid", False), trace.get("trajectory_present", False), trace.get("scorer_sidecar", False),
            trace.get("integrity", {}).get("instance_id") == row["instance_id"],
        ])))
    (diag / "trace_inventory.tsv").write_text("\n".join(inv_lines) + "\n", encoding="utf-8")

    paths = Counter()
    for row in rows:
        for name in row["tool_path"]["ordered_calls"]:
            paths[name] += 1
    path_lines = ["# Tool path summary", "", f"- cases summarized: `{len(rows)}`", "", "| tool | calls |", "|---|---:|"]
    path_lines.extend(f"| {name} | {count} |" for name, count in sorted(paths.items()))
    path_lines += ["", "## Per-case ordered calls", ""]
    for row in rows:
        path_lines.append(f"- `{row['face']}/{row['instance_id']}`: `{' -> '.join(row['tool_path']['ordered_calls'])}`")
    (diag / "tool_path_summary.md").write_text("\n".join(path_lines) + "\n", encoding="utf-8")

    fallbacks = Counter(row["parser_fallback"] for row in rows)
    fallback_lines = ["# Parser fallback summary", "", "Fallback labels follow `explorers/parsing.py`: A direct `path:QualifiedName`, A2 multiline block, B found-files, and native_entities for the preferred entity path.", "", "| fallback | cases |", "|---|---:|"]
    fallback_lines.extend(f"| {name} | {count} |" for name, count in sorted(fallbacks.items()))
    fallback_lines += ["", "| face | instance_id | fallback | regions | whole-file 1:-1 |", "|---|---|---|---:|---:|"]
    fallback_lines.extend(f"| {r['face']} | {r['instance_id']} | {r['parser_fallback']} | {r['final_region_count']} | {r['whole_file_1_minus_1_count']} |" for r in rows)
    (diag / "parser_fallback_summary.md").write_text("\n".join(fallback_lines) + "\n", encoding="utf-8")

    empty = [r for r in rows if r["final_output_present"] and r["final_region_count"] == 0]
    empty_lines = ["# Empty-result diagnosis", "", f"- semantic empty results: `{len(empty)}/{len(rows)}`", "- Results with valid non-empty or empty regions are retained as observed; no semantic result was resampled.", "", "| face | instance_id | layer assessment | tool calls | fallback | trace complete |", "|---|---|---|---:|---|---|"]
    for row in empty:
        calls = row["tool_path"]["ordered_calls"]
        if not row["trace_complete"]:
            layer = "trace infrastructure"
        elif row["tool_path"]["finish_calls"] == 0:
            layer = "Agent finish absent"
        elif row["raw_output_loc_count"] == 0 and row["found_entities_count"] == 0 and row["found_files_count"] == 0:
            layer = "LocAgent/parser input empty"
        elif row["found_entities_count"] == 0 and row["found_files_count"] == 0 and row["raw_output_loc_count"] > 0:
            layer = "parser fallback produced no regions"
        else:
            layer = "scorer/output mapping"
        empty_lines.append(f"| {row['face']} | {row['instance_id']} | {layer} | {len(calls)} | {row['parser_fallback']} | {row['trace_complete']} |")
    if not empty:
        empty_lines.append("\nNo semantic empty result occurred in this analyzed run.")
    (diag / "empty_result_diagnosis.md").write_text("\n".join(empty_lines) + "\n", encoding="utf-8")
    print(json.dumps({"run_root": str(run_root), "cases": len(rows), "trace_complete": sum(bool(r["trace_complete"]) for r in rows), "empty_results": len(empty)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
