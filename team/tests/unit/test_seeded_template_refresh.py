"""
test_seeded_template_refresh.py — the seeded intake-template refresh
(pgai_agent_kanban.ops.seeded_templates) and its install.sh / upgrade.sh
wiring.

WHAT IS TESTED
--------------
1. **The install route.** ``install.sh --force`` over a sandbox root whose
   registered release-workflow project carries an outdated BUG-TEMPLATE.md
   (and outdated PRIORITY / REQUIREMENTS copies) leaves all three
   byte-identical to the installed ``templates/project/release/`` files.
2. **The upgrade route.** ``team/scripts/upgrade.sh`` phase 2 does the same.
3. **The differing-copy policy.** A copy that differs from the installed
   template -- an operator edit or an older release's template, which the
   refresh cannot tell apart -- is kept beside itself as
   ``<file>.<UTC timestamp>.bak`` with its old bytes, then overwritten; one
   line names the project and the file.
4. **Idempotence.** A re-run over current copies writes nothing (bytes and
   mtimes unchanged, no new backups) and prints one no-op line.
5. **Scope.** Only the ``_TEMPLATE_COPIES`` targets are written; every other
   project file is byte-identical afterwards. The project README
   (``projects/<name>/README.md``, seeded once by create/add) is never
   written: an edited one stays byte-unchanged and an absent one stays absent.
6. **Per-workflow source and self-containment.** Each project is refreshed
   from its own workflow type's template set, the one ``create_project``
   seeds from, read from the tree the module is installed in -- never from
   wherever the caller happens to be.
7. **Skips and dry run.** A registered project with no directory is skipped
   with a stderr line while the others are still refreshed; ``--dry-run``
   writes nothing; no registered projects is a single no-op line; a
   colon-legacy registry is read too.

SANDBOX
-------
Every kanban root is a throwaway directory under pytest's ``tmp_path``
(redirected under ``$PGAI_AGENT_KANBAN_TEMP_DIR/tests`` by conftest.py).
Subprocesses get an environment built by ``build_scrubbed_env`` with all
five live-install names (``PGAI_AGENT_KANBAN_ROOT_PATH``, ``KANBAN_ROOT``,
``PGAI_AGENT_KANBAN_TEMP_DIR`` set to the sandbox; ``PGAI_TASKS_DIR`` and
``PGAI_PROJECT_ROOT`` absent) so nothing reads or writes the live install.
"""

from __future__ import annotations

import datetime
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from pgai_agent_kanban.ops import projects as ops_projects  # noqa: E402
from pgai_agent_kanban.ops import seeded_templates  # noqa: E402
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env  # noqa: E402

_REPO_ROOT = _TEAM_DIR.parent
_INSTALL_SH = _REPO_ROOT / "install.sh"
_UPGRADE_SH = _TEAM_DIR / "scripts" / "upgrade.sh"
_SCRIPTS = _TEAM_DIR / "scripts"
_TEMPLATES = _TEAM_DIR / "templates" / "project"
_TIMEOUT = 180

_TARGETS = [target for _source, target in ops_projects._TEMPLATE_COPIES]
_OUTDATED = {
    "bugs/templates/BUG-TEMPLATE.md": "# Bug template from an older release\n\n## Symptom\n\n## Expected\n",
    "priority/templates/PRIORITY-TEMPLATE.md": "# Priority template from an older release\n",
    "requirements/templates/REQUIREMENTS-TEMPLATE.md": "# Requirements template from an older release\n",
}
_FIXED_NOW = datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.timezone.utc)
_FIXED_STAMP = "20260102T030405Z"


# ---------------------------------------------------------------------------
# Scaffolding
# ---------------------------------------------------------------------------


def _write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _project(root: pathlib.Path, name: str, workflow: str = "release", files: dict | None = None) -> pathlib.Path:
    """A project directory with a project.cfg naming *workflow*, plus *files*."""
    project_dir = root / "projects" / name
    _write(project_dir / "project.cfg", f"[project]\nname = {name}\nworkflow_type = {workflow}\n")
    for rel, text in (files or {}).items():
        _write(project_dir / rel, text)
    return project_dir


def _register(root: pathlib.Path, *names: str) -> None:
    body = "".join(f"[project:{name}]\npriority={index}\n\n" for index, name in enumerate(names, start=1))
    _write(root / "projects.cfg", body)


def _snapshot(directory: pathlib.Path) -> dict[str, bytes]:
    """Relative path -> bytes for every file under *directory*."""
    return {
        str(path.relative_to(directory)): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def _backups(directory: pathlib.Path) -> list[pathlib.Path]:
    return sorted(directory.rglob("*.bak"))


def _sandbox_env(sandbox: pathlib.Path, root: pathlib.Path) -> dict:
    """A subprocess environment pointing every live-install name at the sandbox."""
    home = sandbox / "home"
    temp = sandbox / "tmp"
    home.mkdir(parents=True, exist_ok=True)
    temp.mkdir(parents=True, exist_ok=True)
    return build_scrubbed_env(
        home_dir=home,
        extra_path_dirs=[pathlib.Path(sys.executable).parent],
        extra_env={
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(temp),
        },
    )


def _run_install(sandbox: pathlib.Path, root: pathlib.Path) -> subprocess.CompletedProcess:
    """install.sh --force over *root*: non-interactive, no host crontab, no git."""
    return subprocess.run(
        ["bash", str(_INSTALL_SH), "--force", "--no-system-cron", "--stamp-version", "v0.0.0-sandbox-test"],
        capture_output=True, text=True, timeout=_TIMEOUT, env=_sandbox_env(sandbox, root), cwd=str(sandbox),
    )


def _run_upgrade(sandbox: pathlib.Path, root: pathlib.Path) -> subprocess.CompletedProcess:
    """upgrade.sh's phase-2 body against *root* (DEV_TREE is this checkout)."""
    return subprocess.run(
        [
            "bash", str(_UPGRADE_SH), "--phase2", "--phase2-protocol", "1", "--kanban-root", str(root),
            "--force", "--stamp-version", "v0.0.1-sandbox-test",
        ],
        capture_output=True, text=True, timeout=_TIMEOUT, env=_sandbox_env(sandbox, root), cwd=str(sandbox),
    )


def _output(run: subprocess.CompletedProcess) -> str:
    return f"{run.stdout}\n{run.stderr}"


def _assert_matches_installed(root: pathlib.Path, project: str, workflow: str = "release") -> None:
    installed = root / "templates" / "project" / workflow
    project_dir = root / "projects" / project
    for source, target in ops_projects._TEMPLATE_COPIES:
        assert (project_dir / target).read_bytes() == (installed / source).read_bytes(), target


# ---------------------------------------------------------------------------
# 1-2. The install and upgrade routes
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _INSTALL_SH.exists(), reason=f"install.sh not found at {_INSTALL_SH}")
def test_install_over_an_existing_root_refreshes_outdated_seeded_templates(tmp_path):
    root = tmp_path / "root"
    _project(root, "alpha", files=dict(_OUTDATED, **{"bugs/BUG-0001-example.md": "# a filed bug\n"}))
    _register(root, "alpha")
    other_files_before = {
        rel: data for rel, data in _snapshot(root / "projects" / "alpha").items() if rel not in _TARGETS
    }
    cfg_before = (root / "projects.cfg").read_bytes()

    run = _run_install(tmp_path, root)

    assert run.returncode == 0, _output(run)
    _assert_matches_installed(root, "alpha")
    after = _snapshot(root / "projects" / "alpha")
    for rel, data in other_files_before.items():
        assert after[rel] == data, f"{rel} changed"
    assert (root / "projects.cfg").read_bytes() == cfg_before
    assert "Seeded templates: project 'alpha': refreshed bugs/templates/BUG-TEMPLATE.md" in run.stdout


@pytest.mark.skipif(not _UPGRADE_SH.exists(), reason=f"upgrade.sh not found at {_UPGRADE_SH}")
def test_upgrade_refreshes_outdated_seeded_templates(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    _project(root, "alpha", files=_OUTDATED)
    _register(root, "alpha")

    run = _run_upgrade(tmp_path, root)

    assert run.returncode == 0, _output(run)
    _assert_matches_installed(root, "alpha")
    backups = _backups(root / "projects" / "alpha")
    assert len(backups) == 3
    assert "Seeded templates: project 'alpha': refreshed bugs/templates/BUG-TEMPLATE.md" in run.stdout


@pytest.mark.skipif(not _UPGRADE_SH.exists(), reason=f"upgrade.sh not found at {_UPGRADE_SH}")
def test_upgrade_over_current_templates_writes_nothing_and_prints_one_no_op_line(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    _project(root, "alpha", files=_OUTDATED)
    _register(root, "alpha")
    first = _run_upgrade(tmp_path, root)
    assert first.returncode == 0, _output(first)
    before = _snapshot(root / "projects" / "alpha")
    backups_before = _backups(root / "projects" / "alpha")

    second = _run_upgrade(tmp_path, root)

    assert second.returncode == 0, _output(second)
    assert _snapshot(root / "projects" / "alpha") == before
    assert _backups(root / "projects" / "alpha") == backups_before
    lines = [line for line in _output(second).splitlines() if "Seeded templates:" in line]
    assert lines == [
        "Seeded templates: all copies in 1 registered project(s) already match the installed "
        "templates; nothing changed."
    ]


# ---------------------------------------------------------------------------
# 3. The differing-copy policy: back up, then overwrite, narrated
# ---------------------------------------------------------------------------


def test_an_operator_modified_copy_is_backed_up_then_overwritten_with_one_line(tmp_path, capsys):
    root = tmp_path / "root"
    edited = "# Our team's bug template\n\n## Customer impact\n"
    current = {
        target: (_TEMPLATES / "release" / source).read_text(encoding="utf-8")
        for source, target in ops_projects._TEMPLATE_COPIES
    }
    current["bugs/templates/BUG-TEMPLATE.md"] = edited
    project_dir = _project(root, "alpha", files=current)
    _register(root, "alpha")

    result = seeded_templates.refresh_seeded_templates(root, scripts_dir=_SCRIPTS, now=_FIXED_NOW)

    target = project_dir / "bugs/templates/BUG-TEMPLATE.md"
    backup = project_dir / f"bugs/templates/BUG-TEMPLATE.md.{_FIXED_STAMP}.bak"
    assert target.read_bytes() == (_TEMPLATES / "release" / "BUG-TEMPLATE.md").read_bytes()
    assert backup.read_text(encoding="utf-8") == edited
    assert result.refreshed == [("alpha", "bugs/templates/BUG-TEMPLATE.md", backup.relative_to(project_dir).as_posix())]
    out = capsys.readouterr().out
    narration = [line for line in out.splitlines() if "bugs/templates/BUG-TEMPLATE.md" in line]
    assert len(narration) == 1
    assert "project 'alpha'" in narration[0] and backup.name in narration[0]


def test_a_second_backup_in_the_same_second_does_not_overwrite_the_first(tmp_path):
    root = tmp_path / "root"
    project_dir = _project(root, "alpha", files={"bugs/templates/BUG-TEMPLATE.md": "first edit\n"})
    _register(root, "alpha")
    seeded_templates.refresh_seeded_templates(root, scripts_dir=_SCRIPTS, now=_FIXED_NOW)
    (project_dir / "bugs/templates/BUG-TEMPLATE.md").write_text("second edit\n", encoding="utf-8")

    seeded_templates.refresh_seeded_templates(root, scripts_dir=_SCRIPTS, now=_FIXED_NOW)

    first = project_dir / f"bugs/templates/BUG-TEMPLATE.md.{_FIXED_STAMP}.bak"
    second = project_dir / f"bugs/templates/BUG-TEMPLATE.md.{_FIXED_STAMP}.1.bak"
    assert first.read_text(encoding="utf-8") == "first edit\n"
    assert second.read_text(encoding="utf-8") == "second edit\n"


# ---------------------------------------------------------------------------
# 4-5. Idempotence and scope
# ---------------------------------------------------------------------------


def test_a_rerun_over_current_copies_changes_nothing(tmp_path, capsys):
    root = tmp_path / "root"
    project_dir = _project(root, "alpha", files=_OUTDATED)
    _register(root, "alpha")
    seeded_templates.refresh_seeded_templates(root, scripts_dir=_SCRIPTS)
    before = _snapshot(project_dir)
    mtimes = {target: (project_dir / target).stat().st_mtime_ns for target in _TARGETS}
    capsys.readouterr()

    result = seeded_templates.refresh_seeded_templates(root, scripts_dir=_SCRIPTS)

    assert not result.changed and not result.skipped
    assert _snapshot(project_dir) == before
    assert {target: (project_dir / target).stat().st_mtime_ns for target in _TARGETS} == mtimes
    assert capsys.readouterr().out.splitlines() == [
        "Seeded templates: all copies in 1 registered project(s) already match the installed "
        "templates; nothing changed."
    ]


def test_only_the_seeded_template_copies_are_written(tmp_path):
    root = tmp_path / "root"
    others = {
        "bugs/BUG-0001-example.md": "# bug\n",
        "bugs/README.md": "old bugs readme\n",
        "priority/README.md": "old priority readme\n",
        "requirements/v1.0.0-example.md": "# req\n",
        "tasks/queues/coder_backlog.md": "# Coder Backlog\n",
        "release-state.md": "## Active RC\nnone\n",
        "INTENT.md": "intent\n",
    }
    project_dir = _project(root, "alpha", files=others)
    _register(root, "alpha")
    before = _snapshot(project_dir)
    cfg = (root / "projects.cfg").read_bytes()

    seeded_templates.refresh_seeded_templates(root, scripts_dir=_SCRIPTS)

    after = _snapshot(project_dir)
    assert set(after) - set(before) == set(_TARGETS)
    for rel, data in before.items():
        assert after[rel] == data, rel
    assert (root / "projects.cfg").read_bytes() == cfg


@pytest.mark.parametrize("dry_run", [False, True], ids=["write", "dry-run"])
def test_the_project_readme_is_never_refreshed_or_seeded(tmp_path, dry_run):
    root = tmp_path / "root"
    edited = "# Project: alpha\n\nOur own runners and contracts.\n"
    alpha = _project(root, "alpha", files={**_OUTDATED, "README.md": edited})
    beta = _project(root, "beta", files=_OUTDATED)
    _register(root, "alpha", "beta")
    mtime = (alpha / "README.md").stat().st_mtime_ns

    result = seeded_templates.refresh_seeded_templates(root, scripts_dir=_SCRIPTS, dry_run=dry_run)

    assert result.changed
    assert (alpha / "README.md").read_text(encoding="utf-8") == edited
    assert (alpha / "README.md").stat().st_mtime_ns == mtime
    assert not list(alpha.glob("README.md.*"))
    assert not (beta / "README.md").exists()
    assert all(target != "README.md" for _name, target in result.seeded)


# ---------------------------------------------------------------------------
# 6. Per-workflow source, read from the module's own tree
# ---------------------------------------------------------------------------


def test_each_project_is_refreshed_from_its_own_workflow_templates(tmp_path):
    root = tmp_path / "root"
    _project(root, "rel", workflow="release", files=_OUTDATED)
    _project(root, "doc", workflow="document", files=_OUTDATED)
    _project(root, "odd", workflow="no-such-workflow", files=_OUTDATED)
    _register(root, "rel", "doc", "odd")

    seeded_templates.refresh_seeded_templates(root, scripts_dir=_SCRIPTS)

    for name, workflow in (("rel", "release"), ("doc", "document"), ("odd", "release")):
        for source, target in ops_projects._TEMPLATE_COPIES:
            got = (root / "projects" / name / target).read_bytes()
            assert got == (_TEMPLATES / workflow / source).read_bytes(), (name, target)


def test_the_cli_reads_templates_from_the_tree_it_is_installed_in(tmp_path):
    installed = tmp_path / "installed"
    shutil.copytree(_TEAM_DIR / "pgai_agent_kanban", installed / "pgai_agent_kanban",
                    ignore=shutil.ignore_patterns("__pycache__", "tests"))
    (installed / "scripts" / "lib").mkdir(parents=True)
    shutil.copyfile(_SCRIPTS / "lib" / "pp_run_ops.sh", installed / "scripts" / "lib" / "pp_run_ops.sh")
    shutil.copytree(_TEMPLATES, installed / "templates" / "project")
    marker = "# The installed release's bug template\n"
    (installed / "templates" / "project" / "release" / "BUG-TEMPLATE.md").write_text(marker, encoding="utf-8")
    _project(installed, "alpha", files=_OUTDATED)
    _register(installed, "alpha")
    env = _sandbox_env(tmp_path, installed)

    run = subprocess.run(
        ["bash", str(installed / "scripts" / "lib" / "pp_run_ops.sh"), "pgai_agent_kanban.ops",
         "refresh_seeded_templates", f"--kanban-root={installed}"],
        capture_output=True, text=True, timeout=60, env=env, cwd=str(tmp_path),
    )

    assert run.returncode == 0, _output(run)
    assert (installed / "projects/alpha/bugs/templates/BUG-TEMPLATE.md").read_text(encoding="utf-8") == marker


# ---------------------------------------------------------------------------
# 7. Skips, dry run, empty and colon-legacy registries
# ---------------------------------------------------------------------------


def test_a_registered_project_without_a_directory_is_skipped_and_the_rest_still_refresh(tmp_path, capsys):
    root = tmp_path / "root"
    _project(root, "alpha", files=_OUTDATED)
    _register(root, "ghost", "alpha")

    code = seeded_templates._cli_main(
        ["refresh_seeded_templates", f"--kanban-root={root}", f"--scripts-dir={_SCRIPTS}"]
    )

    assert code == 1
    for source, target in ops_projects._TEMPLATE_COPIES:
        assert (root / "projects/alpha" / target).read_bytes() == (_TEMPLATES / "release" / source).read_bytes()
    assert not (root / "projects" / "ghost").exists()
    assert "project 'ghost': skipped; project directory" in capsys.readouterr().err


def test_dry_run_reports_and_writes_nothing(tmp_path, capsys):
    root = tmp_path / "root"
    project_dir = _project(root, "alpha", files={"bugs/templates/BUG-TEMPLATE.md": "old\n"})
    _register(root, "alpha")
    before = _snapshot(project_dir)

    result = seeded_templates.refresh_seeded_templates(root, scripts_dir=_SCRIPTS, dry_run=True)

    assert result.changed and result.dry_run
    assert _snapshot(project_dir) == before
    out = capsys.readouterr().out
    assert "would refresh bugs/templates/BUG-TEMPLATE.md" in out
    assert "would seed missing priority/templates/PRIORITY-TEMPLATE.md" in out


def test_no_registered_projects_is_a_single_no_op_line(tmp_path, capsys):
    root = tmp_path / "root"
    root.mkdir()

    result = seeded_templates.refresh_seeded_templates(root, scripts_dir=_SCRIPTS)

    assert not result.changed and not result.projects
    assert capsys.readouterr().out.splitlines() == ["Seeded templates: no registered projects; nothing to refresh."]


def test_a_colon_legacy_registry_is_refreshed_too(tmp_path):
    root = tmp_path / "root"
    _project(root, "alpha", files=_OUTDATED)
    _write(root / "projects.cfg", "# legacy header\nalpha:1:#378ADD\n")

    seeded_templates.refresh_seeded_templates(root, scripts_dir=_SCRIPTS)

    for source, target in ops_projects._TEMPLATE_COPIES:
        assert (root / "projects/alpha" / target).read_bytes() == (_TEMPLATES / "release" / source).read_bytes()


def test_the_ops_entry_point_routes_the_refresh_verb(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    env = _sandbox_env(tmp_path, root)
    env["PYTHONPATH"] = str(_TEAM_DIR)

    run = subprocess.run(
        [sys.executable, "-m", "pgai_agent_kanban.ops", "refresh_seeded_templates", f"--kanban-root={root}"],
        capture_output=True, text=True, timeout=60, env=env, cwd=str(root),
    )

    assert run.returncode == 0, _output(run)
    assert run.stdout.strip() == "Seeded templates: no registered projects; nothing to refresh."
