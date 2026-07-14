"""
installed_root.py
=================
Test-fidelity helper: simulate the installed kanban tree layout.

WHY THIS EXISTS (an earlier defect)
--------------------------
Before this helper, integration tests ran against the *dev tree* where the
Python shim package (team/pgai_agent_kanban/) is always present on sys.path
and importable without any path manipulation.  In the *installed* layout that
install.sh produces, the shim package is not present — only the runtime
directories and copied scripts live at the kanban root.  Tests that relied on
the dev tree passing silently masked import failures that broke real-world
deployments.

HOW TO USE
----------
As a plain function (any test file):

    from team.tests.fixtures.installed_root import build_installed_root

    root = build_installed_root(tmp_path)
    # root is a pathlib.Path to a directory that mirrors the installed layout:
    #   root/
    #     kanban.cfg                — minimal INI config
    #     workflows/
    #       release/pipeline.yaml  — release pipeline (copied from real src)
    #       document/pipeline.yaml — document pipeline (copied from real src)
    #     roles/                   — empty directory (no team/ shim present)
    #     scripts/                 — empty directory placeholder
    #     logs/                    — runtime log directory
    #     locks/                   — runtime lock directory
    #     projects/                — empty projects registry root

As a pytest fixture (import into conftest.py or request in a test):

    from team.tests.fixtures.installed_root import installed_root_fixture
    # or, using conftest re-export:
    def test_something(installed_root):
        root = installed_root(tmp_path)

WHAT "INSTALLED LAYOUT" MEANS
------------------------------
The installed root produced by install.sh (without --self-project) contains:
  - kanban.cfg            (seeded from kanban.cfg_example)
  - workflows/            (copied from team/workflows/ by install.sh; includes
                           plugin subdirs release/ and document/ with pipeline.yaml)
  - roles/                (copied from team/roles/)
  - scripts/              (copied from team/scripts/)
  - logs/, locks/         (runtime directories created by install.sh)
  - projects/             (created by create-project.sh / add-project.sh)

Critically, it does NOT contain a team/ subdirectory with the Python shim
package.  The helper simulates this by placing the real workflow YAML files
under root/workflows/ and omitting team/ entirely.

WHAT THIS HELPER DOES NOT DO
-----------------------------
- It does not run install.sh.  It is a fast in-process builder.
- It does not create a fully functional projects.cfg or any project entry.
  Callers that need a registered project should use build_installed_root() to
  get the base root, then layer a project on top manually or via two_project.py.
- It does not add scripts/ content.  Tests that need real scripts to be
  callable should point at the dev tree's team/scripts/.
"""

from __future__ import annotations

import pathlib
import shutil

import pytest

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

# team/ directory in the dev tree — two levels up from this file's directory
# (fixtures/ → tests/ → team/)
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent

# Real workflow definitions in the dev tree
_REAL_WORKFLOWS_DIR = _TEAM_DIR / "workflows"

# Workflow plugin directories to copy into the simulated installed root.
# Each entry is a subdirectory of _REAL_WORKFLOWS_DIR containing at minimum
# workflow.cfg and workflow.sh; types with a pipeline.yaml (release, document)
# also carry it inside the directory so load_workflow() can resolve them.
_WORKFLOW_PLUGIN_DIRS = ["release", "document"]


# ---------------------------------------------------------------------------
# Core builder
# ---------------------------------------------------------------------------


def build_installed_root(
    parent: pathlib.Path,
    subdir: str = "kanban_installed",
) -> pathlib.Path:
    """Build a directory tree that mirrors the installed (not dev) kanban layout.

    The returned path is a *simulated installed root* — it contains the
    directories and files that install.sh creates, but does NOT contain a
    team/ subdirectory with the Python shim package.  This is the correct
    layout for testing code that runs after a production install.

    Args:
        parent:  Parent directory for the new root.  Caller is responsible
                 for choosing a temp path (e.g. pytest's tmp_path).
        subdir:  Name of the subdirectory to create under *parent*.
                 Defaults to "kanban_installed" to be self-documenting in
                 test output.

    Returns:
        pathlib.Path — absolute path to the simulated installed root.

    Layout created:
        <parent>/<subdir>/
            kanban.cfg          — minimal INI kanban config
            workflows/
                release/        — plugin directory copied from dev tree
                    pipeline.yaml
                    workflow.cfg
                    workflow.sh
                document/       — plugin directory copied from dev tree
                    pipeline.yaml
                    workflow.cfg
                    workflow.sh
            roles/              — empty (no scripts sourced; placeholder)
            scripts/            — empty (no scripts sourced; placeholder)
            logs/               — runtime log directory (mirrors install.sh)
            locks/              — runtime lock directory (mirrors install.sh)
            projects/           — empty projects registry root

    This builder deliberately omits team/ (and therefore the shim) so tests must
    resolve the package the same way production code does.
    """
    root = parent / subdir
    root.mkdir(parents=True, exist_ok=True)

    # --- kanban.cfg: minimal INI config (enough for read_ini consumers) ---
    (root / "kanban.cfg").write_text(
        "# kanban.cfg — minimal simulated installed-root config\n"
        "# Generated by build_installed_root() for test-fidelity purposes.\n"
        "# BUG-0158: this root has no team/ shim package — mirrors production.\n"
        "\n"
        "[paths]\n"
        "# dev_tree_path is intentionally omitted here; tests that need it\n"
        "# should supply it via monkeypatch or explicit argument.\n"
        "\n"
        "[chain]\n"
        "pm_mode = automatic\n"
        "\n"
        "[wake]\n"
        "max_tasks_per_wake = 1\n"
        "max_runtime_seconds = 600\n",
        encoding="utf-8",
    )

    # --- workflows/: copy plugin directories (mirror install.sh behaviour) ---
    # install.sh copies team/workflows/ contents to $KANBAN_ROOT/workflows/.
    # After v1.3.0, the layout uses plugin directories (workflows/<type>/)
    # containing workflow.cfg, workflow.sh, and optionally pipeline.yaml.
    wf_dir = root / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    for plugin_name in _WORKFLOW_PLUGIN_DIRS:
        src_plugin = _REAL_WORKFLOWS_DIR / plugin_name
        if src_plugin.is_dir():
            dest_plugin = wf_dir / plugin_name
            if dest_plugin.exists():
                shutil.rmtree(dest_plugin)
            shutil.copytree(src_plugin, dest_plugin)

    # --- Skeleton runtime directories (present in a real install) ---
    for dirname in ("roles", "scripts", "logs", "locks", "projects"):
        (root / dirname).mkdir(parents=True, exist_ok=True)

    return root


# ---------------------------------------------------------------------------
# pytest fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def installed_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """Pytest fixture: return a simulated installed-root directory.

    The root mirrors what install.sh produces without --self-project: no
    team/ shim, real workflow YAML files under workflows/, and empty runtime
    directories.

    Composes with conftest.py's _block_live_kanban_writes autouse fixture —
    that fixture redirects env vars to a safe temp dir; this fixture
    builds the installed-root layout independently inside tmp_path so the
    two do not interfere.

    Example usage in a test:

        def test_workflow_pipeline_present(installed_root):
            assert (installed_root / "workflows" / "release" / "pipeline.yaml").exists()

        def test_no_shim_package(installed_root):
            # The installed root must not have team/ (no shim).
            assert not (installed_root / "team").exists()

    an earlier defect: this fixture closes the test-fidelity gap where tests ran
    in the dev tree (shim always importable) instead of the installed tree
    (shim absent).
    """
    return build_installed_root(tmp_path)
