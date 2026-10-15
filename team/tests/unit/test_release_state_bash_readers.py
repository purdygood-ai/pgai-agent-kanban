"""
test_release_state_bash_readers.py
==================================
The bash readers of release-state.md read through the one release-state
reader (pgai_agent_kanban.cm.read_state_field), by way of the wrappers in
team/scripts/lib/cm/common.sh:

  release_state_section <file> <heading>...  one line per heading, section
                                             bounded at the next '##'
  release_state_value   <file> <heading>     the value, or "none"
  release_state_active_rc <file> [none]      a vX.Y.Z version, or [none]
  _active_rc <project> [none]                the same, for a project by name

Every converted reader is fed the release-state.md shapes CM's writer and
its event appenders leave (open RC, no RC, retired Last Released At /
Last Released By Task headings, appended events, no file at all). The
assertions pin the value each one returns. That is the same value its own
awk or inline-python parser returned on that shape:

  * lib/purge-helpers.sh            _purge_read_active_rc
  * lib/overwatch-checks/*.sh       _ccd_read_active_rc, _corb_read_active_rc,
                                    _csarc_read_active_rc
  * lib/green_ledger.sh             _green_ledger_rc_scope
  * lib/project_paths.sh            pp_last_released_version's
                                    release-state.md fallback
  * dashboard/data.sh               ACTIVE_RC and RC_OPENED_AT

The stale-Active-RC repair now resets the three RC fields through the
release-state writer; its test pins that every other line survives.

Every fixture lives under pytest's tmp_path; no live kanban root is read or
written.
"""

from __future__ import annotations

import pathlib

import pytest

from pgai_agent_kanban.cm.read_state_field import read_state_section
from tests.fixtures.dashboard_render_fixture import (
    build_dashboard_project_root,
    clean_subprocess_env,
    run_dashboard_script,
)
from tests.unit.shell_harness import run_bash

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_LIB = _TEAM_DIR / "scripts" / "lib"
_COMMON = _LIB / "cm" / "common.sh"
_PURGE = _LIB / "purge-helpers.sh"
_CHECKS = _LIB / "overwatch-checks"
_GREEN_LEDGER = _LIB / "green_ledger.sh"
_PROJECT_PATHS = _LIB / "project_paths.sh"
_DATA_SH = _TEAM_DIR / "scripts" / "dashboard" / "data.sh"

_RC_OPEN = (
    "# Release State\n\n## Active RC\nv2.30.0\n\n## RC Opened At\n2026-10-05T03:00:00Z\n\n"
    "## RC Opened By Task\nCM-20261005-034-open-rc\n\n## Last Released\nv2.29.2\n"
)
_IDLE = (
    "# Release State\n\n## Active RC\nnone\n\n## RC Opened At\nnone\n\n"
    "## RC Opened By Task\nnone\n\n## Last Released\nv2.29.2\n"
)
_RETIRED_OPEN = (
    "# Release State\n\n## Active RC\nv1.2.0\n\n## RC Opened At\n2026-01-03T00:00:00Z\n\n"
    "## RC Opened By Task\nnone\n\n## Last Released\nv1.1.0\n\n"
    "## Last Released At\n2026-01-01T00:00:00Z\n\n## Last Released By Task\nCM-old\n"
)
_RETIRED_ONLY = (
    "# Release State\n\n## Active RC\nnone\n\n## RC Opened At\nnone\n\n## RC Opened By Task\nnone\n\n"
    "## Last Released At\n2026-01-01T00:00:00Z\n\n## Last Released By Task\nCM-old\n"
)
_EVENTS = (
    "## Active RC\nv2.0.0\n\n## RC Opened At\n2026-02-02T00:00:00Z\n\n## Last Released\nv1.9.9\n\n"
    "## OVERWATCH Decision\nVersion: v2.0.0\n"
)

# name -> (text or None for no file,
#          expected: (Active RC value, active_rc version or "", RC Opened At value,
#                     Last Released fallback version or ""))
_SHAPES = {
    "rc-open": (_RC_OPEN, ("v2.30.0", "v2.30.0", "2026-10-05T03:00:00Z", "v2.29.2")),
    "idle": (_IDLE, ("none", "", "none", "v2.29.2")),
    "retired-headings-rc-open": (_RETIRED_OPEN, ("v1.2.0", "v1.2.0", "2026-01-03T00:00:00Z", "v1.1.0")),
    "retired-headings-only": (_RETIRED_ONLY, ("none", "", "none", "")),
    "events-appended": (_EVENTS, ("v2.0.0", "v2.0.0", "2026-02-02T00:00:00Z", "v1.9.9")),
    "missing-file": (None, ("none", "", "none", "")),
}
_SHAPE_IDS = sorted(_SHAPES)


def _project(tmp_path: pathlib.Path, text: "str | None") -> pathlib.Path:
    """A projects/demo directory holding *text* as release-state.md (or none)."""
    proj = tmp_path / "kanban" / "projects" / "demo"
    proj.mkdir(parents=True)
    if text is not None:
        (proj / "release-state.md").write_text(text, encoding="utf-8")
    return proj


def _bash(tmp_path: pathlib.Path, script: str, **kwargs):
    result = run_bash(tmp_path, "set -euo pipefail\n" + script, timeout=60, **kwargs)
    assert result.returncode == 0, f"rc={result.returncode}\nstdout={result.stdout!r}\nstderr={result.stderr!r}"
    return result.stdout


# --- the wrappers ---------------------------------------------------------------


@pytest.mark.parametrize("shape", _SHAPE_IDS)
def test_wrappers_read_each_shape(tmp_path: pathlib.Path, shape: str) -> None:
    text, (active, version, opened_at, _last) = _SHAPES[shape]
    rs = _project(tmp_path, text) / "release-state.md"
    out = _bash(
        tmp_path,
        f"""source '{_COMMON}'
printf '%s|' "$(release_state_value '{rs}' 'Active RC')"
printf '%s|' "$(release_state_active_rc '{rs}')"
printf '%s|' "$(release_state_active_rc '{rs}' none)"
printf '%s|' "$(release_state_value '{rs}' 'RC Opened At')"
""",
    )
    assert out == f"{active}|{version}|{version or 'none'}|{opened_at}|"


def test_section_batch_prints_one_line_per_heading_in_order(tmp_path: pathlib.Path) -> None:
    rs = _project(tmp_path, _RC_OPEN) / "release-state.md"
    out = _bash(
        tmp_path,
        f"""source '{_COMMON}'
release_state_section '{rs}' 'RC Opened At' 'Active RC' 'Last Released'
""",
    )
    assert out == "2026-10-05T03:00:00Z\nv2.30.0\nv2.29.2\n"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("## Active RC\n\n## RC Opened At\n2026-01-01T00:00:00Z\n", "[\n2026-01-01T00:00:00Z] rc=0"),
        ("## RC Opened At\n2026-01-01T00:00:00Z\n", "[\n2026-01-01T00:00:00Z] rc=1"),
        (None, "[] rc=1"),
    ],
    ids=["empty-field-is-an-empty-line", "absent-heading-returns-1", "missing-file-returns-1-silently"],
)
def test_section_keeps_empty_absent_and_missing_apart(
    tmp_path: pathlib.Path, text: "str | None", expected: str
) -> None:
    rs = _project(tmp_path, text) / "release-state.md"
    out = _bash(
        tmp_path,
        f"""source '{_COMMON}'
out="$(release_state_section '{rs}' 'Active RC' 'RC Opened At')" && rc=0 || rc=$?
printf '[%s] rc=%s' "$out" "$rc"
""",
    )
    assert out == expected


def test_empty_active_rc_never_reads_the_next_field(tmp_path: pathlib.Path) -> None:
    """An empty Active RC followed by a version-valued field reads as no RC."""
    rs = _project(tmp_path, "## Active RC\n\n## Last Released\nv1.2.3\n") / "release-state.md"
    out = _bash(
        tmp_path,
        f"""source '{_COMMON}'
printf '%s|%s' "$(release_state_value '{rs}' 'Active RC')" "$(release_state_active_rc '{rs}' none)"
""",
    )
    assert out == "none|none"
    assert read_state_section(str(rs), "Active RC") == ""


def _active_rc(tmp_path: pathlib.Path, project: str = "demo") -> str:
    """common.sh _active_rc for *project* under the tmp kanban root, default 'none'."""
    kanban = str(tmp_path / "kanban")
    return _bash(
        tmp_path,
        f"source '{_PROJECT_PATHS}'\nsource '{_COMMON}'\nprintf '[%s]' \"$(_active_rc {project} none)\"\n",
        extra_env={"KANBAN_ROOT": kanban, "PGAI_AGENT_KANBAN_ROOT_PATH": kanban, "PGAI_PROJECT_ROOT": ""},
    )


@pytest.mark.parametrize("shape", _SHAPE_IDS)
def test_project_active_rc_reads_each_shape(tmp_path: pathlib.Path, shape: str) -> None:
    """_active_rc returns the version each populated shape holds, else the default."""
    text, (_active, version, _opened, _last) = _SHAPES[shape]
    _project(tmp_path, text)
    assert _active_rc(tmp_path) == f"[{version or 'none'}]"


@pytest.mark.parametrize(
    "text",
    [
        "## Active RC\n\n## Last Released\nv1.2.3\n",
        "## Active RC\n## Last Released\nv1.2.3\n",
        "## Active RC\n\n## OVERWATCH Decision\nv1.2.3\n",
    ],
    ids=["blank-line-then-version-field", "version-field-directly-below", "version-in-appended-block"],
)
def test_project_active_rc_never_returns_the_next_fields_value(tmp_path: pathlib.Path, text: str) -> None:
    """An empty Active RC is no RC, whatever version the section after it holds."""
    _project(tmp_path, text)
    assert _active_rc(tmp_path) == "[none]"


# --- converted lib readers ---------------------------------------------------------


@pytest.mark.parametrize("shape", _SHAPE_IDS)
@pytest.mark.parametrize(
    ("lib", "fn", "arg"),
    [
        (_PURGE, "_purge_read_active_rc", "dir"),
        (_CHECKS / "check-changelog-drift.sh", "_ccd_read_active_rc", "file"),
        (_CHECKS / "check-orphan-rc-branches.sh", "_corb_read_active_rc", "file"),
        (_CHECKS / "check-stale-active-rc.sh", "_csarc_read_active_rc", "file"),
    ],
    ids=["purge", "changelog-drift", "orphan-rc-branches", "stale-active-rc"],
)
def test_lib_active_rc_readers_read_each_shape(
    tmp_path: pathlib.Path, shape: str, lib: pathlib.Path, fn: str, arg: str
) -> None:
    text, (active, _version, _opened, _last) = _SHAPES[shape]
    proj = _project(tmp_path, text)
    target = proj if arg == "dir" else proj / "release-state.md"
    out = _bash(tmp_path, f"source '{lib}'\n{fn} '{target}'\n")
    assert out == f"{active}\n"


@pytest.mark.parametrize("shape", _SHAPE_IDS)
def test_green_ledger_scope_reads_each_shape(tmp_path: pathlib.Path, shape: str) -> None:
    text, (_active, version, _opened, _last) = _SHAPES[shape]
    proj = _project(tmp_path, text)
    out = _bash(
        tmp_path,
        f"source '{_GREEN_LEDGER}'\n_green_ledger_rc_scope demo\n",
        extra_env={"PGAI_PROJECT_ROOT": str(proj), "PGAI_AGENT_KANBAN_ROOT_PATH": str(tmp_path / "kanban")},
    )
    assert out == (version or "no-active-rc")


@pytest.mark.parametrize("shape", _SHAPE_IDS)
def test_last_released_fallback_reads_each_shape(tmp_path: pathlib.Path, shape: str) -> None:
    """A project with no project.cfg resolves its last release from release-state.md."""
    text, (_active, _version, _opened, last) = _SHAPES[shape]
    _project(tmp_path, text)
    out = _bash(
        tmp_path,
        f"source '{_PROJECT_PATHS}'\npp_last_released_version demo\n",
        extra_env={"KANBAN_ROOT": str(tmp_path / "kanban"), "PGAI_AGENT_KANBAN_ROOT_PATH": str(tmp_path / "kanban")},
    )
    assert out == f"{last or 'v0.0.0'}\n"


# --- the stale-Active-RC reset writes through the release-state writer ---------------


def test_stale_active_rc_reset_changes_only_the_three_rc_fields(tmp_path: pathlib.Path) -> None:
    event = "## HALT Event\n- Timestamp: 2026-01-01T00:00:00Z\n- Reason: operator\n"
    before = _RETIRED_OPEN + "\n" + event
    rs = _project(tmp_path, before) / "release-state.md"
    _bash(
        tmp_path,
        f"""source '{_CHECKS / "check-stale-active-rc.sh"}'
overwatch_backup_file() {{ echo '{tmp_path}/backup'; }}
overwatch_log_action() {{ :; }}
_CSARC_RS_FILE='{rs}'
_CSARC_ACTIVE_RC=v1.2.0
_csarc_do_reset
""",
    )
    after = rs.read_text(encoding="utf-8")
    expected = (
        before.replace("## Active RC\nv1.2.0\n", "## Active RC\nnone\n", 1)
        .replace("## RC Opened At\n2026-01-03T00:00:00Z\n", "## RC Opened At\nnone\n", 1)
    )
    assert after == expected


# --- dashboard/data.sh reads Active RC and RC Opened At in one call ----------------------


@pytest.mark.parametrize("shape", _SHAPE_IDS)
def test_dashboard_data_emits_active_rc_and_rc_opened_at(tmp_path: pathlib.Path, shape: str) -> None:
    text, (_active, version, opened_at, _last) = _SHAPES[shape]
    fixture = build_dashboard_project_root(tmp_path, dev_tree_mode="unconfigured")
    rs = fixture.kanban_root / "projects" / fixture.project_name / "release-state.md"
    if text is None:
        rs.unlink(missing_ok=True)
    else:
        rs.write_text(text, encoding="utf-8")
    result = run_dashboard_script(
        ["bash", str(_DATA_SH), "--project", fixture.project_name, "--kanban-root", str(fixture.kanban_root)],
        env=clean_subprocess_env(kanban_root=fixture.kanban_root),
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    pairs = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    assert pairs["ACTIVE_RC"] == (version or "none")
    assert pairs["RC_OPENED_AT"] == ("" if text is None else opened_at)
