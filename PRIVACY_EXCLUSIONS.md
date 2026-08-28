# Privacy exclusions

This publication contains no API secret, `.env` or `.secrets` file, settings
file, Git credential, authorization header, cookie, authenticated URL, virtual
environment, benchmark repository clone, cache, or temporary socket.

The A1/B1 trace payloads and localize logs are retained because they are part
of the requested reproducibility artifact. They were scanned for key-shaped
values and authorization headers before publication. Reports and summaries
contain paths, sizes, structure checks, counts, and redacted usage metadata;
they do not copy prompts or model response prose.

The source tree is published without `SWE-Explore-Bench/repos/`. Local absolute
paths in diagnostics are provenance evidence only; they do not contain secret
values. Large local before/after manifests were retained locally as audit
evidence and excluded from the GitHub package.
