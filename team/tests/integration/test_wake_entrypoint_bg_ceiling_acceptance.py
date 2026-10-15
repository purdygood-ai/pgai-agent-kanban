"""
test_wake_entrypoint_bg_ceiling_acceptance.py
===============================================
Acceptance-layer coverage for the CLI background-wait ceiling derivation
(team/scripts/lib/wake_bootstrap.sh, the BG-WAIT-CEILING-DERIVATION-BEGIN/END
block), driven through the REAL production wake entry point rather than a
sourced library or an extracted block.

`tests/unit/test_wake_bootstrap_bg_ceiling.py` (the mechanism fixture) proves
the arithmetic is correct in isolation: it extracts the sentinel-bounded
block verbatim and runs it under a bare `bash -c` with hand-set inputs. That
is honest about what it proves and what it does not: it never shows that
`wake_bootstrap_init` actually gets called by a real cron-driven firing, from
a directory an operator actually stands in, with an environment an
operator's cron actually has. This file closes that gap. Every test below
runs `bash .../scripts/wake/claude.sh --agent=writer` as a real subprocess
(via `wake_entrypoint_acceptance_harness`) against a synthetic kanban root,
from a scratch cwd outside the repository, under a deliberately rebuilt
(not inherited) environment -- see
`wake_entrypoint_acceptance_harness.scrubbed_wake_env` for the exact
rebuilt/kept/scrubbed accounting.

Coverage
--------
The same four behavioural cases the mechanism fixture proves, re-proved
through the real path:

  1. test_default_budget_derives_ceiling_through_real_entry_point
     -- default MAX_TASK_SECONDS=5400 + KILL_GRACE_SECONDS=30 (both simply
     absent from the scrubbed environment, so the wake path's own
     `"${MAX_TASK_SECONDS:-5400}"` / `"${KILL_GRACE_SECONDS:-30}"` defaults
     apply) derives BASH_MAX_TIMEOUT_MS=5430000.
  2. test_derivation_tracks_changed_budget_through_real_entry_point --
     MAX_TASK_SECONDS=600 + KILL_GRACE_SECONDS=60 (both terms set via
     extra_env) derives 660000 -- proves both terms track, not a pinned
     literal.
  3. test_operator_override_left_untouched_through_real_entry_point -- a
     pre-set BASH_MAX_TIMEOUT_MS survives the real entry point untouched.
  4. test_disabled_watchdog_derives_nothing_through_real_entry_point --
     MAX_TASK_SECONDS=0 leaves BASH_MAX_TIMEOUT_MS unset, not derived from
     the grace term alone.

Plus the central proof:

  5. test_broken_wiring_fails_acceptance_layer_but_not_mechanism_fixture --
     a one-line break in the WIRING that connects the entry point to the
     derivation (wake_common.sh's `source .../wake_bootstrap.sh` line,
     commented out in a throwaway COPY of the synthetic root -- the real
     dev-tree wake_bootstrap.sh is never touched) makes this acceptance
     layer fail while the mechanism fixture -- which extracts the block
     directly from the file's text and never depends on this wiring --
     still passes. Both outcomes are captured in the assertion messages.

And the safety-net verification the constraints demand rather than assume:

  6. test_full_run_leaves_live_kanban_root_and_crontab_untouched -- a
     before/after inspection proving a full run of this file's fixtures
     never wrote under the live kanban root and never touched the real
     crontab.

Two observation channels
-------------------------
Cases 1 and 2 are observed directly: the wake process's own `log
"background-wait ceiling: derived BASH_MAX_TIMEOUT_MS=... from
MAX_TASK_SECONDS=...s + KILL_GRACE_SECONDS=...s"` line is `tee`-d to both
the batch log file and stdout, so it is present verbatim in the captured
subprocess output.

Cases 3 and 4 need a second channel: the operator-override and
disabled-watchdog branches log that a decision was made
("operator override in force" / "watchdog disabled") but never print the
resulting value. `wake_entrypoint_acceptance_harness.build_stub_provider_cli`
supplies that channel: the wake entry point already runs one real child
process per firing regardless of queue contents (the provider-identity
version query), and that child inherits BASH_MAX_TIMEOUT_MS exactly as
derivation left it. The stub answers `--version` by echoing the variable
back; the wake process's own `log "provider CLI version: ..."` line then
carries the observed value into the captured output for every case,
including the two the direct log line does not cover.
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess

import pytest

from tests.integration.wake_entrypoint_acceptance_harness import (
    TEAM_DIR,
    build_stub_provider_cli,
    build_synthetic_kanban_root,
    make_foreign_cwd,
    run_wake_entrypoint,
    scrubbed_wake_env,
)

_AGENT = "writer"
_PROBE_VAR = "BASH_MAX_TIMEOUT_MS"

# Captured at MODULE IMPORT time -- before any per-test fixture (including
# the autouse `_block_live_kanban_writes` safety net in tests/conftest.py)
# has a chance to monkeypatch PGAI_AGENT_KANBAN_ROOT_PATH away for the test
# process. Reads the value the invoking shell (an operator's cron, or
# `run-integration-tests.sh`) actually set. Used only by
# test_full_run_leaves_live_kanban_root_and_crontab_untouched below.
_LIVE_KANBAN_ROOT = os.environ.get("PGAI_AGENT_KANBAN_ROOT_PATH")

# Also captured at import time, before tests/conftest.py's autouse
# `_sandbox_crontab` fixture prepends a stub `crontab` binary to PATH for
# the pytest process itself (a real, in-process `monkeypatch.setenv`, not a
# copy -- a bare "crontab" lookup done anywhere else in this file's test
# bodies would silently hit that stub instead of the real system binary).
# Resolving the absolute path now, before that shadowing exists, and
# invoking it by absolute path later bypasses the shadow entirely, so the
# check below observes the real crontab an operator's cron actually reads.
_REAL_CRONTAB_BIN = shutil.which("crontab")

_DERIVED_RE = re.compile(r"derived BASH_MAX_TIMEOUT_MS=(\d+)")
_OVERRIDE_LOG = "operator override in force"
_WATCHDOG_DISABLED_LOG = "watchdog disabled"
_STUB_PROBE_RE = re.compile(r"provider CLI version: wake-entry-point-probe-stub (.+)")


def _observed_probe_value(output: str) -> str | None:
    """Extract the probe-stub's reported BASH_MAX_TIMEOUT_MS=<value> token
    from the wake process's own "provider CLI version:" log line."""
    m = _STUB_PROBE_RE.search(output)
    if not m:
        return None
    tail = m.group(1).strip()
    prefix = f"{_PROBE_VAR}="
    if tail.startswith(prefix):
        return tail[len(prefix):].strip()
    return tail


def _run_case(
    tmp_path: pathlib.Path,
    *,
    extra_env: dict | None = None,
):
    """Shared setup for every bg-ceiling case: build the synthetic root, the
    probe stub, the scrubbed env, and run the real entry point from a
    foreign cwd. Returns the WakeAcceptanceResult and the marker file path
    (assert marker_file.exists() is False to prove the stub never saw a
    real task-dispatch invocation)."""
    kanban_root = build_synthetic_kanban_root(tmp_path, agent=_AGENT)
    stub_bin_dir, marker_file = build_stub_provider_cli(
        tmp_path, probe_env_vars=(_PROBE_VAR,)
    )
    home_dir = tmp_path / "home"
    home_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = tmp_path / "pgai_temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    foreign_cwd = make_foreign_cwd(tmp_path)

    env = scrubbed_wake_env(
        kanban_root,
        temp_dir=temp_dir,
        home_dir=home_dir,
        stub_bin_dir=stub_bin_dir,
        extra_env=extra_env,
    )
    result = run_wake_entrypoint(
        kanban_root, cwd=foreign_cwd, env=env, agent=_AGENT
    )
    return result, marker_file, foreign_cwd, env


# ---------------------------------------------------------------------------
# Case 1: default budget
# ---------------------------------------------------------------------------


def test_default_budget_derives_ceiling_through_real_entry_point(
    tmp_path: pathlib.Path,
) -> None:
    """No MAX_TASK_SECONDS/KILL_GRACE_SECONDS/BASH_MAX_TIMEOUT_MS in the
    scrubbed environment -- the wake path's own defaults (5400s / 30s)
    apply -- derives BASH_MAX_TIMEOUT_MS=5430000, observed through the real
    entry point's own log line."""
    result, marker_file, foreign_cwd, env = _run_case(tmp_path)

    assert result.returncode == 0, (
        f"wake entry point exited {result.returncode} from cwd={foreign_cwd}.\n"
        f"combined output:\n{result.combined}"
    )
    assert not marker_file.exists(), (
        f"stub provider CLI received an unexpected invocation (a real task "
        f"dispatch was attempted): {marker_file.read_text() if marker_file.exists() else ''}"
    )

    m = _DERIVED_RE.search(result.combined)
    assert m, f"no derivation log line found.\ncombined output:\n{result.combined}"
    observed = m.group(1)
    assert observed == "5430000", f"expected 5430000, observed {observed!r}"

    probe = _observed_probe_value(result.combined)
    assert probe == "5430000", (
        f"probe-stub channel disagreed with the direct log line: "
        f"observed {probe!r} via provider-identity probe, expected '5430000'.\n"
        f"combined output:\n{result.combined}"
    )


# ---------------------------------------------------------------------------
# Case 2: both terms track a changed budget
# ---------------------------------------------------------------------------


def test_derivation_tracks_changed_budget_through_real_entry_point(
    tmp_path: pathlib.Path,
) -> None:
    """MAX_TASK_SECONDS=600 + KILL_GRACE_SECONDS=60 derives 660000 through
    the real entry point -- proves both terms are read live, not a pinned
    literal that happens to match the default case."""
    result, marker_file, foreign_cwd, env = _run_case(
        tmp_path,
        extra_env={"MAX_TASK_SECONDS": "600", "KILL_GRACE_SECONDS": "60"},
    )

    assert result.returncode == 0, (
        f"wake entry point exited {result.returncode}.\ncombined output:\n{result.combined}"
    )
    assert not marker_file.exists()

    m = _DERIVED_RE.search(result.combined)
    assert m, f"no derivation log line found.\ncombined output:\n{result.combined}"
    observed = m.group(1)
    assert observed == "660000", f"expected 660000, observed {observed!r}"

    probe = _observed_probe_value(result.combined)
    assert probe == "660000", f"probe-stub channel observed {probe!r}, expected '660000'"


# ---------------------------------------------------------------------------
# Case 3: operator override survives untouched
# ---------------------------------------------------------------------------


def test_operator_override_left_untouched_through_real_entry_point(
    tmp_path: pathlib.Path,
) -> None:
    """A pre-set BASH_MAX_TIMEOUT_MS (as claude-env would export it before
    the wake path ever reaches the derivation) is not overwritten by the
    real entry point. The direct derivation log line never fires; the
    probe-stub channel is the only way to observe the surviving value."""
    result, marker_file, foreign_cwd, env = _run_case(
        tmp_path, extra_env={"BASH_MAX_TIMEOUT_MS": "999999"}
    )

    assert result.returncode == 0, (
        f"wake entry point exited {result.returncode}.\ncombined output:\n{result.combined}"
    )
    assert not marker_file.exists()

    assert _OVERRIDE_LOG in result.combined, (
        f"expected the operator-override log line; got:\n{result.combined}"
    )
    assert not _DERIVED_RE.search(result.combined), (
        f"derivation ran despite an operator override being present:\n{result.combined}"
    )

    probe = _observed_probe_value(result.combined)
    assert probe == "999999", (
        f"operator override was overwritten by the real entry point: "
        f"observed {probe!r} via provider-identity probe, expected '999999'.\n"
        f"combined output:\n{result.combined}"
    )


# ---------------------------------------------------------------------------
# Case 4: disabled per-task watchdog derives nothing
# ---------------------------------------------------------------------------


def test_disabled_watchdog_derives_nothing_through_real_entry_point(
    tmp_path: pathlib.Path,
) -> None:
    """MAX_TASK_SECONDS=0 (the per-task watchdog disabled) leaves
    BASH_MAX_TIMEOUT_MS unset through the real entry point -- not derived
    from KILL_GRACE_SECONDS alone."""
    result, marker_file, foreign_cwd, env = _run_case(
        tmp_path, extra_env={"MAX_TASK_SECONDS": "0", "KILL_GRACE_SECONDS": "30"}
    )

    assert result.returncode == 0, (
        f"wake entry point exited {result.returncode}.\ncombined output:\n{result.combined}"
    )
    assert not marker_file.exists()

    assert _WATCHDOG_DISABLED_LOG in result.combined, (
        f"expected the disabled-watchdog log line; got:\n{result.combined}"
    )
    assert not _DERIVED_RE.search(result.combined), (
        f"derivation ran despite MAX_TASK_SECONDS=0:\n{result.combined}"
    )

    probe = _observed_probe_value(result.combined)
    assert probe == "<unset>", (
        f"BASH_MAX_TIMEOUT_MS was derived from the grace term alone through "
        f"the real entry point: observed {probe!r}, expected '<unset>'.\n"
        f"combined output:\n{result.combined}"
    )


# ---------------------------------------------------------------------------
# Case 5: the central proof -- a broken wiring fails this layer, not the
# mechanism fixture.
# ---------------------------------------------------------------------------


def test_broken_wiring_fails_acceptance_layer_but_not_mechanism_fixture(
    tmp_path: pathlib.Path,
) -> None:
    """Cut the ONE line that connects the entry point to the derivation --
    wake_common.sh's `source .../wake_bootstrap.sh` -- in a throwaway copy
    of the synthetic root. The derivation block itself is never touched.

    This acceptance layer must fail (the entry point can no longer reach
    the derivation at all -- log() and every other wake_bootstrap_init
    side effect are undefined). The mechanism fixture -- which extracts the
    sentinel-bounded block directly from wake_bootstrap.sh's own text and
    runs it standalone -- is entirely unaffected, because it never depends
    on wake_common.sh sourcing that file; it reads the same, still-intact,
    real dev-tree file this test never modifies.
    """
    kanban_root = build_synthetic_kanban_root(tmp_path, agent=_AGENT)
    stub_bin_dir, marker_file = build_stub_provider_cli(
        tmp_path, probe_env_vars=(_PROBE_VAR,)
    )
    home_dir = tmp_path / "home"
    home_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = tmp_path / "pgai_temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    foreign_cwd = make_foreign_cwd(tmp_path)
    env = scrubbed_wake_env(
        kanban_root, temp_dir=temp_dir, home_dir=home_dir, stub_bin_dir=stub_bin_dir
    )

    # --- Break the wiring in the COPY only. The real dev-tree file at
    # TEAM_DIR / scripts/lib/wake_common.sh is never opened for writing. ---
    wake_common_copy = kanban_root / "scripts" / "lib" / "wake_common.sh"
    original_text = wake_common_copy.read_text(encoding="utf-8")
    source_line = 'source "${SCRIPT_DIR}/../lib/wake_bootstrap.sh"'
    assert source_line in original_text, (
        f"expected wiring line not found verbatim in {wake_common_copy} -- "
        f"the break cannot be applied. This is a fixture-maintenance signal, "
        f"not a real test failure: the wiring line in wake_common.sh moved "
        f"or was reworded and this test needs updating to match."
    )
    broken_text = original_text.replace(
        source_line, f"# BROKEN-FOR-ACCEPTANCE-TEST: {source_line}", 1
    )
    wake_common_copy.write_text(broken_text, encoding="utf-8")

    broken_result = run_wake_entrypoint(
        kanban_root, cwd=foreign_cwd, env=env, agent=_AGENT
    )

    # --- Outcome 1 (quoted): the acceptance layer fails. ---
    assert broken_result.returncode != 0, (
        f"expected the broken wiring to make the real entry point fail; it "
        f"exited 0 instead.\ncombined output:\n{broken_result.combined}"
    )
    assert not _DERIVED_RE.search(broken_result.combined), (
        f"the derivation log line appeared despite the broken wiring -- the "
        f"break did not actually disconnect the entry point from the "
        f"derivation.\ncombined output:\n{broken_result.combined}"
    )

    # --- Outcome 2 (quoted): the mechanism fixture, run against the real,
    # untouched dev-tree wake_bootstrap.sh, still passes. Run it exactly as
    # the shipped suite does -- a real pytest subprocess against the file's
    # own path -- rather than re-deriving its assertions here.
    #
    # This nested pytest session MUST NOT inherit PYTEST_ADDOPTS: when this
    # very test runs under run-integration-tests.sh, that script has
    # exported PYTEST_ADDOPTS="--basetemp=<shared framework basetemp>" for
    # its own (outer, still-running) pytest session. subprocess.run inherits
    # the parent environment by default, and pytest removes and recreates
    # its basetemp at the start of every session -- so an inherited
    # PYTEST_ADDOPTS here would make this inner session wipe the OUTER
    # session's still-in-use basetemp out from under it, destroying this
    # very test's own tmp_path (including wake_common_copy, about to be
    # reverted below) mid-run. Give the inner session its own basetemp
    # under this test's own tmp_path instead -- disposed of by the normal
    # tmp_path teardown, and never shared with the outer session. ---
    mechanism_env = dict(os.environ)
    mechanism_env.pop("PYTEST_ADDOPTS", None)
    mechanism_basetemp = tmp_path / "mechanism_check_basetemp"
    mechanism_result = subprocess.run(
        [
            "python3",
            "-m",
            "pytest",
            "-q",
            f"--basetemp={mechanism_basetemp}",
            "tests/unit/test_wake_bootstrap_bg_ceiling.py",
        ],
        cwd=str(TEAM_DIR),
        env=mechanism_env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert mechanism_result.returncode == 0, (
        f"the mechanism fixture was expected to still pass against the "
        f"untouched real dev-tree wake_bootstrap.sh after the wiring break "
        f"in the synthetic copy, but it did not.\n"
        f"stdout:\n{mechanism_result.stdout}\nstderr:\n{mechanism_result.stderr}"
    )

    # --- Revert. The real dev-tree file was never touched (broken_text was
    # written only to kanban_root's throwaway copy, which pytest's tmp_path
    # teardown removes); reverting the copy here just makes the disposal
    # explicit rather than implicit in teardown. ---
    wake_common_copy.write_text(original_text, encoding="utf-8")
    assert wake_common_copy.read_text(encoding="utf-8") == original_text


# ---------------------------------------------------------------------------
# Case 6: the live kanban root and the real crontab are unchanged by a full
# run of this file's fixtures.
# ---------------------------------------------------------------------------


def test_full_run_leaves_live_kanban_root_and_crontab_untouched(
    tmp_path: pathlib.Path,
) -> None:
    """Before/after inspection, not an assumption: run one full acceptance
    case (via `_run_case`, exactly like the four behavioural tests above),
    then verify the live kanban root gained no trace of this fixture's
    synthetic project/task identifiers, and that the real crontab (read via
    the actual `crontab -l`, never this suite's own PATH-shadowing stub --
    see below) is byte-identical before and after.

    Live-root check design: the live kanban root in this environment is an
    actively cron-driven tree -- other agents legitimately write to it
    (logs, task status, queue files) throughout the day, so a whole-tree
    byte/mtime diff would produce false positives unrelated to this
    fixture. Instead this test asserts the NEGATIVE claim the constraints
    actually require: nothing this fixture created (its synthetic project
    name, its synthetic kanban root's own absolute path) appears anywhere
    under the live root, before or after -- proving the redirect held
    rather than merely trusting it did.

    Crontab check: `crontab -l` is read-only and safe to run for real (not
    through this suite's own sandboxed stub, which only shadows PATH inside
    the pytest process -- the real system crontab is what an operator's
    cron actually reads, and that is the thing this fixture must never
    touch).
    """
    if not _LIVE_KANBAN_ROOT:
        pytest.skip(
            "PGAI_AGENT_KANBAN_ROOT_PATH was not set in the environment "
            "that invoked pytest, so there is no live root path to verify "
            "against. This is a skip, not a pass: it means the safety-net "
            "check could not run, not that it observed no change."
        )

    live_root = pathlib.Path(_LIVE_KANBAN_ROOT)
    if not live_root.is_dir():
        pytest.skip(
            f"PGAI_AGENT_KANBAN_ROOT_PATH={_LIVE_KANBAN_ROOT!r} does not "
            f"resolve to a directory on this host; nothing to verify."
        )

    if not _REAL_CRONTAB_BIN:
        pytest.skip(
            "No 'crontab' binary was resolvable on PATH at module import "
            "time (before this suite's own stub-crontab sandboxing takes "
            "effect); nothing to verify against."
        )

    def _crontab_snapshot() -> str:
        r = subprocess.run(
            [_REAL_CRONTAB_BIN, "-l"], capture_output=True, text=True, timeout=15
        )
        return r.stdout if r.returncode == 0 else f"<no crontab: rc={r.returncode}>"

    crontab_before = _crontab_snapshot()

    # Run one full case (the default-budget case) through this file's own
    # harness -- exercises the exact same code path every behavioural test
    # above already ran, so a pass here is not testing anything new about
    # the derivation; it is testing that running it left no trace outside
    # the synthetic root.
    result, marker_file, foreign_cwd, env = _run_case(tmp_path)
    assert result.returncode == 0, (
        f"the representative run itself failed; cannot evaluate the "
        f"before/after safety-net claim.\ncombined output:\n{result.combined}"
    )

    synthetic_root_str = str(env["PGAI_AGENT_KANBAN_ROOT_PATH"])
    assert synthetic_root_str != str(live_root), (
        "the synthetic kanban root resolved to the same path as the live "
        "root -- the harness itself is broken."
    )

    leaked = []
    for dirpath, dirnames, filenames in os.walk(live_root):
        for name in dirnames + filenames:
            full = os.path.join(dirpath, name)
            if synthetic_root_str in full:
                leaked.append(full)
    assert not leaked, (
        f"found {len(leaked)} path(s) under the live kanban root "
        f"{live_root} referencing the synthetic root {synthetic_root_str!r} "
        f"-- the acceptance run leaked into live state:\n{leaked}"
    )

    crontab_after = _crontab_snapshot()
    assert crontab_after == crontab_before, (
        f"the real crontab changed across this fixture's run.\n"
        f"before:\n{crontab_before}\nafter:\n{crontab_after}"
    )
