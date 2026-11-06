"""
test_metrics_pane_bug_split.py
==============================
Golden and behaviour tests for the Metrics pane's Bugs section
(``scripts/dashboard/metrics.sh``, rendered by
``pgai_agent_kanban.lib.metrics.metrics_pane``): one line per project
splitting its bug count into open, deferred and needs-operator, counted by
``dashboard.status_line.bug_status_split`` under the deferral posture
``lib.defer_posture.resolve_defer_posture`` puts in force.

The sandbox root registers three projects. ``alpha`` defers at ``medium``
(``project.cfg [release]``) and holds 2 open bugs above that level (high,
critical), 3 open bugs at or below it (low, medium, and ``low.`` with a
trailing period), 1 needs-operator bug, and a running and a done bug that
the split does not count. ``beta`` holds only a done bug; ``gamma`` has no
bugs directory.

Covered behaviour:

  * metrics.sh prints the checked-in golden pane: alpha's split 2 / 3 / 1
    in a Bugs section after Reliability, ahead of the footer; beta and gamma
    have no line;
  * with deferral off the deferred count is 0 and those bugs count as open;
    a DEFER-LOW-BUGS campaign file defers at ``low``; a posture that refuses
    to resolve defers nothing and says so;
  * the deferred count is the status line's deferred-bug count for the same
    level;
  * with no bug to count anywhere the section is omitted, and the colour
    escapes wrap the header and project name.

Every tree is built under pytest's tmp_path; metrics.sh runs with an
environment built from an explicit set (``build_scrubbed_env``) whose
kanban root and temp dir name the sandbox, and a throwaway HOME. Bug IDs
are in the fictitious BUG-9xxx range. To re-record the golden after a
deliberate change, run this file directly with ``--record`` from team/:
``python3 tests/unit/test_metrics_pane_bug_split.py --record``.
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys
import tempfile
from datetime import datetime, UTC

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from pgai_agent_kanban.dashboard.status_line import (  # noqa: E402
    BugStatusSplit,
    bug_status_split,
    deferred_bug_ids,
)
from pgai_agent_kanban.lib.metrics.metrics_pane import (  # noqa: E402
    POSTURE_UNREADABLE_NOTE,
    bug_line,
    metrics_pane_lines,
    project_bug_split,
)
from pgai_agent_kanban.lib.temp_root import resolve_temp_root  # noqa: E402
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env  # noqa: E402

_METRICS_SH = _TEAM_DIR / "scripts" / "dashboard" / "metrics.sh"
PANE_GOLDEN = _TEAM_DIR / "tests" / "fixtures" / "golden" / "metrics_pane_bug_split.txt"

RENDER_AT = "2026-10-18T12:00:00+00:00"
_NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
_TODAY = "2026-10-18"

RESET, BOLD, DIM, CYAN = "\033[0m", "\033[1m", "\033[2m", "\033[0;36m"

#: alpha's bugs: file stem -> (status, severity).
ALPHA_BUGS = {
    "BUG-9001-crash-on-save": ("open", "high"),
    "BUG-9002-data-loss": ("open", "critical"),
    "BUG-9003-tooltip-typo": ("open", "low"),
    "BUG-9004-slow-export": ("open", "medium"),
    "BUG-9005-wrong-icon": ("open", "low."),
    "BUG-9006-which-account": ("needs-operator", "medium"),
    "BUG-9007-being-fixed": ("running", "high"),
    "BUG-9008-fixed": ("done", "low"),
}

DEFER_AT_MEDIUM = "[release]\ndeferred_bugs = true\ndeferred_bug_level = medium\n"


def _write_bugs(bugs_dir: pathlib.Path, bugs: dict[str, tuple[str, str]]) -> None:
    bugs_dir.mkdir(parents=True, exist_ok=True)
    for stem, (status, severity) in bugs.items():
        (bugs_dir / f"{stem}.md").write_text(
            f"# {stem}\n\n## Status\n{status}\n\n**Severity:** {severity}\n", encoding="utf-8"
        )


def build_root(base: pathlib.Path) -> pathlib.Path:
    """Lay out the sandbox kanban root described in the module docstring
    under *base*; returns the root."""
    root = base / "root"
    (root / "tmp").mkdir(parents=True)
    (root / "projects.cfg").write_text(
        "[project:alpha]\npriority = 1\n\n[project:beta]\npriority = 2\n\n[project:gamma]\npriority = 3\n",
        encoding="utf-8",
    )
    for name in ("alpha", "beta", "gamma"):
        (root / "projects" / name).mkdir(parents=True)
    (root / "projects" / "alpha" / "project.cfg").write_text(DEFER_AT_MEDIUM, encoding="utf-8")
    _write_bugs(root / "projects" / "alpha" / "bugs", ALPHA_BUGS)
    _write_bugs(root / "projects" / "beta" / "bugs", {"BUG-9101-fixed": ("done", "high")})
    return root


def run_pane(root: pathlib.Path, home: pathlib.Path) -> subprocess.CompletedProcess:
    """metrics.sh over *root*, colour off, rendered as of RENDER_AT."""
    env = build_scrubbed_env(
        home_dir=home,
        extra_path_dirs=[pathlib.Path(sys.executable).parent],
        extra_env={
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
        },
    )
    return subprocess.run(
        ["bash", str(_METRICS_SH), "--kanban-root", str(root), "--no-color", "--now", RENDER_AT],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(root),
        timeout=120,
        check=False,
    )


def normalise_pane(stdout: str) -> str:
    """The pane's stdout with the build day and the Updated: time as
    placeholders."""
    text = re.sub(r"^=== Today \(\d{4}-\d{2}-\d{2}\) ===$", "=== Today ({TODAY}) ===", stdout, count=1, flags=re.M)
    return re.sub(r"^Updated: .*$", "Updated: {UPDATED}", text, count=1, flags=re.M)


def _descriptors(root: pathlib.Path, *names: str) -> str:
    return json.dumps(
        [{"name": name, "bugs_dir": str(root / "projects" / name / "bugs"), "kanban_root": str(root)} for name in names]
    )


def _bug_lines(root: pathlib.Path, *names: str, escapes: tuple[str, ...] = ()) -> list[str]:
    """The Bugs section of the pane for *names*, header through its last line."""
    lines = list(metrics_pane_lines(bool(escapes), _TODAY, _descriptors(root, *names), *escapes, now=_NOW))
    header = f"{escapes[3]}{escapes[1]}=== Bugs ==={escapes[0]}" if escapes else "=== Bugs ==="
    if header not in lines:
        return []
    start = lines.index(header)
    return lines[start:-2]  # up to the footer


def _diagnostic(result: subprocess.CompletedProcess) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


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


def test_pane_matches_the_golden(root: pathlib.Path, home: pathlib.Path) -> None:
    result = run_pane(root, home)
    assert result.returncode == 0, _diagnostic(result)
    assert normalise_pane(result.stdout) == PANE_GOLDEN.read_text(encoding="utf-8"), _diagnostic(result)


def test_golden_pane_splits_alpha_two_three_one_after_reliability() -> None:
    lines = PANE_GOLDEN.read_text(encoding="utf-8").splitlines()
    start = lines.index("=== Bugs ===")
    assert lines.index("=== Reliability ===") < start < lines.index("Updated: {UPDATED}")
    assert lines[start : start + 5] == [
        "=== Bugs ===",
        "",
        "  alpha",
        "    2 open / 3 deferred / 1 needs-operator",
        "",
    ]
    bugs_section = lines[start:]
    assert "  beta" not in bugs_section and "  gamma" not in bugs_section


# ---------------------------------------------------------------------------
# The deferral posture
# ---------------------------------------------------------------------------


def test_deferral_off_counts_no_bug_as_deferred(root: pathlib.Path) -> None:
    (root / "projects" / "alpha" / "project.cfg").write_text("[release]\ndeferred_bugs = false\n", encoding="utf-8")
    split, resolved = project_bug_split(str(root / "projects" / "alpha" / "bugs"), str(root), "alpha")
    assert (split, resolved) == (BugStatusSplit(open=5, deferred=0, needs_operator=1), True)
    assert "    5 open / 0 deferred / 1 needs-operator" in _bug_lines(root, "alpha")


def test_a_descriptor_without_a_kanban_root_reads_as_deferral_off(root: pathlib.Path) -> None:
    split, resolved = project_bug_split(str(root / "projects" / "alpha" / "bugs"), "", "alpha")
    assert (split.deferred, resolved) == (0, True)


def test_a_campaign_file_defers_at_low(root: pathlib.Path) -> None:
    (root / "projects" / "alpha" / "DEFER-LOW-BUGS").write_text("until=v9.0.0\n", encoding="utf-8")
    assert "    3 open / 2 deferred / 1 needs-operator" in _bug_lines(root, "alpha")


def test_a_posture_that_refuses_defers_nothing_and_says_so(root: pathlib.Path) -> None:
    (root / "projects" / "alpha" / "project.cfg").write_text(
        "[release]\ndeferred_bugs = true\ndeferred_bug_level = critical\n", encoding="utf-8"
    )
    split, resolved = project_bug_split(str(root / "projects" / "alpha" / "bugs"), str(root), "alpha")
    assert (split.deferred, resolved) == (0, False)
    assert f"    5 open / 0 deferred / 1 needs-operator  {POSTURE_UNREADABLE_NOTE}" in _bug_lines(root, "alpha")


@pytest.mark.parametrize("level", ["low", "medium", "high"])
def test_deferred_count_is_the_status_line_deferred_count(root: pathlib.Path, level: str) -> None:
    bugs = root / "projects" / "alpha" / "bugs"
    split = bug_status_split(bugs, level)
    assert split.deferred == len(deferred_bug_ids(bugs, level))
    assert split.open + split.deferred == 5 and split.needs_operator == 1


# ---------------------------------------------------------------------------
# Section shape
# ---------------------------------------------------------------------------


def test_no_bug_to_count_omits_the_section(root: pathlib.Path) -> None:
    assert _bug_lines(root, "beta", "gamma") == []
    assert bug_status_split(root / "projects" / "gamma" / "bugs") == BugStatusSplit()


def test_colour_wraps_the_header_and_project_name(root: pathlib.Path) -> None:
    lines = _bug_lines(root, "alpha", escapes=(RESET, BOLD, DIM, CYAN, "", ""))
    assert lines[:4] == [
        f"{CYAN}{BOLD}=== Bugs ==={RESET}",
        "",
        f"  {BOLD}alpha{RESET}",
        "    2 open / 3 deferred / 1 needs-operator",
    ]


def test_bug_line_formats_thousands() -> None:
    assert (
        bug_line(BugStatusSplit(open=1234, deferred=0, needs_operator=2))
        == "1,234 open / 0 deferred / 2 needs-operator"
    )


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


def record() -> None:
    """Rewrite the golden from the current scripts."""
    scratch_root = pathlib.Path(resolve_temp_root())
    scratch_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="bug-split-golden-", dir=scratch_root) as scratch:
        base = pathlib.Path(scratch)
        sandbox = build_root(base)
        home_dir = base / "home"
        home_dir.mkdir()
        PANE_GOLDEN.write_text(normalise_pane(run_pane(sandbox, home_dir).stdout), encoding="utf-8")


if __name__ == "__main__":
    if sys.argv[1:] != ["--record"]:
        print("usage: python3 tests/unit/test_metrics_pane_bug_split.py --record", file=sys.stderr)
        sys.exit(2)
    record()
