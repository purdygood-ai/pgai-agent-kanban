"""
test_wake_cm_role_reminder.py
=============================
The CM reminder the wake prompt carries (wd_role_reminder in
team/scripts/lib/wake_driver_prep.sh) states release.sh's exit-code
contract instead of the refusals the release no longer makes.

Coverage:
  - Exit 4 tells CM to leave the release task WAITING (verification
    requeued, or still pending after the RC's one requeue) and not to re-run
    release.sh in the same session; exit 5 says the RC was cancelled and
    rebundled and the task is WONT-DO; exit 6 says one inbox entry names the
    remote tag, with no BLOCKED and no HALT; a refused push that exits 0
    with a Push Lag Event is a ship.
  - The refusal clause names no TESTER state, squash conflict, push
    failure or remote tag.
  - wd_prepare_task, run for a CM task with its collaborators stubbed,
    puts exactly that reminder in role_reminder.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import textwrap

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

# This file lives at team/tests/unit/; team/ is three parents up.
_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent
_LIB = _TEAM_DIR / "scripts" / "lib"
# provenance-allowlist: synthetic fixture task ID (test DATA the wake reads).
_CM_TASK_ID = "CM-20260101-001-release"


def _run(tmp_path: pathlib.Path, body: str) -> subprocess.CompletedProcess:
    (tmp_path / "home").mkdir(exist_ok=True)
    script = textwrap.dedent(f"""\
        set -euo pipefail
        cd '{_TEAM_DIR}'
        log() {{ echo "[test] wake: $*" >&2; }}
        source '{_LIB}/wake_readme_meta.sh'
        source '{_LIB}/wake_driver_prep.sh'
        """) + body
    env = build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_env={"PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path), "PGAI_AGENT_KANBAN_ROOT_PATH": str(tmp_path)},
    )
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60, env=env)
    assert result.returncode == 0, f"rc={result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}"
    return result


def _cm_reminder(tmp_path: pathlib.Path) -> str:
    return _run(tmp_path, "wd_role_reminder cm\n").stdout.strip()


def _exit_segments(reminder: str) -> dict:
    """Each 'Exit N...' sentence group of *reminder*, keyed by N."""
    return {m.group(1): m.group(0) for m in re.finditer(r"Exit\s+(\d)\b.*?(?=Exit\s+\d\b|\Z)", reminder, re.S)}


def test_cm_reminder_names_each_release_exit_outcome(tmp_path: pathlib.Path) -> None:
    reminder = _cm_reminder(tmp_path)
    segments = _exit_segments(reminder)
    assert set(segments) >= {"4", "5", "6", "0"}, reminder

    assert "WAITING" in segments["4"] and "never BLOCKED" in segments["4"], segments["4"]
    assert "requeued" in segments["4"] and "still pending" in segments["4"], segments["4"]
    assert "never re-run it in the same session" in reminder, reminder

    assert "cancelled" in segments["5"] and "rebundled" in segments["5"], segments["5"]
    assert "WONT-DO" in segments["5"], segments["5"]

    assert "remote" in segments["6"] and "inbox entry" in segments["6"], segments["6"]
    assert "no BLOCKED and no HALT" in segments["6"], segments["6"]

    assert "Push Lag Event" in segments["0"] and "is a ship" in segments["0"], segments["0"]


def test_cm_reminder_refuses_on_no_tester_state_or_release_mechanics(tmp_path: pathlib.Path) -> None:
    reminder = _cm_reminder(tmp_path)
    clause = reminder.split("Refuse only when", 1)[1].split("The matrix", 1)[0]
    for word in ("tester", "squash", "push", "tag"):
        assert word not in clause.lower(), f"the refusal clause names {word!r}: {clause}"
    assert "TESTER state is BLOCKED" not in reminder
    assert "squash conflict, push fail, tag exists" not in reminder


def test_prepare_task_carries_the_cm_reminder_for_a_cm_task(tmp_path: pathlib.Path) -> None:
    task_dir = tmp_path / "projects" / "demo" / "tasks" / _CM_TASK_ID
    task_dir.mkdir(parents=True)
    (task_dir / "README.md").write_text(f"# Task\n\n## Task ID\n{_CM_TASK_ID}\n\n## Role\nCM\n", encoding="utf-8")
    (task_dir / "status.md").write_text("# Status\n\n## State\nBACKLOG\n", encoding="utf-8")
    body = textwrap.dedent(f"""\
        get_state() {{ echo BACKLOG; }}
        transition_task_state() {{ :; }}
        resolve_role_effort() {{ echo '|none'; }}
        check_prerequisites() {{ return 0; }}
        mark_backlog() {{ :; }}
        task_id='{_CM_TASK_ID}' task_readme='{task_dir / "README.md"}' task_status='{task_dir / "status.md"}'
        AGENT=cm ACTIVE_PROVIDER=claude _CURRENT_PROJECT=demo KANBAN_ROOT='{tmp_path}' _debug_gate=false
        TEAM_ROOT='{tmp_path}' PGAI_PROJECT_ROOT='{tmp_path / "projects" / "demo"}'
        role='' SUBAGENT='' selected_model='' model_source='' selected_effort='' effort_source=''
        task_debug_log='' LAST_TASK_FINAL_STATE=''
        wd_prepare_task
        echo "subagent=$SUBAGENT final=[$LAST_TASK_FINAL_STATE]"
        printf '%s\\n' "$role_reminder"
        """)
    lines = _run(tmp_path, body).stdout.strip().splitlines()
    assert lines[0] == "subagent=cm final=[]", lines
    assert "\n".join(lines[1:]) == _cm_reminder(tmp_path)
