"""
test_operator_verbs_kanban_root_resolution.py
=============================================
How operator verbs and ``team/scripts/lib/project_paths.sh`` resolve the
kanban root when only one of its two names is exported.

``env_bootstrap.sh`` resolves and exports ``PGAI_AGENT_KANBAN_ROOT_PATH``
for every entry point; ``project_paths.sh``'s ``_pp_kanban_root`` reads
``KANBAN_ROOT`` first and falls back to that name. Locked contracts:

  1. ``inbox.sh list``, ``release-state.sh`` and ``deep-verify-status.sh``
     run with only ``PGAI_AGENT_KANBAN_ROOT_PATH`` set exit 0 with empty
     stderr, and print exactly what a run with both names set prints.
  2. A verb run where no root resolves at all -- neither name set and no
     shell-env beside the install -- still exits non-zero with
     env_bootstrap.sh's refusal, never project_paths.sh's line.
  3. ``_pp_kanban_root`` itself: ``KANBAN_ROOT`` wins when the two names
     disagree, ``PGAI_AGENT_KANBAN_ROOT_PATH`` is used when ``KANBAN_ROOT``
     is unset or empty, and with neither set it fails closed with the
     ``KANBAN_ROOT is not set`` line and no ``$HOME`` default.

Every verb runs as a real subprocess against a sandbox kanban root under
pytest's ``tmp_path``, from a working directory outside the repository,
under an environment built from an explicit set (HOME is a scratch
directory). The verbs exercised here are read-only; the tests snapshot the
sandbox to prove it. Nothing here reads or writes the live install.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]  # team/tests/unit/ -> team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_PROJECT_PATHS = _SCRIPTS_DIR / "lib" / "project_paths.sh"
_PROJECT = "demo-proj"

_PP_UNSET_LINE = "project_paths.sh: KANBAN_ROOT is not set"
_BOOTSTRAP_REFUSAL = "PGAI_AGENT_KANBAN_ROOT_PATH not set — shell-env missing or broken at"

# Three operator verbs that source env_bootstrap.sh and project_paths.sh and
# resolve --project through pp_resolve_project_name without deriving
# KANBAN_ROOT themselves.
_VERBS = {
    "inbox-list": ("inbox.sh", "--project", _PROJECT, "list"),
    "release-state": ("release-state.sh", "--project", _PROJECT),
    "deep-verify-status": ("deep-verify-status.sh", "--project", _PROJECT),
}


# ---------------------------------------------------------------------------
# Sandbox and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def root(tmp_path: pathlib.Path) -> pathlib.Path:
    """A sandbox kanban root with one registered project and a release state."""
    sandbox = tmp_path / "root"
    project_dir = sandbox / "projects" / _PROJECT
    project_dir.mkdir(parents=True)
    (sandbox / "projects.cfg").write_text(f"[project:{_PROJECT}]\npriority = 1\nenabled = true\n", encoding="utf-8")
    (project_dir / "release-state.md").write_text(
        "# Release State\n\n## Active RC\nnone\n\n## RC Opened At\nnone\n\n" "## RC Opened By Task\nnone\n",
        encoding="utf-8",
    )
    return sandbox


def _snapshot(path: pathlib.Path) -> dict[str, bytes]:
    return {str(p.relative_to(path)): p.read_bytes() for p in sorted(path.rglob("*")) if p.is_file()}


def _run(
    tmp_path: pathlib.Path,
    argv: list[str],
    roots: dict[str, str],
) -> subprocess.CompletedProcess:
    """Run *argv* under an explicit environment carrying only *roots* as root names."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    temp_dir = tmp_path / "framework-tmp"
    temp_dir.mkdir(exist_ok=True)
    cwd = tmp_path / "cwd"
    cwd.mkdir(exist_ok=True)
    env = build_scrubbed_env(
        home_dir=home,
        extra_path_dirs=[pathlib.Path(sys.executable).parent],
        extra_env={"PGAI_AGENT_KANBAN_TEMP_DIR": str(temp_dir), **roots},
    )
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd),
        timeout=60,
        stdin=subprocess.DEVNULL,
    )


def _diagnostic(result: subprocess.CompletedProcess) -> str:
    return f"exit {result.returncode}\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _kanban_root_probe(tmp_path: pathlib.Path, roots: dict[str, str]) -> subprocess.CompletedProcess:
    """Source project_paths.sh and print what _pp_kanban_root resolves."""
    script = f'set -euo pipefail\nsource "{_PROJECT_PATHS}"\n_pp_kanban_root\n'
    return _run(tmp_path, ["bash", "-c", script], roots)


# ---------------------------------------------------------------------------
# 1. Verbs with only PGAI_AGENT_KANBAN_ROOT_PATH set
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("verb", sorted(_VERBS))
def test_verb_with_only_canonical_root_name_succeeds_silently(
    verb: str, root: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    script, *args = _VERBS[verb]
    argv = ["bash", str(_SCRIPTS_DIR / script), *args]
    before = _snapshot(root)

    both = _run(tmp_path, argv, {"KANBAN_ROOT": str(root), "PGAI_AGENT_KANBAN_ROOT_PATH": str(root)})
    only_canonical = _run(tmp_path, argv, {"PGAI_AGENT_KANBAN_ROOT_PATH": str(root)})

    assert both.returncode == 0, f"baseline run with both root names failed:\n{_diagnostic(both)}"
    assert only_canonical.returncode == 0, (
        f"{script} failed with only PGAI_AGENT_KANBAN_ROOT_PATH set:\n" f"{_diagnostic(only_canonical)}"
    )
    assert only_canonical.stderr == "", (
        f"{script} wrote to stderr with only PGAI_AGENT_KANBAN_ROOT_PATH set:\n" f"{_diagnostic(only_canonical)}"
    )
    assert only_canonical.stdout == both.stdout, (
        f"{script} printed different output with KANBAN_ROOT unset.\n"
        f"--- both set ---\n{both.stdout}\n--- only PGAI_AGENT_KANBAN_ROOT_PATH ---\n"
        f"{only_canonical.stdout}"
    )
    assert _snapshot(root) == before, f"read-only verb {script} changed the sandbox root"


# ---------------------------------------------------------------------------
# 2. No resolvable root: env_bootstrap.sh refuses first
# ---------------------------------------------------------------------------


def test_verb_with_no_resolvable_root_refuses_through_env_bootstrap(
    tmp_path: pathlib.Path,
) -> None:
    """The verb runs from an install whose scripts/ links to the real tree,
    so env_bootstrap.sh derives that install's directory -- which holds no
    shell-env -- as its candidate, independent of anything beside team/."""
    install = tmp_path / "install"
    install.mkdir()
    (install / "scripts").symlink_to(_SCRIPTS_DIR, target_is_directory=True)
    assert not (install / "shell-env").exists()

    result = _run(
        tmp_path,
        ["bash", str(install / "scripts" / "release-state.sh"), "--project", _PROJECT],
        {},
    )

    assert result.returncode != 0, _diagnostic(result)
    assert _BOOTSTRAP_REFUSAL in result.stderr, _diagnostic(result)
    assert str(install / "shell-env") in result.stderr, _diagnostic(result)
    assert _PP_UNSET_LINE not in result.stderr, _diagnostic(result)
    assert result.stdout == "", _diagnostic(result)


# ---------------------------------------------------------------------------
# 3. _pp_kanban_root resolution order
# ---------------------------------------------------------------------------


def test_kanban_root_name_wins_when_the_two_names_disagree(tmp_path: pathlib.Path) -> None:
    kanban_root = tmp_path / "named-by-kanban-root"
    canonical = tmp_path / "named-by-canonical"
    result = _kanban_root_probe(
        tmp_path,
        {"KANBAN_ROOT": str(kanban_root), "PGAI_AGENT_KANBAN_ROOT_PATH": str(canonical)},
    )
    assert result.returncode == 0, _diagnostic(result)
    assert result.stdout == f"{kanban_root}\n", _diagnostic(result)
    assert result.stderr == "", _diagnostic(result)


@pytest.mark.parametrize("kanban_root_value", [None, ""], ids=["unset", "empty"])
def test_canonical_name_is_used_when_kanban_root_is_absent(
    kanban_root_value: str | None, tmp_path: pathlib.Path
) -> None:
    canonical = tmp_path / "named-by-canonical"
    roots = {"PGAI_AGENT_KANBAN_ROOT_PATH": str(canonical)}
    if kanban_root_value is not None:
        roots["KANBAN_ROOT"] = kanban_root_value
    result = _kanban_root_probe(tmp_path, roots)
    assert result.returncode == 0, _diagnostic(result)
    assert result.stdout == f"{canonical}\n", _diagnostic(result)
    assert result.stderr == "", _diagnostic(result)


def test_kanban_root_fails_closed_with_neither_name_set(tmp_path: pathlib.Path) -> None:
    """A decoy root at $HOME/pgai_agent_kanban exists, so a default to it
    would print its path; the helper must print nothing and refuse."""
    (tmp_path / "home" / "pgai_agent_kanban").mkdir(parents=True)
    result = _kanban_root_probe(tmp_path, {})
    assert result.returncode != 0, _diagnostic(result)
    assert result.stdout == "", _diagnostic(result)
    assert _PP_UNSET_LINE in result.stderr, _diagnostic(result)
