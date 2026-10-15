"""
test_dashboard_global_defer_segments.py
=======================================
The dashboard's global taskbar (status-bottom.sh without a project, and
status-right.sh) shows one DEFER segment per deferral scope in force --
like HALT-AFTER, per project, with its level and end version -- whether or
not any bug is waiting:

  * a project whose own DEFER-LOW-BUGS campaign file (or project.cfg) defers
    its bugs renders "DEFER <project> <=low until vX.Y.Z (n waiting)", with
    the deferral glyph in rich mode and without it under NO_COLOR; a project
    with no deferral renders nothing;
  * zero open low bugs still renders the segment, "(0 waiting)";
  * a kanban.cfg-level (all projects) deferral renders once, as
    "DEFER GLOBAL ...", counting every project that inherits it; a project
    with its own deferral beside it renders its own segment, after GLOBAL;
  * projects render alphabetically;
  * a malformed campaign file still renders "DEFER CONFIG UNREADABLE: <path>",
    once per offending file;
  * the global bar ignores a PGAI_PROJECT_NAME the environment carries;
  * HALT / HALT-AFTER segments and the per-project drill bar's text are
    unchanged.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

import pytest

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_REPO_ROOT = _TEAM_DIR.parent
_STATUS_BOTTOM = _TEAM_DIR / "scripts" / "dashboard" / "status-bottom.sh"
_STATUS_RIGHT = _TEAM_DIR / "scripts" / "dashboard" / "status-right.sh"
_GLYPH = "✋"  # GLYPH_DEFERRED_BUG in team/scripts/lib/status_glyphs.sh


def _root(tmp_path: pathlib.Path, projects: tuple[str, ...] = ("alpha", "beta")) -> pathlib.Path:
    root = tmp_path / "root"
    root.mkdir()
    (root / "VERSION").write_text("v9.9.9\n", encoding="utf-8")
    (root / "projects.cfg").write_text(
        "".join(f"[project:{name}]\npriority={i}\n\n" for i, name in enumerate(projects, 1)),
        encoding="utf-8",
    )
    for name in projects:
        (root / "projects" / name).mkdir(parents=True)
        (root / "projects" / name / "project.cfg").write_text(
            "[project]\nworkflow_type = release\n", encoding="utf-8"
        )
    return root


def _bug(root: pathlib.Path, project: str, bug_id: str, severity: str = "low", status: str = "open") -> None:
    bugs = root / "projects" / project / "bugs"
    bugs.mkdir(parents=True, exist_ok=True)
    (bugs / f"{bug_id}-fixture.md").write_text(
        f"# {bug_id}\n\n**Severity:** {severity}\n\n## Status\n{status}\n", encoding="utf-8"
    )


def _campaign(root: pathlib.Path, project: "str | None", body: str = "until=v2.33.0\n") -> pathlib.Path:
    path = (root / "projects" / project if project else root) / "DEFER-LOW-BUGS"
    path.write_text(body, encoding="utf-8")
    return path


def _render(
    tmp_path: pathlib.Path, script: pathlib.Path, root: pathlib.Path, *args: str,
    rich: bool = False, **extra: str,
) -> str:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env_extra = {
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
        "PGAI_DEV_TREE_PATH": str(_REPO_ROOT),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "scratch"),
        "TERM": "xterm-256color" if rich else "dumb",
        **extra,
    }
    if not rich:
        env_extra["NO_COLOR"] = "1"
    result = subprocess.run(
        ["bash", str(script), str(root), *args],
        capture_output=True, text=True, timeout=120, check=False,
        env=build_scrubbed_env(
            home_dir=home, extra_path_dirs=[pathlib.Path(sys.executable).parent], extra_env=env_extra
        ),
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _global(tmp_path: pathlib.Path, root: pathlib.Path, **kwargs) -> str:
    return _render(tmp_path, _STATUS_BOTTOM, root, **kwargs)


def _defer_segments(line: str) -> list[str]:
    return [seg for seg in re.split(r" \| |  ", line) if "DEFER" in seg]


def test_project_campaign_renders_its_own_segment_and_nothing_for_others(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path)
    _campaign(root, "alpha")
    _bug(root, "alpha", "BUG-0001")
    _bug(root, "alpha", "BUG-0002")
    _bug(root, "alpha", "BUG-0003", severity="high")
    _bug(root, "beta", "BUG-0004")

    plain = _global(tmp_path, root)
    rich = _global(tmp_path, root, rich=True)

    assert _defer_segments(plain) == ["DEFER alpha <=low until v2.33.0 (2 waiting)"], plain
    assert _defer_segments(rich) == [f"{_GLYPH} DEFER alpha <=low until v2.33.0 (2 waiting)"], rich
    assert "beta" not in plain, plain


def test_a_deferral_with_nothing_waiting_still_shows(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path)
    _campaign(root, "alpha")
    _bug(root, "alpha", "BUG-0001", status="running")

    assert _defer_segments(_global(tmp_path, root)) == ["DEFER alpha <=low until v2.33.0 (0 waiting)"]


def test_kanban_cfg_deferral_renders_once_as_global(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path)
    (root / "kanban.cfg").write_text("[release]\ndeferred_bugs = true\ndeferred_bug_level = low\n", encoding="utf-8")
    _bug(root, "alpha", "BUG-0001")
    _bug(root, "beta", "BUG-0002")
    _bug(root, "beta", "BUG-0003", severity="medium")

    assert _defer_segments(_global(tmp_path, root)) == ["DEFER GLOBAL <=low (2 waiting)"]


def test_a_project_deferral_beside_a_global_one_renders_after_it(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path, ("alpha", "beta", "gamma"))
    (root / "kanban.cfg").write_text("[release]\ndeferred_bugs = true\n", encoding="utf-8")
    with (root / "projects" / "gamma" / "project.cfg").open("a", encoding="utf-8") as cfg:
        cfg.write("\n[release]\ndeferred_bug_level = medium\n")
    _campaign(root, "alpha")
    _bug(root, "alpha", "BUG-0001")
    _bug(root, "beta", "BUG-0002")
    _bug(root, "gamma", "BUG-0003", severity="medium")
    _bug(root, "gamma", "BUG-0004")

    assert _defer_segments(_global(tmp_path, root)) == [
        "DEFER GLOBAL <=low (1 waiting)",
        "DEFER alpha <=low until v2.33.0 (1 waiting)",
        "DEFER gamma <=medium (2 waiting)",
    ]


def test_project_segments_render_alphabetically(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path, ("zulu", "alpha"))
    _campaign(root, "zulu", "until=v3.0.0\n")
    _campaign(root, "alpha")

    assert _defer_segments(_global(tmp_path, root)) == [
        "DEFER alpha <=low until v2.33.0 (0 waiting)",
        "DEFER zulu <=low until v3.0.0 (0 waiting)",
    ]


@pytest.mark.parametrize("scope", ["root", "project"])
def test_malformed_campaign_file_still_renders_unreadable(tmp_path: pathlib.Path, scope: str) -> None:
    root = _root(tmp_path)
    path = _campaign(root, None if scope == "root" else "alpha", "until=garbage\n")

    assert _defer_segments(_global(tmp_path, root)) == [f"DEFER CONFIG UNREADABLE: {path}"]


def test_global_bar_ignores_an_inherited_project_name(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path)
    _campaign(root, "alpha")

    assert _defer_segments(_global(tmp_path, root, PGAI_PROJECT_NAME="alpha")) == [
        "DEFER alpha <=low until v2.33.0 (0 waiting)"
    ]


def test_halt_segments_and_the_drill_bar_text_are_unchanged(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path)
    (root / "HALT").write_text("", encoding="utf-8")
    (root / "projects" / "beta" / "HALT-AFTER").write_text("rc:v1.2.3\n", encoding="utf-8")
    _campaign(root, "alpha")
    _bug(root, "alpha", "BUG-0001")
    _bug(root, "alpha", "BUG-0002")

    global_line = _global(tmp_path, root)
    assert re.sub(r"\w{3} \d{4}-\S+$", "<TS>", global_line) == (
        "v9.9.9 | PM:auto | [HALT GLOBAL] | [HALT-AFTER:beta until v1.2.3] | "
        "DEFER alpha <=low until v2.33.0 (2 waiting) | <TS>"
    )
    drill = _render(tmp_path, _STATUS_BOTTOM, root, "alpha")
    assert re.sub(r"\w{3} \d{4}-\S+$", "<TS>", drill) == (
        "v9.9.9 | alpha:v0.0.0 | workflow:release | PM:auto | [HALT GLOBAL] | "
        "BUGS DEFERRED 2 (<=low) until v2.33.0 | <TS>"
    )


def test_status_right_shows_the_same_segments(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path)
    _campaign(root, "alpha")
    _bug(root, "alpha", "BUG-0001")
    _bug(root, "alpha", "BUG-0002")

    plain = _render(tmp_path, _STATUS_RIGHT, root)
    assert _defer_segments(plain) == ["DEFER alpha <=low until v2.33.0 (2 waiting)"], plain
