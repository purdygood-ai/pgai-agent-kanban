"""
test_read_parity_fixtures.py — the read half of CLI-vs-REST parity on the
golden-pair harness: for every read verb in the operator verb census, the
REST twin returns the same resolved data the CLI renders, or refuses the
same malformed board with the same dual-channel payload.

The cases themselves are declared once, in
``team/tests/fixtures/read_parity_cases.py`` (the ledger), which the parity
gate (``team/scripts/lint_api_parity.py``) also reads to report any census
read row with no case. This module is the runner for that ledger:

  stdout case       One synthetic board. The REAL CLI script runs against it
                    and the REAL production REST entrypoint -- a loopback
                    uvicorn subprocess on an ephemeral port, its kanban root
                    pointed at the same board -- serves the REST call. The
                    envelope's ``stdout`` must equal the subprocess's stdout
                    byte-for-byte and the exit codes must agree; the
                    comparison is made with the golden comparator, which
                    names the differing key.

  resolved case     One board. The ONE resolver both surfaces call is called
                    here too, in-process, over that board. The REST body
                    (envelope stripped) must equal the resolver's result in
                    the route's documented shape, and the CLI's stdout must
                    carry every line or block the ledger builds FROM that
                    result -- looked for as-is, never parsed back out of the
                    text, so no second parser of the CLI's rendering exists.

  refusal case      One malformed board. The CLI must exit with its
                    documented code and print the dual-channel block; the
                    route must answer its documented status with the same
                    ``error``/``message``/``remedy`` as top-level keys. The
                    CLI's triple is read through the production
                    ``parse_error_contract`` reader.

  delegated case    A verb whose golden pair needs bespoke CLI-text parsing
                    that ships in its own module. The runner proves the
                    module exists and defines the named test function, so
                    a delegated entry cannot inventory a verb against
                    nothing.

Integration test contract (team/tests/integration/conftest.py): no
integration point is mocked. Real uvicorn subprocess, real HTTP, real
``bash <script>`` subprocess; only the board's DATA is synthetic.

Test-name policy (roles/TESTER.md Anti-pattern 6): names describe behavior,
never bug IDs, task IDs, or scaffolding labels.
"""

from __future__ import annotations

import ast
import contextlib
import json
import os
import pathlib
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from typing import Generator, Iterator

import pytest

from pgai_agent_kanban.lib.error_contract import parse_error_contract
from pgai_agent_kanban.lib.operator_verb_manifest import load_manifest
from tests.fixtures.api_server import ServerHandle, api_server_fixture
from tests.fixtures.golden_board import PROJECT_NAME, build_synthetic_board
from tests.fixtures.golden_comparator import assert_json_parity, strip_envelope
from tests.fixtures.read_parity_cases import (
    READ_PARITY_CASES,
    DelegatedReadCase,
    RefusalReadCase,
    ResolvedReadCase,
    StdoutReadCase,
    case_id,
    verbs_with_a_case,
)

_STDOUT_CASES = [c for c in READ_PARITY_CASES if isinstance(c, StdoutReadCase)]
_RESOLVED_CASES = [c for c in READ_PARITY_CASES if isinstance(c, ResolvedReadCase)]
_REFUSAL_CASES = [c for c in READ_PARITY_CASES if isinstance(c, RefusalReadCase)]
_DELEGATED_CASES = [c for c in READ_PARITY_CASES if isinstance(c, DelegatedReadCase)]

# team/ is two levels up from this file's directory (integration/ -> tests/
# -> team/); a delegated case's module path is relative to it.
_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent

# The envelope-only keys a ShellEnvelope carries that no CLI stdout does.
# ``exit_code`` is deliberately NOT stripped: a stdout case holds the two
# surfaces to the same exit code as well as the same bytes.
_ENVELOPE_ONLY_KEYS = ("stderr", "warnings")


# ---------------------------------------------------------------------------
# CLI and REST invocation against a board
# ---------------------------------------------------------------------------


def _cli_env(board: pathlib.Path, temp_dir: pathlib.Path) -> dict[str, str]:
    """Environment for a real CLI script run against *board*.

    ``PGAI_AGENT_KANBAN_ROOT_PATH`` is what every entry-point script
    resolves its root from (env_bootstrap.sh honours an exported value);
    ``KANBAN_ROOT`` is the alias project_paths.sh reads directly, so both
    name the board. ``PGAI_PROJECT_NAME`` is cleared so no script assumes a
    default project; colour is switched off the same way on both surfaces
    (see ``_rest_server``) so a byte-for-byte stdout comparison is never
    decided by which side thought it had a terminal.
    """
    env = dict(os.environ)
    env.pop("PGAI_PROJECT_NAME", None)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(board)
    env["KANBAN_ROOT"] = str(board)
    env["PGAI_AGENT_KANBAN_TEMP_DIR"] = str(temp_dir)
    env["NO_COLOR"] = "1"
    env["TERM"] = "dumb"
    return env


def _run_cli(
    board: pathlib.Path, script: str, args: list[str], temp_dir: pathlib.Path
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(board / "scripts" / script), *args],
        capture_output=True,
        text=True,
        env=_cli_env(board, temp_dir),
        timeout=60,
        stdin=subprocess.DEVNULL,
    )


def _get_json(url: str) -> tuple[int, dict]:
    """GET *url* and return ``(status, decoded_body)`` for any status -- a
    refusal (404/422/500) is a result here, not an exception."""
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _rest_url(handle: ServerHandle, route: str, query: dict) -> str:
    return f"http://127.0.0.1:{handle.port}{route}?{urllib.parse.urlencode(query)}"


@contextlib.contextmanager
def _rest_server(
    board: pathlib.Path, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[ServerHandle]:
    """The real production REST entrypoint, rooted at *board*.

    ``api_server_fixture`` inherits this process's environment and sets
    ``PGAI_AGENT_KANBAN_ROOT_PATH`` itself; the alias ``KANBAN_ROOT``, the
    framework temp dir, and the no-colour switches are set here so a script
    the server shells out to sees exactly the environment the CLI side is
    given.
    """
    monkeypatch.setenv("KANBAN_ROOT", str(board))
    monkeypatch.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(tmp_path / "server_tmp"))
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.delenv("PGAI_PROJECT_NAME", raising=False)
    server_logs = tmp_path / "server_logs"
    server_logs.mkdir(parents=True, exist_ok=True)
    with api_server_fixture(server_logs, kanban_root=board) as handle:
        yield handle


@pytest.fixture
def board(tmp_path: pathlib.Path) -> Generator[pathlib.Path, None, None]:
    """One synthetic board both surfaces read."""
    root = tmp_path / "board"
    build_synthetic_board(root)
    yield root


def _seed(case, board: pathlib.Path) -> None:
    if case.seed is not None:
        case.seed(board, board / "projects" / PROJECT_NAME)


# ---------------------------------------------------------------------------
# Stdout parity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", _STDOUT_CASES, ids=case_id)
def test_rest_envelope_stdout_matches_the_cli_stdout_byte_for_byte(
    case: StdoutReadCase,
    board: pathlib.Path,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The REAL CLI script and the REAL REST route, over the SAME seeded
    board, produce the same stdout bytes and the same exit code -- and the
    stdout carries the content the case names, so two empty or refused
    outputs cannot pass as parity."""
    _seed(case, board)

    cli = _run_cli(board, case.script, case.cli_args(board), tmp_path / "cli_tmp")
    for needle in case.expect_stdout_contains:
        assert needle in cli.stdout, (
            f"{case.script} stdout does not carry {needle!r} ({case.note})\n"
            f"stdout: {cli.stdout}\nstderr: {cli.stderr}"
        )

    with _rest_server(board, tmp_path, monkeypatch) as handle:
        status, body = _get_json(_rest_url(handle, case.rest_route, case.rest_query))
    assert status == (200 if cli.returncode == 0 else 500), (
        f"GET {case.rest_route} answered HTTP {status} for a CLI exit of {cli.returncode}: {body}"
    )

    cli_value = {"exit_code": cli.returncode, "stdout": cli.stdout}
    rest_value = strip_envelope(body, envelope_keys=_ENVELOPE_ONLY_KEYS)
    assert_json_parity(cli_value, rest_value)


# ---------------------------------------------------------------------------
# Resolved-fields parity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", _RESOLVED_CASES, ids=case_id)
def test_both_renderers_carry_the_shared_resolvers_result(
    case: ResolvedReadCase,
    board: pathlib.Path,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Over the SAME seeded board, the REST body equals the shared
    resolver's result in the route's documented shape, and the CLI's stdout
    carries every line the ledger builds from that same result -- so the
    text and JSON renderers are proven against one derivation, and neither
    surface's output is parsed back into data."""
    _seed(case, board)
    resolved = case.resolve(board)

    cli = _run_cli(board, case.script, case.cli_args(board), tmp_path / "cli_tmp")
    assert cli.returncode == 0, (
        f"{case.script} exited {cli.returncode}\nstdout: {cli.stdout}\nstderr: {cli.stderr}"
    )
    for needle in case.expect_stdout_contains:
        assert needle in cli.stdout, (
            f"{case.script} stdout does not carry the seeded value {needle!r} -- the seed "
            f"did not reach the board both surfaces read\nstdout: {cli.stdout}"
        )
    for rendered in case.cli_expectations(resolved):
        assert rendered in cli.stdout, (
            f"{case.script} stdout does not carry the resolver's value rendered as "
            f"{rendered!r} ({case.note})\nstdout: {cli.stdout}"
        )

    with _rest_server(board, tmp_path, monkeypatch) as handle:
        status, body = _get_json(_rest_url(handle, case.rest_route, case.rest_query))
    assert status == 200, f"GET {case.rest_route} answered HTTP {status}: {body}"

    assert_json_parity(case.rest_view(resolved), strip_envelope(body, envelope_keys=("warnings",)))


# ---------------------------------------------------------------------------
# Refusal parity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", _REFUSAL_CASES, ids=case_id)
def test_rest_read_refuses_a_malformed_board_like_its_cli_twin(
    case: RefusalReadCase,
    board: pathlib.Path,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Given the same malformed board, the REAL CLI script and the REAL REST
    route refuse alike: the documented exit code and HTTP status on each
    side, and an identical dual-channel ``error``/``message``/``remedy`` --
    the CLI's read from its stderr through the production parser, the
    route's from the body's own top-level keys. Neither prints a guessed
    value in place of the field that failed."""
    _seed(case, board)

    cli = _run_cli(board, case.script, case.cli_args(board), tmp_path / "cli_tmp")
    assert cli.returncode == case.cli_exit_code, (
        f"{case.script} exited {cli.returncode}, expected {case.cli_exit_code} ({case.note})\n"
        f"stdout: {cli.stdout}\nstderr: {cli.stderr}"
    )
    assert cli.stdout == "", f"{case.script} printed to stdout while refusing: {cli.stdout!r}"
    cli_triple = parse_error_contract(cli.stderr)
    assert cli_triple is not None, f"{case.script} refused without a dual-channel block:\n{cli.stderr}"
    assert cli_triple["error"] == case.error_key, cli_triple

    with _rest_server(board, tmp_path, monkeypatch) as handle:
        status, body = _get_json(_rest_url(handle, case.rest_route, case.rest_query))
    assert status == case.rest_status, (
        f"GET {case.rest_route} answered HTTP {status}, expected {case.rest_status}: {body}"
    )
    assert {"error", "message", "remedy"} <= set(body), body
    rest_triple = {k: body[k] for k in ("error", "message", "remedy")}

    assert_json_parity(cli_triple, rest_triple)


# ---------------------------------------------------------------------------
# Delegated pairs: the named test really exists
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", _DELEGATED_CASES, ids=case_id)
def test_delegated_read_verb_names_a_test_that_exists_in_its_module(
    case: DelegatedReadCase,
) -> None:
    """A delegated ledger entry inventories its verb against a real test:
    the module it names exists under team/ and defines the named
    ``test_`` function at module level, so the entry cannot cover a verb
    with a test that was renamed or removed."""
    module_path = _TEAM_DIR / case.module
    assert module_path.is_file(), f"{case.verb}: delegated module not found: {module_path}"
    assert case.test.startswith("test_"), f"{case.verb}: {case.test!r} is not a pytest test name"

    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    defined = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert case.test in defined, (
        f"{case.verb}: {module_path.name} defines no module-level function {case.test!r}; "
        f"its test functions are {sorted(n for n in defined if n.startswith('test_'))}"
    )


# ---------------------------------------------------------------------------
# Coverage: the ledger names every census read verb
# ---------------------------------------------------------------------------


def test_every_census_read_verb_has_a_golden_pair_case() -> None:
    """Every active ``read``-class row in the operator verb census is named
    by at least one ledger case, and no case names a verb the census does
    not carry -- the same two facts the parity gate reports, asserted here
    so a pytest run alone also fails loudly on a missing golden pair."""
    census_reads = {
        v.name for v in load_manifest().values() if v.status == "active" and v.verb_class == "read"
    }
    covered = verbs_with_a_case()
    missing = sorted(census_reads - covered)
    unknown = sorted(covered - census_reads)
    assert missing == [], f"census read verbs with no golden-pair case: {missing}"
    assert unknown == [], f"golden-pair cases naming a verb the census has no active read row for: {unknown}"
