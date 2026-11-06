"""
test_read_md_field.py
=====================
read_md_field (team/scripts/lib/md_field.sh) is the one bash reader of a
``## Heading`` section. It runs the section reader's own CLI
(``pgai_agent_kanban.task_status read --raw`` / ``--value``) and states its
contract in its usage comment:

  * body mode prints the section body; ``--value`` prints the first value
    line of each heading named, one line per heading;
  * exit 0 for a present heading (an empty one prints an empty line),
    exit 3 for an absent heading, exit 1 for a missing or unreadable file
    or a usage error;
  * CRLF files read the same as LF files;
  * the reading flags select the heading match and the end of a section;
  * a byte that is not valid UTF-8 reads as U+FFFD instead of making the
    file unreadable, while the paths that write a file back (``render``,
    the status writer) still refuse it and leave its bytes as they were;
  * loading the library leaves a ``pp_run_ops`` the caller defined in
    place, and the reader runs from the library's own tree whatever
    ``pp_run_ops`` is defined around it.

Every test sources the shipped library under ``set -euo pipefail`` and
runs the function as its callers do. Fixtures live under pytest's
tmp_path; no live kanban root is read or written.
"""

from __future__ import annotations

import pathlib
import shlex

import pytest

from tests.unit.shell_harness import run_bash

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_MD_FIELD = _TEAM_DIR / "scripts" / "lib" / "md_field.sh"
_DISCOVERY_STATE = _TEAM_DIR / "scripts" / "lib" / "discovery_state.sh"
_ATOMIC_WRITE = _TEAM_DIR / "scripts" / "lib" / "atomic_write.sh"
_TASK_STATUS_SH = _TEAM_DIR / "scripts" / "task-status.sh"

_DOC = (
    "# Task\n\n"
    "## Owner\nClaude\n\n"
    "## Notes\n\n"
    "## Goal\nFirst line.\nSecond line.\n\n"
    "## Commented\n<!-- One of: a, b -->\n# not a value\n\nb\n"
)


def _read(
    tmp_path: pathlib.Path,
    *args: str,
    text: str | None = _DOC,
    name: str = "doc.md",
    data: bytes | None = None,
):
    """Run ``read_md_field ARGS`` with ``doc.md`` (holding *text*) as the file.

    The literal argument ``FILE`` is replaced by the document's path; a
    *text* of None leaves the file missing. *data*, when given, is written
    as the file's bytes in place of *text*. Returns (exit status, stdout,
    stderr).
    """
    doc = tmp_path / name
    if data is not None:
        doc.write_bytes(data)
    elif text is not None:
        doc.write_bytes(text.encode("utf-8"))
    argv = " ".join(shlex.quote(str(doc) if a == "FILE" else a) for a in args)
    result = run_bash(
        tmp_path,
        f"set -euo pipefail\nsource {shlex.quote(str(_MD_FIELD))}\n"
        f"rc=0\nread_md_field {argv} || rc=$?\nprintf '<rc=%s>' \"$rc\"\n",
        timeout=60,
    )
    assert result.returncode == 0, f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    out, _, rc = result.stdout.rpartition("<rc=")
    return int(rc.rstrip(">")), out, result.stderr


# --- present, empty, absent, missing file -------------------------------------


def test_present_heading_prints_its_body(tmp_path: pathlib.Path) -> None:
    assert _read(tmp_path, "FILE", "Goal")[:2] == (0, "First line.\nSecond line.\n")


def test_present_heading_value_mode_prints_its_first_value_line(tmp_path: pathlib.Path) -> None:
    assert _read(tmp_path, "--value", "FILE", "Goal")[:2] == (0, "First line.\n")
    assert _read(tmp_path, "--value", "FILE", "Commented")[:2] == (0, "b\n")


@pytest.mark.parametrize("mode", [(), ("--value",)], ids=["body", "value"])
def test_empty_heading_prints_an_empty_line_and_exits_0(tmp_path: pathlib.Path, mode: tuple) -> None:
    """An empty section reads empty, never as the section after it."""
    assert _read(tmp_path, *mode, "FILE", "Notes")[:2] == (0, "\n")


def test_absent_heading_prints_nothing_and_exits_3(tmp_path: pathlib.Path) -> None:
    assert _read(tmp_path, "FILE", "Missing")[:2] == (3, "")


def test_absent_heading_value_mode_prints_every_line_then_exits_3(tmp_path: pathlib.Path) -> None:
    assert _read(tmp_path, "--value", "FILE", "Owner", "Missing", "Goal")[:2] == (3, "Claude\n\nFirst line.\n")


@pytest.mark.parametrize("mode", [(), ("--value",)], ids=["body", "value"])
def test_missing_file_prints_one_stderr_line_and_exits_1(tmp_path: pathlib.Path, mode: tuple) -> None:
    rc, out, err = _read(tmp_path, *mode, "FILE", "Owner", text=None)
    assert (rc, out) == (1, "")
    assert "doc.md" in err and len(err.strip().splitlines()) == 1, err


# --- CRLF ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (("FILE", "Goal"), (0, "First line.\nSecond line.\n")),
        (("--value", "FILE", "Owner"), (0, "Claude\n")),
        (("FILE", "Notes"), (0, "\n")),
        (("FILE", "Missing"), (3, "")),
    ],
    ids=["body", "value", "empty", "absent"],
)
def test_crlf_file_reads_like_lf(tmp_path: pathlib.Path, args: tuple, expected: tuple) -> None:
    """Carriage returns never reach the output or change which section is read."""
    assert _read(tmp_path, *args, text=_DOC.replace("\n", "\r\n"))[:2] == expected


# --- readings ----------------------------------------------------------------------


def test_default_reading_takes_an_inline_value_and_keeps_subheadings(tmp_path: pathlib.Path) -> None:
    text = "## State: DONE\n\n## Body\nx\n### Sub\ny\n## Next\n"
    assert _read(tmp_path, "--value", "FILE", "State", text=text)[:2] == (0, "DONE\n")
    assert _read(tmp_path, "FILE", "Body", text=text)[:2] == (0, "x\n### Sub\ny\n")


def test_exact_heading_reading_needs_the_exact_heading_line(tmp_path: pathlib.Path) -> None:
    text = "## active rc\nv9.9.9\n\n## Active RC\nv1.2.3\n"
    assert _read(tmp_path, "--value", "--exact-heading", "FILE", "Active RC", text=text)[:2] == (0, "v1.2.3\n")


def test_comments_reading_ignores_a_heading_inside_an_html_comment(tmp_path: pathlib.Path) -> None:
    text = "<!--\n## Active RC\nv9.9.9\n-->\n## Active RC\nv1.2.3\n"
    args = ("--value", "--exact-heading", "--comments", "FILE", "Active RC")
    assert _read(tmp_path, *args, text=text)[:2] == (0, "v1.2.3\n")


@pytest.mark.parametrize(
    "heading_line",
    ["## Artifact Name", "##Artifact Name", "## ArtifactName", "##  artifact   NAME  "],
)
def test_free_spacing_reading_matches_any_blanks_and_case(tmp_path: pathlib.Path, heading_line: str) -> None:
    text = f"{heading_line}\nguide\n"
    assert _read(tmp_path, "--free-spacing", "FILE", "Artifact Name", text=text)[:2] == (0, "guide\n")


@pytest.mark.parametrize(
    "heading_line",
    ["## Artifact Name: guide", "### Artifact Name", "## Artifact Names", " ## Artifact Name"],
)
def test_free_spacing_reading_never_widens_to_other_headings(tmp_path: pathlib.Path, heading_line: str) -> None:
    text = f"{heading_line}\nguide\n"
    assert _read(tmp_path, "--free-spacing", "FILE", "Artifact Name", text=text)[:2] == (3, "")


def test_end_at_subheading_and_rule_readings_end_the_body(tmp_path: pathlib.Path) -> None:
    text = "## Goal\nx\n### Sub\ny\n\n## Other\n- a\n---\nprose\n"
    assert _read(tmp_path, "--end-at-subheading", "FILE", "Goal", text=text)[:2] == (0, "x\n")
    assert _read(tmp_path, "FILE", "Other", text=text)[:2] == (0, "- a\n---\nprose\n")
    assert _read(tmp_path, "--end-at-rule", "FILE", "Other", text=text)[:2] == (0, "- a\n")


# --- usage errors ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("args", "needle"),
    [
        (("--bogus", "FILE", "Owner"), "unknown option '--bogus'"),
        (("FILE",), "needs <file> and at least one <heading>"),
        (("FILE", "Owner", "Goal"), "pass --value to read several"),
        (("--exact-heading", "--free-spacing", "FILE", "Owner"), "cannot be combined"),
    ],
    ids=["unknown-option", "no-heading", "several-headings-in-body-mode", "two-heading-readings"],
)
def test_usage_error_names_the_problem_and_exits_1(tmp_path: pathlib.Path, args: tuple, needle: str) -> None:
    rc, out, err = _read(tmp_path, *args)
    assert (rc, out) == (1, "")
    assert needle in err, err


def test_double_dash_ends_the_options(tmp_path: pathlib.Path) -> None:
    """A file whose name starts with a dash is read, not taken for an option."""
    rc, out, _err = _read(tmp_path, "--value", "--", "FILE", "Owner", name="--doc.md")
    assert (rc, out) == (0, "Claude\n")


# --- a byte that is not valid UTF-8 ------------------------------------------------

# A bundle whose ## Status is running, with a Latin-1 byte (0xe9) in a later
# section: the shape a hand-edited requirement or bug file can take.
_LATIN1_BUNDLE = b"# Bundle\n\n## Status\nrunning\n\n## Notes\ncaf\xe9\n"


def test_an_invalid_utf8_byte_in_a_later_section_does_not_stop_the_value_read(tmp_path: pathlib.Path) -> None:
    assert _read(tmp_path, "--value", "FILE", "Status", data=_LATIN1_BUNDLE)[:2] == (0, "running\n")


def test_an_invalid_utf8_byte_reads_as_the_replacement_character(tmp_path: pathlib.Path) -> None:
    assert _read(tmp_path, "FILE", "Notes", data=_LATIN1_BUNDLE)[:2] == (0, "caf\ufffd\n")


def test_discovery_bundle_field_read_tolerates_an_invalid_utf8_byte(tmp_path: pathlib.Path) -> None:
    """Discovery's bundle reader sees running, never its open fallback."""
    bundle = tmp_path / "bundle.md"
    bundle.write_bytes(_LATIN1_BUNDLE)
    result = run_bash(
        tmp_path,
        "set -euo pipefail\n"
        f"source {shlex.quote(str(_DISCOVERY_STATE))}\n"
        "rc=0\n"
        f'value="$(_disc_read_bundle_field {shlex.quote(str(bundle))} Status)" || rc=$?\n'
        'printf \'%s<rc=%s>\' "$value" "$rc"\n',
        timeout=60,
    )
    assert result.returncode == 0, f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    assert result.stdout == "running<rc=0>", result.stderr


def _status_writer_script(root: pathlib.Path, key: str) -> str:
    """Bash running the status-writer CLI against throwaway kanban root *root*."""
    return (
        "unset PGAI_TASKS_DIR PGAI_PROJECT_ROOT\n"
        f"export KANBAN_ROOT={shlex.quote(str(root))} "
        f"PGAI_AGENT_KANBAN_ROOT_PATH={shlex.quote(str(root))} "
        f"PGAI_AGENT_KANBAN_TEMP_DIR={shlex.quote(str(root / 'tmp'))}\n"
        f"bash {shlex.quote(str(_TASK_STATUS_SH))} --project demo --key {key} "
        "--state DONE --summary x\n"
    )


def _wake_render_script(status: pathlib.Path) -> str:
    """Bash committing a render of *status* through the wake's atomic writer."""
    target = shlex.quote(str(status))
    return (
        f"source {shlex.quote(str(_MD_FIELD))}\n"
        f"source {shlex.quote(str(_ATOMIC_WRITE))}\n"
        f"pgai_atomic_write {target} pp_run_ops pgai_agent_kanban.task_status render {target} "
        "--section=State --body=DONE\n"
    )


@pytest.mark.parametrize("writer", ["render", "status-writer"])
def test_a_write_over_an_invalid_utf8_file_still_refuses_and_leaves_its_bytes(
    tmp_path: pathlib.Path, writer: str
) -> None:
    """Only reading decodes with replacement: no write puts U+FFFD in a file."""
    root = tmp_path / "root"
    (root / "tmp").mkdir(parents=True)
    (root / "projects.cfg").write_text("[project:demo]\npriority = 1\n", encoding="utf-8")
    key = "CODER-20260101-001-latin1-status"
    folder = root / "projects" / "demo" / "tasks" / key
    folder.mkdir(parents=True)
    status = folder / "status.md"
    original = b"# Status\n\n## Task\n" + key.encode() + b"\n\n## State\nWORKING\n\n## Summary\ncaf\xe9\n"
    status.write_bytes(original)

    assert _read(tmp_path, "--value", str(status), "State", text=None)[:2] == (0, "WORKING\n")

    script = _wake_render_script(status) if writer == "render" else _status_writer_script(root, key)
    result = run_bash(tmp_path, f"rc=0\n{{\n{script}}} || rc=$?\nprintf '<rc=%s>' \"$rc\"\n", timeout=60)
    assert result.returncode == 0, f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    assert not result.stdout.endswith("<rc=0>"), f"the {writer} must refuse\n{result.stderr}"
    assert str(status) in result.stderr, f"the refusal must name the file\n{result.stderr}"
    assert status.read_bytes() == original, f"the {writer} must leave the file's bytes unchanged"
    assert not [p.name for p in folder.iterdir() if p.name != "status.md"], "no scratch file may be left beside it"


# --- the caller's pp_run_ops -------------------------------------------------------

_CALLER_RUNNER = "pp_run_ops() { echo CALLER_RUNNER; }"


@pytest.mark.parametrize("order", ["defined-before-loading", "defined-after-loading"])
def test_a_caller_pp_run_ops_stays_defined_and_never_answers_for_the_reader(tmp_path: pathlib.Path, order: str) -> None:
    doc = tmp_path / "doc.md"
    doc.write_text(_DOC, encoding="utf-8")
    load = f"source {shlex.quote(str(_MD_FIELD))}\n"
    define = f'{_CALLER_RUNNER}\nbefore="$(declare -f pp_run_ops)"\n'
    setup = define + load if order == "defined-before-loading" else load + define
    result = run_bash(
        tmp_path,
        "set -euo pipefail\n" + setup + 'after="$(declare -f pp_run_ops)"\n'
        '[[ "$before" == "$after" ]] && echo same-definition || echo replaced-definition\n'
        f"read_md_field --value {shlex.quote(str(doc))} Owner\n"
        "pp_run_ops anything\n",
        timeout=60,
    )
    assert result.returncode == 0, f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    assert result.stdout == "same-definition\nClaude\nCALLER_RUNNER\n", result.stderr
