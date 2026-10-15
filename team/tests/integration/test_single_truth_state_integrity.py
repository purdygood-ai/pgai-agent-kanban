"""
test_single_truth_state_integrity.py
=====================================
Closing integration coverage for the v2.11.0 "single truth" release: the
five acceptance fixtures named in the release's requirements Acceptance
section, exercised together against ONE kanban tree rather than five
unrelated ad hoc trees, so a future regression that breaks the compound
guarantee (not just one isolated function) trips here.

The five fixtures (one test class each, same shared tree):
  1. TestStaleMarkerLetsDiscoveryProceed — a stale non-terminal queue
     marker over a task whose status.md already reads DONE does not block
     the discovery idle gate (discovery.sh's _disc_chain_idle reads
     status.md, never the marker).
  2. TestReleaseStateNoneRefusesLoud — a release-state.md field holding
     the bare string 'NONE' (not the lowercase sentinel 'none') is
     refused by the SAME validator verify-rc-state.sh calls
     (pgai_agent_kanban.cm.read_state_field --validate, invoked through
     the shared team/scripts/lib/pp_run_ops.sh entry point), naming the
     accepted forms and a remedy.
  3. TestUnprovenancedLockIsRefused — pgai_lock_acquire
     (team/scripts/lib/lock_provenance.sh) refuses an acquisition missing
     any of its four provenance fields and takes no lock.
  4. TestZeroProgressCapEscalates — three consecutive zero-progress
     reconciliations of the same orphaned task
     (wake_reconcile_sweeps.sh:reconcile_stale_active) reset on cycles 1-2 and
     escalate to BLOCKED + Needs Human=yes on cycle 3, instead of
     resetting a fourth time.
  5. TestContendedProjectRetriedSameIteration — a project whose repo lock
     is held on the first pass of the multi-project wake iteration and
     free by the time the end-of-iteration retry pass reaches it is
     worked in the same firing (a real `bash scripts/wake/claude.sh`
     subprocess against the tree, since this behaviour lives in the
     multi-project dispatch loop, not in a single callable function).

ONE TREE, NOT FIVE
-------------------
`_build_shared_tree()` builds a single kanban root once per test module
(module-scoped fixture) containing:
  - "single-truth-primary": a release-workflow project (dev_tree_path
    points at a non-existent path, so the wake dispatcher's per-project
    dev-tree gate skips it harmlessly whenever fixture 5's real
    subprocess iterates over it — see team/scripts/lib/wake_project.sh
    _check_project_dev_tree). Fixtures 1-4 read and write directly under
    this project's own tasks/, release-state.md, and the kanban root's
    shared locks/ directory.
  - "single-truth-retry-target" plus three filler document-workflow
    projects (git_mode=none, matching the existing
    test_wake_contention_retry.py convention) — registered alongside
    primary in the SAME projects.cfg, used only by fixture 5's real wake
    subprocess drive.
  - Full, real copies of scripts/, pgai_agent_kanban/, workflows/,
    pm-agent/, and config/ (the same set install.sh mirrors into a live
    install) so every fixture — whether it sources a library function
    directly or drives the real `wake/claude.sh` subprocess entry point —
    exercises byte-identical production code from this one tree, and the
    one-time copy cost is paid once for the whole module rather than once
    per fixture.

Fixtures 1-4 source library functions directly (thin subprocess wrappers,
the same pattern team/tests/integration/test_discovery_idle_gate_reads_
task_status.py and team/tests/unit/test_zero_progress_cycle_cap.py
already use) rather than driving a full wake turn, because each of those
four behaviours is reachable as a single function call and the unit
tickets that implemented them already pin every edge case exhaustively
(team/tests/unit/test_release_state_validators.py,
test_lock_provenance.py, test_zero_progress_cycle_cap.py). Per this
task's own scope-discipline note, this file calls each behaviour once and
asserts the outcome rather than re-testing those internals — a full wake
turn for all five would be substantial, unnecessary machinery. Fixture 5
is the one exception: the retry pass is dispatch-loop-wide behaviour with
no single function to call directly, so it alone drives the real
subprocess entry point (mirroring team/tests/integration/
test_wake_contention_retry.py, which already pins every edge case of the
retry pass itself — this file drives only the single base scenario:
contended on the first pass, free by the retry pass, worked in the same
firing).

RED-CASE PROOF
---------------
Every fixture proves it can fail without
breaking the real tree and reverting. Each fixture below is built so its
own assertion is the discriminating signal: the input is deliberately
contradictory in the one dimension that separates the correct behaviour
from the regression in which a gate reads the marker (e.g. fixture 1's
queue marker deliberately disagrees with its task's terminal status.md
state — a gate that regressed to reading the marker would report NOT
idle, so the passing assertion IS the proof the gate reads status.md).
Fixture 4 additionally demonstrates the "not reset a fourth time" half of
its own claim by observing state == BLOCKED (not BACKLOG) on cycle 3.

RUNTIME
-------
Fixtures 1-4 are cheap (a handful of subprocess calls sourcing shell
functions against small files; well under a second each). Fixture 5 is
the dominant cost: a real multi-project wake firing across 4 registered
projects with genuine OS-level flock contention timing, matching the
existing test_wake_contention_retry.py fixtures' own cost. Measured
wall-clock for this module: ~9-12 seconds total (shared-tree build
dominates fixtures 1-4's setup; fixture 5's subprocess dominates the
rest) — see this task's status.md Summary for the number observed on the
run that produced it.
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess
import textwrap
import time

import pytest

from tests.fixtures.scrubbed_subprocess_env import scrub_wake_tunable_env

# ---------------------------------------------------------------------------
# Paths to the dev tree (three levels up from team/tests/integration/).
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent  # team/

PRIMARY_PROJECT = "single-truth-primary"
RETRY_TARGET_PROJECT = "single-truth-retry-target"
FILLER_PROJECTS = [
    "single-truth-filler-one",
    "single-truth-filler-two",
    "single-truth-filler-three",
]
_RETRY_AGENT = "writer"  # a document-workflow agent; never triggers PM's
# post-loop autonomous discovery block, keeping fixture 5's output limited
# to the multi-project iteration and retry-pass log lines under test.

_WAKE_CLAUDE_REL = pathlib.Path("scripts") / "wake" / "claude.sh"
_SUBPROCESS_TIMEOUT_SECONDS = 60


# ---------------------------------------------------------------------------
# Shared-tree builder
# ---------------------------------------------------------------------------


def _write_release_project(projects_root: pathlib.Path, name: str) -> pathlib.Path:
    """A release-workflow project: project.cfg, release-state.md, the full
    per-agent queue set, and a tasks/ directory — the shape fixtures 1-4
    read and write directly. dev_tree_path deliberately does not exist, so
    the wake dispatcher's dev-tree gate skips this project cleanly whenever
    fixture 5's real subprocess iterates over the registered project list.
    """
    proj = projects_root / name
    proj.mkdir(parents=True, exist_ok=True)

    (proj / "project.cfg").write_text(
        f"[project]\n"
        f"project_name = {name}\n"
        f"dev_tree_path = /nonexistent/{name}-dev-tree\n"
        f"git_repo_url = git@example.com:{name}.git\n"
        f"git_remote_name = origin\n"
        f"workflow_type = release\n"
        f"is_self_build = false\n"
        f"\n"
        f"[versioning]\n"
        f"max_patch = 99\n"
        f"max_minor = 9\n"
        f"max_major = 0\n",
        encoding="utf-8",
    )

    (proj / "release-state.md").write_text(
        "# Release State\n\n"
        "## Active RC\nnone\n\n"
        "## RC Opened At\nnone\n\n"
        "## RC Opened By Task\nnone\n",
        encoding="utf-8",
    )

    queues_dir = proj / "tasks" / "queues"
    queues_dir.mkdir(parents=True, exist_ok=True)
    (queues_dir / "plans").mkdir(parents=True, exist_ok=True)
    for agent in ("coder", "pm", "writer", "tester", "cm", "bug", "priority"):
        (queues_dir / f"{agent}_backlog.md").write_text(
            f"# {agent.upper()} Backlog\n\n"
            "<!-- Tasks are listed below. One task per line. -->\n"
            "<!-- Format: - [ ] TASK-ID -->\n",
            encoding="utf-8",
        )

    (proj / "logs").mkdir(parents=True, exist_ok=True)
    (proj / "overwatch" / "backups").mkdir(parents=True, exist_ok=True)
    (proj / "overwatch" / "actions.log").write_text("", encoding="utf-8")
    (proj / "metrics").mkdir(parents=True, exist_ok=True)

    return proj


def _write_document_project(projects_root: pathlib.Path, name: str) -> pathlib.Path:
    """A document-workflow project (git_mode=none — no dev tree or git repo
    required), used only by fixture 5's real wake subprocess drive."""
    proj = projects_root / name
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "project.cfg").write_text(
        f"[project]\nproject_name = {name}\nworkflow_type = document\n",
        encoding="utf-8",
    )
    queues_dir = proj / "tasks" / "queues"
    queues_dir.mkdir(parents=True, exist_ok=True)
    (queues_dir / f"{_RETRY_AGENT}_backlog.md").write_text(
        f"# {name} {_RETRY_AGENT} backlog\n\nNo tasks queued by this fixture.\n",
        encoding="utf-8",
    )
    return proj


def _build_shared_tree(root: pathlib.Path) -> pathlib.Path:
    """Build the one kanban root every fixture in this module exercises.

    See the module docstring's "ONE TREE, NOT FIVE" section for the shape
    and rationale.
    """
    (root / "kanban.cfg").write_text(
        "[paths]\n\n"
        "[chain]\n"
        "pm_mode = automatic\n\n"
        "[wake]\n"
        "max_tasks_per_wake = 1\n"
        "max_runtime_seconds = 600\n"
        # lock_wait_seconds = 0: the contention under test is a wake chain
        # holding the lock, which a wake otherwise waits up to 120 s for;
        # this fixture pins the immediate-deferral path.
        "lock_wait_seconds = 0\n",
        encoding="utf-8",
    )

    all_projects = [PRIMARY_PROJECT, RETRY_TARGET_PROJECT, *FILLER_PROJECTS]
    cfg_lines = []
    for i, name in enumerate(all_projects, start=1):
        cfg_lines.append(f"[project:{name}]\npriority={i}\nenabled=true\n\n")
    (root / "projects.cfg").write_text("".join(cfg_lines), encoding="utf-8")

    (root / "logs" / "agents").mkdir(parents=True, exist_ok=True)
    (root / "logs" / "debug" / "archive").mkdir(parents=True, exist_ok=True)
    (root / "locks").mkdir(parents=True, exist_ok=True)
    (root / "tasks" / "queues" / "plans").mkdir(parents=True, exist_ok=True)

    projects_root = root / "projects"
    projects_root.mkdir(parents=True, exist_ok=True)
    _write_release_project(projects_root, PRIMARY_PROJECT)
    _write_document_project(projects_root, RETRY_TARGET_PROJECT)
    for filler in FILLER_PROJECTS:
        _write_document_project(projects_root, filler)

    # Full, real copies -- the same set install.sh mirrors into a live
    # install -- so every fixture below, whatever it drives, exercises
    # byte-identical production code from this one tree (see module
    # docstring). Paid once per module rather than once per fixture.
    shutil.copytree(_TEAM_DIR / "scripts", root / "scripts")
    shutil.copytree(_TEAM_DIR / "pgai_agent_kanban", root / "pgai_agent_kanban")
    shutil.copytree(_TEAM_DIR / "workflows", root / "workflows")
    shutil.copytree(_TEAM_DIR / "pm-agent", root / "pm-agent")
    shutil.copytree(_TEAM_DIR / "config", root / "config")

    return root


@pytest.fixture(scope="module")
def shared_tree(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    """The one kanban root every test class in this module exercises.
    Module-scoped so the (non-trivial) full-tree copy is paid once; each
    fixture below scopes its own mutations to a distinct task ID, lock
    file, or project so the five tests do not interfere with each other."""
    root = tmp_path_factory.mktemp("single_truth_tree")
    return _build_shared_tree(root)


def _scripts_lib(tree: pathlib.Path) -> pathlib.Path:
    return tree / "scripts" / "lib"


def _base_env(tree: pathlib.Path, project_name: str) -> dict:
    env = dict(os.environ)
    # Same reason as _wake_env below: the shared tree's kanban.cfg [wake]
    # values govern, not whatever the invoking shell exported.
    scrub_wake_tunable_env(env)
    env["KANBAN_ROOT"] = str(tree)
    env["TEAM_ROOT"] = str(tree)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(tree)
    env["PGAI_PROJECT_NAME"] = project_name
    env.pop("PGAI_DEV_TREE_PATH", None)
    return env


# ===========================================================================
# Fixture 1 — stale non-terminal marker over a terminal status.md lets the
# discovery idle gate proceed.
# ===========================================================================


class TestStaleMarkerLetsDiscoveryProceed:
    """discovery.sh's _disc_chain_idle reads status.md, never the queue
    marker. A [ ] (non-terminal) marker left behind over a task whose own
    status.md already reads DONE must not block the gate.

    Red-case proof: the marker (" ") and the status.md state (DONE)
    deliberately disagree. A gate that regressed to reading the marker
    would report NOT idle (exit 1); the real gate reads status.md and
    reports idle (exit 0) — the assertion below is the discriminator.
    """

    def test_stale_marker_over_done_status_is_judged_idle(
        self, shared_tree: pathlib.Path
    ) -> None:
        tree = shared_tree
        task_id = "CODER-20260601-901-single-truth-fixture-one"
        task_dir = tree / "projects" / PRIMARY_PROJECT / "tasks" / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "status.md").write_text(
            "## State\nDONE\n\n## Summary\nfixture task, already done\n",
            encoding="utf-8",
        )
        backlog_file = (
            tree / "projects" / PRIMARY_PROJECT / "tasks" / "queues" / "coder_backlog.md"
        )
        backlog_file.write_text(
            f"# CODER Backlog\n\n- [ ] {task_id} -- stale marker, task actually done\n",
            encoding="utf-8",
        )

        lib = _scripts_lib(tree)
        script = textwrap.dedent(f"""\
            #!/usr/bin/env bash
            set -uo pipefail
            source "{lib}/ini_parser.sh"
            source "{lib}/temp.sh"
            source "{lib}/semver.sh"
            source "{lib}/project_paths.sh"
            source "{lib}/discovery.sh"
            _disc_chain_idle "{PRIMARY_PROJECT}"
            echo "GUARD_EXIT=$?"
        """)
        result = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            env=_base_env(tree, PRIMARY_PROJECT),
            cwd=str(tree),
            timeout=30,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "GUARD_EXIT=0" in result.stdout, (
            "Expected the idle gate to proceed despite the stale non-terminal "
            f"marker, since the task's own status.md already reads DONE.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


# ===========================================================================
# Fixture 2 — a release-state.md field holding 'NONE' is refused loud,
# naming the accepted forms and a remedy, via the SAME validator
# verify-rc-state.sh calls.
# ===========================================================================


class TestReleaseStateNoneRefusesLoud:
    """pgai_agent_kanban.cm.read_state_field --validate (invoked through
    team/scripts/lib/pp_run_ops.sh, the exact entry point
    team/scripts/verify-rc-state.sh's Check A uses) refuses a field holding
    the uppercase 'NONE' rather than the lowercase 'none' sentinel, naming
    the value received and the accepted forms, with a remedy.

    Red-case proof: 'NONE' is deliberately not the accepted 'none' sentinel
    and not a well-formed vX.Y.Z version either. A validator that silently
    accepted it (treating it as equivalent to 'none') would exit 0 with no
    ERROR line; the real validator refuses — the assertions below are the
    discriminator.

    This fixture validates a standalone release-state.md-shaped file under
    the shared tree's scratch/ area rather than PRIMARY_PROJECT's own
    release-state.md: the validator (--validate <filepath>) takes a bare
    file path and has no notion of "project", and PRIMARY_PROJECT's real
    release-state.md is a live dependency of fixture 1's discovery-idle
    check (_disc_chain_idle reads Active RC from it) sharing the same
    module-scoped tree. Writing this fixture's deliberately-malformed
    'NONE' into that shared file would make fixture 1 pass or fail
    depending on pytest-randomly's draw order for this module, instead of
    depending only on the discovery gate's own behaviour — exactly the
    cross-test interference the module docstring's per-test isolation
    note describes.
    """

    def test_active_rc_none_uppercase_is_refused_naming_accepted_forms(
        self, shared_tree: pathlib.Path
    ) -> None:
        tree = shared_tree
        release_state = tree / "scratch" / "single-truth-fixture-two-release-state.md"
        release_state.parent.mkdir(parents=True, exist_ok=True)
        release_state.write_text(
            "# Release State\n\n"
            "## Active RC\nNONE\n\n"
            "## RC Opened At\nnone\n\n"
            "## RC Opened By Task\nnone\n",
            encoding="utf-8",
        )

        pp_run_ops = tree / "scripts" / "lib" / "pp_run_ops.sh"
        env = scrub_wake_tunable_env(dict(os.environ))
        env["KANBAN_ROOT"] = str(tree)
        result = subprocess.run(
            ["bash", str(pp_run_ops), "pgai_agent_kanban.cm.read_state_field",
             "--validate", str(release_state)],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )

        assert result.returncode == 1, (
            f"Expected --validate to refuse (exit 1) on Active RC='NONE'.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        active_rc_lines = [
            line for line in result.stdout.splitlines() if line.startswith("Active RC\t")
        ]
        assert active_rc_lines, f"Expected an 'Active RC' result line.\nstdout: {result.stdout}"
        active_rc_line = active_rc_lines[0]
        assert "\tERROR\t" in active_rc_line, (
            f"Expected Active RC to be reported as ERROR, not silently accepted.\n"
            f"line: {active_rc_line}"
        )
        assert "'none' or vX.Y.Z" in active_rc_line, (
            f"Expected the refusal to name the accepted forms.\nline: {active_rc_line}"
        )
        assert "remedy:" in active_rc_line, (
            f"Expected the refusal to carry a remedy.\nline: {active_rc_line}"
        )

        # release-state.md itself must be untouched by a read-only validator.
        assert "NONE" in release_state.read_text(encoding="utf-8")


# ===========================================================================
# Fixture 3 — a lock acquisition attempted without complete provenance is
# refused and takes no lock.
# ===========================================================================


class TestUnprovenancedLockIsRefused:
    """pgai_lock_acquire (team/scripts/lib/lock_provenance.sh) refuses an
    acquisition with a blank provenance field and leaves no lock behind.

    Red-case proof: the pid field is deliberately blank. An acquire that
    silently granted the lock anyway (ignoring the missing field) would
    return 0 and leave the lock file holding a record; the real helper
    refuses (rc=2) and takes no lock — the assertions below are the
    discriminator.
    """

    def test_acquire_with_blank_pid_is_refused_and_takes_no_lock(
        self, shared_tree: pathlib.Path
    ) -> None:
        tree = shared_tree
        lib = _scripts_lib(tree)
        lock_file = tree / "locks" / "single-truth-provenance-fixture.lock"

        script = textwrap.dedent(f"""\
            set +e
            source "{lib}/lock_provenance.sh"
            fd=""
            pgai_lock_acquire "{lock_file}" "holder" "" "wake-chain:demo" "TASK-1" fd
            echo "RC=$?"
            echo "FD_EMPTY=$([[ -z \"$fd\" ]] && echo yes || echo no)"
            echo "FILE_EXISTS=$([[ -e "{lock_file}" ]] && echo yes || echo no)"
        """)
        result = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert "RC=2" in result.stdout, (
            f"Expected refusal (rc=2) with pid blank.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "FD_EMPTY=yes" in result.stdout, "No fd should be left open on refusal."
        assert "pid" in result.stderr, (
            f"Expected the refusal message to name the missing field 'pid'.\n"
            f"stderr: {result.stderr}"
        )
        assert (
            "error:" in result.stderr and "message:" in result.stderr and "remedy:" in result.stderr
        ), f"Expected the dual-channel error contract shape.\nstderr: {result.stderr}"
        assert not lock_file.exists() or lock_file.read_text(encoding="utf-8").strip() == "", (
            f"Expected no provenance record to be left behind by a refused "
            f"acquisition.\nlock file exists: {lock_file.exists()}"
        )


# ===========================================================================
# Fixture 4 — a task reconciled with no progress on three consecutive
# cycles is flagged for a human rather than reset a fourth time.
# ===========================================================================


_WAKE_TASK_STATE_LIB = pathlib.Path("scripts") / "lib" / "wake_task_state.sh"
_WAKE_RECONCILE_SWEEPS_LIB = pathlib.Path("scripts") / "lib" / "wake_reconcile_sweeps.sh"
_WAKE_TASK_TRANSITION_LIB = pathlib.Path("scripts") / "lib" / "wake_task_transition.sh"
_WAKE_LIFECYCLE_LIB = pathlib.Path("scripts") / "lib" / "wake_lifecycle.sh"
_OVERWATCH_PROTOCOL_LIB = pathlib.Path("scripts") / "lib" / "overwatch_protocol.sh"


def _extract_functions_snippet(tree: pathlib.Path, func_names: list[str]) -> str:
    """Awk brace-matching function extraction (mirrors
    team/tests/unit/test_zero_progress_cycle_cap.py): pulls named top-level
    function bodies out of their stage files, exercising the real
    production function bodies without paying for wake_common.sh's full
    top-level bootstrap."""
    alternation = "|".join(re.escape(name) for name in func_names)
    wake_task_state = tree / _WAKE_TASK_STATE_LIB
    wake_reconcile_sweeps = tree / _WAKE_RECONCILE_SWEEPS_LIB
    wake_task_transition = tree / _WAKE_TASK_TRANSITION_LIB
    wake_lifecycle = tree / _WAKE_LIFECYCLE_LIB
    return textwrap.dedent(f"""\
        eval "$(awk '
            /^({alternation})\\(\\)/ {{ found=1; depth=0 }}
            found {{
                print
                n = split($0, a, "")
                for (i = 1; i <= n; i++) {{
                    if (a[i] == "{{") depth++
                    if (a[i] == "}}") {{
                        depth--
                        if (depth == 0) {{ found=0 }}
                    }}
                }}
            }}
        ' '{wake_task_state}' '{wake_reconcile_sweeps}' '{wake_task_transition}' '{wake_lifecycle}')"
    """)


def _log_stub_fragment(log_file: pathlib.Path) -> str:
    log_file_str = str(log_file.resolve())
    return textwrap.dedent(f"""\
        __LOG_STUB_FILE="{log_file_str}"
        touch "${{__LOG_STUB_FILE}}"
        log() {{
            local _msg="[log-stub] $*"
            printf '%s\\n' "$_msg" | tee -a "${{__LOG_STUB_FILE}}"
        }}
    """)


def _zp_header(
    tree: pathlib.Path,
    log_file: pathlib.Path,
    backlog_file: pathlib.Path,
    tasks_root: pathlib.Path,
) -> str:
    funcs = [
        "normalize_status_file",
        "_status_section_re",
        "_read_status_section",
        "get_state",
        "set_state",
        "set_block",
        "mark_backlog",
        "transition_task_state",
        "_wake_transition_marker_landed",
        "_wake_transition_restore_state",
        "_wake_transition_arm",
        "_wake_transition_disarm",
        "_wake_transition_on_signal",
        "task_liveness_record_path",
        "task_liveness_record",
        "task_liveness_verdict",
        "task_liveness_is_alive",
        "_sweep_correct_terminal_marker",
        "reconcile_stale_active",
    ]
    overwatch_protocol = tree / _OVERWATCH_PROTOCOL_LIB
    lib = _scripts_lib(tree)
    return textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -euo pipefail
        export PGAI_AGENT_KANBAN_TEMP_DIR="{tree / 'kanban_temp'}"
        KANBAN_ROOT="{tree}"
        unset PGAI_AGENT_KANBAN_ROOT_PATH
        unset ZERO_PROGRESS_CAP TRANSIENT_RETRY_CAP
        source "{overwatch_protocol}"
        source "{lib}/error_contract.sh"
        source "{lib}/wake_task_id_gate.sh"
        source "{lib}/atomic_write.sh"
        source "{lib}/temp.sh"
        source "{lib}/lock_provenance.sh"
        BACKLOG="{backlog_file}"
        TASKS_ROOT="{tasks_root}"

        {_log_stub_fragment(log_file)}

        {_extract_functions_snippet(tree, funcs)}
    """)


def _field(status_text: str, heading: str) -> str:
    m = re.search(rf"^## {re.escape(heading)}\s*\n(.*?)(\n+##|\Z)", status_text, flags=re.S | re.M)
    assert m is not None, f"## {heading} section not found in:\n{status_text}"
    return m.group(1).strip()


class TestZeroProgressCapEscalates:
    """Three consecutive reconciliations of the same orphaned task (no live
    process behind its [A] marker, default cap=3) reset on cycles 1-2 and
    escalate to BLOCKED + Needs Human=yes on cycle 3, naming the count and
    the ceiling — instead of resetting a fourth time.

    Red-case proof: the assertion sequence itself IS the discriminator —
    a reconciler that reset unconditionally forever would report state ==
    "BACKLOG" on cycle 3 too, not "BLOCKED". The final assert on
    observed_states catches exactly that regression.
    """

    def test_third_consecutive_zero_progress_cycle_escalates_instead_of_resetting(
        self, shared_tree: pathlib.Path
    ) -> None:
        tree = shared_tree
        task_id = "CODER-20260601-902-single-truth-fixture-four"
        tasks_root = tree / "projects" / PRIMARY_PROJECT / "tasks"
        task_dir = tasks_root / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        status_file = task_dir / "status.md"
        status_file.write_text(
            textwrap.dedent(f"""\
                # Status

                ## Task
                {task_id}

                ## State
                WORKING

                ## Summary
                In progress.

                ## Artifacts
                none

                ## Blockers
                none

                ## Blocked By Agent
                none

                ## Blocked Reason
                none

                ## Needs Human
                no

                ## Next Recommended Step
                TBD

                ## Instruction Conflicts
                none
            """),
            encoding="utf-8",
        )
        backlog_file = tasks_root / "queues" / "coder_backlog.md"
        backlog_file.write_text(f"# CODER Backlog\n\n- [A] {task_id}\n", encoding="utf-8")

        log_file = tree / "single-truth-fixture-four-wake.log"
        header = _zp_header(tree, log_file, backlog_file, tasks_root)

        observed_states: list[str] = []
        for cycle in range(1, 4):
            # A dead-pid liveness record simulates the ordinary between-wakes
            # state (this task's own dispatch already exited); rewritten
            # each cycle (idempotent -- same task_id, same dead pid).
            script = header + f'task_liveness_record "{task_id}" claude 99999999 dispatch\n' + "reconcile_stale_active"
            result = subprocess.run(
                ["bash", "-c", script], capture_output=True, text=True, timeout=30
            )
            assert result.returncode == 0, (
                f"cycle {cycle}: reconcile_stale_active should not error\n"
                f"stdout={result.stdout}\nstderr={result.stderr}"
            )
            status_text = status_file.read_text(encoding="utf-8")
            state = _field(status_text, "State")
            observed_states.append(state)

            if cycle < 3:
                assert state == "BACKLOG", f"cycle {cycle}: expected BACKLOG (below cap)\n{status_text}"
                # Simulate the task being picked up and orphaned again.
                status_file.write_text(
                    re.sub(r"(^## State\s*\n)BACKLOG", r"\1WORKING", status_file.read_text(encoding="utf-8"), flags=re.M),
                    encoding="utf-8",
                )
                backlog_file.write_text(f"# CODER Backlog\n\n- [A] {task_id}\n", encoding="utf-8")

        assert observed_states == ["BACKLOG", "BACKLOG", "BLOCKED"], observed_states

        final_text = status_file.read_text(encoding="utf-8")
        assert _field(final_text, "Needs Human").lower() == "yes", (
            f"cycle 3 (at cap) must escalate to Needs Human=yes\n{final_text}"
        )
        blockers = _field(final_text, "Blockers")
        assert "3" in blockers, f"Blockers should name the count/ceiling (3)\n{blockers}"


# ===========================================================================
# Fixture 5 — a project whose lock is held on the first pass and free
# afterwards is worked within the same iteration.
# ===========================================================================


def _lock_path(kanban_root: pathlib.Path, project_name: str) -> pathlib.Path:
    """Mirrors pp_repo_lock_path's own resolution -- the exact lock file
    run_project_chain acquires for *project_name*."""
    repo_id = "".join(c for c in project_name.replace("/", "_") if c.isalnum() or c in "_-")
    return kanban_root / "locks" / f"repo-wake-{repo_id}.lock"


def _wake_env(kanban_root: pathlib.Path, extra_env: dict | None = None) -> dict:
    env = dict(os.environ)
    # The sandbox kanban.cfg pins its own [wake] values (lock_wait_seconds = 0
    # above all); an inherited LOCK_WAIT_SECONDS or sibling from the shell
    # that launched pytest would override them (docs/test-authoring-
    # guidelines.md, Anti-pattern 2). extra_env still sets any back on purpose.
    scrub_wake_tunable_env(env)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    env["PGAI_TASKS_DIR"] = str(kanban_root / "tasks")
    env.pop("PGAI_QUEUE_DIR", None)
    env.pop("PGAI_RELEASE_STATE_PATH", None)
    if extra_env:
        env.update(extra_env)
    return env


def _start_external_holder(kanban_root: pathlib.Path, project_name: str):
    lock_file = _lock_path(kanban_root, project_name)
    ready_file = kanban_root / "locks" / f"holder-ready-{project_name}"
    done_file = kanban_root / "locks" / f"holder-done-{project_name}"
    for stale in (ready_file, done_file):
        stale.unlink(missing_ok=True)

    holder_script = f"""#!/usr/bin/env bash
exec 9>"{lock_file}"
flock -x 9
printf 'holder=%s\\npid=%s\\nop=%s\\ntask=%s\\n' "{_RETRY_AGENT}" "$$" "wake-chain:{project_name}" "none" > "{lock_file}"
touch "{ready_file}"
while [[ ! -f "{done_file}" ]]; do sleep 0.02; done
exec 9>&-
"""
    holder = subprocess.Popen(["bash", "-c", holder_script])

    deadline = time.time() + 10.0
    while not ready_file.exists() and time.time() < deadline:
        time.sleep(0.02)
    assert ready_file.exists(), f"external lock holder for {project_name} never signaled readiness"

    return holder, done_file


def _run_wake_streaming(cmd, *, kanban_root, extra_env, on_line, timeout=_SUBPROCESS_TIMEOUT_SECONDS):
    proc = subprocess.Popen(
        cmd,
        cwd=str(kanban_root),
        env=_wake_env(kanban_root, extra_env),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    collected: list[str] = []
    start = time.time()
    try:
        for line in iter(proc.stdout.readline, ""):
            collected.append(line)
            on_line(line)
            if time.time() - start > timeout:
                raise subprocess.TimeoutExpired(cmd, timeout)
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass
    returncode = proc.wait(timeout=timeout)
    return returncode, "".join(collected)


class TestContendedProjectRetriedSameIteration:
    """A project contended on the first pass of the multi-project wake
    iteration and free by the time the end-of-iteration retry pass reaches
    it is worked in the same firing, rather than deferred to the next cron
    tick. Drives a real `bash scripts/wake/claude.sh` subprocess against
    the shared tree's retry-target + filler projects (document workflow,
    no dev tree required).

    Red-case proof: the external holder is released only after the first
    pass's own contention-decline log line is observed. A retry pass that
    was never actually implemented (or that only fired for the next cron
    tick instead of the same iteration) would never produce the "retry
    pass: chain completed" line this test asserts on — the assertions
    below are the discriminator.
    """

    def test_contended_project_worked_via_retry_pass_in_same_firing(
        self, shared_tree: pathlib.Path
    ) -> None:
        kanban_root = shared_tree
        target = RETRY_TARGET_PROJECT

        holder, done_file = _start_external_holder(kanban_root, target)
        released = {"flag": False}

        def _on_line(line: str) -> None:
            if not released["flag"] and f"project {target}: repo lock held by" in line:
                done_file.write_text("release\n", encoding="utf-8")
                released["flag"] = True

        try:
            returncode, output = _run_wake_streaming(
                ["bash", str(kanban_root / _WAKE_CLAUDE_REL), f"--agent={_RETRY_AGENT}"],
                kanban_root=kanban_root,
                extra_env=None,
                on_line=_on_line,
            )
        finally:
            if holder.poll() is None:
                done_file.write_text("release\n", encoding="utf-8")
                try:
                    holder.wait(timeout=10.0)
                except subprocess.TimeoutExpired:
                    holder.kill()

        assert released["flag"], (
            f"Never observed the first-pass contention decline line for {target!r}; "
            f"the external holder was never released.\noutput:\n{output}"
        )
        assert returncode == 0, f"wake firing exited {returncode}, expected 0.\noutput:\n{output}"

        assert f"project {target}: repo lock held by holder={_RETRY_AGENT}" in output, (
            f"Expected the first-pass deferral log line to name the holder.\noutput:\n{output}"
        )
        assert f"project {target}: retry pass: retrying after end-of-iteration deferral" in output, (
            f"Expected the retry pass to fire for {target!r}.\noutput:\n{output}"
        )
        assert f"project {target}: retry pass: chain completed" in output, (
            f"Expected the retry to complete (lock now free), not remain contended.\n"
            f"output:\n{output}"
        )
        assert f"project {target}: retry pass: still contended" not in output, (
            f"Retry should have succeeded once the lock was free.\noutput:\n{output}"
        )
