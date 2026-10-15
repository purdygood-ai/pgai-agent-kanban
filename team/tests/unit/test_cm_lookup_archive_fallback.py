"""
test_cm_lookup_archive_fallback.py
==================================
The CM-side lookups by bug ID fall back to the project's archive when an ID
is not live: the Fixed-In restamp (``restamp-fixed-in.sh`` and
``cm.fixed_in_restamp``), the Fixed-In writeback's ``stamp`` step
(``cm.fixed_in_writeback``), and the changelog writer
(``cm.changelog_writer``). A live item always wins; the archive is consulted
only for an ID with no live match.

Covered, every fixture in a sandbox kanban root under pytest's tmp_path
(``tests/fixtures/archive_sandbox.py``), archived through the real
``archive.plan`` / ``archive.apply`` where the test is about "after an
archive run":

  * ``archive.layout.archive_layout_for_category_dir``: the archive tree of
    a live ``projects/<project>/<category>`` directory, and ``None`` for any
    other shape (so no reader handed a non-project directory ever reaches an
    archive).
  * ``restamp-fixed-in.sh`` (real script, subprocess):
      - an archived done bug named by ID is stamped in place in the archive,
        marked ``(archived)``, and nothing is written to the live ledger;
      - correction mode replaces an archived bug's wrong value in place;
      - a bug live and archived under the same number: the live file is
        stamped, the archived copy is left byte-for-byte alone;
      - an ID neither live nor archived is refused (``bug_not_found``)
        naming both directories, and nothing is written anywhere;
      - an ID matching two archived files is refused (``bug_ambiguous``).
  * ``fixed_in_writeback.stamp_fixed_in``: a named bug archived between
    capture and stamp is stamped in the archive; a named bug in neither
    place still counts as failed; the earlier-unstamped count reads the
    live ledger alone.
  * ``changelog_writer``:
      - a CHANGELOG regenerated after archiving the fixture's bugs and
        requirements is byte-identical to one regenerated before (with and
        without a published CHANGELOG, and through the module CLI the
        release engine calls);
      - a disclosable archived bug with no Public ID is assigned the same ID
        a live run assigns, written back in place in the archive;
      - a live bug file wins over an archived one with the same number;
      - ``load_bugs`` reads the archive only when asked, and never for a
        bugs directory outside the live-project shape.

Nothing here reads or writes the live install: the real script runs with an
environment built from an explicit set (``build_scrubbed_env``) whose every
kanban root names the sandbox, and every Python call is handed sandbox paths.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

from pgai_agent_kanban.archive import RESULT_OK, apply, plan
from pgai_agent_kanban.archive.layout import (
    CATEGORY_BUGS,
    CATEGORY_TASKS,
    archive_layout_for_category_dir,
)
from pgai_agent_kanban.cm.changelog_writer import (
    archived_bug_paths,
    load_bugs,
    load_published_manifest,
    parse_bug_file,
    regenerate,
)
from pgai_agent_kanban.cm.fixed_in_writeback import stamp_fixed_in
from tests.fixtures.archive_sandbox import ArchiveSandbox
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_RESTAMP_SH = _TEAM_DIR / "scripts" / "restamp-fixed-in.sh"

_VERSIONS = ["v1.2.0", "v1.1.0", "v1.0.0"]  # newest first, as the release engine passes them
_PUBLISHED = ["v1.0.0", "v1.1.0"]


# ---------------------------------------------------------------------------
# Sandbox content
# ---------------------------------------------------------------------------


def _bug_text(
    stem: str,
    status: str,
    symptom: str,
    *,
    affects: str = "",
    fixed_in: str = "",
    public_id: str = "",
) -> str:
    """A bug file in the ledger's shape, with template comments in the optional sections."""
    affects_block = f"## Affects\n{affects}\n\n" if affects else ""
    return (
        f"# {stem}\n\n**Severity:** medium\n\n"
        f"## Status\n{status}\n\n"
        f"## Symptom\n{symptom}\n\n"
        f"{affects_block}"
        "## Fixed In\n<!-- Written by the release engine at the fixing release. -->\n"
        f"{fixed_in + chr(10) if fixed_in else ''}\n"
        "## Public ID\n<!-- Assigned at first disclosure. -->\n"
        f"{public_id + chr(10) if public_id else ''}"
    )


def _write_bug(box: ArchiveSandbox, number: int, slug: str, status: str, symptom: str, **fields: str) -> pathlib.Path:
    path = box.add_bug(number, status, slug=slug)
    path.write_text(_bug_text(path.stem, status, symptom, **fields), encoding="utf-8")
    return path


def _ledger_sandbox(parent: pathlib.Path) -> ArchiveSandbox:
    """Six bugs and three shipped requirements; archive_count 2 archives bugs 1-4 and v1.0.0.

    Bug 1 is disclosed with an ID, bug 2 is internal, bug 3 is disclosable
    but has no ID yet, bug 4 is disclosed with an ID; bugs 5 (done) and 6
    (open, disclosable, no ID) are the newest two and stay live.
    """
    box = ArchiveSandbox(parent, active_rc="none", last_released="v1.2.0")
    box.set_archive_count(2)
    (box.root / "tmp").mkdir(exist_ok=True)
    _write_bug(box, 1, "dashboard-crash", "done", "The dashboard crashes on load.",
               affects="v1.0.0", fixed_in="v1.1.0", public_id="KI-1.0.0.1")
    _write_bug(box, 2, "internal-lint-noise", "done", "A lint printed noise on every run.",
               fixed_in="v1.2.0")
    _write_bug(box, 3, "queue-marker-drift", "done", "Queue markers drifted after a restart.",
               affects="v1.1.0", fixed_in="v1.2.0")
    _write_bug(box, 4, "slow-board", "done", "The board took ten seconds to render.",
               affects="v1.0.0", fixed_in="v1.2.0", public_id="KI-1.0.0.2")
    _write_bug(box, 5, "help-typo", "done", "The help text misspelled a flag.", fixed_in="v1.2.0")
    _write_bug(box, 6, "export-hang", "open", "Export hangs on a large project.", affects="v1.1.0")
    for version, slug in (("v1.0.0", "first"), ("v1.1.0", "second"), ("v1.2.0", "third")):
        box.add_requirement(version, "done", slug=slug)
    return box


def _repo(parent: pathlib.Path) -> pathlib.Path:
    """A dev tree holding the release notes and PUBLISHED manifest the writer reads."""
    repo = parent / "repo"
    notes = repo / "release-notes"
    notes.mkdir(parents=True)
    (notes / "PUBLISHED").write_text("\n".join(_PUBLISHED) + "\n", encoding="utf-8")
    bodies = {
        "v1.0.0": ("2026-01-10", "- Board view\n", "None\n"),
        "v1.1.0": ("2026-02-10", "- Export command\n", "- BUG-0001 — dashboard crash\n"),
        "v1.2.0": (
            "2026-03-10",
            "None\n",
            "- BUG-0002 — lint noise\n- BUG-0003 — marker drift\n- BUG-0004 — slow board\n"
            "- BUG-0005 — help typo\n",
        ),
    }
    for version, (date, features, fixes) in bodies.items():
        (notes / f"{version}.md").write_text(
            f"# Release {version}\n\n**Release Date:** {date}\n\n"
            f"## Summary\nRelease {version} of the example project.\n\n"
            f"## Features\n{features}\n## Bug Fixes\n{fixes}",
            encoding="utf-8",
        )
    return repo


def _bugs_dir(box: ArchiveSandbox) -> pathlib.Path:
    return box.project_root / "bugs"


def _archived_bugs_dir(box: ArchiveSandbox) -> pathlib.Path:
    return box.root / "archive" / box.project / "bugs"


def _archive_now(box: ArchiveSandbox) -> tuple[str, ...]:
    """Run the real archive engine on the sandbox; return the IDs it moved."""
    result = apply(plan(box.project, kanban_root=box.root), log=lambda _line: None)
    assert result.status == RESULT_OK, result.error
    return tuple(result.moved_ids)


def _regenerate(repo: pathlib.Path, box: ArchiveSandbox, published_changelog=None) -> str:
    return regenerate(
        repo, _VERSIONS, load_published_manifest(repo), _bugs_dir(box),
        published_changelog=published_changelog,
    )


def _snapshot(directory: pathlib.Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(directory.glob("*.md"))} if directory.is_dir() else {}


def _run_restamp(box: ArchiveSandbox, args: list[str], tmp_path: pathlib.Path) -> subprocess.CompletedProcess:
    """Run the real restamp-fixed-in.sh against the sandbox from a foreign cwd."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = build_scrubbed_env(
        home_dir=home,
        extra_path_dirs=[pathlib.Path(sys.executable).parent],
        extra_env={
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(box.root),
            "KANBAN_ROOT": str(box.root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(box.root / "tmp"),
        },
    )
    return subprocess.run(
        [str(_RESTAMP_SH), "--project", box.project, *args],
        capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=60,
        stdin=subprocess.DEVNULL,
    )


def _say(result: subprocess.CompletedProcess) -> str:
    return f"exit {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"


# ---------------------------------------------------------------------------
# archive.layout.archive_layout_for_category_dir
# ---------------------------------------------------------------------------


def test_category_dir_of_a_live_project_maps_to_that_roots_archive(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "kanban"
    layout = archive_layout_for_category_dir(root / "projects" / "demo" / "bugs", CATEGORY_BUGS)
    assert layout is not None
    assert layout.bugs == root / "archive" / "demo" / "bugs"


@pytest.mark.parametrize(
    ("relative", "category"),
    [
        ("projects/demo/tasks", CATEGORY_BUGS),   # directory not named for the category
        ("bugs", CATEGORY_BUGS),                   # single-project install: no per-project archive
        ("elsewhere/demo/bugs", CATEGORY_BUGS),    # parent's parent is not projects/
        ("projects/Demo/bugs", CATEGORY_BUGS),     # project name is not a safe path component
        ("projects/demo/queues", "queues"),        # not an item category
    ],
)
def test_category_dir_outside_the_live_project_shape_has_no_archive(
    tmp_path: pathlib.Path, relative: str, category: str
) -> None:
    assert archive_layout_for_category_dir(tmp_path / relative, category) is None


def test_tasks_dir_mapping_is_unchanged(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "kanban"
    layout = archive_layout_for_category_dir(root / "projects" / "demo" / "tasks", CATEGORY_TASKS)
    assert layout is not None and layout.tasks == root / "archive" / "demo" / "tasks"


# ---------------------------------------------------------------------------
# restamp-fixed-in.sh
# ---------------------------------------------------------------------------


def test_restamp_finds_an_archived_bug_by_id_and_stamps_it_in_the_archive(tmp_path: pathlib.Path) -> None:
    box = _ledger_sandbox(tmp_path)
    _write_bug(box, 2, "internal-lint-noise", "done", "A lint printed noise on every run.")  # empty Fixed In
    assert "BUG-0002" in _archive_now(box)
    archived = _archived_bugs_dir(box) / "BUG-0002-internal-lint-noise.md"
    live_before = _snapshot(_bugs_dir(box))

    result = _run_restamp(box, ["--version", "v1.2.0", "BUG-0002"], tmp_path)

    assert result.returncode == 0, _say(result)
    assert parse_bug_file(archived).fixed_in == "v1.2.0", "the archived bug should be stamped in place"
    assert "stamped: BUG-0002-internal-lint-noise.md (archived)" in result.stdout, _say(result)
    assert result.stdout.strip().splitlines()[-1] == "Fixed-In restamp v1.2.0: 1 stamped, 0 skipped."
    assert _snapshot(_bugs_dir(box)) == live_before, "nothing may be written to the live ledger"


def test_restamp_correction_mode_rewrites_an_archived_wrong_value(tmp_path: pathlib.Path) -> None:
    box = _ledger_sandbox(tmp_path)
    _archive_now(box)
    archived = _archived_bugs_dir(box) / "BUG-0004-slow-board.md"

    result = _run_restamp(box, ["--version", "v1.1.0", "--replace-from", "v1.2.0", "BUG-0004"], tmp_path)

    assert result.returncode == 0, _say(result)
    assert parse_bug_file(archived).fixed_in == "v1.1.0"
    assert "replaced (was v1.2.0): BUG-0004-slow-board.md (archived)" in result.stdout, _say(result)


def test_restamp_prefers_the_live_bug_over_an_archived_copy(tmp_path: pathlib.Path) -> None:
    box = _ledger_sandbox(tmp_path)
    archive_dir = _archived_bugs_dir(box)
    archive_dir.mkdir(parents=True)
    stale = archive_dir / "BUG-0005-help-typo-old-copy.md"
    stale.write_text(_bug_text(stale.stem, "done", "Stale copy."), encoding="utf-8")
    _write_bug(box, 5, "help-typo", "done", "The help text misspelled a flag.")  # live, empty Fixed In
    stale_before = stale.read_bytes()

    result = _run_restamp(box, ["--version", "v1.2.0", "BUG-0005"], tmp_path)

    assert result.returncode == 0, _say(result)
    assert parse_bug_file(_bugs_dir(box) / "BUG-0005-help-typo.md").fixed_in == "v1.2.0"
    assert "stamped: BUG-0005-help-typo.md\n" in result.stdout, "a live bug carries no (archived) mark"
    assert stale.read_bytes() == stale_before, "the archived copy must be left alone when the bug is live"


def test_restamp_refuses_an_id_neither_live_nor_archived_and_writes_nothing(tmp_path: pathlib.Path) -> None:
    box = _ledger_sandbox(tmp_path)
    _write_bug(box, 2, "internal-lint-noise", "done", "A lint printed noise on every run.")
    _archive_now(box)
    live_before = _snapshot(_bugs_dir(box))
    archive_before = _snapshot(_archived_bugs_dir(box))

    result = _run_restamp(box, ["--version", "v1.2.0", "BUG-0002", "BUG-0099"], tmp_path)

    assert result.returncode == 2, _say(result)
    assert "error:   fixed_in_restamp.bug_not_found" in result.stderr, _say(result)
    assert f"or its archive {_archived_bugs_dir(box)}" in result.stderr, _say(result)
    assert f"remedy:  ls {_bugs_dir(box)} {_archived_bugs_dir(box)}" in result.stderr, _say(result)
    assert _snapshot(_bugs_dir(box)) == live_before
    assert _snapshot(_archived_bugs_dir(box)) == archive_before, "a refused run must not stamp the archived bug"


def test_restamp_refuses_an_id_matching_two_archived_files(tmp_path: pathlib.Path) -> None:
    box = _ledger_sandbox(tmp_path)
    _archive_now(box)
    twin = _archived_bugs_dir(box) / "BUG-0001-a-second-file.md"
    twin.write_text(_bug_text(twin.stem, "done", "Twin."), encoding="utf-8")
    archive_before = _snapshot(_archived_bugs_dir(box))

    result = _run_restamp(box, ["--version", "v1.2.0", "BUG-0001"], tmp_path)

    assert result.returncode == 2, _say(result)
    assert "error:   fixed_in_restamp.bug_ambiguous" in result.stderr, _say(result)
    assert _snapshot(_archived_bugs_dir(box)) == archive_before


# ---------------------------------------------------------------------------
# fixed_in_writeback.stamp_fixed_in
# ---------------------------------------------------------------------------


def test_writeback_stamps_a_named_bug_archived_since_capture(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    box = _ledger_sandbox(tmp_path)
    _write_bug(box, 2, "internal-lint-noise", "done", "A lint printed noise on every run.")
    _archive_now(box)
    archived = _archived_bugs_dir(box) / "BUG-0002-internal-lint-noise.md"
    live_before = _snapshot(_bugs_dir(box))

    result = stamp_fixed_in(
        _bugs_dir(box), "v1.2.0", ["BUG-0002-internal-lint-noise.md", "BUG-0098-never-filed.md"]
    )
    out = capsys.readouterr().out

    assert parse_bug_file(archived).fixed_in == "v1.2.0"
    assert result.stamped == 1, result
    assert result.failed == 1, "a named bug in neither place still counts as failed"
    assert "Archived bug read from the archive: BUG-0002-internal-lint-noise.md" in out, out
    assert "bug file no longer present: BUG-0098-never-filed.md" in out, out
    assert result.earlier_unstamped == 0, "the earlier-unstamped count reads the live ledger alone"
    assert _snapshot(_bugs_dir(box)) == live_before


def test_writeback_counts_an_archived_bug_already_set(tmp_path: pathlib.Path) -> None:
    box = _ledger_sandbox(tmp_path)
    _archive_now(box)
    archived = _archived_bugs_dir(box) / "BUG-0001-dashboard-crash.md"
    before = archived.read_bytes()

    result = stamp_fixed_in(_bugs_dir(box), "v1.2.0", [archived.name])

    assert (result.stamped, result.already_set, result.failed) == (0, 1, 0), result
    assert archived.read_bytes() == before


# ---------------------------------------------------------------------------
# changelog_writer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("published", [False, True], ids=["no-published-changelog", "published-changelog"])
def test_changelog_is_identical_before_and_after_archiving(tmp_path: pathlib.Path, published: bool) -> None:
    box = _ledger_sandbox(tmp_path)
    repo = _repo(tmp_path)
    published_changelog = None
    if published:
        # The first render assigns the missing Public IDs; publish it, as a release does.
        (repo / "CHANGELOG.md").write_text(_regenerate(repo, box), encoding="utf-8")
        published_changelog = pathlib.Path("CHANGELOG.md")
    before = _regenerate(repo, box, published_changelog)

    moved = _archive_now(box)

    assert {"BUG-0001", "BUG-0002", "BUG-0003", "BUG-0004", "v1.0.0-first.md"} <= set(moved), moved
    assert not list(_bugs_dir(box).glob("BUG-000[1-4]-*.md")), "the fixture's old bugs should be archived"
    after = _regenerate(repo, box, published_changelog)
    assert after == before, "archiving bugs and requirements must not change the regenerated CHANGELOG"
    # The render is non-trivial: archived internal and disclosed bugs both appear in it.
    assert "A lint printed noise on every run." in after
    assert "KI-1.0.0.1 — The dashboard crashes on load." in after
    assert "bug files unavailable" not in after


def test_module_cli_renders_the_same_changelog_after_archiving(tmp_path: pathlib.Path) -> None:
    """The release engine calls the module CLI with the dev tree and the live bugs dir."""
    box = _ledger_sandbox(tmp_path)
    repo = _repo(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    env = build_scrubbed_env(
        home_dir=home,
        extra_env={"PYTHONPATH": str(_TEAM_DIR), "PYTHONHASHSEED": "0",
                   "PGAI_AGENT_KANBAN_TEMP_DIR": str(box.root / "tmp")},
    )

    def render() -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "pgai_agent_kanban.cm.changelog_writer", str(repo), str(_bugs_dir(box))],
            capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=60,
            stdin=subprocess.DEVNULL,
        )

    first = render()  # assigns the missing Public IDs
    before = render()
    _archive_now(box)
    after = render()

    assert first.returncode == before.returncode == after.returncode == 0, _say(after)
    assert after.stdout == before.stdout, _say(after)


def test_archived_bug_without_public_id_gets_the_id_a_live_run_assigns(tmp_path: pathlib.Path) -> None:
    live_box = _ledger_sandbox(tmp_path / "live")
    archived_box = _ledger_sandbox(tmp_path / "archived")
    _archive_now(archived_box)
    repo = _repo(tmp_path)

    live_render = _regenerate(repo, live_box)
    archived_render = _regenerate(repo, archived_box)

    assert archived_render == live_render
    archived = _archived_bugs_dir(archived_box) / "BUG-0003-queue-marker-drift.md"
    assert parse_bug_file(archived).public_id == "KI-1.1.0.1", "the ID is written back in place in the archive"
    assert parse_bug_file(_bugs_dir(live_box) / "BUG-0003-queue-marker-drift.md").public_id == "KI-1.1.0.1"
    assert not list(_bugs_dir(archived_box).glob("BUG-0003-*.md")), "no live copy may be created"


def test_live_bug_file_wins_over_an_archived_one_with_the_same_number(tmp_path: pathlib.Path) -> None:
    box = _ledger_sandbox(tmp_path)
    repo = _repo(tmp_path)
    archive_dir = _archived_bugs_dir(box)
    archive_dir.mkdir(parents=True)
    stale = archive_dir / "BUG-0005-help-typo.md"
    stale.write_text(_bug_text(stale.stem, "done", "A stale archived title.", fixed_in="v1.2.0"), encoding="utf-8")

    render = _regenerate(repo, box)

    assert "The help text misspelled a flag." in render
    assert "A stale archived title." not in render


def test_load_bugs_reads_the_archive_only_when_asked(tmp_path: pathlib.Path) -> None:
    box = _ledger_sandbox(tmp_path)
    _archive_now(box)

    live_only = [b.bug_id for b in load_bugs(_bugs_dir(box))]
    with_archive = [b.bug_id for b in load_bugs(_bugs_dir(box), include_archive=True)]

    assert live_only == ["BUG-0005", "BUG-0006"]
    assert with_archive == [f"BUG-{n:04d}" for n in range(1, 7)], "archived bugs load in ledger order"


def test_bugs_dir_outside_a_live_project_reads_no_archive(tmp_path: pathlib.Path) -> None:
    box = _ledger_sandbox(tmp_path)
    _archive_now(box)
    loose = tmp_path / "loose" / "bugs"
    loose.mkdir(parents=True)
    for path in _bugs_dir(box).glob("BUG-*.md"):
        (loose / path.name).write_bytes(path.read_bytes())

    assert archived_bug_paths(loose) == []
    assert [b.bug_id for b in load_bugs(loose, include_archive=True)] == ["BUG-0005", "BUG-0006"]
