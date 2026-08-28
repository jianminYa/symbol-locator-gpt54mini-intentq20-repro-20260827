#!/usr/bin/env python3
"""AST/dynamic mock check for LOCAGENT_TEMPERATURE; never calls an API."""
from __future__ import annotations

import ast
import contextlib
import math
import os
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "source/SWE-Explore-Bench/third_party/LocAgent/auto_search_main.py"


def main() -> int:
    source = SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    configured_node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_configured_temperature")
    namespace = {"os": os, "math": math}
    exec(compile(ast.Module(body=[configured_node], type_ignores=[]), str(SOURCE), "exec"), namespace)
    configured = namespace["_configured_temperature"]

    old = os.environ.pop("LOCAGENT_TEMPERATURE", None)
    cases = []
    try:
        for label, env_value, expected in (("default", None, 1.0), ("explicit_zero", "0", 0.0), ("explicit_point_one", "0.1", 0.1)):
            if env_value is None:
                os.environ.pop("LOCAGENT_TEMPERATURE", None)
            else:
                os.environ["LOCAGENT_TEMPERATURE"] = env_value
            value = configured()
            mock_completion_kwargs = {"temperature": value}
            cases.append({"case": label, "configured_temperature": value,
                          "mock_completion_temperature": mock_completion_kwargs["temperature"],
                          "pass": value == expected and mock_completion_kwargs["temperature"] == expected})
        for label, env_value in (("invalid_nan", "nan"), ("invalid_inf", "inf"), ("invalid_not-a-number", "not-a-number")):
            os.environ["LOCAGENT_TEMPERATURE"] = env_value
            try:
                configured()
            except ValueError:
                cases.append({"case": label, "explicit_failure": "ValueError", "pass": True})
            else:
                cases.append({"case": label, "explicit_failure": None, "pass": False})
    finally:
        if old is None:
            os.environ.pop("LOCAGENT_TEMPERATURE", None)
        else:
            os.environ["LOCAGENT_TEMPERATURE"] = old

    impl = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "_auto_search_process_impl")
    completion_calls = [node for node in ast.walk(impl) if isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute) and node.func.attr == "completion"]
    completion_temperature = [next((kw.value.id for kw in call.keywords if kw.arg == "temperature"
                                    and isinstance(kw.value, ast.Name)), None) for call in completion_calls]
    if len(completion_calls) != 3 or completion_temperature != ["temp", "temp", "temp"]:
        raise AssertionError(f"completion temperature mapping mismatch: {completion_temperature}")

    child_call = next(node for node in ast.walk(tree) if isinstance(node, ast.Call)
                      and isinstance(node.func, ast.Name) and node.func.id == "_run_agent_child")
    mapping = next(arg for arg in child_call.args if isinstance(arg, ast.Dict))
    temp_values = [value for key, value in zip(mapping.keys, mapping.values)
                   if isinstance(key, ast.Constant) and key.value == "temp"]
    if len(temp_values) != 1 or not isinstance(temp_values[0], ast.Attribute) or temp_values[0].attr != "locagent_temperature":
        raise AssertionError("child kwargs do not map args.locagent_temperature")
    if not all(item["pass"] for item in cases):
        raise AssertionError(cases)
    print(__import__("json").dumps({"api_calls": 0, "cases": cases,
                                    "completion_calls": len(completion_calls),
                                    "completion_temperature": completion_temperature,
                                    "child_mapping": "args.locagent_temperature",
                                    "status": "PASS"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
