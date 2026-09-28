# TaskLedger

[![CI](https://github.com/vipul21435/taskledger/actions/workflows/ci.yml/badge.svg)](https://github.com/vipul21435/taskledger/actions/workflows/ci.yml)
![Python 3.12](https://img.shields.io/badge/python-3.12-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

Submission pipeline tooling for teams that build benchmark tasks for AI coding
agents.

A benchmark task is a small bundle: an instruction, a pinned environment image,
a reference solution and a byte-exact grader. When dozens of people author
tasks in parallel, the expensive failures are not in any single task. They are
in the pipeline: two authors submit the same idea, a grader quietly depends on
the network, an image tag drifts, the same environment is built five times on
one machine, or a reviewer accepts a task whose grader passes the untouched
starting state. TaskLedger catches those before a reviewer ever sees the task.

## What it does

| Stage | What TaskLedger checks or provides |
| --- | --- |
| Schema | A typed (pydantic) bundle manifest and loader with precise error paths |
| Lint | Rule registry with stable codes: missing tests, unpinned image digests, network use in graders, nondeterminism signals, oversized files, committed secrets. Text, JSON and SARIF output |
| Hash | Canonical content hash of a bundle (normalized line endings, ignored metadata) and a content-addressed dedupe cache |
| Ledger | Shared ledger (SQLite by default, Postgres via an SQLAlchemy URL) with exact collisions by hash, near-duplicate detection (MinHash + LSH), ID collisions, a review state machine and an append-only audit log |
| Locks | Cross-process file locks and DB-backed leases with TTL and stale-lock recovery, plus a build cache keyed by recipe hash so no environment is built twice |
| Gates | Review-gates runner (lint, docker build, reference passes, baseline fails, grader determinism re-run) with a report and a composite GitHub Action for pull requests |
| Serve | FastAPI service, Typer CLI, Prometheus `/metrics`, docker-compose with Postgres |

Status: under active development. See [PLAN.md](PLAN.md) for the ordered
implementation slices and what is done so far. Only features marked done there
are implemented.

## Quickstart

Requires [uv](https://docs.astral.sh/uv/) and GNU make. uv installs Python 3.12
for you.

```bash
git clone https://github.com/vipul21435/taskledger.git
cd taskledger
make install      # uv sync --frozen + pre-commit hooks
make check        # ruff, mypy --strict, pytest with coverage
make demo         # validate the example bundles (grows into the end-to-end demo)
```

## Task bundle format

A task bundle is a directory with a `task.toml` manifest. Every section except
`[task]` is optional and defaults to the conventional layout below.

```
modular-inverse-table/
  task.toml               manifest: id, semver version, images, limits
  instruction.md          what the agent is asked to do
  environment/Dockerfile  image the agent works in (digest-pinned base)
  verifier/Dockerfile     image the grader runs in
  solution/solve.sh       reference solution that computes the answer
  tests/                  byte-exact pytest grader
  baseline/               optional untouched starting workspace
```

A minimal manifest:

```toml
schema_version = 1

[task]
id = "sum-of-squares"        # lowercase slug, 3-64 chars
version = "1.0.0"            # semantic version
title = "Sum of squares of a list"
category = "arithmetic"
```

The schema is strict: values are never coerced (`version = 1` or
`verifier_sec = "30"` are errors), unknown keys are rejected, every path must be
relative and stay inside the bundle (also through symlinks), image references
follow the registry grammar and timeouts and resources are bounded. The full
model lives in [`src/taskledger/bundle/manifest.py`](src/taskledger/bundle/manifest.py).

Two original example bundles ship in [`examples/bundles/`](examples/bundles/):
a modular-inverse table over a composite modulus and a unimodular integer
linear system (determinant +1 or -1, so exactly one integer answer). Their
graders recompute every answer independently and pin the input by digest; a
test runs each grader against the untouched baseline (must fail), the reference
solution (must pass, twice) and a corrupted output (must fail).

### Validate

`taskledger validate PATH...` reports every problem at once, each with a precise
location, and exits 1 if any bundle is invalid (`--json` for a machine-readable
report):

```console
$ taskledger validate examples/bundles/*
ok       examples/bundles/integer-linear-system  (integer-linear-system 1.0.0)
ok       examples/bundles/modular-inverse-table  (modular-inverse-table 1.0.0)

$ taskledger validate broken
invalid  broken  (4 issue(s))
  task.toml:task.id: must be a lowercase slug of 3-64 characters (letters, digits and single hyphens), got 'Modular_Inverse'
  task.toml:task.version: must be a semantic version such as 1.0.0 or 2.1.0-rc.1, got '1.0'
  task.toml:instruction.path: must stay inside the bundle, got '../instruction.md'
  task.toml:resources.cpus: Input should be a valid number
```

Schema errors are reported first; once the manifest is valid, every declared
path is checked on disk (`file not found`, `expected a directory`,
`resolves outside the bundle`).

## Development

| Command | What it runs |
| --- | --- |
| `make lint` | `ruff check` and `ruff format --check` |
| `make typecheck` | `mypy --strict` over `src/` |
| `make test` | `pytest -q` |
| `make cov` | pytest with branch coverage, fails under 85% |
| `make check` | all of the above, same as CI |

Layout: `src/taskledger/` (typed package, `py.typed`), `tests/`,
`.github/workflows/ci.yml`.

## Why this exists

It is modeled on submission tooling I built for benchmark-task work at an
AI-data company: per-repo build recipes, collision detection against a shared
ledger, build locks and dedupe caches that let a team submit tasks in parallel
without duplicates, broken graders or wasted builds. Everything in this
repository, including every example task, rule and dataset, is original.

## License

MIT, see [LICENSE](LICENSE).
