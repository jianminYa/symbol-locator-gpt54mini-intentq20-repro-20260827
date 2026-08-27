# B1 first-case process evidence

This is a redacted record of the read-only process scan performed while the original B1 first case was active. It contains no prompt, response, header, or credential.

- evaluator process group: `257818`
- LocAgent child process group: `257855`
- observed child-owned Pyright process: PID `259191`, PPID `259185`, PGID `257855`, command class `pyright-langserver --stdio`
- observed Pyright node child: PID `259194`, PPID `259191`, PGID `257855`, command class `node ... pyright ... langserver`
- the Pyright processes were descendants of the active LocAgent chain for B1 / `django__django-11206`.
- preserved attempt flags independently record `pyright_warmup_success=true` and `find_symbol_nonempty=true`.
