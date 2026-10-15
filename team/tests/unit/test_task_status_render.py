"""
test_task_status_render.py
==========================
Unit tests for the text-level rewrites in pgai_agent_kanban.task_status and
the two CLI paths the wake's status readers and writers call:

  (A) replace_sections: ordered replacement, skip_missing, refusal of an
      absent section, separators kept byte-for-byte.
  (B) collapse_blank_lines: runs of blank lines cut to one, nothing else.
  (C) upsert_section: replace when present; insert after the first anchor
      present (blank-line runs then cut); append when no anchor is present.
  (D) ``read --raw``: the body exactly as written (State included, bold and
      extra lines kept), exit 3 with no output for an absent heading, an
      empty body printed as an empty line, an unreadable file reported as
      one plain line with exit 1 and no refusal-ledger record.
  (E) ``render``: prints the rewritten file and writes nothing; accepts
      ## State; fails "Could not update ## <heading> block" (exit 1) for an
      absent section; --skip-missing, --insert-after and
      --collapse-blank-lines; mismatched --section/--body counts refused.
  (F) append_to_section and ``render --append``: the existing body kept and
      the addition after one blank line, an empty body becoming the
      addition alone, separators kept, an absent section refused (or left
      alone with --skip-missing).
  (G) set_or_append_section and ``render --append-missing``: the body
      written verbatim, in place when present (separators kept), appended
      at the end after one blank line when absent (a missing final newline
      added first, trailing blank lines kept); mixed with replaced sections
      and --skip-missing in --section order; bad combinations refused.
  (H) record_field and ``read --field``: one ``key: value`` value from a
      record section, the default for an absent/empty/``none`` record or
      field, a missing file read as empty, an unreadable file exit 1, and
      --raw / a missing --section refused.

Naming: function names describe behavior under test, never bug IDs or task
IDs (roles/TESTER.md "Test Authoring Guidelines", Anti-pattern 6).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pgai_agent_kanban.task_status import (
    EXIT_SECTION_ABSENT,
    TaskStatusError,
    append_to_section,
    collapse_blank_lines,
    main,
    record_field,
    replace_sections,
    set_or_append_section,
    upsert_section,
)

_TEXT = (
    "# Status\n\n## Role\nCODER\n\n## State\nWORKING\n\n\n## Summary\nold summary\n\n"
    "## Blockers\nnone\n"
)


@pytest.fixture(autouse=True)
def _no_refusal_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the error emitter from recording refusals against a real project."""
    monkeypatch.delenv("PGAI_PROJECT_NAME", raising=False)


def _file(tmp_path: Path, text: str = _TEXT) -> Path:
    path = tmp_path / "status.md"
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# (A) replace_sections
# ---------------------------------------------------------------------------


def test_replace_sections_rewrites_each_named_body_and_keeps_separators() -> None:
    result = replace_sections(_TEXT, {"State": "BACKLOG", "Summary": "  new  "})
    assert result == (
        "# Status\n\n## Role\nCODER\n\n## State\nBACKLOG\n\n\n## Summary\nnew\n\n"
        "## Blockers\nnone\n"
    )


def test_replace_sections_refuses_an_absent_section() -> None:
    with pytest.raises(TaskStatusError) as excinfo:
        replace_sections(_TEXT, {"State": "BACKLOG", "Needs Human": "no"})
    assert excinfo.value.error_key == "task-status.missing-section"


def test_replace_sections_skip_missing_leaves_absent_sections_alone() -> None:
    result = replace_sections(_TEXT, {"Needs Human": "no", "State": "DONE"}, skip_missing=True)
    assert result == _TEXT.replace("## State\nWORKING", "## State\nDONE")


# ---------------------------------------------------------------------------
# (B) collapse_blank_lines
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("a\n\n\nb\n", "a\n\nb\n"),
        ("a\n\n\n\n\nb\n\n\n", "a\n\nb\n\n"),
        ("a\n\nb\n", "a\n\nb\n"),
        ("a\nb", "a\nb"),
    ],
    ids=["one-extra", "long-runs", "already-single", "no-blank-lines"],
)
def test_collapse_blank_lines_cuts_runs_to_one(text: str, expected: str) -> None:
    assert collapse_blank_lines(text) == expected


# ---------------------------------------------------------------------------
# (C) upsert_section
# ---------------------------------------------------------------------------


def test_upsert_section_replaces_a_present_section_only() -> None:
    text = "## Role\nR\n\n## Model\nold\n\n\n## State\nX\n"
    assert upsert_section(text, "Model", "new", after=("Role",)) == "## Role\nR\n\n## Model\nnew\n\n\n## State\nX\n"


def test_upsert_section_inserts_after_the_first_present_anchor_and_cuts_runs() -> None:
    text = "## Participant\nP\n\n## Role\nR\n\n\n## State\nX\n\n\n\n## Summary\ny\n"
    assert upsert_section(text, "Model", " m ", after=("Role", "Participant")) == (
        "## Participant\nP\n\n## Role\nR\n\n## Model\nm\n\n## State\nX\n\n## Summary\ny\n"
    )


def test_upsert_section_falls_back_to_a_later_anchor() -> None:
    text = "## Participant\nP\n\n## State\nX\n"
    assert upsert_section(text, "Model", "m", after=("Role", "Participant")) == (
        "## Participant\nP\n\n## Model\nm\n\n## State\nX\n"
    )


@pytest.mark.parametrize("text", ["## State\nX\n\n\n", "## State\nX"], ids=["trailing-runs", "no-newline"])
def test_upsert_section_appends_when_no_anchor_is_present(text: str) -> None:
    assert upsert_section(text, "Model", "m", after=("Role",)) == "## State\nX\n\n## Model\nm\n"


# ---------------------------------------------------------------------------
# (D) read --raw
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("block", "printed"),
    [
        ("## State\nWORKING\n\n", "WORKING\n"),
        ("## State\n**WORKING**\n\n", "**WORKING**\n"),
        ("## State\n\n\nworking (resumed)\nsecond\n\n", "working (resumed)\nsecond\n"),
        ("## State\n\n", "\n"),
    ],
    ids=["canonical", "bold-kept", "body-as-written", "empty-body"],
)
def test_read_raw_prints_the_body_as_written(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], block: str, printed: str
) -> None:
    path = _file(tmp_path, f"## Role\nR\n\n{block}## Summary\ns\n")
    assert main(["read", str(path), "--section", "State", "--raw"]) == 0
    assert capsys.readouterr().out == printed


def test_read_raw_reads_another_section(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _file(tmp_path)
    assert main(["read", str(path), "--section=Blockers", "--raw"]) == 0
    assert capsys.readouterr().out == "none\n"


def test_read_raw_absent_heading_exits_3_printing_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _file(tmp_path)
    assert main(["read", str(path), "--section", "Needs Human", "--raw"]) == EXIT_SECTION_ABSENT == 3
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "")


def test_read_raw_unreadable_file_is_one_plain_line_and_records_no_refusal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded: list[tuple] = []
    monkeypatch.setattr(
        "pgai_agent_kanban.task_status.emit_error_contract", lambda *a, **k: recorded.append(a)
    )
    missing = tmp_path / "absent.md"
    assert main(["read", str(missing), "--raw"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith(f"cannot read {missing}: ")
    assert captured.err.count("\n") == 1
    assert recorded == []


# ---------------------------------------------------------------------------
# (E) render
# ---------------------------------------------------------------------------


def test_render_prints_the_rewrite_and_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _file(tmp_path)
    assert main(["render", str(path), "--section=State", "--body=BLOCKED", "--section=Summary", "--body=- dash"]) == 0
    assert capsys.readouterr().out == replace_sections(_TEXT, {"State": "BLOCKED", "Summary": "- dash"})
    assert path.read_text(encoding="utf-8") == _TEXT


def test_render_absent_section_fails_naming_it(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _file(tmp_path)
    assert main(["render", str(path), "--section=State", "--body=X", "--section=Needs Human", "--body=no"]) == 1
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "Could not update ## Needs Human block\n")


def test_render_skip_missing_and_collapse(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _file(tmp_path)
    argv = ["render", str(path), "--section=Needs Human", "--body=no", "--section=State", "--body=BACKLOG",
            "--skip-missing", "--collapse-blank-lines"]
    assert main(argv) == 0
    assert capsys.readouterr().out == collapse_blank_lines(_TEXT.replace("## State\nWORKING", "## State\nBACKLOG"))


def test_render_collapse_alone_normalises_the_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _file(tmp_path)
    assert main(["render", str(path), "--collapse-blank-lines"]) == 0
    assert capsys.readouterr().out == collapse_blank_lines(_TEXT)


def test_render_insert_after_upserts(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _file(tmp_path)
    assert main(["render", str(path), "--section=Model", "--body=m", "--insert-after=Role"]) == 0
    assert capsys.readouterr().out == upsert_section(_TEXT, "Model", "m", after=("Role",))


@pytest.mark.parametrize(
    "argv",
    [
        ["--section=State"],
        ["--section=State", "--body=a", "--body=b"],
        ["--section=Model", "--body=m", "--section=State", "--body=X", "--insert-after=Role"],
    ],
    ids=["body-missing", "extra-body", "insert-after-with-two-sections"],
)
def test_render_refuses_mismatched_arguments(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    path = _file(tmp_path)
    assert main(["render", str(path), *argv]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("render: ")
    assert path.read_text(encoding="utf-8") == _TEXT


def test_render_unreadable_file_fails_plainly(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    missing = tmp_path / "absent.md"
    assert main(["render", str(missing), "--section=State", "--body=X"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith(f"cannot read {missing}: ")


# ---------------------------------------------------------------------------
# (F) append_to_section / render --append
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("## Summary\nold\n\n## Blockers\nnone\n", "## Summary\nold\n\nF\n\n## Blockers\nnone\n"),
        ("## Summary\n\n## Blockers\nnone\n", "## Summary\nF\n\n## Blockers\nnone\n"),
        ("## Summary\n## Blockers\nnone\n", "## Summary\nF\n## Blockers\nnone\n"),
        ("## Summary\na\n\nb\n\n\n## X\n", "## Summary\na\n\nb\n\nF\n\n\n## X\n"),
        ("## State\nDONE\n\n## Summary\nold", "## State\nDONE\n\n## Summary\nold\n\nF\n"),
    ],
    ids=["populated", "empty-then-blank", "empty-then-heading", "paragraphs-separator-kept", "last-no-newline"],
)
def test_append_to_section_keeps_the_body_and_adds_after_a_blank_line(before: str, after: str) -> None:
    assert append_to_section(before, "Summary", " F ") == after


def test_append_to_section_refuses_an_absent_section() -> None:
    with pytest.raises(TaskStatusError) as excinfo:
        append_to_section("## State\nDONE\n", "Summary", "F")
    assert excinfo.value.error_key == "task-status.missing-section"


def test_render_append_prints_the_appended_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _file(tmp_path)
    assert main(["render", str(path), "--section", "Summary", "--body", "F", "--append"]) == 0
    assert capsys.readouterr().out == append_to_section(_TEXT, "Summary", "F")
    assert path.read_text(encoding="utf-8") == _TEXT


def test_render_append_skip_missing_leaves_the_file_unchanged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    text = "## State\nDONE\n"
    path = _file(tmp_path, text)
    assert main(["render", str(path), "--section", "Summary", "--body", "F", "--append", "--skip-missing"]) == 0
    assert capsys.readouterr().out == text
    assert main(["render", str(path), "--section", "Summary", "--body", "F", "--append"]) == 1
    assert capsys.readouterr().err == "Could not update ## Summary block\n"


def test_render_append_refuses_insert_after(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _file(tmp_path)
    argv = ["render", str(path), "--section", "Summary", "--body", "F", "--append", "--insert-after", "Role"]
    assert main(argv) == 1
    assert "cannot be combined" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# (G) set_or_append_section and render --append-missing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("## A\nx\n\n## R\nold\n\n\n## B\ny\n", "## A\nx\n\n## R\nk: v \n\n\n## B\ny\n"),
        ("## A\nx\n\n## R\n## B\ny\n", "## A\nx\n\n## R\nk: v \n## B\ny\n"),
        ("## A\nx\n\n## R   \nold", "## A\nx\n\n## R   \nk: v \n"),
        ("## A\nx\n", "## A\nx\n\n## R\nk: v \n"),
        ("## A\nx", "## A\nx\n\n## R\nk: v \n"),
        ("## A\nx\n\n\n", "## A\nx\n\n\n\n## R\nk: v \n"),
        ("", "\n\n## R\nk: v \n"),
    ],
    ids=["present-separator-kept", "empty-body", "last-no-newline", "absent", "absent-no-newline",
         "absent-trailing-blank-lines", "empty-text"],
)
def test_set_or_append_section_writes_the_body_verbatim(before: str, after: str) -> None:
    assert set_or_append_section(before, "R", "k: v ") == after


def test_render_append_missing_mixes_with_replaced_and_skipped_sections(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _file(tmp_path)
    argv = [
        "render", str(path),
        "--section", "State", "--body", " BACKLOG ",
        "--section", "Labels", "--body", "TRANSIENT",
        "--section", "Needs Human", "--body", "no",
        "--append-missing", "Labels", "--skip-missing", "--collapse-blank-lines",
    ]
    assert main(argv) == 0
    assert capsys.readouterr().out == (
        "# Status\n\n## Role\nCODER\n\n## State\nBACKLOG\n\n## Summary\nold summary\n\n"
        "## Blockers\nnone\n\n## Labels\nTRANSIENT\n"
    )
    assert path.read_text(encoding="utf-8") == _TEXT


def test_render_append_missing_without_skip_missing_still_refuses_other_absent_sections(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _file(tmp_path)
    argv = ["render", str(path), "--section", "Labels", "--body", "T", "--section", "Needs Human",
            "--body", "no", "--append-missing", "Labels"]
    assert main(argv) == 1
    assert capsys.readouterr().err == "Could not update ## Needs Human block\n"


@pytest.mark.parametrize(
    ("flags", "message"),
    [
        (["--append-missing", "Labels", "--append"], "cannot be combined"),
        (["--append-missing", "Labels", "--insert-after", "Role"], "cannot be combined"),
        (["--append-missing", "Other"], "--append-missing Other names no --section"),
    ],
    ids=["with-append", "with-insert-after", "unpaired-heading"],
)
def test_render_append_missing_refuses_bad_combinations(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], flags: list[str], message: str
) -> None:
    path = _file(tmp_path)
    argv = ["render", str(path), "--section", "Labels", "--body", "T", *flags]
    assert main(argv) == 1
    assert message in capsys.readouterr().err


# ---------------------------------------------------------------------------
# (H) record_field and read --field
# ---------------------------------------------------------------------------

_RECORD_TEXT = (
    "## State\nWORKING\n\n## Zero Progress\nreason_code: stale\ncount: 2\nlimit: 3\n"
    "detail: no change  \n\n## Summary\ncount: 9\n"
)


@pytest.mark.parametrize(
    ("text", "field", "expected"),
    [
        (_RECORD_TEXT, "count", "2"),
        (_RECORD_TEXT, "detail", "no change"),
        (_RECORD_TEXT, "missing", "d"),
        ("## Zero Progress\nnone\n", "count", "d"),
        ("## Zero Progress\nNone\n", "count", "d"),
        ("## Zero Progress\n\n## Summary\ncount: 9\n", "count", "d"),
        ("## State\nX\n", "count", "d"),
        ("## Zero Progress\ncount:\nlimit: 3\n", "count", "limit: 3"),
        ("## zero progress\ncount: 4\n", "count", "4"),
        ("## Zero Progress\r\ncount: 5\r\n", "count", "5"),
    ],
    ids=["value", "trailing-space-stripped", "absent-field", "none", "none-any-case", "empty-body",
         "absent-section", "empty-value-runs-on", "lowercase-heading", "crlf"],
)
def test_record_field_reads_one_value_or_the_default(text: str, field: str, expected: str) -> None:
    assert record_field(text, "Zero Progress", field, "d") == expected


def test_read_field_prints_the_value_or_the_default(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _file(tmp_path, _RECORD_TEXT)
    assert main(["read", str(path), "--section", "Zero Progress", "--field", "count"]) == 0
    assert capsys.readouterr().out == "2\n"
    assert main(["read", str(path), "--section", "Zero Progress", "--field", "nope", "--default=-1"]) == 0
    assert capsys.readouterr().out == "-1\n"
    assert main(["read", str(path), "--section", "Transient Retry", "--field", "retry_count"]) == 0
    assert capsys.readouterr().out == "\n"


def test_read_field_on_a_missing_file_prints_the_default(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    missing = tmp_path / "absent" / "status.md"
    assert main(["read", str(missing), "--section", "Zero Progress", "--field", "count", "--default", "0"]) == 0
    assert capsys.readouterr().out == "0\n"


def test_read_field_on_an_unreadable_file_exits_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "status.md"
    path.write_bytes(b"## Zero Progress\ncount: \xff\n")
    assert main(["read", str(path), "--section", "Zero Progress", "--field", "count"]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err.startswith(f"cannot read {path}")


@pytest.mark.parametrize(
    ("argv_tail", "message"),
    [
        (["--section", "Zero Progress", "--field", "count", "--raw"], "--field and --raw cannot be combined"),
        (["--field", "count"], "--field needs --section"),
    ],
    ids=["with-raw", "no-section"],
)
def test_read_field_refuses_bad_combinations(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], argv_tail: list[str], message: str
) -> None:
    path = _file(tmp_path, _RECORD_TEXT)
    assert main(["read", str(path), *argv_tail]) == 1
    assert message in capsys.readouterr().err
