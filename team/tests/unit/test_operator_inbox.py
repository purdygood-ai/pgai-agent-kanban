"""
test_operator_inbox.py
======================
Contract tests for ``pgai_agent_kanban.overwatch.inbox``, the one reader and
writer of ``projects/<name>/operator-inbox.md``.

Interface layer first: every operation is called directly against a
sandbox kanban root with two projects (``project-a`` / ``project-b``) and
asserted on its typed return or typed error and on the bytes left on disk.
A small adapter layer runs the module's own command line by file path in a
scrubbed environment and checks only what the adapter adds: exit codes,
the dual-channel block on stderr, JSON output, and that a printed remedy
runs as printed.
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import re
import subprocess
import sys

import pytest

from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.ops.errors import IoError, NotFound, Refused, cli_exit_code
from pgai_agent_kanban.ops.locks import acquire_lock
from pgai_agent_kanban.overwatch import inbox
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_MODULE_FILE = pathlib.Path(inbox.__file__).resolve()

# The five fields the Inbox definition requires on every entry, as labelled
# on disk.
_REQUIRED_LABELS = ("Date", "Source", "Question", "Meanwhile", "How to answer")

_T0 = datetime.datetime(2026, 1, 1, 10, 0, 0, tzinfo=datetime.UTC)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def root(tmp_path: pathlib.Path) -> pathlib.Path:
    """A throwaway kanban root with two project directories and nothing else."""
    kanban_root = tmp_path / "kanban"
    for name in ("project-a", "project-b"):
        (kanban_root / "projects" / name.replace("_", "-")).mkdir(parents=True)
    return kanban_root


@pytest.fixture
def ctx(root: pathlib.Path) -> OpsContext:
    return OpsContext(kanban_root=root)


PROJECT = "project-a"
OTHER = "project-b"


def _write(ctx: OpsContext, question: str = "Cancel the RC or keep it open?", *, minutes: int = 0, **kw):
    """Write one entry with sensible defaults for the fields a test does not care about."""
    values = {
        "source": "rc:v1.2.3",
        "question": question,
        "meanwhile": "Left the RC open; the rest of the queue kept running.",
        "how_to_answer": "Answer 'cancel' or 'keep'.",
    }
    values.update(kw)
    return inbox.write_entry(ctx, PROJECT, now=_T0 + datetime.timedelta(minutes=minutes), **values)


def _inbox_file(ctx: OpsContext, project: str = PROJECT) -> pathlib.Path:
    return ctx.project_root(project) / "operator-inbox.md"


def _snapshot(path: pathlib.Path):
    """The file's bytes, or None when it does not exist."""
    return path.read_bytes() if path.exists() else None


def _section_text(text: str, heading: str) -> str:
    """The raw text of one ``## <heading>`` section, located independently of the module."""
    parts = re.split(r"(?m)^## ", text)
    for part in parts[1:]:
        if part.split("\n", 1)[0].strip().lower() == heading.lower():
            return part
    raise AssertionError(f"no '## {heading}' section in:\n{text}")


def _entry_blocks(section: str) -> dict[str, str]:
    """Map each ``### INBOX-NNNN`` heading in *section* to its block of text."""
    blocks = re.split(r"(?m)^### ", section)[1:]
    return {block.split("\n", 1)[0].strip(): block for block in blocks}


def _assert_contract(exc: inbox.InboxError, error: str) -> None:
    """The refusal carries all three dual-channel fields and a placeholder-free remedy."""
    assert exc.error == error
    assert exc.message.strip(), "refusal has an empty message"
    assert exc.remedy.strip(), "refusal has an empty remedy"
    assert not re.search(r"<[A-Za-z_ -]+>", exc.remedy), f"remedy carries a placeholder: {exc.remedy}"
    block = str(exc)
    assert block.startswith(f"error:   {error}\n"), block
    assert "\nmessage: " in block and "\nremedy:  " in block, block


# ---------------------------------------------------------------------------
# Round trip and fields
# ---------------------------------------------------------------------------


def test_write_list_answer_close_round_trip_moves_entry_to_done(ctx):
    first = _write(ctx, "First question?")
    second = _write(ctx, "Second question?", minutes=1)
    assert (first.entry_id, second.entry_id) == ("INBOX-0001", "INBOX-0002")

    listed = inbox.list_open(ctx, PROJECT)
    assert [e.entry_id for e in listed] == ["INBOX-0001", "INBOX-0002"]
    assert inbox.inbox_summary(ctx, PROJECT).open_count == 2

    answered = inbox.answer_entry(ctx, PROJECT, "INBOX-0001", "keep it open", now=_T0)
    assert answered.answer == "keep it open"
    assert answered.status == "open"
    assert [e.entry_id for e in inbox.list_open(ctx, PROJECT)] == ["INBOX-0001", "INBOX-0002"]

    closed = inbox.close_entry(ctx, PROJECT, "INBOX-0001", now=_T0)
    assert closed.status == "done"
    assert closed.answer == "keep it open"

    after = inbox.read_inbox(ctx, PROJECT)
    assert [e.entry_id for e in after.open] == ["INBOX-0002"]
    assert [e.entry_id for e in after.done] == ["INBOX-0001"]
    assert inbox.inbox_summary(ctx, PROJECT).open_count == 1

    text = _inbox_file(ctx).read_text(encoding="utf-8")
    assert "INBOX-0001" not in _section_text(text, "Open")
    done_blocks = _entry_blocks(_section_text(text, "Done"))
    assert list(done_blocks) == ["INBOX-0001"]
    assert "- **Answer:** keep it open" in done_blocks["INBOX-0001"]
    assert "- **Closed at:** 2026-01-01T10:00:00+00:00" in done_blocks["INBOX-0001"]


def test_every_written_entry_carries_the_five_inbox_fields(ctx):
    _write(ctx, "One?", source="CODER-20260101-001-example")
    _write(ctx, "Two?\nwith a second line", source="rc:v9.9.9", minutes=5)
    _write(ctx, "Three?", meanwhile="none", how_to_answer="reply in the inbox", minutes=9)

    text = _inbox_file(ctx).read_text(encoding="utf-8")
    blocks = _entry_blocks(_section_text(text, "Open"))
    assert list(blocks) == ["INBOX-0001", "INBOX-0002", "INBOX-0003"]
    for entry_id, block in blocks.items():
        for label in _REQUIRED_LABELS:
            line = next((ln for ln in block.splitlines() if ln.startswith(f"- **{label}:** ")), None)
            assert line is not None, f"{entry_id} has no '{label}' field:\n{block}"
            assert line.split(":**", 1)[1].strip(), f"{entry_id} has an empty '{label}' field"

    for entry in inbox.list_open(ctx, PROJECT):
        for field in ("date", "source", "question", "meanwhile", "how_to_answer"):
            assert getattr(entry, field), f"{entry.entry_id}.{field} read back empty"


def test_entry_date_is_iso_utc_and_values_round_trip(ctx):
    written = _write(ctx, "  Multi\r\n\r\nline\tquestion?  ", minutes=3)
    assert written.date == "2026-01-01T10:03:00+00:00"
    assert written.question == "Multi\nline\tquestion?"
    (read,) = inbox.list_open(ctx, PROJECT)
    assert read == written


def test_summary_reports_open_count_and_newest_open_question(ctx):
    assert inbox.inbox_summary(ctx, PROJECT) == inbox.InboxSummary(open_count=0, newest_open=None)
    _write(ctx, "Older?", minutes=0)
    _write(ctx, "Newest?", minutes=30)
    _write(ctx, "Middle?", minutes=10)
    summary = inbox.inbox_summary(ctx, PROJECT)
    assert summary.open_count == 3
    assert summary.newest_open is not None and summary.newest_open.question == "Newest?"

    inbox.close_entry(ctx, PROJECT, "INBOX-0002")
    summary = inbox.inbox_summary(ctx, PROJECT)
    assert summary.open_count == 2
    assert summary.newest_open.question == "Middle?"
    assert summary.to_dict()["newest_open"]["entry_id"] == "INBOX-0003"


def test_ids_keep_counting_past_closed_entries(ctx):
    _write(ctx, "a?")
    _write(ctx, "b?")
    inbox.close_entry(ctx, PROJECT, "INBOX-0002")
    assert _write(ctx, "c?").entry_id == "INBOX-0003"


def test_entry_ids_are_read_liberally(ctx):
    _write(ctx, "a?")
    assert inbox.answer_entry(ctx, PROJECT, " inbox_1 ", "yes").entry_id == "INBOX-0001"
    assert inbox.close_entry(ctx, PROJECT, "1").entry_id == "INBOX-0001"
    assert inbox.normalize_entry_id("Inbox-0042") == "INBOX-0042"
    assert inbox.normalize_entry_id("INBOX-0") is None
    assert inbox.normalize_entry_id("BUG-0001") is None


def test_close_without_answer_is_allowed(ctx):
    _write(ctx, "Moot?")
    closed = inbox.close_entry(ctx, PROJECT, "INBOX-0001")
    assert closed.answer == "" and closed.status == "done"


# ---------------------------------------------------------------------------
# Missing file, atomicity, isolation
# ---------------------------------------------------------------------------


def test_missing_inbox_reads_empty_and_is_created_on_first_write(ctx):
    path = _inbox_file(ctx)
    assert not path.exists()
    assert inbox.read_inbox(ctx, PROJECT) == inbox.Inbox()
    assert not path.exists(), "a read must not create the inbox"

    _write(ctx)
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# Operator inbox\n")
    assert "## Open" in text and "## Done" in text
    assert text.endswith("\n") and not any(line != line.rstrip() for line in text.splitlines())


def _fail_inbox_replace(monkeypatch, target: pathlib.Path) -> None:
    """Make the final rename onto *target* fail, as a crash between write and rename would."""
    real_replace = os.replace

    def replace(src, dst, *args, **kwargs):
        if os.fspath(dst) == os.fspath(target):
            raise OSError(28, "No space left on device (simulated)")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", replace)


def _scratch_files(directory: pathlib.Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir() if p.name.startswith(".operator_inbox_tmp_"))


def test_failed_write_leaves_previous_file_intact(ctx, monkeypatch):
    _write(ctx, "Kept?")
    path = _inbox_file(ctx)
    before = path.read_bytes()

    _fail_inbox_replace(monkeypatch, path)
    with pytest.raises(inbox.InboxIoError) as caught:
        _write(ctx, "Lost?")
    _assert_contract(caught.value, "inbox.write_failed")
    assert isinstance(caught.value, IoError)

    assert path.read_bytes() == before, "a failed write changed the inbox"
    assert _scratch_files(path.parent) == [], "a failed write left a scratch file"
    monkeypatch.undo()
    assert [e.question for e in inbox.list_open(ctx, PROJECT)] == ["Kept?"]


def test_failed_first_write_leaves_no_file(ctx, monkeypatch):
    path = _inbox_file(ctx)
    _fail_inbox_replace(monkeypatch, path)
    with pytest.raises(inbox.InboxIoError):
        _write(ctx)
    assert not path.exists()
    assert _scratch_files(path.parent) == []


def test_projects_are_isolated(ctx):
    _write(ctx, "Only in project-a?")
    assert inbox.read_inbox(ctx, OTHER) == inbox.Inbox()
    assert not _inbox_file(ctx, OTHER).exists()
    other = inbox.write_entry(
        ctx, OTHER, source="rc:v2.0.0", question="Only in project-b?", meanwhile="m", how_to_answer="h"
    )
    assert other.entry_id == "INBOX-0001"
    assert [e.question for e in inbox.list_open(ctx, PROJECT)] == ["Only in project-a?"]
    with pytest.raises(inbox.InboxNotFound):
        inbox.close_entry(ctx, OTHER, "INBOX-0002")


def test_write_once_reuses_the_open_entry_with_the_same_source_and_question(ctx):
    first = _write(ctx, "Make the registry readable?", source="wake-batch", once=True)
    before = _inbox_file(ctx).read_bytes()

    again = _write(
        ctx,
        "Make the  registry\nreadable?",
        source=" wake-batch ",
        minutes=5,
        meanwhile="different meanwhile text",
        once=True,
    )

    assert again == first
    assert _inbox_file(ctx).read_bytes() == before
    other = _write(ctx, "A different question?", source="wake-batch", once=True)
    assert other.entry_id == "INBOX-0002"
    inbox.close_entry(ctx, PROJECT, first.entry_id)
    reopened = _write(ctx, "Make the registry readable?", source="wake-batch", once=True)
    assert reopened.entry_id == "INBOX-0003"
    assert _write(ctx, "Make the registry readable?", source="wake-batch").entry_id == "INBOX-0004"


def test_cli_write_once_prints_the_existing_entry_and_writes_nothing(root, ctx, tmp_path):
    args = (
        "write",
        "--project",
        PROJECT,
        "--source",
        "wake-batch",
        "--question",
        "Readable?",
        "--meanwhile",
        "kept going",
        "--how-to-answer",
        "chmod u+r projects.cfg",
        "--once",
        "--json",
    )
    first = _run_cli(root, tmp_path, *args)
    assert first.returncode == 0, first.stderr
    before = _inbox_file(ctx).read_bytes()
    second = _run_cli(root, tmp_path, *args)
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout)["entry_id"] == json.loads(first.stdout)["entry_id"] == "INBOX-0001"
    assert _inbox_file(ctx).read_bytes() == before


# ---------------------------------------------------------------------------
# Refusals: dual-channel, nothing written
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [("question", ""), ("question", "   \n\t "), ("source", ""), ("meanwhile", " "), ("how_to_answer", "")],
)
def test_empty_field_is_refused_with_nothing_written(ctx, field, value):
    path = _inbox_file(ctx)
    with pytest.raises(inbox.InboxRefused) as caught:
        _write(ctx, **{field: value})
    _assert_contract(caught.value, f"inbox.empty_{field}")
    assert isinstance(caught.value, Refused) and cli_exit_code(caught.value) == 2
    assert not path.exists(), "a refused first write created the inbox"

    _write(ctx, "Existing?")
    before = path.read_bytes()
    with pytest.raises(inbox.InboxRefused):
        _write(ctx, **{field: value})
    assert path.read_bytes() == before


@pytest.mark.parametrize("operation", ["answer", "close"])
def test_unknown_entry_is_refused_with_nothing_written(ctx, operation):
    _write(ctx)
    path = _inbox_file(ctx)
    before = path.read_bytes()
    with pytest.raises(inbox.InboxNotFound) as caught:
        if operation == "answer":
            inbox.answer_entry(ctx, PROJECT, "INBOX-0099", "yes")
        else:
            inbox.close_entry(ctx, PROJECT, "INBOX-0099")
    _assert_contract(caught.value, "inbox.unknown_entry")
    assert isinstance(caught.value, NotFound) and cli_exit_code(caught.value) == 3
    assert "INBOX-0099" in caught.value.message
    assert path.read_bytes() == before


def test_unknown_entry_on_missing_inbox_creates_nothing(ctx):
    with pytest.raises(inbox.InboxNotFound):
        inbox.close_entry(ctx, PROJECT, "INBOX-0001")
    assert not _inbox_file(ctx).exists()


@pytest.mark.parametrize("operation", ["answer", "close"])
def test_closed_entry_is_refused_with_nothing_written(ctx, operation):
    _write(ctx)
    inbox.close_entry(ctx, PROJECT, "INBOX-0001")
    path = _inbox_file(ctx)
    before = path.read_bytes()
    with pytest.raises(inbox.InboxRefused) as caught:
        if operation == "answer":
            inbox.answer_entry(ctx, PROJECT, "INBOX-0001", "late")
        else:
            inbox.close_entry(ctx, PROJECT, "INBOX-0001")
    _assert_contract(caught.value, "inbox.entry_closed")
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    ("call", "error"),
    [
        (lambda c: inbox.answer_entry(c, PROJECT, "INBOX-0001", "  "), "inbox.empty_answer"),
        (lambda c: inbox.answer_entry(c, PROJECT, "first one", "yes"), "inbox.bad_entry_id"),
        (lambda c: inbox.close_entry(c, PROJECT, ""), "inbox.bad_entry_id"),
        (lambda c: inbox.list_open(c, "../project-a"), "inbox.bad_project"),
        (
            lambda c: inbox.write_entry(c, "Project A", source="s", question="q", meanwhile="m", how_to_answer="h"),
            "inbox.bad_project",
        ),
    ],
)
def test_malformed_input_is_refused_with_nothing_written(ctx, call, error):
    _write(ctx)
    path = _inbox_file(ctx)
    before = path.read_bytes()
    with pytest.raises(inbox.InboxRefused) as caught:
        call(ctx)
    _assert_contract(caught.value, error)
    assert path.read_bytes() == before


def test_unknown_project_is_refused_and_no_directory_is_created(ctx, root):
    with pytest.raises(inbox.InboxNotFound) as caught:
        inbox.write_entry(ctx, "no-such-project", source="s", question="q", meanwhile="m", how_to_answer="h")
    _assert_contract(caught.value, "inbox.unknown_project")
    assert not (root / "projects" / "no-such-project").exists()


def test_busy_lock_is_refused_after_the_wait_with_nothing_written(ctx, monkeypatch):
    _write(ctx)
    path = _inbox_file(ctx)
    before = path.read_bytes()
    monkeypatch.setattr(inbox, "LOCK_WAIT_SECONDS", 0.2)
    with acquire_lock(
        inbox.inbox_lock_path(ctx, PROJECT), holder="test-holder", pid=os.getpid(), op="hold", task="none"
    ):
        with pytest.raises(inbox.InboxRefused) as caught:
            _write(ctx, "Blocked?")
    _assert_contract(caught.value, "inbox.lock_busy")
    assert "test-holder" in caught.value.message
    assert path.read_bytes() == before
    assert _write(ctx, "After release?").entry_id == "INBOX-0002"


# ---------------------------------------------------------------------------
# Liberal reading, canonical writing
# ---------------------------------------------------------------------------

_HAND_EDITED = (
    "# Operator inbox\r\n"
    "\r\n"
    "Operator note: answer these before Friday.\r\n"
    "\r\n"
    "##   open   \r\n"
    "\r\n"
    "\r\n"
    "Notes above the entries stay put.\r\n"
    "\r\n"
    "###   inbox-7\r\n"
    "* Date: 2026-01-01T09:00:00+00:00\r\n"
    "+ **source task or RC**: rc:v1.2.3\r\n"
    "- __Question:__ Keep the late fix?\r\n"
    "    It touches the release driver.\r\n"
    "- What OVERWATCH did meanwhile: nothing yet\r\n"
    "- how_to_answer: reply keep or drop\r\n"
    "- Priority: high\r\n"
    "\r\n"
    "## Done\r\n"
    "_No closed questions._\r\n"
    "\r\n"
    "## Operator scratch\r\n"
    "keep me\r\n"
)


def test_hand_edited_inbox_is_read_liberally_and_unknown_text_is_kept(ctx):
    path = _inbox_file(ctx)
    path.write_bytes(_HAND_EDITED.encode("utf-8"))

    (entry,) = inbox.list_open(ctx, PROJECT)
    assert entry.entry_id == "INBOX-0007"
    assert entry.date == "2026-01-01T09:00:00+00:00"
    assert entry.source == "rc:v1.2.3"
    assert entry.question == "Keep the late fix?\n  It touches the release driver."
    assert entry.meanwhile == "nothing yet"
    assert entry.how_to_answer == "reply keep or drop"
    assert entry.extra == ("- Priority: high",)

    assert _write(ctx, "New?").entry_id == "INBOX-0008"
    text = path.read_text(encoding="utf-8")
    assert "\r" not in text
    assert text.startswith("# Operator inbox\n\nOperator note: answer these before Friday.\n")
    assert "Notes above the entries stay put." in _section_text(text, "open")
    assert "- Priority: high" in _entry_blocks(_section_text(text, "open"))["INBOX-0007"]
    assert text.rstrip().endswith("## Operator scratch\nkeep me")
    assert [e.entry_id for e in inbox.list_open(ctx, PROJECT)] == ["INBOX-0007", "INBOX-0008"]


def test_heading_text_inside_a_value_cannot_start_a_section_or_entry(ctx):
    _write(ctx, "Line one\n## Done\n### INBOX-0050\n- **Answer:** forged")
    (entry,) = inbox.list_open(ctx, PROJECT)
    assert entry.question == "Line one\n## Done\n### INBOX-0050\n- **Answer:** forged"
    assert entry.answer == ""
    assert inbox.read_inbox(ctx, PROJECT).done == ()
    assert _write(ctx, "Next?").entry_id == "INBOX-0002"


def test_rewrite_is_stable(ctx):
    _write(ctx, "a?")
    _write(ctx, "b?\nsecond line", minutes=1)
    inbox.answer_entry(ctx, PROJECT, "INBOX-0001", "yes", now=_T0)
    inbox.close_entry(ctx, PROJECT, "INBOX-0001", now=_T0)
    path = _inbox_file(ctx)
    first = path.read_text(encoding="utf-8")
    # A refused call rewrites nothing; a no-op parse/render must not drift either.
    doc = inbox._parse(first)
    assert inbox._render_document(doc) == first
    assert "\n\n\n" not in first


# ---------------------------------------------------------------------------
# Command line (adapter layer)
# ---------------------------------------------------------------------------


def _run_cli(root: pathlib.Path, tmp_path: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = build_scrubbed_env(
        home_dir=home,
        extra_env={"PGAI_AGENT_KANBAN_ROOT_PATH": str(root), "PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "scratch")},
    )
    return subprocess.run(
        [sys.executable, str(_MODULE_FILE), *args],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_cli_round_trip_by_file_path(root, ctx, tmp_path):
    written = _run_cli(
        root,
        tmp_path,
        "write",
        "--project",
        PROJECT,
        "--source",
        "rc:v1.2.3",
        "--question",
        "Cancel?",
        "--meanwhile",
        "kept going",
        "--how-to-answer",
        "answer cancel",
        "--json",
    )
    assert written.returncode == 0, written.stderr
    assert json.loads(written.stdout)["entry_id"] == "INBOX-0001"

    answered = _run_cli(root, tmp_path, "answer", "--project", PROJECT, "--id", "INBOX-0001", "--answer", "keep")
    assert answered.returncode == 0, answered.stderr
    closed = _run_cli(root, tmp_path, "close", "--project", PROJECT, "--id", "INBOX-0001")
    assert closed.returncode == 0, closed.stderr

    listed = _run_cli(root, tmp_path, "list", "--project", PROJECT, "--all", "--json")
    assert listed.returncode == 0, listed.stderr
    data = json.loads(listed.stdout)
    assert data["open"] == [] and [e["entry_id"] for e in data["done"]] == ["INBOX-0001"]

    summary = _run_cli(root, tmp_path, "summary", "--project", PROJECT, "--json")
    assert summary.returncode == 0, summary.stderr
    assert json.loads(summary.stdout) == {"open_count": 0, "newest_open": None}
    assert [e.entry_id for e in inbox.read_inbox(ctx, PROJECT).done] == ["INBOX-0001"]


def test_cli_refusal_prints_dual_channel_and_remedy_runs(root, ctx, tmp_path):
    _write(ctx)
    before = _inbox_file(ctx).read_bytes()
    refused = _run_cli(root, tmp_path, "close", "--project", PROJECT, "--id", "INBOX-0042")
    assert refused.returncode == 3, refused.stderr
    match = re.search(r"^error:\s+(\S+)\nmessage:\s+.+\nremedy:\s+(.+)$", refused.stderr, re.M)
    assert match, f"no dual-channel block on stderr:\n{refused.stderr}"
    assert match.group(1) == "inbox.unknown_entry"
    assert _inbox_file(ctx).read_bytes() == before

    home = tmp_path / "home"
    remedy = subprocess.run(
        ["bash", "-c", match.group(2)],
        cwd=str(tmp_path),
        env=build_scrubbed_env(
            home_dir=home,
            extra_path_dirs=[pathlib.Path(sys.executable).parent],
            extra_env={"PGAI_AGENT_KANBAN_ROOT_PATH": str(root)},
        ),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert remedy.returncode == 0, f"remedy {match.group(2)!r} failed:\n{remedy.stderr}"
    assert "INBOX-0001" in remedy.stdout


def test_cli_empty_question_exits_2_and_writes_nothing(root, ctx, tmp_path):
    refused = _run_cli(
        root, tmp_path, "write", "--project", PROJECT, "--source", "s", "--meanwhile", "m", "--how-to-answer", "h"
    )
    assert refused.returncode == 2, refused.stderr
    assert "error:   inbox.empty_question" in refused.stderr
    assert not _inbox_file(ctx).exists()
