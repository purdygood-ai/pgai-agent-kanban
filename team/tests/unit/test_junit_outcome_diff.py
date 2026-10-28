"""
test_junit_outcome_diff.py
==========================
The per-testcase outcome diff verify-test-order-independence.sh runs over
its forward and reversed passes:
``pgai_agent_kanban.testing.order_independence``.

Covered behaviour:

  * junit_outcomes reads PASSED, FAILED (failure or error) and SKIPPED per
    ``<classname>::<name>``;
  * two identical reports print both counts and the no-difference line and
    return 0;
  * a changed outcome returns 1 and names the testcase with both outcomes;
  * a testcase present in only one report returns 1 and reads MISSING on
    the other side;
  * divergences are listed sorted by testcase id;
  * the command line prints the same text with the same exit status, and an
    unreadable report gives one stderr line, empty stdout and exit 1.

The command line runs as a subprocess with an environment built from an
explicit set and a throwaway HOME under tmp_path.
"""

from __future__ import annotations

import io
import pathlib
import subprocess
import sys

from pgai_agent_kanban.testing import order_independence
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_MODULE = "pgai_agent_kanban.testing.order_independence"

_NO_DIFFERENCE = "No verdict differences between forward and reversed collection order."


def _report(path: pathlib.Path, *testcases: tuple) -> pathlib.Path:
    """Write a junit-xml report of ``(classname, name, child_tag_or_None)``
    testcases to *path* and return it."""
    body = []
    for classname, name, child in testcases:
        inner = f"<{child}/>" if child else ""
        body.append(f'<testcase classname="{classname}" name="{name}">{inner}</testcase>')
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest">'
        + "".join(body)
        + "</testsuite></testsuites>",
        encoding="utf-8",
    )
    return path


_BASE_CASES = (
    ("tests.unit.test_a", "test_passes", None),
    ("tests.unit.test_a", "test_fails", "failure"),
    ("tests.unit.test_b", "test_errors", "error"),
    ("tests.unit.test_b", "test_skips", "skipped"),
)


def _diff(forward: pathlib.Path, reversed_: pathlib.Path) -> tuple:
    out = io.StringIO()
    rc = order_independence.diff_junit_outcomes(str(forward), str(reversed_), out=out)
    return rc, out.getvalue()


def _run_cli(tmp_path: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    env = build_scrubbed_env(home_dir=tmp_path / "home", extra_env={"PYTHONPATH": str(_TEAM_DIR)})
    return subprocess.run(
        [sys.executable, "-m", _MODULE, *args],
        capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=60, check=False,
    )


def test_outcomes_read_passed_failed_and_skipped_per_testcase(tmp_path: pathlib.Path) -> None:
    report = _report(tmp_path / "r.xml", *_BASE_CASES)

    assert order_independence.junit_outcomes(str(report)) == {
        "tests.unit.test_a::test_passes": "PASSED",
        "tests.unit.test_a::test_fails": "FAILED",
        "tests.unit.test_b::test_errors": "FAILED",
        "tests.unit.test_b::test_skips": "SKIPPED",
    }


def test_identical_reports_return_zero_and_say_so(tmp_path: pathlib.Path) -> None:
    forward = _report(tmp_path / "f.xml", *_BASE_CASES)
    reversed_ = _report(tmp_path / "r.xml", *reversed(_BASE_CASES))

    rc, text = _diff(forward, reversed_)

    assert rc == 0
    assert text == (
        "Forward order:  4 tests collected\n"
        "Reversed order: 4 tests collected\n"
        "\n"
        f"{_NO_DIFFERENCE}\n"
    )


def test_changed_outcome_returns_one_and_names_the_testcase(tmp_path: pathlib.Path) -> None:
    forward = _report(tmp_path / "f.xml", *_BASE_CASES)
    changed = list(_BASE_CASES)
    changed[0] = ("tests.unit.test_a", "test_passes", "failure")
    reversed_ = _report(tmp_path / "r.xml", *changed)

    rc, text = _diff(forward, reversed_)

    assert rc == 1
    assert text == (
        "Forward order:  4 tests collected\n"
        "Reversed order: 4 tests collected\n"
        "\n"
        "ORDER-COUPLING FINDINGS (1):\n"
        "  tests.unit.test_a::test_passes: forward=PASSED reversed=FAILED\n"
    )


def test_testcase_in_only_one_report_reads_missing_on_the_other(tmp_path: pathlib.Path) -> None:
    forward = _report(tmp_path / "f.xml", *_BASE_CASES, ("tests.unit.test_c", "test_forward_only", None))
    reversed_ = _report(tmp_path / "r.xml", *_BASE_CASES, ("tests.unit.test_c", "test_reverse_only", "skipped"))

    rc, text = _diff(forward, reversed_)

    assert rc == 1
    assert "Forward order:  5 tests collected\nReversed order: 5 tests collected\n" in text
    assert "ORDER-COUPLING FINDINGS (2):\n" in text
    assert "  tests.unit.test_c::test_forward_only: forward=PASSED reversed=MISSING\n" in text
    assert "  tests.unit.test_c::test_reverse_only: forward=MISSING reversed=SKIPPED\n" in text
    assert _NO_DIFFERENCE not in text


def test_differences_are_sorted_by_testcase_id() -> None:
    forward = {"b::z": "PASSED", "a::y": "PASSED", "c::x": "SKIPPED"}
    reversed_ = {"b::z": "FAILED", "a::y": "FAILED", "c::x": "SKIPPED"}

    assert order_independence.outcome_differences(forward, reversed_) == [
        ("a::y", "PASSED", "FAILED"),
        ("b::z", "PASSED", "FAILED"),
    ]


def test_command_line_prints_the_diff_and_exits_with_its_status(tmp_path: pathlib.Path) -> None:
    forward = _report(tmp_path / "f.xml", *_BASE_CASES)
    changed = list(_BASE_CASES)
    changed[3] = ("tests.unit.test_b", "test_skips", None)
    reversed_ = _report(tmp_path / "r.xml", *changed)

    same = _run_cli(tmp_path, str(forward), str(forward))
    differs = _run_cli(tmp_path, str(forward), str(reversed_))

    assert same.returncode == 0, same.stderr
    assert same.stdout == _diff(forward, forward)[1]
    assert differs.returncode == 1, differs.stderr
    assert differs.stdout == _diff(forward, reversed_)[1]
    assert "  tests.unit.test_b::test_skips: forward=SKIPPED reversed=PASSED\n" in differs.stdout


def test_command_line_reports_an_unreadable_report_on_stderr(tmp_path: pathlib.Path) -> None:
    forward = _report(tmp_path / "f.xml", *_BASE_CASES)
    broken = tmp_path / "broken.xml"
    broken.write_text("<testsuites><testcase", encoding="utf-8")

    for other in (tmp_path / "absent.xml", broken):
        result = _run_cli(tmp_path, str(forward), str(other))

        assert result.returncode == 1
        assert result.stdout == ""
        assert "Traceback" not in result.stderr
        assert result.stderr.startswith("order_independence: cannot read junit-xml report:")
