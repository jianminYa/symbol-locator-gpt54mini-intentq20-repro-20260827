# No-API parser and sidecar tests

Command:

```text
PYTHONPATH=NEW_ROOT/source/SWE-Explore-Bench python3 NEW_ROOT/diagnostics/no_api_path_tests.py
```

Result: `PASS`; `api_calls=0`.

The offline suite verifies ordinary relative paths, a real absolute path under
the current repository, `/testbed/`, `/workspace/`, a repository-basename
prefix, outside-repository absolute paths, `..` traversal, and entity/Fallback
A/A2/B parser fixtures. It also verifies that all four normalization calls in
`parse_locagent_jsonl()` receive `repo_path`, and that the sidecar call is
source-ordered before the deferred output-integrity raise.

The three exact leading-slash paths captured in the preserved
`django__django-10973` finish are rejected because those files do not exist in
that instance snapshot; they are not blindly truncated into valid-looking
regions. A real absolute path from the same repository is converted to a
repo-relative path. This is the required safe distinction between an absolute
path inside the current repo and an outside/root-relative model spelling.

The temperature patch remains the previously verified patch copied from the
old study. It was rerun before any new API canary with:

```text
python3 NEW_ROOT/diagnostics/temperature_mock_test.py
```

The result was `PASS`, `api_calls=0`: default `1.0`, explicit `0`, and
explicit `0.1` reached the mocked completion kwargs; non-finite and malformed
values raised `ValueError`; all three completion branches use `temp`; and the
child mapping is `args.locagent_temperature`.
