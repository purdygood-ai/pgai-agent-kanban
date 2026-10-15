"""
test_project_summary_symptom_comments.py
========================================
The project summary's one-line summary for a bug is the first line of real
Symptom prose, never HTML comment text.

The shipped bug template opens ``## Symptom`` with a multi-line guidance
comment. These tests pin that ``BugItem.symptom`` and ``_offline_oneliner_bug``
skip such comments (multi-line, inline, and unclosed), that a Symptom holding
only a comment behaves exactly as an empty Symptom does, that a ``<!--``
quoted in prose or inline code is text (the Symptom is its full line, with
nothing from a later section spliced in), that comment-free bugs read
exactly as they always have, and -- through the shipped
``project-summary.sh`` entry point against a throwaway kanban root -- that a
bug made from the real template renders its prose on the Open items line.

All filesystem access is under tmp_path. Nothing reads or writes the live
install.
"""

from __future__ import annotations

import os
import pathlib
import signal
import subprocess

import pytest

from pgai_agent_kanban.reports.project_summary import (
    BugItem,
    _extract_field,
    _offline_oneliner_bug,
)
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]  # team/tests/unit/ -> team/
_BUG_TEMPLATE = _TEAM_DIR / "templates" / "project" / "release" / "BUG-TEMPLATE.md"
_SUMMARY_SCRIPT = _TEAM_DIR / "scripts" / "project-summary.sh"
_PACKAGE_DIR = _TEAM_DIR / "pgai_agent_kanban"

_PROSE = "The dashboard froze after a resize."
_TEMPLATE_PLACEHOLDER = "What was observed."


def _write_bug(directory: pathlib.Path, filename: str, content: str) -> pathlib.Path:
    """Write a bug markdown file under *directory* and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(content, encoding="utf-8")
    return path


def _bug_text(symptom_body: str, *, after: str = "## Expected\nIt keeps working.\n") -> str:
    """A minimal bug file whose Symptom section body is *symptom_body*."""
    return (
        "# BUG-0001-sample\n\n"
        "**Severity:** high\n\n"
        "## Status\nopen\n\n"
        f"## Symptom\n{symptom_body}\n"
        f"{after}"
    )


def _assert_no_comment_text(value: str) -> None:
    assert "<!--" not in value and "-->" not in value, (
        f"comment text leaked into the value: {value!r}"
    )


def _bug_from_template() -> str:
    """The shipped bug template with its placeholder replaced by prose.

    The guidance comment is kept, exactly as a filer who follows the
    template's guidance leaves it.
    """
    template = _BUG_TEMPLATE.read_text(encoding="utf-8")
    assert "<!--" in template.split("## Symptom", 1)[1].split("## Expected", 1)[0], (
        f"{_BUG_TEMPLATE} no longer opens ## Symptom with a guidance comment; "
        "this fixture no longer exercises comment skipping"
    )
    assert _TEMPLATE_PLACEHOLDER in template, (
        f"{_BUG_TEMPLATE} no longer carries the placeholder {_TEMPLATE_PLACEHOLDER!r}"
    )
    return (
        template.replace("# BUG-NNNN-slug", "# BUG-0998-new-template", 1)
        .replace(_TEMPLATE_PLACEHOLDER, _PROSE, 1)
        .replace("(critical / high / medium / low)", "critical", 1)
    )


# ---------------------------------------------------------------------------
# Comments before the prose are skipped
# ---------------------------------------------------------------------------


def test_multiline_comment_before_prose_yields_the_prose(tmp_path: pathlib.Path) -> None:
    """A multi-line comment opening Symptom is skipped; the prose after it is the value."""
    body = (
        "<!-- The opening sentence of this section becomes this bug's bullet.\n"
        "     State the defect plainly in one complete sentence.\n"
        "     Detail and evidence follow the opening sentence. -->\n"
        f"{_PROSE}\n"
        "More detail on a second line."
    )
    bug = BugItem(_write_bug(tmp_path, "BUG-0001-sample.md", _bug_text(body)))

    assert bug.symptom == _PROSE
    assert _offline_oneliner_bug(bug) == _PROSE
    _assert_no_comment_text(bug.symptom)
    _assert_no_comment_text(_offline_oneliner_bug(bug))


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(f"<!-- guidance -->\n{_PROSE}", id="single-line-comment-own-line"),
        pytest.param(f"<!-- guidance --> {_PROSE}", id="comment-then-prose-same-line"),
        pytest.param(f"{_PROSE} <!-- aside -->", id="prose-then-inline-comment"),
        pytest.param(f"<!-- one -->\n\n<!-- two\n   lines -->\n\n{_PROSE}", id="several-comments"),
        pytest.param(f"<!--\n## Heading inside a comment\n-->\n{_PROSE}", id="heading-inside-comment"),
    ],
)
def test_comment_shapes_never_reach_the_symptom(tmp_path: pathlib.Path, body: str) -> None:
    """Every placement of a comment around the opening prose leaves the prose alone."""
    bug = BugItem(_write_bug(tmp_path, "BUG-0001-sample.md", _bug_text(body)))

    assert bug.symptom == _PROSE
    assert _offline_oneliner_bug(bug) == _PROSE


def test_bug_from_shipped_template_reads_its_prose(tmp_path: pathlib.Path) -> None:
    """A bug made from the shipped template, guidance comment kept, reads its prose."""
    bug = BugItem(_write_bug(tmp_path, "BUG-0998-new-template.md", _bug_from_template()))

    assert bug.symptom == _PROSE
    assert _offline_oneliner_bug(bug) == _PROSE


# ---------------------------------------------------------------------------
# A Symptom holding only a comment behaves as an empty Symptom
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "after",
    [
        pytest.param("## Expected\nIt keeps working.\n", id="followed-by-a-section"),
        pytest.param("", id="last-section-in-file"),
    ],
)
@pytest.mark.parametrize(
    "comment_only",
    [
        pytest.param("<!-- guidance only -->", id="single-line"),
        pytest.param("<!-- guidance\n     on several\n     lines -->", id="multi-line"),
        pytest.param("<!-- one -->\n\n<!-- two -->", id="several-comments"),
    ],
)
def test_comment_only_symptom_matches_empty_symptom(
    tmp_path: pathlib.Path, comment_only: str, after: str
) -> None:
    """A Symptom holding only a comment yields what an empty Symptom yields, never comment text."""
    commented = BugItem(_write_bug(tmp_path / "c", "BUG-0001-sample.md", _bug_text(comment_only, after=after)))
    empty = BugItem(_write_bug(tmp_path / "e", "BUG-0001-sample.md", _bug_text("", after=after)))

    assert commented.symptom == empty.symptom
    assert _offline_oneliner_bug(commented) == _offline_oneliner_bug(empty)
    _assert_no_comment_text(commented.symptom)
    _assert_no_comment_text(_offline_oneliner_bug(commented))


def test_comment_only_last_symptom_falls_back_to_title(tmp_path: pathlib.Path) -> None:
    """With nothing but a comment after the Symptom heading, the one-liner is the H1 title."""
    bug = BugItem(_write_bug(tmp_path, "BUG-0001-sample.md", _bug_text("<!-- guidance -->", after="")))

    assert bug.symptom == ""
    assert _offline_oneliner_bug(bug) == "BUG-0001-sample"


def test_unclosed_comment_hides_the_rest_of_the_file(tmp_path: pathlib.Path) -> None:
    """A comment opened and never closed runs to the end of the file, as it renders.

    Nothing after the opener becomes the Symptom value, so the one-liner
    falls back to the H1 title rather than showing comment text.
    """
    body = "<!-- opened and never closed\n     still guidance"
    bug = BugItem(_write_bug(tmp_path, "BUG-0001-sample.md", _bug_text(body)))

    assert bug.symptom == ""
    assert _offline_oneliner_bug(bug) == "BUG-0001-sample"


# ---------------------------------------------------------------------------
# A quoted comment opener is text, and never reaches into a later section
# ---------------------------------------------------------------------------


_QUOTED_OPENER_LINE = "The reader treats a quoted `<!--` as a comment opener."
_LATER_CLOSER_SECTION = "## Expected\nOnly a real `-->` closes a comment.\n"


@pytest.mark.parametrize(
    "symptom_line, after",
    [
        pytest.param(_QUOTED_OPENER_LINE, _LATER_CLOSER_SECTION, id="code-span-opener-later-closer"),
        pytest.param(
            "The reader stops at a quoted `<!--` and loses the rest.",
            "## Expected\nNothing is cut.\n",
            id="code-span-opener-never-closed",
        ),
        pytest.param(
            "A bare <!-- in the middle of prose is text.",
            "## Expected\nA bare --> later on does not close it.\n",
            id="mid-line-opener-later-closer",
        ),
        pytest.param(
            "A bare <!-- in the middle of prose, never closed.",
            "",
            id="mid-line-opener-never-closed-last-section",
        ),
        pytest.param(
            "Quoting the whole pair `<!-- -->` keeps it.",
            _LATER_CLOSER_SECTION,
            id="code-span-holding-a-full-comment",
        ),
    ],
)
def test_quoted_comment_opener_keeps_the_full_symptom_line(
    tmp_path: pathlib.Path, symptom_line: str, after: str
) -> None:
    """A ``<!--`` quoted in prose is text: the Symptom is its full line, with no later-section text spliced in."""
    bug = BugItem(_write_bug(tmp_path, "BUG-0001-sample.md", _bug_text(symptom_line, after=f"\n{after}")))

    assert bug.symptom == symptom_line
    assert _offline_oneliner_bug(bug) == symptom_line


def test_quoted_comment_closer_in_a_later_section_reads_whole() -> None:
    """The section after a quoted opener keeps its own quoted closer and all its text."""
    text = _bug_text(_QUOTED_OPENER_LINE, after=f"\n{_LATER_CLOSER_SECTION}")

    assert _extract_field(text, "Expected") == "Only a real `-->` closes a comment."


def test_real_comment_after_a_quoted_opener_is_still_skipped(tmp_path: pathlib.Path) -> None:
    """A quoted opener in one paragraph does not stop a real comment in the next from being skipped."""
    body = f"<!-- guidance mentions `<!--` too -->\n{_QUOTED_OPENER_LINE} <!-- aside -->"
    bug = BugItem(_write_bug(tmp_path, "BUG-0001-sample.md", _bug_text(body)))

    assert bug.symptom == _QUOTED_OPENER_LINE


# ---------------------------------------------------------------------------
# Comment-free bugs read exactly as before
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body, after, expected",
    [
        pytest.param(_PROSE, "## Expected\nIt keeps working.\n", _PROSE, id="plain-prose"),
        pytest.param(f"\n\n{_PROSE}\nsecond line", "", _PROSE, id="blank-lines-before-prose"),
        pytest.param("Value `a < b` and x > y fails", "", "Value `a < b` and x > y fails", id="angle-brackets"),
        pytest.param("**Bold** opening with *emphasis*", "", "**Bold** opening with *emphasis*", id="inline-markup"),
        pytest.param("", "## Expected\nIt keeps working.\n", "It keeps working.", id="empty-falls-through"),
        pytest.param("", "", "", id="empty-last-section"),
    ],
)
def test_comment_free_symptom_reads_as_before(
    tmp_path: pathlib.Path, body: str, after: str, expected: str
) -> None:
    """Without an HTML comment, the Symptom value and one-liner are the first non-heading line, as written."""
    bug = BugItem(_write_bug(tmp_path, "BUG-0001-sample.md", _bug_text(body, after=after)))

    assert bug.symptom == expected
    assert _offline_oneliner_bug(bug) == (expected or "BUG-0001-sample")


def test_comment_free_long_symptom_is_truncated_as_before(tmp_path: pathlib.Path) -> None:
    """A long comment-free Symptom is cut to 80 characters with an ellipsis, as before."""
    long_line = "word " * 30
    bug = BugItem(_write_bug(tmp_path, "BUG-0001-sample.md", _bug_text(long_line)))

    assert bug.symptom == long_line.strip()
    oneliner = _offline_oneliner_bug(bug)
    assert len(oneliner) <= 80 and oneliner.endswith("...")


@pytest.mark.parametrize(
    "field, expected",
    [("Status", "open"), ("Severity", ""), ("Expected", "It keeps working.")],
)
def test_other_fields_without_comments_read_as_before(field: str, expected: str) -> None:
    """Fields read by the same parser keep their comment-free values."""
    assert _extract_field(_bug_text(_PROSE), field) == expected


# ---------------------------------------------------------------------------
# Through the shipped entry point
# ---------------------------------------------------------------------------


def _diagnostic(result: subprocess.CompletedProcess) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _run_owned(argv: list[str], *, env: dict, cwd: pathlib.Path, timeout: float) -> subprocess.CompletedProcess:
    """Run *argv* in its own process group with an explicit cwd; kill the group on timeout."""
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        cwd=str(cwd),
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()
        raise
    return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)


def _sandbox_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """A throwaway kanban root with one project, laid out like a live install.

    The package directory under test is linked at the root's own
    ``pgai_agent_kanban/``, which is where the wrapper resolves the report
    module first, so the run exercises this tree's code and no other.
    """
    root = tmp_path / "kanban"
    bugs = root / "projects" / "demo" / "bugs"
    bugs.mkdir(parents=True)
    (root / "tmp").mkdir()
    (root / "projects.cfg").write_text("[demo]\npath = projects/demo\n", encoding="utf-8")
    (root / "pgai_agent_kanban").symlink_to(_PACKAGE_DIR, target_is_directory=True)
    _write_bug(bugs, "BUG-0998-new-template.md", _bug_from_template())
    _write_bug(
        bugs,
        "BUG-0997-old-template.md",
        "# BUG-0997-old-template\n\n**Severity:** critical\n\n"
        f"## Status\nopen\n\n## Symptom\n{_PROSE}\n\n## Expected\nIt keeps working.\n",
    )
    return root


def test_summary_script_open_items_show_template_bug_prose(tmp_path: pathlib.Path) -> None:
    """project-summary.sh --all lists a template-made bug by its prose, not its guidance comment."""
    root = _sandbox_root(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    foreign_cwd = tmp_path / "elsewhere"
    foreign_cwd.mkdir()
    env = build_scrubbed_env(
        home_dir=home,
        extra_env={
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )

    result = _run_owned(
        ["bash", str(_SUMMARY_SCRIPT), "--kanban-root", str(root), "--project", "demo", "--all"],
        env=env,
        cwd=foreign_cwd,
        timeout=60,
    )

    assert result.returncode == 0, _diagnostic(result)
    open_lines = [line.strip() for line in result.stdout.splitlines() if line.strip().startswith("* BUG-")]
    assert f"* BUG-0998-new-template: [critical] {_PROSE}" in open_lines, _diagnostic(result)
    assert f"* BUG-0997-old-template: [critical] {_PROSE}" in open_lines, _diagnostic(result)
    assert "<!--" not in result.stdout, _diagnostic(result)
