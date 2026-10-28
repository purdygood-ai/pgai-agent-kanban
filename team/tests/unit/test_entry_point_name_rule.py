"""
test_entry_point_name_rule.py
=============================
Every entry point that checks a project name gives the registry readers'
answer for it. The rule is ``PROJECTS_CFG_NAME_RE`` in
``scripts/lib/projects_cfg.sh``; the readers are ``projects_cfg_list``
(``scripts/lib/projects.sh``), ``pgai_agent_kanban.lib.ini.projects_registry``
and ``pgai_agent_kanban.ops.projects.registered_project_names``.

For each of ``My_App``, ``my_app``, ``-bad`` and ``my-app`` the readers list
only ``my-app``, and so does each entry point's name check:

  * ``wake-batch.sh`` wakes only ``my-app`` out of a registry holding all
    four (with a ``projects/<name>/`` directory for each), logging every
    other name as not a plain project name;
  * ``wake-batch.sh`` leaves its operator-inbox question only in a project
    directory whose name the rule accepts;
  * ``cm/finalize.sh`` and ``cm/open-doc.sh`` refuse every other name
    before anything else, quoting the rule's own pattern, and pass
    ``my-app`` on to their version check;
  * ``import-project.sh`` refuses every other name as the final project
    name, with or without an embedded archive, and imports ``my-app``;
  * the push sites' name checks -- ``push_decision.resolve_push_decision``,
    ``cm/push-watchdog.sh``'s project-name check and
    ``wake_batch_guard_take`` (``scripts/lib/wake_batch_guard.sh``) --
    refuse every other name by the rule itself, even with a
    ``projects/<name>/`` directory and project.cfg in place, and pass
    ``my-app``.

Every script runs as a subprocess against a sandbox kanban root under
``tmp_path`` with an environment built by ``build_scrubbed_env``; nothing
reads or writes the live install. The CM scripts stop at their argument
checks (an unparseable version), and the push watchdog at a HALT file in
the project directory, so no release state, git or remote operation is
reached.
"""

from __future__ import annotations

import io
import pathlib
import re
import shutil
import subprocess
import sys
import tarfile

import pytest

from pgai_agent_kanban.lib import ini, push_decision
from pgai_agent_kanban.ops import projects as ops_projects
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]  # team/
_SCRIPTS = _TEAM_DIR / "scripts"
_LIB_DIR = _SCRIPTS / "lib"

_NAMES = ("My_App", "my_app", "-bad", "my-app")
_ACCEPTED = {"my-app"}

# The provider wake script wake-batch.sh execs: records the project only.
_STUB_WAKE = """#!/usr/bin/env bash
echo "${PGAI_WAKE_BATCH_PROJECT:-}" >> "$PGAI_AGENT_KANBAN_ROOT_PATH/stub-ran"
exit 0
"""

_LIST_DRIVER = r"""
set -euo pipefail
source "$LIB_DIR/project_paths.sh"
source "$LIB_DIR/projects.sh"
projects_cfg_list
"""


def _say(result: subprocess.CompletedProcess) -> str:
    return f"exit {result.returncode}\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _env(tmp_path: pathlib.Path, root: pathlib.Path, **extra: str) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    (root / "tmp").mkdir(parents=True, exist_ok=True)
    env = build_scrubbed_env(
        home_dir=home,
        extra_path_dirs=[pathlib.Path(sys.executable).parent],
        extra_env={
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
            **extra,
        },
    )
    for name in ("PGAI_TASKS_DIR", "PGAI_PROJECT_ROOT", "PGAI_PROJECT_NAME"):
        env.pop(name, None)
    return env


def _run(cmd: list[str], tmp_path: pathlib.Path, root: pathlib.Path, **extra: str) -> subprocess.CompletedProcess:
    cwd = tmp_path / "cwd"
    cwd.mkdir(exist_ok=True)
    return subprocess.run(
        cmd, capture_output=True, text=True, env=_env(tmp_path, root, **extra),
        cwd=str(cwd), timeout=120, stdin=subprocess.DEVNULL, check=False,
    )


def _registry(root: pathlib.Path, names: tuple[str, ...]) -> None:
    sections = [f"[project:{name}]\npriority={number}\n" for number, name in enumerate(names, start=1)]
    (root / "projects.cfg").write_text("\n".join(sections), encoding="utf-8")


@pytest.fixture(scope="module")
def readers_answer(tmp_path_factory) -> set[str]:
    """The names every registry reader lists out of a registry holding all of ``_NAMES``."""
    tmp_path = tmp_path_factory.mktemp("readers")
    root = tmp_path / "kanban"
    (root / "projects").mkdir(parents=True)
    _registry(root, _NAMES)

    result = _run(["bash", "-c", _LIST_DRIVER], tmp_path, root, LIB_DIR=str(_LIB_DIR))
    assert result.returncode == 0, _say(result)
    bash_listed = set(result.stdout.split())
    ini_listed = {entry.name for entry in ini.projects_registry(root)}
    ops_listed = set(ops_projects.registered_project_names(root))

    assert bash_listed == ini_listed == ops_listed, (bash_listed, ini_listed, ops_listed)
    assert bash_listed == _ACCEPTED, f"the registry readers list {sorted(bash_listed)}"
    return bash_listed


# ---------------------------------------------------------------------------
# wake-batch.sh
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def wake_install(tmp_path_factory) -> pathlib.Path:
    """wake-batch.sh, scripts/lib/, pgai_agent_kanban/ and config/ with a stub provider wake script."""
    install = tmp_path_factory.mktemp("install")
    scripts = install / "scripts"
    (scripts / "wake").mkdir(parents=True)
    shutil.copy(_SCRIPTS / "wake-batch.sh", scripts / "wake-batch.sh")
    shutil.copytree(_LIB_DIR, scripts / "lib", ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("pgai_agent_kanban", "config"):
        shutil.copytree(_TEAM_DIR / name, install / name, ignore=shutil.ignore_patterns("__pycache__"))
    stub = scripts / "wake" / "claude.sh"
    stub.write_text(_STUB_WAKE, encoding="utf-8")
    stub.chmod(0o755)
    return scripts / "wake-batch.sh"


def _fire(wake_batch: pathlib.Path, tmp_path: pathlib.Path, root: pathlib.Path) -> subprocess.CompletedProcess:
    return _run(["bash", str(wake_batch), "--agent=coder"], tmp_path, root)


def _woken(root: pathlib.Path) -> set[str]:
    path = root / "stub-ran"
    return set(path.read_text(encoding="utf-8").split()) if path.exists() else set()


def test_wake_batch_wakes_only_the_names_the_registry_readers_list(tmp_path, wake_install, readers_answer):
    root = tmp_path / "kanban"
    for name in _NAMES:
        (root / "projects" / name).mkdir(parents=True)
    _registry(root, _NAMES)

    result = _fire(wake_install, tmp_path, root)

    assert result.returncode == 0, _say(result)
    assert _woken(root) == readers_answer, _say(result)
    for name in set(_NAMES) - readers_answer:
        logged = [line for line in result.stdout.splitlines() if f"'{name}'" in line and "not a plain project name" in line]
        assert len(logged) == 1, f"{name!r} was not refused by name\n{_say(result)}"


@pytest.mark.parametrize("name", _NAMES)
def test_wake_batch_inbox_goes_only_to_a_project_the_registry_readers_list(tmp_path, wake_install, readers_answer, name):
    root = tmp_path / "kanban"
    (root / "projects" / name).mkdir(parents=True)
    # One section the registry refuses, so the firing has a question to park.
    _registry(root, ("bad.name",))

    result = _fire(wake_install, tmp_path, root)

    assert result.returncode == 0, _say(result)
    parked = (root / "projects" / name / "operator-inbox.md").exists()
    assert parked == (name in readers_answer), f"inbox entry in projects/{name}/: {parked}\n{_say(result)}"


# ---------------------------------------------------------------------------
# cm/finalize.sh and cm/open-doc.sh
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("script", ["finalize.sh", "open-doc.sh"])
@pytest.mark.parametrize("name", _NAMES)
def test_cm_document_verbs_accept_only_the_names_the_registry_readers_list(tmp_path, readers_answer, script, name):
    root = tmp_path / "kanban"
    (root / "projects").mkdir(parents=True)

    # An unparseable version: a name the check accepts stops at the version check.
    result = _run(["bash", str(_SCRIPTS / "cm" / script), name, "not-a-version"], tmp_path, root)

    assert result.returncode == 1, _say(result)
    refused = "invalid project name" in result.stderr
    assert refused == (name not in readers_answer), _say(result)
    if refused:
        assert ini.project_name_rule().pattern in result.stderr, "the refusal does not quote the rule\n" + _say(result)
    else:
        assert re.search(r"invalid (semver )?version", result.stderr), _say(result)


# ---------------------------------------------------------------------------
# import-project.sh
# ---------------------------------------------------------------------------


def _export_tarball(tmp_path: pathlib.Path, name: str, carries_archive: bool) -> pathlib.Path:
    """An export-shaped archive whose single top-level directory is *name*/."""
    out = tmp_path / "exports" / "export.tar.gz"
    out.parent.mkdir(parents=True, exist_ok=True)
    files = {f"{name}/project.cfg": b"[project]\nworkflow_type = document\n"}
    if carries_archive:
        files[f"{name}/ARCHIVE/ARCHIVE-LEDGER.tsv"] = b""
    with tarfile.open(out, "w:gz") as tar:
        for member in (f"{name}/", *([f"{name}/ARCHIVE/"] if carries_archive else [])):
            info = tarfile.TarInfo(member.rstrip("/"))
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            tar.addfile(info)
        for path, data in files.items():
            info = tarfile.TarInfo(path)
            info.size = len(data)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
    return out


@pytest.mark.parametrize("carries_archive", [False, True], ids=["without-archive", "with-archive"])
@pytest.mark.parametrize("name", _NAMES)
def test_import_accepts_only_the_names_the_registry_readers_list(tmp_path, readers_answer, name, carries_archive):
    root = tmp_path / "kanban"
    (root / "projects").mkdir(parents=True)
    (root / "VERSION").write_text("v9.9.9\n", encoding="utf-8")
    export = _export_tarball(tmp_path, name, carries_archive)

    result = _run([str(_SCRIPTS / "import-project.sh"), "--archive", str(export)], tmp_path, root)

    if name in readers_answer:
        assert result.returncode == 0, _say(result)
        assert (root / "projects" / name / "project.cfg").is_file(), _say(result)
    else:
        assert result.returncode == 1, _say(result)
        assert re.search(r"^error:\s+import\.invalid_(archive_)?project_name\s*$", result.stderr, re.MULTILINE), _say(result)
        assert ini.project_name_rule().pattern in result.stderr, "the refusal does not quote the rule\n" + _say(result)
        assert sorted(p.name for p in (root / "projects").iterdir()) == [], _say(result)


# ---------------------------------------------------------------------------
# Push sites: push_decision, cm/push-watchdog.sh and wake_batch_guard_take
# ---------------------------------------------------------------------------


def _push_site_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """A registry holding every name, each with a project directory, a
    project.cfg naming a dev tree, and a HALT file that stops the watchdog
    before any git command."""
    root = tmp_path / "kanban"
    for name in _NAMES:
        project_dir = root / "projects" / name
        project_dir.mkdir(parents=True)
        (project_dir / "project.cfg").write_text(
            f"[project]\ndev_tree_path = {tmp_path / 'dev'}\n", encoding="utf-8"
        )
        (project_dir / "HALT").write_text("", encoding="utf-8")
    _registry(root, _NAMES)
    return root


_GUARD_DRIVER = r"""
set -euo pipefail
source "$LIB_DIR/wake_batch_guard.sh"
rc=0
wake_batch_guard_take "$PGAI_AGENT_KANBAN_ROOT_PATH" coder "$GUARD_PROJECT" "$$" || rc=$?
echo "rc=$rc"
"""


@pytest.mark.parametrize("name", _NAMES)
def test_push_sites_accept_only_the_names_the_registry_readers_list(tmp_path, readers_answer, name):
    root = _push_site_root(tmp_path)
    accepted = name in readers_answer
    rule = ini.project_name_rule().pattern

    # push_decision: refused by the name rule itself, as an unresolved
    # registration, before projects.cfg is read.
    decision = push_decision.resolve_push_decision(root, name)
    if accepted:
        assert decision.decision == push_decision.PUSH, decision
    else:
        assert decision.decision == push_decision.UNRESOLVED, decision
        assert decision.missing == push_decision.MISSING_REGISTRATION, decision
        assert f"{name!r} is not a project name" in decision.status_line, decision.status_line
        assert rule in decision.status_line, decision.status_line

    # cm/push-watchdog.sh: the name check logs its refusal and returns 1
    # (the caller then logs the project as invalid); an accepted name
    # reaches the project's HALT check.
    result = _run(["bash", str(_SCRIPTS / "cm" / "push-watchdog.sh"), "--project", name], tmp_path, root)
    assert result.returncode == 0, _say(result)
    global_log = (root / "logs" / "cm-push-watchdog.log").read_text(encoding="utf-8")
    refusal = [line for line in global_log.splitlines() if f"Rejecting suspicious project name: '{name}'" in line]
    project_log = root / "projects" / name / "logs" / "cm-push-watchdog.log"
    if accepted:
        assert refusal == [], global_log
        assert "HALT file present" in project_log.read_text(encoding="utf-8"), _say(result)
    else:
        assert len(refusal) == 1 and rule in refusal[0], global_log
        assert f"Invalid or unknown project: {name}" in global_log, global_log
        assert not project_log.exists(), _say(result)

    # wake_batch_guard_take, sourced on its own: 2 and no record for a
    # refused name, 0 and a record for an accepted one.
    result = _run(["bash", "-c", _GUARD_DRIVER], tmp_path, root, LIB_DIR=str(_LIB_DIR), GUARD_PROJECT=name)
    assert result.returncode == 0, _say(result)
    assert result.stdout.strip() == ("rc=0" if accepted else "rc=2"), _say(result)
    assert (root / "locks" / f"wake-batch-coder-{name}.record").exists() == accepted, _say(result)
