"""
test_status_utf8_safety.py
==========================
A status.md that is not valid UTF-8 never fails a whole queue scan, and the
status writers never produce one.

  (A) scan_queue: one status.md holding a lone 0xe2 byte is left out of the
      result and named (stderr line and the ``unreadable`` collector); the
      other tasks are still returned. A rule that never reads status.md
      still selects the task. Through pp_run_ops the verb exits 0.
  (B) task_status clip: lines are cut to N characters, never bytes, under
      LC_ALL=C too, so a multibyte 80th character is kept whole; ASCII is
      still cut to exactly N; invalid input bytes print as U+FFFD.
  (C) task_status write paths: render's output and set_section's file are
      valid UTF-8 even when a body carries bytes that are not.

Subprocesses run under LC_ALL=C (cron's effective locale) and, where
noted, with Python's UTF-8 mode switched off as well, so the guarantees do
not lean on the interpreter's locale handling.

Naming: function names describe behavior under test, never bug IDs or task
IDs (roles/TESTER.md "Test Authoring Guidelines", Anti-pattern 6).
"""

from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

from pgai_agent_kanban.ops import parse_scan_rule, scan_queue
from pgai_agent_kanban.task_status import clip_chars, set_section, valid_utf8_text

_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent
_PP_RUN_OPS = _TEAM_DIR / "scripts" / "lib" / "pp_run_ops.sh"
_ID_PATTERN = r"^(CLAUDE-)?[A-Z]+-[0-9]{8}-[0-9]{3}-[a-z0-9-]+$"

_GOOD_A = "CODER-20260101-001-readable-first"
_BAD = "CODER-20260101-002-undecodable"
_GOOD_B = "CODER-20260101-003-readable-last"

# 15 ASCII characters + 64 'x' = 79, so the em dash is the 80th character
# and its first byte (0xe2) is the 80th byte: exactly what `cut -c1-80`
# under a C locale splits.
_SIG_MULTIBYTE_80TH = "API Error: 529 " + "x" * 64 + "—tail"

# Locale settings the subprocesses run under. "utf8-mode-off" also turns off
# Python's automatic UTF-8 mode and locale coercion for the C locale.
_C_LOCALES = {
    "c-locale": {"LC_ALL": "C"},
    "c-locale-utf8-mode-off": {"LC_ALL": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0"},
}


def _env(extra: dict[str, str]) -> dict[str, str]:
    """The inherited environment minus the package-path overrides, plus *extra*.

    PYTHONPATH and KANBAN_ROOT are dropped so pp_run_ops resolves the
    package from this tree, never from an installed copy.
    """
    env = {k: v for k, v in os.environ.items() if k not in ("KANBAN_ROOT", "PYTHONPATH", "LANG", "LC_CTYPE")}
    env.update(extra)
    return env


def _pp_run_ops(module: str, *args: "str | bytes", env_extra: dict[str, str], stdin: bytes | None = None
                ) -> subprocess.CompletedProcess:
    """Run ``pp_run_ops MODULE ARGS...`` in bash; bytes in and out."""
    return subprocess.run(
        ["bash", "-c", f'source "{_PP_RUN_OPS}"; pp_run_ops "$@"', "pp", module, *args],
        input=stdin,
        capture_output=True,
        env=_env(env_extra),
        timeout=60,
    )


def _status(tasks: pathlib.Path, key: str, content: bytes) -> pathlib.Path:
    path = tasks / key / "status.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _scan_tree(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    """A queue of three pending tasks; the middle one's status.md has a lone 0xe2."""
    tasks = tmp_path / "tasks"
    _status(tasks, _GOOD_A, b"# Status\n\n## State\nBACKLOG\n")
    bad = _status(tasks, _BAD, b"# Status\n\n## State\nBACKLOG\n\n## Summary\nsplit \xe2 here\n")
    _status(tasks, _GOOD_B, b"# Status\n\n## State\nBACKLOG\n")
    queue = tmp_path / "coder_backlog.md"
    queue.write_text(
        f"# Coder Backlog\n\n- [ ] {_GOOD_A}\n- [ ] {_BAD}\n- [ ] {_GOOD_B}\n", encoding="utf-8"
    )
    return queue, tasks, bad


# ---------------------------------------------------------------------------
# (A) scan_queue
# ---------------------------------------------------------------------------


def test_scan_queue_returns_the_other_tasks_and_names_the_undecodable_one(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    queue, tasks, bad = _scan_tree(tmp_path)
    unreadable: list[pathlib.Path] = []

    found = scan_queue(
        queue, tasks, [parse_scan_rule(" B=,BACKLOG")], id_pattern=_ID_PATTERN, unreadable=unreadable
    )

    assert found == [_GOOD_A, _GOOD_B], "the readable tasks on both sides of the bad one must be returned"
    assert unreadable == [bad], "the undecodable status.md must be reported to the caller"
    err = capsys.readouterr().err
    assert _BAD in err and str(bad) in err, f"stderr must name the skipped task and its file:\n{err}"


def test_scan_queue_skipping_an_undecodable_task_does_not_consume_the_limit(tmp_path: pathlib.Path) -> None:
    queue, tasks, _ = _scan_tree(tmp_path)
    queue.write_text(f"- [ ] {_BAD}\n- [ ] {_GOOD_A}\n", encoding="utf-8")

    assert scan_queue(queue, tasks, [parse_scan_rule(" B=,BACKLOG")], id_pattern=_ID_PATTERN, limit=1) == [
        _GOOD_A
    ], "the pending scan (limit 1) must serve the next readable task, not stop at the bad one"


def test_scan_queue_any_state_rule_still_selects_an_undecodable_task(tmp_path: pathlib.Path) -> None:
    queue, tasks, _ = _scan_tree(tmp_path)
    unreadable: list[pathlib.Path] = []

    found = scan_queue(queue, tasks, [parse_scan_rule(" =*")], id_pattern=_ID_PATTERN, unreadable=unreadable)

    assert found == [_GOOD_A, _BAD, _GOOD_B], "a '*' rule never reads status.md, so nothing is skipped"
    assert unreadable == []


def test_scan_queue_verb_exits_zero_printing_the_readable_keys(tmp_path: pathlib.Path) -> None:
    queue, tasks, bad = _scan_tree(tmp_path)

    result = _pp_run_ops(
        "pgai_agent_kanban.ops", "scan_queue", str(queue), str(tasks), _ID_PATTERN, "0", " B=,BACKLOG",
        env_extra=_C_LOCALES["c-locale"],
    )

    stderr = result.stderr.decode("utf-8", "replace")
    assert result.returncode == 0, f"one bad status.md must not fail the scan\nstderr={stderr}"
    assert result.stdout.decode("utf-8").split() == [_GOOD_A, _GOOD_B]
    assert str(bad) in stderr, f"the verb must name the skipped file on stderr:\n{stderr}"


# ---------------------------------------------------------------------------
# (B) clip
# ---------------------------------------------------------------------------


def test_clip_chars_counts_characters_not_bytes() -> None:
    clipped = clip_chars(_SIG_MULTIBYTE_80TH, 80)
    assert len(clipped) == 80
    assert clipped.endswith("—"), "the multibyte 80th character must be kept whole"
    assert clip_chars("abc\ndefg", 2) == "ab\nde", "each line is cut, like cut -c"


def test_valid_utf8_text_replaces_escaped_bytes_and_keeps_valid_text() -> None:
    escaped = os.fsdecode(b"ok \xe2 end")
    assert valid_utf8_text(escaped) == "ok � end"
    assert valid_utf8_text("em — dash") == "em — dash"


@pytest.mark.parametrize("locale_name", sorted(_C_LOCALES))
def test_clip_keeps_a_multibyte_80th_character_whole(locale_name: str) -> None:
    result = _pp_run_ops(
        "pgai_agent_kanban.task_status", "clip", "--chars", "80",
        env_extra=_C_LOCALES[locale_name], stdin=(_SIG_MULTIBYTE_80TH + "\n").encode("utf-8"),
    )

    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    text = result.stdout.decode("utf-8")  # strict: raises on invalid UTF-8
    assert text == _SIG_MULTIBYTE_80TH[:80] + "\n"


@pytest.mark.parametrize("locale_name", sorted(_C_LOCALES))
def test_clip_cuts_ascii_to_exactly_80_characters(locale_name: str) -> None:
    ascii_sig = "API Error: 503 " + "y" * 120
    result = _pp_run_ops(
        "pgai_agent_kanban.task_status", "clip", "--chars", "80", ascii_sig,
        env_extra=_C_LOCALES[locale_name],
    )

    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert result.stdout.decode("utf-8") == ascii_sig[:80] + "\n"


def test_clip_prints_invalid_input_bytes_as_replacement_characters() -> None:
    result = _pp_run_ops(
        "pgai_agent_kanban.task_status", "clip", "--chars", "80",
        env_extra=_C_LOCALES["c-locale"], stdin=b"half \xe2\x80 dash\n",
    )

    assert result.returncode == 0
    assert result.stdout.decode("utf-8") == "half � dash\n"


def test_clip_without_a_character_count_exits_one() -> None:
    result = _pp_run_ops("pgai_agent_kanban.task_status", "clip", "text", env_extra=_C_LOCALES["c-locale"])

    assert result.returncode == 1
    assert b"--chars" in result.stderr


# ---------------------------------------------------------------------------
# (C) write paths
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("locale_name", sorted(_C_LOCALES))
def test_render_output_is_valid_utf8_when_a_body_carries_a_split_character(
    tmp_path: pathlib.Path, locale_name: str
) -> None:
    status = _status(tmp_path, "task", "# Status\n\n## State\nBLOCKED\n\n## Blocked Reason\nnone\n".encode())
    # A body cut mid-character by a byte-counting tool: 0xe2 0x80 without its last byte.
    body = b"TRANSIENT API ERROR \xe2\x80\x94 " + _SIG_MULTIBYTE_80TH.encode("utf-8")[:80] + b" \xe2\x80\x94 safe to retry"

    result = _pp_run_ops(
        "pgai_agent_kanban.task_status", "render", str(status), "--section=Blocked Reason", b"--body=" + body,
        env_extra=_C_LOCALES[locale_name],
    )

    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    text = result.stdout.decode("utf-8")  # strict: raises on invalid UTF-8
    assert "TRANSIENT API ERROR — API Error: 529 " in text, "valid characters must survive unchanged"
    assert "� — safe to retry" in text, "the split character must become U+FFFD, not raw bytes"


def test_set_section_writes_valid_utf8_for_a_body_with_escaped_bytes(tmp_path: pathlib.Path) -> None:
    status = _status(tmp_path, "task", "# Status\n\n## State\nBLOCKED\n\n## Blockers\nnone\n".encode())

    set_section(status, "Blockers", os.fsdecode(b"cut \xe2\x80 short"))

    text = status.read_bytes().decode("utf-8")  # strict: raises on invalid UTF-8
    assert "## Blockers\ncut � short\n" in text


# ---------------------------------------------------------------------------
# (D) The status-writer CLI refuses an undecodable status.md dual-channel:
#     error key, a message naming the file and the byte offset, a remedy;
#     a non-zero exit, no traceback, and the file's bytes left as they were.
# ---------------------------------------------------------------------------

_TASK_STATUS_SH = _TEAM_DIR / "scripts" / "task-status.sh"


def test_status_writer_refuses_an_undecodable_status_file_without_a_traceback(
    tmp_path: pathlib.Path,
) -> None:
    root = tmp_path / "root"
    (root / "tmp").mkdir(parents=True)
    (root / "projects.cfg").write_text("[project:demo]\npriority = 1\n", encoding="utf-8")
    key = "CODER-20260101-001-undecodable-write"
    original = b"# Status\n\n## Task\n" + key.encode() + b"\n\n## State\nWORKING\n\n## Summary\nT\xe2D\n"
    status = _status(root / "projects" / "demo" / "tasks", key, original)
    offset = original.index(b"\xe2")

    env = {k: v for k, v in _env({}).items() if k not in ("PGAI_TASKS_DIR", "PGAI_PROJECT_ROOT")}
    env.update(
        KANBAN_ROOT=str(root),
        PGAI_AGENT_KANBAN_ROOT_PATH=str(root),
        PGAI_AGENT_KANBAN_TEMP_DIR=str(root / "tmp"),
    )
    result = subprocess.run(
        ["bash", str(_TASK_STATUS_SH), "--project", "demo", "--key", key, "--state", "BLOCKED",
         "--summary", "x", "--reason-code", "agent_unclassified"],
        capture_output=True,
        env=env,
        timeout=60,
    )
    stderr = result.stderr.decode("utf-8", "replace")

    assert result.returncode != 0, f"the writer must refuse, not succeed\n{stderr}"
    assert "Traceback" not in stderr, f"the refusal must not be a raw traceback\n{stderr}"
    assert "task-status.read-failed" in stderr, f"stable error key missing\n{stderr}"
    assert str(status) in stderr, f"the message must name the file\n{stderr}"
    assert f"byte offset {offset}" in stderr, f"the message must name the byte offset\n{stderr}"
    assert "iconv -c -f utf-8 -t utf-8" in stderr, f"a runnable remedy must be printed\n{stderr}"
    assert status.read_bytes() == original, "nothing may be written over the undecodable file"
