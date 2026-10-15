"""
test_dashboard_requirements_ceiling.py
======================================
The dashboard's single-project requirements column marks an open
requirement the project's [versioning] ceilings stop, and never reads a
malformed ceiling as "no ceiling".

column-render.sh's gather stage (team/scripts/lib/dashboard/
column_render_data.sh) reads the owning project's ceilings through the
pp_max_* accessors with their stderr left visible; the renderer
(pgai_agent_kanban.dashboard.column_render) appends " [ceiling]" to an open
requirement whose target exceeds a ceiling in effect, and " [ceiling?]" to
every open requirement while a ceiling is unreadable -- the project is then
ceiling-blocked and discovery queues none of them. The mark sits after the
row's colour, so the colour stays the board status GET /board reports.

Covered, end to end through column-render.sh on a sandbox root (fixture
project, last released v0.5.0, an open v1.0.0 and an open v0.6.0):
  - max_major = O: v1.0.0 is marked unreadable, not drawn as free to go,
    and the accessor's narrated line is on the render's stderr;
  - max_major = none: v1.0.0 is unmarked;
  - the key absent: v1.0.0 is marked ceiling-blocked at the default 0, and
    v0.6.0 (within every default) is unmarked;
  - in colour, the v1.0.0 label keeps the open (white) colour and the mark
    follows the reset.
And, in process: the active RC and shipped requirements are never marked, an
``auto`` target is not marked by an exceeded ceiling, and the default
CeilingCards marks nothing.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from pgai_agent_kanban.dashboard.column_render.cells import (  # noqa: E402
    CEILING_BLOCKED_SUFFIX,
    CEILING_UNREADABLE_SUFFIX,
    CeilingCards,
)
from pgai_agent_kanban.lib.version_ceiling import exceeded_ceiling  # noqa: E402
from tests.fixtures.dashboard_render_fixture import clean_subprocess_env, run_dashboard_script  # noqa: E402

_COLUMN_RENDER = _TEAM_DIR / "scripts" / "dashboard" / "column-render.sh"
_WHITE = "\033[0;37m"
_RESET = "\033[0m"


def _write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _sandbox(tmp_path: pathlib.Path, versioning_line: str) -> pathlib.Path:
    """A kanban root with one project 'demo' whose [versioning] holds *versioning_line*."""
    root = tmp_path / "root"
    (root / "tmp").mkdir(parents=True)
    _write(root / "projects.cfg", "[project:demo]\npriority=1\nenabled=true\n")
    project = root / "projects" / "demo"
    (project / "tasks").mkdir(parents=True)
    _write(project / "project.cfg", f"[project]\nproject_name = demo\n\n[versioning]\n{versioning_line}\n")
    _write(
        project / "release-state.md",
        "# Release State\n\n## Active RC\nnone\n\n## RC Opened At\nnone\n\n"
        "## RC Opened By Task\nnone\n\n## Last Released\nv0.5.0\n",
    )
    for version, slug in (("v1.0.0", "major"), ("v0.6.0", "minor")):
        _write(
            project / "requirements" / f"{version}-{slug}.md",
            f"# {version}\n\n## Target Version\n{version}\n\n## Status\nopen\n",
        )
    return root


def _render(root: pathlib.Path, *, color: bool = False):
    env = clean_subprocess_env(kanban_root=root)
    env["KANBAN_ROOT"] = str(root)
    env["PGAI_AGENT_KANBAN_TEMP_DIR"] = str(root / "tmp")
    if color:
        env.pop("NO_COLOR", None)
        env["TERM"] = "xterm-256color"
    requirements = root / "projects" / "demo" / "requirements"
    return run_dashboard_script(
        ["bash", str(_COLUMN_RENDER), "input", str(requirements), str(root / "tmp" / "cache"), "10", "40",
         "--label", "REQUIREMENTS", "--kanban-root", str(root)],
        env=env,
        timeout=60,
    )


def _row(stdout: str, version: str) -> str:
    rows = [line for line in stdout.splitlines() if version in line]
    assert len(rows) == 1, f"expected one {version} row in:\n{stdout}"
    return rows[0]


# ---------------------------------------------------------------------------
# End to end through column-render.sh
# ---------------------------------------------------------------------------


def test_malformed_ceiling_marks_requirement_and_keeps_the_narrated_line(tmp_path) -> None:
    result = _render(_sandbox(tmp_path, "max_major = O"))

    assert result.returncode == 0, result.stderr
    row = _row(result.stdout, "v1.0.0")
    assert row.endswith(CEILING_UNREADABLE_SUFFIX), row
    assert row != "v1.0.0", "a malformed ceiling must not read as no ceiling"
    assert "version ceiling unreadable" in result.stderr, result.stderr
    assert "max_major = 'O'" in result.stderr, result.stderr


def test_no_ceiling_leaves_requirement_unmarked(tmp_path) -> None:
    result = _render(_sandbox(tmp_path, "max_major = none"))

    assert result.returncode == 0, result.stderr
    assert _row(result.stdout, "v1.0.0") == "v1.0.0"
    assert "version ceiling unreadable" not in result.stderr


def test_absent_key_marks_requirement_blocked_at_the_default(tmp_path) -> None:
    result = _render(_sandbox(tmp_path, ""))

    assert result.returncode == 0, result.stderr
    assert _row(result.stdout, "v1.0.0") == "v1.0.0" + CEILING_BLOCKED_SUFFIX
    assert _row(result.stdout, "v0.6.0") == "v0.6.0"


def test_mark_follows_the_board_colour(tmp_path) -> None:
    result = _render(_sandbox(tmp_path, ""), color=True)

    assert result.returncode == 0, result.stderr
    row = _row(result.stdout, "v1.0.0")
    assert f"{_WHITE}v1.0.0{_RESET}{CEILING_BLOCKED_SUFFIX}" in row, repr(row)


# ---------------------------------------------------------------------------
# In process: what is and is not marked
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("board_status", ["label", "done", "wont-do"])
def test_only_open_requirements_are_marked(board_status) -> None:
    cards = CeilingCards(limits={"max_major": 0}, unreadable=False)
    assert cards.suffix(board_status, "v1.0.0") == ""
    assert CeilingCards(unreadable=True).suffix(board_status, "v1.0.0") == ""


def test_exceeded_ceiling_needs_an_explicit_version() -> None:
    cards = CeilingCards(limits={"max_major": 0, "max_minor": 13, "max_patch": 21})
    assert cards.suffix("open", "auto") == ""
    assert cards.suffix("open", "") == ""
    assert cards.suffix("open", "v0.14.0") == CEILING_BLOCKED_SUFFIX
    assert CeilingCards(unreadable=True).suffix("open", "auto") == CEILING_UNREADABLE_SUFFIX


def test_default_ceiling_cards_mark_nothing() -> None:
    cards = CeilingCards()
    assert not cards.active
    assert cards.suffix("open", "v9.0.0") == ""


def test_exceeded_ceiling_names_the_first_violated_key() -> None:
    limits = {"max_major": 0, "max_minor": 13, "max_patch": 21}
    assert exceeded_ceiling("v1.20.30", limits) == "max_major"
    assert exceeded_ceiling("0.14.0", limits) == "max_minor"
    assert exceeded_ceiling("v0.1.22", limits) == "max_patch"
    assert exceeded_ceiling("v0.13.21", limits) is None
    assert exceeded_ceiling("v5.0.0", {"max_major": None}) is None
