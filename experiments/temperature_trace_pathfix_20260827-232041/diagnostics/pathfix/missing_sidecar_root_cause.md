# Missing trace-integrity sidecar root-cause analysis

This analysis reads only the preserved stopped T0 A1 artifacts.

## Confirmed facts

- The only missing sidecar is for `django__django-10973` attempt 1 under the old A1 trace tree.
- Its trace payload is present and structurally complete: `instance.jsonl`, `output/args.json`, `output/loc_trajs.jsonl`, `output/loc_outputs.jsonl`, `output/localize.log`, and merged LocAgent output are present; the JSONL files parse; the instance ID is correct; and the trajectory is present.
- The old supervisor calls `scan_case_output()`/`validate_output()` before `trace_integrity()` in `run_case()` (old lines 1085–1119).
- `validate_output()` raises `PermanentStop("OUTPUT_INTEGRITY_A1")` as soon as it sees an absolute region path (old lines 690–699). Therefore the exception bypassed the later sidecar call. This is consistent with a complete payload and no `trace_integrity.json`.

## Assessment

The missing sidecar was caused by control-flow ordering, not by missing trace payload data. The hard output stop was appropriate, but the supervisor failed to record the trace integrity result before propagating that stop. The new supervisor defers propagation of the output-integrity exception until after trace inventory, attempt metadata, and the atomic integrity sidecar have been written.

The sidecar will not be marked complete merely because it exists: its `trace_complete` value continues to depend on valid JSONL, correct instance IDs, the required payload/trajectory for a success, or an explicit structured failure for a failed case.

## Unknowns

The old sidecar was never written, so there is no historical sidecar timestamp or serialized pre-stop category to compare. The payload itself is enough to establish the structural facts above; the exact instant at which the process exited relative to the supervisor's finally path is not independently observable.
