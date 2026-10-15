"""
test_block_evidence.py
======================
What a block site records beside its BLOCKED transition, gathered by
pgai_agent_kanban.overwatch.block_evidence: the owner role, the evidence
line with its documented placeholders, the worktree-failure cause, and the
provider-refusal classification of a zero-progress block. The block sites
themselves are driven in test_block_sites_write_block_record.py; this file
pins the edges of each reader.

Every fixture lives under pytest's tmp_path.

Naming: function names describe behaviour under test, never bug IDs or task
IDs (roles/TESTER.md "Test Authoring Guidelines").
"""

from __future__ import annotations

import json
import pathlib
import subprocess

import pytest

from pgai_agent_kanban.overwatch import block_evidence as be


def _task(tmp_path: pathlib.Path, role: str | None = None) -> pathlib.Path:
    task_dir = tmp_path / "task"
    (task_dir / "logs").mkdir(parents=True)
    (task_dir / "status.md").write_text("## State\nBLOCKED\n", encoding="utf-8")
    if role is not None:
        (task_dir / "README.md").write_text(f"# Task\n\n## Role\n{role}\n", encoding="utf-8")
    return task_dir


def _ledger(task_dir: pathlib.Path, batch_log: pathlib.Path, lines: list) -> None:
    rendered = [line if isinstance(line, str) else json.dumps(line) for line in lines]
    (task_dir / "logs" / "dispatch.jsonl").write_text("\n".join(rendered) + "\n", encoding="utf-8")


def _row(event: str, batch_log: pathlib.Path, at: str, start: int, end=None) -> dict:
    return {"event": event, "batch_log": str(batch_log), "recorded_at": at,
            "offset_start": start, "offset_end": end}


# --- owner role -------------------------------------------------------------


def test_owner_role_prefers_a_valid_candidate_over_the_readme(tmp_path: pathlib.Path) -> None:
    task = _task(tmp_path, role="WRITER")
    assert be.resolve_owner_role(task / "status.md", ["tester"]) == "TESTER"


def test_owner_role_skips_invalid_candidates_and_falls_back_to_the_readme(tmp_path: pathlib.Path) -> None:
    task = _task(tmp_path, role="**cm**")
    assert be.resolve_owner_role(task / "status.md", ["NOT-A-ROLE", "overwatch", ""]) == "CM"


def test_owner_role_is_empty_when_nothing_names_an_owner_role(tmp_path: pathlib.Path) -> None:
    task = _task(tmp_path, role="PO")
    assert be.resolve_owner_role(task / "status.md", ["po"]) == ""


# --- dispatch ledger --------------------------------------------------------


def test_latest_dispatch_skips_torn_lines_and_measures_the_span(tmp_path: pathlib.Path) -> None:
    task = _task(tmp_path)
    log = tmp_path / "batch.log"
    log.write_text("x" * 100, encoding="utf-8")
    _ledger(task, log, [
        _row("dispatched", log, "2026-01-01T10:00:00Z", 10),
        _row("returned", log, "2026-01-01T10:00:45Z", 10, 80),
        '{"event": "returned", "batch_log"',  # torn trailing write
    ])
    dispatch = be.latest_dispatch(task)
    assert dispatch.span_seconds == 45
    assert dispatch.token() == f"batch_log={log}#10-80"


def test_an_open_dispatch_has_no_span_and_runs_to_the_end_of_the_log(tmp_path: pathlib.Path) -> None:
    task = _task(tmp_path)
    log = tmp_path / "batch.log"
    log.write_text("y" * 64, encoding="utf-8")
    _ledger(task, log, [
        _row("dispatched", log, "2026-01-01T09:00:00Z", 0),
        _row("returned", log, "2026-01-01T09:00:05Z", 0, 8),
        _row("dispatched", log, "2026-01-01T10:00:00Z", 8),
    ])
    dispatch = be.latest_dispatch(task)
    assert dispatch.span_seconds is None, "an earlier pair must not lend its span to a still-open dispatch"
    assert dispatch.token() == f"batch_log={log}#8-64"


def test_no_ledger_is_zero_progress_with_placeholders(tmp_path: pathlib.Path) -> None:
    task = _task(tmp_path)
    verdict = be.classify_zero_progress(task)
    assert verdict.reason_code == "zero_progress"
    assert verdict.tokens() == ["span_s=unknown", "signature=none"]
    assert be.build_evidence(task_dir=str(task)).endswith("batch_log=none")


# --- refusal signatures -----------------------------------------------------


@pytest.mark.parametrize(("line", "label"), [
    ("Error: authentication_error: invalid x-api-key", "auth"),
    ("OAuth token has expired. Please run /login", "auth"),
    ('{"type":"not_found_error","message":"model: example-model"}', "model_not_found"),
    ("The model example-model does not exist or you do not have access", "model_not_found"),
    ("Claude AI usage limit reached|1767225600", "usage_limit"),
    ("insufficient_quota: You exceeded your current quota", "usage_limit"),
])
def test_refusal_signatures_are_recognised(line: str, label: str) -> None:
    assert be.refusal_signature(f"agent output\n{line}\n") == label


@pytest.mark.parametrize("line", [
    "API Error: 529 overloaded_error",
    "Rate limit reached for requests",
    "edited scripts/lib/wake_lifecycle.sh:401",
])
def test_transient_errors_and_line_numbers_are_not_refusals(line: str) -> None:
    assert be.refusal_signature(line) is None


def test_only_the_dispatch_span_of_the_batch_log_is_searched(tmp_path: pathlib.Path) -> None:
    """A signature from another task's earlier output in the same batch log is not this task's."""
    task = _task(tmp_path)
    log = tmp_path / "batch.log"
    other = "Error: model not found\n"
    mine = "agent read files\nagent ended turn\n"
    log.write_text(other + mine, encoding="utf-8")
    _ledger(task, log, [
        _row("dispatched", log, "2026-01-01T10:00:00Z", len(other)),
        _row("returned", log, "2026-01-01T10:10:00Z", len(other), len(other + mine)),
    ])
    assert be.classify_zero_progress(task).reason_code == "zero_progress"


def test_a_span_at_the_threshold_is_not_a_refusal(tmp_path: pathlib.Path) -> None:
    task = _task(tmp_path)
    log = tmp_path / "batch.log"
    log.write_text("ok\n", encoding="utf-8")
    _ledger(task, log, [
        _row("dispatched", log, "2026-01-01T10:00:00Z", 0),
        _row("returned", log, "2026-01-01T10:01:00Z", 0, 3),
    ])
    assert be.classify_zero_progress(task).reason_code == "zero_progress"
    assert be.classify_zero_progress(task, min_span_seconds=61).reason_code == "provider_refusal"


# --- git evidence and the worktree failure cause ----------------------------


def _git(repo: pathlib.Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _repo(tmp_path: pathlib.Path) -> pathlib.Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    _git(repo, "config", "user.name", "fixture")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "base")
    _git(repo, "branch", "rc")
    _git(repo, "checkout", "-q", "-b", "feature")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "work")
    return repo


def test_git_evidence_counts_commits_ahead_and_reports_the_merge(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    assert be.git_evidence(str(repo), "", "feature", "rc") == [
        f"worktree={repo}", "branch=feature", "ahead=1", "merged=no"]
    _git(repo, "checkout", "-q", "rc")
    _git(repo, "merge", "-q", "--no-ff", "-m", "merge", "feature")
    gone = str(tmp_path / "removed-worktree")
    assert be.git_evidence(gone, str(repo), "feature", "rc") == [
        "worktree=gone", "branch=feature", "ahead=0", "merged=yes"]


def test_git_evidence_uses_placeholders_for_a_missing_branch(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    assert be.git_evidence("", str(repo), "deleted-branch", "rc") == [
        "worktree=gone", "branch=deleted-branch", "ahead=0", "merged=no"]


def test_worktree_failure_code_names_a_missing_source_branch(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    assert be.worktree_failure_code(str(repo), "rc") == "worktree_create_failed"
    assert be.worktree_failure_code(str(repo), "ai_rc/v0.0.0") == "source_branch_missing"
    not_a_repo = tmp_path / "plain-dir"
    not_a_repo.mkdir()
    assert be.worktree_failure_code(str(not_a_repo), "rc") == "worktree_create_failed"
    assert be.worktree_failure_code("", "rc") == "worktree_create_failed"


def test_diag_token_names_the_last_line_of_the_log(tmp_path: pathlib.Path) -> None:
    log = tmp_path / "wake.log"
    log.write_text("one\ntwo\nthree\n", encoding="utf-8")
    assert be.diag_token(str(log)) == f"diag={log}:3"
    assert be.diag_token(str(tmp_path / "absent.log")) == "diag=none"
    assert be.diag_token("") == "diag=none"


def test_cli_subcommands_exit_zero_and_print_one_line(tmp_path: pathlib.Path, capsys) -> None:
    task = _task(tmp_path, role="writer")
    assert be.main(["owner-role", str(task / "status.md")]) == 0
    assert be.main(["zero-progress-code", str(task)]) == 0
    assert be.main(["evidence", "--diag-log", ""]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "WRITER", "zero_progress", "worktree=gone; branch=none; ahead=0; merged=no; diag=none"]
