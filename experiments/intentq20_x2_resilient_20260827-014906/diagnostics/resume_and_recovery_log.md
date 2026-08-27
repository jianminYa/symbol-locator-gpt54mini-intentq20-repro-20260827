# Resume and recovery log

Each case attempt is recorded under `attempts/<face>/<instance_id>/`; no successful JSONL row is deleted or rewritten.

## Controlled orchestration recovery

- reason: false-positive B1 first-case validator stop; see `b1_validation_defect_analysis.md`
- old supervisor PID/PGID: `201800/201800` (exited before recovery)
- old tmux session: `symloc_x2_20260827-014906` (exited before recovery)
- active case at recovery decision: none
- completed before recovery: A1 `20/20`; B1 `1/20` (`django__django-11206` only)
- missing before recovery: B1 `19` IDs; A2 `20` IDs; B2 `20` IDs
- successful IDs are immutable and will not be rerun or overwritten.
- recovery is limited to the same RUN_ROOT and missing IDs; no new experiment directory is created.

{
  "status": "PERMANENT_STOP",
  "reason": "B1_FIRST_CASE_PYRIGHT_OR_FIND_SYMBOL_VALIDATION_FAILED",
  "completed_faces": [
    "A1"
  ],
  "missing_ids": {
    "A1": [],
    "B1": [
      "scikit-learn__scikit-learn-14141",
      "django__django-11066",
      "django__django-12304",
      "django__django-10999",
      "matplotlib__matplotlib-24026",
      "django__django-15104",
      "sympy__sympy-13647",
      "pylint-dev__pylint-7277",
      "scikit-learn__scikit-learn-10844",
      "pylint-dev__pylint-4661",
      "sphinx-doc__sphinx-8621",
      "django__django-13410",
      "django__django-11099",
      "pytest-dev__pytest-6202",
      "django__django-10973",
      "django__django-15572",
      "django__django-14752",
      "pytest-dev__pytest-5809",
      "pytest-dev__pytest-7205"
    ],
    "A2": [
      "django__django-11206",
      "scikit-learn__scikit-learn-14141",
      "django__django-11066",
      "django__django-12304",
      "django__django-10999",
      "matplotlib__matplotlib-24026",
      "django__django-15104",
      "sympy__sympy-13647",
      "pylint-dev__pylint-7277",
      "scikit-learn__scikit-learn-10844",
      "pylint-dev__pylint-4661",
      "sphinx-doc__sphinx-8621",
      "django__django-13410",
      "django__django-11099",
      "pytest-dev__pytest-6202",
      "django__django-10973",
      "django__django-15572",
      "django__django-14752",
      "pytest-dev__pytest-5809",
      "pytest-dev__pytest-7205"
    ],
    "B2": [
      "django__django-11206",
      "scikit-learn__scikit-learn-14141",
      "django__django-11066",
      "django__django-12304",
      "django__django-10999",
      "matplotlib__matplotlib-24026",
      "django__django-15104",
      "sympy__sympy-13647",
      "pylint-dev__pylint-7277",
      "scikit-learn__scikit-learn-10844",
      "pylint-dev__pylint-4661",
      "sphinx-doc__sphinx-8621",
      "django__django-13410",
      "django__django-11099",
      "pytest-dev__pytest-6202",
      "django__django-10973",
      "django__django-15572",
      "django__django-14752",
      "pytest-dev__pytest-5809",
      "pytest-dev__pytest-7205"
    ]
  }
}
