"""
test_dashboard_legend.py
========================
The visibility window's one-line colour legend,
``pgai_agent_kanban.dashboard.legend.legend_line``, which
``scripts/dashboard/legend-render.sh`` prints.

Covered behaviour:

  * with colour off the line is ``DASHBOARD_LEGEND_TEMPLATE`` (read from
    team/scripts/lib/dashboard_legend.sh) with ``[abbr] name`` project tags
    and the plain status keywords substituted in;
  * with colour on each project tag is a filled square in the project's
    truecolor escape and each status keyword is wrapped in its semantic
    status colour (done and wont-do share one, needs-operator has its own)
    and carried is drawn as its row marker; with the escapes removed it
    still matches the template;
  * no projects reads "(no projects)"; a project short of a colour takes
    the palette's last-resort gray; a malformed colour draws an uncoloured
    tag; an empty template and keyword list fall back to the canonical ones;
  * the command line prints the line, and legend-render.sh, run as bash on
    a throwaway kanban root, prints the function's line with colour off and on.

Subprocesses run with an environment built from an explicit set and a
throwaway HOME.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

import pytest

from pgai_agent_kanban.dashboard.legend import (
    DEFAULT_STATUS_KEYWORDS,
    DEFAULT_TEMPLATE,
    legend_line,
)
from pgai_agent_kanban.lib.palette import load_default_palette, resolve_semantic_color
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_PP_RUN_OPS = _TEAM_DIR / "scripts" / "lib" / "pp_run_ops.sh"
_LEGEND_SH = _TEAM_DIR / "scripts" / "dashboard" / "legend-render.sh"
_LEGEND_CONSTANTS = _TEAM_DIR / "scripts" / "lib" / "dashboard_legend.sh"
_MODULE = "pgai_agent_kanban.dashboard.legend"

RESET = "\033[0m"
_ANSI = re.compile(r"\033\[[0-9;]*m")
_GLYPH = "■"


def _diagnostic(result: subprocess.CompletedProcess) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _bash_constants(tmp_path: pathlib.Path) -> tuple[str, list[str]]:
    """Return DASHBOARD_LEGEND_TEMPLATE and DASHBOARD_LEGEND_STATUS_KEYWORDS
    as the bash constants file defines them."""
    env = build_scrubbed_env(home_dir=tmp_path / "home")
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; printf "%s\\n" "$DASHBOARD_LEGEND_TEMPLATE" ' '"${DASHBOARD_LEGEND_STATUS_KEYWORDS[*]}"',
            "_",
            str(_LEGEND_CONSTANTS),
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, _diagnostic(result)
    template, keywords = result.stdout.splitlines()
    return template, keywords.split()


def _entries(keywords, colour: bool) -> list[str]:
    """The legend text of each keyword: carried is drawn as its row marker."""
    carried = "↻ carried" if colour else "[carried]"
    return [carried if keyword == "carried" else keyword for keyword in keywords]


def _fill(template: str, projects_block: str, status_block: str) -> str:
    return template.replace("{PROJECTS_BLOCK}", projects_block).replace("{STATUS_BLOCK}", status_block)


def test_colour_off_matches_the_template(tmp_path: pathlib.Path) -> None:
    template, keywords = _bash_constants(tmp_path)
    line = legend_line(False, ["alpha", "beta-project"], ["#FF8800", "#123456"], template, keywords)
    assert line == _fill(template, "[alph] alpha  [beta] beta-project", "  ".join(_entries(keywords, False)))
    assert line == (
        "PROJECT: [alph] alpha  [beta] beta-project   |   "
        "STATUS: open  running  done  wont-do  blocked  needs-operator  [carried]"
    )
    assert "\033" not in line


def test_colour_on_paints_tags_and_statuses_and_still_matches_the_template(tmp_path: pathlib.Path) -> None:
    template, keywords = _bash_constants(tmp_path)
    line = legend_line(True, ["alpha", "beta-project"], ["#FF8800", "#123456"], template, keywords)

    running = resolve_semantic_color("status_running", True)
    done = resolve_semantic_color("status_done", True)
    blocked = resolve_semantic_color("status_blocked", True)
    opened = resolve_semantic_color("status_open", True)
    needs_operator = resolve_semantic_color("status_needs_operator", True)
    assert all((running, done, blocked, opened, needs_operator))
    assert len({running, done, blocked, opened, needs_operator}) == 5

    projects_block = f"\033[38;2;255;136;0m{_GLYPH}{RESET} alpha  " f"\033[38;2;18;52;86m{_GLYPH}{RESET} beta-project"
    status_block = "  ".join(
        [
            f"{opened}open{RESET}",
            f"{running}running{RESET}",
            f"{done}done{RESET}",
            f"{done}wont-do{RESET}",
            f"{blocked}blocked{RESET}",
            f"{needs_operator}needs-operator{RESET}",
            f"{opened}↻ carried{RESET}",
        ]
    )
    assert line == _fill(template, projects_block, status_block)
    assert _ANSI.sub("", line) == _fill(
        template, f"{_GLYPH} alpha  {_GLYPH} beta-project", "  ".join(_entries(keywords, True))
    )


def test_no_projects_reads_no_projects() -> None:
    assert legend_line(False, [], [], DEFAULT_TEMPLATE, ["open"]) == "PROJECT: (no projects)   |   STATUS: open"


def test_a_project_short_of_a_colour_takes_the_last_resort_gray() -> None:
    gray = load_default_palette()[-1].lstrip("#")
    r, g, b = (int(gray[i : i + 2], 16) for i in (0, 2, 4))
    line = legend_line(True, ["alpha", "beta"], ["#FF8800"], "{PROJECTS_BLOCK}", ["open"])
    assert line.endswith(f"\033[38;2;{r};{g};{b}m{_GLYPH}{RESET} beta")


def test_a_malformed_colour_draws_an_uncoloured_tag() -> None:
    assert legend_line(True, ["alpha"], ["#F80"], "{PROJECTS_BLOCK}", ["open"]) == f"{_GLYPH}{RESET} alpha"
    assert legend_line(True, ["alpha"], ["#GGGGGG"], "{PROJECTS_BLOCK}", ["open"]) == f"{_GLYPH}{RESET} alpha"


def test_empty_template_and_keywords_fall_back_to_the_canonical_ones() -> None:
    line = legend_line(False, ["solo"], ["#000000"], "   ", [])
    assert line == _fill(DEFAULT_TEMPLATE, "[solo] solo", "  ".join(_entries(DEFAULT_STATUS_KEYWORDS, False)))
    assert line == "PROJECT: [solo] solo   |   STATUS: open  running  done  blocked  needs-operator  [carried]"


# ---------------------------------------------------------------------------
# The command line and the pane
# ---------------------------------------------------------------------------


def test_cli_prints_the_line(tmp_path: pathlib.Path) -> None:
    env = build_scrubbed_env(home_dir=tmp_path / "home", extra_path_dirs=[pathlib.Path(sys.executable).parent])
    result = subprocess.run(
        [
            "bash",
            str(_PP_RUN_OPS),
            _MODULE,
            "--use-color=TRUE",
            "--template={PROJECTS_BLOCK} | {STATUS_BLOCK}",
            "--status-keywords= open  blocked ",
            "--names=alpha\n\nbeta\n",
            "--colors=#FF8800\n#123456\n",
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, _diagnostic(result)
    expected = legend_line(
        True, ["alpha", "beta"], ["#FF8800", "#123456"], "{PROJECTS_BLOCK} | {STATUS_BLOCK}", ["open", "blocked"]
    )
    assert result.stdout == expected + "\n"


def _render_pane(tmp_path: pathlib.Path, colour: bool) -> subprocess.CompletedProcess:
    root = tmp_path / "root"
    (root / "tmp").mkdir(parents=True, exist_ok=True)
    (root / "projects.cfg").write_text("[project:demo]\npriority = 1\ndashboard_color = #FF8800\n", encoding="utf-8")
    colour_env = {"TERM": "xterm-256color"} if colour else {"NO_COLOR": "1", "TERM": "dumb"}
    env = build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_path_dirs=[pathlib.Path(sys.executable).parent],
        extra_env={
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
            **colour_env,
        },
    )
    return subprocess.run(
        ["bash", str(_LEGEND_SH), "--kanban-root", str(root)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        timeout=120,
        check=False,
    )


@pytest.mark.parametrize("colour", [False, True])
def test_legend_render_script_prints_the_function_line(tmp_path: pathlib.Path, colour: bool) -> None:
    template, keywords = _bash_constants(tmp_path)
    result = _render_pane(tmp_path, colour)
    assert result.returncode == 0, _diagnostic(result)
    assert result.stdout == legend_line(colour, ["demo"], ["#FF8800"], template, keywords) + "\n", _diagnostic(result)
