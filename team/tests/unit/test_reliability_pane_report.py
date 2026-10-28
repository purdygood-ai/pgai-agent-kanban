"""
test_reliability_pane_report.py
===============================
Golden tests for the two surfaces that print the reliability streaks:
the rc-metrics left pane (``scripts/dashboard/metrics.sh``) and
``scripts/metrics-report.sh --reliability``. Both read only
``projects/<name>/metrics/reliability.json`` and render it through
``pgai_agent_kanban.metrics.reliability_view``.

The sandbox root registers two projects with checked-in fixture
reliability.json files (``tests/fixtures/reliability/``): ``alpha``, whose
streaks run from a human-cleared CM release HALT and a self-cleared task
block, and ``beta``, whose only ledger record is an operator HALT, so it
has no block on record. ``alpha`` also has an active RC, so the Current RC
block renders ahead of the Reliability block. ``gamma`` exists on disk,
unregistered and without a reliability.json, for the CLI's missing-file
cases.

Covered behaviour:

  * metrics.sh, run at 70 columns, prints the checked-in golden pane: the
    ``=== Reliability ===`` block after Current RC and before the footer,
    two lines per project, ``no block on record`` for beta, and no line
    longer than 70 columns;
  * ``metrics-report.sh --reliability`` cases (one project, every
    project, ``--format json``, and the refusals) match the checked-in
    golden outcomes, and ``--format json`` prints the fixture file as
    stored;
  * the lines the CLI prints for one project are the lines the pane prints
    for it;
  * the pane exits 0 and prints a placeholder line when reliability.json is
    missing or malformed, with the Today and Current RC blocks unchanged.

Every tree is built under pytest's tmp_path; subprocesses run with an
environment built from an explicit set and a throwaway HOME. Tree paths,
the build day and the pane's "Updated:" time are replaced by placeholders
so the outcome is stable. To re-record after a deliberate change, run
this file directly with ``--record`` from team/:
``python3 tests/unit/test_reliability_pane_report.py --record``.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Optional

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from pgai_agent_kanban.lib.temp_root import resolve_temp_root  # noqa: E402
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env  # noqa: E402

_METRICS_SH = _TEAM_DIR / "scripts" / "dashboard" / "metrics.sh"
_METRICS_REPORT_SH = _TEAM_DIR / "scripts" / "metrics-report.sh"
_FIXTURES = _TEAM_DIR / "tests" / "fixtures" / "reliability"
_GOLDEN_DIR = _TEAM_DIR / "tests" / "fixtures" / "golden"
PANE_GOLDEN = _GOLDEN_DIR / "metrics_pane_reliability.txt"
CLI_GOLDEN = _GOLDEN_DIR / "metrics_report_reliability_cli.json"

PANE_WIDTH = 70

#: The render time every run is pinned to: the fixture documents'
#: computed_at, so each streak's hours read as stored.
RENDER_AT = "2026-10-18T12:00:00+00:00"

#: label -> metrics-report.sh arguments ("{ROOT}" is the sandbox root).
CLI_CASES: dict[str, tuple[str, ...]] = {
    "one project with blocks": ("--reliability", "--project", "alpha"),
    "one project without a block": ("--reliability", "--project", "beta"),
    "every registered project": ("--reliability",),
    "project without reliability.json": ("--reliability", "--project", "gamma"),
    "json": ("--reliability", "--format", "json", "--project", "alpha"),
    "json without reliability.json": ("--reliability", "--format", "json", "--project", "gamma"),
    "json without a project": ("--reliability", "--format", "json"),
    "unsupported format": ("--reliability", "--format", "jsonl", "--project", "alpha"),
    "combined with csv": ("--reliability", "--csv", "--project", "alpha"),
    "unknown project": ("--reliability", "--project", "nope"),
}


def _diagnostic(result: subprocess.CompletedProcess) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def build_root(base: pathlib.Path) -> pathlib.Path:
    """Lay out the sandbox kanban root described in the module docstring
    under *base*; returns the root."""
    root = base / "root"
    (root / "tmp").mkdir(parents=True)
    (root / "projects.cfg").write_text(
        "[project:alpha]\npriority = 1\n\n[project:beta]\npriority = 2\n", encoding="utf-8"
    )
    for name in ("alpha", "beta"):
        metrics = root / "projects" / name / "metrics"
        metrics.mkdir(parents=True)
        shutil.copyfile(_FIXTURES / f"{name}.json", metrics / "reliability.json")
    alpha = root / "projects" / "alpha"
    (alpha / "release-state.md").write_text(
        "## Active RC\nv2.49.0\n## RC Opened At\n2026-10-18T09:00:00Z\n## RC Opened By Task\nnone\n",
        encoding="utf-8",
    )
    (alpha / "metrics" / "rc").mkdir()
    (alpha / "metrics" / "rc" / "v2.49.0.json").write_text(
        json.dumps({"rc": "v2.49.0", "wall_time_minutes": 95, "blocked_minutes": 12,
                    "tasks": {"total": 6}, "tokens": {"total": {"input": 2_500, "output": 500}}}),
        encoding="utf-8",
    )
    (root / "projects" / "gamma").mkdir(parents=True)
    return root


def _env(root: pathlib.Path, home: pathlib.Path) -> dict:
    return build_scrubbed_env(
        home_dir=home,
        extra_path_dirs=[pathlib.Path(sys.executable).parent],
        extra_env={
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
            "COLUMNS": str(PANE_WIDTH),
        },
    )


def run_pane(root: pathlib.Path, home: pathlib.Path) -> subprocess.CompletedProcess:
    """metrics.sh over *root*, colour off, at the pane's 70 columns, rendered
    as of RENDER_AT."""
    return subprocess.run(
        ["bash", str(_METRICS_SH), "--kanban-root", str(root), "--no-color", "--now", RENDER_AT],
        capture_output=True, text=True, env=_env(root, home), cwd=str(root),
        timeout=120, check=False,
    )


def run_report(root: pathlib.Path, home: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    """metrics-report.sh over *root* with *args*, rendered as of RENDER_AT."""
    return subprocess.run(
        ["bash", str(_METRICS_REPORT_SH), "--kanban-root", str(root), *args, "--now", RENDER_AT],
        capture_output=True, text=True, env=_env(root, home), cwd=str(root),
        timeout=120, check=False,
    )


def normalise_pane(stdout: str) -> str:
    """The pane's stdout with the build day and the Updated: time as
    placeholders."""
    text = re.sub(r"^=== Today \(\d{4}-\d{2}-\d{2}\) ===$", "=== Today ({TODAY}) ===", stdout,
                  count=1, flags=re.M)
    return re.sub(r"^Updated: .*$", "Updated: {UPDATED}", text, count=1, flags=re.M)


def cli_outcome(root: pathlib.Path, result: subprocess.CompletedProcess) -> dict:
    """One CLI run as a golden outcome, with the root as ``{ROOT}``."""
    return {
        "exit_code": result.returncode,
        "stdout": result.stdout.replace(str(root), "{ROOT}"),
        "stderr": result.stderr.replace(str(root), "{ROOT}"),
    }


def _entry(lines: list[str], project: str) -> list[str]:
    """The project's name line and the two lines under it."""
    start = lines.index(f"  {project}")
    return lines[start:start + 3]


@pytest.fixture
def root(tmp_path: pathlib.Path) -> pathlib.Path:
    return build_root(tmp_path)


@pytest.fixture
def home(tmp_path: pathlib.Path) -> pathlib.Path:
    path = tmp_path / "home"
    path.mkdir()
    return path


# ---------------------------------------------------------------------------
# The pane
# ---------------------------------------------------------------------------


def test_pane_at_70_columns_matches_the_golden(root: pathlib.Path, home: pathlib.Path) -> None:
    result = run_pane(root, home)
    assert result.returncode == 0, _diagnostic(result)
    assert normalise_pane(result.stdout) == PANE_GOLDEN.read_text(encoding="utf-8"), _diagnostic(result)


def test_golden_pane_has_the_reliability_block_after_current_rc_within_70_columns() -> None:
    lines = PANE_GOLDEN.read_text(encoding="utf-8").splitlines()
    current_rc = lines.index("=== Current RC ===")
    reliability = lines.index("=== Reliability ===")
    footer = lines.index("Updated: {UPDATED}")
    assert current_rc < reliability < footer
    block = lines[reliability:footer]
    alpha, beta = _entry(block, "alpha"), _entry(block, "beta")
    assert [line.split()[0] for line in alpha[1:]] == ["Hands-off:", "Clean:"]
    assert [line.split()[0] for line in beta[1:]] == ["Hands-off:", "Clean:"]
    assert all("(no block on record)" in line for line in beta[1:])
    assert not any("no block on record" in line for line in alpha)
    assert block[2:] == alpha + [""] + beta + [""]
    assert max(len(line) for line in lines) <= PANE_WIDTH


@pytest.mark.parametrize("content", [None, "{not json", "[]", '{"hands_off": {}, "clean": {}}'])
def test_missing_or_malformed_file_renders_a_placeholder_and_exits_0(
    root: pathlib.Path, home: pathlib.Path, content: Optional[str]
) -> None:
    target = root / "projects" / "beta" / "metrics" / "reliability.json"
    if content is None:
        target.unlink()
        placeholder = "    (no reliability data yet)"
    else:
        target.write_text(content, encoding="utf-8")
        placeholder = "    (reliability.json unreadable)"
    result = run_pane(root, home)
    assert result.returncode == 0, _diagnostic(result)
    lines = normalise_pane(result.stdout).splitlines()
    golden = PANE_GOLDEN.read_text(encoding="utf-8").splitlines()
    assert lines[lines.index("  beta", lines.index("=== Reliability ===")) + 1] == placeholder
    # Everything ahead of the Reliability block is unchanged.
    cut = golden.index("=== Reliability ===")
    assert lines[:cut] == golden[:cut], _diagnostic(result)


# ---------------------------------------------------------------------------
# metrics-report.sh --reliability
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label", sorted(CLI_CASES))
def test_cli_case_matches_the_golden(root: pathlib.Path, home: pathlib.Path, label: str) -> None:
    result = run_report(root, home, *CLI_CASES[label])
    expected = json.loads(CLI_GOLDEN.read_text(encoding="utf-8"))[label]
    assert cli_outcome(root, result) == expected, _diagnostic(result)


def test_every_cli_case_is_recorded() -> None:
    assert set(json.loads(CLI_GOLDEN.read_text(encoding="utf-8"))) == set(CLI_CASES)


@pytest.mark.parametrize("project", ["alpha", "beta"])
def test_cli_prints_the_lines_the_pane_prints_for_the_project(
    root: pathlib.Path, home: pathlib.Path, project: str
) -> None:
    pane = run_pane(root, home)
    report = run_report(root, home, "--reliability", "--project", project)
    assert (pane.returncode, report.returncode) == (0, 0), _diagnostic(pane) + _diagnostic(report)
    pane_lines = pane.stdout.splitlines()
    pane_entry = _entry(pane_lines[pane_lines.index("=== Reliability ==="):], project)
    assert report.stdout.splitlines() == pane_entry, _diagnostic(report)


def test_format_json_prints_the_fixture_file_as_stored(root: pathlib.Path, home: pathlib.Path) -> None:
    result = run_report(root, home, "--reliability", "--format", "json", "--project", "alpha")
    assert result.returncode == 0, _diagnostic(result)
    assert result.stdout == (_FIXTURES / "alpha.json").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


def record() -> None:
    """Rewrite both goldens from the current scripts."""
    scratch_root = pathlib.Path(resolve_temp_root())
    scratch_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="reliability-golden-", dir=scratch_root) as scratch:
        base = pathlib.Path(scratch)
        sandbox = build_root(base)
        home_dir = base / "home"
        home_dir.mkdir()
        pane = run_pane(sandbox, home_dir)
        PANE_GOLDEN.write_text(normalise_pane(pane.stdout), encoding="utf-8")
        outcomes = {
            label: cli_outcome(sandbox, run_report(sandbox, home_dir, *args))
            for label, args in sorted(CLI_CASES.items())
        }
        CLI_GOLDEN.write_text(json.dumps(outcomes, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    if sys.argv[1:] != ["--record"]:
        print("usage: python3 tests/unit/test_reliability_pane_report.py --record", file=sys.stderr)
        sys.exit(2)
    record()
