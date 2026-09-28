"""Property tests: cosmetic changes never change the bundle hash, semantic ones always do."""

from __future__ import annotations

import copy
import json
import tempfile
import tomllib
from datetime import date
from itertools import pairwise
from pathlib import Path
from typing import Any

from hypothesis import assume, given
from hypothesis import strategies as st

from taskledger.bundle import Bundle, Manifest, hash_bundle, hash_files
from taskledger.bundle.hashing import NewlineNormalizer, normalize_newlines

Tree = dict[str, bytes]

# File names never match an ignore pattern: no leading dot, no ".pyc", too short
# for "__pycache__".
NAMES = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789_-", min_size=1, max_size=6)
PATHS = st.lists(NAMES, min_size=1, max_size=3).map("/".join)
LF_TEXT = st.text(
    alphabet=st.characters(codec="utf-8", exclude_characters="\r\x00"), max_size=80
).map(str.encode)
BINARY = st.tuples(st.binary(max_size=40), st.binary(max_size=40)).map(
    lambda parts: parts[0] + b"\x00" + parts[1]
)
CONTENT = st.one_of(LF_TEXT, BINARY)


def _drop_conflicts(tree: Tree) -> Tree:
    """Keep only paths that are not also used as a directory, and not the manifest."""
    return {
        path: data
        for path, data in tree.items()
        if path != "task.toml" and not any(other.startswith(path + "/") for other in tree)
    }


TREES = st.dictionaries(PATHS, CONTENT, max_size=6).map(_drop_conflicts)
NON_EMPTY_TREES = TREES.filter(bool)

SLUGS = st.from_regex(r"[a-z][a-z0-9]{2,8}(-[a-z0-9]{1,6}){0,2}", fullmatch=True)
TAGS = st.lists(st.from_regex(r"[a-z]{2,8}", fullmatch=True), unique=True, max_size=4)
TITLES = st.text(
    alphabet=st.characters(min_codepoint=0x20, max_codepoint=0x7E), min_size=3, max_size=40
).filter(lambda title: len(title.strip()) >= 3)
VERSIONS = st.tuples(*[st.integers(0, 30)] * 3).map(lambda v: f"{v[0]}.{v[1]}.{v[2]}")


@st.composite
def manifest_data(draw: st.DrawFn) -> dict[str, Any]:
    """A valid manifest as the dict tomllib would produce, with optional sections."""
    data: dict[str, Any] = {
        "schema_version": 1,
        "task": {
            "id": draw(SLUGS),
            "version": draw(VERSIONS),
            "title": draw(TITLES),
            "category": draw(SLUGS),
            "difficulty": draw(st.sampled_from(["easy", "medium", "hard"])),
            "tags": draw(TAGS),
        },
    }
    if draw(st.booleans()):
        data["timeouts"] = {"verifier_sec": draw(st.integers(1, 3600))}
    if draw(st.booleans()):
        data["resources"] = {
            "network": draw(st.booleans()),
            "memory_mb": draw(st.integers(64, 8192)),
        }
    if draw(st.booleans()):
        data["verifier"] = {"command": draw(st.lists(SLUGS, min_size=1, max_size=4))}
    if draw(st.booleans()):
        data["baseline"] = {"path": "baseline"}
    return data


METADATA = st.fixed_dictionaries(
    {},
    optional={
        "authors": st.lists(TITLES, max_size=3),
        "created_at": st.dates(min_value=date(2000, 1, 1), max_value=date(2100, 1, 1)),
        "notes": TITLES,
    },
)


LINT = st.fixed_dictionaries(
    {},
    optional={
        "select": st.lists(st.sampled_from(["ALL", "TL", "TL00", "TL002", "TL004"]), max_size=3),
        "ignore": st.lists(st.sampled_from(["TL001", "TL005", "TL006"]), max_size=3),
        "max_file_kb": st.integers(1, 4096),
        "max_bundle_kb": st.integers(1, 65536),
    },
)


def _toml_value(value: object, literal: bool) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | date):
        return str(value)
    if isinstance(value, str):
        if literal and "'" not in value:
            return f"'{value}'"
        return json.dumps(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item, literal) for item in value) + "]"
    raise TypeError(value)


@st.composite
def rendered_toml(draw: st.DrawFn, data: dict[str, Any]) -> str:
    """Render ``data`` as TOML with random key order, spacing, quoting and comments."""
    literal = draw(st.booleans())
    equals = draw(st.sampled_from(["=", " = ", "  =\t"]))
    lines = [f"schema_version{equals}{data['schema_version']}"]
    tables = draw(st.permutations([key for key in data if key != "schema_version"]))
    for table in tables:
        if draw(st.booleans()):
            lines.append(f"# {table} section")
        lines.append(f"[{table}]")
        for key in draw(st.permutations(list(data[table]))):
            lines.append(f"{key}{equals}{_toml_value(data[table][key], literal)}")
        lines.append("" if draw(st.booleans()) else "\n")
    return "\n".join(lines) + "\n"


def canonical_content(data: bytes) -> bytes:
    """What the hash sees: binary as is, text with normalized line endings."""
    return data if b"\x00" in data else normalize_newlines(data)


def to_manifest(toml_text: str) -> Manifest:
    return Manifest.model_validate(tomllib.loads(toml_text))


@st.composite
def with_line_ending_noise(draw: st.DrawFn, tree: Tree) -> Tree:
    """Replace each LF in each text file by LF, CRLF or CR, chosen per occurrence."""
    noisy: Tree = {}
    for path, data in tree.items():
        if b"\x00" in data:
            noisy[path] = data
            continue
        pieces = data.split(b"\n")
        endings = draw(st.lists(st.sampled_from([b"\n", b"\r\n", b"\r"]), min_size=len(pieces)))
        out = pieces[0]
        for piece, ending in zip(pieces[1:], endings, strict=False):
            out += ending + piece
        noisy[path] = out
    return noisy


BASE_MANIFEST = Manifest.model_validate(
    {
        "schema_version": 1,
        "task": {"id": "prop-task", "version": "1.0.0", "title": "Property", "category": "misc"},
    }
)


# -- cosmetic changes never change the hash ---------------------------------------------


@given(st.data(), TREES)
def test_line_endings_never_change_the_hash(data: st.DataObject, tree: Tree) -> None:
    noisy = data.draw(with_line_ending_noise(tree))
    assert hash_files(noisy, BASE_MANIFEST) == hash_files(tree, BASE_MANIFEST)


@given(
    TREES,
    st.lists(
        st.tuples(
            st.lists(NAMES, max_size=2),
            st.sampled_from([".DS_Store", "__pycache__/m.cpython-312.pyc", ".git/HEAD", "x.pyc"]),
            st.binary(max_size=20),
        ),
        min_size=1,
        max_size=4,
    ),
)
def test_ignored_entries_never_change_the_hash(
    tree: Tree, junk: list[tuple[list[str], str, bytes]]
) -> None:
    cluttered = dict(tree)
    for parents, name, content in junk:
        cluttered["/".join([*parents, name])] = content
    assume(all(p == q or not q.startswith(p + "/") for p in cluttered for q in cluttered))
    assert hash_files(cluttered, BASE_MANIFEST) == hash_files(tree, BASE_MANIFEST)


@given(st.data(), manifest_data(), TREES)
def test_manifest_formatting_never_changes_the_hash(
    data: st.DataObject, manifest: dict[str, Any], tree: Tree
) -> None:
    first = to_manifest(data.draw(rendered_toml(manifest)))
    second = to_manifest(data.draw(rendered_toml(manifest)))
    assert hash_files(tree, first) == hash_files(tree, second)


@given(manifest_data(), METADATA, METADATA)
def test_metadata_never_changes_the_hash(
    manifest: dict[str, Any], meta_a: dict[str, Any], meta_b: dict[str, Any]
) -> None:
    a, b = copy.deepcopy(manifest), copy.deepcopy(manifest)
    a["task"] |= meta_a
    b["task"] |= meta_b
    first, second = Manifest.model_validate(a), Manifest.model_validate(b)
    assert hash_files({}, first) == hash_files({}, second)


@given(manifest_data(), LINT, LINT)
def test_lint_configuration_never_changes_the_hash(
    manifest: dict[str, Any], lint_a: dict[str, Any], lint_b: dict[str, Any]
) -> None:
    first = Manifest.model_validate({**manifest, "lint": lint_a})
    second = Manifest.model_validate({**manifest, "lint": lint_b})
    assert hash_files({}, first) == hash_files({}, second)


@given(manifest_data())
def test_spelling_out_defaults_never_changes_the_hash(manifest: dict[str, Any]) -> None:
    explicit = copy.deepcopy(manifest)
    explicit.setdefault("instruction", {})["path"] = "./instruction.md"
    explicit.setdefault("solution", {}).update({"path": "solution", "entrypoint": "solve.sh"})
    explicit.setdefault("timeouts", {}).setdefault("build_sec", 900)
    explicit["task"]["tags"] = list(reversed(explicit["task"]["tags"]))
    implicit, spelled = Manifest.model_validate(manifest), Manifest.model_validate(explicit)
    assert hash_files({}, implicit) == hash_files({}, spelled)


@given(st.data(), TREES)
def test_disk_and_memory_hashes_agree(data: st.DataObject, tree: Tree) -> None:
    noisy = data.draw(with_line_ending_noise(tree))
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for relative, content in noisy.items():
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        on_disk = hash_bundle(Bundle(root=root, manifest=BASE_MANIFEST))
    assert on_disk == hash_files(tree, BASE_MANIFEST)


@given(st.binary(max_size=200), st.lists(st.integers(0, 200), max_size=8))
def test_streaming_normalizer_matches_whole_normalization(data: bytes, cuts: list[int]) -> None:
    data = data.replace(b"a", b"\r").replace(b"b", b"\n")  # plenty of CR/LF
    bounds = [0, *sorted(min(cut, len(data)) for cut in cuts), len(data)]
    normalizer = NewlineNormalizer()
    out = b"".join(normalizer.feed(data[lo:hi]) for lo, hi in pairwise(bounds))
    assert out + normalizer.flush() == normalize_newlines(data)


# -- semantic changes always change the hash ---------------------------------------------


@given(st.data(), NON_EMPTY_TREES, CONTENT)
def test_any_content_change_changes_the_hash(
    data: st.DataObject, tree: Tree, new_content: bytes
) -> None:
    path = data.draw(st.sampled_from(sorted(tree)))
    assume(canonical_content(new_content) != canonical_content(tree[path]))
    changed = {**tree, path: new_content}
    assert hash_files(changed, BASE_MANIFEST).value != hash_files(tree, BASE_MANIFEST).value


@given(TREES, PATHS, CONTENT)
def test_adding_a_file_changes_the_hash(tree: Tree, path: str, content: bytes) -> None:
    grown = _drop_conflicts({**tree, path: content})
    assume(path in grown and path not in tree and grown.keys() > tree.keys())
    assert hash_files(grown, BASE_MANIFEST).value != hash_files(tree, BASE_MANIFEST).value


@given(st.data(), NON_EMPTY_TREES)
def test_removing_a_file_changes_the_hash(data: st.DataObject, tree: Tree) -> None:
    path = data.draw(st.sampled_from(sorted(tree)))
    shrunk = {key: value for key, value in tree.items() if key != path}
    assert hash_files(shrunk, BASE_MANIFEST).value != hash_files(tree, BASE_MANIFEST).value


@given(st.data(), NON_EMPTY_TREES, PATHS)
def test_renaming_a_file_changes_the_hash(data: st.DataObject, tree: Tree, new_path: str) -> None:
    old_path = data.draw(st.sampled_from(sorted(tree)))
    rest = {key: value for key, value in tree.items() if key != old_path}
    renamed = _drop_conflicts({**rest, new_path: tree[old_path]})
    assume(
        new_path != old_path and new_path in renamed and renamed.keys() - {new_path} == rest.keys()
    )
    assert hash_files(renamed, BASE_MANIFEST).value != hash_files(tree, BASE_MANIFEST).value


SEMANTIC_EDITS = st.sampled_from(
    ["title", "version", "tag", "difficulty", "verifier_sec", "network", "baseline", "command"]
)


def _apply_edit(manifest: dict[str, Any], edit: str) -> dict[str, Any]:
    edited = copy.deepcopy(manifest)
    task = edited["task"]
    if edit == "title":
        task["title"] = task["title"].strip() + "!"
    elif edit == "version":
        major, minor, patch = (int(part) for part in task["version"].split("."))
        task["version"] = f"{major}.{minor}.{patch + 1}"
    elif edit == "tag":
        task["tags"] = sorted({*task["tags"], "zz"} - ({"zz"} & set(task["tags"])))
    elif edit == "difficulty":
        task["difficulty"] = {"easy": "hard", "medium": "easy", "hard": "medium"}[
            task["difficulty"]
        ]
    elif edit == "verifier_sec":
        current = edited.get("timeouts", {}).get("verifier_sec", 300)
        edited["timeouts"] = {"verifier_sec": current + 1 if current < 3600 else current - 1}
    elif edit == "network":
        resources = edited.setdefault("resources", {})
        resources["network"] = not resources.get("network", False)
    elif edit == "baseline":
        if "baseline" in edited:
            del edited["baseline"]
        else:
            edited["baseline"] = {}
    else:
        command = edited.get("verifier", {}).get("command", ["pytest", "-q", "tests"])
        edited["verifier"] = {"command": [*command, "-x"]}
    return edited


@given(manifest_data(), SEMANTIC_EDITS, TREES)
def test_any_manifest_field_change_changes_the_hash(
    manifest: dict[str, Any], edit: str, tree: Tree
) -> None:
    before = Manifest.model_validate(manifest)
    after = Manifest.model_validate(_apply_edit(manifest, edit))
    assert hash_files(tree, after).value != hash_files(tree, before).value
