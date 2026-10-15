"""
test_operator_help_short_alias.py
=================================
Every operator script whose --help advertises "--help, -h" accepts -h
exactly as it accepts --help: exit 0, the same bytes on stdout and on
stderr, no "unexpected positional argument" refusal, and nothing written.

Which scripts are covered
-------------------------
The parameter set is derived, never listed by hand:

  1. Operator scripts are the shell scripts under team/scripts/ (sourced
     helpers under any lib/ directory excluded) that render their help
     through operator_args_render_help_for_flags -- the shared renderer
     whose built-in help line is "--help, -h         Show this help and
     exit". Only these are executed: running an arbitrary script with
     --help is not known to be side-effect free, while every renderer
     caller answers --help before doing any work.
  2. Each of those runs once with --help in a throwaway kanban root at
     collection time; it is covered when its combined --help output
     (stdout and stderr -- a few scripts print their usage on stderr)
     contains "--help, -h".

A script that starts advertising -h is covered the moment it lands; one
that advertises -h but still refuses it fails here, naming the script.

What each case asserts
----------------------
In a fresh throwaway root, with the script run from a working directory
inside that root:

  * -h exits 0, and neither stream carries "unexpected positional
    argument";
  * the root's file tree (paths, file bytes, symlink targets) is the same
    after the -h run as before it, HOME and the temp dir included;
  * --help, run afterwards in the same root, produces byte-identical
    stdout and stderr and the same exit code.

Isolation
---------
Every run gets an explicit environment (build_scrubbed_env): HOME, the
kanban root, KANBAN_ROOT and the temp dir all point into the throwaway
root, and PGAI_TASKS_DIR / PGAI_PROJECT_ROOT are absent, so no run can
reach the operator's live install (docs/test-authoring-guidelines.md,
"Live-install safety"). Case roots live under pytest's tmp_path; the
collection-time probe roots come from pgai_mkdtemp and are removed after
each probe.
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import shutil
import subprocess

import pytest

from tests.conftest import pgai_mkdtemp
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _TEAM_DIR / "scripts"

_RENDERER_CALL = "operator_args_render_help_for_flags"
_ADVERTISED_SHORT_HELP = "--help, -h"
_POSITIONAL_REFUSAL = "unexpected positional argument"
_RUN_TIMEOUT_SECONDS = 60


def _renderer_scripts() -> list[pathlib.Path]:
    """Shell scripts under team/scripts/ that render help through the shared renderer."""
    scripts = []
    for path in sorted(_SCRIPTS_DIR.rglob("*.sh")):
        if "lib" in path.relative_to(_SCRIPTS_DIR).parts:
            continue
        if _RENDERER_CALL in path.read_text(encoding="utf-8", errors="replace"):
            scripts.append(path)
    return scripts


def _throwaway_root(parent: pathlib.Path) -> pathlib.Path:
    """A minimal kanban root: the root itself, its temp dir and a working dir."""
    root = parent / "kanban_root"
    (root / "tmp").mkdir(parents=True)
    (root / "cwd").mkdir()
    return root


def _run(script: pathlib.Path, flag: str, root: pathlib.Path) -> subprocess.CompletedProcess:
    """Run *script* with the single argument *flag* inside throwaway *root*."""
    env = build_scrubbed_env(
        home_dir=root,
        extra_env={
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
        },
    )
    return subprocess.run(
        ["bash", str(script), flag],
        capture_output=True, env=env, cwd=str(root / "cwd"),
        stdin=subprocess.DEVNULL, timeout=_RUN_TIMEOUT_SECONDS, check=False,
    )


def _advertises_short_help(script: pathlib.Path) -> bool:
    """True when *script*'s sandboxed --help output contains "--help, -h"."""
    probe_dir = pathlib.Path(pgai_mkdtemp(suffix="-short-help-probe"))
    try:
        result = _run(script, "--help", _throwaway_root(probe_dir))
    finally:
        shutil.rmtree(probe_dir, ignore_errors=True)
    output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    return _ADVERTISED_SHORT_HELP in output


def _script_id(script: pathlib.Path) -> str:
    return str(script.relative_to(_SCRIPTS_DIR))


_ADVERTISING_SCRIPTS = [script for script in _renderer_scripts() if _advertises_short_help(script)]


def _tree_snapshot(root: pathlib.Path) -> dict[str, str | None]:
    """Every path under *root*: directories as None, files by content hash, links by target."""
    snapshot: dict[str, str | None] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        base = pathlib.Path(dirpath)
        for name in dirnames + filenames:
            path = base / name
            rel = str(path.relative_to(root))
            if path.is_symlink():
                snapshot[rel] = "link:" + os.readlink(path)
            elif path.is_dir():
                snapshot[rel + "/"] = None
            else:
                snapshot[rel] = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


def _diagnostic(label: str, result: subprocess.CompletedProcess) -> str:
    return (
        f"--- {label} exit {result.returncode} ---\n"
        f"--- stdout ---\n{result.stdout.decode('utf-8', errors='replace')}\n"
        f"--- stderr ---\n{result.stderr.decode('utf-8', errors='replace')}"
    )


def test_the_covered_set_is_derived_from_scripts_that_advertise_short_help() -> None:
    renderer_scripts = _renderer_scripts()
    assert renderer_scripts, (
        f"no script under {_SCRIPTS_DIR} calls {_RENDERER_CALL}; the discovery "
        "walk is broken, so the -h cases below would cover nothing"
    )
    assert _ADVERTISING_SCRIPTS, (
        f"none of the {len(renderer_scripts)} renderer callers prints "
        f"{_ADVERTISED_SHORT_HELP!r} for --help in a throwaway root; either the "
        "renderer's built-in help line changed or every probe failed"
    )


@pytest.mark.parametrize("script", _ADVERTISING_SCRIPTS, ids=_script_id)
def test_short_help_prints_the_same_help_and_writes_nothing(
    tmp_path: pathlib.Path, script: pathlib.Path
) -> None:
    root = _throwaway_root(tmp_path)
    before = _tree_snapshot(root)

    short = _run(script, "-h", root)
    after = _tree_snapshot(root)
    long = _run(script, "--help", root)

    diagnostic = _diagnostic("-h", short) + "\n" + _diagnostic("--help", long)
    assert short.returncode == 0, f"{_script_id(script)} -h did not exit 0\n{diagnostic}"
    for stream in (short.stdout, short.stderr):
        assert _POSITIONAL_REFUSAL not in stream.decode("utf-8", errors="replace"), (
            f"{_script_id(script)} refused -h as a positional argument\n{diagnostic}"
        )
    assert after == before, (
        f"{_script_id(script)} -h changed the throwaway root's file tree\n"
        f"added: {sorted(set(after) - set(before))}\n"
        f"removed: {sorted(set(before) - set(after))}\n"
        f"changed: {sorted(k for k in set(before) & set(after) if before[k] != after[k])}"
    )
    assert long.returncode == short.returncode, diagnostic
    assert short.stdout == long.stdout, f"{_script_id(script)} -h stdout differs from --help\n{diagnostic}"
    assert short.stderr == long.stderr, f"{_script_id(script)} -h stderr differs from --help\n{diagnostic}"
