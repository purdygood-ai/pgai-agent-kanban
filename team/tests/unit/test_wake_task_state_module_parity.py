"""
test_wake_task_state_module_parity.py
=====================================
Behaviour preservation for the status.md and queue-file readers and writers
in team/scripts/lib/wake_task_state.sh, whose parsing lives in the shared
pgai_agent_kanban modules (task_status ``read --raw`` / ``render``; ops
``render_marker`` / ``get_markers`` / ``scan_queue``).

Each scenario runs twice on byte-identical fixtures at the same path: once
with the module-backed functions (the real lib, sourced), once with the
heredoc-backed reference implementation of the same functions
(tests/fixtures/wake_task_state_heredoc_reference.sh, sourced after the
real lib so its definitions replace the converted ones). The two runs must
agree on exit code, stdout, stderr and the bytes of every fixture file.

Covered functions: get_state, set_state, set_block, normalize_status_file,
stamp_model_field, mark_backlog, promote_waiting_to_backlog,
promote_blocked_to_backlog, get_first_pending_task, recheck_waiting_tasks,
recheck_blocked_tasks (and through them _read_status_section).

Fixture variants: the status.md shapes the writers and hand-edits produce
(canonical, blank lines before the value, a bold value, trailing text after
the value, trailing spaces on the heading, an empty body, a two-line body,
no trailing newline, runs of blank lines, CRLF for the readers; a BLOCKED
task carrying a ## Block Record for the BLOCKED promotion) and queue
lines with every marker, the "[]" alias, extra spacing, no dash, no
description, a key that prefixes another key, and a missing entry.

Inputs the heredoc readers refused, and the shared modules accept by
design, are pinned separately below ("deliberate differences"). Each test
there asserts the module-backed behaviour and that the reference behaved
differently, so a reader can see exactly what moved:
  * an inline ``## State: VALUE`` heading reads and rewrites;
  * a lowercase ``## state`` heading reads and rewrites;
  * a ``### subheading`` stays inside the section body it sits in;
  * a ``##   State`` heading (extra inner spacing) reads as State in the
    pending scan, and a WAITING promotion rewrites it;
  * a queue line with an unknown bracket character is still rewritten;
  * stamp_model_field inserts after a ``## Role`` that is the last section;
  * a missing section in promote_waiting_to_backlog is named without the
    trailing " in status.md".

Every fixture lives under pytest's tmp_path; the subprocess environment is
built explicitly and its working directory is the fixture root, so module
resolution through pp_run_ops is shown not to depend on the caller's cwd.

Naming: function names describe behavior under test, never bug IDs or task
IDs (roles/TESTER.md "Test Authoring Guidelines", Anti-pattern 6).
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import textwrap

import pytest

# This file lives at team/tests/unit/, so three parents up is team/.
_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent
_LIB_DIR = _TEAM_DIR / "scripts" / "lib"
_WAKE_TASK_STATE_LIB = _LIB_DIR / "wake_task_state.sh"
_REFERENCE_LIB = _TEAM_DIR / "tests" / "fixtures" / "wake_task_state_heredoc_reference.sh"

_KEY = "CODER-20260101-001-parity-task"
_OTHER = "CODER-20260101-002-parity-other"
_IMPLS = ("module", "reference")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _script(impl: str, root: pathlib.Path, calls: str) -> str:
    """Bash that loads the *impl* functions against *root*, then runs *calls*.

    errexit is off so a failing call reports its own exit code; each call
    line is expected to print ``RC=<n>`` itself where the code matters.
    """
    reference = f'source "{_REFERENCE_LIB}"\n' if impl == "reference" else ""
    return (
        textwrap.dedent(f"""\
            #!/usr/bin/env bash
            set -uo pipefail
            BACKLOG="{root / 'coder_backlog.md'}"
            TASKS_ROOT="{root / 'tasks'}"
            log() {{ printf 'LOG: %s\\n' "$*"; }}
            get_release_state_field() {{ echo none; }}
            transient_retry_field() {{ printf '%s\\n' "${{3:-}}"; }}
            transient_retry_limit() {{ echo 3; }}
            source "{_WAKE_TASK_STATE_LIB}"
        """)
        + reference
        + calls
        + "\n"
    )


def _run(impl: str, root: pathlib.Path, files: dict[str, str], calls: str) -> dict:
    """Lay *files* out under a fresh *root*, run *calls* with *impl*, and
    return the exit code, both streams and the bytes of every file."""
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    temp_dir = root.parent / f"{root.name}-kanban-temp"
    temp_dir.mkdir(exist_ok=True)
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(root),
        "LANG": "C.UTF-8",
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(temp_dir),
        "KANBAN_ROOT": str(temp_dir),
    }
    proc = subprocess.run(
        ["bash", "-c", _script(impl, root, calls)],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        cwd=str(root),
    )
    snapshot = {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }
    return {"rc": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr, "files": snapshot}


def _both(tmp_path: pathlib.Path, files: dict[str, str], calls: str) -> tuple[dict, dict]:
    """Run *calls* with the module-backed and the reference functions."""
    root = tmp_path / "fixture"
    return _run("module", root, files, calls), _run("reference", root, files, calls)


def _assert_same(module: dict, reference: dict) -> None:
    for field in ("rc", "stdout", "stderr", "files"):
        assert module[field] == reference[field], (
            f"{field} differs between the module-backed and the reference functions\n"
            f"module:    {module[field]!r}\nreference: {reference[field]!r}\n"
            f"module stderr: {module['stderr']}\nreference stderr: {reference['stderr']}"
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _status(state_block: str, *, tail: str = "") -> str:
    return (
        f"# Status\n\n## Task\n{_KEY}\n\n## Participant\nClaude\n\n## Role\nCODER\n\n"
        f"{state_block}"
        "## Summary\nWorking on it.\n\n## Blockers\nnone\n\n## Needs Human\nno\n\n"
        "## Blocked Reason\nnone\n\n## Next Recommended Step\nTBD\n"
        f"{tail}"
    )


# The writer variants of a status.md whose ## State reads WORKING.
_STATUS_VARIANTS = {
    "canonical": _status("## State\nWORKING\n\n"),
    "blank-lines-before-value": _status("## State\n\n\nWORKING\n\n"),
    "bold-value": _status("## State\n**WORKING**\n\n"),
    "trailing-text": _status("## State\nWORKING (resumed after an interrupted wake)\n\n"),
    "heading-trailing-spaces": _status("## State  \nWORKING\n\n"),
    "empty-body": _status("## State\n\n"),
    "two-line-body": _status("## State\nWORKING\nsecond line\n\n"),
    "no-blank-line-before-next": _status("## State\nWORKING\n"),
    "blank-line-runs": _status("## State\nWORKING\n\n\n\n", tail="\n\n\n"),
    "state-last-no-newline": "# Status\n\n## Role\nCODER\n\n## Summary\nx\n\n## State\nWORKING",
}

# Reader-only variants: shapes the heredoc readers also read.
_READER_VARIANTS = {
    **_STATUS_VARIANTS,
    "crlf": _status("## State\nWORKING\n\n").replace("\n", "\r\n"),
    "lowercase-value": _status("## State\nworking\n\n"),
    "no-state-heading": "# Status\n\n## Summary\nWORKING\n",
}


def _queue(*lines: str) -> str:
    return "# Coder Backlog\n\n" + "\n".join(lines) + "\n"


# Queue shapes carrying a line for _KEY (plus a neighbour that must not move).
_QUEUE_VARIANTS = {
    "pending": _queue(f"- [x] {_OTHER} — done", f"- [ ] {_KEY} — the task"),
    "waiting": _queue(f"- [W] {_KEY} — the task", f"- [ ] {_OTHER} — next"),
    "working": _queue(f"- [A] {_KEY} — the task"),
    "blocked": _queue(f"- [B] {_KEY} — the task"),
    "done": _queue(f"- [x] {_KEY} — the task"),
    "alias": _queue(f"- [] {_KEY} — alias marker"),
    "spaced": _queue(f"-   [ W ]   {_KEY}   — extra whitespace"),
    "no-description": f"# Coder Backlog\n\n- [A] {_KEY}",
    "no-dash": _queue(f"[B] {_KEY} — no list dash"),
    "key-prefixes-another": _queue(f"- [A] {_KEY}-followup — not this one", f"- [W] {_KEY} — this one"),
    "duplicate-lines": _queue(f"- [A] {_KEY} — first", f"- [B] {_KEY} — second"),
    "missing-entry": _queue(f"- [ ] {_OTHER} — someone else"),
}


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("variant", sorted(_READER_VARIANTS))
def test_get_state_matches_reference(tmp_path: pathlib.Path, variant: str) -> None:
    module, reference = _both(
        tmp_path,
        {"status.md": _READER_VARIANTS[variant]},
        'state="$(get_state status.md)"; echo "[$state] RC=$?"',
    )
    _assert_same(module, reference)


def test_get_state_on_a_missing_file_fails_naming_the_file(tmp_path: pathlib.Path) -> None:
    # The two implementations word the unreadable-file line differently (a
    # Python traceback before, one plain line now); the refusal itself and
    # the exit code are the same.
    root = tmp_path / "fixture"
    for impl in _IMPLS:
        result = _run(impl, root, {}, "get_state absent.md; echo RC=$?")
        assert result["stdout"] == "RC=1\n", result
        assert "Could not parse ## State block in absent.md" in result["stderr"], result


# ---------------------------------------------------------------------------
# status.md writers
# ---------------------------------------------------------------------------

_WRITER_CALLS = {
    "set_state": "set_state status.md BLOCKED; echo RC=$?",
    "set_state-padded-value": "set_state status.md '  DONE  '; echo RC=$?",
    "set_block-summary": "set_block status.md Summary $'two\\nlines'; echo RC=$?",
    "set_block-needs-human": "set_block status.md 'Needs Human' yes; echo RC=$?",
    "set_block-absent-section": "set_block status.md 'Blocked By Agent' coder; echo RC=$?",
    "normalize_status_file": "normalize_status_file status.md; echo RC=$?",
    "stamp_model_field": "stamp_model_field status.md ' model-a '; echo RC=$?",
}


@pytest.mark.parametrize("call", sorted(_WRITER_CALLS))
@pytest.mark.parametrize("variant", sorted(_STATUS_VARIANTS))
def test_status_writer_matches_reference(tmp_path: pathlib.Path, variant: str, call: str) -> None:
    module, reference = _both(tmp_path, {"status.md": _STATUS_VARIANTS[variant]}, _WRITER_CALLS[call])
    _assert_same(module, reference)


_MODEL_SHAPES = {
    "model-present": "# Status\n\n## Role\nCODER\n\n## Model\nold-model\n\n## State\nWORKING\n",
    "model-empty-not-last": "## Role\nTESTER\n\n## Model\n\n## State\nWORKING\n",
    "model-empty-last": "## Role\nTESTER\n\n## State\nWORKING\n\n## Model\n",
    "no-role-after-participant": "## Participant\nClaude\n\n## State\nWORKING\n",
    "no-anchor-append": "## State\nWORKING\n\n\n",
    "no-anchor-no-newline": "## State\nWORKING",
    "role-empty-body": "## Role\n\n## State\nWORKING\n",
    "role-then-blank-runs": "## Role\nCODER\n\n\n## State\nWORKING\n\n\n\n## Summary\nx\n",
    "role-last-only-section-after-state": "## State\nWORKING\n\n## Role\nCODER\n",
}


@pytest.mark.parametrize("shape", sorted(_MODEL_SHAPES))
def test_stamp_model_field_matches_reference_on_model_shapes(tmp_path: pathlib.Path, shape: str) -> None:
    module, reference = _both(
        tmp_path, {"status.md": _MODEL_SHAPES[shape]}, "stamp_model_field status.md model-b; echo RC=$?"
    )
    _assert_same(module, reference)


def test_set_state_without_a_state_section_fails_and_writes_nothing(tmp_path: pathlib.Path) -> None:
    module, reference = _both(
        tmp_path,
        {"status.md": "# Status\n\n## Summary\nno state here\n"},
        "set_state status.md BLOCKED; echo RC=$?",
    )
    _assert_same(module, reference)
    assert module["stdout"] == "RC=1\n"
    assert module["stderr"] == "Could not update ## State block\n"


# ---------------------------------------------------------------------------
# Queue marker writer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("marker", [" ", "W", "A", "B", "x"], ids=lambda m: f"to[{m}]")
@pytest.mark.parametrize("variant", sorted(_QUEUE_VARIANTS))
def test_mark_backlog_matches_reference(tmp_path: pathlib.Path, variant: str, marker: str) -> None:
    module, reference = _both(
        tmp_path,
        {"coder_backlog.md": _QUEUE_VARIANTS[variant]},
        f'mark_backlog "{_KEY}" "{marker}"; echo RC=$?',
    )
    _assert_same(module, reference)


# ---------------------------------------------------------------------------
# Promotion writers
# ---------------------------------------------------------------------------

_WAITING_STATUS = _status("## State\nWAITING\n\n")
_BLOCKED_STATUS = (
    _status("## State\nBLOCKED\n\n", tail="\n## Blocked By Agent\ncoder\n\n\n")
)
_BLOCKED_STATUS_SPARSE = "## State\nBLOCKED\n\n## Summary\nstuck\n"
# A BLOCKED task carrying the durable ## Block Record (the shape
# pgai_agent_kanban.overwatch.block_record writes): promotion leaves it alone.
_BLOCKED_STATUS_WITH_BLOCK_RECORD = _status(
    "## State\nBLOCKED\n\n",
    tail=(
        "\n## Block Record\nreason_code: per_task_timeout\nowner_role: CODER\nattempts: 1\n"
        "first_blocked_at: 2026-01-01T10:00:00+00:00\nlast_blocked_at: 2026-01-01T11:00:00+00:00\n"
        "cooldown_until: none\nevidence: worktree=gone; ahead=2\nresume: branch\n"
    ),
)


@pytest.mark.parametrize("variant", sorted(_QUEUE_VARIANTS))
@pytest.mark.parametrize(
    "status", [_WAITING_STATUS, _status("## State\n**WAITING**\n\n", tail="\n\n\n")], ids=["waiting", "bold-and-runs"]
)
def test_promote_waiting_to_backlog_matches_reference(tmp_path: pathlib.Path, variant: str, status: str) -> None:
    module, reference = _both(
        tmp_path,
        {"coder_backlog.md": _QUEUE_VARIANTS[variant], f"tasks/{_KEY}/status.md": status},
        f'promote_waiting_to_backlog "{_KEY}" "tasks/{_KEY}/status.md"; echo RC=$?',
    )
    _assert_same(module, reference)


@pytest.mark.parametrize("variant", sorted(_QUEUE_VARIANTS))
@pytest.mark.parametrize(
    "status",
    [_BLOCKED_STATUS, _BLOCKED_STATUS_SPARSE, _BLOCKED_STATUS_WITH_BLOCK_RECORD],
    ids=["full", "sparse", "with-block-record"],
)
def test_promote_blocked_to_backlog_matches_reference(tmp_path: pathlib.Path, variant: str, status: str) -> None:
    module, reference = _both(
        tmp_path,
        {"coder_backlog.md": _QUEUE_VARIANTS[variant], f"tasks/{_KEY}/status.md": status},
        f'promote_blocked_to_backlog "{_KEY}" "tasks/{_KEY}/status.md" "Active RC=none"; echo RC=$?',
    )
    _assert_same(module, reference)


# ---------------------------------------------------------------------------
# Queue scans
# ---------------------------------------------------------------------------


def _task_id(n: int) -> str:
    return f"CODER-20260101-{n:03d}-scan-task"


# (marker as written, ## State block or None for no status.md)
_SCAN_ROWS = [
    ("- [ ]", "## State\nWORKING\n"),
    ("- [x]", "## State\nBACKLOG\n"),
    ("- [B]", "## State\nBLOCKED\n"),
    ("- []", "## State\nBACKLOG\n"),
    ("- [W]", "## State\nBACKLOG\n"),
    ("- [ ]", "## State\nWAITING\n"),
    ("-   [ B ] ", "## State\nblocked\n"),
    ("[ ]", "## State\n**BACKLOG**\n"),
    ("- []", "## State\nWAITING\n"),
    ("- [ ]", "## State\n\n"),
    ("- [B]", None),
    ("- [ ]", "## State\nBACKLOG (requeued)\n"),
    ("- [ ]", "## State\nbacklog\n"),
    ("- [ ]", "##   State\nWAITING\n"),
    ("- [B]", "## State\n\n\nBLOCKED\n"),
    ("- [ ]", None),
]


def _scan_files(rows: list[tuple[str, str | None]], *, readme: str = "## Prerequisites\nnone\n") -> dict[str, str]:
    files = {}
    lines = ["- [ ] ../escape — malformed id is narrated and skipped"]
    for n, (marker, state) in enumerate(rows, start=1):
        key = _task_id(n)
        lines.append(f"{marker} {key} — row {n}")
        files[f"tasks/{key}/README.md"] = f"# Task\n\n{readme}"
        if state is not None:
            files[f"tasks/{key}/status.md"] = (
                f"## Task\n{key}\n\n{state}\n## Summary\nx\n\n## Blockers\nnone\n\n"
                "## Needs Human\nno\n\n## Blocked Reason\nwaiting on Active RC\n"
            )
    files["coder_backlog.md"] = _queue(*lines)
    return files


def _without_spaced_heading(rows: list[tuple[str, str | None]]) -> list[tuple[str, str | None]]:
    """*rows* minus those whose ## State heading has extra inner spacing,
    which the deliberate-difference tests below cover."""
    return [row for row in rows if not (row[1] or "").startswith("##   ")]


@pytest.mark.parametrize("start", range(0, len(_SCAN_ROWS), 3), ids=lambda s: f"from-row-{s + 1}")
def test_get_first_pending_task_matches_reference(tmp_path: pathlib.Path, start: int) -> None:
    rows = _without_spaced_heading(_SCAN_ROWS[start:])
    module, reference = _both(
        tmp_path, _scan_files(rows), 'next="$(get_first_pending_task)"; echo "RC=$? NEXT=[$next]"'
    )
    _assert_same(module, reference)


def test_pending_scan_serves_every_pending_entry_in_order_like_reference(tmp_path: pathlib.Path) -> None:
    rows = _without_spaced_heading(_SCAN_ROWS)
    calls = textwrap.dedent("""\
        while next="$(get_first_pending_task)"; do
          echo "SERVED=[$next]"
          mark_backlog "$next" x || break
        done
    """)
    module, reference = _both(tmp_path, _scan_files(rows), calls)
    _assert_same(module, reference)
    assert "SERVED=" in module["stdout"]


@pytest.mark.parametrize("readme", ["## Prerequisites\nnone\n", "## Prerequisites\nCODER-20260101-099-never-done\n"])
def test_recheck_waiting_tasks_matches_reference(tmp_path: pathlib.Path, readme: str) -> None:
    files = _scan_files(_without_spaced_heading(_SCAN_ROWS), readme=readme)
    module, reference = _both(tmp_path, files, "recheck_waiting_tasks; echo RC=$?")
    _assert_same(module, reference)


def test_recheck_blocked_tasks_matches_reference(tmp_path: pathlib.Path) -> None:
    module, reference = _both(tmp_path, _scan_files(_SCAN_ROWS), "recheck_blocked_tasks; echo RC=$?")
    _assert_same(module, reference)


@pytest.mark.parametrize("needs_human", ["no", "No", "yes", ""])
@pytest.mark.parametrize("reason", ["waiting on Active RC", "waiting on a credential", ""])
def test_recheck_blocked_tasks_reads_needs_human_and_reason_like_reference(
    tmp_path: pathlib.Path, needs_human: str, reason: str
) -> None:
    status = (
        f"## State\nBLOCKED\n\n## Summary\nx\n\n## Blockers\nnone\n\n## Needs Human\n{needs_human}\n\n"
        f"## Blocked Reason\n{reason}\n\n## Next Recommended Step\nwait for the Active RC to close\n"
    )
    files = {
        "coder_backlog.md": _queue(f"- [B] {_KEY} — the task"),
        f"tasks/{_KEY}/README.md": "# Task\n\n## Prerequisites\nnone\n",
        f"tasks/{_KEY}/status.md": status,
    }
    module, reference = _both(tmp_path, files, "recheck_blocked_tasks; echo RC=$?")
    _assert_same(module, reference)


# ---------------------------------------------------------------------------
# Deliberate differences: inputs the heredoc code refused or misread
# ---------------------------------------------------------------------------


def test_inline_state_value_now_reads_and_rewrites(tmp_path: pathlib.Path) -> None:
    files = {"status.md": _status("## State: WORKING\n\n")}
    calls = 'state="$(get_state status.md)"; echo "[$state] RC=$?"; set_state status.md BLOCKED; echo RC=$?'
    module, reference = _both(tmp_path, files, calls)
    assert module["stdout"] == "[WORKING] RC=0\nRC=0\n", module
    assert "## State\nBLOCKED\n\n## Summary" in module["files"]["status.md"].decode()
    assert reference["stdout"] == "[] RC=1\nRC=1\n", "the heredoc reader refused an inline value"


def test_lowercase_state_heading_now_reads_and_rewrites(tmp_path: pathlib.Path) -> None:
    files = {"status.md": _status("## state\nWORKING\n\n")}
    calls = 'state="$(get_state status.md)"; echo "[$state] RC=$?"; set_state status.md BLOCKED; echo RC=$?'
    module, reference = _both(tmp_path, files, calls)
    assert module["stdout"] == "[WORKING] RC=0\nRC=0\n", module
    assert "## state\nBLOCKED\n\n## Summary" in module["files"]["status.md"].decode()
    assert reference["stdout"] == "[] RC=1\nRC=1\n", "the heredoc reader refused a lowercase heading"


def test_subheading_stays_inside_the_section_body(tmp_path: pathlib.Path) -> None:
    files = {"status.md": "## Summary\nfirst\n\n### Detail\nmore\n\n## State\nWORKING\n"}
    module, reference = _both(tmp_path, files, "set_block status.md Summary replaced; echo RC=$?")
    assert module["files"]["status.md"] == b"## Summary\nreplaced\n\n## State\nWORKING\n"
    assert reference["files"]["status.md"] == b"## Summary\nreplaced\n\n### Detail\nmore\n\n## State\nWORKING\n"


def test_pending_scan_reads_state_heading_with_extra_inner_spacing(tmp_path: pathlib.Path) -> None:
    files = _scan_files([("- [ ]", "##   State\nWORKING\n"), ("- [ ]", "## State\nBACKLOG\n")])
    module, reference = _both(tmp_path, files, 'echo "NEXT=[$(get_first_pending_task)]"')
    assert module["stdout"] == f"NEXT=[{_task_id(2)}]\n", "a WORKING task is not served"
    assert reference["stdout"] == f"NEXT=[{_task_id(1)}]\n", "the heredoc scan missed the spaced heading"


def test_waiting_promotion_rewrites_a_state_heading_with_extra_inner_spacing(tmp_path: pathlib.Path) -> None:
    # Both WAITING scans find the task; only the module-backed writer can
    # rewrite the spaced heading, so only it promotes.
    files = _scan_files([("- [ ]", "##   State\nWAITING\n")])
    module, reference = _both(tmp_path, files, "recheck_waiting_tasks; echo RC=$?")
    status = f"tasks/{_task_id(1)}/status.md"
    assert module["files"][status].startswith(b"## Task\n" + _task_id(1).encode() + b"\n\n##   State\nBACKLOG\n")
    assert "promoted 1 task(s)" in module["stdout"], module["stdout"]
    assert "atomic promotion failed" in reference["stdout"], reference["stdout"]


def test_mark_backlog_rewrites_a_line_with_an_unknown_bracket_character(tmp_path: pathlib.Path) -> None:
    files = {"coder_backlog.md": _queue(f"- [?] {_KEY} — odd marker")}
    module, reference = _both(tmp_path, files, f'mark_backlog "{_KEY}" B; echo RC=$?')
    assert module["stdout"] == "RC=0\n"
    assert module["files"]["coder_backlog.md"].decode() == _queue(f"- [B] {_KEY} — odd marker")
    assert reference["stdout"] == "RC=1\n"


def test_stamp_inserts_after_a_role_that_is_the_last_section(tmp_path: pathlib.Path) -> None:
    files = {"status.md": "## Participant\nClaude\n\n## Role\nCODER\n"}
    module, reference = _both(tmp_path, files, "stamp_model_field status.md model-c; echo RC=$?")
    assert module["files"]["status.md"] == b"## Participant\nClaude\n\n## Role\nCODER\n\n## Model\nmodel-c\n"
    assert reference["files"]["status.md"] == b"## Participant\nClaude\n\n## Model\nmodel-c\n\n## Role\nCODER\n"


def test_promote_waiting_names_a_missing_section_without_the_file_suffix(tmp_path: pathlib.Path) -> None:
    files = {
        "coder_backlog.md": _queue(f"- [W] {_KEY} — the task"),
        f"tasks/{_KEY}/status.md": "## State\nWAITING\n\n## Summary\nx\n",
    }
    calls = f'promote_waiting_to_backlog "{_KEY}" "tasks/{_KEY}/status.md"; echo RC=$?'
    module, reference = _both(tmp_path, files, calls)
    assert (module["rc"], module["stdout"], module["files"]) == (reference["rc"], reference["stdout"], reference["files"])
    assert module["stderr"] == "Could not update ## Blockers block\n"
    assert reference["stderr"] == "Could not update ## Blockers block in status.md\n"
