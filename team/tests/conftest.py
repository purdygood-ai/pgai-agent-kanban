"""
conftest.py — shared pytest fixtures for pgai-agent-kanban tests.

Provides:
  tmp_kanban_root  — a synthetic kanban directory tree suitable for unit
                     and integration tests. Creates queue files for all
                     known agents, plus logs, locks, plans directories,
                     a release-state.md file, and team/workflows/ with
                     workflow plugin directories (release/, document/) so the
                     materializer can validate workflow types.

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

Loopback test-client host
-------------------------
All tests are wrapped by an autouse _loopback_test_client_host fixture that
gives every ``TestClient`` built without an explicit ``base_url`` a loopback
one (``http://127.0.0.1``) in place of Starlette's ``http://testserver``.
The operator API refuses any request whose Host header is not a loopback
name, and it carries no allowance for a test host; pointing the client at a
loopback name is the one fixture-level adjustment that keeps the suite
running against the production allow-list.  A test that passes its own
``base_url`` (to send a deliberately foreign Host, say) is left alone.

Live projects/ guard
--------------------
Session-scoped hooks (pytest_sessionstart / pytest_sessionfinish) record the
names under the invoking shell's $PGAI_AGENT_KANBAN_ROOT_PATH/projects/
before any test runs and fail the run (exit 1), naming each one, when the
listing gained an entry that was not there at start and that the root's
projects.cfg does not register. Pre-existing entries and projects registered
while the suite runs are never reported. See the section's own comment
below.

Crontab sandbox (an earlier defect)
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
import shutil
import stat
import subprocess
import sys
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

    Structural guard (the crontab-sandbox hardening (phase 1))
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
    misdirected).  Tests that hardcode /tmp paths (the same hardening's phase-2 scope)
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


# ---------------------------------------------------------------------------
# Deliberate-order seam for the order-coupling self-test
# ---------------------------------------------------------------------------
#
# team/scripts/verify-test-order-independence.sh drives this hook to run the
# collected estate in an order other than pytest's own default (import/file
# discovery order), so that its two runs can be diffed for verdict
# differences without depending on a third-party ordering plugin.
#
# PGAI_TEST_ORDER accepted values (unset/anything else is a no-op — the
# default collection order pytest would have used regardless). Both
# recognized values first canonicalise the collected item list (sort it by
# nodeid) before reordering it, so the result depends only on the set of
# collected tests, never on what order an earlier collection-hook plugin
# (pytest-randomly is the one observed in practice) already put them in:
#   reverse        — canonicalise, then reverse the list end-to-end.
#   shuffle:<seed> — canonicalise, then deterministically shuffle the list
#                    using stdlib random.Random(<seed>), so a specific
#                    divergence can be reproduced by re-running with the
#                    same seed and no other flag — the canonicalisation
#                    step is what makes that reproduction hold even when
#                    pytest-randomly has already reordered the list before
#                    this hook runs.
_PGAI_TEST_ORDER_ENV_VAR = "PGAI_TEST_ORDER"


def pytest_collection_modifyitems(config, items):  # noqa: ARG001
    """Reorder the collected test items when PGAI_TEST_ORDER requests it.

    This never runs for a normal invocation (the env var is unset), so it
    changes nothing about how run-unit-tests.sh / run-integration-tests.sh
    behave today. It exists solely so the order-coupling self-test can
    request a materially different execution order, and so an engineer
    chasing an order-coupled failure can reproduce a specific divergence by
    re-running with the same shuffle seed — using only pytest's own
    collection hook and the standard library, no new test dependency.

    Both recognized values canonicalise the list (sort by nodeid) before
    reordering it. Without that step, the requested order is a function of
    whatever order the items arrived in — and when a third-party ordering
    plugin such as pytest-randomly is active, that arrival order changes on
    every run, so the same seed would produce a different permutation each
    time and "reverse" would not mean the reverse of anything stable.
    """
    order = os.environ.get(_PGAI_TEST_ORDER_ENV_VAR, "").strip()
    if not order:
        return
    if order != "reverse" and not order.startswith("shuffle:"):
        # Unrecognized value: leave collection order untouched rather than
        # guessing what the caller meant.
        return
    items.sort(key=lambda item: item.nodeid)
    if order == "reverse":
        items.reverse()
        return
    import random

    seed = order.split(":", 1)[1]
    random.Random(seed).shuffle(items)


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

# Explicit allowlist of workflow plugin directories. Intent: these are the workflow
# types that pm_materialize.py needs to validate workflow_type values. Listed
# explicitly (rather than globbing) so that a new workflow type added to
# team/workflows/ does not silently enter fixture scope and break tests that
# cannot accommodate an unknown workflow type.
#
# Each entry is a subdirectory of _REAL_WORKFLOWS_DIR containing at minimum
# workflow.cfg and workflow.sh.  Types that carry a pipeline.yaml (release,
# document) also have their pipeline seeded into the test root so load_workflow()
# can resolve them without per-test setup.
# testing-only is included so that capability-gated materializer tests can
# call load_workflow_capabilities('testing-only') without manual fixture setup.
_WORKFLOW_PLUGIN_DIRS_FOR_TESTS = ["release", "document", "testing-only"]


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

    # --- team/workflows/ — copy workflow plugin directories so the materializer
    # can validate workflow_type when called from integration and unit tests.
    # load_workflow() searches team/workflows/<type>/pipeline.yaml (canonical)
    # constructed from the type string.  We copy the real plugin directories.
    team_workflows_dir = kanban_root / "team" / "workflows"
    team_workflows_dir.mkdir(parents=True, exist_ok=True)
    for plugin_name in _WORKFLOW_PLUGIN_DIRS_FOR_TESTS:
        src_plugin = _REAL_WORKFLOWS_DIR / plugin_name
        if src_plugin.is_dir():
            dest_plugin = team_workflows_dir / plugin_name
            if dest_plugin.exists():
                shutil.rmtree(dest_plugin)
            shutil.copytree(src_plugin, dest_plugin)

    return kanban_root


def _copy_workflow_plugins(dest_root: pathlib.Path) -> None:
    """Copy workflow plugin directories into *dest_root/workflows/<type>/*.

    Called by fixtures that need wf_load_plugin to succeed when
    PGAI_AGENT_KANBAN_ROOT_PATH is redirected to a temp root.  Each plugin
    directory contains workflow.cfg, workflow.sh, and (for release/document)
    pipeline.yaml.  The dispatcher (wf_load_plugin) finds them at
    $PGAI_AGENT_KANBAN_ROOT_PATH/workflows/<type>/ (project-local flat override
    path); load_workflow() also searches this location.
    """
    wf_dir = dest_root / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    for plugin_name in _WORKFLOW_PLUGIN_DIRS_FOR_TESTS:
        src_plugin = _REAL_WORKFLOWS_DIR / plugin_name
        if src_plugin.is_dir():
            dest_plugin = wf_dir / plugin_name
            if dest_plugin.exists():
                shutil.rmtree(dest_plugin)
            shutil.copytree(src_plugin, dest_plugin)


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

    KANBAN_ROOT: the legacy bash-only alias every team/scripts/lib/
    project_paths.sh function reads directly (never PGAI_AGENT_KANBAN_ROOT_PATH
    -- see project_paths.sh's _pp_kanban_root) is redirected here too. It is
    ambient in a real agent's own shell (exported by shell-env, inherited by
    every subprocess including a pytest run), so leaving it untouched would
    let any bash-side test that omits its own explicit KANBAN_ROOT override --
    most notably a refusal recorded through team/scripts/lib/refusal_ledger.sh
    -- silently resolve to and overwrite the live install's
    projects/<project>/refusal-ledger.md. Redirecting it here, alongside
    PGAI_AGENT_KANBAN_ROOT_PATH above, closes that gap the same way for every
    test rather than requiring each one to remember its own override.

    PGAI_PROJECT_ROOT: removed, not repointed. A wake exports it as the live
    install's projects/<project> directory, and it outranks both root names
    for every helper that honours it, so a sandbox that repoints the roots
    but inherits it still resolves the operator's project folder -- the
    sandbox recipe clears it (projects/pgai-agent-kanban/README.md,
    "Live-install safety"). A test that exercises it sets its own value.

    PGAI_DEV_TREE_PATH: removed, not repointed. The gated runners export it
    as the operator's real checkout, whose origin is the real remote, so a
    subprocess that inherits it can run git against that checkout -- an
    OVERWATCH sweep check that falls back to it would ls-remote and push the
    checkout's tags. Removing it leaves code that needs the source tree to
    self-locate from its own file (each tree finds itself), and leaves a
    script that needs a dev tree to read it from the sandbox's own config. A
    test that exercises dev-tree resolution sets its own throwaway path with
    monkeypatch.setenv or a subprocess env, on top of this removal.
    """
    safe_root = tmp_path / "_default_kanban_safe_root"
    safe_root.mkdir(parents=True, exist_ok=True)

    # Seed workflow plugin directories in safe_root/workflows/ so that:
    #   - load_workflow() can resolve workflow_type values like "release" and
    #     "document" without per-test setup (pipeline.yaml is inside the plugin dir)
    #   - wf_load_plugin succeeds when PGAI_AGENT_KANBAN_ROOT_PATH is redirected
    #     to safe_root in shell tests (workflow.cfg / workflow.sh are in the dir)
    # This mirrors the live install layout where install.sh copies team/workflows/
    # (including plugin subdirs) to $KANBAN_ROOT/workflows/.
    _copy_workflow_plugins(safe_root)

    monkeypatch.setenv("PGAI_TASKS_DIR", str(safe_root / "tasks"))
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(safe_root))
    monkeypatch.setenv("KANBAN_ROOT", str(safe_root))
    monkeypatch.delenv("PGAI_QUEUE_DIR", raising=False)
    monkeypatch.delenv("PGAI_RELEASE_STATE_PATH", raising=False)
    monkeypatch.delenv("PGAI_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("PGAI_DEV_TREE_PATH", raising=False)


# ---------------------------------------------------------------------------
# Live projects/ guard: session-scoped
# ---------------------------------------------------------------------------
#
# _block_live_kanban_writes above redirects the root names for every test,
# but only for the test's own call: collection-time code, a wide-scoped
# fixture, or a subprocess handed an environment snapshot taken before the
# redirect can still resolve the operator's live root. The guard below is
# the backstop for the one write class that is never legitimate from a
# test: a new entry directly under the live $PGAI_AGENT_KANBAN_ROOT_PATH/
# projects/ (a refusal ledger for a fixture project, an empty project
# folder).
#
#   - pytest_sessionstart records the root the invoking shell named
#     (PGAI_AGENT_KANBAN_ROOT_PATH, then the KANBAN_ROOT alias -- the same
#     order the gated runners' sentinel snapshot uses) and the names under
#     its projects/ at that moment, before any test runs.
#   - pytest_sessionfinish lists projects/ again. A name present at start is
#     never reported, and neither is a new name the root's projects.cfg now
#     registers -- an operator registering a project while the suite runs
#     is a legitimate concurrent action, not a leak. Every other new name
#     fails the run (exit 1), each one named.
#
# Under pytest-xdist only the controller runs it: it brackets every
# worker, so one report covers the session. With no root named at all the
# guard has nothing to watch and stays inactive. It reports and never
# deletes: removing a leaked folder is the operator's call.

_LIVE_PROJECTS_GUARD_KEY = pytest.StashKey[dict]()


def live_projects_guard_root() -> pathlib.Path | None:
    """The kanban root the invoking shell names, or None when it names none."""
    raw = (
        os.environ.get("PGAI_AGENT_KANBAN_ROOT_PATH", "").strip()
        or os.environ.get("KANBAN_ROOT", "").strip()
    )
    return pathlib.Path(raw) if raw else None


def live_projects_listing(root: pathlib.Path) -> set[str]:
    """Names directly under *root*/projects/ (empty when it does not exist)."""
    try:
        return {entry.name for entry in (root / "projects").iterdir()}
    except (FileNotFoundError, NotADirectoryError):
        return set()


def _registered_project_names(root: pathlib.Path) -> set[str]:
    """Projects *root*/projects.cfg registers; empty when absent or unreadable."""
    try:
        from pgai_agent_kanban.lib.ini import projects_registry

        return {entry.name for entry in projects_registry(root)}
    except Exception:  # noqa: BLE001 - an unreadable registry explains nothing
        return set()


def unexplained_live_project_entries(root: pathlib.Path, before: set[str]) -> list[str]:
    """New names under *root*/projects/ since *before* that projects.cfg
    does not register, sorted. Pre-existing names and newly registered
    projects are explained; everything else is a leak."""
    added = live_projects_listing(root) - set(before)
    return sorted(added - _registered_project_names(root))


def live_projects_guard_report(root: pathlib.Path, entries: list[str]) -> str:
    """The dual-channel refusal text for *entries* leaked under *root*."""
    from pgai_agent_kanban.lib.error_contract import format_error_contract

    paths = [str(root / "projects" / name) for name in entries]
    return format_error_contract(
        "tests.live_projects_leak",
        "this test run created "
        f"{len(entries)} unregistered entr{'y' if len(entries) == 1 else 'ies'} under the "
        f"live kanban root's projects/ ({root}): {', '.join(entries)}. A test or a tool "
        "it ran resolved the live root instead of its sandbox; run the test that "
        "writes them through the sandbox recipe (docs/test-authoring-guidelines.md "
        "'Live-install safety'). Nothing was removed.",
        f"ls -la --time-style=full-iso {' '.join(paths)}",
    )


def pytest_sessionstart(session):
    """Record the live projects/ listing before any test runs (see above)."""
    if hasattr(session.config, "workerinput"):
        return
    root = live_projects_guard_root()
    if root is None:
        return
    session.config.stash[_LIVE_PROJECTS_GUARD_KEY] = {
        "root": root,
        "before": live_projects_listing(root),
        "leaked": [],
    }


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    """Fail the run, naming each unexplained new entry under live projects/."""
    state = session.config.stash.get(_LIVE_PROJECTS_GUARD_KEY, None)
    if not state:
        return
    leaked = unexplained_live_project_entries(state["root"], state["before"])
    if not leaked:
        return
    state["leaked"] = leaked
    # CENSUS CLASS BLOCK-WORTHY (docs/blocking-site-census.md): a suite that
    # wrote into the operator's live install is not an isolated verification
    # run; its green cannot stand. Reported, never repaired.
    # CENSUS-ANCHOR: tests-conftest-live-projects-leak-01
    session.exitstatus = pytest.ExitCode.TESTS_FAILED
    if session.config.pluginmanager.get_plugin("terminalreporter") is None:
        sys.stderr.write(live_projects_guard_report(state["root"], leaked) + "\n")


def pytest_terminal_summary(terminalreporter, exitstatus, config):  # noqa: ARG001
    """Print the live projects/ guard's report in the run's summary."""
    state = config.stash.get(_LIVE_PROJECTS_GUARD_KEY, None)
    if not state or not state["leaked"]:
        return
    terminalreporter.section("live kanban root projects/ guard", sep="=", red=True)
    for line in live_projects_guard_report(state["root"], state["leaked"]).splitlines():
        terminalreporter.line(line, red=True)


# ---------------------------------------------------------------------------
# Crontab sandbox (crontab-clobber defense)
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
    test, and set PGAI_CRONTAB_CMD to point at the same stub (crontab sandboxing —
     stop tests from clobbering the real operator crontab).

    Two complementary protection layers are applied:

    1. PATH shadowing (an earlier defect): the stub binary is prepended to PATH so that
       any subprocess which discovers 'crontab' via PATH resolution picks up the
       stub.  Tests that use subprocess.run(..., env=...) with a dict built from
       os.environ inherit the redirected PATH automatically.

    2. PGAI_CRONTAB_CMD seam (an earlier defect): safe_overwrite.sh's _run_crontab()
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

    # Layer 2 (an earlier defect): set PGAI_CRONTAB_CMD so that safe_overwrite.sh's
    # _run_crontab() calls the stub directly, bypassing PATH resolution.
    # This is the default seam engagement that was missing before an earlier defect.
    monkeypatch.setenv("PGAI_CRONTAB_CMD", str(stub_path))


# ---------------------------------------------------------------------------
# Loopback test-client host: autouse fixture
# ---------------------------------------------------------------------------

_LOOPBACK_TEST_CLIENT_BASE_URL = "http://127.0.0.1"


@pytest.fixture(autouse=True)
def _loopback_test_client_host(monkeypatch):
    """
    Autouse fixture: default every ``TestClient`` to a loopback ``base_url``.

    Starlette's ``TestClient`` defaults ``base_url`` to ``http://testserver``,
    so every request it sends carries ``Host: testserver``.  The operator API
    refuses a non-loopback Host on every route (the DNS-rebinding guard in
    ``pgai_agent_kanban.api.app``) and deliberately carries no allowance for
    a test host -- widening the allow-list is the exposure the guard closes.
    This fixture makes the client honest instead: a ``TestClient`` built
    without an explicit ``base_url`` gets ``http://127.0.0.1``, the same Host
    an operator's curl or browser sends, so the suite exercises the
    production allow-list as-is.

    Only the default is changed.  A client constructed with its own
    ``base_url`` -- positionally or by keyword -- keeps it, which is how a
    test sends a deliberately foreign Host to prove the refusal.  A
    ``Host`` header passed per-request also still wins over ``base_url``.

    The import is deferred and the fixture is a no-op when FastAPI is not
    installed, so bash-only tests never pay for the dependency.
    """
    try:
        from fastapi.testclient import TestClient
    except ImportError:  # pragma: no cover - API extras absent
        yield
        return

    original_init = TestClient.__init__

    def _init_with_loopback_default(self, *args, **kwargs):
        # Signature: TestClient(app, base_url=..., ...) -- base_url is the
        # second positional parameter, so a second positional arg is an
        # explicit base_url and is respected.
        if len(args) < 2 and "base_url" not in kwargs:
            kwargs["base_url"] = _LOOPBACK_TEST_CLIENT_BASE_URL
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(TestClient, "__init__", _init_with_loopback_default)
    yield
