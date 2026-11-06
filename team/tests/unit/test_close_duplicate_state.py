"""
test_close_duplicate_state.py
=============================
``duplicate`` as a third closed state, and close.sh on a needs-operator item:

  - ``python3 -m pgai_agent_kanban.lib.terminal_states closed-map`` prints a
    ``duplicate duplicate`` line beside ``done done`` and ``wont-do wont-do``,
    and ``is_terminal('duplicate')`` is True; ``needs-operator`` is not
    terminal.
  - close.sh on a needs-operator bug: ``--state duplicate`` with a ``--note``
    naming a surviving bug closes it to ``duplicate`` and records the
    pointer in the bug file; ``--state wont-do`` closes it to ``wont-do``.
  - ``--state duplicate`` with no pointer -- no ``--note``, or one naming no
    other existing item -- is refused dual-channel with exit 1 and nothing
    written.
  - ``close.sh --help`` lists duplicate.

close.sh runs as a subprocess against a sandbox kanban root under pytest's
``tmp_path`` with every root variable pointed at it and the pre-resolved
install paths absent. Bug IDs are in the fictitious BUG-9xxx range.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

import pytest

from pgai_agent_kanban.lib import terminal_states
from pgai_agent_kanban.lib.error_contract import parse_error_contract
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent  # team/tests/unit/ -> team/
_SCRIPT = _TEAM_DIR / "scripts" / "close.sh"
_PROJECT = "demo-proj"
_DUPLICATE = "BUG-9201-widget-crash-again"
_SURVIVOR = "BUG-9202-widget-crash"


@pytest.fixture
def root(tmp_path: pathlib.Path) -> pathlib.Path:
    """A sandbox kanban root: one project, a needs-operator bug and the bug it duplicates."""
    sandbox = tmp_path / "root"
    project_root = sandbox / "projects" / _PROJECT
    (project_root / "bugs").mkdir(parents=True)
    (project_root / "tasks" / "queues").mkdir(parents=True)
    (sandbox / "projects.cfg").write_text(f"[project:{_PROJECT}]\npriority = 1\nenabled = true\n", encoding="utf-8")
    _bug(sandbox, _DUPLICATE, "needs-operator")
    _bug(sandbox, _SURVIVOR, "open")
    (project_root / "tasks" / "queues" / "bug_backlog.md").write_text(
        f"- [ ] {_DUPLICATE}\n- [ ] {_SURVIVOR}\n", encoding="utf-8"
    )
    return sandbox


def _bug(root: pathlib.Path, stem: str, status: str) -> pathlib.Path:
    path = root / "projects" / _PROJECT / "bugs" / f"{stem}.md"
    path.write_text(f"# {stem}\n\n## Status\n{status}\n\n## Summary\nThe widget crashes.\n", encoding="utf-8")
    return path


def _bug_file(root: pathlib.Path, stem: str = _DUPLICATE) -> pathlib.Path:
    return root / "projects" / _PROJECT / "bugs" / f"{stem}.md"


def _section(path: pathlib.Path, heading: str) -> str | None:
    match = re.search(rf"(?m)^##\s+{re.escape(heading)}\s*\n+(.*?)(?=^##\s|\Z)", path.read_text(encoding="utf-8"), re.S)
    return match.group(1).strip() if match else None


def _close(root: pathlib.Path, tmp_path: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    temp_dir = tmp_path / "framework-tmp"
    temp_dir.mkdir(exist_ok=True)
    cwd = tmp_path / "cwd"
    cwd.mkdir(exist_ok=True)
    env = build_scrubbed_env(
        home_dir=home,
        extra_path_dirs=[pathlib.Path(sys.executable).parent],
        extra_env={
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(temp_dir),
        },
    )
    return subprocess.run(
        ["bash", str(_SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd),
        timeout=60,
        stdin=subprocess.DEVNULL,
    )


def _diagnostic(result: subprocess.CompletedProcess) -> str:
    return f"exit {result.returncode}\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


# ---------------------------------------------------------------------------
# The vocabulary
# ---------------------------------------------------------------------------


def test_closed_map_prints_duplicate_beside_done_and_wont_do():
    result = subprocess.run(
        [sys.executable, "-m", "pgai_agent_kanban.lib.terminal_states", "closed-map"],
        cwd=_TEAM_DIR,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    for line in ("done done", "wont-do wont-do", "duplicate duplicate"):
        assert line in lines, result.stdout


@pytest.mark.parametrize(
    "state, terminal",
    [("duplicate", True), ("DUPLICATE", True), ("needs-operator", False), ("open", False), ("running", False)],
)
def test_duplicate_is_terminal_and_needs_operator_is_not(state, terminal):
    assert terminal_states.is_terminal(state) is terminal


def test_intake_vocabulary_lists_active_states_then_closed_states():
    assert terminal_states.ACTIVE_INTAKE_STATES == ("open", "running", "needs-operator")
    assert terminal_states.INTAKE_STATES == (
        *terminal_states.ACTIVE_INTAKE_STATES,
        *terminal_states.CLOSED_STATES,
    )


# ---------------------------------------------------------------------------
# close.sh on a needs-operator bug
# ---------------------------------------------------------------------------


def test_duplicate_with_a_pointer_closes_and_records_the_pointer(root, tmp_path):
    note = f"duplicate of {_SURVIVOR}"
    result = _close(root, tmp_path, "--project", _PROJECT, "--key", _DUPLICATE, "--state", "duplicate", "--note", note)

    assert result.returncode == 0, _diagnostic(result)
    assert _section(_bug_file(root), "Status") == "duplicate"
    assert _section(_bug_file(root), "Close Note") == note
    assert _section(_bug_file(root, _SURVIVOR), "Status") == "open"


def test_wont_do_closes_a_needs_operator_bug(root, tmp_path):
    result = _close(root, tmp_path, "--project", _PROJECT, "--key", _DUPLICATE, "--state", "wont-do")

    assert result.returncode == 0, _diagnostic(result)
    assert _section(_bug_file(root), "Status") == "wont-do"


@pytest.mark.parametrize(
    "note_args",
    [(), ("--note", "same crash as before"), ("--note", f"duplicate of {_DUPLICATE}"), ("--note", "see BUG-9299")],
    ids=["no-note", "no-id-in-note", "points-at-itself", "points-at-missing-bug"],
)
def test_duplicate_without_a_pointer_is_refused_and_writes_nothing(root, tmp_path, note_args):
    project_root = root / "projects" / _PROJECT
    files = sorted(p for p in project_root.rglob("*") if p.is_file())
    before = {p: p.read_bytes() for p in files}

    result = _close(root, tmp_path, "--project", _PROJECT, "--key", _DUPLICATE, "--state", "duplicate", *note_args)

    assert result.returncode == 1, _diagnostic(result)
    fields = parse_error_contract(result.stderr)
    assert fields is not None, _diagnostic(result)
    assert fields["error"] == "close.duplicate_pointer_missing"
    assert fields["message"].strip() and fields["remedy"].strip()
    assert "<" not in fields["remedy"], fields["remedy"]
    assert sorted(p for p in project_root.rglob("*") if p.is_file()) == files
    assert {p: p.read_bytes() for p in files} == before


def test_help_lists_duplicate(root, tmp_path):
    result = _close(root, tmp_path, "--help")

    assert result.returncode == 0, _diagnostic(result)
    assert "done|wont-do|duplicate" in result.stdout
    assert re.search(r"(?m)^\s+duplicate\s+—", result.stdout), result.stdout
