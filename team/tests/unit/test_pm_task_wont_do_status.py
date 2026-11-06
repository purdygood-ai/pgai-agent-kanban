"""
test_pm_task_wont_do_status.py
==============================
The status.md rewrite OVERWATCH's README-bundled check makes when it closes
a PM task whose requirements input is a README or a template:
``pgai_agent_kanban.overwatch.pm_task_wont_do``.

Covered behaviour:

  * ``## State`` becomes WONT-DO, ``## Needs Human`` no, and ``## Summary``
    and ``## Blocked Reason`` carry the given bad input, bug number, bug
    file and detection time;
  * every other section keeps its bytes; Blocked Reason is added directly
    after ``## Blockers``, rewritten in place when present, and appended at
    the end when the file has neither;
  * a State, Summary or Needs Human the file does not carry is not added;
  * an unreadable status file (missing, or a directory) fails with
    ``Cannot read status file:`` and the command line exits 1;
  * through check-readme-bundled.sh's _crb_mark_status_wont_do, the file
    is rewritten even when the caller defined its own pp_run_ops.

Every fixture lives under tmp_path; subprocesses run with an environment
built from an explicit set.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import textwrap

import pytest

from pgai_agent_kanban.overwatch import pm_task_wont_do
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_CHECK = _TEAM_DIR / "scripts" / "lib" / "overwatch-checks" / "check-readme-bundled.sh"
_MODULE = "pgai_agent_kanban.overwatch.pm_task_wont_do"

_BAD_INPUT = "requirements/templates/requirement.md"
_BUG_NUM = "0042"
_BUG_FILE = "/kanban/projects/demo/bugs/BUG-0042-pm-task-readme-or-template-as-requirements-input.md"
_DETECTED_AT = "2026-10-18T03:04:05Z"
_ARGS = (_BAD_INPUT, _BUG_NUM, _BUG_FILE, _DETECTED_AT)

_SUMMARY = (
    f"PM task marked WONT-DO by OVERWATCH check-readme-bundled at {_DETECTED_AT}: "
    "input path points at a README.md or templates/ file, not a valid requirements bundle. "
    f"Bug BUG-{_BUG_NUM} filed. Offending input: {_BAD_INPUT}"
)
_REASON = (
    f"Input path points at a README.md or templates/ file: {_BAD_INPUT}. "
    "This is not a valid requirements bundle. "
    f"Bug BUG-{_BUG_NUM} filed at {_BUG_FILE}."
)

_STATUS = (
    "# Status\n\n"
    "## Task\nPM-20261018-001-decompose\n\n"
    "## State\nWORKING\n\n"
    "## Summary\nold summary\n### detail\nkept under the summary\n\n"
    "## Artifacts\n- keep me\n\n"
    "## Blockers\nnone\n\n"
    "## Needs Human\nyes\n\n"
    "## Next Recommended Step\nkeep me too\n"
)


def _run_module(tmp_path: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    env = build_scrubbed_env(home_dir=tmp_path / "home", extra_env={"PYTHONPATH": str(_TEAM_DIR)})
    return subprocess.run(
        [sys.executable, "-m", _MODULE, "--", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        timeout=60,
        check=False,
    )


def test_state_is_set_to_wont_do_with_the_given_reason_fields() -> None:
    after = pm_task_wont_do.wont_do_status_text(_STATUS, *_ARGS)
    assert after == (
        "# Status\n\n"
        "## Task\nPM-20261018-001-decompose\n\n"
        "## State\nWONT-DO\n\n"
        f"## Summary\n{_SUMMARY}\n"
        "### detail\nkept under the summary\n\n"
        "## Artifacts\n- keep me\n\n"
        "## Blockers\nnone\n\n"
        f"## Blocked Reason\n{_REASON}\n\n"
        "## Needs Human\nno\n\n"
        "## Next Recommended Step\nkeep me too\n"
    )


def test_existing_blocked_reason_is_rewritten_in_place() -> None:
    before = "## State\nBACKLOG\n\n## Blocked Reason\nold reason\n\n## Labels\nkeep\n"
    after = pm_task_wont_do.wont_do_status_text(before, *_ARGS)
    assert after == f"## State\nWONT-DO\n\n## Blocked Reason\n{_REASON}\n\n## Labels\nkeep\n"


def test_blocked_reason_is_appended_and_absent_sections_are_not_added() -> None:
    before = "# Status\n\n## State\nWORKING\n\n## Labels\nkeep\n\n\n\n"
    after = pm_task_wont_do.wont_do_status_text(before, *_ARGS)
    assert after == f"# Status\n\n## State\nWONT-DO\n\n## Labels\nkeep\n\n## Blocked Reason\n{_REASON}\n"


def test_command_line_rewrites_the_file_and_prints_nothing(tmp_path: pathlib.Path) -> None:
    status = tmp_path / "status.md"
    status.write_text(_STATUS, encoding="utf-8")
    result = _run_module(tmp_path, str(status), *_ARGS)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", ""), result.stderr
    assert status.read_text(encoding="utf-8") == pm_task_wont_do.wont_do_status_text(_STATUS, *_ARGS)


@pytest.mark.parametrize("shape", ["missing", "directory"])
def test_unreadable_status_file_fails_with_one_line(tmp_path: pathlib.Path, shape: str) -> None:
    status = tmp_path / "status.md"
    if shape == "directory":
        status.mkdir()
    with pytest.raises(pm_task_wont_do.StatusRewriteError) as raised:
        pm_task_wont_do.mark_status_wont_do(status, *_ARGS)
    assert str(raised.value).startswith("Cannot read status file: ")

    result = _run_module(tmp_path, str(status), *_ARGS)
    assert (result.returncode, result.stdout) == (1, "")
    assert result.stderr.startswith("Cannot read status file: ") and result.stderr.count("\n") == 1, result.stderr


def test_readme_check_rewrite_step_runs_its_own_runner(tmp_path: pathlib.Path) -> None:
    status = tmp_path / "status.md"
    status.write_text(_STATUS, encoding="utf-8")
    script = textwrap.dedent(f"""\
        set -euo pipefail
        pp_run_ops() {{ echo "caller-runner $*"; return 0; }}
        source '{_CHECK}'
        rc=0
        _crb_mark_status_wont_do '{status}' '{_BAD_INPUT}' '{_BUG_NUM}' '{_BUG_FILE}' '{_DETECTED_AT}' || rc=$?
        echo "rc=$rc"
    """)
    env = build_scrubbed_env(home_dir=tmp_path / "home")
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        timeout=60,
        check=False,
    )
    assert result.stdout == "rc=0\n", f"stdout={result.stdout}\nstderr={result.stderr}"
    assert status.read_text(encoding="utf-8") == pm_task_wont_do.wont_do_status_text(_STATUS, *_ARGS)
