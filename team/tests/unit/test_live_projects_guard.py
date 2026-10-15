"""
test_live_projects_guard.py
===========================
Behavioral tests for the session-scoped live projects/ guard in
team/tests/conftest.py (pytest_sessionstart / pytest_sessionfinish): the
backstop that fails a test run which created an unregistered entry under
the invoking shell's $PGAI_AGENT_KANBAN_ROOT_PATH/projects/.

Coverage:
  - end to end: a child pytest session, loading the conftest as a plugin
    and pointed at a sandbox root by the full five-variable sandbox recipe,
    runs one test that plants new folders under that root's projects/. The
    session exits 1 and its output names every planted entry and the
    dual-channel error key -- the guard fires.
  - the same child session tolerates an entry that already existed at
    session start and one the test registered in projects.cfg while it ran
    (a legitimate concurrent operator action), exiting 0.
  - with no root named at all the guard stays inactive.
  - the helper unexplained_live_project_entries() directly: pre-existing
    and registered names are explained, the rest are returned sorted.

Every root is a throwaway directory under tmp_path; the child session's
environment is built from an explicit minimal set (build_scrubbed_env), so
the operator's live root is never named to it.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

from tests.conftest import unexplained_live_project_entries
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]

# The planted test: creates each name in PLANT_NAMES under the sandbox
# root's projects/ and registers each name in PLANT_REGISTER in its
# projects.cfg. It reads the root from PLANT_ROOT because the autouse
# _block_live_kanban_writes fixture repoints the root names for the test's
# own call -- the guard watches the root the session started with.
_PLANTED_TEST = """\
import os
import pathlib


def test_plants_entries_under_the_session_root():
    root = pathlib.Path(os.environ["PLANT_ROOT"])
    for name in os.environ.get("PLANT_NAMES", "").split():
        (root / "projects" / name).mkdir(parents=True)
    for name in os.environ.get("PLANT_REGISTER", "").split():
        with open(root / "projects.cfg", "a", encoding="utf-8") as handle:
            handle.write(f"\\n[project:{name}]\\npriority=2\\n")
"""


def _sandbox(tmp_path: pathlib.Path) -> pathlib.Path:
    root = tmp_path / "sandbox_root"
    (root / "tmp").mkdir(parents=True)
    (root / "projects" / "pre-existing").mkdir(parents=True)
    (root / "projects.cfg").write_text("[project:pre-existing]\npriority=1\n", encoding="utf-8")
    return root


def _run_child_session(
    tmp_path: pathlib.Path,
    root: pathlib.Path | None,
    plant: str = "",
    register: str = "",
) -> subprocess.CompletedProcess:
    """Run the planted test in a child pytest session with the conftest
    loaded as a plugin. *root* is named through all five root variables
    together (KANBAN_ROOT, PGAI_AGENT_KANBAN_ROOT_PATH and
    PGAI_AGENT_KANBAN_TEMP_DIR set; PGAI_TASKS_DIR and PGAI_PROJECT_ROOT
    absent, since build_scrubbed_env never constructs them); None names no
    root at all."""
    test_dir = tmp_path / "planted"
    test_dir.mkdir()
    (test_dir / "test_planted.py").write_text(_PLANTED_TEST, encoding="utf-8")
    plant_root = root if root is not None else tmp_path / "unnamed_root"
    extra = {
        "PYTHONPATH": str(_TEAM_DIR),
        "PLANT_ROOT": str(plant_root),
        "PLANT_NAMES": plant,
        "PLANT_REGISTER": register,
    }
    if root is not None:
        extra.update(
            {
                "KANBAN_ROOT": str(root),
                "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
                "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
            }
        )
    else:
        extra["PGAI_AGENT_KANBAN_TEMP_DIR"] = str(tmp_path / "child_tmp")
    env = build_scrubbed_env(home_dir=tmp_path / "home", extra_env=extra)
    return subprocess.run(
        [
            sys.executable, "-m", "pytest",
            "-p", "no:cacheprovider", "-p", "no:randomly", "-p", "tests.conftest",
            "--rootdir", str(test_dir), "-q", str(test_dir / "test_planted.py"),
        ],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
        env=env,
        timeout=120,
    )


def test_guard_fails_the_run_and_names_each_planted_entry(tmp_path: pathlib.Path) -> None:
    root = _sandbox(tmp_path)

    result = _run_child_session(tmp_path, root, plant="a no-such-project")

    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert "1 passed" in output, "the planted test itself passes; the guard fails the run"
    assert "tests.live_projects_leak" in output
    assert str(root / "projects" / "a") in output
    assert str(root / "projects" / "no-such-project") in output
    assert str(root / "projects" / "pre-existing") not in output
    # Reported, never repaired.
    assert (root / "projects" / "a").is_dir()


def test_guard_tolerates_pre_existing_and_newly_registered_entries(tmp_path: pathlib.Path) -> None:
    root = _sandbox(tmp_path)

    result = _run_child_session(tmp_path, root, plant="registered-now", register="registered-now")

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "tests.live_projects_leak" not in output


def test_guard_is_inactive_when_no_root_is_named(tmp_path: pathlib.Path) -> None:
    unnamed = tmp_path / "unnamed_root"
    (unnamed / "projects").mkdir(parents=True)

    result = _run_child_session(tmp_path, None, plant="a")

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "tests.live_projects_leak" not in output


def test_unexplained_entries_excludes_pre_existing_and_registered_names(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "root"
    for name in ("pre-existing", "zeta", "alpha", "registered"):
        (root / "projects" / name).mkdir(parents=True)
    (root / "projects.cfg").write_text("[project:registered]\npriority=1\n", encoding="utf-8")

    assert unexplained_live_project_entries(root, {"pre-existing"}) == ["alpha", "zeta"]


def test_unexplained_entries_is_empty_when_projects_dir_is_absent(tmp_path: pathlib.Path) -> None:
    assert unexplained_live_project_entries(tmp_path / "no-root", set()) == []
