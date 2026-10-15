"""
test_wake_contention_retry.py
==============================
End-to-end coverage for the end-of-iteration contention retry pass: a
project whose repo lock is held by another agent during the ordinary
multi-project pass gets exactly one more look, after every other project in
the iteration has had its own turn, instead of waiting for the next cron
firing.

Every fixture below drives the REAL production entry point — a real
`bash scripts/wake/claude.sh --agent=writer` subprocess against a real,
throwaway kanban root with several registered document-workflow projects
(git_mode=none, so no dev tree or git repo is needed) — rather than calling
internal shell functions directly. The `claude` CLI on PATH is never
actually invoked: every project's writer backlog is empty, so
process_one_task finds nothing to dispatch and returns immediately. Repo
lock contention is real OS-level flock contention: a background holder
subprocess takes the lock exactly the same way run_project_chain itself
would (via pgai_lock_acquire), and this test controls precisely when that
holder releases it.

Coverage (requirements/v2.11.0-single-truth-state-integrity.md, CODER task
"Retry a lock-contended project once at the end of the iteration"):

  - A project contended on the first pass and free by the time the retry
    pass reaches it is worked in the same firing (test_contended_project...).
  - A project contended on both passes is deferred with a log line naming
    the project and the lock holder, and the firing still exits cleanly
    (test_project_contended_on_both_passes...).
  - A firing whose runtime budget is already exhausted when the first pass
    ends performs no retry and says so in the log
    (test_retry_pass_skipped_when_runtime_budget_exhausted).
  - A project that acquired a HALT sentinel between the two passes is not
    retried (test_project_halted_between_passes_is_not_retried).
  - A LOCK_WAIT_SECONDS / LOCK_STARVATION_FIRINGS exported in the process
    that runs these tests -- as every agent wake exports them -- does not
    reach the sandboxed wake, so the sandbox kanban.cfg's
    lock_wait_seconds = 0 still takes the immediate-deferral path
    (test_inherited_lock_wait_seconds_does_not_override_the_sandbox_cfg).
  - A LOCK_WAIT_SECONDS set deliberately for the wake -- the operator
    override -- still outranks that cfg value
    (test_lock_wait_seconds_set_for_the_wake_overrides_the_sandbox_cfg).

Design notes
------------
- Every kanban root built here is a full, self-contained copy of this dev
  tree's scripts/, pgai_agent_kanban/, workflows/, and pm-agent/ directories
  (the same set install.sh mirrors into a live install), so the wake script
  invoked as a subprocess is byte-identical to production and self-locates
  exactly as it would on a real host.
- Every registered project uses the document workflow (git_mode=none) so no
  dev tree or git repository is required — run_project_chain's dev-tree gate
  is exempt, and the lock-contention step under test happens BEFORE that
  gate is even reached.
- The "filler" projects registered alongside the contended one exist purely
  to give the wake subprocess enough real first-pass work (each project's
  own model-preflight, blocked-task scan, and recheck calls) to create a
  comfortable timing margin between "the contended project's first-pass
  decline is logged" and "the retry pass reaches that project" -- the two
  live-release fixtures react to the first pass's own decline log line, not
  to a fixed sleep, so no production code is touched to make the test
  deterministic.
- All temp paths use pytest's tmp_path. No bare /tmp.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import time

import pytest

from tests.fixtures.scrubbed_subprocess_env import scrub_wake_tunable_env

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_WAKE_CLAUDE_REL = pathlib.Path("scripts") / "wake" / "claude.sh"

# The agent identity every fixture in this file dispatches as. "writer" is
# one of the document workflow's declared agents (workflows/document/
# workflow.cfg's [capabilities] agents=pm,writer,tester,cm) and, unlike
# "pm", never triggers the post-loop autonomous discovery block in
# run_project_chain -- keeping these fixtures' output limited to exactly
# the multi-project iteration and retry-pass log lines under test.
_AGENT = "writer"

# Generous but bounded: real subprocess wall time for a handful of
# empty-queue projects is a fraction of a second; this only guards against
# a genuine hang (e.g. a wedged flock) turning into an indefinite test stall.
_SUBPROCESS_TIMEOUT_SECONDS = 60


# ---------------------------------------------------------------------------
# Fixture-tree builder
# ---------------------------------------------------------------------------


def _write_queue_file(path: pathlib.Path, title: str) -> None:
    """An always-empty per-agent backlog: no bracket-marker lines, so
    process_one_task's get_first_pending_task finds nothing and returns
    immediately -- no dispatch, no real CLI invocation, ever."""
    path.write_text(f"# {title}\n\nNo tasks queued by this fixture.\n", encoding="utf-8")


def _build_kanban_root(tmp_path: pathlib.Path, project_names: list[str]) -> pathlib.Path:
    """Build a full, self-contained kanban root registering every name in
    *project_names* as a document-workflow project (no dev tree, no git
    repo required). A real copy of this dev tree's scripts/,
    pgai_agent_kanban/, workflows/, and pm-agent/ trees -- the same set
    install.sh mirrors into a live install -- so the wake script invoked
    against this root is byte-identical to production.
    """
    root = tmp_path / "kanban_root"
    root.mkdir(parents=True, exist_ok=True)

    (root / "kanban.cfg").write_text(
        "[paths]\n\n[chain]\npm_mode = automatic\n\n"
        "[release]\nmax_rc_retries = 3\n\n[wake]\nmax_tasks_per_wake = 1\n"
        # lock_wait_seconds = 0: the contention under test is a wake chain
        # holding the lock, which a wake otherwise waits up to 120 s for;
        # this fixture pins the immediate-deferral path.
        "lock_wait_seconds = 0\n",
        encoding="utf-8",
    )

    projects_cfg_lines = []
    for i, name in enumerate(project_names, start=1):
        projects_cfg_lines.append(f"[project:{name}]\npriority={i}\nenabled=true\n\n")
    (root / "projects.cfg").write_text("".join(projects_cfg_lines), encoding="utf-8")

    for name in project_names:
        proj = root / "projects" / name
        proj.mkdir(parents=True, exist_ok=True)
        (proj / "project.cfg").write_text(
            "[project]\n"
            f"project_name = {name}\n"
            "workflow_type = document\n",
            encoding="utf-8",
        )
        queues_dir = proj / "tasks" / "queues"
        queues_dir.mkdir(parents=True, exist_ok=True)
        _write_queue_file(queues_dir / f"{_AGENT}_backlog.md", f"{name} {_AGENT} backlog")

    (root / "logs").mkdir(parents=True, exist_ok=True)
    (root / "locks").mkdir(parents=True, exist_ok=True)

    # Full, real copies -- the same set install.sh mirrors into a live
    # install -- so the wake script invoked as a subprocess is
    # byte-identical to production.
    shutil.copytree(_TEAM_DIR / "scripts", root / "scripts")
    shutil.copytree(_TEAM_DIR / "pgai_agent_kanban", root / "pgai_agent_kanban")
    shutil.copytree(_TEAM_DIR / "workflows", root / "workflows")
    shutil.copytree(_TEAM_DIR / "pm-agent", root / "pm-agent")
    # config/default_palette.txt is the canonical palette definition
    # projects_cfg.sh fails loud without (dashboard color assignment).
    shutil.copytree(_TEAM_DIR / "config", root / "config")

    return root


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


def _start_external_holder(
    kanban_root: pathlib.Path, project_name: str
) -> tuple[subprocess.Popen, pathlib.Path]:
    """Start a background bash process that holds the real repo-wake lock
    for *project_name* -- exactly the flock pgai_lock_acquire itself takes
    -- until the returned "done" marker file appears. Blocks until the
    holder confirms it actually holds the lock (a "ready" marker file),
    so the caller never races the wake subprocess against an unheld lock.

    Returns (holder_process, done_marker_path). The caller releases the
    lock by writing the done marker (see _release_external_holder).
    """
    lock_file = _lock_path(kanban_root, project_name)
    ready_file = kanban_root / "locks" / f"holder-ready-{project_name}"
    done_file = kanban_root / "locks" / f"holder-done-{project_name}"
    for stale in (ready_file, done_file):
        stale.unlink(missing_ok=True)

    # Write the same "holder=/pid=/op=/task=" provenance record a real
    # pgai_lock_acquire caller would leave behind (lib/lock_provenance.sh),
    # so the contended wake subprocess's own contention message actually
    # names a holder instead of reporting "(no provenance recorded)". The
    # holder identity is deliberately the same agent name (_AGENT) this
    # file's wake subprocesses run as -- a concurrent same-agent firing
    # holding the lock is the realistic contention scenario the retry pass
    # exists for.
    holder_script = f"""#!/usr/bin/env bash
exec 9>"{lock_file}"
flock -x 9
printf 'holder=%s\\npid=%s\\nop=%s\\ntask=%s\\n' "{_AGENT}" "$$" "wake-chain:{project_name}" "none" > "{lock_file}"
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


def _release_external_holder(holder: subprocess.Popen, done_file: pathlib.Path) -> None:
    done_file.write_text("release\n", encoding="utf-8")
    try:
        holder.wait(timeout=10.0)
    except subprocess.TimeoutExpired:
        holder.kill()
        holder.wait(timeout=5.0)


def _run_wake_streaming(
    cmd: list[str],
    *,
    kanban_root: pathlib.Path,
    extra_env: dict | None,
    on_line,
    timeout: float = _SUBPROCESS_TIMEOUT_SECONDS,
) -> tuple[int, str]:
    """Run *cmd* as a subprocess, calling on_line(line) synchronously for
    every line of its combined stdout/stderr as it is produced, in real
    time. Returns (returncode, full_output) once the process exits.

    Used to react to a specific log line the wake subprocess prints DURING
    its run (e.g. a project's first-pass contention decline) instead of
    only being able to inspect output after the whole firing completes --
    the mechanism the live-release fixtures below need to control exactly
    when an externally held lock is released relative to the wake
    subprocess's own progress.
    """
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


# ---------------------------------------------------------------------------
# Fixture 1: contended on the first pass, free by the retry pass -- worked
# in the same firing.
# ---------------------------------------------------------------------------


def test_contended_project_worked_via_retry_pass_in_same_firing(
    tmp_path: pathlib.Path,
) -> None:
    target = "retry-target"
    fillers = ["filler-one", "filler-two", "filler-three"]
    kanban_root = _build_kanban_root(tmp_path, [target, *fillers])

    holder, done_file = _start_external_holder(kanban_root, target)
    released = {"flag": False}

    def _on_line(line: str) -> None:
        if not released["flag"] and f"project {target}: repo lock held by" in line:
            # The first pass has just declined the contended project --
            # release the external holder now so the lock is free well
            # before the retry pass (which only runs after every filler
            # project has also had its first-pass turn) reaches it.
            done_file.write_text("release\n", encoding="utf-8")
            released["flag"] = True

    try:
        returncode, output = _run_wake_streaming(
            ["bash", str(kanban_root / _WAKE_CLAUDE_REL), f"--agent={_AGENT}"],
            kanban_root=kanban_root,
            extra_env=None,
            on_line=_on_line,
        )
    finally:
        # Best-effort: the holder should already have exited once its done
        # marker appeared; this only matters if the marker line was never
        # observed (test about to fail) and the holder is still parked.
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

    assert f"project {target}: repo lock held by holder={_AGENT}" in output, (
        f"Expected the first-pass deferral log line to name the holder ({_AGENT}).\n"
        f"output:\n{output}"
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


# ---------------------------------------------------------------------------
# Fixture 2: contended on both passes -- deferred with a log line naming the
# project and the holder, and the firing still exits cleanly.
# ---------------------------------------------------------------------------


def test_project_contended_on_both_passes_is_deferred_and_firing_exits_cleanly(
    tmp_path: pathlib.Path,
) -> None:
    target = "always-contended"
    fillers = ["filler-one"]
    kanban_root = _build_kanban_root(tmp_path, [target, *fillers])

    holder, done_file = _start_external_holder(kanban_root, target)
    try:
        result = subprocess.run(
            ["bash", str(kanban_root / _WAKE_CLAUDE_REL), f"--agent={_AGENT}"],
            cwd=str(kanban_root),
            env=_wake_env(kanban_root),
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        )
    finally:
        _release_external_holder(holder, done_file)

    output = result.stdout + result.stderr
    assert result.returncode == 0, f"wake firing exited {result.returncode}.\noutput:\n{output}"

    first_pass_line = f"project {target}: repo lock held by holder={_AGENT}"
    assert output.count(first_pass_line) >= 2, (
        f"Expected the contention decline line naming holder={_AGENT!r} to appear "
        f"on both the first pass and the retry pass (>= 2 occurrences); "
        f"got {output.count(first_pass_line)}.\noutput:\n{output}"
    )
    assert f"project {target}: retry pass: retrying after end-of-iteration deferral" in output
    assert f"project {target}: retry pass: still contended after retry" in output, (
        f"Expected the retry pass to name the still-contended outcome.\noutput:\n{output}"
    )


# ---------------------------------------------------------------------------
# Fixture 3: batch runtime budget already exhausted when the first pass
# ends -- no retry is performed, and the log says so.
# ---------------------------------------------------------------------------


def test_retry_pass_skipped_when_runtime_budget_exhausted(tmp_path: pathlib.Path) -> None:
    target = "budget-target"
    kanban_root = _build_kanban_root(tmp_path, [target])

    holder, done_file = _start_external_holder(kanban_root, target)
    try:
        result = subprocess.run(
            ["bash", str(kanban_root / _WAKE_CLAUDE_REL), f"--agent={_AGENT}"],
            cwd=str(kanban_root),
            # A batch budget of 0 seconds is already exhausted the instant
            # the first pass's elapsed-time check runs -- deterministic
            # regardless of real wall-clock timing (mirrors the existing,
            # un-guarded `ELAPSED >= MAX_RUNTIME_SECONDS` convention already
            # used by run_project_chain's own per-task loop, which likewise
            # does not special-case 0 as "unlimited").
            env=_wake_env(kanban_root, {"MAX_RUNTIME_SECONDS": "0"}),
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        )
    finally:
        _release_external_holder(holder, done_file)

    output = result.stdout + result.stderr
    assert result.returncode == 0, f"wake firing exited {result.returncode}.\noutput:\n{output}"

    assert "retry pass: batch runtime budget already exhausted" in output, (
        f"Expected the budget-exhausted retry skip to be narrated.\noutput:\n{output}"
    )
    budget_lines = [line for line in output.splitlines() if "performing no retry for" in line]
    assert budget_lines and target in budget_lines[0], (
        f"Expected the budget-exhausted skip line to name {target!r}.\noutput:\n{output}"
    )
    assert f"project {target}: retry pass: retrying after end-of-iteration deferral" not in output, (
        f"No retry should have been attempted once the batch budget was exhausted.\n"
        f"output:\n{output}"
    )


# ---------------------------------------------------------------------------
# Fixture 4: a project that acquires a HALT sentinel between the two passes
# is not retried.
# ---------------------------------------------------------------------------


def test_project_halted_between_passes_is_not_retried(tmp_path: pathlib.Path) -> None:
    target = "halts-mid-iteration"
    fillers = ["filler-one", "filler-two", "filler-three"]
    kanban_root = _build_kanban_root(tmp_path, [target, *fillers])
    halt_file = kanban_root / "projects" / target / "HALT"

    holder, done_file = _start_external_holder(kanban_root, target)
    acted = {"flag": False}

    def _on_line(line: str) -> None:
        if not acted["flag"] and f"project {target}: repo lock held by" in line:
            # The first pass has just declined the contended project on
            # lock grounds (HALT was NOT present yet, so this really
            # exercised the contention path rather than the pre-existing
            # per-project HALT fast-exit). Now free the lock AND raise a
            # per-project HALT before the retry pass reaches this project,
            # so the retry pass's own fresh HALT re-check is what is under
            # test, not a lock re-contention.
            done_file.write_text("release\n", encoding="utf-8")
            halt_file.write_text("# operator halt raised mid-iteration by this fixture\n", encoding="utf-8")
            acted["flag"] = True

    try:
        returncode, output = _run_wake_streaming(
            ["bash", str(kanban_root / _WAKE_CLAUDE_REL), f"--agent={_AGENT}"],
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

    assert acted["flag"], (
        f"Never observed the first-pass contention decline line for {target!r}; "
        f"the lock/HALT sequencing never fired.\noutput:\n{output}"
    )
    assert returncode == 0, f"wake firing exited {returncode}, expected 0.\noutput:\n{output}"

    assert f"project {target}: repo lock held by holder={_AGENT}" in output, (
        f"Expected the first-pass deferral log line to name the holder.\noutput:\n{output}"
    )
    assert f"project {target}: retry pass: per-project HALT now present, not retried" in output, (
        f"Expected the retry pass to re-check per-project HALT and decline.\noutput:\n{output}"
    )
    assert f"project {target}: retry pass: retrying after end-of-iteration deferral" not in output, (
        f"A project that halted between the two passes must not be retried.\noutput:\n{output}"
    )


# ---------------------------------------------------------------------------
# Fixture 5: [wake] tunables exported by the shell that launched pytest do
# not reach the sandboxed wake; one set deliberately for the wake does.
# ---------------------------------------------------------------------------

# Bounded well below the module's general subprocess timeout: with the
# sandbox's lock_wait_seconds = 0 governing, a single contended project
# defers in a few seconds. An inherited 120 s budget instead keeps the wake
# waiting past this bound, and the test fails naming that cause rather than
# surfacing a bare timeout.
_INHERITED_ENV_TIMEOUT_SECONDS = 40


def _run_contended_wake(kanban_root: pathlib.Path, target: str, extra_env: dict | None) -> str:
    """Run one wake firing while an external wake-chain holder keeps
    *target*'s repo lock for the whole firing; return its combined output."""
    holder, done_file = _start_external_holder(kanban_root, target)
    try:
        result = subprocess.run(
            ["bash", str(kanban_root / _WAKE_CLAUDE_REL), f"--agent={_AGENT}"],
            cwd=str(kanban_root),
            env=_wake_env(kanban_root, extra_env),
            capture_output=True,
            text=True,
            timeout=_INHERITED_ENV_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        partial = "".join(
            s.decode("utf-8", "replace") if isinstance(s, bytes) else (s or "")
            for s in (exc.stdout, exc.stderr)
        )
        pytest.fail(
            f"the sandboxed wake was still running after {_INHERITED_ENV_TIMEOUT_SECONDS}s -- "
            "it is waiting on the repo lock instead of deferring, so a lock-wait "
            "budget other than the sandbox kanban.cfg's lock_wait_seconds = 0 "
            f"reached it.\npartial output:\n{partial}"
        )
    finally:
        _release_external_holder(holder, done_file)
    output = result.stdout + result.stderr
    assert result.returncode == 0, f"wake firing exited {result.returncode}.\noutput:\n{output}"
    return output


def test_inherited_lock_wait_seconds_does_not_override_the_sandbox_cfg(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The process running this test exports the live wake's lock-wait
    tunables; the sandboxed wake must still obey its own kanban.cfg's
    lock_wait_seconds = 0 and defer at once, never wait."""
    monkeypatch.setenv("LOCK_WAIT_SECONDS", "120")
    monkeypatch.setenv("LOCK_STARVATION_FIRINGS", "3")
    target = "inherits-lock-wait"
    kanban_root = _build_kanban_root(tmp_path, [target])

    output = _run_contended_wake(kanban_root, target, extra_env=None)

    deferral = f"project {target}: repo lock held by holder={_AGENT}"
    assert deferral in output and "deferring" in output, (
        f"Expected the immediate deferral line naming holder={_AGENT!r}.\noutput:\n{output}"
    )
    assert "waiting up to" not in output, (
        "The sandboxed wake waited for the lock: the LOCK_WAIT_SECONDS exported "
        "by this test's own process overrode the sandbox cfg's "
        f"lock_wait_seconds = 0.\noutput:\n{output}"
    )


def test_lock_wait_seconds_set_for_the_wake_overrides_the_sandbox_cfg(
    tmp_path: pathlib.Path,
) -> None:
    """LOCK_WAIT_SECONDS handed to the wake on purpose is the operator
    override: it outranks the sandbox kanban.cfg's lock_wait_seconds = 0,
    so the wake waits that long for the wake-chain holder before deferring."""
    target = "operator-override"
    kanban_root = _build_kanban_root(tmp_path, [target])

    output = _run_contended_wake(kanban_root, target, extra_env={"LOCK_WAIT_SECONDS": "2"})

    assert f"project {target}: waiting up to 2s for the repo lock held by wake chain" in output, (
        f"Expected the wake to wait the overriding 2 s budget.\noutput:\n{output}"
    )
    assert "(lock_wait_seconds=2," in output, (
        f"Expected the wait line to report the overriding value.\noutput:\n{output}"
    )
    assert f"project {target}: repo lock still held after waiting" in output, (
        f"Expected the wait to end at its budget with the holder still present.\noutput:\n{output}"
    )
