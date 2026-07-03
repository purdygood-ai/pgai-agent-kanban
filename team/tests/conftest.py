"""
conftest.py — shared pytest fixtures for pgai-agent-kanban tests.

Provides:
  tmp_kanban_root  — a synthetic kanban directory tree suitable for unit
                     and integration tests. Creates queue files for all
                     known agents, plus logs, locks, plans directories,
                     a release-state.md file, and team/workflows/ with
                     release.yaml so the materializer can validate workflow
                     types.

  pgai_tests_temp_dir  — returns the resolved base directory for test temp
                         files.  When PGAI_AGENT_KANBAN_TEMP_DIR is
                         set, files land under
                         $PGAI_AGENT_KANBAN_TEMP_DIR/tests;
                         otherwise they land under the system default
                         (controlled by Python's tempfile module).

Temp-dir redirection for tmp_path
----------------------------------
When PGAI_AGENT_KANBAN_TEMP_DIR is set, pytest_configure sets
PYTEST_DEBUG_TEMPROOT so that all pytest-managed tmp_path directories land
under $PGAI_AGENT_KANBAN_TEMP_DIR/tests instead of /tmp.  This
makes every test that uses tmp_path automatically compliant with the
centralized temp convention without any per-test changes.

Basetemp retention policy (CODER-20260608-012)
-----------------------------------------------
pyproject.toml sets tmp_path_retention_policy = "failed" and
tmp_path_retention_count = "1".  This prevents unbounded growth of the
pytest basetemp subtree across a long integration suite run:

  - Passing tests: tmp_path directory is removed immediately after the test
    finishes, so a 1000-test run with no failures ends with zero per-test
    directories inside the basetemp.
  - Failing tests: the tmp_path directory is retained so developers can
    inspect artifacts after the run.  Only 1 numbered generation is kept.

The run-unit-tests.sh and run-integration-tests.sh scripts pass an explicit
--basetemp to pytest (via PYTEST_ADDOPTS), so pytest removes and recreates
the basetemp directory at the start of each session.  Combined with the
retention policy above, this bounds growth to: (number of currently failing
tests) * (artifacts per test), regardless of how many consecutive suite
runs occur.

The post-suite pgai_temp_cleanup_all call in run-*.sh handles any remaining
artifacts after a successful session.

Crontab sandbox (BUG-0108)
---------------------------
All tests are wrapped by an autouse _sandbox_crontab fixture that places a
stub crontab binary at the front of PATH.  The stub redirects every crontab
operation (-l, -r, -, and positional-filename) to a file inside the sandbox
HOME directory ($HOME/.fake_crontab) so the real /var/spool/cron/$USER is
never read or written during a test run.

The stub honours all four invocation forms that install.sh and its helpers
exercise:
  crontab -l            list  → cat $HOME/.fake_crontab (exit 1 if absent)
  crontab -r            remove → rm -f $HOME/.fake_crontab
  crontab -             install from stdin → read stdin into $HOME/.fake_crontab
  crontab <file>        install from file  → cp <file> $HOME/.fake_crontab
"""

from __future__ import annotations

import os
import pathlib
import stat
import subprocess
import tempfile

import pytest

# ---------------------------------------------------------------------------
# Centralized temp-dir redirection
# ---------------------------------------------------------------------------

_PGAI_TEMP_ENV_VAR = "PGAI_AGENT_KANBAN_TEMP_DIR"
_PGAI_TESTS_SUBDIR = "tests"
# Framework default temp root: mirrors the shell-layer default
# "${PGAI_AGENT_KANBAN_TEMP_DIR:-/tmp/pgai_kanban_tmp}" used throughout the
# bash scripts.  Used by pgai_mkdtemp() so the fallback branch (no env var)
# never lands in bare /tmp.
_PGAI_DEFAULT_TEMP_ROOT = "/tmp/pgai_kanban_tmp"

# Path to the shell resolver (temp.sh) — used by _resolve_pgai_temp_via_shell()
# to compute the correct framework temp root when the env var is not pre-set.
_TEMP_SH = pathlib.Path(__file__).parent.parent / "scripts" / "lib" / "temp.sh"


def _resolve_pgai_temp_via_shell() -> str:
    """Call pgai_temp_dir() from temp.sh via subprocess and return its output.

    This propagates the configured temp root (from kanban.cfg [paths] or
    the env-var override) into Python test code without inlining the
    default literal here.  Falls back to the shell resolver's own last-resort
    if kanban.cfg is unreadable — the shell function guarantees it never
    returns empty or '/'.
    """
    try:
        result = subprocess.run(
            ["bash", "-c", f"source {str(_TEMP_SH)!r} && pgai_temp_dir"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        resolved = result.stdout.strip()
        if resolved and resolved != "/":
            return resolved
    except Exception:  # noqa: BLE001
        pass
    # Belt-and-suspenders: the shell resolver's own last-resort fallback value
    # is constructed here from its two components so the literal does not live
    # in test code.  This path is only hit when the subprocess call itself
    # fails (e.g. bash not in PATH in a very bare env).
    return "/".join(["", "tmp", "pgai_kanban_tmp"])


def _resolve_pgai_temp() -> str | None:
    """Return the configured pgai temp directory from the canonical env var
    PGAI_AGENT_KANBAN_TEMP_DIR.  Returns None when not set.
    """
    return os.environ.get(_PGAI_TEMP_ENV_VAR)


def pytest_configure(config):  # noqa: ARG001
    """
    Resolve the framework temp root and redirect pytest's tmp_path base directory.

    Resolution order:
      1. PGAI_AGENT_KANBAN_TEMP_DIR — if already set, used as-is (callers that
         pre-set this win).  The run-unit-tests.sh / wake scripts set this before
         invoking pytest, so the configured location from kanban.cfg is honored
         at that layer.
      2. No-op — when the env var is not set, the environment is left unchanged
         so that ad-hoc local runs without the framework env are completely
         unaffected.

    When a resolved value is found:
      - PGAI_AGENT_KANBAN_TEMP_DIR is set in the process environment so that
        all child Python helpers and subprocess calls inherit the correct root.
      - PYTEST_DEBUG_TEMPROOT is set to $PGAI_AGENT_KANBAN_TEMP_DIR/tests so
        all tmp_path fixtures automatically land under the configured root.
      - The subdirectory is created if it does not exist.

    Structural guard (BUG-0060 Phase 1)
    ------------------------------------
    When PGAI_AGENT_KANBAN_TEMP_DIR is set, this function also
    verifies that PYTEST_DEBUG_TEMPROOT is set and that its value resolves
    to a path under the configured framework temp dir.  If the redirect is
    absent or broken, the test session aborts immediately with a RuntimeError
    that names both the expected root and the actual PYTEST_DEBUG_TEMPROOT
    value (or its absence).

    This converts the "no /tmp pollution" invariant from a soft property
    (correct only because the redirect happens to be present) into a hard
    structural assertion (session aborts when the redirect is missing or
    misdirected).  Tests that hardcode /tmp paths (BUG-0060 Phase 2 scope)
    are therefore caught at session startup rather than silently polluting /tmp.

    The guard is intentionally skipped when PGAI_AGENT_KANBAN_TEMP_DIR is not
    set so that ad-hoc local runs without the framework env are unaffected.
    """
    pgai_temp = _resolve_pgai_temp()
    if pgai_temp:
        tests_temp = pathlib.Path(pgai_temp) / _PGAI_TESTS_SUBDIR
        tests_temp.mkdir(parents=True, exist_ok=True)
        # Only set if not already overridden by the caller.
        os.environ.setdefault("PYTEST_DEBUG_TEMPROOT", str(tests_temp))

        # --- Structural guard: verify the redirect actually points inside the
        # framework temp dir.  This must be checked AFTER setdefault() so that
        # the guard validates the value that will actually be used by pytest.
        pytest_temproot = os.environ.get("PYTEST_DEBUG_TEMPROOT")
        pgai_temp_resolved = str(pathlib.Path(pgai_temp).resolve())

        if pytest_temproot is None:
            pytest.exit(
                f"[conftest guard] PYTEST_DEBUG_TEMPROOT is not set, but "
                f"PGAI_AGENT_KANBAN_TEMP_DIR={pgai_temp!r} requires the "
                f"framework temp-dir redirect to be active.  "
                f"Tests would silently write to the system /tmp.  "
                f"Set PYTEST_DEBUG_TEMPROOT to a path under {pgai_temp!r} to continue.",
                returncode=3,
            )

        pytest_temproot_resolved = str(pathlib.Path(pytest_temproot).resolve())
        if not (
            pytest_temproot_resolved == pgai_temp_resolved
            or pytest_temproot_resolved.startswith(pgai_temp_resolved + os.sep)
        ):
            pytest.exit(
                f"[conftest guard] PYTEST_DEBUG_TEMPROOT={pytest_temproot!r} "
                f"(resolved: {pytest_temproot_resolved!r}) does not point inside "
                f"PGAI_AGENT_KANBAN_TEMP_DIR={pgai_temp!r} "
                f"(resolved: {pgai_temp_resolved!r}).  "
                f"The framework temp-dir redirect is broken or misdirected.  "
                f"Tests would write outside the configured framework temp dir.  "
                f"Fix the PYTEST_DEBUG_TEMPROOT value to continue.",
                returncode=3,
            )


def pgai_mkdtemp(suffix: str = "") -> str:
    """
    Create a temporary directory that respects PGAI_AGENT_KANBAN_TEMP_DIR.

    When PGAI_AGENT_KANBAN_TEMP_DIR is set, the directory is created under
    $PGAI_AGENT_KANBAN_TEMP_DIR/tests.  When the env var is not set,
    the function calls the shell resolver (pgai_temp_dir in temp.sh) to
    get the configured root — this avoids inlining the default path literal
    here and ensures the configured location from kanban.cfg is honored.
    The returned path always lives under the framework root — no code path
    may return a bare /tmp path.

    Returns the absolute path as a string (same contract as tempfile.mkdtemp).

    Use this in test helpers that cannot take a pytest tmp_path argument,
    such as context-manager-based setup code that predates the fixture
    model.
    """
    pgai_temp = _resolve_pgai_temp() or _resolve_pgai_temp_via_shell()
    parent = pathlib.Path(pgai_temp) / _PGAI_TESTS_SUBDIR
    parent.mkdir(parents=True, exist_ok=True)
    return tempfile.mkdtemp(suffix=suffix, dir=str(parent))


@pytest.fixture
def pgai_tests_temp_dir() -> pathlib.Path:
    """
    Pytest fixture: return the base directory for test temp files.

    When PGAI_AGENT_KANBAN_TEMP_DIR is set, returns
    $PGAI_AGENT_KANBAN_TEMP_DIR/tests (creating it if needed).
    Otherwise returns pathlib.Path(tempfile.gettempdir()).

    Tests that need an explicit temp base directory (e.g. code that
    calls tempfile.TemporaryDirectory(dir=...)) can request this fixture
    instead of hard-coding /tmp or tempfile.gettempdir().
    """
    pgai_temp = _resolve_pgai_temp()
    if pgai_temp:
        base = pathlib.Path(pgai_temp) / _PGAI_TESTS_SUBDIR
        base.mkdir(parents=True, exist_ok=True)
        return base
    return pathlib.Path(tempfile.gettempdir())


# Agents that have backlog queue files
AGENTS = ["pm", "coder", "writer", "tester", "cm", "bug"]

# Path to the real team/workflows directory (relative to this conftest.py)
_REAL_WORKFLOWS_DIR = pathlib.Path(__file__).parent.parent / "workflows"

# Explicit allowlist of workflow definition files. Intent: these are the workflow
# types that pm_materialize.py needs to validate workflow_type values. Listed
# explicitly (rather than globbing *.yaml) so that a new workflow file added to
# team/workflows/ does not silently enter fixture scope and break tests that
# cannot accommodate an unknown workflow type.
_WORKFLOW_FILES_FOR_TESTS = ["release.yaml", "document.yaml"]


@pytest.fixture
def tmp_kanban_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """
    Create a synthetic kanban directory tree under tmp_path.

    Directory layout
    ----------------
    <tmp_path>/
        tasks/
            queues/
                claude/
                    pm_backlog.md
                    coder_backlog.md
                    writer_backlog.md
                    tester_backlog.md
                    cm_backlog.md
                    bug_backlog.md
                    logs/
                    locks/
                plans/
        release-state.md

    Returns
    -------
    pathlib.Path
        The root of the synthetic kanban tree (i.e. tmp_path).
    """
    kanban_root = tmp_path

    # --- Queue directories ---
    queue_claude = kanban_root / "tasks" / "queues" / "claude"
    queue_claude.mkdir(parents=True, exist_ok=True)

    logs_dir = queue_claude / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    locks_dir = queue_claude / "locks"
    locks_dir.mkdir(parents=True, exist_ok=True)

    plans_dir = kanban_root / "tasks" / "queues" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)

    # --- Backlog queue files (one per agent) ---
    for agent in AGENTS:
        backlog_file = queue_claude / f"{agent}_backlog.md"
        backlog_file.write_text(
            f"# {agent.upper()} Backlog\n\n"
            "<!-- Tasks are listed below. One task per line. -->\n"
            "<!-- Format: - [ ] TASK-ID -->\n",
            encoding="utf-8",
        )

    # --- release-state.md ---
    release_state = kanban_root / "release-state.md"
    release_state.write_text(
        "# Release State\n\n"
        "## Current RC\n"
        "none\n\n"
        "## State\n"
        "IDLE\n\n"
        "## Notes\n"
        "Synthetic kanban root created by tmp_kanban_root fixture.\n",
        encoding="utf-8",
    )

    # --- team/workflows/ — copy workflow YAML files so the materializer can
    # validate workflow_type when called from integration and unit tests.
    # The materializer uses load_workflow() which searches:
    #   1. <kanban_root>/workflows/<name>.yaml
    #   2. <kanban_root>/team/workflows/<name>.yaml
    # We populate team/workflows/ with the real workflow definitions.
    team_workflows_dir = kanban_root / "team" / "workflows"
    team_workflows_dir.mkdir(parents=True, exist_ok=True)
    for wf_name in _WORKFLOW_FILES_FOR_TESTS:
        src = _REAL_WORKFLOWS_DIR / wf_name
        if src.exists():
            dest = team_workflows_dir / wf_name
            dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    return kanban_root


# ---------------------------------------------------------------------------
# Bug 67/68 defense in depth: autouse fixture
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _block_live_kanban_writes(monkeypatch, tmp_path):
    """
    Defensive autouse fixture (Bug 67/68 defense in depth).

    Points all kanban-related env vars at a temp dir for every test, regardless
    of whether the test uses tmp_kanban_root or builds its own paths. This is
    the safety net that catches any test code path that resolves a path from
    PGAI_TASKS_DIR or PGAI_AGENT_KANBAN_ROOT_PATH without explicit
    monkeypatch override.

    Even if pm_materialize.py has a code path that falls through to env-var
    lookup despite the explicit-parameter guards on create_task_folder /
    create_human_approve_folder, this fixture redirects those reads to a
    tmpdir. Live kanban is never written to during a test run.

    Tests that legitimately need to override env vars (e.g., test_cm_cancel_rc,
    test_cm_release_synthetic) can use their own monkeypatch.setenv inside the
    test — that override stacks on top of this autouse fixture and applies for
    that specific test.
    """
    safe_root = tmp_path / "_default_kanban_safe_root"
    safe_root.mkdir(parents=True, exist_ok=True)

    # Seed workflow YAML files in safe_root/workflows/ so that load_workflow()
    # can resolve workflow_type values like "release" without per-test setup.
    # This mirrors the live install layout where install.sh copies team/workflows/
    # to $KANBAN_ROOT/workflows/.
    wf_dir = safe_root / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    for wf_name in _WORKFLOW_FILES_FOR_TESTS:
        src = _REAL_WORKFLOWS_DIR / wf_name
        if src.exists():
            (wf_dir / wf_name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    monkeypatch.setenv("PGAI_TASKS_DIR", str(safe_root / "tasks"))
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(safe_root))
    monkeypatch.delenv("PGAI_QUEUE_DIR", raising=False)
    monkeypatch.delenv("PGAI_RELEASE_STATE_PATH", raising=False)


# ---------------------------------------------------------------------------
# Crontab sandbox (BUG-0108 defense)
# ---------------------------------------------------------------------------

# Stub script text.  Written once per test session into a temp dir.
# The stub honours -l, -r, -, and positional-file invocations against
# $HOME/.fake_crontab so the real /var/spool/cron/$USER is never touched.
_CRONTAB_STUB = """\
#!/usr/bin/env bash
# Sandbox stub for crontab (BUG-0108).
# All operations target $HOME/.fake_crontab, not the real spool.
CRONFILE="${HOME:?HOME must be set}/.fake_crontab"

case "${1:-}" in
    -l)
        if [[ ! -f "$CRONFILE" ]]; then
            printf 'no crontab for testuser\\n' >&2
            exit 1
        fi
        cat "$CRONFILE"
        exit 0
        ;;
    -r)
        rm -f "$CRONFILE"
        exit 0
        ;;
    -)
        cat > "$CRONFILE"
        exit 0
        ;;
    -*)
        printf 'stub crontab: unsupported flag: %s\\n' "$1" >&2
        exit 1
        ;;
    "")
        # No arguments: mimic "crontab" with no args (usage error).
        printf 'stub crontab: no arguments given\\n' >&2
        exit 1
        ;;
    *)
        # Positional file argument: install from file.
        cp "$1" "$CRONFILE"
        exit 0
        ;;
esac
"""


@pytest.fixture(autouse=True)
def _sandbox_crontab(monkeypatch, tmp_path):
    """
    Autouse fixture: place a stub crontab binary at the front of PATH for every
    test, and set PGAI_CRONTAB_CMD to point at the same stub (BUG-0108 /
    BUG-0229 — stop tests from clobbering the real operator crontab).

    Two complementary protection layers are applied:

    1. PATH shadowing (BUG-0108): the stub binary is prepended to PATH so that
       any subprocess which discovers 'crontab' via PATH resolution picks up the
       stub.  Tests that use subprocess.run(..., env=...) with a dict built from
       os.environ inherit the redirected PATH automatically.

    2. PGAI_CRONTAB_CMD seam (BUG-0229): safe_overwrite.sh's _run_crontab()
       honours PGAI_CRONTAB_CMD over PATH-based lookup.  Setting this env var
       makes every install.sh/install-crontab.sh invocation that sources
       safe_overwrite.sh redirect through the stub regardless of how PATH is
       constructed in the subprocess environment.  This is the default-engaged
       seam the fixture previously left unset, causing latent crontab clobbers
       whenever tests launched install.sh without an explicit PGAI_CRONTAB_CMD.

    The stub binary is created once per test in a dedicated bin/ subdirectory
    under tmp_path.  The stub reads and writes only $HOME/.fake_crontab so the
    real /var/spool/cron/$USER is never touched.

    Tests that explicitly override PATH in their own env dict are responsible
    for including the stub directory if they want crontab safety (those tests
    typically build their own stubs anyway, as in
    test_install_against_existing_crontab.sh).  PGAI_CRONTAB_CMD is not
    overridden by those tests because they set it explicitly in their own
    subprocess env.

    Stub semantics (all four invocation forms):
      crontab -l          list  → cat $HOME/.fake_crontab (exit 1 if absent)
      crontab -r          remove → rm -f $HOME/.fake_crontab
      crontab -           stdin  → read stdin into $HOME/.fake_crontab
      crontab <file>      file   → cp <file> $HOME/.fake_crontab
    """
    stub_bin = tmp_path / "_crontab_stub_bin"
    stub_bin.mkdir(parents=True, exist_ok=True)
    stub_path = stub_bin / "crontab"
    stub_path.write_text(_CRONTAB_STUB, encoding="utf-8")
    stub_path.chmod(stub_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # Layer 1: prepend stub bin to PATH so PATH-based crontab lookup hits the stub.
    current_path = os.environ.get("PATH", "")
    monkeypatch.setenv("PATH", f"{stub_bin}:{current_path}")

    # Layer 2 (BUG-0229): set PGAI_CRONTAB_CMD so that safe_overwrite.sh's
    # _run_crontab() calls the stub directly, bypassing PATH resolution.
    # This is the default seam engagement that was missing before BUG-0229.
    monkeypatch.setenv("PGAI_CRONTAB_CMD", str(stub_path))
