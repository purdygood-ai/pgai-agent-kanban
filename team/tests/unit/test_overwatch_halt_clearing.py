"""
test_overwatch_halt_clearing.py
===============================
Contract tests for how OVERWATCH's resolution stage
(``pgai_agent_kanban.overwatch.resolution_stage``) decides on a HALT by its
provenance (``halt_provenance``) and clears a chain HALT once its cause is
resolved (``halt_clearing``), in a throwaway kanban root.

What is pinned:

* An operator HALT -- empty from the operator verb, or with a body -- is
  decided ``none`` and its bytes are unchanged; a chain HALT beside an
  operator HALT is untouched too.
* A chain HALT whose cause is resolved is cleared through
  ``ops.write.unhalt`` (its sentinel-writes record says ``unhalt``), after a
  byte-identical backup, with a ``## OVERWATCH Decision`` event naming the
  evidence. Unresolved causes, triggers that clear on nothing, and a
  runaway HALT are never cleared.
* An empty HALT-AFTER promotion HALT reads as the chain's. A completed
  drain stays exactly as it is under ``halt_after_auto_resume = false`` and
  is cleared to an inbox entry with the real version under ``true``.
* resolution-matrix.yaml has a trigger row for every CM trigger number the
  ship policy defines (team/roles/CM.md and the trigger-N error keys the
  release scripts emit).
* No ``Needs Human raised`` outcome is left in the stage.
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess

import pytest

from pgai_agent_kanban.halt_after.promote import promote
from pgai_agent_kanban.lib.sentinel_provenance import latest_sentinel_record, record_sentinel_write
from pgai_agent_kanban.ops import write as ops_write
from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.overwatch import halt_clearing, inbox, resolution_matrix, resolution_stage
from pgai_agent_kanban.overwatch.halt_provenance import (
    WRITER_OVERWATCH_RUNAWAY,
    WRITER_ZERO_PROGRESS_CAP,
    classify_halt,
    provenance_lines,
)
from pgai_agent_kanban.overwatch.resolution_stage import (
    DECISION_NONE,
    DECISION_VERB,
    DECLINED,
    EXECUTED,
    classify_halt_file,
    clear_chain_halt,
    run_resolution_stage,
    write_inbox_question,
)
from tests.fixtures.binary_guard import require_binary

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_TEAM = _REPO_ROOT / "team"
_CM_ROLE = _TEAM / "roles" / "CM.md"
_STAGE_SOURCE = pathlib.Path(resolution_stage.__file__)

PROJECT = "demo"
VERSION = "v1.4.0"
HALT_TIME = "2026-03-01T10:00:00Z"
AFTER_HALT = "2026-03-01T11:00:00Z"
BEFORE_HALT = "2026-03-01T09:00:00Z"
TESTER_TASK = "TESTER-20260301-004-verify-1-4-0"


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def root(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """A throwaway kanban root with one project; every root variable points at it."""
    kanban_root = tmp_path / "kanban"
    (kanban_root / "projects" / PROJECT / "tasks").mkdir(parents=True)
    (kanban_root / "tmp").mkdir()
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(kanban_root))
    monkeypatch.setenv("KANBAN_ROOT", str(kanban_root))
    monkeypatch.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(kanban_root / "tmp"))
    monkeypatch.delenv("PGAI_TASKS_DIR", raising=False)
    monkeypatch.delenv("PGAI_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("PGAI_PROJECT_NAME", raising=False)
    _release_state(kanban_root, active=VERSION)
    (kanban_root / "projects" / PROJECT / "project.cfg").write_text(
        f"[project]\nproject_name = {PROJECT}\nbranch_prefix = ai_\n", encoding="utf-8"
    )
    return kanban_root


def _project(root: pathlib.Path) -> pathlib.Path:
    return root / "projects" / PROJECT


def _halt(root: pathlib.Path) -> pathlib.Path:
    return _project(root) / "HALT"


def _release_state(root: pathlib.Path, active: str, last: str = "none") -> None:
    (root / "projects" / PROJECT / "release-state.md").write_text(
        "# Release State\n\n"
        f"## Active RC\n{active}\n\n"
        "## RC Opened At\nnone\n\n"
        "## RC Opened By Task\nnone\n\n"
        f"## Last Released\n{last}\n",
        encoding="utf-8",
    )


def _cm_halt(root: pathlib.Path, trigger: str, key: str) -> bytes:
    """A CM chain HALT in the shape cm_halt's write_halt_file writes."""
    body = (
        f"# HALT created by CM at {HALT_TIME}\n"
        "# halt-writer: cm-release\n"
        f"# halt-reason: {key}\n"
        f"# halt-trigger: {trigger}\n"
        f"# error:   {key}\n"
        "# message: the release stopped\n"
        "# remedy:  scripts/cm/release.sh --project demo\n"
        "# Resolution: Operator review required. See CM task status.md for full reason.\n"
    )
    _halt(root).write_text(body, encoding="utf-8")
    return body.encode("utf-8")


def _chain_halt(root: pathlib.Path, writer: str, reason: str) -> bytes:
    lines = [f"# HALT created by {writer} at {HALT_TIME}", *provenance_lines(writer, reason)]
    body = "\n".join(lines) + "\n"
    _halt(root).write_text(body, encoding="utf-8")
    return body.encode("utf-8")


def _cancelled_record(root: pathlib.Path, closed_at: str, cargo: list) -> None:
    """A cancelled RC archived to history/, the shape cancel-rc leaves."""
    history = _project(root) / "release-state" / "history"
    history.mkdir(parents=True, exist_ok=True)
    record = {"attempt": 1, "cargo": cargo, "closed_at": closed_at, "outcome": "cancelled", "rc": VERSION}
    (history / f"{VERSION}-cancelled-20260301.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")


def _bug(root: pathlib.Path, name: str, status: str) -> None:
    bugs = _project(root) / "bugs"
    bugs.mkdir(exist_ok=True)
    (bugs / name).write_text(f"# {name}\n\n## Status\n{status}\n", encoding="utf-8")


def _task(root: pathlib.Path, task_id: str, state: str, version: str = VERSION) -> None:
    task_dir = _project(root) / "tasks" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "README.md").write_text(f"# Task\n\n## Release Version\n{version}\n", encoding="utf-8")
    (task_dir / "status.md").write_text(
        f"# Status\n\n## Task\n{task_id}\n\n## State\n{state}\n\n## Summary\nx\n", encoding="utf-8"
    )


def _rc_cancelled_and_rebundled(root: pathlib.Path) -> None:
    _release_state(root, active="none")
    _bug(root, "BUG-0007-example.md", "open")
    _cancelled_record(root, AFTER_HALT, ["BUG-0007-example.md"])


def _backups(root: pathlib.Path) -> list:
    return sorted((_project(root) / "overwatch" / "backups").rglob("HALT"))


def _decision_blocks(root: pathlib.Path) -> list:
    text = (_project(root) / "release-state.md").read_text(encoding="utf-8")
    return text.split("## OVERWATCH Decision")[1:]


def _auto_resume(root: pathlib.Path, value: str) -> None:
    (root / "kanban.cfg").write_text(f"[overwatch]\nhalt_after_auto_resume = {value}\n", encoding="utf-8")


def _assert_cleared_through_unhalt(root: pathlib.Path, before: bytes, outcome) -> None:
    assert outcome.status == EXECUTED, outcome.reason
    assert outcome.decision == DECISION_VERB
    assert not _halt(root).exists()
    record = latest_sentinel_record(_halt(root), kanban_root=root)
    assert record is not None and record.get("op") == "unhalt", record
    backups = _backups(root)
    assert len(backups) == 1 and backups[0].read_bytes() == before
    assert backups[0].parent.name == "project-root"
    blocks = _decision_blocks(root)
    assert len(blocks) == 1, blocks
    assert "Lane: chain-halt" in blocks[0]
    assert str(backups[0]) in blocks[0]
    assert "ops.write unhalt" in blocks[0]


def _assert_untouched(root: pathlib.Path, before: bytes, outcome) -> None:
    assert outcome.status == DECLINED, outcome.reason
    assert outcome.decision == DECISION_NONE
    assert _halt(root).read_bytes() == before
    assert _backups(root) == []
    assert _decision_blocks(root) == []


# ---------------------------------------------------------------------------
# Operator HALTs: decision none, bytes unchanged
# ---------------------------------------------------------------------------


def test_operator_halt_from_the_operator_verb_decides_none_and_is_untouched(root):
    ops_write.halt(OpsContext(kanban_root=root), PROJECT)
    _rc_cancelled_and_rebundled(root)  # every chain cause would read resolved
    before = _halt(root).read_bytes()

    outcome = run_resolution_stage(PROJECT, str(root))

    _assert_untouched(root, before, outcome)
    assert "operator HALT" in outcome.reason
    assert classify_halt_file(str(_halt(root))) == "operator"


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("# operator full-stop pending governance review\n", id="operator-body"),
        pytest.param("# halt-writer: someone-else\n# halt-reason: x\n", id="unknown-writer"),
        pytest.param("", id="empty-no-record"),
    ],
)
def test_any_operator_halt_body_decides_none(root, body):
    _halt(root).write_text(body, encoding="utf-8")
    _rc_cancelled_and_rebundled(root)
    before = _halt(root).read_bytes()

    _assert_untouched(root, before, run_resolution_stage(PROJECT, str(root)))


def test_operator_halt_at_the_root_leaves_a_resolved_chain_halt_alone(root):
    before = _cm_halt(root, "5", "cm-release.trigger-5.squash-conflict")
    _rc_cancelled_and_rebundled(root)
    (root / "HALT").write_text("", encoding="utf-8")

    outcome = run_resolution_stage(PROJECT, str(root))

    _assert_untouched(root, before, outcome)
    assert "operator-authored HALT" in outcome.reason
    assert (root / "HALT").read_bytes() == b""


# ---------------------------------------------------------------------------
# CM chain HALTs: cleared once the cause is resolved
# ---------------------------------------------------------------------------


def test_cm_halt_whose_rc_was_cancelled_and_rebundled_is_cleared_backed_up_and_logged(root):
    before = _cm_halt(root, "5", "cm-release.trigger-5.squash-conflict")
    _rc_cancelled_and_rebundled(root)

    outcome = run_resolution_stage(PROJECT, str(root))

    _assert_cleared_through_unhalt(root, before, outcome)
    block = _decision_blocks(root)[0]
    assert "rc-cancelled" in block and "CM trigger 5 (squash-conflict)" in block
    log = (_project(root) / "overwatch" / "actions.log").read_text(encoding="utf-8")
    assert "auto-fix:clear-chain-halt" in log


def test_cm_halt_stays_while_cargo_still_reads_running(root):
    before = _cm_halt(root, "5", "cm-release.trigger-5.squash-conflict")
    _release_state(root, active="none")
    _bug(root, "BUG-0007-example.md", "running")
    _cancelled_record(root, AFTER_HALT, ["BUG-0007-example.md"])

    outcome = run_resolution_stage(PROJECT, str(root))

    _assert_untouched(root, before, outcome)
    assert "cargo still reads running" in outcome.reason


def test_cm_halt_stays_when_the_cancel_predates_the_halt(root):
    before = _cm_halt(root, "4", "cm-release.trigger-4.pre-squash-hook-failed")
    _release_state(root, active="none")
    _cancelled_record(root, BEFORE_HALT, [])

    _assert_untouched(root, before, run_resolution_stage(PROJECT, str(root)))


def test_cm_halt_stays_while_the_rc_is_still_active(root):
    before = _cm_halt(root, "10", "cm-release.trigger-10.pollution-guard-strict")

    outcome = run_resolution_stage(PROJECT, str(root))

    _assert_untouched(root, before, outcome)
    assert "waits for the operator" in outcome.reason


@pytest.mark.parametrize("trigger, key", [
    pytest.param("8", "cm-release.trigger-8.last-three-non-functional", id="trigger-8"),
    pytest.param("5b", "cm-release.trigger-5b.tag-points-at-wrong-commit", id="trigger-5b"),
    pytest.param("11", "cm-release.trigger-11.fidelity-gate-divergence", id="trigger-11"),
])
def test_triggers_that_clear_on_nothing_are_never_cleared(root, trigger, key):
    before = _cm_halt(root, trigger, key)
    _rc_cancelled_and_rebundled(root)

    outcome = run_resolution_stage(PROJECT, str(root))

    _assert_untouched(root, before, outcome)
    assert "no cause check clears this trigger" in outcome.reason


def test_trigger_1_halt_is_cleared_once_the_tester_task_is_reset(root):
    before = _cm_halt(root, "1", "cm-release.trigger-1.tester-blocked")
    _task(root, "TESTER-20260301-001-verify-1-4-0", "WONT-DO")
    _task(root, TESTER_TASK, "BACKLOG")

    outcome = run_resolution_stage(PROJECT, str(root))

    _assert_cleared_through_unhalt(root, before, outcome)
    assert f"{TESTER_TASK} was reset and reads BACKLOG" in _decision_blocks(root)[0]


# ---------------------------------------------------------------------------
# Other chain writers
# ---------------------------------------------------------------------------


def test_runaway_halt_is_never_cleared_by_the_stage(root):
    before = _chain_halt(root, WRITER_OVERWATCH_RUNAWAY, "runaway.rc_cycles_per_day: 3 RC cycles within 1 day")
    _rc_cancelled_and_rebundled(root)
    _task(root, TESTER_TASK, "BACKLOG")
    assert classify_halt(_halt(root), kanban_root=root).is_chain

    outcome = run_resolution_stage(PROJECT, str(root))

    _assert_untouched(root, before, outcome)
    assert "never clears a runaway HALT" in outcome.reason


@pytest.mark.parametrize("state, cleared", [("BACKLOG", True), ("BLOCKED", False)])
def test_zero_progress_cap_halt_clears_once_its_task_is_reset(root, state, cleared):
    task = "CODER-20260301-002-fix-thing"
    _task(root, task, state)
    before = _chain_halt(root, WRITER_ZERO_PROGRESS_CAP, f"zero-progress cap reached on {task}")

    outcome = run_resolution_stage(PROJECT, str(root))

    if cleared:
        _assert_cleared_through_unhalt(root, before, outcome)
    else:
        _assert_untouched(root, before, outcome)


# ---------------------------------------------------------------------------
# HALT-AFTER: provenance of an empty promotion; auto-resume on and off
# ---------------------------------------------------------------------------


def test_empty_halt_after_promotion_halt_is_chain_not_operator(root):
    _halt(root).write_text("", encoding="utf-8")
    record_sentinel_write(root, _halt(root), "promote")

    provenance = classify_halt(_halt(root), kanban_root=root)
    assert provenance.is_chain and provenance.writer == "halt-after-promotion"
    assert classify_halt_file(str(_halt(root))) == "chain"


@pytest.mark.parametrize("cfg", [None, "false"], ids=["key-absent", "false"])
def test_completed_drain_stays_exactly_as_today_without_auto_resume(root, cfg):
    if cfg is not None:
        _auto_resume(root, cfg)
    (_project(root) / "HALT-AFTER").write_text(f"rc:{VERSION}\n", encoding="utf-8")
    _release_state(root, active="none", last=VERSION)
    promote(_project(root), f"rc:{VERSION}")
    before = _halt(root).read_bytes()

    outcome = run_resolution_stage(PROJECT, str(root))

    _assert_untouched(root, before, outcome)
    assert "halt_after_auto_resume is false" in outcome.reason
    assert not (_project(root) / "operator-inbox.md").exists()


def test_completed_drain_with_auto_resume_is_cleared_with_the_inbox_entry(root):
    _auto_resume(root, "true")
    (_project(root) / "HALT-AFTER").write_text(f"rc:{VERSION}\n", encoding="utf-8")
    _release_state(root, active="none", last="v1.4.1")  # the token's version wins
    promote(_project(root), f"rc:{VERSION}")
    before = _halt(root).read_bytes()

    outcome = run_resolution_stage(PROJECT, str(root))

    _assert_cleared_through_unhalt(root, before, outcome)
    entries = inbox.list_open(OpsContext(kanban_root=root), PROJECT)
    assert len(entries) == 1
    assert entries[0].question == f"stopped after rc:{VERSION} as armed; resume with unhalt"
    assert entries[0].source == f"rc:{VERSION}"
    assert outcome.inbox_entry_id == entries[0].entry_id


def test_empty_promotion_with_auto_resume_names_the_last_released_version(root):
    _auto_resume(root, "true")
    _release_state(root, active="none", last="v1.4.2")
    _halt(root).write_text("", encoding="utf-8")
    record_sentinel_write(root, _halt(root), "promote")

    outcome = run_resolution_stage(PROJECT, str(root))

    _assert_cleared_through_unhalt(root, b"", outcome)
    entries = inbox.list_open(OpsContext(kanban_root=root), PROJECT)
    assert [e.question for e in entries] == ["stopped after rc:v1.4.2 as armed; resume with unhalt"]


def test_unfinished_promotion_is_left_even_with_auto_resume(root):
    _auto_resume(root, "true")
    _release_state(root, active="none", last=VERSION)
    (_project(root) / "HALT-AFTER").write_text(f"rc:{VERSION}\n", encoding="utf-8")
    promote(_project(root), f"rc:{VERSION}")
    (_project(root) / "HALT-AFTER").write_text(f"rc:{VERSION}\n", encoding="utf-8")
    before = _halt(root).read_bytes()

    _assert_untouched(root, before, run_resolution_stage(PROJECT, str(root)))


# ---------------------------------------------------------------------------
# The one clearing path refuses a HALT it did not decide on
# ---------------------------------------------------------------------------


def test_clear_refuses_a_halt_whose_bytes_changed(root):
    decided = _cm_halt(root, "5", "cm-release.trigger-5.squash-conflict")
    _halt(root).write_text(decided.decode() + "# operator note: keep this\n", encoding="utf-8")
    before = _halt(root).read_bytes()

    result = clear_chain_halt(OpsContext(kanban_root=root), str(root), PROJECT, step="clear", expected=decided)

    assert not result.cleared and _halt(root).read_bytes() == before
    assert "overwatch.chain-halt-changed" in result.error and "remedy" in result.error
    assert _backups(root) == []


def test_clear_refuses_an_operator_halt(root):
    ops_write.halt(OpsContext(kanban_root=root), PROJECT)

    result = clear_chain_halt(OpsContext(kanban_root=root), str(root), PROJECT, step="clear")

    assert not result.cleared and _halt(root).is_file()
    assert "reads as the operator's" in result.error


# ---------------------------------------------------------------------------
# Trigger 9 re-checked green on the RC branch
# ---------------------------------------------------------------------------


def _dev_tree(root: pathlib.Path, notes: str) -> pathlib.Path:
    require_binary("git", label="halt-clearing-notes-recheck")
    repo = root.parent / "dev"
    repo.mkdir()

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    git("init", "-q", "-b", "ai_main")
    git("config", "user.email", "t@example.invalid")
    git("config", "user.name", "t")
    (repo / "README.md").write_text("x\n")
    git("add", "README.md")
    git("commit", "-q", "-m", "init")
    git("checkout", "-q", "-b", f"ai_rc/{VERSION}")
    (repo / "release-notes").mkdir()
    (repo / "release-notes" / f"{VERSION}.md").write_text(notes)
    git("add", "release-notes")
    git("commit", "-q", "-m", "notes")
    git("checkout", "-q", "ai_main")
    (_project(root) / "project.cfg").write_text(
        f"[project]\nproject_name = {PROJECT}\nbranch_prefix = ai_\ndev_tree_path = {repo}\n", encoding="utf-8"
    )
    return repo


@pytest.mark.parametrize("notes, cleared", [
    pytest.param(f"# {VERSION}\n\n## Status\nPENDING-RELEASE\n\n## Notes\nx\n", True, id="status-heading"),
    pytest.param(f"# {VERSION}\n\n## Notes\nx\n", False, id="no-status-heading"),
])
def test_trigger_9_is_cleared_once_the_notes_recheck_green(root, notes, cleared):
    _dev_tree(root, notes)
    before = _cm_halt(root, "9", "cm-release.trigger-9.notes-missing-status-heading")

    outcome = run_resolution_stage(PROJECT, str(root))

    if cleared:
        _assert_cleared_through_unhalt(root, before, outcome)
        assert "notes-status-green" in _decision_blocks(root)[0]
    else:
        _assert_untouched(root, before, outcome)


# ---------------------------------------------------------------------------
# The matrix: one row per CM trigger number
# ---------------------------------------------------------------------------


def _ship_policy_trigger_numbers() -> set:
    """Every trigger number CM.md's trigger table and the release scripts' error keys define."""
    text = _CM_ROLE.read_text(encoding="utf-8")
    table = text.split("### The triggers", 1)[1].split("###", 1)[0]
    numbers = {m.group(1).lower() for m in re.finditer(r"^\|\s*(\d+[a-z]?)\s*\|", table, re.MULTILINE)}
    assert len(numbers) >= 9, numbers
    key_re = re.compile(r"cm-release\.trigger-(\d+[a-zA-Z]?)\.[a-z]")
    for path in (_TEAM / "scripts").rglob("*.sh"):
        numbers |= {m.group(1).lower() for m in key_re.finditer(path.read_text(encoding="utf-8", errors="replace"))}
    return numbers


def test_resolution_matrix_has_a_row_for_every_cm_trigger_number():
    rows = resolution_matrix.load_trigger_rows()
    defined = _ship_policy_trigger_numbers()
    assert {"1", "5b", "9", "10", "11"} <= defined
    missing = sorted(defined - set(rows))
    assert not missing, f"resolution-matrix.yaml has no trigger row for CM trigger(s) {missing}"


def test_a_malformed_trigger_row_refuses_with_the_contract_and_clears_nothing(root):
    shipped = pathlib.Path(resolution_matrix.__file__).with_name("resolution-matrix.yaml").read_text()
    (root / "resolution-matrix.yaml").write_text(shipped.replace("rc-cancelled]", "rc-canceled]", 1))
    with pytest.raises(resolution_matrix.MatrixError) as raised:
        resolution_matrix.load_trigger_rows(kanban_root=str(root))
    assert "resolution_matrix.bad_trigger_row" in str(raised.value)
    before = _cm_halt(root, "5", "cm-release.trigger-5.squash-conflict")
    _rc_cancelled_and_rebundled(root)

    outcome = run_resolution_stage(PROJECT, str(root))

    _assert_untouched(root, before, outcome)
    assert "resolution matrix error" in outcome.reason


# ---------------------------------------------------------------------------
# Inbox instead of Needs Human
# ---------------------------------------------------------------------------


def test_stage_source_raises_no_needs_human():
    assert "Needs Human raised" not in _STAGE_SOURCE.read_text(encoding="utf-8")


def test_an_open_question_is_written_once(root):
    ctx = OpsContext(kanban_root=root)
    fields = dict(source=f"rc:{VERSION}", question="Cancel or keep?", meanwhile="kept going", how_to_answer="answer")
    first = write_inbox_question(ctx, PROJECT, **fields)
    second = write_inbox_question(ctx, PROJECT, **fields)
    assert first[1] is True and second[1] is False and first[0] == second[0]
    assert len(inbox.list_open(ctx, PROJECT)) == 1


# ---------------------------------------------------------------------------
# The cause-check module's own command line
# ---------------------------------------------------------------------------


def test_cause_check_cli_reports_the_check(root):
    _cm_halt(root, "5", "cm-release.trigger-5.squash-conflict")
    _rc_cancelled_and_rebundled(root)
    done = subprocess.run(
        ["python3", halt_clearing.__file__, "check", "--project-root", str(_project(root)), "--check", "rc-cancelled"],
        capture_output=True, text=True, check=False,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.startswith("passed\trc-cancelled\t")
