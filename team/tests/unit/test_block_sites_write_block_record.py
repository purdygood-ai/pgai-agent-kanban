"""
test_block_sites_write_block_record.py
=======================================
Every site that blocks a task also writes the task's ``## Block Record``
(pgai_agent_kanban.overwatch.block_record) with the reason code naming the
block's cause and the role that owns the task.

Each parametrised case drives one real block site in a sandbox root under
the framework temp directory (pytest's tmp_path) with a throwaway git
repository where the site reads git: it builds the task folder, sources the
real stage file (or extracts the real function from it), stubs only the
collaborators that would reach outside the sandbox (queue markers, the
repo lock, the provider, worktree creation), drives the condition that
makes the site block, and then reads the status.md the site left with the
module reader (``load_block_record``). Each case asserts the record's
reason_code and owner_role, and that the site's own BLOCKED behaviour (the
State and Needs Human values it has always written) is unchanged.

A zero-progress block at the ceiling is classified ``provider_refusal``
when the task's latest dispatch lasted under 60 s or its batch-log tail
carries a model-not-found (or auth / usage-limit) signature, and
``zero_progress`` for a normal-length clean dispatch — three cases below.

A static guard closes the inventory: every BLOCKED state write under
team/scripts/lib/ is followed by a block_record_write call.

Naming: function names describe behaviour under test, never bug IDs or task
IDs (roles/TESTER.md "Test Authoring Guidelines").
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import re
import subprocess
import textwrap
from typing import Callable

import pytest

from pgai_agent_kanban.overwatch.block_record import load_block_record, parse_evidence
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

# This file lives at team/tests/unit/; team/ is three parents up.
_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent
_LIB = _TEAM_DIR / "scripts" / "lib"
_TASK_ID = "CODER-20260101-001-example-task"
_FEATURE_BRANCH = f"ai_feature/{_TASK_ID}"
_RC_BRANCH = "ai_rc/v9.9.9"


# ---------------------------------------------------------------------------
# Sandbox builders
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class Sandbox:
    """One sandbox kanban root with a single task folder."""

    root: pathlib.Path

    @property
    def task_dir(self) -> pathlib.Path:
        return self.root / "projects" / "demo" / "tasks" / _TASK_ID

    @property
    def status(self) -> pathlib.Path:
        return self.task_dir / "status.md"

    @property
    def readme(self) -> pathlib.Path:
        return self.task_dir / "README.md"

    @property
    def batch_log(self) -> pathlib.Path:
        return self.root / "logs" / "agents" / "coder-batch-20260101-000000.log"

    @property
    def repo(self) -> pathlib.Path:
        return self.root / "repo"


def _status_text(state: str = "WORKING", extra: str = "") -> str:
    return textwrap.dedent(f"""\
        # Status

        ## Task
        {_TASK_ID}

        ## State
        {state}

        ## Summary
        In progress.

        ## Artifacts
        none

        ## Blockers
        none

        ## Blocked Reason
        none

        ## Needs Human
        no

        ## Next Recommended Step
        TBD

        ## Instruction Conflicts
        none
        """) + extra


def _make_sandbox(tmp_path: pathlib.Path, *, role: str | None = "CODER", state: str = "WORKING",
                  status_extra: str = "", readme_extra: str = "") -> Sandbox:
    sb = Sandbox(tmp_path / "kanban")
    sb.task_dir.mkdir(parents=True)
    (sb.task_dir / "logs").mkdir()
    (sb.root / "tmp").mkdir()
    (sb.root / "home").mkdir()
    sb.batch_log.parent.mkdir(parents=True)
    sb.batch_log.write_text("wake starting\n", encoding="utf-8")
    sb.status.write_text(_status_text(state, status_extra), encoding="utf-8")
    if role is not None:
        sb.readme.write_text(
            f"# Task\n\n## Task ID\n{_TASK_ID}\n\n## Role\n{role}\n\n## Workflow Type\nrelease\n\n{readme_extra}",
            encoding="utf-8",
        )
    return sb


def _git(repo: pathlib.Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _make_repo(sb: Sandbox, *, rc: bool = True, feature_commits: int = 0) -> pathlib.Path:
    """A throwaway repo: main, optionally the RC branch and a feature branch ahead of it."""
    repo = sb.repo
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    _git(repo, "config", "user.name", "fixture")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "base.txt")
    _git(repo, "commit", "-q", "-m", "base")
    if rc:
        _git(repo, "branch", _RC_BRANCH)
    if feature_commits:
        _git(repo, "checkout", "-q", "-b", _FEATURE_BRANCH, _RC_BRANCH)
        for n in range(feature_commits):
            (repo / f"work{n}.txt").write_text(f"{n}\n", encoding="utf-8")
            _git(repo, "add", f"work{n}.txt")
            _git(repo, "commit", "-q", "-m", f"work {n}")
    return repo


def _write_dispatch(sb: Sandbox, tail: str, *, span_seconds: int) -> None:
    """Append one dispatch's output to the batch log and record it in the ledger."""
    start = sb.batch_log.stat().st_size
    with sb.batch_log.open("a", encoding="utf-8") as fh:
        fh.write(tail)
    end = sb.batch_log.stat().st_size
    minute, second = divmod(span_seconds, 60)
    ledger = sb.task_dir / "logs" / "dispatch.jsonl"
    rows = [
        {"event": "dispatched", "batch_log": str(sb.batch_log), "offset_start": start,
         "offset_end": None, "exit_code": None, "recorded_at": "2026-01-01T10:00:00Z"},
        {"event": "returned", "batch_log": str(sb.batch_log), "offset_start": start,
         "offset_end": end, "exit_code": 0, "recorded_at": f"2026-01-01T10:{minute:02d}:{second:02d}Z"},
    ]
    ledger.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


# ---------------------------------------------------------------------------
# Bash harness
# ---------------------------------------------------------------------------


def _extract(names: list[str], source: pathlib.Path) -> str:
    """Bash that defines the named top-level functions from *source* (brace-matched)."""
    alternation = "|".join(re.escape(n) for n in names)
    return textwrap.dedent(f"""\
        eval "$(awk '
            /^({alternation})\\(\\)/ {{ found=1; depth=0 }}
            found {{
                print
                n = split($0, a, "")
                for (i = 1; i <= n; i++) {{
                    if (a[i] == "{{") depth++
                    if (a[i] == "}}") {{ depth--; if (depth == 0) {{ found=0 }} }}
                }}
            }}
        ' '{source}')"
        """)


def _prelude(sb: Sandbox) -> str:
    """Shared bash prelude: real status writers and Block Record helpers, stubbed markers."""
    return textwrap.dedent(f"""\
        set -euo pipefail
        cd '{_TEAM_DIR}'
        export LOG_FILE='{sb.batch_log}'
        log() {{ echo "[test] wake: $*" | tee -a "$LOG_FILE" >&2; }}
        source '{_LIB}/pp_run_ops.sh'
        source '{_LIB}/atomic_write.sh'
        source '{_LIB}/error_contract.sh'
        source '{_LIB}/block_record.sh'
        """) + _extract(
        ["ensure_status_file", "_read_status_section", "get_state", "normalize_status_file",
         "set_state", "set_block", "set_state_or_insert", "set_block_or_append",
         "block_task_with_record", "block_task_with_unreadable_state"],
        _LIB / "wake_task_state.sh",
    ) + textwrap.dedent(f"""\
        mark_backlog() {{ echo "$1 [$2]" >> '{sb.root}/markers.log'; }}
        transition_task_state() {{ set_state "$2" "$3"; }}
        task_id='{_TASK_ID}'
        task_dir='{sb.task_dir}'
        task_readme='{sb.readme}'
        task_status='{sb.status}'
        TASKS_ROOT='{sb.task_dir.parent}'
        TEAM_ROOT='{sb.root}'
        KANBAN_ROOT='{sb.root}'
        LAST_TASK_FINAL_STATE=""
        """)


def _run(sb: Sandbox, body: str) -> subprocess.CompletedProcess:
    env = build_scrubbed_env(
        home_dir=sb.root / "home",
        extra_env={"PGAI_AGENT_KANBAN_TEMP_DIR": str(sb.root / "tmp"), "PGAI_AGENT_KANBAN_ROOT_PATH": str(sb.root)},
    )
    result = subprocess.run(
        ["bash", "-c", _prelude(sb) + body], capture_output=True, text=True, timeout=120, env=env,
    )
    assert result.returncode == 0, f"site driver failed (exit {result.returncode}):\n{result.stderr}"
    return result


# ---------------------------------------------------------------------------
# Site drivers — each builds its sandbox and drives one block site.
# ---------------------------------------------------------------------------


def _drive_missing_readme(tmp_path):
    sb = _make_sandbox(tmp_path, role=None)
    _run(sb, _extract(["process_one_task"], _LIB / "wake_driver.sh") + textwrap.dedent("""\
        _PENDING_TASK="$task_id"
        AGENT=writer
        _resolve_debug_gate() { echo false; }
        get_first_pending_task() { echo "${_PENDING_TASK}"; }
        setup_project_directories() { :; }
        process_one_task
        """))
    return sb


def _prep_body(agent: str, effort: str) -> str:
    return _extract(
        ["get_role_from_readme", "get_assigned_agent_from_readme", "role_is_valid_agent",
         "get_model_override_from_readme"],
        _LIB / "wake_readme_meta.sh",
    ) + textwrap.dedent(f"""\
        source '{_LIB}/wake_driver_prep.sh'
        AGENT={agent}; ACTIVE_PROVIDER=claude; _CURRENT_PROJECT=demo; _debug_gate=false
        role=""; SUBAGENT=""; selected_model=""; model_source=""; selected_effort=""; effort_source=""; task_debug_log=""
        {effort}
        wd_prepare_task
        """)


def _drive_invalid_assignment(tmp_path):
    sb = _make_sandbox(tmp_path, role="NOT-A-ROLE")
    _run(sb, _prep_body("coder", 'resolve_role_effort() { echo "|none"; }'))
    return sb


def _drive_invalid_effort(tmp_path):
    sb = _make_sandbox(tmp_path, role="WRITER")
    _run(sb, _prep_body("writer", "resolve_role_effort() { return 1; }"))
    return sb


def _drive_invalid_state(tmp_path):
    sb = _make_sandbox(tmp_path, role="TESTER", state="SIDEWAYS")
    _run(sb, _prep_body("tester", 'resolve_role_effort() { echo "|none"; }'))
    return sb


def _worktree_body(sb: Sandbox, *, subagent: str, role: str, stubs: str) -> str:
    return textwrap.dedent(f"""\
        source '{_LIB}/wake_driver_task_setup.sh'
        source '{_LIB}/wake_driver_worktree.sh'
        SUBAGENT={subagent}; role={role}; _CURRENT_PROJECT=demo
        PP_dev_tree_path='{sb.repo}'
        _task_feature_branch=""; _task_worktree_path=""; _task_workflow_type=""
        _task_source_branch=""; _canonical_dev_tree_path='{sb.repo}'; _park_resting_branch=""; role_reminder=""
        pp_prefix_branch() {{ echo "ai_$2"; }}
        _update_readme_feature_branch() {{ :; }}
        wf_load_plugin() {{ return 0; }}
        wf_git_mode() {{ echo branch; }}
        create_task_worktree() {{ echo "create_task_worktree: refused" >&2; return 1; }}
        create_detached_worktree() {{ echo "create_detached_worktree: refused" >&2; return 1; }}
        {stubs}
        wd_setup_task_worktree
        """)


_SOURCE_BRANCH_FIELD = f"## Source Branch\n{_RC_BRANCH}\n"


def _drive_branch_prefix(tmp_path):
    sb = _make_sandbox(tmp_path, role="CODER", readme_extra=_SOURCE_BRANCH_FIELD)
    _make_repo(sb)
    _run(sb, _worktree_body(sb, subagent="coder", role="CODER", stubs="pp_prefix_branch() { return 1; }"))
    return sb


def _drive_workflow_type(tmp_path):
    # The README reader defaults an absent field to release; the site fires
    # when the reader itself fails, which the stub reproduces.
    sb = _make_sandbox(tmp_path, role="CODER", readme_extra=_SOURCE_BRANCH_FIELD)
    _make_repo(sb)
    _run(sb, _worktree_body(sb, subagent="coder", role="CODER",
                            stubs="get_workflow_type_from_readme() { return 1; }"))
    return sb


def _drive_plugin_load(tmp_path):
    sb = _make_sandbox(tmp_path, role="WRITER", readme_extra=_SOURCE_BRANCH_FIELD)
    _make_repo(sb)
    _run(sb, _worktree_body(sb, subagent="writer", role="WRITER",
                            stubs='wf_load_plugin() { WF_LOAD_ERROR="manifest missing"; return 1; }'))
    return sb


def _drive_source_branch_field(tmp_path):
    sb = _make_sandbox(tmp_path, role="CODER")
    _make_repo(sb)
    _run(sb, _worktree_body(sb, subagent="coder", role="CODER", stubs=""))
    return sb


def _drive_worktree_create(tmp_path):
    sb = _make_sandbox(tmp_path, role="CODER", readme_extra=_SOURCE_BRANCH_FIELD)
    _make_repo(sb)
    _run(sb, _worktree_body(sb, subagent="coder", role="CODER", stubs=""))
    return sb


def _drive_worktree_create_rc_missing(tmp_path):
    sb = _make_sandbox(tmp_path, role="CODER", readme_extra=_SOURCE_BRANCH_FIELD)
    _make_repo(sb, rc=False)
    _run(sb, _worktree_body(sb, subagent="coder", role="CODER", stubs=""))
    return sb


def _drive_tester_plugin_load(tmp_path):
    sb = _make_sandbox(tmp_path, role="TESTER", readme_extra=_SOURCE_BRANCH_FIELD)
    _make_repo(sb)
    _run(sb, _worktree_body(sb, subagent="tester", role="TESTER",
                            stubs='wf_load_plugin() { WF_LOAD_ERROR="manifest missing"; return 1; }'))
    return sb


def _drive_tester_source_branch_field(tmp_path):
    sb = _make_sandbox(tmp_path, role="TESTER")
    _make_repo(sb)
    _run(sb, _worktree_body(sb, subagent="tester", role="TESTER", stubs=""))
    return sb


def _drive_tester_worktree_create(tmp_path):
    sb = _make_sandbox(tmp_path, role="TESTER", readme_extra=_SOURCE_BRANCH_FIELD)
    _make_repo(sb)
    _run(sb, _worktree_body(sb, subagent="tester", role="TESTER", stubs=""))
    return sb


def _drive_per_task_timeout(tmp_path):
    sb = _make_sandbox(tmp_path, role="WRITER")
    _make_repo(sb, feature_commits=2)
    _write_dispatch(sb, "agent working\nagent still working\n", span_seconds=3000)
    _run(sb, textwrap.dedent(f"""\
        source '{_LIB}/wake_driver_finalize.sh'
        narrate_model_mismatch() {{ :; }}
        metrics_rollup_refresh_for_task() {{ :; }}
        provider_dispatch_refused() {{ return 1; }}
        pgai_lock_reverify_or_narrate() {{ return 0; }}
        wake_bracket_post_session() {{ :; }}
        teardown_task_worktree() {{ :; }}
        AGENT=overwatch; SUBAGENT=writer; role=WRITER; _CURRENT_PROJECT=demo
        _task_killed=true; _provider_pgid=""; _provider_timeout_seconds=60; MAX_TASK_SECONDS=60
        agent_exit=137; task_artifact_dir="$task_dir/artifacts"; _debug_gate=false
        _pollution_snapshot_ok=false; _litter_snapshot_file=""; _project_lock_file=""
        _task_worktree_path='{sb.repo}'; _task_feature_branch='{_FEATURE_BRANCH}'; _task_source_branch='{_RC_BRANCH}'
        _canonical_dev_tree_path='{sb.repo}'; _park_resting_branch=""
        wd_finalize_task
        """))
    return sb


def _lifecycle_body(call: str) -> str:
    return textwrap.dedent(f"""\
        source '{_LIB}/overwatch_protocol.sh'
        source '{_LIB}/wake_lifecycle.sh'
        transition_task_state() {{ set_state "$2" "$3"; }}
        REPO_TEAM_DIR='{_TEAM_DIR / "scripts"}'
        {call}
        """)


_TRANSIENT_TAIL = "agent working\nAPI Error: 529 overloaded_error\n"


def _drive_transient_below_cap(tmp_path):
    sb = _make_sandbox(tmp_path, role="CODER")
    _write_dispatch(sb, _TRANSIENT_TAIL, span_seconds=900)
    _run(sb, _lifecycle_body('role=CODER; SUBAGENT=coder; TRANSIENT_RETRY_CAP=3; '
                             'handle_working_after_exit "$task_id" "$task_status" "$LOG_FILE" 1 >/dev/null'))
    return sb


def _drive_transient_at_cap(tmp_path):
    sb = _make_sandbox(tmp_path, role="CODER", status_extra=(
        "\n## Transient Retry\nreason_code: transient_api_error\nretry_count: 2\n"
        "retry_limit: 3\nprovider: claude\nlast_error: overloaded\n"))
    _write_dispatch(sb, _TRANSIENT_TAIL, span_seconds=900)
    _run(sb, _lifecycle_body('role=CODER; SUBAGENT=coder; TRANSIENT_RETRY_CAP=3; '
                             'handle_working_after_exit "$task_id" "$task_status" "$LOG_FILE" 1 >/dev/null'))
    return sb


def _gate_sandbox(tmp_path, *, merged: bool, dirty: bool = False, empty_commit: bool = False) -> Sandbox:
    sb = _make_sandbox(tmp_path, role="WRITER", state="DONE")
    _make_repo(sb, feature_commits=1)
    repo = sb.repo
    if empty_commit:
        _git(repo, "commit", "-q", "--allow-empty", "-m", "nothing")
    if merged:
        _git(repo, "checkout", "-q", _RC_BRANCH)
        _git(repo, "merge", "-q", "--no-ff", "-m", f"Merge {_FEATURE_BRANCH}", _FEATURE_BRANCH)
    if dirty:
        (repo / "stray.txt").write_text("uncommitted\n", encoding="utf-8")
    return sb


def _gate_call(sb: Sandbox) -> str:
    return (f'coder_writer_completion_gate "$task_id" "$task_status" writer '
            f'"{_FEATURE_BRANCH}" "{_RC_BRANCH}" "{sb.repo}" >/dev/null')


def _drive_gate_merge_not_found(tmp_path):
    sb = _gate_sandbox(tmp_path, merged=False)
    _run(sb, _lifecycle_body(_gate_call(sb)))
    return sb


def _drive_gate_merge_evidence_not_found(tmp_path):
    sb = _gate_sandbox(tmp_path, merged=False)
    _git(sb.repo, "checkout", "-q", "--detach", _FEATURE_BRANCH)
    _git(sb.repo, "branch", "-D", _FEATURE_BRANCH)
    _run(sb, _lifecycle_body(_gate_call(sb)))
    return sb


def _drive_gate_dirty_worktree(tmp_path):
    sb = _gate_sandbox(tmp_path, merged=True, dirty=True)
    _run(sb, _lifecycle_body(_gate_call(sb)))
    return sb


def _drive_gate_empty_commit(tmp_path):
    sb = _gate_sandbox(tmp_path, merged=True, empty_commit=True)
    _run(sb, _lifecycle_body(_gate_call(sb)))
    return sb


_ZERO_PROGRESS_AT_CAP = (
    "\n## Zero Progress\nreason_code: clean_exit_no_progress\ncount: 2\nlimit: 3\ndetail: earlier cycles\n"
)


def _drive_zero_progress(tmp_path, tail: str, span_seconds: int) -> Sandbox:
    sb = _make_sandbox(tmp_path, role="CM", status_extra=_ZERO_PROGRESS_AT_CAP)
    _write_dispatch(sb, tail, span_seconds=span_seconds)
    _run(sb, _lifecycle_body('ZERO_PROGRESS_CAP=3; '
                             'handle_working_after_exit "$task_id" "$task_status" "$LOG_FILE" 0 >/dev/null'))
    return sb


def _drive_zero_progress_clean(tmp_path):
    return _drive_zero_progress(tmp_path, "agent read files\nagent ended its turn\n", span_seconds=900)


def _drive_zero_progress_short_span(tmp_path):
    return _drive_zero_progress(tmp_path, "agent ended its turn\n", span_seconds=12)


def _drive_zero_progress_model_not_found(tmp_path):
    return _drive_zero_progress(
        tmp_path, "Error: model not found: example-model-name\n", span_seconds=900)


def _drive_overwatch_transient_ceiling(tmp_path):
    sb = _make_sandbox(tmp_path, role="PM", state="BLOCKED", status_extra=(
        "\n## Transient Retry\nreason_code: transient_api_error\nretry_count: 3\n"
        "retry_limit: 3\nprovider: claude\nlast_error: overloaded\n"))
    _write_dispatch(sb, _TRANSIENT_TAIL, span_seconds=900)
    (sb.root / "projects" / "demo" / "overwatch" / "backups").mkdir(parents=True)
    _run(sb, textwrap.dedent(f"""\
        source '{_LIB}/overwatch_protocol.sh'
        source '{_LIB}/overwatch-checks/check-transient-api-error.sh'
        export OVERWATCH_PROJECT=demo
        _CTAE_TASK_ID="$task_id"; _CTAE_STATUS_FILE="$task_status"; _CTAE_CURRENT_COUNT=3; _CTAE_CAP=3
        _ctae_do_escalate
        """))
    return sb


# ---------------------------------------------------------------------------
# AT1: the parametrised site table
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Site:
    name: str
    drive: Callable[[pathlib.Path], Sandbox]
    reason_code: str
    owner_role: str
    needs_human: str = "yes"


SITES: tuple[Site, ...] = (
    Site("wake_driver missing README", _drive_missing_readme, "missing_readme", "WRITER"),
    Site("prep invalid role assignment", _drive_invalid_assignment, "invalid_assignment", "CODER"),
    Site("prep invalid effort config", _drive_invalid_effort, "invalid_effort_config", "WRITER"),
    Site("prep invalid task state", _drive_invalid_state, "invalid_state", "TESTER"),
    Site("worktree branch prefix failure", _drive_branch_prefix, "branch_prefix_failed", "CODER"),
    Site("worktree workflow type unparseable", _drive_workflow_type, "workflow_type_unparseable", "CODER"),
    Site("worktree plugin load failure", _drive_plugin_load, "plugin_load_failed", "WRITER"),
    Site("worktree source branch field missing", _drive_source_branch_field, "source_branch_missing", "CODER"),
    Site("worktree create failure", _drive_worktree_create, "worktree_create_failed", "CODER"),
    Site("worktree create with source branch absent", _drive_worktree_create_rc_missing,
         "source_branch_missing", "CODER"),
    Site("tester plugin load failure", _drive_tester_plugin_load, "plugin_load_failed", "TESTER"),
    Site("tester source branch field missing", _drive_tester_source_branch_field,
         "source_branch_missing", "TESTER"),
    Site("tester detached worktree create failure", _drive_tester_worktree_create,
         "worktree_create_failed", "TESTER"),
    Site("finalize per-task timeout", _drive_per_task_timeout, "per_task_timeout", "WRITER"),
    Site("transient error below cap", _drive_transient_below_cap, "transient_api_error", "CODER", "no"),
    Site("transient error at cap", _drive_transient_at_cap, "transient_api_error", "CODER"),
    Site("completion gate merge not found", _drive_gate_merge_not_found, "gate_merge_not_found", "WRITER"),
    Site("completion gate merge evidence not found", _drive_gate_merge_evidence_not_found,
         "gate_merge_evidence_not_found", "WRITER"),
    Site("completion gate dirty worktree", _drive_gate_dirty_worktree, "gate_dirty_worktree", "WRITER"),
    Site("completion gate empty commit", _drive_gate_empty_commit, "gate_empty_commit", "WRITER"),
    Site("zero-progress cap, clean normal-span exit", _drive_zero_progress_clean, "zero_progress", "CM"),
    Site("zero-progress cap, dispatch under 60 s", _drive_zero_progress_short_span, "provider_refusal", "CM"),
    Site("zero-progress cap, model-not-found tail", _drive_zero_progress_model_not_found,
         "provider_refusal", "CM"),
    Site("overwatch transient retry ceiling", _drive_overwatch_transient_ceiling, "transient_api_error", "PM"),
)


def _section(text: str, heading: str) -> str:
    m = re.search(rf"^##\s+{re.escape(heading)}\s*\n+(.*?)(?=\n+##|\Z)", text, re.S | re.M)
    return m.group(1).strip() if m else ""


@pytest.mark.parametrize("site", SITES, ids=[s.name for s in SITES])
def test_block_site_writes_block_record_with_cause_and_owner(tmp_path: pathlib.Path, site: Site) -> None:
    sb = site.drive(tmp_path)
    record = load_block_record(sb.status)
    text = sb.status.read_text(encoding="utf-8")
    assert record is not None, f"{site.name}: no ## Block Record in status.md:\n{text}"
    assert record.reason_code == site.reason_code, f"{site.name}: reason_code {record.reason_code!r}"
    assert record.owner_role == site.owner_role, f"{site.name}: owner_role {record.owner_role!r}"
    assert record.attempts == 0 and record.resume == "fresh", f"{site.name}: first block starts fresh"
    # The site's own block behaviour is unchanged by the record.
    assert _section(text, "State") == "BLOCKED", f"{site.name}: State is {_section(text, 'State')!r}"
    assert _section(text, "Needs Human") == site.needs_human, f"{site.name}: Needs Human changed"


def test_per_task_timeout_record_carries_git_and_batch_log_evidence(tmp_path: pathlib.Path) -> None:
    """The timeout record points at the surviving work (branch, commits ahead, batch-log span) and its limit."""
    sb = _drive_per_task_timeout(tmp_path)
    evidence = parse_evidence(load_block_record(sb.status).evidence)
    assert evidence["worktree"] == str(sb.repo)
    assert evidence["branch"] == _FEATURE_BRANCH
    assert evidence["ahead"] == "2", "two unmerged commits on the feature branch"
    assert evidence["merged"] == "no"
    assert evidence["batch_log"].startswith(f"{sb.batch_log}#"), evidence["batch_log"]
    assert evidence["diag"].startswith(f"{sb.batch_log}:"), evidence["diag"]
    assert evidence["timeout_s"] == "60", "the limit the attempt was killed at, for the triage note"


def test_pre_dispatch_block_writes_placeholders_for_unknown_evidence(tmp_path: pathlib.Path) -> None:
    """A block before any worktree exists records the documented placeholders, never invented values."""
    sb = _drive_missing_readme(tmp_path)
    evidence = parse_evidence(load_block_record(sb.status).evidence)
    assert (evidence["worktree"], evidence["branch"], evidence["ahead"], evidence["merged"]) == (
        "gone", "none", "0", "no")


def test_provider_refusal_record_names_the_signal(tmp_path: pathlib.Path) -> None:
    """A refusal classified by its tail says which signature matched; one by span says how long it ran."""
    by_tail = parse_evidence(load_block_record(_drive_zero_progress_model_not_found(tmp_path / "a").status).evidence)
    assert by_tail["signature"] == "model_not_found"
    by_span = parse_evidence(load_block_record(_drive_zero_progress_short_span(tmp_path / "b").status).evidence)
    assert (by_span["span_s"], by_span["signature"]) == ("12", "none")


def test_block_record_write_refusal_never_stops_the_block(tmp_path: pathlib.Path) -> None:
    """A task whose role is outside the owner set still blocks; the refused record is only logged."""
    sb = _make_sandbox(tmp_path, role="PO")
    _run(sb, _prep_body("po", "resolve_role_effort() { return 1; }"))
    text = sb.status.read_text(encoding="utf-8")
    assert _section(text, "State") == "BLOCKED"
    assert load_block_record(sb.status) is None
    assert "Block Record not written" in sb.batch_log.read_text(encoding="utf-8")


# Static guard: every BLOCKED state write under team/scripts/lib/ is followed
# by a block_record_write call — the inventory check that keeps a new block
# site from shipping without its record. Paired with the behavioural cases above.
_BLOCKED_WRITE_RE = re.compile(r'^\s*(set_state|if transition_task_state|transition_task_state)\b.*"BLOCKED"')
_WINDOW = 14


def test_every_blocked_state_write_in_lib_calls_block_record_write() -> None:
    missing = []
    for path in sorted(_LIB.rglob("*.sh")):
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if _BLOCKED_WRITE_RE.match(line):
                window = "\n".join(lines[i:i + _WINDOW])
                if "block_record_write" not in window:
                    missing.append(f"{path.relative_to(_TEAM_DIR)}:{i + 1}")
    assert not missing, "BLOCKED state writes without a block_record_write call: " + ", ".join(missing)
