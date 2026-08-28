#!/usr/bin/env python3
"""Offline parser and supervisor-order regression tests; never calls an API."""
from __future__ import annotations

import ast
import json
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1] / "source" / "SWE-Explore-Bench"
REPO = PROJECT / "repos" / "django__django-10973"
INSTANCE = "django__django-10973"
PARSER_FILE = PROJECT / "explorers" / "parsing.py"
SUPERVISOR_FILE = Path(__file__).resolve().parents[1] / "night_supervisor.py"
sys.path.insert(0, str(PROJECT))
from explorers.parsing import _normalize_path, parse_locagent_jsonl  # noqa: E402


def assert_equal(actual, expected, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: {actual!r} != {expected!r}")


def assert_relative_inside(path: str, label: str) -> None:
    if not path or Path(path).is_absolute() or path.startswith("../"):
        raise AssertionError(f"{label}: unsafe normalized path {path!r}")
    (REPO / path).resolve().relative_to(REPO.resolve())


def parse_fixture(record: dict) -> list[str]:
    with tempfile.TemporaryDirectory(prefix="pathfix-no-api-") as td:
        fixture = Path(td) / "merged.jsonl"
        fixture.write_text(json.dumps(record) + "\n", encoding="utf-8")
        parsed = parse_locagent_jsonl(str(fixture), INSTANCE, str(REPO))
    return [region.path for region in parsed[0].regions] if parsed else []


def main() -> int:
    rel = "django/db/backends/postgresql/client.py"
    assert_equal(_normalize_path(rel, str(REPO)), rel, "ordinary relative path")
    assert_equal(_normalize_path(str(REPO / rel), str(REPO)), rel, "real repo absolute path")
    assert_equal(_normalize_path("/testbed/" + rel, str(REPO)), rel, "/testbed compatibility")
    assert_equal(_normalize_path("/workspace/" + REPO.name + "/" + rel, str(REPO)), rel, "/workspace compatibility")
    assert_equal(_normalize_path(REPO.name + "/" + rel, str(REPO)), rel, "repo basename prefix")
    assert_equal(_normalize_path("/tmp/not-this-repo/outside.py", str(REPO)), "", "outside absolute rejection")
    assert_equal(_normalize_path("/data/other/repos/other-instance/" + rel, str(REPO)), "", "other repo rejection")
    assert_equal(_normalize_path("../outside.py", str(REPO)), "", "dot-dot rejection")
    assert_equal(_normalize_path(str(REPO / ".." / "outside.py"), str(REPO)), "", "absolute dot-dot rejection")

    captured = [
        "/django/db/backends/base/client.py",
        "/django/db/backends/postgresql/base.py",
        "/django/db/backends/postgresql_psycopg2/base.py",
    ]
    # These exact finish spellings are root-relative model output, not
    # filesystem paths in this particular per-instance snapshot.  They must
    # be rejected safely rather than blindly truncated into valid-looking
    # regions.
    for actual in captured:
        assert_equal(_normalize_path(actual, str(REPO)), "", f"captured external path rejection {actual}")

    expected = ["django/contrib/auth/backends.py"]
    exact_repo_absolute = str(REPO / expected[0])
    assert_equal(_normalize_path(exact_repo_absolute, str(REPO)), expected[0], "exact repo absolute fixture")
    assert_relative_inside(_normalize_path(exact_repo_absolute, str(REPO)), "exact repo absolute fixture")

    entity_paths = parse_fixture({
        "instance_id": INSTANCE,
        "found_entities": [[exact_repo_absolute + ":ModelBackend"]],
        "found_files": [],
        "raw_output_loc": [],
    })
    assert_equal(entity_paths, [expected[0]], "entity parser")

    fallback_a = parse_fixture({
        "instance_id": INSTANCE,
        "found_entities": [[]],
        "found_files": [[]],
        "raw_output_loc": [exact_repo_absolute + ":ModelBackend"],
    })
    assert_equal(fallback_a, expected, "Fallback A parser")

    fallback_a2 = parse_fixture({
        "instance_id": INSTANCE,
        "found_entities": [[]],
        "found_files": [[]],
        "raw_output_loc": [exact_repo_absolute + "\nclass: ModelBackend"],
    })
    assert_equal(fallback_a2, [expected[0]], "Fallback A2 parser")

    fallback_b = parse_fixture({
        "instance_id": INSTANCE,
        "found_entities": [],
        "found_files": [[exact_repo_absolute, rel]],
        "raw_output_loc": [],
    })
    assert_equal(fallback_b, [expected[0], rel], "Fallback B parser")

    source = PARSER_FILE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    parse_fn = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "parse_locagent_jsonl")
    normalize_calls = [node for node in ast.walk(parse_fn) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_normalize_path"]
    if len(normalize_calls) != 4 or any(len(call.args) < 2 for call in normalize_calls):
        raise AssertionError("parse_locagent_jsonl has a normalization call without repo_path")

    sup_source = SUPERVISOR_FILE.read_text(encoding="utf-8")
    trace_pos = sup_source.index("trace = self.trace_integrity(")
    raise_pos = sup_source.index("raise PermanentStop(output_error)")
    if trace_pos >= raise_pos:
        raise AssertionError("sidecar/trace integrity is not before deferred output stop")

    print(json.dumps({
        "status": "PASS",
        "api_calls": 0,
        "normalize_calls_in_parse_locagent_jsonl": len(normalize_calls),
        "captured_paths": len(captured),
        "fixtures": ["entity", "fallback_a", "fallback_a2", "fallback_b"],
        "sidecar_order_check": "PASS",
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
