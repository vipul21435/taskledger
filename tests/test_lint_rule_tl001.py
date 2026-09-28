"""TL001 missing-tests."""

from __future__ import annotations

import pytest

from conftest import MINIMAL_MANIFEST, BundleFactory, lint_findings, where
from taskledger.lint.rules.grader_tests import is_pytest_file, runs_pytest


def messages(factory: BundleFactory, files: dict[str, str | bytes | None], **kw: str) -> list[str]:
    return [f.message for f in lint_findings(factory, "TL001", files, **kw)]


def test_real_tests_pass(make_bundle: BundleFactory) -> None:
    assert messages(make_bundle, {}) == []


def test_missing_test_directory(make_bundle: BundleFactory) -> None:
    findings = lint_findings(make_bundle, "TL001", {"tests/test_answer.py": None})
    assert [(f.file, f.message) for f in findings] == [
        ("tests", "grader test directory tests/ does not exist")
    ]


def test_directory_with_only_empty_files(make_bundle: BundleFactory) -> None:
    files = {"tests/test_answer.py": "", "tests/conftest.py": "\n  \n"}
    assert messages(make_bundle, files) == ["grader test directory tests/ has no non-empty files"]


def test_pytest_verifier_needs_test_files(make_bundle: BundleFactory) -> None:
    files = {"tests/test_answer.py": None, "tests/check.py": "assert True\n"}
    assert messages(make_bundle, files) == [
        "the verifier runs pytest but tests/ has no test_*.py or *_test.py files"
    ]


def test_non_pytest_verifier_only_needs_content(make_bundle: BundleFactory) -> None:
    manifest = MINIMAL_MANIFEST + '\n[verifier]\ncommand = ["bash", "tests/check.sh"]\n'
    files = {"tests/test_answer.py": None, "tests/check.sh": "cmp out expected\n"}
    assert messages(make_bundle, files, manifest=manifest) == []


def test_binary_file_counts_as_content(make_bundle: BundleFactory) -> None:
    manifest = MINIMAL_MANIFEST + '\n[verifier]\ncommand = ["./tests/grade"]\n'
    files = {"tests/test_answer.py": None, "tests/grade": b"\x7fELF\x00\x01"}
    assert messages(make_bundle, files, manifest=manifest) == []


def test_file_without_test_functions(make_bundle: BundleFactory) -> None:
    files = {"tests/test_helpers.py": "def helper() -> int:\n    return 1\n"}
    findings = lint_findings(make_bundle, "TL001", files)
    assert [(f.file, f.line, f.message) for f in findings] == [
        ("tests/test_helpers.py", 1, "grader test file defines no test functions")
    ]


def test_vacuous_tests_are_reported_at_their_def(make_bundle: BundleFactory) -> None:
    source = (
        "import pytest\n"
        "\n"
        "def test_real() -> None:\n"
        "    assert 2 + 2 == 4\n"
        "\n"
        "def test_pass() -> None:\n"
        "    pass\n"
        "\n"
        "def test_ellipsis() -> None:\n"
        '    """Nothing yet."""\n'
        "    ...\n"
        "\n"
        "class TestGroup:\n"
        "    def test_constant(self) -> None:\n"
        "        assert True\n"
        "\n"
        "    async def test_falsy(self) -> None:\n"
        "        assert 0, 'always fails, so it does check something'\n"
    )
    findings = lint_findings(make_bundle, "TL001", {"tests/test_answer.py": source})
    assert where(findings) == [
        ("tests/test_answer.py", 6, 1),
        ("tests/test_answer.py", 9, 1),
        ("tests/test_answer.py", 14, 5),
    ]
    assert findings[0].message == (
        "test 'test_pass' checks nothing (only pass, ..., docstrings or a constant assert)"
    )
    assert findings[0].end_line == 7


def test_syntax_errors_are_reported_where_they_are(make_bundle: BundleFactory) -> None:
    findings = lint_findings(make_bundle, "TL001", {"tests/test_answer.py": "x = 1\ndef test(:\n"})
    assert len(findings) == 1
    assert (findings[0].line, findings[0].column) == (2, 10)
    assert findings[0].message.startswith("grader test file does not parse: ")


def test_custom_test_directory_and_binary_test_file(make_bundle: BundleFactory) -> None:
    manifest = MINIMAL_MANIFEST + '\n[tests]\npath = "grader"\n'
    files = {
        "tests/test_answer.py": None,
        "grader/test_ok.py": "def test_ok() -> None:\n    assert [1] == [1]\n",
        "grader/test_blob.py": b"\x00binary",
    }
    assert messages(make_bundle, files, manifest=manifest) == []


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (["pytest", "-q", "tests"], True),
        (["python", "-m", "pytest"], True),
        (["/usr/local/bin/pytest"], True),
        (["py.test"], True),
        (["bash", "-c", "cd /grader && pytest -q"], True),
        (["bash", "tests/run.sh"], False),
        (["pytest-runner"], False),
        (["python", "grade_pytest.py"], False),
    ],
)
def test_runs_pytest(command: list[str], expected: bool) -> None:
    assert runs_pytest(command) is expected


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("tests/test_x.py", True),
        ("tests/x_test.py", True),
        ("tests/conftest.py", False),
        ("tests/test_x.txt", False),
        ("tests/testing.py", False),
    ],
)
def test_is_pytest_file(path: str, expected: bool) -> None:
    assert is_pytest_file(path) is expected
