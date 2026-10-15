"""
test_export_import_archive.py
=============================
``export-project.sh`` carries a project's archive -- ``archive/<project>/``,
``ARCHIVE-LEDGER.tsv`` included -- inside the export, and
``import-project.sh`` puts it back at ``archive/<final-name>/`` on the
importing kanban root, byte for byte.

Covered, against the real scripts run as subprocesses from a foreign cwd,
the source project built and archived in a sandbox kanban root
(``tests/fixtures/archive_sandbox.py``'s acceptance scenario, archived
through the real ``archive.plan`` / ``archive.apply``) and imported into a
second, empty sandbox root:

  * the export holds ``<project>/ARCHIVE/`` with the ledger and every
    archived file, byte-identical, and leaves the exporting root untouched;
  * a round trip restores ``archive/<project>/`` byte for byte, leaves no
    ``ARCHIVE/`` inside the imported project, and leaves the live project
    tree as exported (MANIFEST and the blanked ``project.cfg`` aside);
  * ``--name`` places the archive under the new name, ledger rows unchanged;
  * an existing ``archive/<project>/`` is refused without ``--force``
    (``import.archive_destination_exists``) and nothing on the importing
    root changes; with ``--force`` it is moved aside, not deleted;
  * a final name outside the project-name shape is refused when the export
    carries an archive (``import.invalid_archive_project_name``), with a
    remedy naming a sanitised ``--name``, and nothing changes;
  * a final name that is not one plain project name -- ``--name ../../x``
    -- is refused (``import.invalid_project_name``) whether or not the
    export carries an archive, and nothing changes inside or outside the
    importing root;
  * a project with no archive exports no ``ARCHIVE/`` and imports none;
  * an import under ``--name`` (with or without ``--force``) leaves the
    original-name project byte-identical, mtimes included, and creates the
    new one; ``--force`` backs up only an existing final-name directory, and
    without it a taken final name is refused (``import.destination_exists``);
  * a failed extraction -- a truncated archive, or one ``tar`` lists but
    cannot extract -- leaves no staging directory and changes nothing.

Nothing here reads or writes the live install: every script runs with an
environment built from an explicit set (``build_scrubbed_env``) whose every
kanban root names a sandbox.
"""

from __future__ import annotations

import io
import os
import pathlib
import re
import subprocess
import sys
import tarfile

import pytest

from pgai_agent_kanban.archive import RESULT_OK, apply, plan
from pgai_agent_kanban.archive.layout import LEDGER_FILE_NAME, archive_layout
from pgai_agent_kanban.archive.ledger import read_rows
from tests.fixtures.archive_sandbox import ArchiveSandbox, build_acceptance_scenario
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_EXPORT_SH = _TEAM_DIR / "scripts" / "export-project.sh"
_IMPORT_SH = _TEAM_DIR / "scripts" / "import-project.sh"

_EMBEDDED = "ARCHIVE"
_IMPORT_ADDED = {"MANIFEST"}  # written into the imported project by the export
_IMPORT_REWRITTEN = {"project.cfg"}  # env-specific fields blanked on import


# ---------------------------------------------------------------------------
# Sandboxes
# ---------------------------------------------------------------------------


def _register(root: pathlib.Path, project: str) -> None:
    """Register *project* in *root*'s projects.cfg and give the root a VERSION."""
    (root / "projects.cfg").write_text(
        f"[project:{project}]\npriority = 1\n", encoding="utf-8"
    )
    (root / "VERSION").write_text("v9.9.9\n", encoding="utf-8")


def _archived_source(tmp_path: pathlib.Path) -> ArchiveSandbox:
    """The acceptance scenario, registered and archived; its archive holds a ledger."""
    box = build_acceptance_scenario(tmp_path / "source").sandbox
    _register(box.root, box.project)
    result = apply(plan(box.project, kanban_root=box.root), log=lambda _line: None)
    assert result.status == RESULT_OK, result.error
    assert result.moved_ids, "the archive run moved nothing; the round trip would prove nothing"
    ledger = archive_layout(box.root, box.project).ledger
    assert read_rows(ledger), f"the archive run wrote no ledger rows to {ledger}"
    return box


def _target_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """An empty kanban root to import into: a projects/ directory, a VERSION, nothing else."""
    root = tmp_path / "target" / "kanban"
    (root / "projects").mkdir(parents=True)
    (root / "VERSION").write_text("v9.9.9\n", encoding="utf-8")
    return root


def _tree(directory: pathlib.Path) -> dict[str, object]:
    """Every entry under *directory*, relative: ``"dir"`` for a directory, the bytes for a file."""
    state: dict[str, object] = {}
    for dirpath, dirnames, filenames in os.walk(directory):
        base = pathlib.Path(dirpath)
        for name in dirnames:
            state[(base / name).relative_to(directory).as_posix()] = "dir"
        for name in filenames:
            path = base / name
            state[path.relative_to(directory).as_posix()] = path.read_bytes()
    return state


# ---------------------------------------------------------------------------
# Running the real scripts
# ---------------------------------------------------------------------------


def _run(script: pathlib.Path, root: pathlib.Path, tmp_path: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    scratch = tmp_path / "scratch"
    scratch.mkdir(exist_ok=True)
    foreign_cwd = tmp_path / "cwd"
    foreign_cwd.mkdir(exist_ok=True)
    env = build_scrubbed_env(
        home_dir=home,
        extra_path_dirs=[pathlib.Path(sys.executable).parent],
        extra_env={
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(scratch),
        },
    )
    return subprocess.run(
        [str(script), *args], capture_output=True, text=True, env=env, cwd=str(foreign_cwd),
        timeout=120, stdin=subprocess.DEVNULL,
    )


def _say(result: subprocess.CompletedProcess) -> str:
    return f"exit {result.returncode}\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _export(box: ArchiveSandbox, tmp_path: pathlib.Path) -> pathlib.Path:
    out = tmp_path / "exports" / f"{box.project}.tar.gz"
    out.parent.mkdir(exist_ok=True)
    result = _run(_EXPORT_SH, box.root, tmp_path, "--project", box.project, "--out", str(out))
    assert result.returncode == 0, _say(result)
    assert out.is_file(), f"export-project.sh reported success but wrote no {out}\n{_say(result)}"
    return out


def _import(root: pathlib.Path, export: pathlib.Path, tmp_path: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    return _run(_IMPORT_SH, root, tmp_path, "--archive", str(export), *args)


def _error_key(result: subprocess.CompletedProcess) -> str:
    match = re.search(r"^error:\s+(\S+)\s*$", result.stderr, re.MULTILINE)
    return match.group(1) if match else ""


def _remedy(result: subprocess.CompletedProcess) -> str:
    match = re.search(r"^remedy:\s+(.+?)\s*$", result.stderr, re.MULTILINE)
    return match.group(1) if match else ""


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def test_export_carries_the_archive_and_ledger_and_leaves_the_source_untouched(tmp_path):
    box = _archived_source(tmp_path)
    archive_dir = archive_layout(box.root, box.project).root
    before = _tree(box.root)

    export = _export(box, tmp_path)

    assert _tree(box.root) == before, "export-project.sh changed the exporting kanban root"
    embedded = f"{box.project}/{_EMBEDDED}"
    with tarfile.open(export, "r:gz") as tar:
        members = {m.name: m for m in tar.getmembers()}
        assert f"{embedded}/{LEDGER_FILE_NAME}" in members, (
            f"the export carries no {embedded}/{LEDGER_FILE_NAME}; members: {sorted(members)[:20]}"
        )
        for relative, content in _tree(archive_dir).items():
            member = members.get(f"{embedded}/{relative}")
            assert member is not None, f"archive/{box.project}/{relative} is missing from the export"
            if content != "dir":
                assert tar.extractfile(member).read() == content, (
                    f"archive/{box.project}/{relative} differs inside the export"
                )


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


def test_round_trip_restores_the_archive_and_ledger_byte_for_byte(tmp_path):
    box = _archived_source(tmp_path)
    source_archive = _tree(archive_layout(box.root, box.project).root)
    source_project = _tree(box.project_root)
    export = _export(box, tmp_path)
    target = _target_root(tmp_path)

    result = _import(target, export, tmp_path)

    assert result.returncode == 0, _say(result)
    imported_archive = archive_layout(target, box.project).root
    assert _tree(imported_archive) == source_archive, (
        f"archive/{box.project}/ did not survive the round trip byte for byte\n{_say(result)}"
    )
    imported_project = target / "projects" / box.project
    assert not (imported_project / _EMBEDDED).exists(), "the embedded ARCHIVE/ was left inside the imported project"
    ignored = _IMPORT_ADDED | _IMPORT_REWRITTEN
    assert {k: v for k, v in _tree(imported_project).items() if k not in ignored} == {
        k: v for k, v in source_project.items() if k not in ignored
    }, "the live project tree changed in the round trip"
    assert str(imported_archive) in result.stdout, f"the import did not report where the archive went\n{_say(result)}"


def test_import_under_a_new_name_places_the_archive_under_that_name(tmp_path):
    box = _archived_source(tmp_path)
    source_archive = _tree(archive_layout(box.root, box.project).root)
    export = _export(box, tmp_path)
    target = _target_root(tmp_path)

    result = _import(target, export, tmp_path, "--name", "renamed-demo")

    assert result.returncode == 0, _say(result)
    assert _tree(archive_layout(target, "renamed-demo").root) == source_archive, (
        "archive/renamed-demo/ is not the exported archive byte for byte (ledger rows included)"
    )
    assert not (target / "archive" / box.project).exists(), "the archive was also placed under the exported name"


def test_import_refuses_to_overwrite_an_existing_archive_without_force(tmp_path):
    box = _archived_source(tmp_path)
    export = _export(box, tmp_path)
    target = _target_root(tmp_path)
    existing = archive_layout(target, box.project).root
    existing.mkdir(parents=True)
    (existing / LEDGER_FILE_NAME).write_text("existing ledger\n", encoding="utf-8")
    before = _tree(target)

    result = _import(target, export, tmp_path)

    assert result.returncode == 4, _say(result)
    assert _error_key(result) == "import.archive_destination_exists", _say(result)
    assert "--force" in _remedy(result), _say(result)
    assert _tree(target) == before, f"a refused import changed the importing kanban root\n{_say(result)}"


def test_import_with_force_moves_the_existing_archive_aside(tmp_path):
    box = _archived_source(tmp_path)
    source_archive = _tree(archive_layout(box.root, box.project).root)
    export = _export(box, tmp_path)
    target = _target_root(tmp_path)
    existing = archive_layout(target, box.project).root
    existing.mkdir(parents=True)
    (existing / LEDGER_FILE_NAME).write_text("existing ledger\n", encoding="utf-8")

    result = _import(target, export, tmp_path, "--force")

    assert result.returncode == 0, _say(result)
    assert _tree(existing) == source_archive, f"the imported archive is not in place\n{_say(result)}"
    backups = sorted((target / "archive").glob(f"{box.project}.bak-*"))
    assert len(backups) == 1, f"expected one backup of the previous archive; found {backups}\n{_say(result)}"
    assert (backups[0] / LEDGER_FILE_NAME).read_text(encoding="utf-8") == "existing ledger\n"


def test_import_refuses_a_final_name_that_cannot_name_the_archive(tmp_path):
    box = _archived_source(tmp_path)
    export = _export(box, tmp_path)
    target = _target_root(tmp_path)
    before = _tree(target)

    result = _import(target, export, tmp_path, "--name", "Bad_Name")

    assert result.returncode == 1, _say(result)
    assert _error_key(result) == "import.invalid_archive_project_name", _say(result)
    assert "--name bad-name" in _remedy(result), _say(result)
    assert _tree(target) == before, f"a refused import changed the importing kanban root\n{_say(result)}"


def _plain_export(tmp_path: pathlib.Path) -> pathlib.Path:
    """An export of a project with no archive (no embedded ARCHIVE/)."""
    box = ArchiveSandbox(tmp_path / "source")
    _register(box.root, box.project)
    box.add_bug(1, "done")
    return _export(box, tmp_path)


@pytest.mark.parametrize("carries_archive", [True, False], ids=["with-archive", "without-archive"])
def test_import_refuses_a_path_shaped_final_name(tmp_path, carries_archive):
    export = _export(_archived_source(tmp_path), tmp_path) if carries_archive else _plain_export(tmp_path)
    target = _target_root(tmp_path)
    outside = target.parent
    before, before_outside = _tree(target), sorted(p.name for p in outside.iterdir())

    result = _import(target, export, tmp_path, "--name", "../../x")

    assert result.returncode == 1, _say(result)
    assert _error_key(result) == "import.invalid_project_name", _say(result)
    assert "--name x" in _remedy(result), _say(result)
    assert _tree(target) == before, f"a refused import changed the importing kanban root\n{_say(result)}"
    assert sorted(p.name for p in outside.iterdir()) == before_outside, "a refused import wrote beside the root"
    assert not (tmp_path / "x").exists()


def test_a_project_without_an_archive_exports_and_imports_none(tmp_path):
    box = ArchiveSandbox(tmp_path / "source")
    _register(box.root, box.project)
    box.add_bug(1, "done")
    export = _export(box, tmp_path)
    with tarfile.open(export, "r:gz") as tar:
        embedded = [m.name for m in tar.getmembers() if m.name.startswith(f"{box.project}/{_EMBEDDED}")]
    assert embedded == [], f"an export of a project with no archive carries {embedded}"
    target = _target_root(tmp_path)

    result = _import(target, export, tmp_path)

    assert result.returncode == 0, _say(result)
    assert not (target / "archive").exists(), f"an import with no archive created archive/\n{_say(result)}"


# ---------------------------------------------------------------------------
# Import under a new name: staging extraction, final-name collision only
# ---------------------------------------------------------------------------

_STAGING_PREFIX = ".import-staging-"


def _alpha_root(tmp_path: pathlib.Path) -> ArchiveSandbox:
    """A sandbox root holding a registered ``projects/alpha`` with one bug."""
    box = ArchiveSandbox(tmp_path / "rename", project="alpha")
    _register(box.root, "alpha")
    box.add_bug(1, "done")
    return box


def _state(directory: pathlib.Path) -> dict[str, object]:
    """Like ``_tree`` but a file also carries its mtime, so a move-and-restore shows up."""
    state: dict[str, object] = {}
    for dirpath, dirnames, filenames in os.walk(directory):
        base = pathlib.Path(dirpath)
        for name in dirnames:
            state[(base / name).relative_to(directory).as_posix()] = "dir"
        for name in filenames:
            path = base / name
            state[path.relative_to(directory).as_posix()] = (path.read_bytes(), path.stat().st_mtime_ns)
    return state


def _staging_left(root: pathlib.Path) -> list[str]:
    return sorted(p.name for p in (root / "projects").iterdir() if p.name.startswith(_STAGING_PREFIX))


@pytest.mark.parametrize("force", [False, True], ids=["without-force", "with-force"])
def test_import_under_a_new_name_leaves_the_original_project_untouched(tmp_path, force):
    box = _alpha_root(tmp_path)
    export = _export(box, tmp_path)
    original = _state(box.project_root)
    projects_cfg = (box.root / "projects.cfg").read_bytes()

    result = _import(box.root, export, tmp_path, "--name", "alpha-copy", *(["--force"] if force else []))

    assert result.returncode == 0, _say(result)
    assert _state(box.project_root) == original, f"projects/alpha changed on an import under another name\n{_say(result)}"
    copy = box.root / "projects" / "alpha-copy"
    assert (copy / "MANIFEST").is_file(), f"projects/alpha-copy was not created from the export\n{_say(result)}"
    assert "project_name = alpha-copy" in (copy / "project.cfg").read_text(encoding="utf-8")
    assert sorted((box.root / "projects").glob("alpha.bak-*")) == [], "the original-name project was backed up"
    assert _staging_left(box.root) == [], "the import left its staging directory behind"
    assert (box.root / "projects.cfg").read_bytes() == projects_cfg


def test_import_with_force_backs_up_only_the_final_name(tmp_path):
    box = _alpha_root(tmp_path)
    export = _export(box, tmp_path)
    original = _state(box.project_root)
    existing = box.root / "projects" / "alpha-copy"
    existing.mkdir()
    (existing / "previous.txt").write_text("previous alpha-copy\n", encoding="utf-8")

    result = _import(box.root, export, tmp_path, "--name", "alpha-copy", "--force")

    assert result.returncode == 0, _say(result)
    backups = sorted((box.root / "projects").glob("alpha-copy.bak-*"))
    assert len(backups) == 1, f"expected one backup of projects/alpha-copy; found {backups}\n{_say(result)}"
    assert (backups[0] / "previous.txt").read_text(encoding="utf-8") == "previous alpha-copy\n"
    assert sorted((box.root / "projects").glob("alpha.bak-*")) == [], "the original-name project was backed up"
    assert _state(box.project_root) == original, f"projects/alpha changed\n{_say(result)}"
    assert (existing / "MANIFEST").is_file(), f"the import is not at projects/alpha-copy\n{_say(result)}"
    assert not (existing / "previous.txt").exists(), "the previous alpha-copy content was merged into the import"
    assert str(backups[0]) in result.stdout, f"the import did not report the backup\n{_say(result)}"
    assert _staging_left(box.root) == []


def test_import_refuses_a_taken_final_name_without_force(tmp_path):
    box = _alpha_root(tmp_path)
    export = _export(box, tmp_path)
    (box.root / "projects" / "alpha-copy").mkdir()
    before = _state(box.root)

    result = _import(box.root, export, tmp_path, "--name", "alpha-copy")

    assert result.returncode == 4, _say(result)
    assert _error_key(result) == "import.destination_exists", _say(result)
    remedy = _remedy(result)
    assert "--name alpha-copy" in remedy and "--force" in remedy, _say(result)
    assert _state(box.root) == before, f"a refused import changed the kanban root\n{_say(result)}"


def _truncated(export: pathlib.Path) -> pathlib.Path:
    data = export.read_bytes()
    out = export.with_name("truncated.tar.gz")
    out.write_bytes(data[: len(data) // 2])
    return out


def _unextractable(export: pathlib.Path) -> pathlib.Path:
    """An archive ``tar t`` lists cleanly but ``tar x`` cannot finish: a file
    named ``alpha/x`` followed by an entry that needs ``alpha/x`` to be a directory."""
    out = export.with_name("unextractable.tar.gz")
    with tarfile.open(out, "w:gz") as tar:
        top = tarfile.TarInfo("alpha")
        top.type = tarfile.DIRTYPE
        top.mode = 0o755
        tar.addfile(top)
        for name in ("alpha/x", "alpha/x/y"):
            member = tarfile.TarInfo(name)
            member.size = 3
            tar.addfile(member, io.BytesIO(b"hi\n"))
    return out


@pytest.mark.parametrize("make_bad", [_truncated, _unextractable], ids=["truncated", "unextractable"])
@pytest.mark.parametrize(
    "args",
    [(), ("--force",), ("--name", "alpha-copy", "--force")],
    ids=["same-name", "same-name-force", "new-name-force"],
)
def test_a_failed_extraction_leaves_no_staging_and_every_project_in_place(tmp_path, make_bad, args):
    box = _alpha_root(tmp_path)
    export = _export(box, tmp_path)
    existing = box.root / "projects" / "alpha-copy"
    existing.mkdir()
    (existing / "previous.txt").write_text("previous alpha-copy\n", encoding="utf-8")
    bad = make_bad(export)
    before = _state(box.root)

    result = _import(box.root, bad, tmp_path, *args)

    assert result.returncode != 0, f"a broken archive imported cleanly\n{_say(result)}"
    assert _staging_left(box.root) == [], f"the failed import left its staging directory\n{_say(result)}"
    assert _state(box.root) == before, f"a failed import changed the kanban root\n{_say(result)}"
