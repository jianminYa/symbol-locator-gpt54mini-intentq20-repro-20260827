# Offline reparse of preserved T0 A1 16 traces

- API calls: `0`.
- trace sets parsed: `16/16`.
- final paths relative and syntactically inside their repo: `16/16`.
- `/repos/repos/` in new paths: `0`.
- persisted-output vs new-parser region changes: `2/16` (includes one pre-existing old-output/replay discrepancy).
- old-parser replay vs new-parser normalization changes: `1/16`; unrelated normalization changes: `0`.

## django__django-10973 before/after

- before regions: `[('django/db/backends/postgresql/client.py', 1, -1), ('/django/db/backends/base/client.py', 1, -1), ('/django/db/backends/postgresql/base.py', 1, -1), ('/django/db/backends/postgresql_psycopg2/base.py', 1, -1), ('/django/db/backends/postgresql/client.py', 1, -1)]`
- old-parser replay regions: `[('django/db/backends/postgresql/client.py', 1, -1), ('/django/db/backends/base/client.py', 1, -1), ('/django/db/backends/postgresql/base.py', 1, -1), ('/django/db/backends/postgresql_psycopg2/base.py', 1, -1), ('/django/db/backends/postgresql/client.py', 1, -1)]`
- after regions: `[('django/db/backends/postgresql/client.py', 1, -1)]`
- absolute paths before/after: `4/0`
- The three exact root-relative `/django/...` model spellings were rejected as outside this snapshot; the two ordinary relative spellings remain. This removes absolute output pollution without inventing repository membership.

| metric | preserved old row | recomputed old regions | recomputed new regions | new-old recomputed |
|---|---:|---:|---:|---:|
| precision | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| recall | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| f1_score | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| hit_file_rate | 0.500000 | 0.500000 | 0.500000 | 0.000000 |
| noise_file_rate | 0.800000 | 0.800000 | 0.000000 | -0.800000 |
| hit_region_rate | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| noise_region_rate | 1.000000 | 1.000000 | 1.000000 | 0.000000 |
| weighted_core_coverage | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| context_efficiency | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| ndcg_at_100 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| ndcg_at_300 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| ndcg_at_500 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| recall_at_100 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| recall_at_300 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| recall_at_500 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| first_useful_hit | 0.000000 | 0.000000 | 0.000000 | 0.000000 |

## Pre-existing artifact discrepancy

`sphinx-doc__sphinx-8621` has five regions in the preserved output but six when its preserved trace is replayed with the old parser. The old and new parser replays are identical for that case, so it is not caused by this path-normalization patch.

## Gate

`PASS`: all 16 traces parsed; all new paths are relative and inside-check safe; the only parser-replay change is the known absolute-path case.
