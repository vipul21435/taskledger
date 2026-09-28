"""Near-duplicate detection: shingles, MinHash, LSH and the paraphrase corpus."""

from __future__ import annotations

import json
import math
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from paraphrase_corpus import TASKS, disguise
from taskledger.bundle import load_bundle
from taskledger.neardup import (
    DEFAULT_THRESHOLD,
    LSHIndex,
    MinHasher,
    NearDupIndex,
    code_shingles,
    code_tokens,
    collision_probability,
    estimate,
    fingerprint_bundle,
    fingerprint_texts,
    jaccard,
    optimal_params,
    word_shingles,
)
from taskledger.neardup.minhash import EMPTY
from taskledger.neardup.shingles import ngrams

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "bundles"


# -- shingles -----------------------------------------------------------------


def test_word_shingles_ignore_case_and_markdown() -> None:
    assert word_shingles("# Sort the **Rows**, then print!") == {
        "sort the rows",
        "the rows then",
        "rows then print",
    }
    assert word_shingles("two words") == {"two words"}
    assert word_shingles("   ") == set()


def test_ngrams_rejects_a_zero_size() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        ngrams(["a"], 0)


def test_code_tokens_ignore_names_comments_docstrings_and_layout() -> None:
    first = 'def area(width, height):\n    """Area."""\n    return width * height  # product\n'
    second = "def f(a,\n      b):\n\n    return a * b\n"
    assert code_tokens(first) == code_tokens(second)
    expected = ["def", "ID0", "(", "ID1", ",", "ID2", ")", ":", "return", "ID1", "*", "ID2"]
    assert code_tokens(first) == expected


def test_code_tokens_keep_builtins_and_collapse_strings() -> None:
    assert code_tokens("print(len('abc'), f'{x}!', 0X1F)\n") == [
        "print",
        "(",
        "len",
        "(",
        "STR",
        ")",
        ",",
        "STR",
        "{",
        "ID0",
        "}",
        ",",
        "0x1f",
        ")",
    ]


def test_underscore_is_renamed_like_any_identifier() -> None:
    assert code_tokens("_ = 1\n") == code_tokens("unused = 1\n")


def test_non_python_sources_use_the_fallback_tokenizer() -> None:
    # The unclosed parenthesis makes Python's tokenizer give up at EOF.
    js = 'function add(a, b) {\n  // sum\n  return (a + b; # "x"\n}\n'
    renamed = "function plus(x, y) {\n  return (x + y;\n}\n"
    assert code_tokens(js) == code_tokens(renamed)
    assert "//" not in code_tokens(js)
    assert code_tokens('let s = "a\\"b"; if (s) {')[3] == "STR"


def test_code_shingles_do_not_depend_on_file_order() -> None:
    a, b = "x = 1\ny = x + 2\n", "def g(n):\n    return n\n"
    assert code_shingles([a, b]) == code_shingles([b, a])


# -- MinHash ------------------------------------------------------------------


def test_signatures_are_deterministic_across_processes() -> None:
    shingles = sorted(word_shingles(TASKS[0].instruction))
    here = MinHasher(16, seed=7).signature(shingles)
    script = (
        "import json, sys\n"
        "from taskledger.neardup import MinHasher\n"
        "print(json.dumps(MinHasher(16, seed=7).signature(json.loads(sys.argv[1]))))\n"
    )
    for hash_seed in ("0", "12345"):
        env = {**os.environ, "PYTHONHASHSEED": hash_seed}
        out = subprocess.run(
            [sys.executable, "-c", script, json.dumps(shingles)],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert tuple(json.loads(out)) == here


def test_seed_changes_the_hash_family() -> None:
    shingles = {"a b c", "b c d"}
    assert MinHasher(8, seed=1).signature(shingles) != MinHasher(8, seed=2).signature(shingles)


def test_empty_set_signature_and_validation() -> None:
    hasher = MinHasher(4)
    assert hasher.signature([]) == (EMPTY,) * 4
    with pytest.raises(ValueError, match="num_perm"):
        MinHasher(0)
    with pytest.raises(ValueError, match="differ in length"):
        estimate((1, 2), (1,))
    with pytest.raises(ValueError, match="empty"):
        estimate((), ())


def test_exact_jaccard() -> None:
    assert jaccard(set(), set()) == 1.0
    assert jaccard({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)


@pytest.mark.parametrize("target", [0.1, 0.3, 0.5, 0.7, 0.9])
def test_estimate_stays_within_four_standard_errors(target: float) -> None:
    num_perm = 256
    rng = random.Random(int(target * 100))  # noqa: S311  (reproducible fixture)
    shared = round(1000 * target)
    common = {f"c{i}" for i in range(shared)}
    a = common | {f"a{i}" for i in range((1000 - shared) // 2)}
    b = common | {f"b{i}" for i in range(1000 - shared - (1000 - shared) // 2)}
    true = jaccard(a, b)
    bound = 4 * math.sqrt(true * (1 - true) / num_perm) + 1e-9
    for seed in (rng.randrange(1 << 30) for _ in range(3)):
        hasher = MinHasher(num_perm, seed)
        assert abs(estimate(hasher.signature(a), hasher.signature(b)) - true) <= bound


# -- LSH ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("threshold", "expected"),
    [(0.3, (37, 3)), (0.5, (25, 5)), (0.7, (14, 9)), (0.9, (5, 25))],
)
def test_optimal_params(threshold: float, expected: tuple[int, int]) -> None:
    bands, rows = optimal_params(threshold, 128)
    assert (bands, rows) == expected
    assert bands * rows <= 128
    # The S-curve rises through the threshold: unlikely well below it, likely well above.
    assert collision_probability(threshold - 0.2, bands, rows) < 0.35
    assert collision_probability(min(threshold + 0.2, 1.0), bands, rows) > 0.9


def test_optimal_params_validation() -> None:
    with pytest.raises(ValueError, match="between 0 and 1"):
        optimal_params(1.0, 128)
    with pytest.raises(ValueError, match="num_perm"):
        optimal_params(0.5, 0)


def test_lsh_index_insert_query_and_errors() -> None:
    index = LSHIndex(0.5, 128)
    hasher = MinHasher(128)
    base = {f"s{i}" for i in range(100)}
    index.insert("base", hasher.signature(base))
    assert len(index) == 1
    assert index.query(hasher.signature(base | {"extra"})) == {"base"}
    assert index.query(hasher.signature({f"t{i}" for i in range(100)})) == set()
    with pytest.raises(KeyError, match="already"):
        index.insert("base", hasher.signature(base))
    with pytest.raises(ValueError, match="needs"):
        index.band_keys((1, 2, 3))


# -- paraphrase corpus ---------------------------------------------------------


def _extended(solution: str) -> str:
    return disguise(solution) + "\n\ndef main():\n    import sys\n    print(sys.argv[1:])\n"


def test_paraphrase_corpus_precision_and_recall() -> None:
    index = NearDupIndex(threshold=DEFAULT_THRESHOLD)
    for task in TASKS:
        index.add(task.key, fingerprint_texts(task.instruction, [task.solution], index.hasher))
    true_positive = false_positive = false_negative = 0
    for task in TASKS:
        for solution in (disguise(task.solution), _extended(task.solution)):
            query = fingerprint_texts(task.paraphrase, [solution], index.hasher)
            found = {match.key for match in index.find(query)}
            true_positive += task.key in found
            false_negative += task.key not in found
            false_positive += len(found - {task.key})
    precision = true_positive / (true_positive + false_positive)
    recall = true_positive / (true_positive + false_negative)
    assert (true_positive, false_positive, false_negative) == (2 * len(TASKS), 0, 0)
    assert (precision, recall) == (1.0, 1.0)


def test_distinct_tasks_stay_far_apart() -> None:
    worst_instruction = worst_solution = 0.0
    for i, a in enumerate(TASKS):
        for b in TASKS[i + 1 :]:
            worst_instruction = max(
                worst_instruction,
                jaccard(word_shingles(a.instruction), word_shingles(b.paraphrase)),
            )
            worst_solution = max(
                worst_solution,
                jaccard(code_shingles([a.solution]), code_shingles([disguise(b.solution)])),
            )
    assert worst_instruction < 0.1
    assert worst_solution < 0.1


def test_matches_report_both_similarities_most_similar_first() -> None:
    index = NearDupIndex()
    task = TASKS[2]
    index.add("original", fingerprint_texts(task.instruction, [task.solution], index.hasher))
    index.add("copy", fingerprint_texts(task.instruction, [_extended(task.solution)], index.hasher))
    assert len(index) == 2
    matches = index.find(fingerprint_texts(task.instruction, [task.solution], index.hasher))
    assert [m.key for m in matches] == ["original", "copy"]
    assert matches[0].instruction_similarity == matches[0].solution_similarity == 1.0
    assert matches[1].instruction_similarity == 1.0
    assert matches[1].solution_similarity < 1.0
    assert matches[1].similarity == 1.0


def test_bundles_fingerprint_their_instruction_and_solution(tmp_path: Path) -> None:
    hasher = MinHasher()
    bundles = sorted(p for p in EXAMPLES.iterdir() if (p / "task.toml").is_file())
    assert len(bundles) >= 2
    index = NearDupIndex()
    for path in bundles:
        index.add(path.name, fingerprint_bundle(load_bundle(path).unwrap(), hasher))
    copy = tmp_path / "copy"
    shutil.copytree(bundles[0], copy)
    solve = copy / "solution" / "solve.py"
    solve.write_text(disguise(solve.read_text(encoding="utf-8")), encoding="utf-8")
    (copy / "solution" / "blob.bin").write_bytes(b"\xff\xfe\x00binary")
    (copy / "solution" / "huge.txt").write_text("x = 1\n" * 50_000, encoding="utf-8")
    matches = index.find(fingerprint_bundle(load_bundle(copy).unwrap(), hasher))
    assert [m.key for m in matches] == [bundles[0].name]
