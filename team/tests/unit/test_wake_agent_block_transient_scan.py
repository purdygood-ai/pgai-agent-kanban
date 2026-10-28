"""
test_wake_agent_block_transient_scan.py
=======================================
The wake's transient check of a block an agent wrote itself.

An agent that survives its subagent's provider error records BLOCKED through
the status-writer tool with the catch-all reason code (agent_unclassified)
and exits cleanly. wd_finalize_task (scripts/lib/wake_driver_finalize.sh)
then runs reclassify_agent_unclassified_block
(scripts/lib/wake_agent_block_scan.sh), which hands the log tail to
handle_working_after_exit in agent-block mode (scripts/lib/wake_lifecycle.sh):

  * a transient signature on one of the provider's own error lines (an
    "API Error:" line, a JSON error event) rewrites the Block Record to
    transient_api_error and records ## Transient Retry, with Needs Human by
    the transient path's own rule;
  * a transient word only in the agent's own prose (a file rate_limit.sh,
    "500 chars"), a billing or refusal signature, or none, leaves the
    agent's record;
  * any reason code other than agent_unclassified skips the check.

Each case builds a sandbox kanban root, writes the agent's block with the
real scripts/task-status.sh, puts the agent's output in the wake batch log
(with its dispatch ledger), and drives the real wd_finalize_task with only
the collaborators that reach outside the sandbox stubbed (queue markers, the
repo lock, worktree teardown, token narration).

Naming: function names describe behaviour under test, never bug IDs or task
IDs (roles/TESTER.md "Test Authoring Guidelines").
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
import textwrap

import pytest

from pgai_agent_kanban.overwatch.block_record import load_block_record
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

# This file lives at team/tests/unit/; team/ is three parents up.
_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent
_LIB = _TEAM_DIR / "scripts" / "lib"
_PROJECT = "demo"
_TASK_ID = "CODER-20260101-001-example-task"
_AGENT_BLOCKERS = "subagent stopped before any work; see the batch log"

_TRANSIENT = "API Error: 529 overloaded_error"
_BILLING = "insufficient_quota: You exceeded your current quota"
_REFUSAL = "Error: authentication_error: invalid x-api-key"


def _task_dir(root: pathlib.Path) -> pathlib.Path:
    return root / "projects" / _PROJECT / "tasks" / _TASK_ID


def _batch_log(root: pathlib.Path) -> pathlib.Path:
    return root / "logs" / "agents" / "coder-batch-20260101-000000.log"


def _env(root: pathlib.Path) -> dict[str, str]:
    return build_scrubbed_env(
        home_dir=root / "home",
        extra_env={"PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
                   "PGAI_AGENT_KANBAN_ROOT_PATH": str(root), "KANBAN_ROOT": str(root)},
    )


def _agent_blocked_task(tmp_path: pathlib.Path, reason_args: list[str], tail_line: str,
                        status_extra: str = "") -> pathlib.Path:
    """A sandbox root holding one task its agent blocked, and that agent's output.

    The block is written by the real status-writer tool, exactly as an agent
    writes it; *tail_line* is the last thing the agent printed before exiting.
    Returns the sandbox root.
    """
    root = tmp_path / "kanban"
    task = _task_dir(root)
    (task / "logs").mkdir(parents=True)
    (root / "tmp").mkdir()
    (root / "home").mkdir()
    (task / "README.md").write_text(
        f"# Task\n\n## Task ID\n{_TASK_ID}\n\n## Role\nCODER\n\n## Workflow Type\nrelease\n",
        encoding="utf-8")
    (task / "status.md").write_text(textwrap.dedent(f"""\
        # Status

        ## Task
        {_TASK_ID}

        ## State
        WORKING

        ## Summary
        In progress.

        ## Blockers
        none

        ## Blocked Reason
        none

        ## Needs Human
        no
        """) + status_extra, encoding="utf-8")

    batch = _batch_log(root)
    batch.parent.mkdir(parents=True)
    batch.write_text("wake starting\n", encoding="utf-8")
    start = batch.stat().st_size
    with batch.open("a", encoding="utf-8") as fh:
        fh.write(f"agent starting\nsubagent dispatched\n{tail_line}\nagent exiting\n")
    end = batch.stat().st_size
    rows = [
        {"event": "dispatched", "batch_log": str(batch), "offset_start": start, "offset_end": None,
         "exit_code": None, "recorded_at": "2026-01-01T10:00:00Z"},
        {"event": "returned", "batch_log": str(batch), "offset_start": start, "offset_end": end,
         "exit_code": 0, "recorded_at": "2026-01-01T10:15:00Z"},
    ]
    (task / "logs" / "dispatch.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    result = subprocess.run(
        ["bash", str(_TEAM_DIR / "scripts" / "task-status.sh"), "--project", _PROJECT, "--key", _TASK_ID,
         "--state", "BLOCKED", "--needs-human", "yes", "--blockers", _AGENT_BLOCKERS, *reason_args],
        capture_output=True, text=True, timeout=60, env=_env(root),
    )
    assert result.returncode == 0, f"status-writer refused the agent's block:\n{result.stderr}"
    return root


def _finalize(root: pathlib.Path) -> subprocess.CompletedProcess:
    """Run the real wd_finalize_task for a task whose agent exited 0, unkilled."""
    task = _task_dir(root)
    script = textwrap.dedent(f"""\
        set -euo pipefail
        cd '{_TEAM_DIR}'
        export LOG_FILE='{_batch_log(root)}'
        log() {{ echo "[test] wake: $*" | tee -a "$LOG_FILE" >&2; }}
        source '{_LIB}/pp_run_ops.sh'
        source '{_LIB}/atomic_write.sh'
        source '{_LIB}/error_contract.sh'
        source '{_LIB}/temp.sh'
        source '{_LIB}/block_record.sh'
        source '{_LIB}/md_field.sh'
        source '{_LIB}/overwatch_protocol.sh'
        source '{_LIB}/wake_task_state.sh'
        source '{_LIB}/wake_lifecycle.sh'
        source '{_LIB}/wake_provider_pause.sh'
        source '{_LIB}/wake_driver_finalize.sh'
        mark_backlog() {{ echo "$1 [$2]" >> '{root}/markers.log'; }}
        transition_task_state() {{ set_state "$2" "$3"; }}
        narrate_model_mismatch() {{ :; }}
        metrics_rollup_refresh_for_task() {{ :; }}
        provider_dispatch_refused() {{ return 1; }}
        pgai_lock_reverify_or_narrate() {{ return 0; }}
        wake_bracket_post_session() {{ :; }}
        teardown_task_worktree() {{ :; }}
        task_id='{_TASK_ID}'; task_dir='{task}'; task_readme='{task}/README.md'
        task_status='{task}/status.md'; task_artifact_dir='{task}/artifacts'
        TASKS_ROOT='{task.parent}'; TEAM_ROOT='{root}'; KANBAN_ROOT='{root}'
        AGENT=coder; SUBAGENT=coder; role=CODER; ACTIVE_PROVIDER=claude; _CURRENT_PROJECT={_PROJECT}
        TRANSIENT_RETRY_CAP=3; LAST_TASK_FINAL_STATE=""
        _task_killed=false; _provider_pgid=""; agent_exit=0; _debug_gate=false
        _pollution_snapshot_ok=false; _litter_snapshot_file=""; _project_lock_file=""
        _task_worktree_path=""; _task_feature_branch=""; _task_source_branch=""
        _canonical_dev_tree_path=""; _park_resting_branch=""
        wd_finalize_task
        echo "final_state=$LAST_TASK_FINAL_STATE"
        """)
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=120,
                            env=_env(root))
    assert result.returncode == 0, f"wd_finalize_task failed (exit {result.returncode}):\n{result.stderr}"
    assert "final_state=BLOCKED" in result.stdout, result.stdout + result.stderr
    return result


def _section(text: str, heading: str) -> str | None:
    m = re.search(rf"^## {re.escape(heading)}[ \t]*\n(.*?)(?=^## |\Z)", text, flags=re.S | re.M)
    return m.group(1).strip() if m else None


def _retry_record(text: str) -> dict[str, str] | None:
    body = _section(text, "Transient Retry")
    if body is None or body.lower() == "none":
        return None
    return dict(re.findall(r"^(\w+):[ \t]*(.*)$", body, flags=re.M))


def _outcome(root: pathlib.Path) -> tuple[str, dict[str, str] | None, object]:
    status = _task_dir(root) / "status.md"
    return status.read_text(encoding="utf-8"), _retry_record(status.read_text(encoding="utf-8")), \
        load_block_record(status)


def test_catch_all_block_with_transient_tail_becomes_a_transient_retry(tmp_path: pathlib.Path) -> None:
    root = _agent_blocked_task(tmp_path, ["--reason-code", "agent_unclassified"], _TRANSIENT)
    _finalize(root)
    text, retry, record = _outcome(root)

    assert record.reason_code == "transient_api_error", text
    assert record.owner_role == "CODER", text
    assert f"diag={_batch_log(root)}:" in record.evidence, record.evidence
    assert "batch_log=" in record.evidence, record.evidence
    assert retry is not None, f"no ## Transient Retry record:\n{text}"
    assert retry["reason_code"] == "transient_api_error"
    assert retry["retry_count"] == "1"
    assert retry["retry_limit"] == "3"
    assert "529" in retry["last_error"], retry
    assert _section(text, "State") == "BLOCKED"
    assert _section(text, "Needs Human") == "no", "below the ceiling the transient path needs no human"
    assert _section(text, "Blocked Reason").startswith("TRANSIENT API ERROR"), text


def test_catch_all_block_at_the_retry_ceiling_escalates_by_the_transient_rule(tmp_path: pathlib.Path) -> None:
    earlier = ("\n## Transient Retry\nreason_code: transient_api_error\nretry_count: 2\n"
               "retry_limit: 3\nprovider: claude\nlast_error: overloaded\n")
    root = _agent_blocked_task(tmp_path, ["--reason-code", "agent_unclassified"], _TRANSIENT, earlier)
    _finalize(root)
    text, retry, record = _outcome(root)

    assert record.reason_code == "transient_api_error", text
    assert retry is not None and retry["retry_count"] == "3", text
    assert _section(text, "Needs Human") == "yes", "at the ceiling the transient path escalates"


def test_catch_all_block_without_a_signature_keeps_the_agents_record(tmp_path: pathlib.Path) -> None:
    root = _agent_blocked_task(tmp_path, ["--reason-code", "agent_unclassified"], "agent gave up: README unclear")
    before = (_task_dir(root) / "status.md").read_text(encoding="utf-8")
    _finalize(root)
    text, retry, record = _outcome(root)

    assert record.reason_code == "agent_unclassified", text
    assert retry is None, f"no transient tail, yet a ## Transient Retry record:\n{text}"
    assert _section(text, "Needs Human") == "yes"
    assert _section(text, "Blockers") == _AGENT_BLOCKERS
    assert _section(text, "Block Record") == _section(before, "Block Record"), "the agent's record must stand"


@pytest.mark.parametrize("tail_line", [_BILLING, _REFUSAL], ids=["billing", "refusal"])
def test_catch_all_block_with_a_non_transient_signature_is_not_reclassified(
    tmp_path: pathlib.Path, tail_line: str,
) -> None:
    root = _agent_blocked_task(tmp_path, ["--reason-code", "agent_unclassified"], tail_line)
    _finalize(root)
    text, retry, record = _outcome(root)

    assert record.reason_code == "agent_unclassified", text
    assert retry is None, text
    assert _section(text, "Needs Human") == "yes"
    assert _section(text, "Blockers") == _AGENT_BLOCKERS


@pytest.mark.parametrize("reason_args, code", [
    (["--reason-code", "agent_transient_provider_error"], "agent_transient_provider_error"),
    (["--reason-code", "agent_operator_data_action", "--action-args", "verb=restamp-fixed-in",
      f"project={_PROJECT}", "version=v1.2.3", "bugs=BUG-0001"], "agent_operator_data_action"),
], ids=["agent_transient_provider_error", "agent_operator_data_action"])
def test_block_with_an_agent_named_code_skips_the_scan(
    tmp_path: pathlib.Path, reason_args: list[str], code: str,
) -> None:
    root = _agent_blocked_task(tmp_path, reason_args, _TRANSIENT)
    before = (_task_dir(root) / "status.md").read_text(encoding="utf-8")
    result = _finalize(root)
    text, retry, record = _outcome(root)

    assert record.reason_code == code, text
    assert retry is None, f"an agent-named code was scanned anyway:\n{text}"
    assert _section(text, "Block Record") == _section(before, "Block Record")
    assert _section(text, "Needs Human") == "yes"
    assert "after transient API error" not in result.stderr, result.stderr


# The agent's own final reply, ending the log tail: prose that carries
# transient signature words but reports no provider failure.
_PROSE_TAILS = {
    "rate_limit_path": ("BLOCKED: git merge reported a conflict in team/scripts/lib/rate_limit.sh; "
                        "left the feature branch in place for inspection."),
    "500_chars": ("BLOCKED: requirements doc missing; the summary was cut at 500 chars and the "
                  "PM ticket is unparseable."),
    "http_503_section": "BLOCKED: merge conflict in docs/api.md (both sides edited the HTTP 503 section).",
    "overloaded_word": "BLOCKED: the 429 handler in overloaded_queue.py conflicts with the 529 retry path.",
}


@pytest.mark.parametrize("tail_line", list(_PROSE_TAILS.values()), ids=list(_PROSE_TAILS))
def test_catch_all_block_whose_prose_names_signature_words_keeps_the_agents_question(
    tmp_path: pathlib.Path, tail_line: str,
) -> None:
    root = _agent_blocked_task(tmp_path, ["--reason-code", "agent_unclassified"], tail_line)
    before = (_task_dir(root) / "status.md").read_text(encoding="utf-8")
    _finalize(root)
    text, retry, record = _outcome(root)

    assert record.reason_code == "agent_unclassified", text
    assert _section(text, "Blockers") == _AGENT_BLOCKERS, "the agent's question must not be overwritten"
    assert _section(text, "Needs Human") == "yes"
    assert retry is None, f"prose alone wrote a ## Transient Retry record:\n{text}"
    assert _section(text, "Block Record") == _section(before, "Block Record"), "the agent's record must stand"


def test_provider_error_line_beside_signature_prose_is_the_one_quoted(tmp_path: pathlib.Path) -> None:
    tail = f"{_TRANSIENT}\nBLOCKED: subagent died; see team/scripts/lib/rate_limit.sh notes."
    root = _agent_blocked_task(tmp_path, ["--reason-code", "agent_unclassified"], tail)
    _finalize(root)
    text, retry, record = _outcome(root)

    assert record.reason_code == "transient_api_error", text
    assert retry is not None, text
    assert retry["last_error"].startswith("API Error: 529"), retry
    assert "rate_limit.sh" not in _section(text, "Blockers"), "the quoted signature comes from the provider's line"


@pytest.mark.parametrize("tail_line", [
    '{"type":"error","message":"stream error: exceeded retry limit, last status: 429 Too Many Requests"}',
    '{"type":"result","subtype":"error_during_execution","is_error":true,"result":"API Error: 503 Service Unavailable"}',
], ids=["codex_error_event", "claude_error_result"])
def test_catch_all_block_with_a_json_provider_error_event_becomes_a_transient_retry(
    tmp_path: pathlib.Path, tail_line: str,
) -> None:
    root = _agent_blocked_task(tmp_path, ["--reason-code", "agent_unclassified"], tail_line)
    _finalize(root)
    text, retry, record = _outcome(root)

    assert record.reason_code == "transient_api_error", text
    assert retry is not None and retry["retry_count"] == "1", text


def test_billing_prose_still_outranks_a_provider_5xx_line(tmp_path: pathlib.Path) -> None:
    tail = f"{_TRANSIENT}\nBLOCKED: the account reports {_BILLING}"
    root = _agent_blocked_task(tmp_path, ["--reason-code", "agent_unclassified"], tail)
    _finalize(root)
    text, retry, record = _outcome(root)

    assert record.reason_code == "agent_unclassified", text
    assert retry is None, text
    assert _section(text, "Blockers") == _AGENT_BLOCKERS


def _provider_lines(text: str) -> subprocess.CompletedProcess:
    script = f"source '{_LIB}/wake_agent_block_scan.sh'; agent_block_provider_lines \"$1\""
    return subprocess.run(["bash", "-c", script, "agent_block_provider_lines", text],
                          capture_output=True, text=True, timeout=30)


def test_provider_line_selector_keeps_only_the_providers_own_error_lines() -> None:
    kept = [
        "API Error: 529 overloaded_error",
        "  api error: 500 Internal Server Error",
        '{"type":"error","message":"stream error: 429"}',
        '{"type": "turn.failed", "error": {"message": "overloaded"}}',
        '{"type":"result","is_error":true,"result":"API Error: 503"}',
    ]
    dropped = [
        "BLOCKED: merge conflict in team/scripts/lib/rate_limit.sh",
        "the summary was cut at 500 chars",
        "Quoting the log: API Error: 529 was seen earlier",
        '{"type":"result","is_error":false,"result":"rate_limit.sh conflict"}',
        '{"type":"item.completed","item":{"type":"agent_message","text":"HTTP 503 section"}}',
    ]
    result = _provider_lines("\n".join(dropped[:2] + kept + dropped[2:]))
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == kept


def test_provider_line_selector_prints_nothing_for_prose_only() -> None:
    result = _provider_lines(_PROSE_TAILS["rate_limit_path"])
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
