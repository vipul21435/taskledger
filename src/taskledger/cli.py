"""Command line entry point for TaskLedger."""

from __future__ import annotations

import getpass
import json
import sys
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

import typer

from taskledger import __version__
from taskledger.bundle import Bundle, HashError, LoadResult, hash_bundle, load_bundle
from taskledger.cache import CacheError, ObjectStore, cache_bundle
from taskledger.ledger import Ledger, LedgerError, ReviewStatus, TaskRecord, fold_id
from taskledger.lint import (
    REGISTRY,
    UnknownSelectorError,
    format_json,
    format_sarif,
    format_text,
    lint_paths,
)
from taskledger.neardup import DEFAULT_THRESHOLD, MinHasher, NearDupIndex, fingerprint_bundle
from taskledger.settings import Settings, parse_size

app = typer.Typer(
    name="taskledger",
    help=(
        "Validate, lint and content-hash benchmark task bundles, find near-duplicates, "
        "dedupe them in a content-addressed cache and track them in a shared ledger."
    ),
    no_args_is_help=True,
    add_completion=False,
)

JsonFlag = Annotated[bool, typer.Option("--json", help="Print a JSON report instead of text.")]


def _print_version(value: bool) -> None:
    if value:
        typer.echo(f"taskledger {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_print_version,
            is_eager=True,
            help="Show the installed version and exit.",
        ),
    ] = False,
) -> None:
    """TaskLedger command line interface."""


@app.command("version")
def version_cmd() -> None:
    """Print the installed TaskLedger version."""
    typer.echo(__version__)


def _validation_record(path: Path, result: LoadResult) -> dict[str, Any]:
    bundle = result.bundle
    return {
        "path": str(path),
        "valid": bundle is not None,
        "id": bundle.id if bundle else None,
        "version": bundle.version if bundle else None,
        "issues": [issue.to_dict() for issue in result.issues],
    }


@app.command("validate")
def validate_cmd(
    paths: Annotated[list[Path], typer.Argument(help="Bundle directories to validate.")],
    as_json: JsonFlag = False,
) -> None:
    """Check bundles against the task bundle schema. Exit code 1 if any is invalid."""
    results = [(path, load_bundle(path)) for path in paths]
    all_valid = all(result.ok for _, result in results)
    if as_json:
        report = {
            "valid": all_valid,
            "bundles": [_validation_record(path, result) for path, result in results],
        }
        typer.echo(json.dumps(report, indent=2))
    else:
        for path, result in results:
            if result.bundle is not None:
                typer.echo(f"ok       {path}  ({result.bundle.id} {result.bundle.version})")
                continue
            typer.echo(f"invalid  {path}  ({len(result.issues)} issue(s))")
            for issue in result.issues:
                typer.echo(f"  {issue}")
    if not all_valid:
        raise typer.Exit(code=1)


@app.command("hash")
def hash_cmd(
    path: Annotated[Path, typer.Argument(help="Bundle directory to hash.")],
    as_json: JsonFlag = False,
) -> None:
    """Print the canonical content hash of a bundle.

    Line endings, manifest formatting, the authors/created_at/notes metadata and
    OS or tool clutter (.DS_Store, __pycache__, .git) never change the hash; any
    semantic change does. Exit code 1 if the bundle is invalid.
    """
    result = load_bundle(path)
    if result.bundle is None:
        typer.echo(f"invalid  {path}  ({len(result.issues)} issue(s))", err=True)
        for issue in result.issues:
            typer.echo(f"  {issue}", err=True)
        raise typer.Exit(code=1)
    try:
        digest = hash_bundle(result.bundle)
    except HashError as exc:
        typer.echo(f"cannot hash {path}: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if as_json:
        record = {
            "path": str(path),
            "id": result.bundle.id,
            "version": result.bundle.version,
            **digest.to_dict(),
        }
        typer.echo(json.dumps(record, indent=2))
    else:
        typer.echo(f"{digest.value}  {path}")


@app.command("similar")
def similar_cmd(
    paths: Annotated[list[Path], typer.Argument(help="Bundle directories to compare.")],
    threshold: Annotated[
        float,
        typer.Option(
            min=0.01, max=0.99, help="Similarity at or above which two bundles are near-duplicates."
        ),
    ] = DEFAULT_THRESHOLD,
    as_json: JsonFlag = False,
) -> None:
    """Report near-duplicate pairs among bundles (MinHash + LSH).

    Each bundle is fingerprinted from its instruction's word shingles and its
    solution's normalized code tokens (identifiers renamed, comments dropped).
    A pair is reported when either similarity reaches the threshold. Exit code
    1 if any pair is reported or a bundle is invalid.
    """
    index = NearDupIndex(threshold=threshold)
    pairs: list[dict[str, Any]] = []
    invalid = False
    for path in paths:
        result = load_bundle(path)
        if result.bundle is None:
            typer.echo(f"invalid  {path}  ({len(result.issues)} issue(s))", err=True)
            invalid = True
            continue
        key = str(path)
        fingerprint = fingerprint_bundle(result.bundle, index.hasher)
        for match in index.find(fingerprint):
            pairs.append(
                {
                    "a": match.key,
                    "b": key,
                    "similarity": round(match.similarity, 4),
                    "instruction_similarity": round(match.instruction_similarity, 4),
                    "solution_similarity": round(match.solution_similarity, 4),
                }
            )
        if key not in index:
            index.add(key, fingerprint)
    if as_json:
        report = {"threshold": threshold, "bands": index.bands, "rows": index.rows, "pairs": pairs}
        typer.echo(json.dumps(report, indent=2))
    elif pairs:
        for pair in pairs:
            typer.echo(
                f"near-dup  {pair['similarity']:.2f}  {pair['a']}  {pair['b']}  "
                f"(instruction {pair['instruction_similarity']:.2f}, "
                f"solution {pair['solution_similarity']:.2f})"
            )
    else:
        typer.echo(f"No near-duplicates among {len(index)} bundle(s) at threshold {threshold}.")
    if pairs or invalid:
        raise typer.Exit(code=1)


class LintFormat(StrEnum):
    """Output formats of ``taskledger lint``."""

    TEXT = "text"
    JSON = "json"
    SARIF = "sarif"


def _split_selectors(values: list[str] | None) -> list[str]:
    """Flatten repeated and comma separated ``--select``/``--ignore`` values."""
    return [part.strip() for value in values or [] for part in value.split(",") if part.strip()]


@app.command("lint")
def lint_cmd(
    paths: Annotated[list[Path], typer.Argument(help="Bundle directories to lint.")],
    output_format: Annotated[
        LintFormat, typer.Option("--format", "-f", help="Report format.")
    ] = LintFormat.TEXT,
    select: Annotated[
        list[str] | None,
        typer.Option(help="Rule codes or prefixes to enable (repeatable, comma separated)."),
    ] = None,
    ignore: Annotated[
        list[str] | None,
        typer.Option(help="Rule codes or prefixes to disable (repeatable, comma separated)."),
    ] = None,
    hints: Annotated[
        bool, typer.Option("--hints/--no-hints", help="Print a fix hint under each finding.")
    ] = True,
) -> None:
    """Run the lint rules over bundles.

    --select and --ignore are applied after the lint section of each
    bundle's task.toml. --format sarif writes SARIF 2.1.0 for GitHub code
    scanning. Exit code 1 if any finding is an error, 2 on an unknown rule
    code.
    """
    try:
        reports = lint_paths(
            [str(path) for path in paths],
            select=_split_selectors(select),
            ignore=_split_selectors(ignore),
        )
    except UnknownSelectorError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    if output_format is LintFormat.JSON:
        typer.echo(format_json(reports), nl=False)
    elif output_format is LintFormat.SARIF:
        typer.echo(format_sarif(reports), nl=False)
    else:
        typer.echo(format_text(reports, hints=hints), nl=False)
    if any(report.has_errors for report in reports):
        raise typer.Exit(code=1)


@app.command("rules")
def rules_cmd(as_json: JsonFlag = False) -> None:
    """List the registered lint rules with their codes and default severities."""
    rules = REGISTRY.rules
    if as_json:
        typer.echo(json.dumps([rule.to_dict() for rule in rules], indent=2))
        return
    for rule in rules:
        typer.echo(f"{rule.code}  {rule.severity.value:<7}  {rule.name}: {rule.description}")


cache_app = typer.Typer(
    help="Content-addressed dedupe cache: objects stored by the sha256 of their bytes.",
    no_args_is_help=True,
)
app.add_typer(cache_app, name="cache")

CacheDir = Annotated[
    Path | None,
    typer.Option(
        "--cache-dir",
        help="Object store root (default: $TASKLEDGER_HOME/cache, TASKLEDGER_HOME=.taskledger).",
    ),
]


def _store(cache_dir: Path | None) -> ObjectStore:
    return ObjectStore(cache_dir or Settings.from_env().cache_dir)


def _fail(message: str, code: int = 1) -> typer.Exit:
    typer.echo(f"error: {message}", err=True)
    return typer.Exit(code=code)


@cache_app.command("put")
def cache_put_cmd(
    paths: Annotated[list[Path], typer.Argument(help="Bundle directories or plain files.")],
    cache_dir: CacheDir = None,
    as_json: JsonFlag = False,
) -> None:
    """Store bundles (file by file, by canonical digest) or plain files.

    A bundle is stored as its canonical entries plus one tree object; storing
    an identical bundle again writes nothing and reports it as already cached.
    """
    store = _store(cache_dir)
    records: list[dict[str, object]] = []
    for path in paths:
        try:
            if path.is_dir():
                bundle = load_bundle(path)
                if bundle.bundle is None:
                    raise _fail(f"{path} is not a valid bundle (run taskledger validate)")
                result = cache_bundle(store, bundle.bundle)
                records.append({"path": str(path), "type": "bundle", **result.to_dict()})
                state = "already cached" if result.already_cached else "stored"
                line = (
                    f"{result.tree_key}  {path}  ({state}: bundle {result.bundle_hash}, "
                    f"{result.new_objects} of {result.objects} objects new, "
                    f"{result.bytes_written} of {result.bytes_total} bytes written)"
                )
            else:
                put = store.put_file(path)
                records.append(
                    {
                        "path": str(path),
                        "type": "file",
                        "key": put.key,
                        "size": put.size,
                        "created": put.created,
                    }
                )
                state = "stored" if put.created else "already cached"
                line = f"{put.key}  {path}  ({state}: {put.size} bytes)"
        except (OSError, CacheError, HashError) as exc:
            raise _fail(f"cannot cache {path}: {exc}") from exc
        if not as_json:
            typer.echo(line)
    if as_json:
        typer.echo(json.dumps(records, indent=2))


@cache_app.command("get")
def cache_get_cmd(
    key: Annotated[str, typer.Argument(help="Object key (sha256:<hex> or bare hex).")],
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Write to this file, not stdout.")
    ] = None,
    cache_dir: CacheDir = None,
) -> None:
    """Print a stored object, verified against its key. Exit 1 if missing or corrupt."""
    try:
        data = _store(cache_dir).get(key)
    except (CacheError, ValueError) as exc:
        raise _fail(str(exc)) from exc
    if output is None:
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()
    else:
        output.write_bytes(data)


@cache_app.command("has")
def cache_has_cmd(
    keys: Annotated[list[str], typer.Argument(help="Object keys to look up.")],
    cache_dir: CacheDir = None,
) -> None:
    """Report whether each key is stored. Exit 1 if any is missing."""
    store = _store(cache_dir)
    try:
        present = [(key, store.has(key)) for key in keys]
    except ValueError as exc:
        raise _fail(str(exc), code=2) from exc
    for key, found in present:
        typer.echo(f"{'present' if found else 'missing'}  {key}")
    if not all(found for _, found in present):
        raise typer.Exit(code=1)


@cache_app.command("gc")
def cache_gc_cmd(
    max_size: Annotated[
        str, typer.Option("--max-size", help="Byte budget, e.g. 1048576, 512K, 100MB, 2GiB.")
    ],
    cache_dir: CacheDir = None,
    as_json: JsonFlag = False,
) -> None:
    """Delete least recently used objects until the store fits --max-size."""
    try:
        budget = parse_size(max_size)
    except ValueError as exc:
        raise _fail(str(exc), code=2) from exc
    result = _store(cache_dir).gc(budget)
    if as_json:
        typer.echo(json.dumps(result.to_dict(), indent=2))
        return
    typer.echo(
        f"removed {result.removed} object(s), freed {result.freed_bytes} bytes; "
        f"kept {result.kept} object(s), {result.kept_bytes} bytes "
        f"(budget {budget}); {result.stale_tmp_removed} stale temp file(s) removed"
    )


ledger_app = typer.Typer(
    help="Shared ledger: register bundles, detect collisions, move review status, audit.",
    no_args_is_help=True,
)
app.add_typer(ledger_app, name="ledger")

DatabaseUrl = Annotated[
    str | None,
    typer.Option(
        "--db",
        help="SQLAlchemy URL (default: $TASKLEDGER_DATABASE_URL, else SQLite under "
        "$TASKLEDGER_HOME).",
    ),
]
Actor = Annotated[
    str | None,
    typer.Option("--actor", help="Who is acting, for the audit log (default: $USER)."),
]


def _open_ledger(url: str | None) -> Ledger:
    ledger = Ledger(url or Settings.from_env().database_url)
    ledger.migrate()
    return ledger


def _actor(actor: str | None) -> str:
    if actor:
        return actor
    try:
        return getpass.getuser()
    except (KeyError, OSError):  # pragma: no cover - no passwd entry and no $USER
        return "unknown"


def _task_line(task: TaskRecord) -> str:
    return f"{task.slug} {task.version}  {task.status}  {task.content_hash}"


@ledger_app.command("init")
def ledger_init_cmd(db: DatabaseUrl = None) -> None:
    """Create or upgrade the ledger schema (safe to run again)."""
    with _open_ledger(db) as ledger:
        typer.echo(f"ledger ready at {ledger.display_url} (schema revision {ledger.revision()})")


Threshold = Annotated[
    float,
    typer.Option(
        min=0.01, max=0.99, help="Similarity at or above which a task is a near-duplicate."
    ),
]


def _load_for_ledger(path: Path) -> tuple[Bundle, str]:
    result = load_bundle(path)
    if result.bundle is None:
        raise _fail(f"{path} is not a valid bundle (run taskledger validate)")
    try:
        digest = hash_bundle(result.bundle)
    except HashError as exc:
        raise _fail(f"cannot hash {path}: {exc}") from exc
    return result.bundle, digest.value


def _ledger_write(
    path: Path,
    db: str | None,
    actor: str | None,
    *,
    revise: bool,
    threshold: float = DEFAULT_THRESHOLD,
    allow_near_dup: bool = False,
) -> TaskRecord:
    bundle, content_hash = _load_for_ledger(path)
    task = bundle.manifest.task
    fingerprint = fingerprint_bundle(bundle, MinHasher())
    with _open_ledger(db) as ledger:
        try:
            if revise:
                return ledger.revise(
                    slug=task.id,
                    version=task.version,
                    title=task.title,
                    content_hash=content_hash,
                    actor=_actor(actor),
                    fingerprint=fingerprint,
                )
            return ledger.register(
                slug=task.id,
                version=task.version,
                title=task.title,
                content_hash=content_hash,
                actor=_actor(actor),
                fingerprint=fingerprint,
                threshold=threshold,
                allow_near_dup=allow_near_dup,
            )
        except LedgerError as exc:
            raise _fail(str(exc)) from exc


@ledger_app.command("register")
def ledger_register_cmd(
    path: Annotated[Path, typer.Argument(help="Bundle directory to register.")],
    *,
    db: DatabaseUrl = None,
    actor: Actor = None,
    threshold: Threshold = DEFAULT_THRESHOLD,
    allow_near_dup: Annotated[
        bool,
        typer.Option(
            "--allow-near-dup",
            help="Register even if a near-duplicate exists; the matches are audited.",
        ),
    ] = False,
    as_json: JsonFlag = False,
) -> None:
    """Register a bundle as a new draft task.

    Exit code 1 on an invalid bundle, an exact collision (same canonical
    hash), an ID collision (same ID once case and separators are folded) or a
    near-duplicate at or above --threshold (unless --allow-near-dup).
    """
    task = _ledger_write(
        path, db, actor, revise=False, threshold=threshold, allow_near_dup=allow_near_dup
    )
    if as_json:
        typer.echo(json.dumps(task.to_dict(), indent=2))
    else:
        typer.echo(f"registered  {_task_line(task)}")


@ledger_app.command("check")
def ledger_check_cmd(
    path: Annotated[Path, typer.Argument(help="Bundle directory to check.")],
    db: DatabaseUrl = None,
    threshold: Threshold = DEFAULT_THRESHOLD,
    as_json: JsonFlag = False,
) -> None:
    """Report exact, ID and near-duplicate collisions without registering.

    Exit code 1 if any collision is found or the bundle is invalid.
    """
    bundle, content_hash = _load_for_ledger(path)
    slug = bundle.manifest.task.id
    with _open_ledger(db) as ledger:
        exact = ledger.find_by_hash(content_hash)
        id_owner = ledger.find_by_id_key(fold_id(slug))
        near = ledger.near_duplicates(fingerprint_bundle(bundle, MinHasher()), threshold=threshold)
    report: dict[str, Any] = {
        "path": str(path),
        "id": slug,
        "content_hash": content_hash,
        "threshold": threshold,
        "exact": None if exact is None else exact.slug,
        "id_collision": None if id_owner is None else id_owner.slug,
        "near_duplicates": [
            {
                "slug": match.key,
                "similarity": round(match.similarity, 4),
                "instruction_similarity": round(match.instruction_similarity, 4),
                "solution_similarity": round(match.solution_similarity, 4),
            }
            for match in near
        ],
    }
    collided = bool(exact or id_owner or near)
    if as_json:
        typer.echo(json.dumps(report, indent=2))
    else:
        if exact is not None:
            typer.echo(f"exact     {exact.slug}  (same canonical hash {content_hash})")
        if id_owner is not None:
            typer.echo(f"id        {id_owner.slug}  (folds to the same ID as '{slug}')")
        for item in report["near_duplicates"]:
            typer.echo(
                f"near-dup  {item['slug']}  {item['similarity']:.2f}  "
                f"(instruction {item['instruction_similarity']:.2f}, "
                f"solution {item['solution_similarity']:.2f})"
            )
        if not collided:
            typer.echo(f"clear     {path}  (no exact, ID or near-duplicate collision)")
    if collided:
        raise typer.Exit(code=1)


@ledger_app.command("revise")
def ledger_revise_cmd(
    path: Annotated[Path, typer.Argument(help="Bundle directory with the new content.")],
    db: DatabaseUrl = None,
    actor: Actor = None,
    as_json: JsonFlag = False,
) -> None:
    """Record new content for a task in draft or needs_changes.

    The status does not change; move needs_changes to submitted next. Exit
    code 1 in any other status, for an unknown task, or when the content was
    registered before (including as an earlier revision).
    """
    task = _ledger_write(path, db, actor, revise=True)
    if as_json:
        typer.echo(json.dumps(task.to_dict(), indent=2))
    else:
        typer.echo(f"revised     {_task_line(task)}")


@ledger_app.command("status")
def ledger_status_cmd(
    slug: Annotated[str, typer.Argument(help="Task ID.")],
    db: DatabaseUrl = None,
    as_json: JsonFlag = False,
) -> None:
    """Show a task's review status, version and content hash."""
    with _open_ledger(db) as ledger:
        try:
            task = ledger.get(slug)
        except LedgerError as exc:
            raise _fail(str(exc)) from exc
    if as_json:
        typer.echo(json.dumps(task.to_dict(), indent=2))
    else:
        typer.echo(_task_line(task))


@ledger_app.command("transition")
def ledger_transition_cmd(
    slug: Annotated[str, typer.Argument(help="Task ID.")],
    target: Annotated[ReviewStatus, typer.Argument(help="New review status.")],
    db: DatabaseUrl = None,
    actor: Actor = None,
    note: Annotated[str | None, typer.Option(help="Reason, stored in the audit log.")] = None,
) -> None:
    """Move a task to a new review status. Exit code 1 on an illegal transition."""
    with _open_ledger(db) as ledger:
        try:
            before = ledger.get(slug).status
            task = ledger.transition(slug, target, actor=_actor(actor), note=note)
        except LedgerError as exc:
            raise _fail(str(exc)) from exc
    typer.echo(f"{slug}: {before} -> {task.status}")


@ledger_app.command("history")
def ledger_history_cmd(
    slug: Annotated[str | None, typer.Argument(help="Task ID (default: the whole log).")] = None,
    db: DatabaseUrl = None,
    as_json: JsonFlag = False,
) -> None:
    """Print audit log rows, oldest first."""
    with _open_ledger(db) as ledger:
        rows = ledger.history(slug)
    if as_json:
        typer.echo(json.dumps([row.to_dict() for row in rows], indent=2))
        return
    for row in rows:
        typer.echo(
            f"{row.seq:>4}  {row.at}  {row.actor}  {row.action}  {row.task_slug or '-'}  "
            f"{row.payload}  {row.row_hash[:12]}"
        )


@ledger_app.command("verify")
def ledger_verify_cmd(db: DatabaseUrl = None, as_json: JsonFlag = False) -> None:
    """Recompute the audit hash chain. Exit code 1 if any row was tampered with."""
    with _open_ledger(db) as ledger:
        report = ledger.verify_chain()
    if as_json:
        typer.echo(json.dumps(report.to_dict(), indent=2))
    elif report.ok:
        typer.echo(f"audit chain ok: {report.entries} entries, head {report.head}")
    else:
        typer.echo(
            f"audit chain BROKEN at seq {report.first_bad_seq}: {report.reason} "
            f"({report.entries} entries verified before it)"
        )
    if not report.ok:
        raise typer.Exit(code=1)
