"""
test_inbox_cli.py
=================
Tests for ``team/scripts/inbox.sh``, the operator verb over the operator
inbox (``pgai_agent_kanban.overwatch.inbox``):

  - An entry written through the module is shown by ``list`` under
    ``## Open``, answered with ``answer``, closed with ``close``, and then
    appears only under ``## Done``.
  - ``--project`` is required -- ``PGAI_PROJECT_NAME`` never stands in for
    it -- and resolves like every project-scoped verb (exact, then an
    unambiguous prefix of a registered name).
  - Every malformed call is refused dual-channel (error / message / a
    remedy with no placeholder) with the inbox file byte-identical, and the
    wrapper's own remedy runs as printed.
  - The script is executable, and ``--help`` names the three commands.

Every test runs the real script as a subprocess against its own sandbox
kanban root under pytest's ``tmp_path``, with the environment rebuilt rather
than inherited: both root names point at the sandbox, the framework temp
dir is the test's own, and the pre-resolved install paths are absent.
Nothing here reads or writes the live install.
"""

from __future__ import annotations

import os
import pathlib
import re
import shlex
import stat
import subprocess
import sys

import pytest

from pgai_agent_kanban.lib.error_contract import parse_error_contract
from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.overwatch import inbox
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent  # team/tests/unit/ -> team/
_SCRIPT = _TEAM_DIR / "scripts" / "inbox.sh"
_PROJECT = "demo-proj"


# ---------------------------------------------------------------------------
# Sandbox and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def root(tmp_path: pathlib.Path) -> pathlib.Path:
    """A sandbox kanban root with one registered project directory."""
    sandbox = tmp_path / "root"
    (sandbox / "projects" / _PROJECT).mkdir(parents=True)
    (sandbox / "projects.cfg").write_text(f"[project:{_PROJECT}]\npriority = 1\nenabled = true\n", encoding="utf-8")
    return sandbox


def _ctx(root: pathlib.Path) -> OpsContext:
    return OpsContext(kanban_root=root)


def _write(root: pathlib.Path, question: str = "Cancel the RC or keep it open?") -> inbox.InboxEntry:
    """Write one open question through the module, the inbox's only writer."""
    return inbox.write_entry(
        _ctx(root),
        _PROJECT,
        source="rc:v1.2.3",
        question=question,
        meanwhile="Left the RC open; the queue kept running.",
        how_to_answer='Answer "cancel" or "keep".',
    )


def _inbox_file(root: pathlib.Path) -> pathlib.Path:
    return inbox.inbox_path(_ctx(root), _PROJECT)


def _run(
    root: pathlib.Path, tmp_path: pathlib.Path, *args: str, extra_env: dict | None = None
) -> subprocess.CompletedProcess:
    """Run the real inbox.sh against the sandbox *root*, from a working
    directory outside the repository, under a rebuilt environment."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    temp_dir = tmp_path / "framework-tmp"
    temp_dir.mkdir(exist_ok=True)
    cwd = tmp_path / "cwd"
    cwd.mkdir(exist_ok=True)
    env = build_scrubbed_env(
        home_dir=home,
        extra_path_dirs=[pathlib.Path(sys.executable).parent],
        extra_env={
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(temp_dir),
            **(extra_env or {}),
        },
    )
    return subprocess.run(
        ["bash", str(_SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd),
        timeout=60,
        stdin=subprocess.DEVNULL,
    )


def _diagnostic(result: subprocess.CompletedProcess) -> str:
    return f"exit {result.returncode}\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _sections(list_output: str) -> tuple[str, str]:
    """Split ``inbox.sh list`` output into its Open and Done section bodies."""
    assert list_output.startswith("## Open"), list_output
    open_part, _, done_part = list_output.partition("## Done")
    assert done_part, f"no '## Done' section in:\n{list_output}"
    return open_part, done_part


def _contract(stderr: str) -> dict:
    fields = parse_error_contract(stderr)
    assert fields is not None, f"no error/message/remedy block in:\n{stderr}"
    assert "<" not in fields["remedy"], f"remedy carries a placeholder: {fields['remedy']!r}"
    return fields


def _snapshot(path: pathlib.Path) -> bytes | None:
    return path.read_bytes() if path.exists() else None


# ---------------------------------------------------------------------------
# The script itself
# ---------------------------------------------------------------------------


def test_script_is_executable_by_owner_group_and_other():
    mode = stat.S_IMODE(_SCRIPT.stat().st_mode)
    assert mode & 0o755 == 0o755, f"{_SCRIPT} is mode {oct(mode)}; operator scripts are 0755"


def test_help_names_the_three_commands_and_exits_zero(root, tmp_path):
    result = _run(root, tmp_path, "--help")
    assert result.returncode == 0, _diagnostic(result)
    for text in ("list", "answer --id ID --answer TEXT", "close --id ID", "--project NAME"):
        assert text in result.stdout, f"--help does not mention {text!r}:\n{result.stdout}"
    assert _run(root, tmp_path, "-h").stdout == result.stdout


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------


def test_written_entry_is_listed_answered_closed_and_then_only_in_done(root, tmp_path):
    entry = _write(root)

    listed = _run(root, tmp_path, "--project", _PROJECT, "list")
    assert listed.returncode == 0, _diagnostic(listed)
    open_part, done_part = _sections(listed.stdout)
    assert f"### {entry.entry_id}" in open_part
    assert entry.question in open_part
    assert entry.entry_id not in done_part

    answered = _run(root, tmp_path, "--project", _PROJECT, "answer", "--id", entry.entry_id, "--answer", "keep")
    assert answered.returncode == 0, _diagnostic(answered)
    assert "- **Answer:** keep" in answered.stdout
    open_part, done_part = _sections(_run(root, tmp_path, "--project", _PROJECT, "list").stdout)
    assert "- **Answer:** keep" in open_part, "an answered entry stays open until it is closed"
    assert entry.entry_id not in done_part

    closed = _run(root, tmp_path, "--project", _PROJECT, "close", "--id", entry.entry_id)
    assert closed.returncode == 0, _diagnostic(closed)
    assert "- **Closed at:**" in closed.stdout

    open_part, done_part = _sections(_run(root, tmp_path, "--project", _PROJECT, "list").stdout)
    assert entry.entry_id not in open_part
    assert f"### {entry.entry_id}" in done_part
    assert "- **Answer:** keep" in done_part
    state = inbox.read_inbox(_ctx(root), _PROJECT)
    assert [e.entry_id for e in state.open] == []
    assert [(e.entry_id, e.answer) for e in state.done] == [(entry.entry_id, "keep")]


def test_entry_ids_are_read_liberally_and_answers_are_kept_verbatim(root, tmp_path):
    entry = _write(root)
    result = _run(root, tmp_path, "--project", _PROJECT, "answer", "--id", "inbox_1", "--answer", "-1, wait")
    assert result.returncode == 0, _diagnostic(result)
    assert inbox.list_open(_ctx(root), _PROJECT)[0].answer == "-1, wait"
    assert inbox.list_open(_ctx(root), _PROJECT)[0].entry_id == entry.entry_id


def test_project_prefix_resolves_to_the_registered_project(root, tmp_path):
    entry = _write(root)
    result = _run(root, tmp_path, "--project", "demo", "list")
    assert result.returncode == 0, _diagnostic(result)
    assert f"### {entry.entry_id}" in _sections(result.stdout)[0]


def test_list_with_no_inbox_file_shows_two_empty_sections_and_creates_nothing(root, tmp_path):
    result = _run(root, tmp_path, "--project", _PROJECT, "list")
    assert result.returncode == 0, _diagnostic(result)
    open_part, done_part = _sections(result.stdout)
    assert "(none)" in open_part and "(none)" in done_part
    assert not _inbox_file(root).exists()


# ---------------------------------------------------------------------------
# Refusals: dual-channel, nothing written
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("args", "error", "exit_code"),
    [
        (("list",), "inbox.project_required", 2),
        (("--project", _PROJECT), "inbox.command_required", 2),
        (("--project", _PROJECT, "frob"), "inbox.unknown_command", 2),
        (("--project", _PROJECT, "list", "extra"), "inbox.extra_argument", 2),
        (("--project", _PROJECT, "list", "--bogus"), "inbox.unknown_flag", 2),
        (("--project", _PROJECT, "list", "--id", "1"), "inbox.flag_not_used", 2),
        (("--project", _PROJECT, "close", "--id", "1", "--answer", "keep"), "inbox.flag_not_used", 2),
        (("--project", _PROJECT, "answer", "--id", "1"), "inbox.empty_answer", 2),
        (("--project", _PROJECT, "answer", "--id", "1", "--answer", "   "), "inbox.empty_answer", 2),
        (("--project", _PROJECT, "answer", "--answer", "keep"), "inbox.bad_entry_id", 2),
        (("--project", _PROJECT, "answer", "--id", "zz", "--answer", "keep"), "inbox.bad_entry_id", 2),
        (("--project", _PROJECT, "answer", "--id", "9", "--answer", "keep"), "inbox.unknown_entry", 3),
        (("--project", _PROJECT, "close", "--id", "9"), "inbox.unknown_entry", 3),
        (("--project", "no-such-project", "list"), "project_resolve.not_found", 1),
    ],
)
def test_bad_arguments_are_refused_dual_channel_with_nothing_written(root, tmp_path, args, error, exit_code):
    _write(root)
    before = _snapshot(_inbox_file(root))

    result = _run(root, tmp_path, *args)

    assert result.returncode == exit_code, _diagnostic(result)
    assert _contract(result.stderr)["error"] == error, _diagnostic(result)
    assert result.stdout == "", _diagnostic(result)
    assert _snapshot(_inbox_file(root)) == before, "a refused call changed the inbox file"


def test_project_name_variable_never_stands_in_for_project(root, tmp_path):
    _write(root)
    before = _snapshot(_inbox_file(root))
    result = _run(
        root,
        tmp_path,
        "answer",
        "--id",
        "1",
        "--answer",
        "keep",
        extra_env={"PGAI_PROJECT_NAME": _PROJECT},
    )
    assert result.returncode == 2, _diagnostic(result)
    assert _contract(result.stderr)["error"] == "inbox.project_required"
    assert _snapshot(_inbox_file(root)) == before


def test_closed_entry_is_refused_on_answer_and_close(root, tmp_path):
    entry = _write(root)
    assert _run(root, tmp_path, "--project", _PROJECT, "close", "--id", entry.entry_id).returncode == 0
    before = _snapshot(_inbox_file(root))
    for args in (("answer", "--id", entry.entry_id, "--answer", "keep"), ("close", "--id", entry.entry_id)):
        result = _run(root, tmp_path, "--project", _PROJECT, *args)
        assert result.returncode == 2, _diagnostic(result)
        assert _contract(result.stderr)["error"] == "inbox.entry_closed"
    assert _snapshot(_inbox_file(root)) == before


def test_wrapper_refusal_remedy_runs_as_printed(root, tmp_path):
    refused = _run(root, tmp_path, "--project", _PROJECT)
    remedy = _contract(refused.stderr)["remedy"]
    argv = shlex.split(remedy)
    assert pathlib.Path(argv[0]) == _SCRIPT
    rerun = _run(root, tmp_path, *argv[1:])
    assert rerun.returncode == 0, _diagnostic(rerun)
    assert rerun.stdout.startswith("## Open")


# ---------------------------------------------------------------------------
# A word after the command: the remedy corrects the refused call
# ---------------------------------------------------------------------------


def _write_two(root: pathlib.Path) -> tuple[inbox.InboxEntry, inbox.InboxEntry]:
    first = _write(root, "Cancel the RC or keep it open?")
    second = _write(root, "Retry the build or skip it?")
    assert (first.entry_id, second.entry_id) == ("INBOX-0001", "INBOX-0002")
    return first, second


def _entry_block(root: pathlib.Path, entry_id: str) -> str:
    """Return *entry_id*'s heading and field lines exactly as the inbox file holds them."""
    text = _inbox_file(root).read_text(encoding="utf-8")
    match = re.search(rf"^### {re.escape(entry_id)}\n(?:[^#\n].*\n)*", text, re.MULTILINE)
    assert match is not None, f"no {entry_id} entry in:\n{text}"
    return match.group(0)


def _refused_extra_argument_remedy(root: pathlib.Path, tmp_path: pathlib.Path, *args: str) -> list[str]:
    """Run a call carrying an extra word; assert it is refused with nothing
    written and return the printed remedy's arguments after the script path."""
    before = _snapshot(_inbox_file(root))
    refused = _run(root, tmp_path, *args)
    assert refused.returncode == 2, _diagnostic(refused)
    fields = _contract(refused.stderr)
    assert fields["error"] == "inbox.extra_argument", _diagnostic(refused)
    assert _snapshot(_inbox_file(root)) == before, "a refused call changed the inbox file"
    argv = shlex.split(fields["remedy"])
    assert pathlib.Path(argv[0]) == _SCRIPT, fields["remedy"]
    return argv[1:]


def test_extra_entry_id_after_close_gets_a_remedy_that_closes_that_entry(root, tmp_path):
    _write_two(root)
    untouched = _entry_block(root, "INBOX-0001")

    remedy = _refused_extra_argument_remedy(root, tmp_path, "--project", "demo", "close", "INBOX-0002")

    assert "close --id INBOX-0002" in shlex.join(remedy)
    rerun = _run(root, tmp_path, *remedy)
    assert rerun.returncode == 0, _diagnostic(rerun)
    state = inbox.read_inbox(_ctx(root), _PROJECT)
    assert [e.entry_id for e in state.open] == ["INBOX-0001"]
    assert [e.entry_id for e in state.done] == ["INBOX-0002"]
    assert _entry_block(root, "INBOX-0001") == untouched, "the remedy changed an entry the call did not name"


@pytest.mark.parametrize(
    ("args", "expected_remedy"),
    [
        (("list", "INBOX-0002"), ("--project", "demo", "list")),
        (
            ("answer", "INBOX-0002", "--answer", "keep"),
            ("--project", "demo", "answer", "--id", "INBOX-0002", "--answer", "keep"),
        ),
        (
            ("answer", "--answer", "-1, wait", "inbox_2"),
            ("--project", "demo", "answer", "--id", "inbox_2", "--answer", "-1, wait"),
        ),
    ],
    ids=["list", "answer-with-answer", "answer-text-needs-quoting"],
)
def test_extra_word_remedy_is_the_refused_call_corrected(root, tmp_path, args, expected_remedy):
    _write_two(root)
    untouched = _entry_block(root, "INBOX-0001")
    before = _snapshot(_inbox_file(root))

    remedy = _refused_extra_argument_remedy(root, tmp_path, "--project", "demo", *args)

    assert tuple(remedy) == expected_remedy
    rerun = _run(root, tmp_path, *remedy)
    assert rerun.returncode == 0, _diagnostic(rerun)
    if "answer" in remedy:
        answers = {e.entry_id: e.answer for e in inbox.list_open(_ctx(root), _PROJECT)}
        assert answers["INBOX-0002"] == expected_remedy[-1]
        assert _entry_block(root, "INBOX-0001") == untouched, "the remedy changed an entry the call did not name"
    else:
        assert rerun.stdout.startswith("## Open")
        assert _snapshot(_inbox_file(root)) == before


@pytest.mark.parametrize(
    ("args", "expected_remedy", "rerun_exit"),
    [
        (("--project", "demo", "answer", "INBOX-0002"), ("--project", "demo", "list"), 0),
        (("--project", "demo", "answer", "INBOX-0002", "--answer", "   "), ("--project", "demo", "list"), 0),
        (("--project", "demo", "answer", "INBOX-0002", "keep"), ("--project", "demo", "list"), 0),
        (("--project", "demo", "frob", "INBOX-0002"), ("--help",), 0),
        (("--project", "demo", "close", "INBOX-0001", "INBOX-0002"), ("--project", "demo", "list"), 0),
        (("--project", "demo", "close", "INBOX-0002", "--id", "1"), ("--project", "demo", "list"), 0),
        (("--project", "demo", "close", "INBOX-0002", "--answer", "keep"), ("--project", "demo", "list"), 0),
        # No --project: the example project is not registered in the sandbox,
        # so the read-only list is refused by the resolver -- still no write.
        (("close", "INBOX-0002"), ("--project", "pgai-agent-kanban", "list"), 1),
    ],
    ids=[
        "answer-without-answer",
        "answer-with-blank-answer",
        "answer-text-as-a-word",
        "unknown-command",
        "two-extra-words",
        "close-with-a-second-id",
        "close-with-an-answer",
        "no-project",
    ],
)
def test_extra_word_remedy_that_cannot_be_inferred_writes_nothing(root, tmp_path, args, expected_remedy, rerun_exit):
    _write_two(root)
    before = _snapshot(_inbox_file(root))

    remedy = _refused_extra_argument_remedy(root, tmp_path, *args)

    assert tuple(remedy) == expected_remedy
    rerun = _run(root, tmp_path, *remedy)
    assert rerun.returncode == rerun_exit, _diagnostic(rerun)
    assert _snapshot(_inbox_file(root)) == before, "a read-only remedy changed the inbox file"


def test_script_reaches_the_module_by_the_function_each_command_runs():
    """The census row's impl names answer_entry; the script must call the
    module by that name (and list/close by theirs) through pp_run_ops."""
    code = "\n".join(
        line for line in _SCRIPT.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#")
    )
    for function in ("read_inbox", "answer_entry", "close_entry"):
        assert f"pp_run_ops pgai_agent_kanban.overwatch.inbox {function}" in code
    assert os.access(_SCRIPT, os.X_OK)
