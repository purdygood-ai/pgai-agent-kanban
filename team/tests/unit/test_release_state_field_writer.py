"""
test_release_state_field_writer.py
==================================
Behavioural tests for the release-state.md field writer
(``write_rc_state.py release-state`` / ``update_release_state_fields``) and
for the three CM entry points that route through it.

Contract under test: opening, releasing and cancelling an RC change only the
four schema fields — Active RC, RC Opened At, RC Opened By Task, Last
Released — and keep every block appended after them (``## HALT Event``,
``## Operator Override``, ...) byte-for-byte. After every step the file
still passes the release-state loader.

Appending an Auto-Ship Event (``write_rc_state.py auto-ship-event`` /
``append_auto_ship_event``) adds one ``## Auto-Ship Event`` block after
everything already in the file, keeps every existing byte, never overwrites
an earlier event, and refuses a multi-line or empty value, a non-numeric
trigger or a missing file without writing anything.

Design:
  - Writer-level tests import the module and work on files under pytest's
    ``tmp_path``.
  - The lifecycle test drives the shipped entry points (``cm/open-rc.sh``,
    ``cm/release.sh``, ``cm/cancel-rc.sh``) as real subprocesses, from a
    working directory outside the repository, under an environment rebuilt
    with ``build_scrubbed_env``. Every path lives under ``tmp_path``: a
    throwaway kanban root and a local git repository with no remote.
    ``push_to_remote = false`` keeps the scripts off origin entirely.
  - Each subprocess runs in its own process group, killed whole on timeout.
"""

from __future__ import annotations

import os
import pathlib
import re
import signal
import stat
import subprocess
import sys

import pytest
from tests.fixtures.binary_guard import require_binary
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

from pgai_agent_kanban.cm.read_state_field import load_release_state
from pgai_agent_kanban.cm.write_rc_state import (
    append_auto_ship_event,
    render_release_state,
    update_release_state_fields,
)

_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent  # team/
_CM_SCRIPTS = _TEAM_DIR / "scripts" / "cm"
_OPEN_RC = _CM_SCRIPTS / "open-rc.sh"
_RELEASE = _CM_SCRIPTS / "release.sh"
_CANCEL_RC = _CM_SCRIPTS / "cancel-rc.sh"
_WRITER = _TEAM_DIR / "pgai_agent_kanban" / "cm" / "write_rc_state.py"
_CM_PY_SRC = _TEAM_DIR / "pgai_agent_kanban" / "cm"

_PROJECT = "release-state-writer-fixture"
_TIMEOUT_SECONDS = 120

_FIELDS_IDLE = (
    "# Release State\n"
    "\n"
    "## Active RC\n"
    "none\n"
    "\n"
    "## RC Opened At\n"
    "none\n"
    "\n"
    "## RC Opened By Task\n"
    "none\n"
    "\n"
    "## Last Released\n"
    "v0.0.9\n"
)

# Two appended event blocks shaped as the CM helpers append them. The
# trailing spaces, the heading-shaped reason line and the multi-line body are
# deliberate: each is something a whole-file rewrite or a careless line
# editor would alter.
_EVENT_BLOCKS = (
    "\n"
    "## HALT Event\n"
    "Timestamp: 2026-01-02T03:04:05Z\n"
    "CM Task: cm-release.sh (automated)\n"
    "error: cm-release.fixture-halt\n"
    "message: fixture halt kept for the audit trail   \n"
    "remedy: rm projects/fixture/HALT\n"
    "\n"
    "## Operator Override\n"
    "Timestamp: 2026-01-02T04:05:06Z\n"
    "Version: v0.0.9\n"
    "Stop Overridden: cm-release.fixture-stop\n"
    "Reason: shipped by hand; quoted the field below verbatim\n"
    "## Last Released\n"
    "v9.9.9\n"
)


# ---------------------------------------------------------------------------
# Writer-level behaviour
# ---------------------------------------------------------------------------


def test_only_named_field_lines_change_and_events_are_kept(tmp_path: pathlib.Path) -> None:
    """Setting all four fields edits exactly their value lines; the event tail is untouched."""
    path = tmp_path / "release-state.md"
    path.write_text(_FIELDS_IDLE + _EVENT_BLOCKS, encoding="utf-8")

    update_release_state_fields(str(path), {
        "Active RC": "v0.1.0",
        "RC Opened At": "2026-02-01T00:00:00+00:00",
        "RC Opened By Task": "CM-fixture-open",
        "Last Released": "v0.0.9",
    })

    text = path.read_text(encoding="utf-8")
    assert text.endswith(_EVENT_BLOCKS), text
    assert text == _FIELDS_IDLE.replace(
        "## Active RC\nnone\n", "## Active RC\nv0.1.0\n"
    ).replace(
        "## RC Opened At\nnone\n", "## RC Opened At\n2026-02-01T00:00:00+00:00\n"
    ).replace(
        "## RC Opened By Task\nnone\n", "## RC Opened By Task\nCM-fixture-open\n"
    ) + _EVENT_BLOCKS
    assert load_release_state(str(path)) == {
        "Active RC": "v0.1.0",
        "RC Opened At": "2026-02-01T00:00:00+00:00",
        "RC Opened By Task": "CM-fixture-open",
        "Last Released": "v0.0.9",
    }


def test_missing_field_is_inserted_above_appended_blocks(tmp_path: pathlib.Path) -> None:
    """An absent schema heading is added beside its siblings, not inside or after an event.

    The override block quotes a '## Last Released' line; it must neither be
    edited nor be mistaken for the real field.
    """
    fields_without_last_released = _FIELDS_IDLE.split("\n## Last Released\n")[0] + "\n"
    path = tmp_path / "release-state.md"
    path.write_text(fields_without_last_released + _EVENT_BLOCKS, encoding="utf-8")

    update_release_state_fields(str(path), {"Last Released": "v0.1.0"})

    text = path.read_text(encoding="utf-8")
    assert text == fields_without_last_released + "\n## Last Released\nv0.1.0\n" + _EVENT_BLOCKS, text
    assert load_release_state(str(path))["Last Released"] == "v0.1.0"


def test_unnamed_fields_are_left_exactly_as_found(tmp_path: pathlib.Path) -> None:
    """Fields not passed to the writer keep their bytes, odd spacing included."""
    original = _FIELDS_IDLE.replace("## Last Released\nv0.0.9\n", "## Last Released\n\nv0.0.9  \n")
    rendered = render_release_state(original, {"Active RC": "v0.2.0"})
    assert rendered == original.replace("## Active RC\nnone\n", "## Active RC\nv0.2.0\n")


def test_missing_file_is_created_in_canonical_shape(tmp_path: pathlib.Path) -> None:
    """A file that does not exist yet is created with the title and all named fields."""
    path = tmp_path / "release-state.md"
    update_release_state_fields(str(path), {
        "Active RC": "none",
        "RC Opened At": "none",
        "RC Opened By Task": "none",
        "Last Released": "none",
    })
    assert path.read_text(encoding="utf-8") == _FIELDS_IDLE.replace("v0.0.9", "none")
    assert stat.S_IMODE(path.stat().st_mode) & 0o600 == 0o600


def test_write_replaces_the_file_and_keeps_its_mode(tmp_path: pathlib.Path) -> None:
    """The write lands by rename (new inode), keeps permission bits, leaves no temp file."""
    state_dir = tmp_path / "project"
    state_dir.mkdir()
    path = state_dir / "release-state.md"
    path.write_text(_FIELDS_IDLE + _EVENT_BLOCKS, encoding="utf-8")
    path.chmod(0o640)
    inode_before = path.stat().st_ino

    update_release_state_fields(str(path), {"Active RC": "v0.1.0"})

    assert path.stat().st_ino != inode_before
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert sorted(p.name for p in state_dir.iterdir()) == ["release-state.md"]


def test_cli_refuses_an_empty_value_and_leaves_the_file_untouched(tmp_path: pathlib.Path) -> None:
    """An empty value would leave a file the loader refuses, so nothing is written."""
    path = tmp_path / "release-state.md"
    path.write_text(_FIELDS_IDLE + _EVENT_BLOCKS, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(_WRITER), "release-state", str(path), "--active-rc", ""],
        capture_output=True, text=True, timeout=30, cwd=str(tmp_path),
    )
    assert result.returncode == 2, _diagnostic(result.returncode, result.stdout, result.stderr)
    assert "use 'none' for an unset field" in result.stderr
    assert path.read_text(encoding="utf-8") == _FIELDS_IDLE + _EVENT_BLOCKS


# ---------------------------------------------------------------------------
# Auto-Ship Event append (``append_auto_ship_event`` / ``auto-ship-event``)
# ---------------------------------------------------------------------------

# The four schema fields followed by one Operator Override, shaped as the
# release script appends it.
_FIELDS_WITH_OVERRIDE = (
    _FIELDS_IDLE
    + "\n"
    + "## Operator Override\n"
    + "Timestamp: 2026-01-02T04:05:06Z\n"
    + "Version: v0.0.9\n"
    + "Stop Overridden: cm-release.trigger-2.systemic-risk-high\n"
    + "Reason: shipped by hand   \n"
)

_TRIGGER_2_EVENT = dict(
    timestamp="2026-02-03T04:05:06Z",
    version="v0.1.0",
    trigger=2,
    error_key="cm-release.trigger-2.systemic-risk-high",
    ship_posture="always",
    tester_task="TESTER-fixture-verify",
)
_TRIGGER_2_BLOCK = (
    "## Auto-Ship Event\n"
    "Timestamp: 2026-02-03T04:05:06Z\n"
    "Version: v0.1.0\n"
    "Trigger: 2\n"
    "Error Key: cm-release.trigger-2.systemic-risk-high\n"
    "Ship Posture: always\n"
    "TESTER Task: TESTER-fixture-verify\n"
)
_TRIGGER_8_EVENT = dict(
    timestamp="2026-03-04T05:06:07Z",
    version="v0.2.0",
    trigger="8",
    error_key="cm-release.trigger-8.last-3-rcs-non-functional",
    ship_posture="always",
    tester_task="TESTER-fixture-verify-2",
    filed_bug="BUG-fixture-repeated-non-functional.md",
)
_TRIGGER_8_BLOCK = (
    "## Auto-Ship Event\n"
    "Timestamp: 2026-03-04T05:06:07Z\n"
    "Version: v0.2.0\n"
    "Trigger: 8\n"
    "Error Key: cm-release.trigger-8.last-3-rcs-non-functional\n"
    "Ship Posture: always\n"
    "TESTER Task: TESTER-fixture-verify-2\n"
    "Filed Bug: BUG-fixture-repeated-non-functional.md\n"
)
_LOADED_IDLE = {
    "Active RC": "none",
    "RC Opened At": "none",
    "RC Opened By Task": "none",
    "Last Released": "v0.0.9",
}


def test_auto_ship_event_appends_after_override_and_keeps_every_byte(tmp_path: pathlib.Path) -> None:
    """The event lands after the fields and the override, one blank line apart, and nothing before it moves."""
    path = tmp_path / "release-state.md"
    path.write_text(_FIELDS_WITH_OVERRIDE, encoding="utf-8")

    block = append_auto_ship_event(str(path), **_TRIGGER_2_EVENT)

    assert block == _TRIGGER_2_BLOCK
    assert path.read_text(encoding="utf-8") == _FIELDS_WITH_OVERRIDE + "\n" + _TRIGGER_2_BLOCK
    assert load_release_state(str(path)) == _LOADED_IDLE


def test_second_auto_ship_event_adds_a_second_block(tmp_path: pathlib.Path) -> None:
    """A second append keeps the first event intact and adds its own block after it."""
    path = tmp_path / "release-state.md"
    path.write_text(_FIELDS_WITH_OVERRIDE, encoding="utf-8")

    append_auto_ship_event(str(path), **_TRIGGER_2_EVENT)
    append_auto_ship_event(str(path), **_TRIGGER_8_EVENT)

    text = path.read_text(encoding="utf-8")
    assert text == _FIELDS_WITH_OVERRIDE + "\n" + _TRIGGER_2_BLOCK + "\n" + _TRIGGER_8_BLOCK
    assert text.count("## Auto-Ship Event\n") == 2
    assert load_release_state(str(path)) == _LOADED_IDLE


@pytest.mark.parametrize(
    "existing, separator",
    [
        pytest.param(_FIELDS_IDLE, "\n", id="ends_in_newline"),
        pytest.param(_FIELDS_IDLE + "\n", "", id="already_ends_in_blank_line"),
        pytest.param(_FIELDS_IDLE.rstrip("\n"), "\n\n", id="last_line_unterminated"),
    ],
)
def test_auto_ship_event_separator_adds_bytes_and_never_changes_them(
    tmp_path: pathlib.Path, existing: str, separator: str
) -> None:
    """Whatever the file ends in, its bytes are an exact prefix and one blank line precedes the event."""
    path = tmp_path / "release-state.md"
    path.write_text(existing, encoding="utf-8")
    append_auto_ship_event(str(path), **_TRIGGER_2_EVENT)
    assert path.read_text(encoding="utf-8") == existing + separator + _TRIGGER_2_BLOCK


def test_field_writer_after_an_event_keeps_it_and_inserts_fields_above(tmp_path: pathlib.Path) -> None:
    """A later RC open or release edits only the fields; an absent field goes above the event, never inside it."""
    without_last_released = _FIELDS_IDLE.split("\n## Last Released\n")[0] + "\n"
    path = tmp_path / "release-state.md"
    path.write_text(without_last_released, encoding="utf-8")
    append_auto_ship_event(str(path), **_TRIGGER_2_EVENT)

    update_release_state_fields(str(path), {"Active RC": "v0.2.0", "Last Released": "v0.1.0"})

    assert path.read_text(encoding="utf-8") == (
        without_last_released.replace("## Active RC\nnone\n", "## Active RC\nv0.2.0\n")
        + "## Last Released\nv0.1.0\n\n"
        + _TRIGGER_2_BLOCK
    )


def test_auto_ship_event_default_timestamp_is_utc_seconds(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "release-state.md"
    path.write_text(_FIELDS_IDLE, encoding="utf-8")
    event = {k: v for k, v in _TRIGGER_2_EVENT.items() if k != "timestamp"}
    block = append_auto_ship_event(str(path), **event)
    assert re.search(r"^Timestamp: \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", block, re.M), block


def test_auto_ship_event_keeps_mode_and_leaves_no_temp_file(tmp_path: pathlib.Path) -> None:
    state_dir = tmp_path / "project"
    state_dir.mkdir()
    path = state_dir / "release-state.md"
    path.write_text(_FIELDS_IDLE, encoding="utf-8")
    path.chmod(0o640)
    append_auto_ship_event(str(path), **_TRIGGER_2_EVENT)
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert sorted(p.name for p in state_dir.iterdir()) == ["release-state.md"]


@pytest.mark.parametrize(
    "override, message_part",
    [
        pytest.param({"version": "v0.1.0\n## Last Released\nv9.9.9"}, "'Version' needs a single-line",
                     id="multi_line_value_could_plant_a_heading"),
        pytest.param({"error_key": "   "}, "'Error Key' needs a single-line", id="blank_value"),
        pytest.param({"tester_task": None}, "'TESTER Task' needs a single-line", id="missing_value"),
        pytest.param({"filed_bug": ""}, "'Filed Bug' needs a single-line", id="empty_filed_bug"),
        pytest.param({"trigger": "two"}, "positive whole number", id="trigger_not_a_number"),
        pytest.param({"trigger": 0}, "positive whole number", id="trigger_zero"),
        pytest.param({"trigger": "-3"}, "positive whole number", id="trigger_negative"),
    ],
)
def test_auto_ship_event_refuses_bad_values_and_writes_nothing(
    tmp_path: pathlib.Path, override: dict, message_part: str
) -> None:
    path = tmp_path / "release-state.md"
    path.write_text(_FIELDS_WITH_OVERRIDE, encoding="utf-8")
    with pytest.raises(ValueError, match=re.escape(message_part)):
        append_auto_ship_event(str(path), **{**_TRIGGER_2_EVENT, **override})
    assert path.read_text(encoding="utf-8") == _FIELDS_WITH_OVERRIDE


def test_auto_ship_event_refuses_a_missing_file_and_creates_nothing(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "release-state.md"
    with pytest.raises(FileNotFoundError):
        append_auto_ship_event(str(path), **_TRIGGER_2_EVENT)
    assert not path.exists()


def _event_cli(tmp_path: pathlib.Path, path: pathlib.Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_WRITER), "auto-ship-event", str(path), *extra],
        capture_output=True, text=True, timeout=30, cwd=str(tmp_path),
    )


_TRIGGER_8_ARGV = (
    "--version", "v0.2.0", "--trigger", "8",
    "--error-key", "cm-release.trigger-8.last-3-rcs-non-functional",
    "--ship-posture", "always", "--tester-task", "TESTER-fixture-verify-2",
    "--filed-bug", "BUG-fixture-repeated-non-functional.md",
    "--timestamp", "2026-03-04T05:06:07Z",
)


def test_cli_auto_ship_event_appends_the_same_block_as_the_function(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "release-state.md"
    path.write_text(_FIELDS_WITH_OVERRIDE, encoding="utf-8")
    result = _event_cli(tmp_path, path, *_TRIGGER_8_ARGV)
    assert result.returncode == 0, _diagnostic(result.returncode, result.stdout, result.stderr)
    assert path.read_text(encoding="utf-8") == _FIELDS_WITH_OVERRIDE + "\n" + _TRIGGER_8_BLOCK


@pytest.mark.parametrize(
    "replace, stderr_part",
    [
        pytest.param(("--trigger", "eight"), "positive whole number", id="bad_trigger"),
        pytest.param(("--ship-posture", ""), "'Ship Posture' needs a single-line", id="empty_posture"),
    ],
)
def test_cli_auto_ship_event_refuses_bad_values_and_leaves_the_file_untouched(
    tmp_path: pathlib.Path, replace: tuple, stderr_part: str
) -> None:
    path = tmp_path / "release-state.md"
    path.write_text(_FIELDS_WITH_OVERRIDE, encoding="utf-8")
    argv = list(_TRIGGER_8_ARGV)
    argv[argv.index(replace[0]) + 1] = replace[1]
    result = _event_cli(tmp_path, path, *argv)
    assert result.returncode == 2, _diagnostic(result.returncode, result.stdout, result.stderr)
    assert stderr_part in result.stderr
    assert path.read_text(encoding="utf-8") == _FIELDS_WITH_OVERRIDE


def test_cli_auto_ship_event_refuses_a_missing_file(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "release-state.md"
    result = _event_cli(tmp_path, path, *_TRIGGER_8_ARGV)
    assert result.returncode == 2, _diagnostic(result.returncode, result.stdout, result.stderr)
    assert f"release-state.md not found: {path}" in result.stderr
    assert not path.exists()


# ---------------------------------------------------------------------------
# Lifecycle through the shipped entry points
# ---------------------------------------------------------------------------


def _git(repo: pathlib.Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def _build_repo(tmp_path: pathlib.Path) -> pathlib.Path:
    """A local repo on ``main`` with no remote."""
    repo = tmp_path / "dev_repo"
    repo.mkdir()
    _git(repo, "init", "--initial-branch=main")
    _git(repo, "config", "--local", "user.email", "test@pgai.local")
    _git(repo, "config", "--local", "user.name", "PGAI Test")
    (repo / "README.md").write_text("# fixture\n", encoding="utf-8")
    # The changelog writer reads this manifest; empty means nothing published.
    (repo / "release-notes").mkdir()
    (repo / "release-notes" / "PUBLISHED").write_text("", encoding="utf-8")
    _git(repo, "add", "README.md", "release-notes/PUBLISHED")
    _git(repo, "commit", "-m", "Initial commit")
    return repo


def _build_kanban_root(tmp_path: pathlib.Path, repo: pathlib.Path) -> pathlib.Path:
    """A throwaway kanban root whose release-state.md carries two event blocks."""
    root = tmp_path / "kanban_root"
    proj = root / "projects" / _PROJECT
    (proj / "tasks" / "queues").mkdir(parents=True)
    (proj / "release-state").mkdir()
    for sub in ("logs", "locks", "tmp"):
        (root / sub).mkdir()

    (root / "kanban.cfg").write_text(
        "[paths]\n\n[chain]\npm_mode = automatic\n", encoding="utf-8"
    )
    (root / "projects.cfg").write_text(
        f"[project:{_PROJECT}]\npriority=1\nenabled=true\n", encoding="utf-8"
    )
    (proj / "project.cfg").write_text(
        "[project]\n"
        f"project_name = {_PROJECT}\n"
        f"dev_tree_path = {repo}\n"
        "workflow_type = release\n"
        "is_self_build = false\n"
        "push_to_remote = false\n"
        "\n"
        "[versioning]\n"
        "max_patch = 99\n"
        "max_minor = 9\n"
        "max_major = 0\n",
        encoding="utf-8",
    )
    (proj / "release-state.md").write_text(_FIELDS_IDLE + _EVENT_BLOCKS, encoding="utf-8")

    # The CM scripts reach their python helpers under the kanban root, as the
    # live install lays them out.
    cm_dir = root / "pgai_agent_kanban" / "cm"
    cm_dir.mkdir(parents=True)
    for src in _CM_PY_SRC.glob("*.py"):
        (cm_dir / src.name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return root


def _add_passing_tester_task(root: pathlib.Path, version: str) -> None:
    """A DONE TESTER task with a PASS / low-risk report, which release.sh requires."""
    task_id = f"TESTER-20260101-001-verify-{version.replace('.', '-')}"
    task_dir = root / "projects" / _PROJECT / "tasks" / task_id
    (task_dir / "artifacts").mkdir(parents=True)
    (task_dir / "README.md").write_text(
        f"# {task_id}\n\n## Role\nTESTER\n\n## Release Version\n{version}\n",
        encoding="utf-8",
    )
    (task_dir / "status.md").write_text(
        "# Status\n\n## State\nDONE\n\n## Summary\nfixture\n", encoding="utf-8"
    )
    (task_dir / "artifacts" / "report.md").write_text(
        f"# TESTER Report for {version}\n\n"
        "## Recommendation\nPASS\n\n"
        "## Systemic Risk\nlow\n\n"
        "## Summary\nAll checks passed.\n",
        encoding="utf-8",
    )


def _commit_on_rc_branch(repo: pathlib.Path, version: str) -> None:
    """Stand-in for coder work merged into the RC branch."""
    _git(repo, "checkout", f"rc/{version}")
    (repo / f"feature-{version}.txt").write_text("feature\n", encoding="utf-8")
    _git(repo, "add", f"feature-{version}.txt")
    _git(repo, "commit", "-m", f"feature for {version}")
    _git(repo, "checkout", "main")


def _run(tmp_path: pathlib.Path, root: pathlib.Path, repo: pathlib.Path, argv: list[str]):
    foreign_cwd = tmp_path / "elsewhere"
    foreign_cwd.mkdir(exist_ok=True)
    (tmp_path / "home").mkdir(exist_ok=True)
    env = build_scrubbed_env(
        home_dir=tmp_path / "home",
        # The interpreter running this suite, so the scripts' python3 has the
        # version the tree is written for.
        extra_path_dirs=[pathlib.Path(sys.executable).parent],
        extra_env={
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "KANBAN_ROOT": str(root),
            "TEAM_ROOT": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
            "PGAI_PROJECT_NAME": _PROJECT,
            "REPO_ROOT": str(repo),
        },
    )
    proc = subprocess.Popen(
        ["bash", *argv], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, env=env, cwd=str(foreign_cwd),
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        proc.communicate()
        raise
    return proc.returncode, stdout, stderr


def _diagnostic(rc: int, stdout: str, stderr: str) -> str:
    return f"exit={rc}\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}"


def _assert_step_result(state_path: pathlib.Path, step: str, run, expected: dict) -> None:
    """Events kept byte-for-byte, fields set as expected, loader accepts the file."""
    rc, stdout, stderr = run
    diag = _diagnostic(rc, stdout, stderr)
    assert rc == 0, f"{step} failed\n{diag}"
    text = state_path.read_text(encoding="utf-8")
    assert _EVENT_BLOCKS in text, (
        f"{step} did not keep the appended event blocks byte-for-byte\n"
        f"--- release-state.md ---\n{text}\n{diag}"
    )
    assert text.index("## Active RC") < text.index("## HALT Event"), text
    loaded = load_release_state(str(state_path))
    for heading, want in expected.items():
        got = loaded[heading]
        if want is _TIMESTAMP:
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}([+-]\d{2}:\d{2}|Z)?", got), (
                f"{step}: '## {heading}' is {got!r}, expected a timestamp\n{text}"
            )
        else:
            assert got == want, f"{step}: '## {heading}' is {got!r}, expected {want!r}\n{text}"


_TIMESTAMP = object()


def test_open_release_open_cancel_keep_event_blocks(tmp_path: pathlib.Path) -> None:
    """open-rc, release, open-rc again and cancel-rc each keep both event blocks
    byte-for-byte and leave the four fields at that step's values."""
    require_binary("git", label="release-state-field-writer")
    repo = _build_repo(tmp_path)
    root = _build_kanban_root(tmp_path, repo)
    state = root / "projects" / _PROJECT / "release-state.md"

    _assert_step_result(
        state, "open-rc v0.1.0",
        _run(tmp_path, root, repo,
             [str(_OPEN_RC), "--project", _PROJECT, "v0.1.0", "CM-fixture-open-1"]),
        {"Active RC": "v0.1.0", "RC Opened At": _TIMESTAMP,
         "RC Opened By Task": "CM-fixture-open-1", "Last Released": "v0.0.9"},
    )

    _commit_on_rc_branch(repo, "v0.1.0")
    _add_passing_tester_task(root, "v0.1.0")
    _assert_step_result(
        state, "release v0.1.0",
        _run(tmp_path, root, repo, [str(_RELEASE), "--project", _PROJECT]),
        {"Active RC": "none", "RC Opened At": "none",
         "RC Opened By Task": "none", "Last Released": "v0.1.0"},
    )

    _assert_step_result(
        state, "open-rc v0.2.0",
        _run(tmp_path, root, repo,
             [str(_OPEN_RC), "--project", _PROJECT, "v0.2.0", "CM-fixture-open-2"]),
        {"Active RC": "v0.2.0", "RC Opened At": _TIMESTAMP,
         "RC Opened By Task": "CM-fixture-open-2", "Last Released": "v0.1.0"},
    )

    _assert_step_result(
        state, "cancel-rc v0.2.0",
        _run(tmp_path, root, repo,
             [str(_CANCEL_RC), "--project", _PROJECT, "--key", "v0.2.0", "--yes"]),
        {"Active RC": "none", "RC Opened At": "none",
         "RC Opened By Task": "none", "Last Released": "v0.1.0"},
    )
