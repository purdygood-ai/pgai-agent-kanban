"""
test_wake_task_timeout_budget.py
=================================
Behavioral fixtures for the per-task runtime budget: the effective timeout
a task is armed with, the startup warning that the three runtime ceilings
nest, and the wall-clock deadline watchdog.

The mechanism under test, across three files:

  team/scripts/lib/wake_task_budget.sh -- wd_effective_task_timeout prints
    min(max_task, remaining project budget, remaining batch budget) with 0
    meaning "no bound"; wd_warn_runtime_bound_nesting logs a WARNING for a
    per-task limit AT OR ABOVE the project or batch cap (equality
    included) and for a project cap above the batch cap, naming the
    invariant task < project <= batch; wd_chain_task_timeout_budget wires
    the first into run_project_chain's own clocks; _wd_epoch_now is the
    one clock the watchdog reads.

  team/scripts/lib/wake_driver_invoke.sh -- the watchdog block (extracted
    verbatim between its WATCHDOG-INVOKE-BEGIN/END markers, as the
    sibling watchdog tests do) compares _wd_epoch_now against an absolute
    deadline taken at launch instead of counting sleep-1 iterations, so a
    loaded host that stretches every iteration cannot delay the fire; its
    fire line names the real elapsed seconds against the limit; its
    held-lock refresh cadence is measured in wall-clock seconds.

  team/scripts/lib/wake_driver.sh -- run_project_chain calls the warning
    once per project and wd_chain_task_timeout_budget before every
    dispatch; the wiring is asserted structurally here because driving the
    whole chain needs a provider, a queue and a lock, which the acceptance
    harnesses own.

Acceptance criteria exercised:
  - all three ceilings at 120 and a dispatch 60s into the batch arm a 60s
    timeout, so a 100s task is preempted at the batch cap.
  - with MAX_TASK_SECONDS unset, the armed timeout is the config
    registry default for [wake] max_task_seconds, never 0 (no bound),
    in both the budget stage and the invoke stage's watchdog; with
    MAX_RUNTIME_SECONDS unset, the batch bound is the registry default
    for [wake] max_runtime_seconds; an explicit 0 still disables either.
  - every batch-budget check reads that one rule (wd_batch_budget_left /
    wd_batch_budget_reached): with MAX_RUNTIME_SECONDS=0 the dispatch loop
    in run_project_chain dispatches with no batch stop, the contended-
    project retry pass in wake_common_run retries every project, and the
    lock wait is uncapped by the batch; unset or blank holds them to the
    registry default; a positive value stops them at it. The dispatch-loop
    check and the retry pass are run verbatim from the shipped files on a
    fake clock.
  - the warning fires on max_task >= project and max_task >= batch, not
    only on strict excess, and its text states task < project <= batch.
  - under a mocked clock that jumps 12s per poll (a starved host), a 30s
    deadline fires on the first poll past it -- 3 iterations, not 30 --
    and the fire line reads "fired after 36s (limit 30s)".
  - on the real clock, a 3s deadline against a provider that never exits
    fires within limit + 2s.
  - the lock-refresh cadence still fires every N wall-clock seconds inside
    the deadline-driven loop.

Every harness runs under tmp_path (redirected by conftest under the
framework temp root) and owns every process it starts.
"""

from __future__ import annotations

import os
import pathlib
import re
import signal
import subprocess

import pytest

# ---------------------------------------------------------------------------
# Paths (relative to this file: team/tests/unit/ -> team/ -> repo root)
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]  # team/
_LIB_DIR = _TEAM_DIR / "scripts" / "lib"
_TASK_BUDGET_SH = _LIB_DIR / "wake_task_budget.sh"
_DRIVER_INVOKE = _LIB_DIR / "wake_driver_invoke.sh"
_DRIVER_SH = _LIB_DIR / "wake_driver.sh"
_DRIVER_FINALIZE = _LIB_DIR / "wake_driver_finalize.sh"
_BOOTSTRAP_SH = _LIB_DIR / "wake_bootstrap.sh"
_LOCK_PROVENANCE_SH = _LIB_DIR / "lock_provenance.sh"
_TEMP_SH = _LIB_DIR / "temp.sh"

_WATCHDOG_BEGIN = "# WATCHDOG-INVOKE-BEGIN"
_WATCHDOG_END = "# WATCHDOG-INVOKE-END"

_INVARIANT_TEXT = "task < project <= batch"


def _diagnostic(result: subprocess.CompletedProcess) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _run_bash(script: str, cwd: pathlib.Path, timeout: float = 30) -> subprocess.CompletedProcess:
    """Run *script* under bash in its own session; on timeout kill the whole
    group so no descendant outlives the test, then let the timeout stand."""
    proc = subprocess.Popen(
        ["bash", "-c", script],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(cwd),
        env=dict(os.environ),
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        proc.communicate()
        raise
    return subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)


def _extract_watchdog_block() -> str:
    text = _DRIVER_INVOKE.read_text(encoding="utf-8")
    begin = text.index(_WATCHDOG_BEGIN)
    end = text.index(_WATCHDOG_END) + len(_WATCHDOG_END)
    return text[begin:end]


# ---------------------------------------------------------------------------
# (1) wd_effective_task_timeout: the minimum over the enabled bounds.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "args, expected, why",
    [
        # The filed scenario: three equal ceilings, dispatch 60s in -> 60s left.
        (("120", "120", "60", "120", "60"), "60", "equal ceilings cap at the remaining batch budget"),
        (("5400", "14400", "100", "14400", "100"), "5400", "shipped defaults: task limit is the smallest"),
        (("5400", "14400", "13000", "14400", "13000"), "1400", "late dispatch: remaining project budget wins"),
        (("5400", "0", "9999", "14400", "14000"), "400", "project cap disabled: task vs remaining batch"),
        (("0", "0", "0", "14400", "100"), "14300", "per-task bound disabled: remaining batch still caps"),
        (("0", "600", "100", "14400", "100"), "500", "per-task bound disabled: remaining project still caps"),
        (("0", "0", "0", "0", "0"), "0", "every ceiling disabled -> watchdog off"),
        (("5400", "120", "130", "120", "121"), "1", "exhausted budget clamps to 1s, never to 0/disabled"),
        (("abc", "", "5", " 90 ", "10"), "80", "non-numeric reads as no bound; whitespace is tolerated"),
    ],
)
def test_effective_timeout_is_the_minimum_over_enabled_bounds(
    tmp_path: pathlib.Path, args: tuple[str, ...], expected: str, why: str
) -> None:
    quoted = " ".join(f"'{a}'" for a in args)
    result = _run_bash(
        f"set -uo pipefail; source {str(_TASK_BUDGET_SH)!r}; wd_effective_task_timeout {quoted}",
        cwd=tmp_path,
    )
    assert result.returncode == 0, _diagnostic(result)
    assert result.stdout.strip() == expected, f"{why}\n{_diagnostic(result)}"


def test_chain_budget_reads_the_chain_clocks(tmp_path: pathlib.Path) -> None:
    """wd_chain_task_timeout_budget derives both elapsed values from the
    chain's own start epochs against _wd_epoch_now, and an unset clock
    reads as a full budget."""
    script = f"""
set -uo pipefail
source {str(_TASK_BUDGET_SH)!r}
_wd_epoch_now() {{ echo 1000; }}
_eff_task=120; _eff_project=120; MAX_RUNTIME_SECONDS=120
_project_start_epoch=940; WAKE_START_EPOCH=940
wd_chain_task_timeout_budget
echo "AT_60S=${{_task_timeout_budget_seconds}}"
_project_start_epoch=990; WAKE_START_EPOCH=940
wd_chain_task_timeout_budget
echo "PROJECT_FRESH_BATCH_OLD=${{_task_timeout_budget_seconds}}"
unset _project_start_epoch WAKE_START_EPOCH
wd_chain_task_timeout_budget
echo "UNSET_CLOCKS=${{_task_timeout_budget_seconds}}"
"""
    result = _run_bash(script, cwd=tmp_path)
    assert result.returncode == 0, _diagnostic(result)
    values = dict(re.findall(r"^(\w+)=(.*)$", result.stdout, re.M))
    assert values["AT_60S"] == "60", _diagnostic(result)
    assert values["PROJECT_FRESH_BATCH_OLD"] == "60", _diagnostic(result)
    assert values["UNSET_CLOCKS"] == "120", _diagnostic(result)


# ---------------------------------------------------------------------------
# (1b) An unset runtime budget arms the config registry default, never 0.
# ---------------------------------------------------------------------------


def _registry_default(key: str) -> str:
    """The [wake] *key* default as the config loader's Key Registry block
    documents it (``<key>  OPTIONAL  <n>``)."""
    text = (_LIB_DIR / "config_loader.sh").read_text(encoding="utf-8")
    match = re.search(rf"^#\s+{key}\s+OPTIONAL\s+(\d+)\s*$", text, re.M)
    assert match, f"config_loader.sh Key Registry no longer lists [wake] {key} with a default"
    return match.group(1)


def test_unset_max_task_seconds_arms_the_registry_default(tmp_path: pathlib.Path) -> None:
    """With MAX_TASK_SECONDS unset and the project and batch budgets off,
    the watchdog is armed at the config loader's default for [wake]
    max_task_seconds -- the value config_wake_max_task_seconds_default
    prints and the Key Registry documents -- not at 0 (no bound). The
    driver's seed (wd_configured_max_task_seconds) and the chain's own
    unset-_eff_task fallback both resolve there; an explicit value,
    0 included, keeps its meaning."""
    script = f"""
set -uo pipefail
unset MAX_TASK_SECONDS _eff_task
source {str(_TASK_BUDGET_SH)!r}
_wd_epoch_now() {{ echo 1000; }}
echo "LOADER_DEFAULT=$(config_wake_max_task_seconds_default)"
_eff_project=0; MAX_RUNTIME_SECONDS=0; _project_start_epoch=1000; WAKE_START_EPOCH=1000
wd_chain_task_timeout_budget
echo "UNSET_EFF_TASK_ARMED=${{_task_timeout_budget_seconds}}"
_eff_task="$(wd_configured_max_task_seconds)"
echo "UNSET_SEED=${{_eff_task}}"
wd_chain_task_timeout_budget
echo "UNSET_SEED_ARMED=${{_task_timeout_budget_seconds}}"
MAX_TASK_SECONDS=""
echo "BLANK_SEED=$(wd_configured_max_task_seconds)"
MAX_TASK_SECONDS=0
echo "EXPLICIT_ZERO_SEED=$(wd_configured_max_task_seconds)"
_eff_task="$(wd_configured_max_task_seconds)"
wd_chain_task_timeout_budget
echo "EXPLICIT_ZERO_ARMED=${{_task_timeout_budget_seconds}}"
MAX_TASK_SECONDS=600
echo "EXPLICIT_SEED=$(wd_configured_max_task_seconds)"
"""
    result = _run_bash(script, cwd=tmp_path)
    assert result.returncode == 0, _diagnostic(result)
    values = dict(re.findall(r"^(\w+)=(.*)$", result.stdout, re.M))
    registry_default = _registry_default("max_task_seconds")
    assert values["LOADER_DEFAULT"] == registry_default, _diagnostic(result)
    assert values["UNSET_EFF_TASK_ARMED"] == registry_default, _diagnostic(result)
    assert values["UNSET_SEED"] == registry_default, _diagnostic(result)
    assert values["UNSET_SEED_ARMED"] == registry_default, _diagnostic(result)
    assert values["BLANK_SEED"] == registry_default, _diagnostic(result)
    assert values["EXPLICIT_ZERO_SEED"] == "0", _diagnostic(result)
    assert values["EXPLICIT_ZERO_ARMED"] == "0", _diagnostic(result)
    assert values["EXPLICIT_SEED"] == "600", _diagnostic(result)


def test_unset_max_runtime_seconds_bounds_the_batch_at_the_registry_default(tmp_path: pathlib.Path) -> None:
    """With MAX_RUNTIME_SECONDS unset and the task and project bounds off, a
    dispatch 100s into the batch is armed with the remaining registry-default
    batch budget -- the value config_wake_max_runtime_seconds_default prints
    and the Key Registry documents -- not 0 (no bound). Blank reads the same
    way; an explicit 0 still disables the batch bound."""
    script = f"""
set -uo pipefail
unset MAX_RUNTIME_SECONDS
source {str(_TASK_BUDGET_SH)!r}
_wd_epoch_now() {{ echo 1100; }}
echo "LOADER_DEFAULT=$(config_wake_max_runtime_seconds_default)"
echo "UNSET_SEED=$(wd_configured_max_runtime_seconds)"
_eff_task=0; _eff_project=0; _project_start_epoch=1000; WAKE_START_EPOCH=1000
wd_chain_task_timeout_budget
echo "UNSET_ARMED=${{_task_timeout_budget_seconds}}"
MAX_RUNTIME_SECONDS=""
wd_chain_task_timeout_budget
echo "BLANK_ARMED=${{_task_timeout_budget_seconds}}"
MAX_RUNTIME_SECONDS=0
echo "EXPLICIT_ZERO_SEED=$(wd_configured_max_runtime_seconds)"
wd_chain_task_timeout_budget
echo "EXPLICIT_ZERO_ARMED=${{_task_timeout_budget_seconds}}"
MAX_RUNTIME_SECONDS=600
wd_chain_task_timeout_budget
echo "EXPLICIT_ARMED=${{_task_timeout_budget_seconds}}"
"""
    result = _run_bash(script, cwd=tmp_path)
    assert result.returncode == 0, _diagnostic(result)
    values = dict(re.findall(r"^(\w+)=(.*)$", result.stdout, re.M))
    registry_default = _registry_default("max_runtime_seconds")
    assert values["LOADER_DEFAULT"] == registry_default, _diagnostic(result)
    assert values["UNSET_SEED"] == registry_default, _diagnostic(result)
    assert values["UNSET_ARMED"] == str(int(registry_default) - 100), _diagnostic(result)
    assert values["BLANK_ARMED"] == str(int(registry_default) - 100), _diagnostic(result)
    assert values["EXPLICIT_ZERO_SEED"] == "0", _diagnostic(result)
    assert values["EXPLICIT_ZERO_ARMED"] == "0", _diagnostic(result)
    assert values["EXPLICIT_ARMED"] == "500", _diagnostic(result)


def test_sourcing_the_budget_stage_keeps_a_loaded_config_path(tmp_path: pathlib.Path) -> None:
    """wake_task_budget.sh sources config_loader.sh only when the caller has
    not: a second source would reset the cfg path load_config recorded."""
    script = f"""
set -uo pipefail
source {str(_LIB_DIR / "config_loader.sh")!r}
_CONFIG_LOADER_CFG_PATH=/sentinel/kanban.cfg
source {str(_TASK_BUDGET_SH)!r}
echo "CFG_PATH=${{_CONFIG_LOADER_CFG_PATH}}"
"""
    result = _run_bash(script, cwd=tmp_path)
    assert result.returncode == 0, _diagnostic(result)
    assert "CFG_PATH=/sentinel/kanban.cfg" in result.stdout, _diagnostic(result)


@pytest.mark.parametrize(
    "key, env_name",
    [("max_task_seconds", "MAX_TASK_SECONDS"), ("max_runtime_seconds", "MAX_RUNTIME_SECONDS")],
)
def test_runtime_budget_default_is_written_once(key: str, env_name: str) -> None:
    """Each runtime-budget default lives in the config loader alone: load_config
    exports it through its config_wake_<key>_default accessor (named as the
    default accessor, run only when the default is needed), and no wake
    stage that reads the variable falls back to 0 (no bound) or restates the
    default in code. Comments that state the default may stay."""
    loader = (_LIB_DIR / "config_loader.sh").read_text(encoding="utf-8")
    assert re.search(
        rf"_config_loader_export_wake_integer\s+\"\$cfg\"\s+{key}\s+{env_name}\s+"
        rf"\"\"\s+config_wake_{key}_default\b",
        loader,
    ), f"load_config must export {env_name} with config_wake_{key}_default as its default"
    registry_default = _registry_default(key)
    for path in (_DRIVER_SH, _TASK_BUDGET_SH, _DRIVER_INVOKE, _DRIVER_FINALIZE, _BOOTSTRAP_SH, _LOCK_PROVENANCE_SH):
        text = path.read_text(encoding="utf-8")
        assert not re.search(rf"{env_name}:-0|_eff_task:-0", text), f"{path.name} falls back to 0 (no bound)"
        code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
        assert not re.search(rf"\b{registry_default}\b", code), f"{path.name} restates the {key} default in code"


# ---------------------------------------------------------------------------
# (1c) Every batch-budget check reads the one rule: 0 = no batch bound,
#      unset or blank = the registry default, a positive value stops there.
# ---------------------------------------------------------------------------

_COMMON_SH = _LIB_DIR / "wake_common.sh"
_LOCK_WAIT_SH = _LIB_DIR / "wake_lock_wait.sh"

# MAX_RUNTIME_SECONDS as the wake sees it: None = unset, "" = blank.
_BUDGET_SETTINGS = [None, "", "0", "600"]


def _budget_assignment(budget: str | None) -> str:
    return "unset MAX_RUNTIME_SECONDS" if budget is None else f"MAX_RUNTIME_SECONDS={budget!r}"


def _expected_cap(budget: str | None) -> int:
    """The batch cap the rule gives *budget*; 0 means no batch bound."""
    return int(_registry_default("max_runtime_seconds")) if not budget else int(budget)


@pytest.mark.parametrize("budget", _BUDGET_SETTINGS)
@pytest.mark.parametrize("elapsed", [0, 599, 600, 20000])
def test_batch_budget_left_and_reached_follow_the_configured_budget(
    tmp_path: pathlib.Path, budget: str | None, elapsed: int
) -> None:
    script = f"""
set -uo pipefail
source {str(_TASK_BUDGET_SH)!r}
{_budget_assignment(budget)}
echo "LEFT=[$(wd_batch_budget_left {elapsed})]"
if wd_batch_budget_reached {elapsed}; then echo "REACHED=yes"; else echo "REACHED=no"; fi
"""
    result = _run_bash(script, cwd=tmp_path)
    assert result.returncode == 0, _diagnostic(result)
    values = dict(re.findall(r"^(\w+)=(.*)$", result.stdout, re.M))
    cap = _expected_cap(budget)
    if cap == 0:
        assert values["LEFT"] == "[]", f"0 is no batch bound\n{_diagnostic(result)}"
        assert values["REACHED"] == "no", f"0 is never reached\n{_diagnostic(result)}"
    else:
        assert values["LEFT"] == f"[{max(cap - elapsed, 0)}]", _diagnostic(result)
        assert values["REACHED"] == ("yes" if elapsed >= cap else "no"), _diagnostic(result)


def _dispatch_loop_batch_check() -> str:
    """run_project_chain's between-tasks batch check, verbatim: from the
    ELAPSED reading through the end of the if that stops the loop."""
    text = _DRIVER_SH.read_text(encoding="utf-8")
    chain = text[text.index("run_project_chain() {") :]
    begin = chain.index("    ELAPSED=$(( $(date +%s) - WAKE_START_EPOCH ))")
    end = chain.index("\n    fi\n", begin) + len("\n    fi\n")
    return chain[begin:end]


@pytest.mark.parametrize("budget", _BUDGET_SETTINGS)
def test_dispatch_loop_batch_stop_follows_the_configured_budget(tmp_path: pathlib.Path, budget: str | None) -> None:
    """The shipped dispatch-loop check, driven on a fake clock that advances
    500s per dispatched task for up to 40 tasks (20000s): 0 dispatches all
    40 with no batch stop; unset or blank stops at the registry default;
    600 stops after two tasks (1000s), exactly as before."""
    script = f"""
set -uo pipefail
source {str(_TASK_BUDGET_SH)!r}
{_budget_assignment(budget)}
FAKE_NOW=1000; WAKE_START_EPOCH=1000; STOP_REASON=""; DISPATCHED=0
date() {{ echo "$FAKE_NOW"; }}
while (( DISPATCHED < 40 )); do
{_dispatch_loop_batch_check()}
  DISPATCHED=$(( DISPATCHED + 1 )); FAKE_NOW=$(( FAKE_NOW + 500 ))
done
echo "DISPATCHED=$DISPATCHED"
echo "STOP_REASON=$STOP_REASON"
"""
    result = _run_bash(script, cwd=tmp_path)
    assert result.returncode == 0, _diagnostic(result)
    values = dict(re.findall(r"^(\w+)=(.*)$", result.stdout, re.M))
    cap = _expected_cap(budget)
    if cap == 0:
        assert values["DISPATCHED"] == "40", _diagnostic(result)
        assert values["STOP_REASON"] == "", _diagnostic(result)
    else:
        tasks = -(-cap // 500)  # the first boundary at or past the cap
        assert values["DISPATCHED"] == str(tasks), _diagnostic(result)
        assert values["STOP_REASON"] == (
            f"reached MAX_RUNTIME_SECONDS (batch) ({cap}s, batch_elapsed={tasks * 500}s)"
        ), _diagnostic(result)


def _retry_pass_block() -> str:
    """wake_common_run's end-of-iteration contention retry pass, verbatim."""
    text = _COMMON_SH.read_text(encoding="utf-8")
    begin = text.index("  if (( ${#_CONTENDED_PROJECTS[@]} > 0 )); then")
    end = text.index("  # --- Lock-starvation guard", begin)
    return text[begin:end]


@pytest.mark.parametrize("budget", _BUDGET_SETTINGS)
@pytest.mark.parametrize("pass_elapsed", [100, 20000])
def test_retry_pass_follows_the_configured_budget(
    tmp_path: pathlib.Path, budget: str | None, pass_elapsed: int
) -> None:
    """The shipped retry pass for two contended projects, with the first
    pass ending *pass_elapsed* seconds into the batch and each retry taking
    550s on a fake clock. 0 retries both projects however late; unset or
    blank holds the registry default; 600 retries one project at 100s (the
    second starts past the cap) and none at 20000s."""
    script = f"""
set -uo pipefail
source {str(_TASK_BUDGET_SH)!r}
{_budget_assignment(budget)}
FAKE_NOW=$(( 1000 + {pass_elapsed} )); WAKE_START_EPOCH=1000
date() {{ echo "$FAKE_NOW"; }}
log() {{ echo "LOG: $*"; }}
wake_stop_file_present() {{ return 1; }}
pp_project_halted() {{ return 1; }}
pp_project_root() {{ echo "/nonexistent/$1"; }}
pp_tasks_dir() {{ echo "/nonexistent/$1/tasks"; }}
run_project_chain() {{ echo "RETRIED=$1"; FAKE_NOW=$(( FAKE_NOW + 550 )); return 0; }}
_check_halt_after_for_project() {{ return 1; }}
AGENT=coder
_CONTENDED_PROJECTS=(alpha beta)
_LOCK_DEFERRED_PROJECTS=()
{_retry_pass_block()}
echo "DEFERRED=${{_LOCK_DEFERRED_PROJECTS[*]:-}}"
"""
    result = _run_bash(script, cwd=tmp_path)
    assert result.returncode == 0, _diagnostic(result)
    retried = re.findall(r"^RETRIED=(\S+)$", result.stdout, re.M)
    deferred = re.search(r"^DEFERRED=(.*)$", result.stdout, re.M).group(1).split()
    cap = _expected_cap(budget)
    if cap == 0 or cap > pass_elapsed + 550:
        expected_retried = ["alpha", "beta"]
    elif cap > pass_elapsed:
        expected_retried = ["alpha"]
    else:
        expected_retried = []
    assert retried == expected_retried, _diagnostic(result)
    assert deferred == [p for p in ("alpha", "beta") if p not in expected_retried], _diagnostic(result)
    if not expected_retried:
        assert f"cap={cap}s) -- performing no retry" in result.stdout, _diagnostic(result)
    elif len(expected_retried) == 1:
        assert f"cap={cap}s) -- skipping remaining retries" in result.stdout, _diagnostic(result)
    else:
        assert "batch runtime budget" not in result.stdout, _diagnostic(result)


@pytest.mark.parametrize("budget", _BUDGET_SETTINGS)
@pytest.mark.parametrize("elapsed", [100, 590, 20000])
def test_lock_wait_budget_follows_the_configured_budget(
    tmp_path: pathlib.Path, budget: str | None, elapsed: int
) -> None:
    """wlw_lock_wait_budget caps lock_wait_seconds (120) at what is left of
    the batch: 0 leaves it uncapped and the batch reads "unbounded"; unset
    or blank caps at the registry default; 600 caps at what remains of it.
    The stage is sourced alone, so it loads the budget rule itself."""
    script = f"""
set -uo pipefail
source {str(_LOCK_WAIT_SH)!r}
{_budget_assignment(budget)}
LOCK_WAIT_SECONDS=120; WAKE_START_EPOCH=1000
pgai_lock_wait_now() {{ echo $(( 1000 + {elapsed} )); }}
wlw_lock_wait_budget demo-project
echo "WAIT_BUDGET=$WLW_WAIT_BUDGET"
echo "BATCH_LEFT=$(_wlw_batch_left_text)"
"""
    result = _run_bash(script, cwd=tmp_path)
    assert result.returncode == 0, _diagnostic(result)
    values = dict(re.findall(r"^(\w+)=(.*)$", result.stdout, re.M))
    cap = _expected_cap(budget)
    if cap == 0:
        assert values["WAIT_BUDGET"] == "120", _diagnostic(result)
        assert values["BATCH_LEFT"] == "unbounded", _diagnostic(result)
    else:
        left = max(cap - elapsed, 0)
        assert values["WAIT_BUDGET"] == str(min(120, left)), _diagnostic(result)
        assert values["BATCH_LEFT"] == f"{left}s", _diagnostic(result)


@pytest.mark.parametrize("path", [_DRIVER_SH, _COMMON_SH, _LOCK_WAIT_SH, _DRIVER_FINALIZE, _TASK_BUDGET_SH])
def test_no_batch_check_compares_the_raw_budget(path: pathlib.Path) -> None:
    """No wake stage that checks the batch budget compares elapsed time with
    MAX_RUNTIME_SECONDS or subtracts from it directly; each goes through
    wake_task_budget.sh."""
    code = "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#")
    )
    assert not re.search(
        r"\(\(\s*[A-Z_]*ELAPSED\s*>=\s*\$?MAX_RUNTIME_SECONDS", code
    ), f"{path.name} compares elapsed time with the raw batch budget"
    assert not re.search(
        r"\$\(\(\s*\$?MAX_RUNTIME_SECONDS\s*-", code
    ), f"{path.name} computes the remaining batch budget from the raw value"


# ---------------------------------------------------------------------------
# (2) wd_warn_runtime_bound_nesting: >= fires, and names the invariant.
# ---------------------------------------------------------------------------


def _warnings_for(tmp_path: pathlib.Path, max_task: str, project: str, batch: str) -> str:
    script = f"""
set -uo pipefail
source {str(_TASK_BUDGET_SH)!r}
log() {{ echo "$*"; }}
wd_warn_runtime_bound_nesting demo-project {max_task!r} {project!r} {batch!r}
echo "RC=$?"
"""
    result = _run_bash(script, cwd=tmp_path)
    assert result.returncode == 0, _diagnostic(result)
    assert "RC=0" in result.stdout, _diagnostic(result)
    return result.stdout


def test_equal_task_and_project_and_batch_warns_twice_with_invariant(tmp_path: pathlib.Path) -> None:
    out = _warnings_for(tmp_path, "21600", "21600", "21600")
    lines = [ln for ln in out.splitlines() if ln.startswith("WARNING:")]
    assert len(lines) == 2, out
    assert any("MAX_TASK_SECONDS (21600s) >= MAX_PROJECT_SECONDS (21600s)" in ln for ln in lines), out
    assert any("MAX_TASK_SECONDS (21600s) >= MAX_RUNTIME_SECONDS (21600s)" in ln for ln in lines), out
    for ln in lines:
        assert _INVARIANT_TEXT in ln, f"warning must state the invariant:\n{ln}"
        assert "demo-project" in ln, ln


def test_task_equal_to_batch_only_warns_once(tmp_path: pathlib.Path) -> None:
    out = _warnings_for(tmp_path, "120", "0", "120")
    lines = [ln for ln in out.splitlines() if ln.startswith("WARNING:")]
    assert len(lines) == 1, out
    assert "MAX_TASK_SECONDS (120s) >= MAX_RUNTIME_SECONDS (120s)" in lines[0], out
    assert _INVARIANT_TEXT in lines[0], out


def test_project_above_batch_warns_with_invariant(tmp_path: pathlib.Path) -> None:
    out = _warnings_for(tmp_path, "100", "500", "300")
    lines = [ln for ln in out.splitlines() if ln.startswith("WARNING:")]
    assert len(lines) == 1, out
    assert "MAX_PROJECT_SECONDS (500s) > MAX_RUNTIME_SECONDS (300s)" in lines[0], out
    assert _INVARIANT_TEXT in lines[0], out


@pytest.mark.parametrize(
    "max_task, project, batch",
    [
        ("5400", "14400", "14400"),  # shipped defaults: project == batch is allowed
        ("100", "120", "300"),
        ("0", "120", "120"),  # per-task bound disabled: nothing to compare
        ("0", "0", "14400"),
    ],
)
def test_properly_nested_ceilings_do_not_warn(tmp_path: pathlib.Path, max_task: str, project: str, batch: str) -> None:
    out = _warnings_for(tmp_path, max_task, project, batch)
    assert "WARNING" not in out, out


# ---------------------------------------------------------------------------
# (3) The deadline watchdog under a mocked clock.
# ---------------------------------------------------------------------------


def _watchdog_harness(
    tmp_path: pathlib.Path,
    *,
    max_task_seconds: int | None,
    kill_grace_seconds: int,
    seconds_per_sleep: int,
    refresh_interval: int | None = None,
    budget_override: str | None = None,
    provider_body: str = "command sleep 60 </dev/null >/dev/null 2>&1",
) -> tuple[str, pathlib.Path, pathlib.Path, pathlib.Path, pathlib.Path]:
    """Build a harness that runs the REAL watchdog block against a mocked
    clock. `sleep` never blocks; each call advances the clock by
    *seconds_per_sleep*, which is how a starved host is simulated -- one
    poll iteration costing many seconds of wall-clock time. A
    *max_task_seconds* of None leaves MAX_TASK_SECONDS unset."""
    log_file = tmp_path / "wake.log"
    log_file.write_text("", encoding="utf-8")
    temp_root = tmp_path / "kanban_tmp"
    temp_root.mkdir(exist_ok=True)
    clock_file = tmp_path / "clock"
    clock_file.write_text("1000", encoding="utf-8")
    sleep_calls_file = tmp_path / "sleep_calls"
    sleep_calls_file.write_text("0", encoding="utf-8")
    refresh_calls_file = tmp_path / "refresh_calls"
    refresh_calls_file.write_text("0", encoding="utf-8")
    pgid_file = tmp_path / "pgid"
    refresh_line = (
        f"_PGAI_LOCK_HELD_REFRESH_INTERVAL_SECONDS={refresh_interval}" if refresh_interval is not None else ""
    )
    budget_line = f"_task_timeout_budget_seconds={budget_override!r}" if budget_override is not None else ""
    max_task_line = f"MAX_TASK_SECONDS={max_task_seconds}" if max_task_seconds is not None else "unset MAX_TASK_SECONDS"
    harness = f"""
set -uo pipefail
export PGAI_AGENT_KANBAN_TEMP_DIR={str(temp_root)!r}
source {str(_TEMP_SH)!r}
source {str(_TASK_BUDGET_SH)!r}
log() {{ echo "[$(date -Iseconds)] wake(${{AGENT}}): $*" | tee -a "$LOG_FILE"; }}

LOG_FILE={str(log_file)!r}
AGENT="coder"
task_id="TASK-DEADLINE-CLOCK"
{max_task_line}
KILL_GRACE_SECONDS={kill_grace_seconds}
{refresh_line}
{budget_line}

_CLOCK_FILE={str(clock_file)!r}
_SLEEP_CALLS_FILE={str(sleep_calls_file)!r}
_REFRESH_CALLS_FILE={str(refresh_calls_file)!r}
# Mocked clock: the watchdog's single clock read.
_wd_epoch_now() {{ cat "$_CLOCK_FILE"; }}
# Intercepted poll: never blocks, advances the clock by {seconds_per_sleep}s per call.
sleep() {{
  echo "$(( $(cat "$_SLEEP_CALLS_FILE") + 1 ))" > "$_SLEEP_CALLS_FILE"
  echo "$(( $(cat "$_CLOCK_FILE") + {seconds_per_sleep} ))" > "$_CLOCK_FILE"
}}
_wd_refresh_project_lock_mtime() {{
  echo "$(( $(cat "$_REFRESH_CALLS_FILE") + 1 ))" > "$_REFRESH_CALLS_FILE"
}}
# Stub provider: records the wrapper's pgid so the test can kill the group
# on any exit path, then runs on the REAL clock (command sleep bypasses the
# stub above) so only the watchdog's SIGTERM ends it.
_wd_run_provider_with_telemetry_safeguard_retry() {{
  echo "$BASHPID" > {str(pgid_file)!r}
  {provider_body}
  PROVIDER_AGENT_EXIT_CODE=0
}}

_run_watchdog() {{
{_extract_watchdog_block()}
}}
_run_watchdog
echo "FINAL_EXIT=${{PROVIDER_AGENT_EXIT_CODE}}"
echo "TASK_KILLED=${{_task_killed}}"
echo "ARMED_LIMIT=${{_provider_timeout_seconds}}"
echo "SLEEP_CALLS=$(cat "$_SLEEP_CALLS_FILE")"
echo "REFRESH_CALLS=$(cat "$_REFRESH_CALLS_FILE")"
echo "CLOCK_AT_END=$(cat "$_CLOCK_FILE")"
"""
    return harness, log_file, pgid_file, sleep_calls_file, refresh_calls_file


def _kill_recorded_group(pgid_file: pathlib.Path) -> None:
    if not pgid_file.is_file():
        return
    text = pgid_file.read_text(encoding="utf-8").strip()
    if text.isdigit():
        try:
            os.killpg(int(text), signal.SIGKILL)
        except ProcessLookupError:
            pass


def _run_harness(
    harness: str, cwd: pathlib.Path, pgid_file: pathlib.Path, timeout: float = 40
) -> subprocess.CompletedProcess:
    try:
        return _run_bash(harness, cwd=cwd, timeout=timeout)
    finally:
        _kill_recorded_group(pgid_file)


def _values(result: subprocess.CompletedProcess) -> dict[str, str]:
    return dict(re.findall(r"^(\w+)=(.*)$", result.stdout, re.M))


def test_deadline_fires_on_wall_clock_not_iteration_count(tmp_path: pathlib.Path) -> None:
    """A starved host where every poll costs 12s: a 30s limit fires on the
    first poll past the deadline (3 polls, clock at +36s), where an
    iteration-counting loop would have needed 30 polls -- 360s of real
    time. The fire line names the real elapsed seconds and the limit."""
    harness, log_file, pgid_file, _, _ = _watchdog_harness(
        tmp_path, max_task_seconds=30, kill_grace_seconds=10, seconds_per_sleep=12
    )
    result = _run_harness(harness, tmp_path, pgid_file)
    assert result.returncode == 0, _diagnostic(result)
    values = _values(result)
    assert values["TASK_KILLED"] == "true", _diagnostic(result)
    assert values["FINAL_EXIT"] == "124", _diagnostic(result)
    # 3 polls to cross the 30s deadline at +36s; at most 1 more inside the
    # 10s grace window (the cooperative stub dies on SIGTERM).
    sleep_calls = int(values["SLEEP_CALLS"])
    assert 3 <= sleep_calls <= 4, f"expected 3-4 polls, got {sleep_calls}\n{_diagnostic(result)}"
    log_text = log_file.read_text(encoding="utf-8")
    assert "timeout fired after 36s (limit 30s)" in log_text, log_text


def test_deadline_loop_exits_promptly_when_the_provider_finishes(tmp_path: pathlib.Path) -> None:
    harness, log_file, pgid_file, _, _ = _watchdog_harness(
        tmp_path,
        max_task_seconds=30,
        kill_grace_seconds=5,
        seconds_per_sleep=1,
        provider_body=":",
    )
    result = _run_harness(harness, tmp_path, pgid_file)
    assert result.returncode == 0, _diagnostic(result)
    values = _values(result)
    assert values["TASK_KILLED"] == "false", _diagnostic(result)
    assert values["FINAL_EXIT"] == "0", _diagnostic(result)
    assert int(values["SLEEP_CALLS"]) <= 2, _diagnostic(result)
    assert "timeout fired" not in log_file.read_text(encoding="utf-8")


def test_lock_refresh_cadence_is_wall_clock_inside_the_deadline_loop(tmp_path: pathlib.Path) -> None:
    """With a 2s refresh interval and a 6s limit, the loop refreshes at
    +2s and +4s (the +6s tick is the deadline) -- the cadence survives the
    move from iteration counting to a wall-clock deadline."""
    harness, _, pgid_file, _, _ = _watchdog_harness(
        tmp_path, max_task_seconds=6, kill_grace_seconds=1, seconds_per_sleep=1, refresh_interval=2
    )
    result = _run_harness(harness, tmp_path, pgid_file)
    assert result.returncode == 0, _diagnostic(result)
    values = _values(result)
    assert values["TASK_KILLED"] == "true", _diagnostic(result)
    assert values["REFRESH_CALLS"] == "2", _diagnostic(result)


def test_lock_refresh_skips_ticks_a_slow_host_swallowed_without_double_firing(tmp_path: pathlib.Path) -> None:
    """Each poll costs 5s against a 2s refresh interval: one refresh per
    poll, never a burst to 'catch up', and the interval re-anchors on the
    clock at each refresh."""
    harness, _, pgid_file, _, _ = _watchdog_harness(
        tmp_path, max_task_seconds=20, kill_grace_seconds=1, seconds_per_sleep=5, refresh_interval=2
    )
    result = _run_harness(harness, tmp_path, pgid_file)
    assert result.returncode == 0, _diagnostic(result)
    values = _values(result)
    # polls at +0 (no refresh: next=2), +5 (refresh), +10 (refresh), +15 (refresh), +20 deadline
    assert values["REFRESH_CALLS"] == "3", _diagnostic(result)
    assert 4 <= int(values["SLEEP_CALLS"]) <= 5, _diagnostic(result)


def test_watchdog_arms_the_chain_budget_over_max_task_seconds(tmp_path: pathlib.Path) -> None:
    """When run_project_chain left a smaller per-dispatch budget, the
    watchdog arms THAT limit, logs the cap, and records it for finalize."""
    harness, log_file, pgid_file, _, _ = _watchdog_harness(
        tmp_path, max_task_seconds=120, kill_grace_seconds=1, seconds_per_sleep=10, budget_override="60"
    )
    result = _run_harness(harness, tmp_path, pgid_file)
    assert result.returncode == 0, _diagnostic(result)
    values = _values(result)
    assert values["ARMED_LIMIT"] == "60", _diagnostic(result)
    assert values["TASK_KILLED"] == "true", _diagnostic(result)
    log_text = log_file.read_text(encoding="utf-8")
    assert "per-task timeout active: 60s" in log_text, log_text
    assert "capped from max_task_seconds=120s" in log_text, log_text
    assert "timeout fired after 60s (limit 60s)" in log_text, log_text


def test_watchdog_arms_the_registry_default_when_max_task_seconds_is_unset(tmp_path: pathlib.Path) -> None:
    """With MAX_TASK_SECONDS unset and no per-dispatch budget from a chain,
    the invoke stage arms the registry default for [wake] max_task_seconds,
    not 0 (watchdog off), and does not report it as a budget cap."""
    harness, log_file, pgid_file, _, _ = _watchdog_harness(
        tmp_path, max_task_seconds=None, kill_grace_seconds=1, seconds_per_sleep=1, provider_body=":"
    )
    result = _run_harness(harness, tmp_path, pgid_file)
    assert result.returncode == 0, _diagnostic(result)
    values = _values(result)
    registry_default = _registry_default("max_task_seconds")
    assert values["ARMED_LIMIT"] == registry_default, _diagnostic(result)
    assert values["TASK_KILLED"] == "false", _diagnostic(result)
    log_text = log_file.read_text(encoding="utf-8")
    assert f"per-task timeout active: {registry_default}s" in log_text, log_text
    assert "capped from max_task_seconds" not in log_text, log_text


def test_watchdog_disabled_when_every_ceiling_is_off(tmp_path: pathlib.Path) -> None:
    harness, log_file, pgid_file, _, _ = _watchdog_harness(
        tmp_path, max_task_seconds=0, kill_grace_seconds=1, seconds_per_sleep=1, provider_body=":"
    )
    result = _run_harness(harness, tmp_path, pgid_file)
    assert result.returncode == 0, _diagnostic(result)
    values = _values(result)
    assert values["ARMED_LIMIT"] == "0", _diagnostic(result)
    assert values["TASK_KILLED"] == "false", _diagnostic(result)
    assert "per-task timeout active" not in log_file.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# (4) The deadline on the real clock: fires within limit + 2s.
# ---------------------------------------------------------------------------


def test_real_clock_deadline_fires_within_two_seconds_of_the_limit(tmp_path: pathlib.Path) -> None:
    """The shipped clock and the shipped `sleep`: a 3s limit against a
    provider that never exits fires between 3s and 5s of real time, and
    the fire line's elapsed value agrees with the limit."""
    log_file = tmp_path / "wake.log"
    log_file.write_text("", encoding="utf-8")
    temp_root = tmp_path / "kanban_tmp"
    temp_root.mkdir()
    pgid_file = tmp_path / "pgid"
    harness = f"""
set -uo pipefail
export PGAI_AGENT_KANBAN_TEMP_DIR={str(temp_root)!r}
source {str(_TEMP_SH)!r}
source {str(_TASK_BUDGET_SH)!r}
log() {{ echo "[$(date -Iseconds)] wake(${{AGENT}}): $*" | tee -a "$LOG_FILE"; }}
LOG_FILE={str(log_file)!r}
AGENT="coder"
task_id="TASK-DEADLINE-REAL"
MAX_TASK_SECONDS=3
KILL_GRACE_SECONDS=2
_wd_refresh_project_lock_mtime() {{ :; }}
_wd_run_provider_with_telemetry_safeguard_retry() {{
  echo "$BASHPID" > {str(pgid_file)!r}
  sleep 60 </dev/null >/dev/null 2>&1
  PROVIDER_AGENT_EXIT_CODE=0
}}
START=$(date +%s)
_run_watchdog() {{
{_extract_watchdog_block()}
}}
_run_watchdog
echo "FIRED_AFTER=$(( _provider_deadline_epoch - START ))"
echo "TASK_KILLED=${{_task_killed}}"
"""
    result = _run_harness(harness, tmp_path, pgid_file, timeout=30)
    assert result.returncode == 0, _diagnostic(result)
    values = _values(result)
    assert values["TASK_KILLED"] == "true", _diagnostic(result)
    fired_after = int(values["FIRED_AFTER"])
    assert (
        3 <= fired_after <= 5
    ), f"deadline must fire within limit + 2s; fired after {fired_after}s\n{_diagnostic(result)}"
    log_text = log_file.read_text(encoding="utf-8")
    match = re.search(r"timeout fired after (\d+)s \(limit 3s\)", log_text)
    assert match, log_text
    assert 3 <= int(match.group(1)) <= 5, log_text


# ---------------------------------------------------------------------------
# (5) Structural guarantees on the shipped files.
# ---------------------------------------------------------------------------


def test_watchdog_block_reads_the_clock_and_never_counts_iterations() -> None:
    block = _extract_watchdog_block()
    assert "_wd_epoch_now" in block, "the watchdog must read the shared clock"
    assert "_deadline_epoch" in block, "the watchdog must compare against an absolute deadline"
    assert "(( _elapsed++ ))" not in block, "iteration counting must not come back"
    assert "_task_timeout_budget_seconds" in block, "the watchdog must arm the chain's per-dispatch budget"


def test_run_project_chain_derives_the_budget_before_every_dispatch() -> None:
    """Every process_one_task dispatch inside run_project_chain is preceded
    by wd_chain_task_timeout_budget, and the nesting warning is called."""
    text = _DRIVER_SH.read_text(encoding="utf-8")
    chain = text[text.index("run_project_chain() {") :]
    assert "wd_warn_runtime_bound_nesting" in chain
    dispatches = [m.start() for m in re.finditer(r"^\s*process_one_task\s*$", chain, re.M)]
    assert dispatches, "run_project_chain must dispatch through process_one_task"
    for pos in dispatches:
        preceding = chain[max(0, pos - 400) : pos]
        assert (
            "wd_chain_task_timeout_budget" in preceding
        ), f"dispatch without a fresh budget at offset {pos}:\n{preceding}"
    assert 'source "${SCRIPT_DIR}/../lib/wake_task_budget.sh"' in text


@pytest.mark.parametrize("script", [_TASK_BUDGET_SH, _DRIVER_INVOKE, _DRIVER_SH, _LIB_DIR / "config_loader.sh"])
def test_shipped_shell_parses(script: pathlib.Path) -> None:
    result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"bash -n {script} failed:\n{result.stderr}"
