"""
install_shaped_kanban_root.py
==============================
Test-fidelity helper: build a kanban tree that mirrors the INSTALL layout
produced by install.sh, populated with the scripts pseudocron and
dashboard/create.sh need to start up inside a container.

WHY THIS EXISTS (gap the v1.26.0 gated fixtures missed)
--------------------------------------------------------
The v1.26.0 docker-gated fixtures used a dev-tree-shaped volume for the
/pgai_agent_kanban bind-mount.  In the dev tree, pseudocron.py lives at
``team/scripts/pseudocron.py`` and dashboard create.sh at
``team/scripts/dashboard/create.sh``.  The entrypoint was fixed to use
INSTALL-layout paths (``scripts/pseudocron.py``, ``scripts/dashboard/create.sh``
— no ``team/`` prefix), but the fixture volume retained the dev-tree shape.
Both paths resolved (one directly, one via fallback) so the defect was masked.

This fixture builds a tree that matches what a real operator mounts:
files live at ``scripts/...``, not ``team/scripts/...``.  Using this
fixture in a docker-gated test ensures the INSTALL-layout path is the
only one that resolves.

RELATIONSHIP TO installed_root.py
----------------------------------
``team/tests/fixtures/installed_root.py`` produces an install-shaped tree
for Python/shell integration tests (no docker).  Its ``scripts/`` directory
is left empty because those tests do not execute scripts inside containers.

This module builds on that foundation and adds the script content that
docker-gated tests need:

  - ``scripts/pseudocron.py``   — copied from the dev tree
  - ``scripts/dashboard/create.sh`` — stub script (echoes a sentinel and
    exits 0 so the dashboard dispatch path can be verified without tmux)
  - ``pseudocron.cfg``           — minimal schedule that pseudocron can
    parse without error (one never-matching job so it does not fire during
    the test; startup banner still emits before the first sleep)
  - ``kanban.cfg``               — minimal INI config (kanban root config)

The fixture deliberately does NOT contain a ``team/`` directory so the
INSTALL-layout path is the only one that resolves.

HOW TO USE
----------
As a plain function (any test file):

    from tests.fixtures.install_shaped_kanban_root import (
        build_install_shaped_kanban_root,
    )

    root = build_install_shaped_kanban_root(tmp_path)
    # root is a pathlib.Path:
    #   root/
    #     kanban.cfg
    #     pseudocron.cfg
    #     scripts/
    #       pseudocron.py           (real script — copied from dev tree)
    #       dashboard/
    #         create.sh             (stub — echo sentinel + exit 0)
    #     roles/                    (empty directory)
    #     logs/                     (empty directory)
    #     locks/                    (empty directory)
    #     projects/                 (empty directory)

As a pytest fixture (request by name or import into conftest.py):

    from tests.fixtures.install_shaped_kanban_root import (
        install_shaped_kanban_root,
    )

INSTALL LAYOUT CONTRACT (what install.sh writes)
-------------------------------------------------
After ``bash install.sh`` the kanban root contains:
  - kanban.cfg            — seeded from kanban.cfg_example
  - scripts/              — copied from team/scripts/ (all scripts, flat)
  - scripts/dashboard/    — copied as a subdirectory
  - scripts/lib/          — copied as a subdirectory
  - roles/                — copied from team/roles/
  - workflows/            — copied from team/workflows/
  - logs/                 — created by install.sh
  - locks/                — created by install.sh
  - projects/             — created by create-project.sh or install --self

This fixture populates only the subset needed by docker-gated AC1-3 tests.
"""

from __future__ import annotations

import pathlib
import shutil

import pytest

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

# team/ directory in the dev tree — resolved from this file's location.
# fixtures/ → tests/ → team/
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent

# Repo root is one level above team/.
_REPO_ROOT = _TEAM_DIR.parent

# Real pseudocron.py lives at team/scripts/pseudocron.py in the dev tree.
_REAL_PSEUDOCRON = _TEAM_DIR / "scripts" / "pseudocron.py"

# Container entrypoint lives at docker/entrypoint.sh in the repo root.
# install.sh copies it to scripts/entrypoint.sh in the installed kanban root.
_REAL_ENTRYPOINT = _REPO_ROOT / "docker" / "entrypoint.sh"

# The pgai_agent_kanban Python package in the dev tree.
# install.sh copies it to $KANBAN_ROOT/pgai_agent_kanban/ so that pseudocron.py
# can import it via the sys.path insertion (_TEAM_DIR = $KANBAN_ROOT when run
# from $KANBAN_ROOT/scripts/pseudocron.py).
_REAL_PGAI_PACKAGE = _TEAM_DIR / "pgai_agent_kanban"


# ---------------------------------------------------------------------------
# Core builder
# ---------------------------------------------------------------------------


def build_install_shaped_kanban_root(
    parent: pathlib.Path,
    subdir: str = "kanban_install",
) -> pathlib.Path:
    """Build a directory tree that mirrors the install.sh output layout.

    The returned path is a *simulated install root* suitable for bind-mounting
    into a container at /pgai_agent_kanban.  It contains the scripts that
    docker/entrypoint.sh dispatches to, at the INSTALL-layout paths:

      ``scripts/pseudocron.py``          — real script copied from dev tree
      ``scripts/dashboard/create.sh``    — stub: echo sentinel, exit 0
      ``pseudocron.cfg``                 — minimal valid schedule
      ``kanban.cfg``                     — minimal INI config

    The tree does NOT contain a ``team/`` directory.  This is the critical
    constraint: only INSTALL-layout paths resolve, so a test using this fixture
    will catch any regression to dev-tree paths in the entrypoint.

    Args:
        parent:  Parent directory for the new root.  Use pytest's tmp_path.
        subdir:  Name of the subdirectory to create under *parent*.

    Returns:
        pathlib.Path — absolute path to the simulated install root.

    Layout created:
        <parent>/<subdir>/
            kanban.cfg               — minimal INI config
            pseudocron.cfg           — one never-firing job (startup banner is the evidence)
            pgai_agent_kanban/       — Python package (copied from team/pgai_agent_kanban/)
                                       required by pseudocron.py's sys.path import
            scripts/
                entrypoint.sh        — real entrypoint (copied from docker/entrypoint.sh)
                pseudocron.py        — real script (copied from team/scripts/pseudocron.py)
                dashboard/
                    create.sh        — stub: echo sentinel + exit 0
            roles/                   — empty
            logs/                    — empty
            locks/                   — empty
            projects/                — empty
    """
    root = parent / subdir
    root.mkdir(parents=True, exist_ok=True)

    # --- kanban.cfg: minimal INI config ---
    (root / "kanban.cfg").write_text(
        "# kanban.cfg — minimal install-shaped fixture config\n"
        "# Built by install_shaped_kanban_root.build_install_shaped_kanban_root()\n"
        "\n"
        "[paths]\n"
        "\n"
        "[chain]\n"
        "pm_mode = automatic\n"
        "\n"
        "[wake]\n"
        "max_tasks_per_wake = 1\n"
        "max_runtime_seconds = 600\n",
        encoding="utf-8",
    )

    # --- pseudocron.cfg: minimal schedule pseudocron can parse ---
    # Minute 59 chosen so it never fires during a short-lived container test.
    # The startup banner ("pseudocron starting: 1 jobs loaded") emits before
    # any sleep, providing observable evidence without waiting for a wake.
    (root / "pseudocron.cfg").write_text(
        "# pseudocron.cfg — minimal test schedule\n"
        "# Built by install_shaped_kanban_root.build_install_shaped_kanban_root()\n"
        "# One never-firing job so pseudocron loads without error.\n"
        "59  echo kanban-pseudocron-test-job\n",
        encoding="utf-8",
    )

    # --- pgai_agent_kanban/: copy the Python package ---
    # pseudocron.py adds its parent's parent to sys.path and imports
    # pgai_agent_kanban.env.resolve_kanban_root.  In a real install,
    # install.sh copies team/pgai_agent_kanban/ to $KANBAN_ROOT/pgai_agent_kanban/.
    # When pseudocron.py is at $KANBAN_ROOT/scripts/pseudocron.py, _TEAM_DIR
    # resolves to $KANBAN_ROOT, and the package is found at
    # $KANBAN_ROOT/pgai_agent_kanban/.
    if not _REAL_PGAI_PACKAGE.exists():
        raise FileNotFoundError(
            f"pgai_agent_kanban package not found at {_REAL_PGAI_PACKAGE}; "
            "the fixture requires it to satisfy pseudocron.py imports"
        )
    dest_package = root / "pgai_agent_kanban"
    if dest_package.exists():
        shutil.rmtree(dest_package)
    shutil.copytree(_REAL_PGAI_PACKAGE, dest_package)

    # --- scripts/: create the directory tree ---
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)

    # --- scripts/entrypoint.sh: copy the real entrypoint from the repo ---
    # docker/entrypoint.sh is the container entrypoint script.
    # install.sh copies it to scripts/entrypoint.sh in the installed kanban root
    # (mirroring the install layout so the mount path is install-shaped).
    # Docker-gated tests invoke it via:
    #   docker run --entrypoint /pgai_agent_kanban/scripts/entrypoint.sh <image> [mode]
    if not _REAL_ENTRYPOINT.exists():
        raise FileNotFoundError(
            f"docker/entrypoint.sh not found at {_REAL_ENTRYPOINT}; "
            "the fixture requires the real entrypoint to populate "
            "scripts/entrypoint.sh"
        )
    dest_entrypoint = scripts_dir / "entrypoint.sh"
    shutil.copy2(_REAL_ENTRYPOINT, dest_entrypoint)
    dest_entrypoint.chmod(0o755)

    # --- scripts/pseudocron.py: copy the real script from the dev tree ---
    # The entrypoint dispatches to ${PGAI_AGENT_KANBAN_ROOT_PATH}/scripts/pseudocron.py.
    # Copying the real script (not a stub) ensures the import chain inside
    # pseudocron.py resolves correctly when PGAI_AGENT_KANBAN_ROOT_PATH points
    # at this install-shaped root.
    if not _REAL_PSEUDOCRON.exists():
        raise FileNotFoundError(
            f"pseudocron.py not found at dev-tree path {_REAL_PSEUDOCRON}; "
            "the fixture requires the real script to populate scripts/pseudocron.py"
        )
    shutil.copy2(_REAL_PSEUDOCRON, scripts_dir / "pseudocron.py")

    # --- scripts/dashboard/: create stub create.sh ---
    # The entrypoint dispatches to:
    #   exec /bin/bash "${PGAI_AGENT_KANBAN_ROOT_PATH}/scripts/dashboard/create.sh"
    # A full tmux dashboard session is not practical inside a non-TTY container.
    # This stub echoes a sentinel line to stderr (matching the entrypoint log
    # format) and exits 0 — sufficient to verify the dispatch path fires.
    dashboard_dir = scripts_dir / "dashboard"
    dashboard_dir.mkdir(parents=True, exist_ok=True)
    create_sh = dashboard_dir / "create.sh"
    create_sh.write_text(
        "#!/usr/bin/env bash\n"
        "# create.sh — install-shaped fixture stub for docker-gated tests\n"
        "# Emits a sentinel line to stderr so tests can assert dashboard dispatch.\n"
        "echo 'install-fixture: dashboard create.sh stub reached' >&2\n"
        "exit 0\n",
        encoding="utf-8",
    )
    create_sh.chmod(0o755)

    # --- Skeleton runtime directories ---
    for dirname in ("roles", "logs", "locks", "projects"):
        (root / dirname).mkdir(parents=True, exist_ok=True)

    return root


# ---------------------------------------------------------------------------
# pytest fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def install_shaped_kanban_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """Pytest fixture: return an install-shaped kanban root for docker-gated tests.

    The root mirrors the layout install.sh produces, populated with the scripts
    docker/entrypoint.sh dispatches to.  Suitable for bind-mounting at
    /pgai_agent_kanban inside a container.

    Does NOT contain a team/ directory — only INSTALL-layout paths resolve.

    Example usage:

        def test_pseudocron_starts(install_shaped_kanban_root):
            root = install_shaped_kanban_root
            assert (root / "scripts" / "pseudocron.py").exists()
            assert not (root / "team").exists()
    """
    return build_install_shaped_kanban_root(tmp_path)
