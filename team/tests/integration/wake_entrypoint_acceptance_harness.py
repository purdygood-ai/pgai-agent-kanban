"""
wake_entrypoint_acceptance_harness.py
======================================
Shared, reusable harness for acceptance-layer fixtures that drive the REAL
production wake entry point (`scripts/wake/claude.sh`, dispatched through
`scripts/wake-batch.sh`) as a subprocess, rather than sourcing a library file
or extracting a block of shell for standalone execution.

Not a test module itself -- pytest does not collect this file (it carries no
`test_*` / `Test*` names). Test files under `tests/integration/` import the
functions below to build one acceptance fixture per operator-facing
behaviour, without re-deriving the synthetic-root, stub-provider, or
scrubbed-environment machinery each time.

Why this exists
----------------
A mechanism fixture (e.g. `tests/unit/test_wake_bootstrap_bg_ceiling.py`)
extracts a sentinel-bounded block of shell from a library file and runs the
arithmetic in isolation. That proves the mechanism computes the right
answer; it cannot prove the production entry point ever reaches that
mechanism, with the environment an operator actually has, from the
directory an operator actually stands in. This harness closes that gap:
every fixture built on it runs `bash .../scripts/wake/claude.sh` (or
`wake-batch.sh`) as a real subprocess against a full, self-contained copy of
this dev tree's runtime (the same set `install.sh` mirrors into a live
install), from a scratch working directory outside the repository, under a
deliberately rebuilt (not inherited) environment.

Adding the next behaviour
--------------------------
1. Call `build_synthetic_kanban_root(tmp_path, ...)` to get a throwaway,
   self-contained kanban root.
2. Call `build_stub_provider_cli(tmp_path, probe_env_vars=(...))` if the
   behaviour under test needs to observe a value the wake process exports
   into its environment (see "The probe channel" below). Skip this call for
   behaviours whose evidence is a log line or an on-disk artifact instead.
3. Call `scrubbed_wake_env(...)` to build the subprocess environment,
   passing any extra env vars the specific behaviour needs to set (a
   budget, an override, ...).
4. Call `run_wake_entrypoint(...)` from a scratch cwd (e.g.
   `tmp_path / "foreign_cwd"`, created but never populated with repo
   files) and assert on `returncode`, `stdout`, `stderr`.

No behaviour-specific logic lives in this file. A later ticket adding
acceptance coverage for a second operator-facing behaviour writes a new
test file that imports these functions -- it does not copy this module.

The probe channel
------------------
Some behaviours (the CLI background-wait ceiling among them) export a value
into the wake process's own environment rather than printing it directly.
The production entry point already runs a real subprocess once per firing
regardless of queue contents -- the provider-identity version query
(`scripts/wake/claude.sh`, "PROVIDER-IDENTITY-LOG-BEGIN": `timeout 10
"$PROVIDER_CLI" --version`) -- and that child process inherits every
exported variable the wake process has set by the time it runs. The stub
provider CLI built by `build_stub_provider_cli` answers `--version` by
printing the requested env vars' values back out; the wake process's own
`log "provider CLI version: ${...}"` line then carries that observation
into the real, captured subprocess output. This is the real entry point
observing its own real, exported state through a real (if substituted)
child process -- not a re-implementation of the derivation, and not a
sourced/extracted shortcut. The stub never answers any invocation shape
other than `--version`; anything else is recorded to a marker file and
refused loud, so an accidental real task dispatch is both impossible to
mistake for a pass and independently verifiable after the run.

Environment scrubbing
----------------------
`scrubbed_wake_env` builds the subprocess environment from an explicit,
minimal set of names -- it does not start from `dict(os.environ)` and
delete. See its docstring for the exact rebuilt/kept/scrubbed accounting.
The universal part of that base (PATH/HOME/LANG, explicit-build-up-never-
inherit) is `tests.fixtures.scrubbed_subprocess_env.build_scrubbed_env`;
`scrubbed_wake_env` layers the wake-specific names (the synthetic kanban
root, the stub-provider bin directory) on top of it.
"""

from __future__ import annotations

import pathlib
import shutil
import stat
import subprocess
import sys
import textwrap
from dataclasses import dataclass, field
from typing import Iterable, Optional

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

# team/ -- three parents up from tests/integration/wake_entrypoint_acceptance_harness.py
TEAM_DIR = pathlib.Path(__file__).parent.parent.parent

WAKE_CLAUDE_REL = pathlib.Path("scripts") / "wake" / "claude.sh"
WAKE_BATCH_REL = pathlib.Path("scripts") / "wake-batch.sh"

# The dev-tree directories a live install mirrors verbatim (install.sh's own
# copy set). Copying exactly these makes the wake subprocess byte-identical
# to what an operator's cron actually runs.
_MIRRORED_DIRS = ("scripts", "pgai_agent_kanban", "workflows", "pm-agent", "config")

_STUB_MARKER_FILENAME = "stub-provider-unexpected-invocation.marker"


# ---------------------------------------------------------------------------
# Synthetic kanban root
# ---------------------------------------------------------------------------


def build_synthetic_kanban_root(
    tmp_path: pathlib.Path,
    *,
    project_name: str = "probe",
    agent: str = "writer",
) -> pathlib.Path:
    """Build a full, self-contained kanban root under *tmp_path*.

    Registers one document-workflow project (git_mode=none -- no dev tree
    or git repository required) with an always-empty *agent* backlog, so
    `process_one_task` finds no work and the real `claude` CLI is never
    dispatched for a task turn. `scripts/`, `pgai_agent_kanban/`,
    `workflows/`, `pm-agent/`, and `config/` are real copies of this dev
    tree's own directories (the same set `install.sh` mirrors into a live
    install), so the wake scripts invoked against this root are
    byte-identical to production and self-locate exactly as they would on a
    real host.
    """
    root = tmp_path / "kanban_root"
    root.mkdir(parents=True, exist_ok=True)

    (root / "kanban.cfg").write_text(
        "[paths]\n\n[chain]\npm_mode = automatic\n\n"
        "[release]\nmax_rc_retries = 3\n\n[wake]\nmax_tasks_per_wake = 1\n",
        encoding="utf-8",
    )
    (root / "projects.cfg").write_text(
        f"[project:{project_name}]\npriority=1\nenabled=true\n",
        encoding="utf-8",
    )

    proj = root / "projects" / project_name
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "project.cfg").write_text(
        "[project]\n"
        f"project_name = {project_name}\n"
        "workflow_type = document\n",
        encoding="utf-8",
    )
    queues_dir = proj / "tasks" / "queues"
    queues_dir.mkdir(parents=True, exist_ok=True)
    (queues_dir / f"{agent}_backlog.md").write_text(
        f"# {project_name} {agent} backlog\n\nNo tasks queued by this fixture.\n",
        encoding="utf-8",
    )

    (root / "logs").mkdir(parents=True, exist_ok=True)
    (root / "locks").mkdir(parents=True, exist_ok=True)

    for dirname in _MIRRORED_DIRS:
        shutil.copytree(TEAM_DIR / dirname, root / dirname)

    return root


# ---------------------------------------------------------------------------
# Stub provider CLI -- never a real model call
# ---------------------------------------------------------------------------


def build_stub_provider_cli(
    tmp_path: pathlib.Path,
    *,
    probe_env_vars: Iterable[str] = (),
) -> tuple[pathlib.Path, pathlib.Path]:
    """Write a stub `claude` executable to a fresh bin/ directory.

    The stub answers `--version` (the only invocation the wake entry point
    makes once per firing regardless of queue contents -- see
    "PROVIDER-IDENTITY-LOG-BEGIN" in scripts/wake/claude.sh) by printing the
    current value of each name in *probe_env_vars*, so the wake process's
    own `log "provider CLI version: ..."` line -- captured in the real
    subprocess output -- carries the observed value out. Every other
    invocation shape (a real task dispatch would use one) is refused: the
    stub exits non-zero and appends a line to the returned marker file
    naming the unexpected arguments, so an accidental dispatch is loud and
    independently checkable after the run (`marker_file.exists()`).

    Returns (bin_dir, marker_file). Prepend *bin_dir* to PATH ahead of
    every other entry so `claude` resolves to the stub, not any real CLI
    that happens to be installed on the host.
    """
    bin_dir = tmp_path / "stub_provider_bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    marker_file = tmp_path / _STUB_MARKER_FILENAME

    probe_prints = "\n".join(
        f'printf "{var}=%s " "${{{var}:-<unset>}}"' for var in probe_env_vars
    )
    script = textwrap.dedent(
        f"""\
        #!/usr/bin/env bash
        # Stand-in for the real provider CLI. Answers --version (the only
        # shape the wake entry point uses on a firing with no queued work);
        # any other invocation shape would mean a real task dispatch was
        # attempted, which must never happen in this harness.
        if [[ "${{1:-}}" == "--version" ]]; then
            printf 'wake-entry-point-probe-stub '
            {probe_prints if probe_prints else 'true'}
            printf '\\n'
            exit 0
        fi
        echo "unexpected invocation args=[$*]" >> "{marker_file}"
        exit 99
        """
    )
    stub_path = bin_dir / "claude"
    stub_path.write_text(script, encoding="utf-8")
    stub_path.chmod(stub_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # Also seed a `python3` resolving to the exact interpreter running this
    # test process, in the same bin_dir the scrubbed PATH puts first.
    #
    # Why here and not just "add the interpreter's own directory to PATH":
    # sys.executable is commonly a chain of symlinks (a venv/pyenv shim ->
    # a versioned binary -> a system path), and `.resolve()`-ing it lands on
    # a directory (e.g. /usr/bin) that a host can ALSO populate with an
    # unrelated, older `python3` (e.g. a distro default pointing at 3.9) --
    # adding that directory to PATH would silently resolve `python3` to the
    # WRONG interpreter, failing the wake path's own Python >=3.12 guard for
    # a reason that has nothing to do with the behaviour under test. A
    # private symlink in a directory that holds nothing else has no such
    # collision.
    python3_link = bin_dir / "python3"
    if not python3_link.exists():
        python3_link.symlink_to(pathlib.Path(sys.executable).resolve())

    return bin_dir, marker_file


# ---------------------------------------------------------------------------
# Scrubbed environment
# ---------------------------------------------------------------------------


def scrubbed_wake_env(
    kanban_root: pathlib.Path,
    *,
    temp_dir: pathlib.Path,
    home_dir: pathlib.Path,
    stub_bin_dir: pathlib.Path,
    extra_env: Optional[dict] = None,
) -> dict:
    """Build a wake-subprocess environment from an explicit minimal set of
    names -- never `dict(os.environ)` with deletions layered on top.

    The universal part of this base (PATH built from an explicit directory
    list, HOME pointed at an isolated scratch directory, LANG pinned) is
    `tests.fixtures.scrubbed_subprocess_env.build_scrubbed_env`; everything
    below is the wake-specific layer on top of it -- *stub_bin_dir* wins
    PATH resolution ahead of the standard toolchain, and the synthetic
    kanban root/temp dir are added via that shared builder's own
    *extra_env*.

    Rebuilt (present, but never simply inherited):
      PATH   -- stub_bin_dir first (so `claude` resolves to the stub, never
                a real CLI the host happens to have installed, and so
                `python3` resolves to the private symlink
                `build_stub_provider_cli` seeds there -- see that
                function's docstring for why a directory holding nothing
                else is required rather than just adding the real
                interpreter's own directory to PATH), then
                /usr/local/bin:/usr/bin:/bin for the standard POSIX
                toolchain (bash, mkdir, git, etc.).
      HOME   -- a fresh, empty scratch directory unique to this run, never
                the real operator/developer $HOME. This is deliberate, not
                incidental: wake_bootstrap_init optionally sources
                $HOME/.config/pgai-kanban.cfg if present, so inheriting a
                real $HOME could silently pull in operator tunables and
                produce exactly the false-green class this harness exists
                to catch.
      LANG   -- pinned to C.UTF-8. Discovered necessary, not assumed: a
                fully scrubbed environment with no locale at all makes
                bash's `${#var}` count UTF-8 bytes instead of characters,
                which fails config_loader.sh's dashboard_status_glyph
                validation (a single-character default glyph is 3 bytes in
                UTF-8) before the wake process ever reaches the behaviour
                under test. Kept deliberately, not because the
                behaviour under test needs it, but because the entry point cannot be
                reached at all without it.
      PGAI_AGENT_KANBAN_ROOT_PATH, PGAI_AGENT_KANBAN_TEMP_DIR -- point the
                subprocess at the synthetic root and a scratch temp dir,
                never the live install.

    Scrubbed (present in a typical developer shell, deliberately absent
    here unless *extra_env* adds them back for a specific case under test):
      MAX_TASK_SECONDS, KILL_GRACE_SECONDS, BASH_MAX_TIMEOUT_MS -- the
                three tunables the behaviour under test derives from/masks;
                a fixture that inherited one of these from the invoking
                shell would pass for the wrong reason.
      PGAI_VERBOSE_MODE, PGAI_REASONING_TRACE, ANTHROPIC_API_KEY,
      AI_AUTH_MODE, PGAI_PROJECT_NAME, PGAI_PROJECT_ROOT, PGAI_TASKS_DIR,
      PGAI_QUEUE_DIR, PGAI_RELEASE_STATE_PATH, PGAI_DEV_TREE_PATH,
      PGAI_WRITER_MODEL / PGAI_CODER_MODEL / etc., KANBAN_ROOT,
      KANBAN_ROOT_PATH, PP_TEMP_DIR, PP_dev_tree_path, PP_project_name,
      PGAI_AGENT_WAKE_CONTEXT, STOP_FILE, TMPDIR, VIRTUAL_ENV, PYTHONPATH,
      any CLAUDE_* / TERM / EDITOR / SHELL / LC_* variable a real shell
      commonly carries.

    Every case-specific value (a budget, an override) is layered on top via
    *extra_env*, which wins over the rebuilt base -- the only vars a given
    test intentionally adds back are the ones its own assertions are about.
    """
    env = build_scrubbed_env(
        home_dir=home_dir,
        extra_path_dirs=(stub_bin_dir,),
        extra_env={
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(kanban_root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(temp_dir),
        },
    )
    if extra_env:
        env.update(extra_env)
    return env


# ---------------------------------------------------------------------------
# Running the real entry point
# ---------------------------------------------------------------------------


@dataclass
class WakeAcceptanceResult:
    """Result of one real wake-entry-point subprocess invocation."""

    returncode: int
    stdout: str
    stderr: str
    combined: str = field(init=False)
    env: dict = field(default_factory=dict)
    cwd: Optional[pathlib.Path] = field(default=None)

    def __post_init__(self) -> None:
        self.combined = self.stdout + self.stderr


def run_wake_entrypoint(
    kanban_root: pathlib.Path,
    *,
    cwd: pathlib.Path,
    env: dict,
    agent: str = "writer",
    max_tasks: int = 1,
    via_wake_batch: bool = False,
    timeout: int = 60,
) -> WakeAcceptanceResult:
    """Invoke the real production wake entry point as a subprocess.

    By default this invokes `scripts/wake/claude.sh` directly (the provider
    wake script an operator's crontab would fire via wake-batch.sh's
    dispatch). Set *via_wake_batch* to route through
    `scripts/wake-batch.sh` instead -- the provider-agnostic dispatcher --
    when a fixture specifically needs to exercise that dispatch hop too.

    *cwd* must be a directory outside the repository (a scratch directory);
    nothing in this harness or its callers may depend on the process
    standing inside the tree. *env* is used verbatim (see
    `scrubbed_wake_env`) -- this function never merges in `os.environ`.
    """
    entry_rel = WAKE_BATCH_REL if via_wake_batch else WAKE_CLAUDE_REL
    cmd = ["bash", str(kanban_root / entry_rel), f"--agent={agent}", f"--max-tasks={max_tasks}"]
    completed = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return WakeAcceptanceResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        env=env,
        cwd=cwd,
    )


def make_foreign_cwd(tmp_path: pathlib.Path) -> pathlib.Path:
    """Return a scratch directory under *tmp_path* that is outside the repo
    and holds none of the fixture's own files -- a working directory an
    operator's cron would never actually stand in, used to prove nothing in
    the assertions depends on the process's cwd."""
    foreign = tmp_path / "foreign_cwd"
    foreign.mkdir(parents=True, exist_ok=True)
    return foreign
