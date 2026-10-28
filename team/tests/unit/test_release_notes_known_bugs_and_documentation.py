"""
test_release_notes_known_bugs_and_documentation.py
===================================================
The generated release notes fill ``## Known Issues`` under status
``KNOWN-BUGS`` from the release's own ``## Bugs Skipped`` rows, and fill
``## Documentation`` with the WRITER tickets that shipped in the release.

Covered:

  * KNOWN-BUGS with a Bugs Skipped block holding a medium row: Known Issues
    is not ``None`` and lists that row's substance; a low row is left out.
  * KNOWN-BUGS whose every row is rated low: Known Issues points at the
    Bugs Skipped table instead of reading ``None``.
  * KNOWN-BUGS with a row whose severity names no recognised rung: the row
    is listed, marked not rated.
  * An RC whose task folders hold a DONE WRITER ticket for the release:
    Documentation lists the lead sentence of that ticket's Goal, with no
    task id, bug id or local absolute path; a WRITER ticket for another
    release, a WRITER ticket not DONE and a CODER ticket are not listed.
  * A DONE WRITER ticket whose Goal lead sentence, scrubbed, names only the
    bugs it cites ("Paired documentation for" one bug id): no Documentation
    bullet carries the "another item" placeholder or a raw bug id; the
    ticket is listed by its task title, or, when the title also names only
    ids or is absent, by a plain statement that documentation shipped.
  * FUNCTIONAL with no skipped bugs and no WRITER tickets: both sections
    read ``None``; FUNCTIONAL with Bugs Skipped rows still reads ``None``
    under Known Issues.
  * The release stage (``cm_release_generate_notes`` in
    ``team/scripts/lib/cm/release_notes.sh``) hands the generator the
    project's tasks/ directory, so a stage run with the real generator
    lists the WRITER ticket under Documentation and the medium row under
    Known Issues.

Fidelity: the generator cases call the shipped module's CLI entry point,
``main()``, with the argv the release stage passes; the Bugs Skipped block
is built by the real ``build_bugs_skipped_notes_block`` plus the table
header ``cm_build_bugs_skipped_ship_block`` adds. The stage case sources the
real stage into bash against a throwaway git repository, stubbing only what
its caller (cm/release.sh) provides. Every path is under pytest's tmp_path.
"""

from __future__ import annotations

import os
import pathlib
import re
import signal
import subprocess
import sys

from pgai_agent_kanban.cm.release_notes_generator import main as release_notes_main
from pgai_agent_kanban.cm.release_notes_generator import shipped_documentation
from tests.fixtures.binary_guard import require_binary
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env
from tests.unit.shell_harness import run_bash

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_KNOWN_ISSUE_LIB = "scripts/lib/cm/release_known_issue.sh"
_RELEASE_NOTES_SH = _TEAM_DIR / "scripts" / "lib" / "cm" / "release_notes.sh"
_VERSION = "v9.9.9"
_TIMEOUT_SECONDS = 60

_BUGS_SKIPPED_HEADER = (
    "| Substance (what was wrong) | Public ID (if disclosed) | Severity | "
    "Rationale for Skipping |"
)
_BUGS_SKIPPED_SEPARATOR = (
    "|------------------------------|---------------------------|----------"
    "|-------------------------|"
)

_MEDIUM_SYMPTOM = "The queue view drops the last row when the list is full."
_LOW_SYMPTOM = "The help text misspells the word configuration."
_UNRATED_SYMPTOM = "The archive count is off by one after a restore."

_WRITER_GOAL_LEAD = "The operations guide describes the cleanup step that deletes stale lock records."
_TASK_ID_SHAPE = re.compile(r"\b(?:CODER|WRITER|TESTER|CM|PM|PO)-\d{8}-\d+")
_BUG_ID_SHAPE = re.compile(r"\bBUG-\d+")
_ID_PLACEHOLDER = "another item"
_DOCUMENTATION_SHIPPED = "- Further documentation updates shipped in this release."
_PAIRED_GOAL = "Paired documentation for BUG-0001."
_BUG_LIST_GOAL = (
    "Documentation for BUG-0001, BUG-0002, BUG-0003 and BUG-0004, "
    "written after the matching code tickets merge."
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_bug(bugs_dir: pathlib.Path, name: str, symptom: str, severity: str) -> pathlib.Path:
    bugs_dir.mkdir(parents=True, exist_ok=True)
    path = bugs_dir / f"{name}.md"
    path.write_text(
        f"# {name}\n\n## Status\nopen\n\n## Symptom\n{symptom}\n\n## Severity\n{severity}\n",
        encoding="utf-8",
    )
    return path


def _bugs_skipped_block(tmp_path: pathlib.Path, *pairs: tuple[pathlib.Path, str]) -> str:
    """The full Bugs Skipped block, built as cm_build_bugs_skipped_ship_block builds it."""
    args = " ".join(f"'{path}' '{rationale}'" for path, rationale in pairs)
    result = run_bash(tmp_path, f"source {_KNOWN_ISSUE_LIB} && build_bugs_skipped_notes_block {args}")
    assert result.returncode == 0, result.stderr
    return f"{_BUGS_SKIPPED_HEADER}\n{_BUGS_SKIPPED_SEPARATOR}\n{result.stdout.strip()}"


def _write_task(
    tasks_dir: pathlib.Path, task_id: str, role: str, version: str, state: str, goal: str,
    title: str | None = "fixture",
) -> pathlib.Path:
    """A task folder whose README opens with '# Task: <title>' (no H1 when *title* is None)."""
    task_dir = tasks_dir / task_id
    task_dir.mkdir(parents=True)
    heading = f"# Task: {title}\n\n" if title is not None else ""
    (task_dir / "README.md").write_text(
        f"{heading}## Task ID\n{task_id}\n\n## Role\n{role}\n\n"
        f"## Goal\n{goal}\n\n## Release Version\n{version}\n",
        encoding="utf-8",
    )
    (task_dir / "status.md").write_text(
        f"# Status\n\n## Task\n{task_id}\n\n## State\n{state}\n", encoding="utf-8"
    )
    return task_dir


def _shipped_writer_tasks(tasks_dir: pathlib.Path) -> None:
    """One DONE WRITER ticket for the release, plus three that must not be listed."""
    _write_task(
        tasks_dir, "WRITER-20990101-004-ops-cleanup-docs", "WRITER", _VERSION, "DONE",
        f"{_WRITER_GOAL_LEAD} It names BUG-0900 and /home/someone/notes.md in its detail.",
    )
    _write_task(tasks_dir, "WRITER-20990101-005-other-release", "WRITER", "v9.9.8", "DONE",
                "A goal for another release.")
    _write_task(tasks_dir, "WRITER-20990101-006-not-done", "WRITER", _VERSION, "WAITING",
                "A goal that did not ship.")
    _write_task(tasks_dir, "CODER-20990101-007-code", "CODER", _VERSION, "DONE",
                "A code change, not documentation.")


def _generate(
    tmp_path: pathlib.Path, status: str, bugs_skipped_block: str = "", tasks_dir: str = "none"
) -> str:
    """Run the shipped generator with the argv cm_release_generate_notes passes."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir(exist_ok=True)
    argv = [
        "none", str(repo_root), _VERSION, status, "2099-01-01", "test-project",
        "none", "rc/" + _VERSION, "", bugs_skipped_block, tasks_dir,
    ]
    assert release_notes_main(argv) == 0
    return (repo_root / "release-notes" / f"{_VERSION}.md").read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    match = re.search(rf"^##\s+{re.escape(heading)}\s*$", text, re.MULTILINE)
    assert match, f"'## {heading}' not found in:\n{text}"
    rest = text[match.end():]
    following = re.search(r"^##\s+", rest, re.MULTILINE)
    return (rest[: following.start()] if following else rest).strip()


# ---------------------------------------------------------------------------
# Known Issues under KNOWN-BUGS
# ---------------------------------------------------------------------------


def test_known_bugs_lists_the_medium_bugs_skipped_row(tmp_path):
    bugs = tmp_path / "bugs"
    block = _bugs_skipped_block(
        tmp_path,
        (_write_bug(bugs, "BUG-0901-queue", _MEDIUM_SYMPTOM, "medium"), "Deferred to a follow-up release."),
        (_write_bug(bugs, "BUG-0902-typo", _LOW_SYMPTOM, "low"), "Cosmetic."),
    )

    notes = _generate(tmp_path, "KNOWN-BUGS", bugs_skipped_block=block)

    known = _section(notes, "Known Issues")
    assert known != "None", notes
    assert f"- {_MEDIUM_SYMPTOM} (severity medium)" in known.splitlines(), known
    assert "Bugs Skipped" in known, "the section points the reader at the table"
    assert _LOW_SYMPTOM not in known, "a low row is not a known issue"
    assert _section(notes, "Bugs Skipped") == block, "the table itself is placed as given"


def test_known_bugs_with_only_low_rows_points_at_the_table(tmp_path):
    block = _bugs_skipped_block(
        tmp_path, (_write_bug(tmp_path / "bugs", "BUG-0903-typo", _LOW_SYMPTOM, "low"), "Cosmetic.")
    )

    known = _section(_generate(tmp_path, "KNOWN-BUGS", bugs_skipped_block=block), "Known Issues")

    assert known != "None"
    assert "Bugs Skipped table above lists them" in known, known
    assert _LOW_SYMPTOM not in known


def test_known_bugs_lists_an_unrated_row(tmp_path):
    bugs = tmp_path / "bugs"
    unrated = bugs / "BUG-0904-archive.md"
    bugs.mkdir()
    unrated.write_text(f"# BUG-0904-archive\n\n## Symptom\n{_UNRATED_SYMPTOM}\n", encoding="utf-8")
    block = _bugs_skipped_block(tmp_path, (unrated, "Deferred."))

    known = _section(_generate(tmp_path, "KNOWN-BUGS", bugs_skipped_block=block), "Known Issues")

    assert f"- {_UNRATED_SYMPTOM} (severity not rated)" in known.splitlines(), known


def test_known_bugs_with_no_skipped_rows_reads_none(tmp_path):
    assert _section(_generate(tmp_path, "KNOWN-BUGS"), "Known Issues") == "None"


# ---------------------------------------------------------------------------
# Documentation from the release's WRITER tickets
# ---------------------------------------------------------------------------


def test_documentation_lists_the_shipped_writer_ticket_goal_without_ids(tmp_path):
    tasks_dir = tmp_path / "tasks"
    _shipped_writer_tasks(tasks_dir)

    notes = _generate(tmp_path, "FUNCTIONAL", tasks_dir=str(tasks_dir))

    docs = _section(notes, "Documentation")
    assert docs.splitlines() == [f"- {_WRITER_GOAL_LEAD}"], docs
    assert not _TASK_ID_SHAPE.search(notes), notes
    assert "BUG-0900" not in notes and "/home/someone/notes.md" not in notes


def test_documentation_reads_a_prefixed_release_version(tmp_path):
    tasks_dir = tmp_path / "tasks"
    _write_task(tasks_dir, "WRITER-20990101-008-prefixed", "**WRITER**", f"ai_{_VERSION}", "DONE",
                "The guide names the installed script paths.")

    docs = _section(_generate(tmp_path, "FUNCTIONAL", tasks_dir=str(tasks_dir)), "Documentation")

    assert docs == "- The guide names the installed script paths.", docs


def test_documentation_scrubs_a_task_id_and_local_path_in_the_lead_sentence(tmp_path):
    tasks_dir = tmp_path / "tasks"
    _write_task(tasks_dir, "WRITER-20990101-009-paired", "WRITER", _VERSION, "DONE",
                "Document what CODER-20990101-003-code changed in `/tmp/scratch/out.md` for operators.")

    docs = _section(_generate(tmp_path, "FUNCTIONAL", tasks_dir=str(tasks_dir)), "Documentation")

    assert not _TASK_ID_SHAPE.search(docs), docs
    assert "/tmp/scratch/out.md" not in docs, docs
    assert docs.startswith("- Document what another item changed in a local path"), docs


def _assert_no_placeholder_or_bug_id(bullets: list[str]) -> None:
    for bullet in bullets:
        assert _ID_PLACEHOLDER not in bullet, bullets
        assert not _BUG_ID_SHAPE.search(bullet), bullets


def test_documentation_paired_goal_is_listed_by_its_task_title(tmp_path):
    tasks_dir = tmp_path / "tasks"
    _write_task(tasks_dir, "WRITER-20990101-011-paired", "WRITER", "v1.2.3", "DONE",
                _PAIRED_GOAL, title="The operations guide names the per-run evidence location")

    bullets = shipped_documentation(str(tasks_dir), "v1.2.3")

    _assert_no_placeholder_or_bug_id(bullets)
    assert bullets == ["- The operations guide names the per-run evidence location."], bullets


def test_documentation_paired_goal_with_an_id_only_title_says_documentation_shipped(tmp_path):
    tasks_dir = tmp_path / "tasks"
    _write_task(tasks_dir, "WRITER-20990101-012-paired", "WRITER", "v1.2.3", "DONE",
                _PAIRED_GOAL, title="Docs for BUG-0001")

    bullets = shipped_documentation(str(tasks_dir), "v1.2.3")

    _assert_no_placeholder_or_bug_id(bullets)
    assert bullets == [_DOCUMENTATION_SHIPPED], bullets


def test_documentation_goal_listing_only_bug_ids_says_documentation_shipped(tmp_path):
    tasks_dir = tmp_path / "tasks"
    _write_task(tasks_dir, "WRITER-20990101-013-bug-list", "WRITER", "v1.2.3", "DONE",
                _BUG_LIST_GOAL, title=None)

    bullets = shipped_documentation(str(tasks_dir), "v1.2.3")

    _assert_no_placeholder_or_bug_id(bullets)
    assert bullets == [_DOCUMENTATION_SHIPPED], bullets


def test_documentation_goal_listing_only_bug_ids_renders_through_the_cli(tmp_path):
    tasks_dir = tmp_path / "tasks"
    _write_task(tasks_dir, "WRITER-20990101-014-bug-list", "WRITER", _VERSION, "DONE",
                _BUG_LIST_GOAL, title="Operator docs: runner keys and the name rule")

    docs = _section(_generate(tmp_path, "FUNCTIONAL", tasks_dir=str(tasks_dir)), "Documentation")

    _assert_no_placeholder_or_bug_id(docs.splitlines())
    assert docs == "- Operator docs: runner keys and the name rule.", docs


# ---------------------------------------------------------------------------
# Nothing to list
# ---------------------------------------------------------------------------


def test_functional_with_nothing_to_list_reads_none_in_both_sections(tmp_path):
    tasks_dir = tmp_path / "tasks"
    _write_task(tasks_dir, "CODER-20990101-010-code", "CODER", _VERSION, "DONE", "A code change.")

    notes = _generate(tmp_path, "FUNCTIONAL", tasks_dir=str(tasks_dir))

    assert _section(notes, "Known Issues") == "None"
    assert _section(notes, "Documentation") == "None"


def test_functional_with_skipped_rows_keeps_known_issues_none(tmp_path):
    block = _bugs_skipped_block(
        tmp_path, (_write_bug(tmp_path / "bugs", "BUG-0905-queue", _MEDIUM_SYMPTOM, "medium"), "Deferred.")
    )

    assert _section(_generate(tmp_path, "FUNCTIONAL", bugs_skipped_block=block), "Known Issues") == "None"


# ---------------------------------------------------------------------------
# The release stage passes the tasks directory
# ---------------------------------------------------------------------------

# Stubs for what cm/release.sh provides; pp_run_ops runs the real generator
# from this tree and answers the changelog render with a fixed text.
_HARNESS = r"""
set -euo pipefail
source "$STAGE"
git_step() { shift; "$@"; }
pp_strip_prefix_from_tag() { printf '%s\n' "$2"; }
pp_project_root() { printf '%s\n' "$FIXTURE_PROJECT_ROOT"; }
cm_ship_posture_stamp_notes() { :; }
cm_release_write_version_file() { :; }
pp_run_ops() {
  local module="$1"; shift
  if [[ "$module" == pgai_agent_kanban.cm.changelog_writer ]]; then
    printf '# Changelog\n'
  else
    PYTHONPATH="$TEAM_DIR" "$PYTHON" -m "$module" "$@"
  fi
}
cm_release_generate_notes
"""


def test_release_stage_hands_the_generator_the_project_tasks(tmp_path):
    require_binary("git", label="release-notes-known-bugs-and-documentation")
    repo = tmp_path / "dev_repo"
    repo.mkdir()
    for args in (["init", "--initial-branch=main"], ["config", "--local", "user.email", "t@pgai.local"],
                 ["config", "--local", "user.name", "PGAI Test"]):
        subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True)
    (repo / "README.md").write_text("See CHANGELOG.md.\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], capture_output=True, check=True)

    project_root = tmp_path / "kanban" / "projects" / "fixture"
    _shipped_writer_tasks(project_root / "tasks")
    block = _bugs_skipped_block(
        tmp_path, (_write_bug(project_root / "bugs", "BUG-0906-queue", _MEDIUM_SYMPTOM, "medium"), "Deferred.")
    )
    (tmp_path / "home").mkdir()
    cwd = tmp_path / "elsewhere"
    cwd.mkdir()
    env = build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_path_dirs=[pathlib.Path(sys.executable).parent],
        extra_env={
            "STAGE": str(_RELEASE_NOTES_SH),
            "TEAM_DIR": str(_TEAM_DIR),
            "PYTHON": sys.executable,
            "FIXTURE_PROJECT_ROOT": str(project_root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(tmp_path / "kanban"),
            "KANBAN_ROOT": str(tmp_path / "kanban"),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "sandbox_tmp"),
            "REPO_ROOT": str(repo),
            "MAIN_BRANCH": "main",
            "RC_BRANCH": "main",
            "ACTIVE_RC": _VERSION,
            "PROJECT_NAME": "fixture",
            "_CM_PROJECT_ROOT": str(project_root),
            "_RELEASE_NOTES_STATUS": "KNOWN-BUGS",
            "_BUGS_SKIPPED_NOTES_BLOCK": block,
        },
    )
    proc = subprocess.Popen(
        ["bash", "-c", _HARNESS], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, env=env, cwd=str(cwd), start_new_session=True,
    )
    try:
        out, err = proc.communicate(timeout=_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        proc.communicate()
        raise
    diag = f"exit={proc.returncode}\n--- stdout ---\n{out}\n--- stderr ---\n{err}"

    assert proc.returncode == 0, diag
    notes = (repo / "release-notes" / f"{_VERSION}.md").read_text(encoding="utf-8")
    assert _section(notes, "Documentation") == f"- {_WRITER_GOAL_LEAD}", notes
    assert f"- {_MEDIUM_SYMPTOM} (severity medium)" in _section(notes, "Known Issues").splitlines(), notes
