#!/usr/bin/env python3
"""Reparse the preserved old A1 traces with the new parser; no API calls."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OLD = Path("/data/workspace/symbol-locator-repair-20260826-083933/experiments/temperature_trace_study_20260827-152310")
PROJECT = ROOT / "source/SWE-Explore-Bench"
OLD_RUN = OLD / "t0_x2/attempt_2"
sys.path.insert(0, str(PROJECT))
from eval import ExploreEvaluator  # noqa: E402
from explorers.parsing import parse_locagent_jsonl  # noqa: E402

METRICS = [
    "precision", "recall", "f1_score", "hit_file_rate", "noise_file_rate",
    "hit_region_rate", "noise_region_rate", "weighted_core_coverage",
    "context_efficiency", "ndcg_at_100", "ndcg_at_300", "ndcg_at_500",
    "recall_at_100", "recall_at_300", "recall_at_500", "first_useful_hit",
]


def old_rows() -> dict[str, dict]:
    path = OLD_RUN / "faces/A1/output/locagent_top5.jsonl"
    return {row["instance_id"]: row for row in (json.loads(line) for line in path.read_text().splitlines() if line.strip())}


def metrics_for(evaluator: ExploreEvaluator, iid: str, regions: list[tuple[str, int, int]]) -> dict[str, float]:
    evaluator._current_instance_id = iid
    evaluator._current_file_line_counts = evaluator.file_line_counts.get(iid, {})
    gt = evaluator.bench_data_dict[iid]["ground_truth"]
    return {metric: float(getattr(evaluator, f"evaluate_{metric}")(regions, gt)) for metric in METRICS}


def replay_with_old_parser(merged: Path, iid: str, repo: Path) -> list[tuple[str, int, int]]:
    code = (
        "import json,sys; from explorers.parsing import parse_locagent_jsonl; "
        "p=parse_locagent_jsonl(sys.argv[1],sys.argv[2],sys.argv[3]); "
        "print(json.dumps([(r.path,int(r.start),int(r.end)) for x in p for r in x.regions]))"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(OLD / "source/SWE-Explore-Bench")
    completed = subprocess.run([sys.executable, "-c", code, str(merged), iid, str(repo)],
                               check=True, capture_output=True, text=True, env=env)
    return [tuple(item) for item in json.loads(completed.stdout)]


def main() -> int:
    data = [json.loads(line) for line in (ROOT / "data/intentq20.jsonl").read_text().splitlines() if line.strip()]
    line_counts: dict[str, dict[str, int]] = {}
    for rec in data:
        iid = rec["instance_id"]
        repo = PROJECT / "repos" / iid
        paths = set()
        gt = rec.get("ground_truth") or {}
        for region in gt.get("read_core_regions") or []:
            paths.add(region.get("path"))
        for group in (gt.get("read_optional_regions_map") or {}).values():
            paths.update(region.get("path") for region in group or [])
        for path in paths:
            if isinstance(path, str) and (repo / path).is_file():
                line_counts.setdefault(iid, {})[path] = len((repo / path).read_text(errors="replace").splitlines())
    evaluator = ExploreEvaluator(ROOT / "data/intentq20.jsonl", line_counts)
    before = old_rows()
    trace_dirs = sorted((OLD_RUN / "faces/A1/traces").glob("*_attempt_1"))
    if len(trace_dirs) != 16 or set(before) != {p.name.removesuffix("_attempt_1") for p in trace_dirs}:
        raise RuntimeError("expected exactly the preserved 16 A1 trace directories and rows")

    rows = []
    for trace_dir in trace_dirs:
        iid = trace_dir.name.removesuffix("_attempt_1")
        merged = trace_dir / iid / "output/merged_loc_outputs_mrr.jsonl"
        parsed = parse_locagent_jsonl(str(merged), iid, str(PROJECT / "repos" / iid))
        new_regions = [(r.path, int(r.start), int(r.end)) for result in parsed for r in result.regions]
        old_parser_regions = replay_with_old_parser(merged, iid, OLD / "source/SWE-Explore-Bench/repos" / iid)
        old_regions = [(str(r["path"]), int(r["start"]), int(r["end"])) for r in before[iid].get("regions", [])]
        new_paths = [path for path, _start, _end in new_regions]
        repo = (PROJECT / "repos" / iid).resolve()
        relative = all(path and not Path(path).is_absolute() and not path.startswith("../") for path in new_paths)
        inside = all((repo / path).resolve().relative_to(repo) is not None for path in new_paths) if new_paths else True
        rows.append({
            "instance_id": iid,
            "trace_dir": str(trace_dir),
            "trace_parse_status": "parsed" if parsed else "empty_parser_result",
            "old_regions": old_regions,
            "old_parser_replay_regions": old_parser_regions,
            "new_regions": new_regions,
            "old_absolute_count": sum(Path(path).is_absolute() for path, _s, _e in old_regions),
            "new_absolute_count": sum(Path(path).is_absolute() for path, _s, _e in new_regions),
            "new_relative_paths": relative,
            "new_paths_inside_repo": inside,
            "normalization_changed": old_parser_regions != new_regions,
            "persisted_output_differs_from_old_parser_replay": old_regions != old_parser_regions,
            "old_metrics": before[iid].get("metrics", {}),
            "recomputed_old_metrics": metrics_for(evaluator, iid, old_regions),
            "recomputed_new_metrics": metrics_for(evaluator, iid, new_regions),
        })

    output = ROOT / "diagnostics/offline_reparse_16_comparison.jsonl"
    output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    changed = [row for row in rows if row["old_regions"] != row["new_regions"]]
    normalization_changed = [row for row in rows if row["normalization_changed"]]
    unrelated = [row for row in normalization_changed if row["instance_id"] != "django__django-10973"]
    all_relative = all(row["new_relative_paths"] and row["new_paths_inside_repo"] for row in rows)
    if len(rows) != 16 or not all_relative or unrelated:
        raise RuntimeError(f"offline gate failed: rows={len(rows)} all_relative={all_relative} unrelated_changes={len(unrelated)}")

    target = next(row for row in rows if row["instance_id"] == "django__django-10973")
    md = [
        "# Offline reparse of preserved T0 A1 16 traces", "",
        "- API calls: `0`.",
        f"- trace sets parsed: `{len(rows)}/16`.",
        f"- final paths relative and syntactically inside their repo: `{sum(row['new_relative_paths'] and row['new_paths_inside_repo'] for row in rows)}/16`.",
        f"- `/repos/repos/` in new paths: `{sum('/repos/repos/' in path for row in rows for path, _s, _e in row['new_regions'])}`.",
        f"- persisted-output vs new-parser region changes: `{len(changed)}/16` (includes one pre-existing old-output/replay discrepancy).",
        f"- old-parser replay vs new-parser normalization changes: `{len(normalization_changed)}/16`; unrelated normalization changes: `{len(unrelated)}`.", "",
        "## django__django-10973 before/after", "",
        f"- before regions: `{target['old_regions']}`",
        f"- old-parser replay regions: `{target['old_parser_replay_regions']}`",
        f"- after regions: `{target['new_regions']}`",
        f"- absolute paths before/after: `{target['old_absolute_count']}/{target['new_absolute_count']}`",
        "- The three exact root-relative `/django/...` model spellings were rejected as outside this snapshot; the two ordinary relative spellings remain. This removes absolute output pollution without inventing repository membership.", "",
        "| metric | preserved old row | recomputed old regions | recomputed new regions | new-old recomputed |",
        "|---|---:|---:|---:|---:|",
    ]
    for metric in METRICS:
        old_value = float(target["old_metrics"].get(metric, 0.0))
        recomputed_old = target["recomputed_old_metrics"][metric]
        new_value = target["recomputed_new_metrics"][metric]
        md.append(f"| {metric} | {old_value:.6f} | {recomputed_old:.6f} | {new_value:.6f} | {new_value-recomputed_old:.6f} |")
    discrepancy = next((row for row in rows if row["persisted_output_differs_from_old_parser_replay"] and row["instance_id"] != "django__django-10973"), None)
    if discrepancy:
        md += ["", "## Pre-existing artifact discrepancy", "", f"`{discrepancy['instance_id']}` has five regions in the preserved output but six when its preserved trace is replayed with the old parser. The old and new parser replays are identical for that case, so it is not caused by this path-normalization patch."]
    md += ["", "## Gate", "", "`PASS`: all 16 traces parsed; all new paths are relative and inside-check safe; the only parser-replay change is the known absolute-path case."]
    (ROOT / "diagnostics/offline_reparse_16_comparison.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS", "api_calls": 0, "traces": len(rows), "persisted_output_changes": len(changed), "normalization_changes": len(normalization_changed), "unrelated_normalization_changes": len(unrelated), "absolute_before": sum(row["old_absolute_count"] for row in rows), "absolute_after": sum(row["new_absolute_count"] for row in rows)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
