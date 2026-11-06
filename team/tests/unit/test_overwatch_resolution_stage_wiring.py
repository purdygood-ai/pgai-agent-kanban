"""
test_overwatch_resolution_stage_wiring.py
============================================
Structural guards for the OVERWATCH resolution stage wiring: the stage must
be reached through the production overwatch wake entry point, not a
separate or duplicated call path.

An overwatch wake (`wake-batch.sh --agent=overwatch`, reaching whichever
wake provider sibling is active: scripts/wake/claude.sh or
scripts/wake/codex.sh) runs `run_overwatch_resolution_stage`, the shared
wake_project.sh function (sourced transitively via wake_common.sh) that
invokes `pgai_agent_kanban.overwatch.resolution_stage`. The on-BLOCK nudge
(team/tests/unit/test_on_block_overwatch_trigger.py) runs block triage
rather than a wake; block triage hands a ship-policy block to the same
resolution stage. This file proves the wake hop: the call is present in
BOTH provider siblings' `run_project_chain`, with a byte-identical call-site
block. The question a lane parks in the operator inbox resumes with the
unhalt.sh an installed kanban carries, built through
halt_provenance.unhalt_command; the inbox close and halt-after commands the
stage prints name the installed inbox.sh and halt-after.sh, built through
halt_provenance.installed_script_command, so each runs as printed on a
kanban root whose path contains a space.
"""

from __future__ import annotations

import pathlib
import textwrap

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
# Both siblings' run_project_chain (and this call site within it) now live
# in the one shared wake driver (v2.9.20-engine-decomposition moved both
# claude.sh's and codex.sh's own copies onto it — both wake scripts source
# this one file instead of carrying their own inline copy). Both constants
# below point at the one shared file the call site now lives in.
_CLAUDE_SH = _TEAM_DIR / "scripts" / "lib" / "wake_driver.sh"
_CODEX_SH = _TEAM_DIR / "scripts" / "lib" / "wake_driver.sh"
_WAKE_PROJECT_SH = _TEAM_DIR / "scripts" / "lib" / "wake_project.sh"

_MARKER = "Autonomous block-resolution stage"


def _extract_call_block(path: pathlib.Path) -> list:
    """Extract the marker comment line plus the following non-blank lines up
    to (and including) the run_overwatch_resolution_stage call."""
    lines = path.read_text(encoding="utf-8").splitlines()
    block: list = []
    capturing = False
    for line in lines:
        if _MARKER in line:
            capturing = True
        if capturing:
            block.append(line)
            if "run_overwatch_resolution_stage" in line:
                break
    return block


def test_claude_sh_calls_run_overwatch_resolution_stage_in_run_project_chain() -> None:
    content = _CLAUDE_SH.read_text(encoding="utf-8")
    assert "run_overwatch_resolution_stage" in content
    block = _extract_call_block(_CLAUDE_SH)
    assert block, f"call-site marker '{_MARKER}' not found in {_CLAUDE_SH}"


def test_codex_sh_calls_run_overwatch_resolution_stage_in_run_project_chain() -> None:
    content = _CODEX_SH.read_text(encoding="utf-8")
    assert "run_overwatch_resolution_stage" in content
    block = _extract_call_block(_CODEX_SH)
    assert block, f"call-site marker '{_MARKER}' not found in {_CODEX_SH}"


def test_call_site_blocks_are_byte_identical_between_siblings() -> None:
    """The sibling-equality gate: the call-site block claude.sh and codex.sh
    each carry must match exactly, mirroring the on-BLOCK trigger's own
    sibling discipline (test_on_block_overwatch_trigger.py)."""
    claude_block = _extract_call_block(_CLAUDE_SH)
    codex_block = _extract_call_block(_CODEX_SH)
    assert claude_block == codex_block, (
        "run_overwatch_resolution_stage call-site blocks differ between siblings.\n"
        "claude.sh block:\n" + "\n".join(claude_block) + "\n\n"
        "codex.sh block:\n" + "\n".join(codex_block)
    )


def test_call_site_precedes_reconcile_stale_active_in_both_siblings() -> None:
    """Structural: the resolution-stage call must run once per project,
    unconditionally (before task dispatch begins), so it fires even when
    the overwatch task queue is empty — matching the "no-op unless there is
    a ship-policy HALT to evaluate" contract the stage itself implements.
    Anchored on `reconcile_stale_active`, the first call after the dev-tree
    gate in both siblings' run_project_chain.
    """
    for path in (_CLAUDE_SH, _CODEX_SH):
        content = path.read_text(encoding="utf-8")
        call_idx = content.index('run_overwatch_resolution_stage "$project_name"')
        reconcile_idx = content.index("reconcile_stale_active", call_idx)
        assert call_idx < reconcile_idx, (
            f"run_overwatch_resolution_stage call does not precede " f"reconcile_stale_active in {path}"
        )


def test_run_overwatch_resolution_stage_defined_once_in_wake_project() -> None:
    """The function body lives in exactly one place (wake_project.sh), shared
    by both siblings — not duplicated per-sibling. This is what keeps the
    two call sites trivially byte-identical: they invoke the same shared
    function rather than each carrying their own copy of the logic."""
    content = _WAKE_PROJECT_SH.read_text(encoding="utf-8")
    assert content.count("run_overwatch_resolution_stage() {") == 1

    for path in (_CLAUDE_SH, _CODEX_SH):
        sibling_content = path.read_text(encoding="utf-8")
        assert "run_overwatch_resolution_stage() {" not in sibling_content, (
            f"{path} must not redefine run_overwatch_resolution_stage — it "
            f"should only call the shared wake_project.sh function."
        )


def test_wake_project_function_is_a_no_op_for_non_overwatch_agents() -> None:
    """Structural: the guard clause `[[ "$AGENT" == "overwatch" ]] || return 0`
    must be the first statement in the function body, so every other agent
    (pm, coder, writer, tester, cm) is unaffected."""
    content = _WAKE_PROJECT_SH.read_text(encoding="utf-8")
    start = content.index("run_overwatch_resolution_stage() {")
    end = content.index("\n}\n", start)
    body = content[start:end]
    assert '[[ "$AGENT" == "overwatch" ]] || return 0' in body


def test_pp_run_ops_invokes_the_resolution_stage_module() -> None:
    content = _WAKE_PROJECT_SH.read_text(encoding="utf-8")
    start = content.index("run_overwatch_resolution_stage() {")
    end = content.index("\n}\n", start)
    body = content[start:end]
    assert "pp_run_ops pgai_agent_kanban.overwatch.resolution_stage" in body
    assert "--project" in body
    assert "--kanban-root" in body


# ---------------------------------------------------------------------------
# Behaviour: the drill matches an RC's tasks by an exact version token, and
# the wake runs the stage under the project's firing lock
# ---------------------------------------------------------------------------


def _task(tasks_dir: pathlib.Path, task_id: str, readme: str, state: str, report: bool = False) -> None:
    d = tasks_dir / task_id
    (d / "artifacts").mkdir(parents=True)
    (d / "README.md").write_text(readme, encoding="utf-8")
    (d / "status.md").write_text(f"# Status\n\n## State\n{state}\n", encoding="utf-8")
    if report:
        (d / "artifacts" / "report.md").write_text("# Verification Report\n", encoding="utf-8")


def test_rc_task_set_never_includes_a_task_naming_only_a_longer_version(tmp_path) -> None:
    from pgai_agent_kanban.overwatch.resolution_stage import find_rc_task_ids

    tasks = tmp_path / "tasks"
    _task(tasks, "CODER-20260101-001-a", "# Task\n\n## Release Version\nv1.4.10\n", "BACKLOG")
    _task(tasks, "CODER-20260101-002-b", "# Task\n\n## Release Version\nv1.4.1\n", "BACKLOG")
    _task(
        tasks,
        "WRITER-20260101-003-c",
        "# Task\n\n## Inputs\n- requirements/v1.4.1-bugfix-bundle-20260101.md\n",
        "WORKING",
    )
    _task(tasks, "CODER-20260101-004-d", "# Task\n\nSee ai_v1.4.1.2 and v11.4.1.\n", "BACKLOG")

    assert find_rc_task_ids(str(tasks), "v1.4.1") == ["CODER-20260101-002-b", "WRITER-20260101-003-c"]
    assert find_rc_task_ids(str(tasks), "v1.4.10") == ["CODER-20260101-001-a"]


def test_tester_report_lookup_never_picks_a_longer_versions_report(tmp_path) -> None:
    from pgai_agent_kanban.overwatch.resolution_stage import find_tester_report

    tasks = tmp_path / "tasks"
    _task(tasks, "TESTER-20260101-005-verify-1-4-10", "# Task\n\n## Release Version\nv1.4.10\n", "DONE", report=True)

    assert find_tester_report(str(tasks), "v1.4.1") is None

    _task(tasks, "TESTER-20260101-004-verify-1-4-1", "# Task\n\n## Release Version\nv1.4.1\n", "DONE", report=True)
    found = find_tester_report(str(tasks), "v1.4.1")
    assert found is not None and "TESTER-20260101-004-verify-1-4-1" in found


def test_wake_skips_the_resolution_stage_while_another_firing_holds_the_lock(tmp_path) -> None:
    from tests.unit.shell_harness import run_bash

    root = tmp_path / "kanban"
    (root / "projects" / "demo" / "overwatch").mkdir(parents=True)
    (root / "projects" / "demo" / "overwatch" / "actions.log").write_text("", encoding="utf-8")
    (root / "locks").mkdir()
    (root / "tmp").mkdir()
    calls = tmp_path / "stage-calls"
    script = textwrap.dedent(f"""\
        set -euo pipefail
        log() {{ echo "LOG: $*"; }}
        pp_run_ops() {{ echo "$*" >> "{calls}"; }}
        source scripts/lib/lock_provenance.sh
        source scripts/lib/overwatch_lib.sh
        eval "$(awk '/^run_overwatch_resolution_stage\\(\\)/{{f=1}} f{{print}} f&&/^}}/{{exit}}' {_WAKE_PROJECT_SH})"
        eval "$(awk '/^_overwatch_wake_load_firing_lock\\(\\)/{{f=1}} f{{print}} f&&/^}}/{{exit}}' {_WAKE_PROJECT_SH})"
        AGENT=overwatch
        SCRIPT_DIR="$PWD/scripts/wake"
        overwatch_acquire_firing_lock demo   # another firing, already in progress
        run_overwatch_resolution_stage demo
        overwatch_release_firing_lock
        run_overwatch_resolution_stage demo  # free again: the stage runs
        echo STAGE_DONE
    """)

    result = run_bash(
        tmp_path,
        script,
        extra_env={
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
        },
    )

    assert "STAGE_DONE" in result.stdout, result.stderr
    assert calls.read_text(encoding="utf-8").count("pgai_agent_kanban.overwatch.resolution_stage") == 1
    log = (root / "logs" / "overwatch-resolution-stage.log").read_text(encoding="utf-8")
    assert "overwatch.firing_lock_held" in log
    assert "\tfiring-skipped:lock-held\t" in (root / "projects" / "demo" / "overwatch" / "actions.log").read_text(
        encoding="utf-8"
    )


def test_a_parked_lane_question_resumes_with_the_installed_unhalt_script(tmp_path) -> None:
    """The inbox question a lane parks names <root>/scripts/unhalt.sh, quoted for a root with a space."""
    import shlex

    from pgai_agent_kanban.ops.context import OpsContext
    from pgai_agent_kanban.overwatch import inbox
    from pgai_agent_kanban.overwatch.resolution_stage import _park_lane_question

    root = tmp_path / "kanban root"
    (root / "projects" / "demo").mkdir(parents=True)
    halt = root / "projects" / "demo" / "HALT"

    line, entry_id = _park_lane_question(str(root), "demo", "v1.0.0", "Which option?", str(halt), "C")

    assert line == f"inbox entry {entry_id} written"
    (entry,) = inbox.list_open(OpsContext(kanban_root=root), "demo")
    answer = entry.how_to_answer
    assert "team/scripts" not in answer
    prefix = "resume with "
    resume = answer.split(prefix, 1)[1].split(";", 1)[0]
    assert resume.startswith(f"'{root}/scripts/unhalt.sh'")
    assert shlex.split(resume) == [f"{root}/scripts/unhalt.sh", "--project", "demo"]


def test_the_inbox_close_and_halt_after_commands_name_the_installed_scripts_quoted(tmp_path) -> None:
    """Both commands name <root>/scripts/..., shell-quoted, for a root containing a space."""
    import shlex

    from pgai_agent_kanban.ops.context import OpsContext
    from pgai_agent_kanban.overwatch.resolution_stage import _halt_after_command, _inbox_close_command

    root = tmp_path / "kanban root"

    close = _inbox_close_command(OpsContext(kanban_root=root), "demo")
    command, prose = close.split(" followed by ", 1)
    assert prose == "this entry's INBOX ID"
    assert command.startswith(f"'{root}/scripts/inbox.sh'")
    assert shlex.split(command) == [f"{root}/scripts/inbox.sh", "--project", "demo", "close", "--id"]

    halt_after = _halt_after_command(str(root), "demo")
    assert halt_after.startswith(f"'{root}/scripts/halt-after.sh'")
    assert shlex.split(halt_after) == [f"{root}/scripts/halt-after.sh", "--project", "demo"]


def test_a_resumed_halt_after_entry_prints_runnable_commands(tmp_path, monkeypatch) -> None:
    """The inbox entry an auto-resumed HALT-AFTER leaves names both installed scripts, quoted."""
    import shlex

    from pgai_agent_kanban.lib.sentinel_provenance import record_sentinel_write
    from pgai_agent_kanban.ops.context import OpsContext
    from pgai_agent_kanban.overwatch import inbox
    from pgai_agent_kanban.overwatch.resolution_stage import EXECUTED, run_resolution_stage

    root = tmp_path / "kanban root"
    project = root / "projects" / "demo"
    (project / "tasks").mkdir(parents=True)
    (root / "tmp").mkdir()
    for name in ("PGAI_AGENT_KANBAN_ROOT_PATH", "KANBAN_ROOT"):
        monkeypatch.setenv(name, str(root))
    monkeypatch.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(root / "tmp"))
    for name in ("PGAI_TASKS_DIR", "PGAI_PROJECT_ROOT", "PGAI_PROJECT_NAME"):
        monkeypatch.delenv(name, raising=False)
    (root / "kanban.cfg").write_text("[overwatch]\nhalt_after_auto_resume = true\n", encoding="utf-8")
    (project / "project.cfg").write_text("[project]\nproject_name = demo\nbranch_prefix = ai_\n", encoding="utf-8")
    (project / "release-state.md").write_text(
        "# Release State\n\n## Active RC\nnone\n\n## RC Opened At\nnone\n\n"
        "## RC Opened By Task\nnone\n\n## Last Released\nv1.4.2\n",
        encoding="utf-8",
    )
    (project / "HALT").write_text("", encoding="utf-8")
    record_sentinel_write(root, project / "HALT", "promote")

    outcome = run_resolution_stage("demo", str(root))

    assert outcome.status == EXECUTED, outcome.reason
    (entry,) = inbox.list_open(OpsContext(kanban_root=root), "demo")
    answer = entry.how_to_answer
    assert "team/scripts" not in answer
    halt_after = answer.split("arm ", 1)[1].split(" again;", 1)[0]
    assert shlex.split(halt_after) == [f"{root}/scripts/halt-after.sh", "--project", "demo"]
    close = answer.split("close this entry with ", 1)[1].split(" followed by ", 1)[0]
    assert shlex.split(close) == [f"{root}/scripts/inbox.sh", "--project", "demo", "close", "--id"]
