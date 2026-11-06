"""
test_cron_firings_line.py
=========================
The multi-project header's "Next cron firings" line: ``next_firings_line``
in ``pgai_agent_kanban.dashboard.cron_firings`` and the module's ``--line``
mode, which ``scripts/dashboard/show-multi.sh`` prints after its label.

Covered behaviour:

  * crontab text with agent entries gives ``<agent>: <label>`` entries in
    crontab line order, joined by three spaces, from the real
    ``pm-agent/lib/cron_parser.py`` at a fixed reference time;
  * crontab text with no agent entries, and empty text, give "";
  * the label of each parser value: an integer is seconds until the
    firing and is labelled in whole minutes (under a minute "now", 60-119
    seconds "in 1 min", N whole minutes "in N min"), a sentinel string is
    itself, and None is left out;
  * each agent's label on the line equals the label ``cron_firings()``
    gives the single-project panes for the same crontab and time;
  * the command line prints the line (an empty line for no entries), and
    for a missing or raising parser prints nothing on stdout, one line on
    stderr and exits 1;
  * show-multi.sh, run as bash on a throwaway two-project kanban root with
    a stub ``crontab``, prints the line after its label, and only the label
    when the parser fails.

Subprocesses run with an environment built from an explicit set and a
throwaway HOME; the stub ``crontab`` ahead on PATH means no real crontab is
read.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
from datetime import datetime

import pytest

from pgai_agent_kanban.dashboard.cron_firings import cron_firings, next_firings_line
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_PP_RUN_OPS = _TEAM_DIR / "scripts" / "lib" / "pp_run_ops.sh"
_SHOW_MULTI_SH = _TEAM_DIR / "scripts" / "dashboard" / "show-multi.sh"
_CRON_PARSER = _TEAM_DIR / "pm-agent" / "lib" / "cron_parser.py"
_MODULE = "pgai_agent_kanban.dashboard.cron_firings"

# A Thursday, on a minute boundary.
_NOW = datetime(2026, 10, 15, 10, 0, 0)

_CRONTAB = (
    "# kanban schedule\n"
    "* * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=pm --sleep=12\n"
    "* * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=coder --sleep=0\n"
    "0 3 * * 4 $KANBAN_ROOT/scripts/wake-batch.sh --agent=overwatch\n"
    "0 4 * * 0 $KANBAN_ROOT/scripts/cleanup.sh\n"
)
_WEEKLY_CRONTAB = (
    "0 3 * * 4 $KANBAN_ROOT/scripts/wake-batch.sh --agent=overwatch\n" "0 4 * * 0 $KANBAN_ROOT/scripts/cleanup.sh\n"
)
_NO_AGENT_CRONTAB = "# nothing scheduled\n*/5 * * * * /usr/bin/true\n"
# Six minutes before coder's next */10 firing, 26 before pm's next */30.
_MINUTES_NOW = datetime(2026, 10, 18, 5, 4, 0)
_MINUTES_CRONTAB = (
    "*/10 * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=coder\n"
    "*/30 * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=pm\n"
)


def _diagnostic(result: subprocess.CompletedProcess) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _stub_parser(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    path = tmp_path / "stub_cron_parser.py"
    path.write_text(f"def next_firings(crontab_text, now=None):\n    {body}\n", encoding="utf-8")
    return path


def test_entries_follow_crontab_order_with_their_labels() -> None:
    assert next_firings_line(_CRONTAB, str(_CRON_PARSER), now=_NOW) == (
        "pm: now   coder: in 1 min   overwatch: Thu 3am   cleanup: Sun 4am"
    )


def test_labels_are_minutes_and_match_the_single_project_panes() -> None:
    line = next_firings_line(_MINUTES_CRONTAB, str(_CRON_PARSER), now=_MINUTES_NOW)
    labels = dict(entry.split(": ", 1) for entry in line.split("   "))
    assert labels["coder"] == "in 6 min", line
    pane_labels = cron_firings(_MINUTES_CRONTAB, str(_CRON_PARSER), now_ts=_MINUTES_NOW.timestamp())
    assert labels == pane_labels


@pytest.mark.parametrize("crontab_text", [_NO_AGENT_CRONTAB, ""])
def test_no_agent_entries_give_an_empty_line(crontab_text: str) -> None:
    assert next_firings_line(crontab_text, str(_CRON_PARSER), now=_NOW) == ""


def test_each_parser_value_is_labelled_in_whole_minutes(tmp_path: pathlib.Path) -> None:
    parser = _stub_parser(
        tmp_path, 'return {"a": 0, "b": -5, "c": 1, "d": 347, "e": None, "f": "Sun 4am", "g": 60, "h": 120}'
    )
    assert next_firings_line("ignored", str(parser)) == (
        "a: now   b: now   c: now   d: in 5 min   f: Sun 4am   g: in 1 min   h: in 2 min"
    )


def test_a_missing_parser_raises(tmp_path: pathlib.Path) -> None:
    with pytest.raises(OSError):
        next_firings_line(_CRONTAB, str(tmp_path / "absent.py"))


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------


def _run(tmp_path: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    env = build_scrubbed_env(home_dir=tmp_path / "home", extra_path_dirs=[pathlib.Path(sys.executable).parent])
    return subprocess.run(
        ["bash", str(_PP_RUN_OPS), _MODULE, *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        timeout=60,
        check=False,
    )


def test_cli_prints_the_line(tmp_path: pathlib.Path) -> None:
    result = _run(tmp_path, "--line", _WEEKLY_CRONTAB, str(_CRON_PARSER))
    assert (result.returncode, result.stdout) == (0, "overwatch: Thu 3am   cleanup: Sun 4am\n"), _diagnostic(result)


def test_cli_prints_an_empty_line_for_no_entries(tmp_path: pathlib.Path) -> None:
    result = _run(tmp_path, "--line", _NO_AGENT_CRONTAB, str(_CRON_PARSER))
    assert (result.returncode, result.stdout) == (0, "\n"), _diagnostic(result)


@pytest.mark.parametrize("parser_kind", ["missing", "raising"])
def test_cli_failure_prints_nothing_and_exits_one(tmp_path: pathlib.Path, parser_kind: str) -> None:
    if parser_kind == "missing":
        parser = tmp_path / "absent.py"
    else:
        parser = _stub_parser(tmp_path, 'raise RuntimeError("parser broke")')
    result = _run(tmp_path, "--line", _WEEKLY_CRONTAB, str(parser))
    assert (result.returncode, result.stdout) == (1, ""), _diagnostic(result)
    assert "--line" in result.stderr and len(result.stderr.splitlines()) == 1, _diagnostic(result)


def test_cli_refuses_a_wrong_argument_count(tmp_path: pathlib.Path) -> None:
    result = _run(tmp_path, "--line", _WEEKLY_CRONTAB)
    assert (result.returncode, result.stdout) == (1, ""), _diagnostic(result)
    assert "Usage:" in result.stderr


# ---------------------------------------------------------------------------
# The multi-project header pane
# ---------------------------------------------------------------------------


def _multi_header(tmp_path: pathlib.Path, parser_source: str | None) -> subprocess.CompletedProcess:
    root = tmp_path / "root"
    (root / "tmp").mkdir(parents=True)
    (root / "VERSION").write_text("v1.0.0\n", encoding="utf-8")
    (root / "projects.cfg").write_text("[project:one]\npriority = 1\n\n[project:two]\npriority = 2\n", encoding="utf-8")
    lib = root / "pm-agent" / "lib"
    lib.mkdir(parents=True)
    if parser_source is None:
        shutil.copy(_CRON_PARSER, lib / "cron_parser.py")
    else:
        (lib / "cron_parser.py").write_text(parser_source, encoding="utf-8")
    crontab_file = tmp_path / "crontab.txt"
    crontab_file.write_text(_WEEKLY_CRONTAB, encoding="utf-8")
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    stub = stub_bin / "crontab"
    stub.write_text(f'#!/bin/sh\n[ "$1" = "-l" ] && exec cat "{crontab_file}"\nexit 1\n', encoding="utf-8")
    stub.chmod(0o755)
    env = build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_path_dirs=[stub_bin, pathlib.Path(sys.executable).parent],
        extra_env={
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
            "NO_COLOR": "1",
            "TERM": "dumb",
        },
    )
    return subprocess.run(
        ["bash", str(_SHOW_MULTI_SH), "--kanban-root", str(root), "--mode", "header"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        timeout=120,
        check=False,
    )


def test_show_multi_prints_the_line_after_its_label(tmp_path: pathlib.Path) -> None:
    result = _multi_header(tmp_path, None)
    assert result.returncode == 0, _diagnostic(result)
    assert "═══ two (priority 2) ═══" in result.stdout, _diagnostic(result)
    assert result.stdout.endswith("\nNext cron firings: overwatch: Thu 3am   cleanup: Sun 4am\n"), _diagnostic(result)


def test_show_multi_prints_only_the_label_when_the_parser_fails(tmp_path: pathlib.Path) -> None:
    result = _multi_header(tmp_path, 'def next_firings(crontab_text, now=None):\n    raise RuntimeError("x")\n')
    assert result.returncode == 0, _diagnostic(result)
    assert result.stdout.endswith("\nNext cron firings: \n"), _diagnostic(result)
