"""
test_action_parity_fixtures.py — the action half of CLI-vs-REST parity on
the golden-pair harness: for every action verb in the operator verb census,
a REST call produces the same on-disk effect as its CLI twin, or a
malformed parameter is refused with the same dual-channel payload.

The cases themselves are declared once, in
``team/tests/fixtures/action_parity_cases.py`` (the ledger), which the
parity gate (``team/scripts/lint_api_parity.py``) also reads to report any
census action row with no case. This module is the runner for that ledger:

  effect case   Two synthetic boards are built and seeded identically
                (``build_synthetic_board`` + the case's ``seed``). The REAL
                CLI script runs against one; the REAL production REST
                entrypoint -- a loopback uvicorn subprocess on an ephemeral
                port, its kanban root pointed at the other board -- serves
                the REST call. The set of files each surface created,
                changed or removed is captured (path -> content, ``None``
                for a removal) and compared with the golden comparator,
                which names the first differing path. The effect, not the
                response body, is what is compared: a route returning 200
                while writing nothing fails here.

  refusal case  One board; both surfaces are given the same malformed
                parameter. Where the script emits the dual-channel house
                shape, the ``error``/``message``/``remedy`` triple must be
                identical across surfaces -- read from the REST body's own
                top-level keys when the route lifts them, from the
                envelope's ``stderr`` through the production parser when it
                does not, or from the detached child's log file for the two
                routes that dispatch and return before the script speaks.
                Where the script refuses with plain text, exit code and
                stderr must be byte-identical, and a route that lifts plain
                text to ``message`` must carry exactly that text.

Sentinel hygiene: a case that arms HALT / HALT-AFTER / DEFER-LOW-BUGS arms
it inside its own synthetic board only. Every case removes those files from
both boards on the way out -- pass or fail -- and asserts none remain;
``run-integration-tests.sh``'s post-suite sentinel check on the live kanban
root is the second, independent line.

Integration test contract (team/tests/integration/conftest.py): no
integration point is mocked. Real uvicorn subprocess, real HTTP, real
``bash <script>`` subprocess; only the boards' DATA is synthetic.

Test-name policy (roles/TESTER.md Anti-pattern 6): names describe behavior,
never bug IDs, task IDs, or scaffolding labels.
"""

from __future__ import annotations

import contextlib
import json
import os
import pathlib
import re
import subprocess
import time
import urllib.error
import urllib.request
from typing import Generator, Iterator

import pytest

from pgai_agent_kanban.api.adapter import http_status_for
from pgai_agent_kanban.lib.error_contract import parse_error_contract
from pgai_agent_kanban.lib.operator_verb_manifest import load_manifest
from pgai_agent_kanban.ops.errors import NotFound, Refused
from tests.fixtures.action_parity_cases import (
    ACTION_PARITY_CASES,
    PARITY_UNTIL_VERSION,
    SENTINEL_BASENAMES,
    EffectParityCase,
    RefusalParityCase,
    case_id,
    verbs_with_a_case,
)
from tests.fixtures.api_server import ServerHandle, api_server_fixture
from tests.fixtures.golden_board import PROJECT_NAME, build_synthetic_board
from tests.fixtures.golden_comparator import assert_json_parity

_EFFECT_CASES = [c for c in ACTION_PARITY_CASES if isinstance(c, EffectParityCase)]
_REFUSAL_CASES = [c for c in ACTION_PARITY_CASES if isinstance(c, RefusalParityCase)]

# Wall-clock stamps scripts write into files or narration (reset.log, a
# generated project.cfg header). Two boards run seconds apart, so these are
# normalised before comparison; nothing else is.
_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:?\d{2}|Z)?")

# The sentinel writers' provenance log (pgai_agent_kanban.lib.sentinel_provenance)
# names the writing process -- its epoch second, command line, pid, parent pid
# and parent command line -- which legitimately differ between the CLI script
# and the API server. Those fields are normalised; op, path, sha256, user and
# test must still agree, so both twins are held to recording the same write.
_PROVENANCE_LOG_REL = "logs/sentinel-writes.log"
_PROVENANCE_PROCESS_FIELD_RE = re.compile(r"(^|\t)(epoch|entry|pid|ppid|parent)=[^\t\n]*", re.MULTILINE)

# How long a detached child (deep-verify / cold-build-now dispatch) is given
# to refuse and exit before the log-channel refusal case gives up.
_DETACHED_LOG_TIMEOUT_SECONDS = 30


# ---------------------------------------------------------------------------
# Board snapshot and effect capture
# ---------------------------------------------------------------------------


def _snapshot(root: pathlib.Path) -> dict[str, bytes]:
    """Every regular file under *root* (board-relative path -> bytes).

    Symlinks are skipped -- the board's ``scripts/`` link points outward at
    the real ``team/scripts/`` tree, which is not part of the board's state.
    ``rglob`` does not descend into a symlinked directory, so the link's
    target is never walked either.
    """
    files: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        files[str(path.relative_to(root))] = path.read_bytes()
    return files


def _normalise(content: bytes, root: pathlib.Path) -> str:
    """Decode *content* and replace the two legitimately board-specific
    values -- the board's own absolute root path and wall-clock timestamps
    -- with stable tokens, so two boards' effects compare on substance."""
    text = content.decode("utf-8", errors="replace")
    for root_form in {str(root), str(root.resolve())}:
        text = text.replace(root_form, "<board>")
    return _TIMESTAMP_RE.sub("<timestamp>", text)


def _effect(before: dict[str, bytes], after: dict[str, bytes], root: pathlib.Path) -> dict:
    """The change set between two snapshots: created and changed files map
    to their normalised content, removed files to ``None``."""
    changed: dict[str, object] = {}
    for rel in sorted(set(before) | set(after)):
        if rel not in after:
            changed[rel] = None
        elif rel not in before or before[rel] != after[rel]:
            text = _normalise(after[rel], root)
            if rel == _PROVENANCE_LOG_REL:
                text = _PROVENANCE_PROCESS_FIELD_RE.sub(r"\1\2=<process>", text)
            changed[rel] = text
    return changed


# ---------------------------------------------------------------------------
# CLI and REST invocation against a board
# ---------------------------------------------------------------------------


# The names a wake-invoked shell hands every subprocess pre-resolved to the
# operator's install. A board is only sandboxed when all of them are pinned
# to it or cleared (docs/test-authoring-guidelines.md "Live-install safety"):
# the two roots are set to the board; the two pre-resolved project paths and
# the project name are cleared, because they outrank anything a root derives
# and a script that needs them derives them from the pinned root instead.
_INHERITED_INSTALL_NAMES: tuple[str, ...] = (
    "PGAI_TASKS_DIR",
    "PGAI_PROJECT_ROOT",
    "PGAI_PROJECT_NAME",
)


def _cli_env(board: pathlib.Path, temp_dir: pathlib.Path) -> dict[str, str]:
    """Environment for a real CLI script run against *board*.

    ``PGAI_AGENT_KANBAN_ROOT_PATH`` is what every entry-point script
    resolves its root from (env_bootstrap.sh honours an exported value);
    ``KANBAN_ROOT`` is the alias project_paths.sh reads directly, so both
    name the board. Every pre-resolved install path the invoking shell may
    carry (``_INHERITED_INSTALL_NAMES``) is cleared so no script can reach a
    root other than the board, and the framework temp dir is the test's own.
    """
    env = dict(os.environ)
    for name in _INHERITED_INSTALL_NAMES:
        env.pop(name, None)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(board)
    env["KANBAN_ROOT"] = str(board)
    env["PGAI_AGENT_KANBAN_TEMP_DIR"] = str(temp_dir)
    return env


def _run_cli(board: pathlib.Path, script: str, args: list[str], temp_dir: pathlib.Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(board / "scripts" / script), *args],
        capture_output=True,
        text=True,
        env=_cli_env(board, temp_dir),
        timeout=60,
        stdin=subprocess.DEVNULL,
    )


def _post_json(url: str, body: dict) -> tuple[int, dict]:
    """POST *body* as JSON and return ``(status, decoded_body)`` for any
    status -- a refusal's error status is a result here, not an exception."""
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


@contextlib.contextmanager
def _rest_server(
    board: pathlib.Path, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[ServerHandle]:
    """The real production REST entrypoint, rooted at *board*.

    ``api_server_fixture`` inherits this process's environment and pins the
    two roots itself, clearing the pre-resolved install paths as it does; the
    same names are pinned here in this process as well, and the framework
    temp dir (where a detached dispatch writes its log) is pointed at the
    test's own scratch, so a script the server shells out to sees exactly
    the environment the CLI side is given.
    """
    monkeypatch.setenv("KANBAN_ROOT", str(board))
    monkeypatch.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(tmp_path / "server_tmp"))
    for name in _INHERITED_INSTALL_NAMES:
        monkeypatch.delenv(name, raising=False)
    server_logs = tmp_path / "server_logs"
    server_logs.mkdir(parents=True, exist_ok=True)
    with api_server_fixture(server_logs, kanban_root=board) as handle:
        yield handle


# ---------------------------------------------------------------------------
# Sentinel hygiene
# ---------------------------------------------------------------------------


def _sentinel_paths(board: pathlib.Path) -> list[pathlib.Path]:
    """Every HALT / HALT-AFTER / DEFER-LOW-BUGS present at the board root or
    under any of its projects."""
    found: list[pathlib.Path] = []
    roots = [board] + sorted(p for p in (board / "projects").glob("*") if p.is_dir()) \
        if (board / "projects").is_dir() else [board]
    for scope in roots:
        for name in SENTINEL_BASENAMES:
            candidate = scope / name
            if candidate.exists():
                found.append(candidate)
    return found


def _remove_sentinels(*boards: pathlib.Path) -> None:
    """Remove every sentinel a case may have armed, then prove none remain.
    Called from a ``finally`` so a failing assertion cannot leave one."""
    for board in boards:
        for path in _sentinel_paths(board):
            path.unlink()
        remaining = _sentinel_paths(board)
        assert remaining == [], f"sentinel files survived cleanup under {board}: {remaining}"


@pytest.fixture
def two_boards(tmp_path: pathlib.Path) -> Generator[tuple[pathlib.Path, pathlib.Path], None, None]:
    """Two identically shaped synthetic boards -- one per surface -- with
    guaranteed sentinel cleanup on both when the test ends."""
    cli_board = tmp_path / "cli_board"
    rest_board = tmp_path / "rest_board"
    build_synthetic_board(cli_board)
    build_synthetic_board(rest_board)
    try:
        yield cli_board, rest_board
    finally:
        _remove_sentinels(cli_board, rest_board)


@pytest.fixture
def one_board(tmp_path: pathlib.Path) -> Generator[pathlib.Path, None, None]:
    """One synthetic board both surfaces refuse against, with guaranteed
    sentinel cleanup when the test ends."""
    board = tmp_path / "board"
    build_synthetic_board(board)
    try:
        yield board
    finally:
        _remove_sentinels(board)


def _seed(case, board: pathlib.Path) -> None:
    if case.seed is not None:
        case.seed(board, board / "projects" / PROJECT_NAME)


# ---------------------------------------------------------------------------
# Effect parity
# ---------------------------------------------------------------------------


def _run_effect_case(
    case: EffectParityCase,
    cli_board: pathlib.Path,
    rest_board: pathlib.Path,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Seed both boards from *case*, run the CLI script against one and the
    REST route against the other, and assert the two effects agree (the
    body of the parametrised effect test, shared with the ambient-root
    guard below so both exercise the identical path)."""
    _seed(case, cli_board)
    _seed(case, rest_board)

    # --- CLI side ---
    before_cli = _snapshot(cli_board)
    cli = _run_cli(cli_board, case.script, case.cli_args(cli_board), tmp_path / "cli_tmp")
    assert cli.returncode == 0, (
        f"{case.script} exited {cli.returncode} on the CLI board\n"
        f"stdout: {cli.stdout}\nstderr: {cli.stderr}"
    )
    cli_effect = _effect(before_cli, _snapshot(cli_board), cli_board)

    # --- REST side ---
    before_rest = _snapshot(rest_board)
    with _rest_server(rest_board, tmp_path, monkeypatch) as handle:
        status, body = _post_json(f"http://127.0.0.1:{handle.port}{case.rest_route}", case.rest_body)
    assert status == 200, f"POST {case.rest_route} returned HTTP {status}: {body}"
    assert body["exit_code"] == 0, f"POST {case.rest_route} envelope reports a non-zero exit: {body}"
    rest_effect = _effect(before_rest, _snapshot(rest_board), rest_board)

    # --- Non-triviality: the effect the case names actually happened ---
    assert cli_effect, f"{case.script} changed nothing on the CLI board -- the comparison would be vacuous"
    for rel in case.expect_changed:
        assert rel in cli_effect, (
            f"expected {case.script} to create/change/remove {rel!r}; the CLI change set was "
            f"{sorted(cli_effect)}"
        )

    # --- The parity assertion: same files, same content, same removals ---
    assert_json_parity(cli_effect, rest_effect)


@pytest.mark.parametrize("case", _EFFECT_CASES, ids=case_id)
def test_rest_action_produces_the_same_on_disk_effect_as_its_cli_twin(
    case: EffectParityCase,
    two_boards: tuple[pathlib.Path, pathlib.Path],
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The REAL CLI script on one seeded board and the REAL REST route on
    an identically seeded second board create, change and remove the same
    files with the same content (modulo each board's own root path and
    wall-clock stamps) -- and the change set is non-empty and includes the
    file the case names, so a route that answers 200 and writes nothing, or
    both surfaces writing nothing, cannot pass."""
    cli_board, rest_board = two_boards
    _run_effect_case(case, cli_board, rest_board, tmp_path, monkeypatch)


# The project name a wake-invoked shell exports for this project's lane; the
# ambient-root guard below reproduces that shape, project directory and all.
_AMBIENT_PROJECT_NAME = "pgai-agent-kanban"


def test_global_halt_verbs_and_sentinel_cleanup_leave_an_ambient_root_halt_untouched(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With every root-naming variable pointed at a directory standing in
    for the operator's install -- the environment a wake-invoked runner hands
    this suite, pre-resolved project paths included -- the ``halt-global`` and
    ``unhalt-global`` effect cases arm and clear the fleet HALT on their own
    synthetic boards only, and the boards' sentinel cleanup never crosses out
    of them: a HALT armed on that ambient root is byte-identical afterward
    and no sentinel appears beside it or under its project. Only the
    operator removes a HALT; this proves the suite is not a writer."""
    ambient_root = tmp_path / "ambient_root"
    ambient_project = ambient_root / "projects" / _AMBIENT_PROJECT_NAME
    (ambient_project / "tasks").mkdir(parents=True)
    ambient_halt = ambient_root / "HALT"
    armed_bytes = b"armed by the operator\n"
    ambient_halt.write_bytes(armed_bytes)

    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(ambient_root))
    monkeypatch.setenv("KANBAN_ROOT", str(ambient_root))
    monkeypatch.setenv("PGAI_PROJECT_ROOT", str(ambient_project))
    monkeypatch.setenv("PGAI_TASKS_DIR", str(ambient_project / "tasks"))
    monkeypatch.setenv("PGAI_PROJECT_NAME", _AMBIENT_PROJECT_NAME)

    cases = {c.verb: c for c in _EFFECT_CASES if c.verb in ("halt-global", "unhalt-global")}
    assert set(cases) == {"halt-global", "unhalt-global"}, (
        f"the ledger no longer carries both fleet-HALT effect cases: {sorted(cases)}"
    )

    for verb in ("halt-global", "unhalt-global"):
        scratch = tmp_path / verb
        cli_board = scratch / "cli_board"
        rest_board = scratch / "rest_board"
        build_synthetic_board(cli_board)
        build_synthetic_board(rest_board)
        try:
            _run_effect_case(cases[verb], cli_board, rest_board, scratch, monkeypatch)
        finally:
            _remove_sentinels(cli_board, rest_board)

    assert ambient_halt.read_bytes() == armed_bytes, (
        "the ambient root's HALT was removed or rewritten by the fleet-HALT cases; "
        "a fixture resolved that root instead of its synthetic board"
    )
    assert _sentinel_paths(ambient_root) == [ambient_halt], (
        f"sentinels appeared on the ambient root beside the operator's HALT: "
        f"{_sentinel_paths(ambient_root)}"
    )


def test_halt_after_until_arms_byte_identical_sentinel_content_over_rest_and_cli(
    two_boards: tuple[pathlib.Path, pathlib.Path],
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The requirements document's named action case, stated at the byte
    level: ``halt-after.sh --until vX.Y.Z`` and ``POST /operations/halt-after``
    with the same ``until`` write the same bytes to ``<project>/HALT-AFTER``
    -- the versioned ``rc:vX.Y.Z`` token drain.py's own in-flight-RC
    capture produces -- on two boards that started identical."""
    cli_board, rest_board = two_boards
    cli_sentinel = cli_board / "projects" / PROJECT_NAME / "HALT-AFTER"
    rest_sentinel = rest_board / "projects" / PROJECT_NAME / "HALT-AFTER"
    assert not cli_sentinel.exists() and not rest_sentinel.exists()

    cli = _run_cli(
        cli_board, "halt-after.sh",
        ["--project", PROJECT_NAME, "--until", PARITY_UNTIL_VERSION],
        tmp_path / "cli_tmp",
    )
    assert cli.returncode == 0, cli.stderr

    with _rest_server(rest_board, tmp_path, monkeypatch) as handle:
        status, body = _post_json(
            f"http://127.0.0.1:{handle.port}/operations/halt-after",
            {"project": PROJECT_NAME, "key": "rc", "until": PARITY_UNTIL_VERSION},
        )
    assert status == 200 and body["exit_code"] == 0, body

    assert cli_sentinel.is_file(), "the CLI did not arm HALT-AFTER"
    assert rest_sentinel.is_file(), "the REST route answered 200 but armed no HALT-AFTER"
    cli_bytes = cli_sentinel.read_bytes()
    assert cli_bytes == rest_sentinel.read_bytes(), (
        f"HALT-AFTER content differs: CLI={cli_bytes!r} REST={rest_sentinel.read_bytes()!r}"
    )
    assert cli_bytes == f"rc:{PARITY_UNTIL_VERSION}\n".encode("utf-8")


# ---------------------------------------------------------------------------
# Refusal parity
# ---------------------------------------------------------------------------


def _refusal_statuses(exit_code: int, error_key: str) -> set[int]:
    """Every HTTP status the adapter's own mapping can give a refusal keyed
    *error_key*.

    A key that names its refusal kind (malformed, not found, conflict)
    settles the status by itself, so the set holds one value. A key that
    names none -- ``remove_project.active_rc``, a route's ``<verb>.refused``
    fallback -- leaves the status to the ops exception behind the result,
    which is not observable from outside the server; the set then holds what
    ``http_status_for`` answers with no exception, a ``NotFound`` and a
    ``Refused``.
    """
    return {
        http_status_for(exit_code, error_key=error_key, error=error)
        for error in (None, NotFound(error_key), Refused(error_key))
    }


def _detached_log_refusal(body: dict) -> dict:
    """For a route that dispatches detached: read the dual-channel triple
    from the log file the response names, once the child has written it.

    The response's ``stdout`` names the log path ("captured to PATH"). The
    child runs in its own session and is never reaped by the server while it
    lives, so its pid stays visible as a zombie after exit and is no
    liveness signal; the log is: the script's last act before exiting is to
    print the three-line block, so the triple parsing AND the file size
    holding still across two polls means the child is done. Fails naming the
    log content when no triple appeared within the window.
    """
    stdout = body["stdout"]
    log_match = re.search(r"captured to\s+(\S+?)\.?(?:\s|$)", stdout)
    assert log_match, f"detached dispatch narration did not name its log file: {stdout!r}"
    log_path = pathlib.Path(log_match.group(1))

    deadline = time.monotonic() + _DETACHED_LOG_TIMEOUT_SECONDS
    last_size = -1
    log_text = ""
    while time.monotonic() < deadline:
        if log_path.is_file():
            log_text = log_path.read_text(encoding="utf-8", errors="replace")
            size = len(log_text)
            if parse_error_contract(log_text) is not None and size == last_size:
                break
            last_size = size
        time.sleep(0.25)
    triple = parse_error_contract(log_text)
    assert triple is not None, (
        f"detached child's log at {log_path} carried no error/message/remedy block within "
        f"{_DETACHED_LOG_TIMEOUT_SECONDS}s:\n{log_text}"
    )
    return triple


@pytest.mark.parametrize("case", _REFUSAL_CASES, ids=case_id)
def test_rest_action_refuses_a_malformed_parameter_like_its_cli_twin(
    case: RefusalParityCase,
    one_board: pathlib.Path,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Given the same malformed parameter over the same board, the REAL CLI
    script and the REAL REST route refuse alike: identical dual-channel
    ``error``/``message``/``remedy`` where the script emits the house shape
    (the REST triple read from the body's own top-level keys, or from the
    detached child's log),
    and identical exit code and stderr bytes where it refuses with plain
    text. Neither surface may write anything while refusing."""
    board = one_board
    _seed(case, board)

    before = _snapshot(board)
    cli = _run_cli(board, case.script, case.cli_args(board), tmp_path / "cli_tmp")
    assert cli.returncode != 0, (
        f"{case.script} accepted the malformed input {case.cli_args(board)!r}:\n{cli.stdout}"
    )
    assert _effect(before, _snapshot(board), board) == {}, "the CLI refusal wrote to the board"

    with _rest_server(board, tmp_path, monkeypatch) as handle:
        status, body = _post_json(f"http://127.0.0.1:{handle.port}{case.rest_route}", case.rest_body)
        if case.channel == "detached-log":
            assert status == 200, f"detached dispatch should answer 200 on start; got {status}: {body}"
            rest_triple = _detached_log_refusal(body)
        else:
            refusal_key = case.error_key or body.get("error", "")
            expected = _refusal_statuses(cli.returncode, refusal_key)
            assert status in expected, (
                f"POST {case.rest_route} answered {status}; the adapter maps a refusal "
                f"keyed {refusal_key!r} to {sorted(expected)}: {body}"
            )
            rest_triple = None
    assert _effect(before, _snapshot(board), board) == {}, "the REST refusal wrote to the board"

    if case.error_key is None:
        # Plain-stderr refusal: the whole refusal text is the contract, and
        # the route still lifts it to the three top-level keys -- the text
        # verbatim as 'message' under the route's own fallback key, with a
        # remedy the route supplies because the script printed none.
        assert body["exit_code"] == cli.returncode, (case.note, body, cli.stderr)
        assert body["stderr"] == cli.stderr, f"REST stderr differs from the CLI's ({case.note})"
        assert body["stdout"] == cli.stdout
        assert {"error", "message", "remedy"} <= set(body), (
            f"POST {case.rest_route} refused without top-level error/message/remedy: {body}"
        )
        assert body["message"] == cli.stderr.strip(), (
            "a route that lifts plain stderr to 'message' must carry exactly that text"
        )
        assert body["error"] and body["remedy"], body
        return

    cli_triple = parse_error_contract(cli.stderr)
    assert cli_triple is not None, f"{case.script} refused without a dual-channel block:\n{cli.stderr}"
    assert cli_triple["error"] == case.error_key, cli_triple

    if rest_triple is None:
        # Every action route lifts the script's triple to top-level keys
        # (DualChannelShellEnvelope): those keys ARE the REST refusal. A
        # triple present only inside stderr is the drift this asserts against.
        assert {"error", "message", "remedy"} <= set(body), (
            f"POST {case.rest_route} refused without top-level error/message/remedy: {body}"
        )
        rest_triple = {k: body[k] for k in ("error", "message", "remedy")}
        assert body["stderr"] == cli.stderr, "REST stderr must match the CLI byte-for-byte"
        assert body["exit_code"] == cli.returncode, (body, cli.returncode)

    assert_json_parity(cli_triple, rest_triple)


# ---------------------------------------------------------------------------
# Coverage: the ledger names every census action verb
# ---------------------------------------------------------------------------


def test_every_census_action_verb_has_an_effect_or_refusal_parity_case() -> None:
    """Every active ``action``-class row in the operator verb census is
    named by at least one ledger case, and no case names a verb the census
    does not carry -- the same two facts the parity gate reports, asserted
    here so a pytest run alone also fails loudly on a missing fixture."""
    census_actions = {
        v.name for v in load_manifest().values() if v.status == "active" and v.verb_class == "action"
    }
    covered = verbs_with_a_case()
    missing = sorted(census_actions - covered)
    unknown = sorted(covered - census_actions)
    assert missing == [], f"census action verbs with no parity case: {missing}"
    assert unknown == [], f"parity cases naming a verb the census has no active action row for: {unknown}"
