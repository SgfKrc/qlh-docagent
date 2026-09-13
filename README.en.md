# docagent

> **Language**: [English](README.en.md) · [简体中文](README.md)

`docagent` is a standalone, standard-library-first, read-only documentation maintenance scanner.

```text
python tools/docagent/run.py rules
python tools/docagent/run.py scan --root G:/path/to/project --fail-on none
python tools/docagent/run.py scan --root G:/path/to/project --profile minimal --json --fail-on none
python tools/docagent/run.py audit --root G:/path/to/project --output build/docagent/audit.json
python tools/docagent/run.py audit-entry --root G:/path/to/project --doc docs/plan.md --anchor delivery
python tools/docagent/run.py init --root G:/path/to/project
python tools/docagent/run.py config --root G:/path/to/project
```

The P3B core scans a profile-defined documentation tree recursively using the versioned R1-R5 rules contract. Bundled profiles are `qlh` (the main project's Git-aware layout) and `minimal` (Git checks disabled). A project can run `init` to copy a profile into `.docagent/profile.yaml`, or pass `--profile <name-or-path>` explicitly. `scan` never writes the target repository. `audit --output` may write a report outside the configured documentation directory.

The main-project compatibility entrypoint is `python scripts/doc_maintenance_audit.py`. Its historical M1 flags delegate to this package and keep writing `build/doc-audit/audit.json` plus `audit.md`; M2/M3 extension flags remain on the legacy implementation until their dedicated adapters are migrated.

## Dedicated environment configuration

Copy [`.env.docagent.example`](.env.docagent.example) to `.env.docagent` in the
target repository. The copy is ignored by Git. Docagent never reads the main
`.env` and never merges `DOCAGENT_*` values from the process environment.

Validate the file before scanning:

```text
python tools/docagent/run.py config --root .
python tools/docagent/run.py config --root . --env configs/team.docagent.env --json
```

An explicit `--env` is required to exist. Without `--env`, scan/audit remains
backward-compatible when `.env.docagent` is absent; when the project file is
present it is loaded and validated fail-closed. Invalid or duplicate fields,
unsafe URLs, a non-loopback Ollama endpoint, parent-traversing profiles, and an
incomplete explicitly selected remote provider return exit code 2 with a
field-level message. Values are never included in errors.

`DOCAGENT_PROFILE` accepts a bundled name (`qlh` or `minimal`) or a profile
path relative to the env file. Selection precedence is `--profile`, then
`DOCAGENT_PROFILE`, then the existing project/bundled default. Pass the same
env to a scan when it is not located at `<root>/.env.docagent`:

```text
python tools/docagent/run.py scan --root . --env configs/team.docagent.env --json --fail-on none
```

`config` reports which non-secret fields came from the file and which defaults
were applied. `DOCAGENT_DEEPSEEK_API_KEY` is represented only by a configured
boolean; its value is excluded from object representations, stdout, stderr,
JSON reports, and scanner reports. The standalone scanner does not initiate an
LLM request; these provider settings are validated now for the separately
migrated M2 adapter.

For CI conventions, stable exit codes, report formats, and a GitHub Actions example, see [`CI.md`](CI.md).

The dry-run baseline delta is available with `--baseline --dry-run`; it reports `new`, `gone`, `changed`, and `affected_docs`, with optional `--max-new N` and `--max-gone N` gates.

## Targeted entry audit

`audit-entry` checks one explicitly selected Markdown heading section or one
literal entry line. It does not recurse through the documentation tree and it
does not execute tests. The report extracts bounded claim lines, reuses R1/R3
against only the selected content, checks referenced or explicit evidence
paths, reads Git commit/worktree state, and emits a structured “wording versus
actual” difference list.

```text
python tools/docagent/run.py audit-entry --root . \
  --doc docs/release-plan.md --anchor acceptance \
  --evidence test-results/acceptance.xml --json --fail-on warn

python tools/docagent/run.py audit-entry --root . \
  --doc docs/tickets.md --entry DOC-AUDIT-ENTRY-01 --occurrence 2 \
  --markdown --output build/docagent/entry-audit.md --fail-on none
```

`--doc` and every explicit `--evidence` value must be repository-relative;
the document must stay under the profile's `docs_dir`. Ambiguous `--entry`
matches fail closed unless a one-based `--occurrence` is supplied. `--anchor`
accepts a GitHub-style heading slug with or without `#`, including duplicate
heading suffixes such as `-1`. Output files are rejected inside `docs_dir`.

The JSON schema is `qlh.docagent.entry-audit.v1`. `read_only=true` and
`tests_executed=false` make the evidence boundary explicit: a `12 passed`
claim without an existing result artifact is reported as `TEST_RESULT_UNBOUND`.
Small JSON/JUnit XML/log artifacts are parsed and their available counts are
compared; unreadable or mismatched evidence remains a difference instead of
being treated as verified. `--fail-on` accepts `none`, `info`, `warn`, `error`,
`R1`, or `R3`; selector/configuration errors return 2.

Reports include a `report_fingerprint` covering their complete path-free JSON
content. Generate a CI gate artifact and optionally append an M3 event in one
audit invocation:

```text
python tools/docagent/run.py audit --root . --profile qlh --json --fail-on error \
  --output build/docagent-gates/report.json \
  --gate build/docagent-gates/gate.json \
  --events build/docagent-gates/events.sqlite
```

Verify the report against the rules file and all supplied bindings before
accepting the artifact. A changed report or rules file returns code 1; a
malformed artifact or configuration returns code 2:

```text
python tools/docagent/run.py gate verify \
  --report build/docagent-gates/report.json \
  --rules .docagent/rules.yaml --profile qlh \
  --gate build/docagent-gates/gate.json
```

Rules are reverted through Git, then rescanned against the unchanged baseline.
The command is an explicit shorthand for the baseline dry-run and must have a
baseline, so a successful rollback is demonstrated by a zero delta:

```text
git revert <rules-change-commit>
python tools/docagent/run.py gate rescan --root . \
  --baseline build/doc-audit/baseline.json --json --fail-on none
```

Rule evolution is gated through `rules evolve`. An agent may prepare a candidate
rules file, but the candidate is only data: the scanner and validator code are
not part of this entry point. Every evolution record requires a non-empty
`change_note`, stores old/new fingerprints and a stable evolution fingerprint,
and follows `proposed -> preflight -> approved -> released` (or `rejected`).

```text
python tools/docagent/run.py rules evolve \
  --old .docagent/rules.yaml --new rules-next.yaml \
  --change-note "why this candidate is needed" \
  --state approved --output build/docagent-gates/evolution.json
```

Low-risk vocabulary/parameter changes are auto-approved. New or removed
`warn`/`error` rules and severity changes stop at `preflight` with exit code 1.
After review, provide an approval JSON whose
`evolution_fingerprint` matches the preflight record, then transition the
record to `approved` and finally to `released`. See [`CI.md`](CI.md) for the
approval record shape and gate behavior.
