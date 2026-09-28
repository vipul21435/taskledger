# TaskLedger implementation plan

TaskLedger is submission pipeline tooling for teams that build benchmark tasks
for AI coding agents: schema-checked task bundles, automated review gates,
content-addressed dedupe, collision detection against a shared ledger, and
build locks. This file is the source of truth for scope and order. Work the
slices top to bottom; each one lands as 3-4 real, tested commits.

## Decisions

- Fresh repository, no fork. The GitHub search API was rate limited during the
  fork-base search, and the spec (bundle schema, ledger, leases, gates) is
  bespoke enough that no small upstream would be genuinely built on. MIT
  license, Copyright (c) 2026 Vipul Raj Jha.
- Python 3.12 only, uv with a committed uv.lock, src/ layout, ruff (lint +
  format), mypy --strict on src/, pytest + pytest-cov with branch coverage and
  `fail_under = 85`. pytest runs with `filterwarnings = error`.
- Runtime stack, added only by the slice that first needs it: pydantic v2 and
  pydantic-settings (schema, config), SQLAlchemy 2.0 (ledger; SQLite default,
  Postgres through `postgresql+psycopg://` URLs, psycopg as an optional
  `postgres` extra), Alembic (migrations), FastAPI + uvicorn (service),
  prometheus-client (metrics), Typer (CLI). No torch, numpy or other heavy
  dependencies: MinHash and LSH are implemented in pure Python.
- No paid API or GPU anywhere. Docker is only required for the docker-build
  gate and the compose demo; every other gate has a local subprocess executor
  so the test suite runs without a Docker daemon.
- File locks use `fcntl.flock` (POSIX). Windows is out of scope and documented
  as such.
- Every example bundle, lint fixture, dataset and rubric in this repository is
  original synthetic content (for example integer linear algebra and modular
  arithmetic tasks whose answers are computed, not typed in).
- Configuration comes from `TASKLEDGER_*` environment variables (see
  `.env.example`); nothing secret is committed.

## Target layout

```
src/taskledger/
  bundle/     manifest model, loader, canonical hashing
  cache/      content-addressed store (dedupe cache, build cache backend)
  lint/       rule registry, rules, text/JSON/SARIF formatters
  ledger/     SQLAlchemy models, repository, state machine, audit log, collisions
  neardup/    tokenizer, MinHash, LSH index
  locks/      file locks, DB leases, build cache
  gates/      gate runner, executors, reports
  api/        FastAPI app, routes, Prometheus metrics
  cli.py      Typer entry point (each slice adds its subcommands)
  settings.py pydantic-settings configuration
examples/bundles/   original sample task bundles used by tests and the demo
action.yml          composite GitHub Action (slice 6)
```

## Working rules for every slice

- `make check` (ruff, mypy --strict, pytest with coverage) is green before each
  commit. Conventional Commits. No empty, padding or typo-only commits.
- Tests land in the same commit as the code they cover.
- Every number that appears in README.md comes from a command that was actually
  run, and the command is shown next to it.
- ASCII only in code comments, docs and commit messages.

## Slices

### Slice 1: Task bundle schema, loader and canonical content hashing

Goal: Define the on-disk task bundle format (task.toml manifest plus instruction.md, environment/Dockerfile, verifier/Dockerfile, solution/, tests/ and an optional baseline/) as a pydantic v2 model with strict field validation (slug IDs, semver versions, relative paths that must exist and stay inside the bundle, image references, timeouts), a loader that returns a typed Bundle or a list of precise error paths, and two original example bundles under examples/bundles/. Add canonical content hashing: a Merkle-style sha256 over sorted relative paths and file digests with CRLF/CR normalized to LF, ignorable metadata fields (authors, created_at, notes) and ignore patterns (.DS_Store, __pycache__, .git), so cosmetic changes never change the hash and any semantic change always does (hypothesis property tests). Expose `taskledger validate PATH` and `taskledger hash PATH [--json]`. Commits: manifest model + loader; example bundles + validate CLI; canonical hasher + hash CLI with property tests.

### Slice 2: Bundle linter with rule registry, rule codes and SARIF output

Goal: Build a linter around a decorator-based rule registry where each rule has a stable code, name, default severity, description and fix hint, and can be selected or ignored by code or prefix (config in task.toml or CLI flags). Ship rules TL001 missing or empty tests, TL002 unpinned base image (FROM without @sha256 digest, including multi-stage and ARG-substituted FROM lines), TL003 network use in the grader (requests/httpx/urllib/socket imports, curl/wget/pip install at test time), TL004 nondeterminism signals (unseeded random, time/datetime.now, uuid4, os.urandom, set-order dependent output, unsorted directory listings), TL005 oversized files, TL006 committed secrets (known key formats plus a Shannon-entropy check, with redacted findings). Findings carry file, line and column. Formatters: human text, JSON, and SARIF 2.1.0 validated against the schema's required fields so GitHub code scanning accepts it. Expose `taskledger lint PATH... --format text|json|sarif --select --ignore` with exit code 1 on errors. Commits: registry + finding model + text/JSON formatters; TL001-TL003; TL004-TL006; SARIF formatter + CLI.

### Slice 3: Content-addressed dedupe cache and ledger core

Goal: Add a content-addressed store (objects/ab/cdef... fan-out, atomic write via temp file + os.replace, verify-on-read, size-bounded LRU garbage collection) used as the dedupe cache so an identical bundle is recognized without re-running anything. Build the shared ledger on SQLAlchemy 2.0: tasks, submissions, content_hashes and audit_log tables with an Alembic baseline migration, SQLite by default and Postgres via TASKLEDGER_DATABASE_URL. Implement exact collision detection by canonical hash (unique constraint, so races resolve in the database), ID collision checks (same slug, different content; case and separator folded), a review status state machine (draft -> submitted -> in_review -> accepted | rejected | needs_changes, needs_changes -> submitted) that rejects illegal transitions with a typed error, and an append-only, hash-chained audit log (each row stores the previous row's hash; UPDATE/DELETE blocked by triggers; `verify_chain()` detects tampering). Expose `taskledger cache put|get|has|gc` and `taskledger ledger init|register|status|transition|history`. Commits: CAS store + cache CLI; models + migration + repository with exact and ID collisions; state machine; audit log + ledger CLI.

### Slice 4: Near-duplicate detection with MinHash and LSH

Goal: Detect paraphrased or lightly edited resubmissions. Implement a tokenizer that produces word shingles from the instruction and normalized code tokens from the solution (identifiers canonicalized, comments and whitespace stripped), a pure-Python MinHash with a seeded 64-bit universal hash family (deterministic across processes and machines), and an LSH index with band/row parameters chosen from the target threshold by minimizing false positive plus false negative area under the S-curve. Persist signatures and band buckets in the ledger (indexed table) so candidate lookup is a query, then confirm candidates with the signature Jaccard estimate and report instruction and solution similarity separately. Integrate into `taskledger ledger check PATH`, which reports exact, near-duplicate and ID collisions in one JSON/text report and refuses registration above the threshold unless `--allow-near-dup` is given (recorded in the audit log). Tests cover estimator error bounds, the band/row optimizer, and precision/recall on an original synthetic paraphrase corpus. Commits: tokenizer + MinHash; LSH index + parameter optimizer; ledger persistence + check CLI.

### Slice 5: Build locks, DB leases, build cache and concurrency tests

Goal: Guarantee that no environment is built twice and that two workers never build the same thing at once. Implement a cross-process file lock (fcntl.flock, timeout, context manager, reentrancy guard) and DB-backed leases with owner tokens, TTL, heartbeat renewal, monotonic fencing tokens and stale-lock recovery (an expired lease is taken over with a single compare-and-swap UPDATE, so exactly one contender wins). Add a build cache keyed by recipe hash (Dockerfile bytes + canonical build-context hash + build args + platform) that records the resulting image ID, with `build_once(recipe, builder)` combining lease, cache lookup and build. Prove it with multiprocessing tests: N processes contending for one file lock and one lease never overlap (checked through a shared timeline), stale leases are recovered after TTL, `build_once` runs the builder exactly once across N processes, and N processes registering the same bundle in the ledger yield exactly one accepted registration and N-1 exact collisions. Expose `taskledger lock acquire|release|status` and `taskledger build-cache get|list`. Commits: file lock; DB leases with fencing + stale recovery; build cache + build_once; multiprocessing concurrency suite.

### Slice 6: Review-gates runner and composite GitHub Action

Goal: Run the checks a reviewer would, automatically, and produce one report. Gates: lint (slice 2), docker build of the environment and verifier images through the build cache (slice 5), reference solution passes the grader, baseline (untouched starting state) fails the grader, and grader determinism (re-run N times, byte-exact comparison of results). Gates run through an Executor protocol with a DockerExecutor (docker CLI, network disabled, CPU/memory/time limits) and a LocalExecutor (subprocess in a temp copy) so the suite runs without Docker; each gate has a timeout, can be skipped with a reason, and later gates are skipped when a prerequisite fails. Reports: JSON (machine), Markdown (PR comment) and JUnit XML (CI test tab), with per-gate durations. Ship a composite action (action.yml) that finds bundles changed in a pull request, runs `taskledger gates`, uploads SARIF and posts the Markdown summary to the job summary, plus an example workflow that exercises it on examples/bundles/. Expose `taskledger gates PATH... --executor local|docker --report-dir`. Commits: gate model + runner + LocalExecutor; DockerExecutor + build gate; report formats; composite action + example workflow.

### Slice 7: FastAPI service and Prometheus metrics

Goal: Serve the ledger to a whole team. A FastAPI app factory with typed request/response models: POST /bundles/check (exact, near and ID collisions for an uploaded bundle archive or manifest + hashes), POST /tasks (register), POST /tasks/{id}/transitions, GET /tasks/{id} and /tasks/{id}/history, lease endpoints (acquire, renew, release) and POST /lint. Optional bearer-token auth via TASKLEDGER_API_TOKEN, structured JSON logging with request IDs, consistent problem+json errors, /healthz and /readyz (DB ping). A Prometheus /metrics endpoint exposes request counts and latency histograms by route, registrations and collisions by kind, lease acquisitions, contention and stale recoveries, and gate outcomes. Add `taskledger serve` and a thin HTTP client so the CLI can target a remote ledger with --server. Tests use FastAPI's TestClient against SQLite. Commits: app factory + ledger routes; lease + lint routes + auth; metrics + serve command + remote client.

### Slice 8: Docker image, docker-compose with Postgres and end-to-end demo

Goal: Make the whole system runnable with one command. A slim multi-stage Dockerfile (uv builder, python:3.12-slim runtime pinned by digest, non-root user, HEALTHCHECK, label project=taskledger), a docker-compose.yml with Postgres (healthcheck, named volume), a one-shot migrate service and the API, and a CI job that runs the Postgres-marked integration tests against a Postgres service container so both backends are tested. `make demo` runs an end-to-end scenario on the example bundles with a scripted narrative: validate, lint (with one intentionally broken bundle producing SARIF), hash, register, resubmit the same bundle (exact collision), submit a paraphrased copy (near-duplicate flagged), race two builds for the same recipe (one build, one cache hit), run the gates, walk the state machine to accepted, and print the audit chain verification; `make demo-compose` does the same against the compose stack. Commits: Dockerfile + compose; Postgres CI job + integration tests; demo script + Makefile targets.

### Slice 9: Benchmarks and documentation polish

Goal: Back every claim with a reproducible number and make the repo easy to evaluate. Add benchmarks/ scripts (plain Python, JSON output) for canonical hash throughput, near-duplicate precision/recall and LSH query latency at 1k/10k ledger sizes, lease acquisition latency and contention behavior, and gate runtime on the example bundles, with a `make bench` target. Write docs/architecture.md (components, data model, lock and lease protocol, state machine diagram in Mermaid), docs/rules.md generated from the rule registry (with a test that fails when it is stale), docs/bundle-format.md, and ADRs for the main design choices. Finish README: feature list with real numbers and the command that produced each, CLI and API reference, coverage badge value from `make cov`, and a CHANGELOG for v0.1.0. Commits: benchmarks + make bench; architecture/format docs + ADRs; generated rules reference + README numbers + CHANGELOG.

## Slice 1 decisions

- Sections other than `[task]` default to the conventional layout, so the
  minimal manifest is `schema_version` plus `[task]`. The model is purely
  syntactic (usable by the API on a bare manifest); the loader checks declared
  paths on disk and reports at the same field locations. Schema errors are
  reported before path errors because paths come from a valid model.
- Strict mode everywhere (no `"5"` to `5`); TOML arrays are converted to tuples
  before validation. Tags are sorted, so their order is cosmetic.
- `baseline/` is the untouched starting workspace. The example environment
  images are built with `context = "baseline"`, so a reference solution can
  never leak into the agent image by construction.
- Hashing hashes the validated manifest (canonical JSON, `exclude_defaults`,
  metadata dropped) rather than TOML bytes: formatting and spelled-out
  defaults are cosmetic, and adding a defaulted field in a later schema
  version does not change existing hashes. A file is text iff it has no NUL
  byte (the usual git heuristic); only text gets line endings normalized.
  File modes are not hashed (entry points are run through `bash`). Symlinks
  are hashed by target and never followed. File names are compared in NFC.
- Per-file digests are plain sha256 of the canonical content (checkable with
  `sha256sum`); directory entries carry a kind byte so a file and a
  directory or symlink can never produce the same parent node.

## Slice 2 decisions

- TL005 counts the same file set the canonical hash covers (no `.git`,
  `__pycache__` or symlinks). A file over `max_file_kb` is reported at the
  file; a bundle over `max_bundle_kb` at `lint.max_bundle_kb` in `task.toml`
  (its `[lint]` header, else line 1) with the three largest files named.
- TL006 runs on every text file; files above the 4 MiB content cache are
  streamed (binary if the first 8 KiB hold a NUL). Known formats are exact
  provider shapes; the entropy check is gated on a secret-looking name, needs
  16+ characters with letters and digits and at least 3.5 bits per character
  (3.0 for hex). Messages are built from a redacted form only.
- SARIF: one run per invocation (GitHub rejects several runs with the same
  tool and category), rules = union of the rules that ran, columns declared as
  Unicode code points, `%SRCROOT%`-relative URIs for relative paths. The
  required-fields checker lives in `src/` so the gates runner (slice 6) can
  reuse it before an upload.

## Status

| Slice | State |
| --- | --- |
| Scaffold (pyproject, uv.lock, tooling, CI, README) | done |
| 1. Bundle schema, loader, canonical hashing | done |
| 2. Linter, rule registry, SARIF | done |
| 3. Dedupe cache and ledger core | todo |
| 4. Near-duplicate detection | todo |
| 5. Locks, leases, build cache, concurrency tests | todo |
| 6. Review gates and GitHub Action | todo |
| 7. FastAPI service and metrics | todo |
| 8. Docker, compose, end-to-end demo | partial: digest-pinned image, scripts/demo.sh, make demo/docker-demo, CI demo job done; compose, Postgres CI todo |
| 9. Benchmarks and docs polish | todo |
