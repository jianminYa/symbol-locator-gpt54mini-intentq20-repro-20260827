# Absolute-path root-cause analysis

This is a read-only analysis of the preserved stopped study:
`temperature_trace_study_20260827-152310`. No old artifact was rewritten.

## Confirmed facts

- The affected case is `django__django-10973` in the old T0 A1 attempt. Its trace payload contains one trajectory and valid JSONL payload files. The model finish locations, in order, were:
  `django/db/backends/postgresql/client.py:DatabaseClient`,
  `/django/db/backends/base/client.py:BaseDatabaseClient`,
  `/django/db/backends/postgresql/base.py:DatabaseWrapper`,
  `/django/db/backends/postgresql_psycopg2/base.py:DatabaseWrapper`, and
  `django/db/backends/postgresql/client.py:DatabaseClient.runshell`.
- The two leading-slash locations first appear in the model finish locations. They are preserved unchanged in `raw_output_loc` and the merged LocAgent output. The corresponding `found_entities` and `found_files` fields are empty in this record, so neither field produced these five locations.
- The old final parser output contains five regions. The first and fifth paths are relative; the middle three are absolute paths, each with range `1:-1`. The resulting old row has zero precision/recall/F1 and `hit_file_rate=0.5`.
- The old `parsing.py` already declared `_normalize_path(path, repo_path="")`, but the entity, Fallback A, Fallback A2, and Fallback B calls inside `parse_locagent_jsonl()` called `_normalize_path()` without `repo_path` (old lines 253, 294, 343, and 365).
- The old absolute-prefix expression only covered `/opt`, `/home`, `/root`, `/tmp`, `/workspace`, and `/testbed`; it did not cover `/data/.../repos/<instance>/...`.
- The actual benchmark repository is the per-instance directory under the discovered `SWE-Explore-Bench/repos/` root. The captured `/django/...` strings are not filesystem-root paths to that repository, but their leading slash can be safely interpreted as a repository-root-relative spelling only after checking the candidate inside the known repository.

## Layered trace evidence

| layer | observed result | interpretation |
|---|---|---|
| model finish locations | five ordered locations, including three `/django/...` paths | confirmed first appearance |
| `raw_output_loc` | same five lines, no path rewrite | transport/LocAgent merge preserved model output |
| merged LocAgent output | same `raw_output_loc`; empty `found_entities`/`found_files` | no structured entity/file path caused the bad paths |
| parser regions | three absolute paths and two relative paths | old normalization returned unknown absolute paths unchanged |
| evaluator validation | hard stop on an absolute region path | correct safety behavior; continuing would contaminate metrics |

## Root cause assessment

The confirmed code defect is parser context loss: `parse_locagent_jsonl()` did not pass the real `repo_path` to its LocAgent path-normalization calls. Consequently the parser could not map an absolute path to the current repository and its fallback behavior returned an unknown absolute path unchanged. The missing `/data/.../repos/...` prefix rule is a second confirmed compatibility gap.

It is a strong inference that the observed absolute-path pollution is fully repaired by repository-aware normalization: a candidate is accepted only when it resolves inside the current repository, while an external absolute path is rejected. The model/provider is still allowed to emit either relative or absolute spellings; the parser, not the supervisor, is the correct common repair point.

Unknown: this trace proves the exact captured `/django/...` spellings, but does not establish whether another provider response used a full `/data/.../repos/<instance>/...` spelling. The new offline tests therefore cover both the exact captured locations and the `/data/...` form without assuming which one will recur.

## Safety requirement

The fix must not simply strip every leading slash. It must normalize through the resolved current repository, reject `..` traversal and outside-repository absolute paths, and leave the hard output-integrity gate active.
