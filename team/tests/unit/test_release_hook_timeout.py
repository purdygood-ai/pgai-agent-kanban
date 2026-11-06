"""
test_release_hook_timeout.py
============================
Behavioral tests for the time limit on release lifecycle hooks, kanban.cfg
``[release] hook_timeout_seconds``, on both hook runners:

  - ship-rc (pgai_agent_kanban.cm.ship): a hook that starts a background
    sleeping child and then sleeps past the limit has its whole process
    group stopped -- neither process is alive afterwards -- and the phase
    takes the path a failing hook of that phase takes: pre-squash is the
    recorded HALT, pre-tag stops the ship, post-tag is a warning. The
    timeout is narrated with the reason ``ship_rc.hook_timeout`` and the
    hook reports rc=124.
  - ship-rc's hook resolver runs under the same limit: a resolver that
    hangs is the library-unavailable stop, naming the limit.
  - cm/release.sh's ``_run_release_hook`` (team/scripts/lib/cm/
    release_hook_runner.sh), sourced into a bash harness the way
    test_cm_release_hook_failure_remedy.py does: the same
    hook is stopped after ``$CM_HOOK_TIMEOUT_SECONDS``, its group is gone,
    a blocking phase exits 1 and ``--no-block`` continues with a warning.
    A background child that ignores SIGTERM and holds the hook's output is
    stopped too, with SIGKILL, inside the limit plus the grace period. The
    hook sees the caller's PYTHONPATH, not the one its runner was started
    with.
  - The grace period after SIGTERM is honoured, through
    ``run_in_process_group`` and through the command-line entry
    ``_run_release_hook`` uses: a background child that handles SIGTERM
    with a one-second cleanup, and holds none of the hook's output, writes
    its marker file before the group is killed.
  - The limit is read from kanban.cfg: absent resolves to the default; a
    value that is not a positive whole number resolves to the default with
    one warning.

Every kanban root is a throwaway directory under pytest's tmp_path. The
limit is 1 second; the hooks sleep at most 30 seconds and die on SIGTERM,
except the one background child that ignores it and is SIGKILLed and the
one that cleans up for a second first.
"""

from __future__ import annotations

import pathlib
import subprocess
import time

import pytest

from pgai_agent_kanban.cm import ship
from pgai_agent_kanban.lib.bounded_run import KILL_GRACE_SECONDS, run_in_process_group
from pgai_agent_kanban.lib.error_contract import parse_error_contract
from pgai_agent_kanban.ops.context import OpsContext
from tests.unit.shell_harness import run_bash

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_LIB_HOOK_RUNNER = _TEAM_DIR / "scripts" / "lib" / "cm" / "release_hook_runner.sh"
_LIB_ERROR_CONTRACT = _TEAM_DIR / "scripts" / "lib" / "error_contract.sh"
_LIB_PP_RUN_OPS = _TEAM_DIR / "scripts" / "lib" / "pp_run_ops.sh"

_PROJECT = "hook-timeout"
_VERSION = "v2.1.0"
_RC = f"rc/{_VERSION}"

# Bound on one timed-out hook: the 1s limit plus the SIGTERM grace.
_BOUND_SECONDS = 1 + KILL_GRACE_SECONDS

_SLEEPING_HOOK = """#!/usr/bin/env bash
echo "hook started"
sleep 30 &
echo "$!" > "{pid_dir}/child.pid"
echo "$$" > "{pid_dir}/leader.pid"
sleep 30
"""

# The background child ignores SIGTERM and keeps the hook's stdout open, so
# only a SIGKILL to the whole group lets the output pipe close.
_TERM_IGNORING_HOOK = """#!/usr/bin/env bash
echo "hook started"
( trap '' TERM; echo "$BASHPID" > "{pid_dir}/child.pid"; exec sleep 30 ) &
echo "$$" > "{pid_dir}/leader.pid"
sleep 30
"""

# The background child handles SIGTERM with a one-second cleanup that writes
# a marker file. Its output goes to /dev/null, so the hook's own output
# closes as soon as the leader exits and only the grace period keeps the
# child alive long enough to finish.
_TERM_CLEANUP_HOOK = """#!/usr/bin/env bash
echo "hook started"
( trap 'sleep 1; echo "cleaned up" > "{pid_dir}/cleanup.done"; exit 0' TERM; sleep 30 & wait ) >/dev/null 2>&1 &
echo "$!" > "{pid_dir}/child.pid"
echo "$$" > "{pid_dir}/leader.pid"
sleep 30
"""


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _seed(tmp_path: pathlib.Path, cfg: str = "[release]\nhook_timeout_seconds = 1\n") -> OpsContext:
    """A kanban root with one project, a repository directory and a kanban.cfg."""
    root = tmp_path / "root"
    proj = root / "projects" / _PROJECT
    proj.mkdir(parents=True)
    (root / "projects.cfg").write_text(f"[project:{_PROJECT}]\npriority=1\n", encoding="utf-8")
    (proj / "project.cfg").write_text(
        f"[project]\nproject_name = {_PROJECT}\ndev_tree_path = {tmp_path / 'repo'}\n", encoding="utf-8"
    )
    (root / "kanban.cfg").write_text(cfg, encoding="utf-8")
    (tmp_path / "repo").mkdir()
    (tmp_path / "pids").mkdir()
    return OpsContext(kanban_root=root)


def _sleeping_hook(tmp_path: pathlib.Path, template: str = _SLEEPING_HOOK) -> pathlib.Path:
    hook = tmp_path / "hooks" / "sleeper.sh"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(template.format(pid_dir=tmp_path / "pids"), encoding="utf-8")
    hook.chmod(0o755)
    return hook


def _release(ctx: OpsContext, tmp_path: pathlib.Path) -> ship._Release:
    return ship._Release(ctx, _PROJECT, _VERSION, str(tmp_path / "repo"), _RC, ctx.project_root(_PROJECT))


def _alive(pid: int) -> bool:
    """True while *pid* is a live (non-zombie) process."""
    try:
        stat = pathlib.Path(f"/proc/{pid}/stat").read_text()
    except (FileNotFoundError, ProcessLookupError):
        return False
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


def _assert_group_gone(tmp_path: pathlib.Path) -> None:
    """The hook and the background child it started are both gone."""
    pids = {name: int((tmp_path / "pids" / f"{name}.pid").read_text()) for name in ("leader", "child")}
    deadline = time.monotonic() + 2.0
    while any(_alive(pid) for pid in pids.values()) and time.monotonic() < deadline:
        time.sleep(0.05)
    survivors = {name: pid for name, pid in pids.items() if _alive(pid)}
    assert not survivors, f"processes from the hook's group still alive: {survivors}"


def _run_phase(ctx: OpsContext, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, phase: str):
    """Run *phase* with the sleeping hook resolved; return (raised, out, err, elapsed)."""
    hook = _sleeping_hook(tmp_path)
    monkeypatch.setattr(ship, "_resolve_hook", lambda release, p, say_raw, warn_raw: (str(hook), True))
    out: list[str] = []
    err: list[str] = []
    raised = None
    started = time.monotonic()
    try:
        ship._hook_phase(_release(ctx, tmp_path), phase, out.append, err.append)
    except ship.ShipRcError as exc:
        raised = exc
    return raised, "".join(out), "".join(err), time.monotonic() - started


# ---------------------------------------------------------------------------
# ship-rc: a timed-out hook takes its phase's failed-hook path
# ---------------------------------------------------------------------------


def _assert_timeout_narrated(out: str, err: str, elapsed: float) -> None:
    assert elapsed < _BOUND_SECONDS, f"the hook held the release for {elapsed:.1f}s:\n{out}{err}"
    assert "[hook cm-release-" in out and "hook started" in out, "output read before the stop must be kept:\n" + out
    assert "ship_rc.hook_timeout" in err and "did not finish within 1s" in err, err
    assert "hook_timeout_seconds" in err, err


def test_timed_out_pre_squash_hook_is_the_recorded_halt(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _seed(tmp_path)

    raised, out, err, elapsed = _run_phase(ctx, tmp_path, monkeypatch, "pre-squash")

    _assert_timeout_narrated(out, err, elapsed)
    _assert_group_gone(tmp_path)
    assert isinstance(raised, ship.ShipRcError)
    assert raised.error == "ship-rc.pre-squash-hook-failed" and raised.exit_code == 1
    assert parse_error_contract(raised.text.split("\n\nHALT:")[0])["error"] == "ship-rc.pre-squash-hook-failed"
    assert "hook cm-release-pre-squash failed (rc=124), blocking release" in err, err
    ledger = (ctx.project_root(_PROJECT) / "refusal-ledger.md").read_text(encoding="utf-8")
    assert "- Error: ship-rc.pre-squash-hook-failed" in ledger


def test_timed_out_pre_tag_hook_stops_the_ship(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    raised, out, err, elapsed = _run_phase(_seed(tmp_path), tmp_path, monkeypatch, "pre-tag")

    _assert_timeout_narrated(out, err, elapsed)
    _assert_group_gone(tmp_path)
    assert isinstance(raised, ship.ShipRcError)
    assert (raised.error, raised.exit_code) == ("ship_rc.hook_failed", 1)
    assert "hook cm-release-pre-tag failed (rc=124), blocking release" in err, err


def test_timed_out_post_tag_hook_is_a_warning(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    raised, out, err, elapsed = _run_phase(_seed(tmp_path), tmp_path, monkeypatch, "post-tag")

    _assert_timeout_narrated(out, err, elapsed)
    _assert_group_gone(tmp_path)
    assert raised is None
    assert "hook cm-release-post-tag failed (rc=124), continuing per --no-block" in err, err
    assert "completed successfully" not in out


def test_hung_hook_resolver_is_the_library_unavailable_stop(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _seed(tmp_path)
    lib_dir = tmp_path / "hung-lib"
    lib_dir.mkdir()
    (lib_dir / "project_paths.sh").write_text("sleep 30\n", encoding="utf-8")
    monkeypatch.setattr(ship, "_HOOK_LIB_DIR", lib_dir)

    started = time.monotonic()
    with pytest.raises(ship.ShipRcError) as caught:
        ship._resolve_hook(_release(ctx, tmp_path), "pre-tag", lambda _t: None, lambda _t: None)
    elapsed = time.monotonic() - started

    assert elapsed < _BOUND_SECONDS, f"the resolver held the release for {elapsed:.1f}s"
    assert caught.value.error == "ship_rc.hook_library_unavailable"
    message = parse_error_contract(caught.value.text)["message"]
    assert "within 1s" in message and "hook_timeout_seconds" in message, message


# ---------------------------------------------------------------------------
# cm/release.sh: _run_release_hook stops the group after the limit
# ---------------------------------------------------------------------------


def _run_release_hook(tmp_path: pathlib.Path, hook: pathlib.Path, *, no_block: bool, setup: str = "") -> object:
    """Source the real _run_release_hook() and run *hook* under a 1-second limit.

    *setup* is bash run just before the call (an ``export``, for example).
    """
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    flag = " '--no-block'" if no_block else ""
    script = f"""\
set -uo pipefail
REPO_ROOT={str(repo_root)!r}
ACTIVE_RC='v9.9.9'
PROJECT_NAME='timeout-proj'
_CM_PROJECT_ROOT={str(tmp_path / "kanban_project")!r}
RC_BRANCH='rc/v9.9.9'
KANBAN_ROOT={str(tmp_path / "kanban")!r}
CM_HOOK_TIMEOUT_SECONDS=1
source {str(_LIB_ERROR_CONTRACT)!r}
source {str(_LIB_PP_RUN_OPS)!r}
source {str(_LIB_HOOK_RUNNER)!r}
{setup}
_run_release_hook 'cm-release-pre-tag' {str(hook)!r}{flag}
"""
    kanban = tmp_path / "kanban"
    return run_bash(
        tmp_path,
        script,
        extra_env={
            "KANBAN_ROOT": str(kanban),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(kanban),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "tmp"),
            "PGAI_PROJECT_ROOT": "",
            "PGAI_TASKS_DIR": "",
        },
        timeout=60,
    )


def _diagnostic(result) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def test_release_sh_timed_out_blocking_hook_fails_the_phase(tmp_path: pathlib.Path) -> None:
    (tmp_path / "pids").mkdir()
    hook = _sleeping_hook(tmp_path)

    started = time.monotonic()
    result = _run_release_hook(tmp_path, hook, no_block=False)
    elapsed = time.monotonic() - started

    assert result.returncode == 1, _diagnostic(result)
    assert elapsed < _BOUND_SECONDS + 5, f"the hook held the release for {elapsed:.1f}s\n" + _diagnostic(result)
    assert "[hook cm-release-pre-tag] hook started" in result.stdout, _diagnostic(result)
    assert "did not finish within 1s ([release] hook_timeout_seconds)" in result.stderr, _diagnostic(result)
    assert "hook exited non-zero (rc=124)" in result.stderr, _diagnostic(result)
    _assert_group_gone(tmp_path)


def test_release_sh_timed_out_no_block_hook_is_a_warning(tmp_path: pathlib.Path) -> None:
    (tmp_path / "pids").mkdir()
    hook = _sleeping_hook(tmp_path)

    result = _run_release_hook(tmp_path, hook, no_block=True)

    assert result.returncode == 0, _diagnostic(result)
    assert "did not finish within 1s" in result.stderr, _diagnostic(result)
    assert "failed (rc=124), continuing per --no-block" in result.stderr, _diagnostic(result)
    _assert_group_gone(tmp_path)


def test_release_sh_kills_a_term_ignoring_child_holding_the_output(tmp_path: pathlib.Path) -> None:
    (tmp_path / "pids").mkdir()
    hook = _sleeping_hook(tmp_path, _TERM_IGNORING_HOOK)

    started = time.monotonic()
    result = _run_release_hook(tmp_path, hook, no_block=False)
    elapsed = time.monotonic() - started

    assert elapsed < _BOUND_SECONDS + 3, f"the hook held the release for {elapsed:.1f}s\n" + _diagnostic(result)
    assert result.returncode == 1, _diagnostic(result)
    assert "[hook cm-release-pre-tag] hook started" in result.stdout, _diagnostic(result)
    assert "did not finish within 1s ([release] hook_timeout_seconds)" in result.stderr, _diagnostic(result)
    assert "hook exited non-zero (rc=124)" in result.stderr, _diagnostic(result)
    _assert_group_gone(tmp_path)


@pytest.mark.parametrize(
    ("setup", "expected"),
    [("export PYTHONPATH=/caller/path", "/caller/path"), ("unset PYTHONPATH", "unset")],
)
def test_release_sh_hook_sees_the_callers_pythonpath(tmp_path: pathlib.Path, setup: str, expected: str) -> None:
    hook = tmp_path / "hooks" / "env.sh"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text('#!/usr/bin/env bash\necho "PYTHONPATH=${PYTHONPATH-unset}"\n', encoding="utf-8")
    hook.chmod(0o755)

    result = _run_release_hook(tmp_path, hook, no_block=False, setup=setup)

    assert result.returncode == 0, _diagnostic(result)
    assert f"[hook cm-release-pre-tag] PYTHONPATH={expected}\n" in result.stdout, _diagnostic(result)


def test_release_sh_unset_limit_takes_the_config_loader_default(tmp_path: pathlib.Path) -> None:
    """With $CM_HOOK_TIMEOUT_SECONDS unset, the limit comes from the config-loader accessor."""
    (tmp_path / "pids").mkdir()
    hook = _sleeping_hook(tmp_path)
    setup = "unset CM_HOOK_TIMEOUT_SECONDS\nconfig_release_hook_timeout_seconds_default() { echo 1; }"

    result = _run_release_hook(tmp_path, hook, no_block=False, setup=setup)

    assert result.returncode == 1, _diagnostic(result)
    assert "did not finish within 1s ([release] hook_timeout_seconds)" in result.stderr, _diagnostic(result)
    _assert_group_gone(tmp_path)


# ---------------------------------------------------------------------------
# The grace period: a SIGTERM cleanup finishes before the group is killed
# ---------------------------------------------------------------------------


def _assert_cleanup_finished(tmp_path: pathlib.Path) -> None:
    """The background child's SIGTERM cleanup wrote its marker before the call returned."""
    marker = tmp_path / "pids" / "cleanup.done"
    assert marker.is_file(), "the group was killed before its member finished the SIGTERM cleanup"
    assert marker.read_text(encoding="utf-8") == "cleaned up\n"


def test_run_in_process_group_lets_a_member_finish_its_sigterm_cleanup(tmp_path: pathlib.Path) -> None:
    (tmp_path / "pids").mkdir()
    hook = _sleeping_hook(tmp_path, _TERM_CLEANUP_HOOK)

    done = run_in_process_group(
        [str(hook)], timeout_seconds=1, capture_output=True, text=True, stdin=subprocess.DEVNULL
    )

    _assert_cleanup_finished(tmp_path)
    assert done.timed_out and done.returncode is None, done
    assert "hook started" in done.stdout, done
    _assert_group_gone(tmp_path)


def test_release_sh_lets_a_member_finish_its_sigterm_cleanup(tmp_path: pathlib.Path) -> None:
    (tmp_path / "pids").mkdir()
    hook = _sleeping_hook(tmp_path, _TERM_CLEANUP_HOOK)

    result = _run_release_hook(tmp_path, hook, no_block=False)

    _assert_cleanup_finished(tmp_path)
    assert result.returncode == 1, _diagnostic(result)
    assert "did not finish within 1s ([release] hook_timeout_seconds)" in result.stderr, _diagnostic(result)
    assert "hook exited non-zero (rc=124)" in result.stderr, _diagnostic(result)
    _assert_group_gone(tmp_path)


# ---------------------------------------------------------------------------
# The configured limit
# ---------------------------------------------------------------------------


def test_absent_key_resolves_to_the_default(tmp_path: pathlib.Path) -> None:
    ctx = _seed(tmp_path, cfg="[release]\nship_posture = always\n")
    warnings: list[str] = []

    assert ship.resolve_hook_timeout_seconds(ctx.kanban_root, warnings.append) == ship.DEFAULT_HOOK_TIMEOUT_SECONDS
    assert warnings == []


@pytest.mark.parametrize("raw", ["0", "-1", "15m", "never"])
def test_unusable_value_warns_and_resolves_to_the_default(tmp_path: pathlib.Path, raw: str) -> None:
    ctx = _seed(tmp_path, cfg=f"[release]\nhook_timeout_seconds = {raw}\n")
    warnings: list[str] = []

    assert ship.resolve_hook_timeout_seconds(ctx.kanban_root, warnings.append) == ship.DEFAULT_HOOK_TIMEOUT_SECONDS
    assert len(warnings) == 1 and "[release] hook_timeout_seconds" in warnings[0] and repr(raw) in warnings[0]


def test_hook_inside_the_limit_is_untouched(tmp_path: pathlib.Path) -> None:
    ctx = _seed(tmp_path, cfg="[release]\nhook_timeout_seconds = 30\n")
    hook = tmp_path / "hooks" / "quick.sh"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/usr/bin/env bash\necho quick\nexit 0\n", encoding="utf-8")
    hook.chmod(0o755)
    out: list[str] = []
    err: list[str] = []

    code = ship._run_hook(_release(ctx, tmp_path), "cm-release-pre-tag", str(hook), out.append, err.append)

    assert code == 0
    assert "".join(out) == "[ship-rc] running hook cm-release-pre-tag...\n[hook cm-release-pre-tag] quick\n"
    assert err == []
