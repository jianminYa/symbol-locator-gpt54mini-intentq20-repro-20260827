# B1 first-case validation defect analysis

- observed stop: `B1_FIRST_CASE_PYRIGHT_OR_FIND_SYMBOL_VALIDATION_FAILED`
- this is an orchestration-validator defect, not a source or benchmark mutation.
- the successful B1 first-case attempt record has `return_code=0`, `output_written=true`, `pyright_warmup_success=true`, and `find_symbol_nonempty=true`.
- the same record has `pyright_seen=false` and `pyright_warmup_failure=true`. The preserved trace contains warmup signatures whose numeric status is `failed=0`; the old parser treated the word `failed` in that success signature as a failure and did not infer the Pyright signal from the successful warmup marker.
- during the live case, a read-only process scan observed the child-owned Pyright chain: evaluator -> LocAgent shim -> `pyright-langserver` and its node child, all belonging to the active B1 case tree. No process was modified by this analysis.
- the preserved trace also has non-empty `find_symbol` evidence. No prompt, response body, header, or credential was copied into this record.

## Controlled action

The supervisor is being corrected only inside this RUN_ROOT, after the original supervisor and all case processes exited. The correction will:

1. treat a `failed=0` warmup signature as success and not as failure;
2. validate the existing successful B1 first case from its preserved trace and the recorded child-process evidence without rerunning it;
3. preserve existing timeline, canary, output, attempt, and report files during the controlled resume;
4. run only missing B1 IDs, then continue in the fixed A2 -> B2 order.

The repair source, original handoff, benchmark, data, prompt, parser, scorer, and historical results are outside the edit scope.
