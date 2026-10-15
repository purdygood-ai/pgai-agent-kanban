"""
test_task_status_reason_codes.py
================================
The status-writer tool keeps a task's ``## Block Record`` in step with its
state:

  - a BLOCKED write records the block, with ``--reason-code`` from the
    closed agent list (``agent_unclassified`` when omitted) and owner_role
    taken from the task's ``## Role``; ``agent_operator_data_action``
    carries typed ``--action-args`` in a parseable evidence line;
  - an unknown code, malformed or unknown action arguments, or the flags in
    a combination that does not fit, refuse dual-channel and leave status.md
    byte-identical;
  - DONE and WONT-DO remove the record and leave every other section
    byte-identical;
  - callers that pass none of the new flags see no change on any state but
    BLOCKED.

The CLI cases run the real ``team/scripts/task-status.sh`` against a sandbox
kanban root under pytest's tmp_path (inside the framework temp directory)
with an environment built from an explicit set; nothing touches the live
install.

Naming: function names describe behaviour under test, never bug IDs or task
IDs (roles/TESTER.md "Test Authoring Guidelines").
"""

from __future__ import annotations

import dataclasses
import pathlib
import subprocess

import pytest

from pgai_agent_kanban.overwatch.block_record import (
    load_block_record,
    parse_evidence,
    read_action_args,
)
from pgai_agent_kanban.task_status import (
    StatusWriteResult,
    TaskStatusError,
    write_task_status,
)
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

# This file lives at team/tests/unit/; team/ is three parents up.
_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent
_WRAPPER = _TEAM_DIR / "scripts" / "task-status.sh"
_PROJECT = "demo"
_TASK_ID = "CODER-20260101-001-example-task"

_ACTION_ARGS = [
    "verb=restamp-fixed-in",
    "project=pgai-agent-kanban",
    "version=v2.28.0",
    "bugs=BUG-0412,BUG-0413",
]

_STATUS_TEXT = (
    "# Status\n"
    "\n"
    "## Task\n"
    f"{_TASK_ID}\n"
    "\n"
    "## Participant\n"
    "Claude\n"
    "\n"
    "## Role\n"
    "{status_role}\n"
    "\n"
    "## State\n"
    "WORKING\n"
    "\n"
    "## Summary\n"
    "Working on it.\n"
    "\n"
    "## Artifacts\n"
    "- src/example.py\n"
    "\n"
    "## Blockers\n"
    "none\n"
    "\n"
    "## Needs Human\n"
    "no\n"
    "\n"
    "## Next Recommended Step\n"
    "Keep going.\n"
    "\n"
    "## Instruction Conflicts\n"
    "none\n"
)

# A record placed between two sections on purpose: removal must close the
# gap without disturbing either neighbour.
_RECORD_BODY = (
    "reason_code: per_task_timeout\n"
    "owner_role: CODER\n"
    "attempts: 1\n"
    "first_blocked_at: 2026-01-01T00:00:00+00:00\n"
    "last_blocked_at: 2026-01-01T00:05:00+00:00\n"
    "cooldown_until: none\n"
    "evidence: worktree=gone; branch=ai_feature/example; ahead=2; merged=no\n"
    "resume: branch\n"
)


@dataclasses.dataclass
class Sandbox:
    """A throwaway kanban root holding one task folder."""

    root: pathlib.Path

    @property
    def task_dir(self) -> pathlib.Path:
        return self.root / "projects" / _PROJECT / "tasks" / _TASK_ID

    @property
    def status(self) -> pathlib.Path:
        return self.task_dir / "status.md"

    def run(self, *args: str) -> subprocess.CompletedProcess:
        """Run the real wrapper against this sandbox; never the live install."""
        env = build_scrubbed_env(
            home_dir=self.root / "home",
            extra_env={
                "KANBAN_ROOT": str(self.root),
                "PGAI_AGENT_KANBAN_ROOT_PATH": str(self.root),
                "PGAI_AGENT_KANBAN_TEMP_DIR": str(self.root / "tmp"),
            },
        )
        return subprocess.run(
            ["bash", str(_WRAPPER), "--project", _PROJECT, "--key", _TASK_ID, *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )


def _make_sandbox(
    tmp_path: pathlib.Path,
    *,
    readme_role: str = "CODER",
    status_role: str = "CODER",
    state: str = "WORKING",
    record_between: bool = False,
) -> Sandbox:
    sb = Sandbox(tmp_path / "kanban")
    sb.task_dir.mkdir(parents=True)
    (sb.root / "home").mkdir()
    (sb.root / "tmp").mkdir()
    (sb.task_dir / "README.md").write_text(
        f"# Task: example\n\n## Role\n{readme_role}\n", encoding="utf-8"
    )
    text = _STATUS_TEXT.replace("{status_role}", status_role).replace(
        "## State\nWORKING\n", f"## State\n{state}\n"
    )
    if record_between:
        text = text.replace(
            "## Needs Human\n", f"## Block Record\n{_RECORD_BODY}\n## Needs Human\n"
        )
    sb.status.write_text(text, encoding="utf-8")
    return sb


def _assert_refused_unchanged(result: subprocess.CompletedProcess, before: bytes, sb: Sandbox, key: str) -> None:
    assert result.returncode != 0, result.stdout
    for field in ("error:", "message:", "remedy:"):
        assert field in result.stderr, f"missing {field!r} in stderr: {result.stderr}"
    assert key in result.stderr
    assert sb.status.read_bytes() == before, "a refused write changed status.md"


# ---------------------------------------------------------------------------
# BLOCKED writes record the block
# ---------------------------------------------------------------------------


def test_operator_data_action_block_records_code_role_and_typed_args(tmp_path: pathlib.Path) -> None:
    sb = _make_sandbox(tmp_path)
    result = sb.run(
        "--state", "BLOCKED", "--needs-human", "yes",
        "--reason-code", "agent_operator_data_action", "--action-args", *_ACTION_ARGS,
    )
    assert result.returncode == 0, result.stderr
    text = sb.status.read_text(encoding="utf-8")
    assert "## State\nBLOCKED\n" in text
    record = load_block_record(sb.status)
    assert record is not None
    assert record.reason_code == "agent_operator_data_action"
    assert record.owner_role == "CODER"
    assert read_action_args(record) == {
        "verb": "restamp-fixed-in",
        "project": "pgai-agent-kanban",
        "version": "v2.28.0",
        "bugs": "BUG-0412,BUG-0413",
    }
    assert parse_evidence(record.evidence)["bugs"].split(",") == ["BUG-0412", "BUG-0413"]
    assert "Block Record written: reason_code=agent_operator_data_action" in result.stdout


@pytest.mark.parametrize("role", ["CODER", "WRITER", "TESTER", "CM", "PM"])
def test_block_record_owner_role_is_the_tasks_readme_role(tmp_path: pathlib.Path, role: str) -> None:
    sb = _make_sandbox(tmp_path, readme_role=role, status_role="CODER")
    assert sb.run("--state", "BLOCKED").returncode == 0
    record = load_block_record(sb.status)
    assert record is not None and record.owner_role == role


def test_owner_role_falls_back_to_status_role_without_readme_role(tmp_path: pathlib.Path) -> None:
    sb = _make_sandbox(tmp_path, readme_role="", status_role="TESTER")
    assert sb.run("--state", "BLOCKED").returncode == 0
    record = load_block_record(sb.status)
    assert record is not None and record.owner_role == "TESTER"


def test_blocked_without_reason_code_records_agent_unclassified(tmp_path: pathlib.Path) -> None:
    sb = _make_sandbox(tmp_path)
    result = sb.run("--state", "BLOCKED", "--blockers", "Waiting on a credential.")
    assert result.returncode == 0, result.stderr
    record = load_block_record(sb.status)
    assert record is not None
    assert record.reason_code == "agent_unclassified"
    assert record.evidence == "none"


def test_blocked_rewrite_without_code_keeps_existing_record(tmp_path: pathlib.Path) -> None:
    sb = _make_sandbox(tmp_path, state="BLOCKED", record_between=True)
    result = sb.run("--state", "BLOCKED", "--summary", "Still blocked.")
    assert result.returncode == 0, result.stderr
    assert _RECORD_BODY in sb.status.read_text(encoding="utf-8")
    assert "kept" in result.stdout


def test_blocked_rewrite_with_code_reclassifies_and_keeps_first_blocked_at(tmp_path: pathlib.Path) -> None:
    sb = _make_sandbox(tmp_path, state="BLOCKED", record_between=True)
    assert sb.run("--state", "BLOCKED", "--reason-code", "agent_unclassified").returncode == 0
    record = load_block_record(sb.status)
    assert record is not None
    assert record.reason_code == "agent_unclassified"
    assert record.first_blocked_at == "2026-01-01T00:00:00+00:00"
    assert record.attempts == 1


def test_role_outside_owner_roles_writes_state_with_warning_and_no_record(tmp_path: pathlib.Path) -> None:
    sb = _make_sandbox(tmp_path, readme_role="PO", status_role="PO")
    result = sb.run("--state", "BLOCKED")
    assert result.returncode == 0, result.stderr
    assert "## State\nBLOCKED\n" in sb.status.read_text(encoding="utf-8")
    assert load_block_record(sb.status) is None
    assert "warning:" in result.stderr and "no Block Record written" in result.stderr


# ---------------------------------------------------------------------------
# Refusals: dual-channel, status.md byte-identical
# ---------------------------------------------------------------------------


def test_unknown_reason_code_refuses_and_writes_nothing(tmp_path: pathlib.Path) -> None:
    sb = _make_sandbox(tmp_path)
    before = sb.status.read_bytes()
    result = sb.run("--state", "BLOCKED", "--reason-code", "agent_made_up")
    _assert_refused_unchanged(result, before, sb, "task-status.unknown-reason-code")
    assert "agent_unclassified" in result.stderr  # the remedy names a real code


def test_wake_reason_code_is_not_an_agent_code(tmp_path: pathlib.Path) -> None:
    sb = _make_sandbox(tmp_path)
    before = sb.status.read_bytes()
    result = sb.run("--state", "BLOCKED", "--reason-code", "per_task_timeout")
    _assert_refused_unchanged(result, before, sb, "task-status.unknown-reason-code")


@pytest.mark.parametrize(
    "action_args",
    [
        pytest.param(["please restamp the fixed-in field"], id="prose-only"),
        pytest.param(["verb=restamp-fixed-in", "project=pgai-agent-kanban", "version=2.28", "bugs=BUG-0412"], id="malformed-version"),
        pytest.param(["verb=restamp-fixed-in", "project=pgai-agent-kanban", "version=v2.28.0", "bugs=BUG-412"], id="malformed-bug"),
        pytest.param(["verb=restamp-fixed-in", "project=pgai-agent-kanban", "version=v2.28.0"], id="missing-arg"),
        pytest.param([*_ACTION_ARGS, "force=yes"], id="unknown-key"),
        pytest.param([*_ACTION_ARGS, "version=v2.29.0"], id="repeated-key"),
        pytest.param(["verb=delete-everything", "project=pgai-agent-kanban"], id="unknown-verb"),
        pytest.param(["project=pgai-agent-kanban", "version=v2.28.0", "bugs=BUG-0412"], id="missing-verb"),
    ],
)
def test_invalid_action_args_refuse_and_write_nothing(tmp_path: pathlib.Path, action_args: list[str]) -> None:
    sb = _make_sandbox(tmp_path)
    before = sb.status.read_bytes()
    result = sb.run(
        "--state", "BLOCKED", "--reason-code", "agent_operator_data_action", "--action-args", *action_args,
    )
    _assert_refused_unchanged(result, before, sb, "task-status.invalid-action-args")


def test_action_args_without_operator_data_action_refuse(tmp_path: pathlib.Path) -> None:
    sb = _make_sandbox(tmp_path)
    before = sb.status.read_bytes()
    result = sb.run("--state", "BLOCKED", "--action-args", *_ACTION_ARGS)
    _assert_refused_unchanged(result, before, sb, "task-status.action-args-without-code")
    result = sb.run("--state", "BLOCKED", "--reason-code", "agent_unclassified", "--action-args", *_ACTION_ARGS)
    _assert_refused_unchanged(result, before, sb, "task-status.action-args-without-code")


def test_operator_data_action_without_action_args_refuses(tmp_path: pathlib.Path) -> None:
    sb = _make_sandbox(tmp_path)
    before = sb.status.read_bytes()
    result = sb.run("--state", "BLOCKED", "--reason-code", "agent_operator_data_action")
    _assert_refused_unchanged(result, before, sb, "task-status.missing-action-args")


@pytest.mark.parametrize("state", ["DONE", "WORKING", "BACKLOG"])
def test_reason_code_on_a_state_other_than_blocked_refuses(tmp_path: pathlib.Path, state: str) -> None:
    sb = _make_sandbox(tmp_path)
    before = sb.status.read_bytes()
    result = sb.run("--state", state, "--reason-code", "agent_unclassified")
    _assert_refused_unchanged(result, before, sb, "task-status.reason-code-needs-blocked")


# ---------------------------------------------------------------------------
# Terminal states remove the record; every other section byte-identical
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("state", ["DONE", "WONT-DO"])
def test_terminal_state_removes_block_record_and_nothing_else(tmp_path: pathlib.Path, state: str) -> None:
    sb = _make_sandbox(tmp_path, state="BLOCKED", record_between=True)
    original = sb.status.read_text(encoding="utf-8")
    result = sb.run("--state", state)
    assert result.returncode == 0, result.stderr
    expected = original.replace(f"## Block Record\n{_RECORD_BODY}\n", "").replace(
        "## State\nBLOCKED\n", f"## State\n{state}\n"
    )
    assert sb.status.read_text(encoding="utf-8") == expected
    assert "Block Record removed" in result.stdout


def test_terminal_state_removes_a_record_that_ends_the_file(tmp_path: pathlib.Path) -> None:
    sb = _make_sandbox(tmp_path)
    original = sb.status.read_text(encoding="utf-8")
    assert sb.run("--state", "BLOCKED").returncode == 0
    assert load_block_record(sb.status) is not None
    assert sb.run("--state", "DONE").returncode == 0
    assert sb.status.read_text(encoding="utf-8") == original.replace("## State\nWORKING\n", "## State\nDONE\n")


@pytest.mark.parametrize("state", ["BACKLOG", "WAITING", "WORKING"])
def test_non_terminal_non_blocked_write_leaves_record_untouched(tmp_path: pathlib.Path, state: str) -> None:
    sb = _make_sandbox(tmp_path, state="BLOCKED", record_between=True)
    original = sb.status.read_text(encoding="utf-8")
    assert sb.run("--state", state).returncode == 0
    assert sb.status.read_text(encoding="utf-8") == original.replace("## State\nBLOCKED\n", f"## State\n{state}\n")


def test_done_on_a_task_without_record_is_unchanged_behaviour(tmp_path: pathlib.Path) -> None:
    sb = _make_sandbox(tmp_path)
    original = sb.status.read_text(encoding="utf-8")
    result = sb.run("--state", "DONE")
    assert result.returncode == 0
    assert sb.status.read_text(encoding="utf-8") == original.replace("## State\nWORKING\n", "## State\nDONE\n")
    assert "Block Record" not in result.stdout


# ---------------------------------------------------------------------------
# Library entry point and --help
# ---------------------------------------------------------------------------


def test_write_task_status_returns_what_it_did_with_the_record(tmp_path: pathlib.Path) -> None:
    sb = _make_sandbox(tmp_path)
    blocked = write_task_status(sb.status, state="BLOCKED", reason_code="AGENT-UNCLASSIFIED")
    assert isinstance(blocked, StatusWriteResult)
    assert blocked.block_record is not None
    assert blocked.block_record.reason_code == "agent_unclassified"
    done = write_task_status(sb.status, state="DONE")
    assert done.block_record_removed is True
    assert load_block_record(sb.status) is None


def test_library_refusal_raises_before_any_write(tmp_path: pathlib.Path) -> None:
    sb = _make_sandbox(tmp_path)
    before = sb.status.read_bytes()
    with pytest.raises(TaskStatusError) as excinfo:
        write_task_status(sb.status, state="BLOCKED", reason_code="agent_operator_data_action",
                          action_args=["verb=restamp-fixed-in", "project=Not_A_Name"])
    assert excinfo.value.error_key == "task-status.invalid-action-args"
    assert "<" not in excinfo.value.remedy  # runnable as printed
    assert sb.status.read_bytes() == before


def test_help_documents_reason_code_and_action_args(tmp_path: pathlib.Path) -> None:
    sb = _make_sandbox(tmp_path)
    env = build_scrubbed_env(home_dir=sb.root / "home")
    result = subprocess.run(["bash", str(_WRAPPER), "--help"], capture_output=True, text=True, env=env, timeout=60)
    assert result.returncode == 0
    assert "--reason-code" in result.stdout
    assert "--action-args" in result.stdout
    assert "agent_operator_data_action" in result.stdout
    assert "agent_unclassified" in result.stdout
