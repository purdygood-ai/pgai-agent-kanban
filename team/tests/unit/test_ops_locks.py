"""
test_ops_locks.py — Unit tests for pgai_agent_kanban.ops.locks: the one
implementation behind team/scripts/release-locks.sh, GET /release-locks and
POST /operations/release-locks.

Coverage:

  1. Route/CLI parity (tests/fixtures/route_cli_parity.py): POST
     /operations/release-locks calls ops.locks.release_lock and GET
     /release-locks calls ops.locks.list_locks, neither starting a process;
     release-locks.sh reaches both functions through pp_run_ops; and each
     route's stdout equals the CLI's on twin boards.
  2. CLI behaviour against the committed golden
     (tests/fixtures/golden/release_locks_cli.json, captured from the script
     before its logic moved to Python): for every recorded scenario --
     listing (no locks, dead / live / pre-upgrade / torn / partial records,
     non-lock files, a configured wake budget), release (exact, prefix,
     ".lock" suffix, ambiguous, not found, already gone, idempotent re-run,
     --force on a dead holder), --help and usage errors -- stdout, stderr,
     exit code and every file under locks/ (bytes and mode) are unchanged.
  3. A live holder (this test process's own pid) is refused on every entry
     point -- the CLI, POST /operations/release-locks and the function --
     dual-channel naming holder, pid, op and task, exit 4, and its lock file
     is left byte-for-byte in place.
  4. --force against a live holder, in process with the signal and the clock
     replaced: the holder is signalled and never unlinked; the let-go,
     exited-after-signal, could-not-signal and still-holding outcomes.
  5. The helpers directly: field parsing, liveness, the held line and held
     verdict, the stale-age threshold derivation, name resolution.

Every board is a throwaway directory under tmp_path; every CLI run gets an
explicit, minimal environment (build_scrubbed_env). No test spawns a
long-running helper process: a live holder is this test process itself.
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import signal
import stat
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from pgai_agent_kanban.api import adapter  # noqa: E402
from pgai_agent_kanban.lib import operator_verb_manifest as ovm  # noqa: E402
from pgai_agent_kanban.lib.error_contract import parse_error_contract  # noqa: E402
from pgai_agent_kanban.ops import locks as ops_locks  # noqa: E402
from pgai_agent_kanban.ops.context import OpsContext  # noqa: E402
from pgai_agent_kanban.ops.errors import IoError  # noqa: E402
from tests.fixtures.golden_board import build_synthetic_board  # noqa: E402
from tests.fixtures.route_cli_parity import assert_route_cli_parity, build_api_client  # noqa: E402
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env  # noqa: E402

_SCRIPT = _TEAM_DIR / "scripts" / "release-locks.sh"
_GOLDEN_FILE = _TEAM_DIR / "tests" / "fixtures" / "golden" / "release_locks_cli.json"

# A pid no kernel hands out (above the 4194304 pid_max ceiling), so it never
# answers kill -0: the recorded holder is dead.
_DEAD = "999999999"

# Seed records. "{LIVE}" becomes this process's own pid (alive for the whole
# run); "{AGO}" becomes an acquired_at 1000 seconds before the run.
_REC_DEAD = f"holder=wake-coder\npid={_DEAD}\nop=wake\ntask=CODER-1\nacquired_at=2099-01-01T00:00:00Z\n"
_REC_DEAD_PRE = "holder=overwatch\npid=999999998\nop=sweep\ntask=none\n"
_REC_DEAD_AGO = f"holder=wake-tester\npid={_DEAD}\nop=wake\ntask=TESTER-3\nacquired_at={{AGO}}\n"
_REC_LIVE = "holder=wake-pm\npid={LIVE}\nop=wake\ntask=none\nacquired_at=2099-01-01T00:00:00Z\n"
_REC_LIVE_AGO = "holder=wake-cm\npid={LIVE}\nop=ship\ntask=CM-2\nacquired_at={AGO}\n"
_REC_PARTIAL = "pid=999999997\n"
_REC_NO_PID = "holder=x\nop=y\ntask=z\n"
_REC_SPACED = f"holder=h\npid={_DEAD}\nop=o\ntask=\nacquired_at= 2099-01-01 00:00:00.5+05:30 \n"
_REC_TORN = f"holder=h\npid={_DEAD}\nop=o\ntask=t\nacquired_at=2026-13-45T99:00:00Z\n"
_REC_CR = f"holder=h\r\npid={_DEAD}\r\nop=o\r\ntask=t\r\n"
_REC_GARBAGE = "not a provenance record\n"
_REC_DUP = f"holder=first\nholder=second\npid={_DEAD}\nop=o\ntask=t\nacquired_at=2099-01-01T00:00:00Z\n"

_DIR = None  # a seed value that makes a directory

# A [wake] budget small enough that the stale floor (1+1+1+120 = 123s) falls
# below the 3-period figure (360s), which then wins.
_SMALL_BUDGET = {"MAX_RUNTIME_SECONDS": "1", "MAX_TASK_SECONDS": "1", "KILL_GRACE_SECONDS": "1"}
_SMALL_BUDGET_CFG = "[wake]\nmax_runtime_seconds = 1\nmax_task_seconds = 1  # inline\nkill_grace_seconds = 1\n"

# name -> (seed files, args, extra environment)
_SCENARIOS: dict[str, tuple[dict, list[str], dict]] = {
    "list-no-dir": ({}, [], {}),
    "list-empty-dir": ({"locks": _DIR}, [], {}),
    "list-only-non-lock-files": ({"locks/readme.txt": "x\n", "locks/a.lock.tmp.12": "x\n"}, [], {}),
    "list-dead-holder": ({"locks/repo-wake-alpha.lock": _REC_DEAD}, [], {}),
    "list-mixed": (
        {
            "locks/repo-wake-alpha.lock": _REC_DEAD,
            "locks/overwatch-alpha.lock": _REC_DEAD_PRE,
            "locks/repo-wake-beta.lock": _REC_LIVE,
            "locks/repo-wake-gamma.lock": _REC_DEAD_AGO,
            "locks/partial.lock": _REC_PARTIAL,
            "locks/no-pid.lock": _REC_NO_PID,
            "locks/spaced.lock": _REC_SPACED,
            "locks/torn.lock": _REC_TORN,
            "locks/crlf.lock": _REC_CR,
            "locks/garbage.lock": _REC_GARBAGE,
            "locks/empty.lock": "",
            "locks/dup.lock": _REC_DUP,
            "locks/.hidden.lock": _REC_DEAD,
            "locks/dir.lock": _DIR,
            "locks/notes.txt": "ignored\n",
        },
        [],
        {},
    ),
    "list-long-held-env-budget": ({"locks/repo-wake-delta.lock": _REC_LIVE_AGO}, [], _SMALL_BUDGET),
    "list-long-held-cfg-budget": (
        {"locks/repo-wake-delta.lock": _REC_LIVE_AGO, "kanban.cfg": _SMALL_BUDGET_CFG}, [], {},
    ),
    "list-default-budget": ({"locks/repo-wake-delta.lock": _REC_LIVE_AGO}, [], {}),
    "list-positional-ignored": ({"locks/repo-wake-alpha.lock": _REC_DEAD}, ["stray"], {}),
    "list-force-alone": ({"locks/repo-wake-alpha.lock": _REC_DEAD}, ["--force"], {}),
    "release-dead-exact": ({"locks/repo-wake-alpha.lock": _REC_DEAD}, ["--release", "repo-wake-alpha"], {}),
    "release-dead-equals-form": ({"locks/repo-wake-alpha.lock": _REC_DEAD}, ["--release=repo-wake-alpha"], {}),
    "release-dead-pre-upgrade": ({"locks/overwatch-alpha.lock": _REC_DEAD_PRE}, ["--release", "overwatch-alpha"], {}),
    "release-dead-prefix": (
        {"locks/repo-wake-alpha.lock": _REC_DEAD, "locks/overwatch-alpha.lock": _REC_DEAD_PRE},
        ["--release", "repo"],
        {},
    ),
    "release-dead-suffix": ({"locks/repo-wake-alpha.lock": _REC_DEAD}, ["--release", "repo-wake-alpha.lock"], {}),
    "release-dead-force": ({"locks/repo-wake-alpha.lock": _REC_DEAD}, ["--release", "repo-wake-alpha", "--force"], {}),
    "release-exact-beats-prefix": (
        {"locks/a.lock": _REC_DEAD, "locks/a-b.lock": _REC_DEAD},
        ["--release", "a"],
        {},
    ),
    "release-ambiguous": (
        {"locks/repo-wake-alpha.lock": _REC_DEAD, "locks/repo-wake-beta.lock": _REC_DEAD},
        ["--release", "repo-wake"],
        {},
    ),
    "release-prefix-boundary": ({"locks/repo-wake-alphabet.lock": _REC_DEAD}, ["--release", "repo-wake-alpha"], {}),
    "release-not-found": ({"locks/repo-wake-alpha.lock": _REC_DEAD}, ["--release", "nope"], {}),
    "release-not-found-no-locks": ({}, ["--release", "nope"], {}),
    "release-already-gone": ({"locks/repo-wake-alpha.lock": ""}, ["--release", "repo-wake-alpha"], {}),
    "release-no-pid-record": ({"locks/no-pid.lock": _REC_NO_PID}, ["--release", "no-pid"], {}),
    "release-garbage-record": ({"locks/garbage.lock": _REC_GARBAGE}, ["--release", "garbage"], {}),
    "release-empty-value": ({"locks/repo-wake-alpha.lock": _REC_DEAD}, ["--release="], {}),
    "release-missing-value": ({"locks/repo-wake-alpha.lock": _REC_DEAD}, ["--release"], {}),
    "help": ({}, ["--help"], {}),
    "help-short": ({}, ["-h"], {}),
    "unknown-flag": ({}, ["--bogus"], {}),
}

# Scenarios run twice in a row on the same board; the second run is recorded.
_RERUN = {"release-dead-exact"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ago_stamp(seconds: int = 1000) -> str:
    stamp = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=seconds)
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def _fill(text: str, tokens: dict) -> str:
    for token, value in tokens.items():
        text = text.replace("{" + token + "}", value)
    return text


def _seed(root: pathlib.Path, files: dict, tokens: dict) -> None:
    (root / "tmp").mkdir(parents=True)
    for rel, content in files.items():
        path = root / rel
        if content is _DIR:
            path.mkdir(parents=True, exist_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(_fill(content, tokens).encode("utf-8"))


def _env(tmp_path: pathlib.Path, root: pathlib.Path, extra: dict | None = None) -> dict:
    env_extra = {
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
        "KANBAN_ROOT": str(root),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
    }
    env_extra.update(extra or {})
    return build_scrubbed_env(home_dir=tmp_path / "home", extra_env=env_extra)


def _run(tmp_path, root, args, extra=None, script=_SCRIPT) -> subprocess.CompletedProcess:
    """Run the script; stdout and stderr are decoded with no newline translation (a CR survives)."""
    run = subprocess.run(
        ["bash", str(script), *args],
        capture_output=True, env=_env(tmp_path, root, extra),
        cwd=str(root), stdin=subprocess.DEVNULL, timeout=120,
    )
    run.stdout = run.stdout.decode("utf-8", errors="replace")
    run.stderr = run.stderr.decode("utf-8", errors="replace")
    return run


def _norm(text: str, root: pathlib.Path, tokens: dict) -> str:
    text = text.replace(str(root), "<ROOT>")
    for token, value in tokens.items():
        text = text.replace(value, f"<{token}>")
    return text


def _locks_tree(root: pathlib.Path, tokens: dict) -> dict:
    """Every entry under locks/: a file's normalised text and mode, a directory by name."""
    locks = root / "locks"
    out: dict = {}
    if not locks.is_dir():
        return out
    for path in sorted(locks.rglob("*")):
        rel = str(path.relative_to(root))
        if path.is_dir():
            out[rel + "/"] = None
        else:
            out[rel] = {
                "text": _norm(path.read_bytes().decode("utf-8", errors="replace"), root, tokens),
                "mode": oct(stat.S_IMODE(path.stat().st_mode)),
            }
    return out


def run_scenario(tmp_path: pathlib.Path, name: str, script: pathlib.Path = _SCRIPT) -> dict:
    """Seed a fresh board for *name*, run the script, return the normalised outcome."""
    files, args, extra = _SCENARIOS[name]
    tokens = {"LIVE": str(os.getpid()), "AGO": _ago_stamp()}
    root = tmp_path / f"root-{name}"
    _seed(root, files, tokens)
    run = _run(tmp_path, root, args, extra, script=script)
    if name in _RERUN:
        run = _run(tmp_path, root, args, extra, script=script)
    return {
        "rc": run.returncode,
        "stdout": _norm(run.stdout, root, tokens),
        "stderr": _norm(run.stderr, root, tokens),
        "locks": _locks_tree(root, tokens),
    }


def _golden() -> dict:
    return json.loads(_GOLDEN_FILE.read_text(encoding="utf-8"))


def _board(tmp_path: pathlib.Path, name: str, locks: dict) -> pathlib.Path:
    """A synthetic board under tmp_path with the given lock files."""
    board = tmp_path / name
    build_synthetic_board(board)
    (board / "tmp").mkdir(exist_ok=True)
    for lock_name, record in locks.items():
        path = board / "locks" / f"{lock_name}.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(record, encoding="utf-8")
    return board


def _ctx(root: pathlib.Path) -> OpsContext:
    return OpsContext(kanban_root=root)


def _live_record(task: str = "CODER-9") -> str:
    return f"holder=wake-coder\npid={os.getpid()}\nop=wake\ntask={task}\nacquired_at=2099-01-01T00:00:00Z\n"


# ---------------------------------------------------------------------------
# 1. Route/CLI parity
# ---------------------------------------------------------------------------

# The listing mode's GET route is a second route of the same censused verb
# (operator_verbs.manifest carries one REST twin per verb), so its parity row
# is built here from the manifest row with the listing function and route.
_LIST_ROW = SimpleNamespace(
    name="release-locks",
    impl="module:pgai_agent_kanban.ops.locks.list_locks",
    rest_method="GET",
    rest_route="/release-locks",
    script="scripts/release-locks.sh",
)


def test_manifest_row_names_release_lock():
    verb = ovm.get_verb("release-locks")
    assert verb.impl == "module:pgai_agent_kanban.ops.locks.release_lock"
    assert (verb.rest_method, verb.rest_route) == ("POST", "/operations/release-locks")


def test_release_route_and_cli_reach_ops_locks_release_lock(tmp_path):
    rest_board = _board(tmp_path, "rest-board", {"repo-wake-alpha": _REC_DEAD})
    cli_board = _board(tmp_path, "cli-board", {"repo-wake-alpha": _REC_DEAD})

    result = assert_route_cli_parity(
        "release-locks",
        build_api_client(rest_board),
        json={"release": "repo-wake-alpha"},
        cli_args=["--release", "repo-wake-alpha"],
        env=_env(tmp_path, cli_board),
        scratch_dir=tmp_path / "scratch",
    )

    assert result.target.dotted == "pgai_agent_kanban.ops.locks.release_lock"
    assert result.response.status_code == 200, result.response.text
    assert result.cli is not None and result.cli.completed.returncode == 0, result.cli.completed.stderr
    assert result.response.json()["stdout"].replace(str(rest_board), "<B>") == (
        result.cli.completed.stdout.replace(str(cli_board), "<B>")
    )
    for board in (rest_board, cli_board):
        assert (board / "locks" / "repo-wake-alpha.lock").read_bytes() == b""


def test_list_route_and_cli_reach_ops_locks_list_locks(tmp_path):
    locks = {"repo-wake-alpha": _REC_DEAD, "overwatch-alpha": _REC_DEAD_PRE, "repo-wake-beta": _fill(_REC_LIVE, {"LIVE": str(os.getpid())})}
    rest_board = _board(tmp_path, "rest-board", locks)
    cli_board = _board(tmp_path, "cli-board", locks)

    result = assert_route_cli_parity(
        _LIST_ROW,
        build_api_client(rest_board),
        cli_args=[],
        env=_env(tmp_path, cli_board),
        scratch_dir=tmp_path / "scratch",
    )

    assert result.response.status_code == 200, result.response.text
    assert result.cli is not None and result.cli.completed.returncode == 0, result.cli.completed.stderr
    rest_out = result.response.json()["stdout"].replace(str(rest_board), "<B>")
    assert rest_out == result.cli.completed.stdout.replace(str(cli_board), "<B>")
    assert rest_out.count("lock: <B>/locks/") == 3
    assert "  verdict: held\n" in rest_out and "  verdict: stale\n" in rest_out
    for board in (rest_board, cli_board):
        assert (board / "locks" / "repo-wake-alpha.lock").read_text(encoding="utf-8") == _REC_DEAD, (
            "listing is read-only"
        )


def test_get_release_locks_still_refuses_release_and_force(tmp_path):
    board = _board(tmp_path, "board", {"repo-wake-alpha": _REC_DEAD})
    client = build_api_client(board)
    for params in ({"release": "repo-wake-alpha"}, {"force": "true"}):
        response = client.get("/release-locks", params=params)
        assert response.status_code == 422, response.text
        assert "POST /operations/release-locks" in response.text
    assert (board / "locks" / "repo-wake-alpha.lock").read_text(encoding="utf-8") == _REC_DEAD


# ---------------------------------------------------------------------------
# 2. CLI behaviour equals the recorded pre-port behaviour
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(_SCENARIOS))
def test_cli_matches_the_recorded_behaviour(tmp_path, name):
    recorded = _golden()["scenarios"][name]
    assert run_scenario(tmp_path, name) == recorded


def test_every_scenario_is_recorded():
    assert set(_golden()["scenarios"]) == set(_SCENARIOS)


# ---------------------------------------------------------------------------
# 3. A live holder is refused on every entry point and its lock stays
# ---------------------------------------------------------------------------


def _assert_names_the_holder(message: str, task: str) -> None:
    for field in ("holder=wake-coder", f"pid={os.getpid()}", "op=wake", f"task={task}"):
        assert field in message, f"the refusal must name {field}: {message}"


def test_live_holder_refused_on_the_cli(tmp_path):
    board = _board(tmp_path, "board", {"repo-wake-alpha": _live_record()})
    before = (board / "locks" / "repo-wake-alpha.lock").read_bytes()

    run = _run(tmp_path, board, ["--release", "repo-wake-alpha"])

    assert run.returncode == 4, run.stdout + run.stderr
    assert run.stdout == ""
    contract = parse_error_contract(run.stderr)
    assert contract is not None, run.stderr
    assert contract["error"] == "release_locks.holder_alive"
    _assert_names_the_holder(contract["message"], "CODER-9")
    assert "team/scripts/release-locks.sh --release repo-wake-alpha --force" in contract["remedy"]
    assert (board / "locks" / "repo-wake-alpha.lock").read_bytes() == before


def test_live_holder_refused_over_rest(tmp_path):
    board = _board(tmp_path, "board", {"repo-wake-alpha": _live_record("none")})
    before = (board / "locks" / "repo-wake-alpha.lock").read_bytes()

    response = build_api_client(board).post("/operations/release-locks", json={"release": "repo-wake"})

    assert response.status_code == 409, response.text
    body = response.json()
    assert body["exit_code"] == 4
    assert body["error"] == "release_locks.holder_alive"
    _assert_names_the_holder(body["message"], "none")
    assert body["remedy"].endswith("--release repo-wake-alpha --force.")
    assert (board / "locks" / "repo-wake-alpha.lock").read_bytes() == before


def test_live_holder_refused_by_the_function(tmp_path):
    board = _board(tmp_path, "board", {"repo-wake-alpha": _live_record()})
    before = (board / "locks" / "repo-wake-alpha.lock").read_bytes()
    lines: list[str] = []

    with pytest.raises(ops_locks.LockRefusal) as caught:
        ops_locks.release_lock(_ctx(board), "repo-wake-alpha.lock", say=lines.append)

    assert caught.value.exit_code == 4
    assert caught.value.error == "release_locks.holder_alive"
    assert ops_locks.locks_exit_code(caught.value) == 4
    _assert_names_the_holder(str(caught.value), "CODER-9")
    assert lines == []
    assert (board / "locks" / "repo-wake-alpha.lock").read_bytes() == before


# ---------------------------------------------------------------------------
# 4. --force against a live holder (signal and clock replaced; no real signal)
# ---------------------------------------------------------------------------

_FAKE_PID = 4242
_FAKE_RECORD = f"holder=wake-pm\npid={_FAKE_PID}\nop=wake\ntask=PM-1\nacquired_at=2099-01-01T00:00:00Z\n"


class _FakeHolder:
    """Stands in for os.kill against _FAKE_PID; never signals a real process."""

    def __init__(
        self, lock: pathlib.Path, *, on_term: str, term_error: type | None = None, release_after: int = 0
    ) -> None:
        self.lock = lock
        self.on_term = on_term  # "release" | "exit" | "ignore" | "release-later"
        self.term_error = term_error
        self.release_after = release_after  # "release-later": seconds slept before letting go
        self.termed = False
        self.alive = True
        self.signals: list[int] = []
        self.sleeps = 0

    def kill(self, pid: int, sig: int) -> None:
        assert pid == _FAKE_PID, f"only the fake holder may be signalled, got pid {pid}"
        self.signals.append(sig)
        if sig == 0:
            if not self.alive:
                raise ProcessLookupError(pid)
            return
        if self.term_error is not None:
            if self.term_error is ProcessLookupError:
                self.alive = False
            raise self.term_error(pid)
        self.termed = True
        if self.on_term == "release":
            self.lock.unlink()
            self.alive = False
        elif self.on_term == "exit":
            self.alive = False

    def sleep(self, _seconds: float) -> None:
        self.sleeps += 1
        if self.on_term == "release-later" and self.termed and self.alive and self.sleeps >= self.release_after:
            # A stopping wake: its provider group outlived the SIGTERM, was
            # stopped, and only then did the wake release its lock.
            self.lock.unlink()
            self.alive = False


@pytest.fixture()
def fake_board(tmp_path):
    board = _board(tmp_path, "board", {"repo-wake-pm": _FAKE_RECORD})
    return board, board / "locks" / "repo-wake-pm.lock"


def _install(monkeypatch, holder: _FakeHolder) -> None:
    monkeypatch.setattr(ops_locks, "_kill", holder.kill)
    monkeypatch.setattr(ops_locks, "_sleep", holder.sleep)


def test_force_holder_lets_go(monkeypatch, fake_board):
    board, lock = fake_board
    holder = _FakeHolder(lock, on_term="release")
    _install(monkeypatch, holder)
    lines: list[str] = []

    outcome = ops_locks.release_lock(_ctx(board), "repo-wake-pm", force=True, say=lines.append, grace_seconds=5)

    assert outcome.outcome == "holder_let_go" and outcome.waited_seconds == 0
    assert signal.SIGTERM in holder.signals
    assert lines[0].startswith(f"release-locks: sent SIGTERM to live holder pid {_FAKE_PID} of lock repo-wake-pm")
    assert lines[-1] == f"release-locks: released lock repo-wake-pm ({lock}): holder pid {_FAKE_PID} let go of it 0s after SIGTERM"


def test_force_holder_exits_leaving_its_record(monkeypatch, fake_board):
    board, lock = fake_board
    holder = _FakeHolder(lock, on_term="exit")
    _install(monkeypatch, holder)
    lines: list[str] = []

    outcome = ops_locks.release_lock(_ctx(board), "repo-wake-pm", force=True, say=lines.append, grace_seconds=5)

    assert outcome.outcome == "holder_exited"
    assert "exited 0s after SIGTERM" in lines[-1]
    assert lock.read_text(encoding="utf-8") == _FAKE_RECORD, "the record is reaped by the next acquisition, not here"


def test_force_holder_that_cannot_be_signalled_is_refused(monkeypatch, fake_board):
    board, lock = fake_board
    holder = _FakeHolder(lock, on_term="ignore", term_error=PermissionError)
    _install(monkeypatch, holder)

    with pytest.raises(ops_locks.LockRefusal) as caught:
        ops_locks.release_lock(_ctx(board), "repo-wake-pm", force=True, say=lambda _l: None, grace_seconds=5)

    assert caught.value.exit_code == 5 and caught.value.error == "release_locks.holder_not_released"
    for field in ("holder=wake-pm", f"pid={_FAKE_PID}", "op=wake", "task=PM-1"):
        assert field in str(caught.value)
    assert f"kill -TERM {_FAKE_PID}" in str(caught.value)
    assert holder.sleeps == 0
    assert lock.read_text(encoding="utf-8") == _FAKE_RECORD


def test_force_holder_gone_before_the_signal_is_cleared_as_stale(monkeypatch, fake_board):
    board, lock = fake_board
    holder = _FakeHolder(lock, on_term="ignore", term_error=ProcessLookupError)
    _install(monkeypatch, holder)
    lines: list[str] = []

    outcome = ops_locks.release_lock(_ctx(board), "repo-wake-pm", force=True, say=lines.append, grace_seconds=5)

    assert outcome.outcome == "released_stale"
    assert "exited before it could be signalled" in lines[0]
    assert lines[1] == f"release-locks: released stale lock repo-wake-pm ({lock})"
    assert lock.read_bytes() == b""


def test_force_holder_still_holding_after_the_grace_is_refused(monkeypatch, fake_board):
    board, lock = fake_board
    holder = _FakeHolder(lock, on_term="ignore")
    _install(monkeypatch, holder)

    with pytest.raises(ops_locks.LockRefusal) as caught:
        ops_locks.release_lock(_ctx(board), "repo-wake-pm", force=True, say=lambda _l: None, grace_seconds=2)

    assert caught.value.exit_code == 5
    assert "still held by holder=wake-pm" in str(caught.value) and "2s after SIGTERM" in str(caught.value)
    assert "stopping its provider's process group" in str(caught.value)
    assert "--release repo-wake-pm --force" in str(caught.value), "the first remedy is to wait and re-run"
    assert f"kill -KILL {_FAKE_PID}" in str(caught.value)
    assert holder.sleeps == 2
    assert lock.read_text(encoding="utf-8") == _FAKE_RECORD


def test_force_waits_twice_kill_grace_seconds_plus_the_margin(monkeypatch, fake_board):
    board, lock = fake_board
    holder = _FakeHolder(lock, on_term="ignore")
    _install(monkeypatch, holder)
    monkeypatch.setenv("KILL_GRACE_SECONDS", "3")

    with pytest.raises(ops_locks.LockRefusal):
        ops_locks.release_lock(_ctx(board), "repo-wake-pm", force=True, say=lambda _l: None)

    assert holder.sleeps == 2 * 3 + ops_locks.FORCE_WAIT_MARGIN_SECONDS


def test_force_outlasts_a_wake_stopping_a_provider_that_ignores_sigterm(monkeypatch, fake_board):
    """The provider group ignores SIGTERM for one grace period; the wake
    SIGKILLs it, waits for the group, and only then releases its lock --
    about two grace periods after the SIGTERM. --force waits it out."""
    board, lock = fake_board
    monkeypatch.setenv("KILL_GRACE_SECONDS", "2")
    holder = _FakeHolder(lock, on_term="release-later", release_after=2 * 2)
    _install(monkeypatch, holder)
    lines: list[str] = []

    outcome = ops_locks.release_lock(_ctx(board), "repo-wake-pm", force=True, say=lines.append)

    assert outcome.outcome == "holder_let_go", lines
    assert outcome.waited_seconds == 4
    assert "let go of it 4s after SIGTERM" in lines[-1]


def _swap_on_read(monkeypatch, lock: pathlib.Path, new_record: str, *, in_place: bool) -> None:
    """After the release reads holder A's record, A lets go and holder B takes the lock."""
    real_read = ops_locks.read_provenance

    def read_then_swap(path):
        record = real_read(path)
        if pathlib.Path(path) == lock:
            if in_place:
                lock.write_text(new_record, encoding="utf-8")
            else:
                staged = lock.with_name(lock.name + ".next")
                staged.write_text(new_record, encoding="utf-8")
                staged.replace(lock)
        return record

    monkeypatch.setattr(ops_locks, "read_provenance", read_then_swap)


@pytest.mark.parametrize("in_place", [False, True], ids=["new-inode", "rewritten-record"])
def test_force_signals_nobody_when_the_lock_changed_hands_after_it_was_read(monkeypatch, fake_board, in_place):
    board, lock = fake_board
    holder = _FakeHolder(lock, on_term="ignore")
    _install(monkeypatch, holder)
    next_pid = 5151
    next_record = f"holder=wake-coder\npid={next_pid}\nop=wake\ntask=CODER-2\nacquired_at=2099-01-01T00:00:00Z\n"
    _swap_on_read(monkeypatch, lock, next_record, in_place=in_place)
    lines: list[str] = []

    outcome = ops_locks.release_lock(_ctx(board), "repo-wake-pm", force=True, say=lines.append, grace_seconds=5)

    assert outcome.outcome == "holder_let_go" and outcome.pid == "", outcome
    assert signal.SIGTERM not in holder.signals, "nobody may be signalled"
    narration = "\n".join(lines)
    assert "let go" in narration
    assert str(_FAKE_PID) not in narration and str(next_pid) not in narration, narration
    assert lock.read_text(encoding="utf-8") == next_record, "the new holder's lock is left alone"


def test_force_over_rest_reports_the_narration(monkeypatch, fake_board):
    board, lock = fake_board
    holder = _FakeHolder(lock, on_term="release")
    _install(monkeypatch, holder)

    response = build_api_client(board).post(
        "/operations/release-locks", json={"release": "repo-wake-pm", "force": True}
    )

    assert response.status_code == 200, response.text
    stdout = response.json()["stdout"]
    assert stdout.startswith(f"release-locks: sent SIGTERM to live holder pid {_FAKE_PID}")
    assert stdout.endswith("let go of it 0s after SIGTERM\n")


def test_uncaptured_ops_call_takes_no_capture_lock():
    """A route that narrates through a callback must not queue behind another
    thread's captured ops call (a force release may wait for its holder)."""
    held, done = threading.Event(), threading.Event()

    def _hold() -> None:
        with adapter._OPS_CAPTURE_LOCK:
            held.set()
            done.wait(10)

    holder = threading.Thread(target=_hold)
    holder.start()
    held.wait(5)
    outcomes: list = []

    def _call() -> None:
        with adapter.ops_call(capture=False) as outcome:
            raise ops_locks.LockRefusal("release_locks.ambiguous", "m", "r", 2)
        outcomes.append(outcome)

    caller = threading.Thread(target=_call)
    caller.start()
    caller.join(5)
    finished = not caller.is_alive()
    done.set()
    holder.join(5)
    caller.join(5)
    assert finished, "ops_call(capture=False) waited on the capture lock"
    assert outcomes[0].error is not None and outcomes[0].stderr.startswith("error:   release_locks.ambiguous")


# ---------------------------------------------------------------------------
# 5. The helpers directly
# ---------------------------------------------------------------------------


def test_lock_field_reads_the_first_line_for_a_key():
    record = "holder=a\nholder=b\npid=12\nopx=no\nop=yes"
    assert ops_locks.lock_field(record, "holder") == "a"
    assert ops_locks.lock_field(record, "op") == "yes"
    assert ops_locks.lock_field(record, "task") == ""


def test_describe_provenance():
    assert ops_locks.describe_provenance("") == "(no provenance recorded)"
    assert ops_locks.describe_provenance("noise\n") == "(no provenance recorded)"
    assert ops_locks.describe_provenance("pid=7") == "holder=<unknown> pid=7 op=<unknown> task=<unknown>"


# The live PID is the {LIVE} token, filled in inside the test: each xdist worker
# is its own process, so a PID in a parametrize value would give every worker
# different test IDs and xdist refuses to run a suite that collects differently.
@pytest.mark.parametrize(
    "pid, verdict",
    [("{LIVE}", "held"), (_DEAD, "stale"), ("0", "stale"), ("-1", "stale"), ("abc", "stale"),
     ("{LIVE}\r", "stale"), ("99999999999999999999999", "stale")],
)
def test_pid_liveness_verdict(pid, verdict):
    pid = _fill(pid, {"LIVE": str(os.getpid())})
    assert ops_locks.pid_liveness_verdict(f"holder=h\npid={pid}\n") == verdict


def test_pid_liveness_verdict_without_a_pid_is_unparseable():
    assert ops_locks.pid_liveness_verdict("holder=h\nop=o\n") == "unparseable"
    assert ops_locks.pid_liveness_verdict("") == "unparseable"


# Values GNU date -u -d VALUE +%s gives (None where it refuses or is negative).
@pytest.mark.parametrize(
    "value, epoch",
    [
        ("2026-01-01T00:00:00Z", 1767225600),
        ("2026-01-01 00:00:00.999Z", 1767225600),
        (" 2026-01-01T00:00:00Z ", 1767225600),
        ("2026-01-01T00:00:00+05:30", 1767205800),
        ("2026-01-01T00:00:00+0530", 1767205800),
        ("2026-01-01T00:00:00+05:99", 1767201660),
        ("2026-01-01T00:00:00+24:00", 1767139200),
        ("2026-01-01T00:00:00-24:00", 1767312000),
        ("2026-01-01T00:00:00+24:01", None),
        ("2026-01-01T00:00:00+23:99", None),
        ("2026-02-30T00:00:00Z", None),
        ("2026-01-01T24:00:00Z", None),
        ("2026-01-01T23:59:60Z", None),
        ("1969-12-31T23:59:59Z", None),
        ("2026-01-01T00:00:00", None),
        ("  2026-01-01T00:00:00Z", None),
        ("", None),
    ],
)
def test_acquired_epoch_matches_date(value, epoch):
    assert ops_locks.acquired_epoch(f"acquired_at={value}") == epoch


def test_held_seconds_and_format():
    record = "acquired_at=2026-01-01T00:00:00Z"
    assert ops_locks.held_seconds(record, 1767225600 + 125) == 125
    assert ops_locks.held_seconds(record, 1767225600 - 5) == 0
    assert ops_locks.held_seconds("pid=1") is None
    assert [ops_locks.format_held_seconds(s) for s in (0, 59, 60, 3599, 3600, 7322)] == [
        "0s", "59s", "1m", "59m", "1h 0m", "2h 2m",
    ]


def test_describe_held():
    base = 1767225600
    record = f"holder=h\npid=9\nop=o\ntask=none\nacquired_at=2026-01-01T00:00:00Z"
    assert ops_locks.describe_held(record, base + 61) == (
        "held: 1m (since 2026-01-01T00:00:00Z) by h pid 9 — chain, no task yet"
    )
    assert ops_locks.describe_held("task=T-1", base) == (
        "held: acquired_at not recorded (pre-upgrade lock) by <unknown> pid <unknown> — T-1"
    )


def test_held_verdict_rounds_the_overage_up_to_minutes():
    base = 1767225600
    live = f"pid={os.getpid()}\nacquired_at=2026-01-01T00:00:00Z"
    assert ops_locks.held_verdict(live, 360, base + 360) == "live"
    assert ops_locks.held_verdict(live, 360, base + 361) == "long (1m past floor)"
    assert ops_locks.held_verdict(live, 360, base + 420) == "long (1m past floor)"
    assert ops_locks.held_verdict(live, 360, base + 421) == "long (2m past floor)"
    dead = f"pid={_DEAD}\nacquired_at=2026-01-01T00:00:00Z"
    assert ops_locks.held_verdict(dead, 360, base) == f"dead (pid {_DEAD} not running)"
    assert ops_locks.held_verdict(f"pid={os.getpid()}", 360, base) is None


def test_stale_threshold_defaults_and_overrides(monkeypatch, tmp_path):
    for name in ("MAX_RUNTIME_SECONDS", "MAX_TASK_SECONDS", "KILL_GRACE_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    ctx = _ctx(tmp_path)
    assert ops_locks.stale_age_threshold_seconds(ctx) == 14400 + 5400 + 30 + 120

    (tmp_path / "kanban.cfg").write_text(
        "[wake]\nmax_runtime_seconds = 100 ; note\nmax_task_seconds=x\n[other]\nkill_grace_seconds = 1\n",
        encoding="utf-8",
    )
    assert ops_locks.chain_budget_floor_seconds(ctx) == 100 + 5400 + 30 + 120

    monkeypatch.setenv("MAX_TASK_SECONDS", "10")
    monkeypatch.setenv("KILL_GRACE_SECONDS", "ten")
    assert ops_locks.chain_budget_floor_seconds(ctx) == 100 + 10 + 30 + 120
    assert ops_locks.stale_age_threshold_seconds(ctx) == 360, "the 3-period figure wins over a smaller floor"


def test_resolve_lock_name():
    names = ["a", "a-b", "repo-wake-alpha", "repo-wake-alphabet", "repo-wake-beta"]
    assert ops_locks.resolve_lock_name(names, "a") == ["a"]
    assert ops_locks.resolve_lock_name(names, "a.lock") == ["a"]
    assert ops_locks.resolve_lock_name(names, "repo-wake-alpha") == ["repo-wake-alpha"]
    assert ops_locks.resolve_lock_name(names, "repo-wake") == ["repo-wake-alpha", "repo-wake-alphabet", "repo-wake-beta"]
    assert ops_locks.resolve_lock_name(["repo-wake-alphabet"], "repo-wake-alpha") == []


def test_list_locks_without_a_locks_dir(tmp_path):
    listing = ops_locks.list_locks(_ctx(tmp_path))
    assert listing.locks == ()
    assert ops_locks.render_lock_listing(listing) == f"no lock files found under {tmp_path.resolve()}/locks\n"


def test_list_locks_entry_fields(tmp_path):
    board = _board(tmp_path, "board", {"repo-wake-alpha": _REC_DEAD})
    (entry,) = ops_locks.list_locks(_ctx(board), now=0).locks
    assert (entry.name, entry.holder, entry.pid, entry.op, entry.task) == (
        "repo-wake-alpha", "wake-coder", _DEAD, "wake", "CODER-1",
    )
    assert entry.verdict == "stale" and entry.held_verdict == f"dead (pid {_DEAD} not running)"


def test_release_without_a_name_is_refused(tmp_path):
    with pytest.raises(ops_locks.LockRefusal) as caught:
        ops_locks.release_lock(_ctx(tmp_path), "")
    assert caught.value.exit_code == 1 and caught.value.error == "release_locks.release_required"


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes into a read-only directory")
def test_a_swap_that_cannot_be_written_is_an_io_error(tmp_path):
    board = _board(tmp_path, "board", {"repo-wake-alpha": _REC_DEAD})
    locks = board / "locks"
    locks.chmod(0o555)
    try:
        with pytest.raises(IoError) as caught:
            ops_locks.release_lock(_ctx(board), "repo-wake-alpha", say=lambda _l: None)
    finally:
        locks.chmod(0o755)
    assert ops_locks.locks_exit_code(caught.value) == 1
    assert (locks / "repo-wake-alpha.lock").read_text(encoding="utf-8") == _REC_DEAD
    assert sorted(p.name for p in locks.iterdir()) == ["repo-wake-alpha.lock"]


def test_cli_main_refuses_an_unknown_verb(capsys):
    assert ops_locks._cli_main(["nope"]) == 1
    assert "VERB must be one of: list_locks, release_lock" in capsys.readouterr().err
