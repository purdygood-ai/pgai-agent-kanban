"""
scrubbed_subprocess_env.py
============================
Shared builder for a subprocess environment assembled from an explicit,
minimal set of names -- never `dict(os.environ)` with names deleted back
out of it.

WHY EXPLICIT BUILD-UP, NEVER INHERIT-THEN-DELETE
--------------------------------------------------
A deletion list has to be re-derived every time the host gains a new
environment variable that happens to change the behaviour under test --
nobody notices the gap until a suite goes red on one machine and stays
green on every other, because the verdict quietly became a property of the
host instead of a property of the tree. Building the child's environment
from a fixed, named set closes that gap structurally: a name this builder
does not construct is absent from the result, full stop, regardless of
what the invoking shell happens to carry. `docs/test-authoring-guidelines.md`
names the class this avoids as anti-pattern 2 (environment-coupled
fixtures).

This module generalises the shape that
`tests.integration.wake_entrypoint_acceptance_harness.scrubbed_wake_env`
proved out first for the real wake entry point; that function is now
re-expressed on top of `build_scrubbed_env` below rather than building its
own copy of the same base.

WHAT THE BASE BUILDS
---------------------
PATH -- *extra_path_dirs* (if any), in the order given, then the standard
        POSIX toolchain directories (/usr/local/bin:/usr/bin:/bin) so
        common tools the subprocess needs (bash, git, mkdir, ...) still
        resolve. A caller whose subprocess must resolve a stub or
        test-private binary ahead of the system tools passes that
        directory as *extra_path_dirs* -- the base never hardcodes a stub
        location itself, because which stub (if any) belongs to the
        specific behaviour under test, not to every subprocess test. (See
        `scrubbed_wake_env` for a concrete case: it passes the wake
        harness's stub-provider bin directory here so a stubbed `claude`
        and a private `python3` symlink win PATH resolution ahead of
        whatever the host happens to have installed.)
HOME -- always *home_dir*, a scratch directory the caller owns -- never the
        real operator/developer $HOME. A subprocess that inherits a real
        $HOME can silently pick up whatever dotfiles or config that account
        happens to carry, turning a test's verdict into a property of
        whoever's machine it runs on rather than of the tree under test.
        This was discovered by a real failure, not assumed: see
        `scrubbed_wake_env`'s docstring for the wake-specific case (a
        config file `wake_bootstrap_init` optionally sources from $HOME)
        this guards against.
LANG -- pinned to *lang* (default `C.UTF-8`) rather than left absent or
        inherited. A fully scrubbed environment with no locale at all
        changes how some shell builtins measure string length (bytes
        instead of characters), which can break a script under test before
        the behaviour being verified is ever reached. This default was
        chosen after a real failure, not assumed to be harmless: see
        `scrubbed_wake_env`'s docstring for the specific breakage
        (`config_loader.sh`'s glyph-width validation) this default avoids.

Every case-specific value (a budget variable, an override, a project root)
is layered on top via *extra_env*, applied last, so a caller-supplied value
always wins over the base for the same name -- the only variables a given
test adds back are the ones its own assertions are actually about.

SCRUBBING AN INHERITED BASE, NOT REBUILDING IT
------------------------------------------------
`build_scrubbed_env` above assumes a caller can name every variable a
subprocess needs. A fixture that shells out to a real, unstubbed toolchain
-- `git`, `bash`, `ssh` -- to drive a CM script end-to-end cannot: it needs
`dict(os.environ)` for the ambient toolchain configuration it does not
control. That caller still owes one guarantee independent of how much of
the ambient environment it lets through: an agent-scoped signal such as
`PGAI_AGENT_WAKE_ACTIVE` -- exported into every real agent's environment by
the wake layer -- must never reach the child by simple inheritance, because
the script under test branches on that signal to tell an operator from an
agent. `scrub_agent_scoped_env` removes exactly those names from a caller-
built base dict, in place, before the caller layers its own `extra_env` on
top -- a test that wants the wake context still gets it, explicitly, the
same way every other case-specific value is added back.

`scrub_wake_tunable_env` is the same shape for a second family: the
[wake] tunables a wake's own config load exports into every agent it
dispatches (`WAKE_TUNABLE_ENV_NAMES`). `load_config` reads a set one as an
operator override of the cfg value, so a sandboxed wake built from an
inherited base would obey the invoking wake's value -- LOCK_WAIT_SECONDS
most visibly -- instead of the [wake] section its own kanban.cfg pins. A
fixture that writes [wake] values into a sandbox kanban.cfg scrubs these
before layering its own `extra_env`; a test that means to exercise the
operator override sets the name back explicitly through that `extra_env`.
"""

from __future__ import annotations

import os
import pathlib
from typing import Iterable, Optional, Union

_DEFAULT_LANG = "C.UTF-8"
_STANDARD_TOOLCHAIN_DIRS = ("/usr/local/bin", "/usr/bin", "/bin")

# Names that tell a script under test it is running inside an agent wake
# rather than an interactive operator session. A fixture that inherits the
# ambient environment for a real subprocess must still strip these before
# any case-specific extra_env is layered on top, so a test's assertions
# about operator-vs-agent behaviour are never a property of the shell that
# happened to launch pytest.
AGENT_SCOPED_ENV_NAMES = ("PGAI_AGENT_WAKE_ACTIVE",)

# Every environment variable team/scripts/lib/config_loader.sh's
# load_config exports for a [wake] key -- the Python twin of that file's
# CONFIG_LOADER_WAKE_ENV_NAMES array. A set one outranks the cfg value
# (the operator override), so any of them inherited from the shell that
# launched pytest would override a sandboxed kanban.cfg's own [wake]
# section. tests/unit/test_wake_env_names_scrub.py fails when this tuple,
# the bash array, and the loader's [wake] exports disagree.
WAKE_TUNABLE_ENV_NAMES = (
    "MAX_TASKS_PER_WAKE",
    "MAX_RUNTIME_SECONDS",
    "PAUSE_BETWEEN_TASKS",
    "STOP_ON_BLOCKED",
    "STOP_FILE",
    "PM_MAX_TASKS",
    "MAX_TASK_SECONDS",
    "MAX_PROJECT_SECONDS",
    "KILL_GRACE_SECONDS",
    "TRANSIENT_RETRY_CAP",
    "ZERO_PROGRESS_CAP",
    "BLOCK_STORM_WINDOW",
    "BLOCK_HOLD_CAP",
    "SYSTEMIC_BLOCK_REASONS",
    "LOCK_WAIT_SECONDS",
    "LOCK_STARVATION_FIRINGS",
    "USAGE_LIMIT_PAUSE_SECONDS",
    "MODEL_PREFLIGHT_TTL_SECONDS",
    "MODEL_PREFLIGHT_TIMEOUT_SECONDS",
)


def build_scrubbed_env(
    *,
    home_dir: Union[str, pathlib.Path],
    extra_path_dirs: Iterable[Union[str, pathlib.Path]] = (),
    lang: str = _DEFAULT_LANG,
    extra_env: Optional[dict] = None,
) -> dict:
    """Build a subprocess environment from an explicit minimal set of names.

    Required:
      home_dir        -- every subprocess test needs an isolated scratch
                          HOME. There is no default: the entire point is
                          that it must never be the invoking process's real
                          $HOME, so a caller must always name one
                          explicitly (typically a `tmp_path` subdirectory).

    Optional:
      extra_path_dirs -- directories to prepend to PATH ahead of the
                          standard POSIX toolchain, in the order given.
                          Empty by default -- most subprocess tests need
                          nothing ahead of the standard toolchain; pass a
                          directory here only when the subprocess must
                          resolve a stub or test-private binary first.
      lang             -- defaults to "C.UTF-8", a locale-stable choice
                          that avoids byte-vs-character counting surprises
                          in shell scripts under test. Override only when
                          the behaviour under test specifically depends on
                          a different locale.
      extra_env        -- case-specific values (a budget variable, a
                          project root, an operator override) layered on
                          top last, so a caller-supplied value always wins
                          over the base for the same name.

    Returns a plain dict suitable for `subprocess.run(..., env=...)`. Never
    merges `os.environ` -- every name present in the result is either
    constructed here or supplied by the caller through extra_env.
    """
    path_entries = [str(p) for p in extra_path_dirs] + list(_STANDARD_TOOLCHAIN_DIRS)
    env: dict = {
        "PATH": os.pathsep.join(path_entries),
        "HOME": str(home_dir),
        "LANG": lang,
    }
    if extra_env:
        env.update(extra_env)
    return env


def scrub_agent_scoped_env(env: dict) -> dict:
    """Remove agent-wake-scoped signals (`AGENT_SCOPED_ENV_NAMES`) from *env*, in place.

    For a fixture that must build its base environment from a real,
    inherited `dict(os.environ)` -- because the subprocess under test drives
    an unstubbed toolchain (`git`, `bash`, ...) that a caller cannot fully
    enumerate -- this is the counterpart to `build_scrubbed_env` above: it
    does not replace the base, it removes the small, named set of variables
    that would otherwise let the invoking shell's own agent-wake status leak
    into a child whose behaviour depends on telling an operator from an
    agent apart.

    Call this on the base dict *before* the caller layers its own
    `extra_env` on top, so a test that deliberately wants the wake context
    still gets it -- explicitly, through `extra_env`, the same way every
    other case-specific value is added back -- rather than by accident of
    whatever launched pytest.

    Returns *env* for convenient chaining; mutates *env* in place.
    """
    for name in AGENT_SCOPED_ENV_NAMES:
        env.pop(name, None)
    return env


def scrub_wake_tunable_env(env: dict) -> dict:
    """Remove every inherited [wake] tunable (`WAKE_TUNABLE_ENV_NAMES`) from *env*, in place.

    For a fixture that builds a sandboxed wake's environment from an
    inherited `dict(os.environ)` and pins [wake] values in the sandbox's own
    kanban.cfg: without this, a LOCK_WAIT_SECONDS (or any sibling) the
    invoking wake exported reaches the sandboxed wake, and load_config reads
    it as an operator override of the sandbox's cfg value.

    Call this on the base dict *before* the caller layers its own
    `extra_env` on top, so a test that deliberately sets one of these names
    -- to exercise the operator override, or to pin a budget -- still gets
    exactly the value it chose.

    Returns *env* for convenient chaining; mutates *env* in place.
    """
    for name in WAKE_TUNABLE_ENV_NAMES:
        env.pop(name, None)
    return env
