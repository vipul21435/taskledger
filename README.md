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
make demo         # smoke-run the CLI (grows into the end-to-end demo)
```

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
