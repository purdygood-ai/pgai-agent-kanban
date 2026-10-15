"""
read_parity_cases.py
====================
The read-verb parity ledger: one declared golden-pair case per operator read
verb, read by two consumers that must agree on it.

  1. ``team/tests/integration/test_read_parity_fixtures.py`` parametrizes
     over ``READ_PARITY_CASES`` and runs each one on the golden-pair
     harness -- the REAL production CLI script and the REAL production REST
     entrypoint (a loopback uvicorn subprocess on an ephemeral port) over
     the SAME synthetic board -- asserting that the two surfaces resolve to
     the same data, or refuse the same malformed board with the same
     dual-channel payload.
  2. ``team/scripts/lint_api_parity.py``'s census-walk gate imports this
     module and reports, for every ``read``-class row in
     ``team/config/operator_verbs.manifest``, the absence of a case here as
     a ``read_fixture_missing`` finding -- so a new read verb cannot ship
     without a golden pair, and a case cannot name a verb the census does
     not carry.

Because a case declared here IS a test that runs, "declared" and
"exercised" cannot drift apart: the gate reads the same tuple pytest
parametrizes over. This is the read-verb sibling of
``action_parity_cases.py``, which carries the action half.

FOUR KINDS OF CASE
------------------
A **stdout** case proves that a read verb whose REST twin returns the shell
envelope (``exit_code``/``stdout``/``stderr``/``warnings`` around the
script's own output) carries the CLI's stdout byte-for-byte, with the same
exit code. No parsing on either side: the envelope's ``stdout`` string is
compared to the subprocess's stdout string as-is.

A **resolved-fields** case proves a read verb whose CLI renders text and
whose REST twin renders JSON -- two renderers over the ONE resolver they
share (``pgai_agent_kanban.ops.read``). The fixture calls that same
resolver itself, in-process, over the same board, and holds BOTH surfaces
to its result: the REST body must equal the resolver's result in the
route's own documented shape, and the CLI's stdout must carry every
resolved value in the CLI's own rendering (each expected line or block is
built FROM the resolver's result and looked for in the stdout as-is). The
fixture never parses the CLI's ``key=value`` or prose output back into
data -- a second parser of that text would be exactly the second
derivation the shared-resolver rule exists to forbid.

A **refusal** case proves that a malformed board is refused identically:
the same machine ``error`` key, ``message`` and ``remedy`` on the CLI's
stderr (through the production ``parse_error_contract`` reader) and as the
REST body's own top-level keys, with the documented exit code and HTTP
status on each side.

A **delegated** case names a read verb whose golden pair needs bespoke
CLI-text parsing (a verdict line, a metrics table, a queue listing) that
already ships in its own dedicated integration module. The ledger records
the module and test function so the verb is inventoried here for the
gate, and the runner proves the named test actually exists in that module
-- a delegated entry cannot point at nothing.

CONSTRAINTS EVERY CASE HONOURS
------------------------------
- Only the production script and the production route are invoked; nothing
  here re-implements either, and nothing here parses the CLI's text.
- Every seed writes inside the fixture's own synthetic board -- ``seed``
  and every script run receive that board as their kanban root -- never at
  the live kanban root or a live project root. Seeds use the production
  writers where one exists (the deep-verify ledger's ``append_record``).
- This module imports no pytest and starts no process at import time, so
  the parity gate can import it from a lint run.
"""

from __future__ import annotations

import dataclasses
import pathlib
from typing import Callable, Optional, Union

from tests.fixtures.golden_board import PROJECT_NAME, task_ids_by_state

# team/ is three levels up from this file (fixtures/ -> tests/ -> team/); the
# repository root, where the project.cfg template lives, is its parent. Same
# self-location convention as action_parity_cases.py and golden_board.py.
_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent
_REPO_ROOT = _TEAM_DIR.parent

# Values the seeds below write into release-state.md and the deep-verify
# ledger. Well-formed and deliberately far ahead of any real release, so a
# synthetic board can never be mistaken for a real one if inspected by hand.
SEEDED_ACTIVE_RC = "v2.30.0"
SEEDED_RC_OPENED_AT = "2026-01-01T00:00:00+00:00"
SEEDED_RC_OPENED_BY_TASK = "CM-20260101-001-golden-open-rc"
SEEDED_LEDGER_ENTRIES: tuple[dict, ...] = (
    {
        "timestamp": "2026-01-01T01:00:00+00:00",
        "project": PROJECT_NAME,
        "version": "v2.29.0",
        "commit": "abc1234",
        "verdict": "built",
        "detail": "docker/claude-code: built",
    },
    {
        "timestamp": "2026-01-02T01:00:00+00:00",
        "project": PROJECT_NAME,
        "version": SEEDED_ACTIVE_RC,
        "commit": "def5678",
        "verdict": "could-not-attempt",
        "detail": "no container engine on the host",
    },
)

# A value the release-state schema refuses for '## Active RC' ('none' is a
# value and case-sensitive; 'NONE' is not) -- the malformed field the
# refusal case seeds so both surfaces must refuse it alike.
MALFORMED_ACTIVE_RC = "NONE"

# The release-state.md heading each CLI 'key=value' line renders, in the
# schema's own order -- release-state.sh prints exactly these four keys.
RELEASE_STATE_CLI_KEYS: tuple[tuple[str, str], ...] = (
    ("Active RC", "active_rc"),
    ("RC Opened At", "rc_opened_at"),
    ("RC Opened By Task", "rc_opened_by_task"),
    ("Last Released", "last_released"),
)


# ---------------------------------------------------------------------------
# Case records
# ---------------------------------------------------------------------------

SeedFn = Callable[[pathlib.Path, pathlib.Path], None]
"""(board_root, project_dir) -> None: adds state on top of the golden board."""

CliArgsFn = Callable[[pathlib.Path], list[str]]
"""(board_root) -> argv after the script path."""

ResolveFn = Callable[[pathlib.Path], dict]
"""(board_root) -> the shared resolver's result over that board."""

RestViewFn = Callable[[dict], dict]
"""(resolved) -> the REST body the route documents for that result, minus
the envelope-only keys the runner strips (``warnings``)."""

CliExpectationsFn = Callable[[dict], tuple[str, ...]]
"""(resolved) -> substrings the CLI's stdout must carry, each built from
the resolver's result in the CLI's own rendering."""


@dataclasses.dataclass(frozen=True)
class StdoutReadCase:
    """A read verb whose REST twin's envelope ``stdout`` must equal the
    CLI's stdout byte-for-byte, with the same exit code.

    Attributes:
        verb:        The census verb name (``team/config/operator_verbs.manifest``).
        script:      Script path relative to the board's ``scripts/`` link.
        cli_args:    Builds the CLI argv (after the script path) for a board.
        rest_route:  The REST twin's route, e.g. ``/show``.
        rest_query:  The query parameters the REST side sends.
        expect_stdout_contains:
                     Substrings that MUST appear in the CLI's stdout -- the
                     non-triviality guard: a byte-for-byte comparison of two
                     empty or pre-flight-refusal outputs would pass even if
                     neither surface rendered the board.
        seed:        Optional extra board state.
        note:        Why the case is shaped as it is.
    """

    verb: str
    script: str
    cli_args: CliArgsFn
    rest_route: str
    rest_query: dict
    expect_stdout_contains: tuple[str, ...]
    seed: Optional[SeedFn] = None
    note: str = ""

    @property
    def kind(self) -> str:
        return "stdout"


@dataclasses.dataclass(frozen=True)
class ResolvedReadCase:
    """A read verb whose two renderers are held to their one shared resolver.

    Attributes:
        verb, script, cli_args, rest_route, rest_query, seed, note:
                     As for ``StdoutReadCase``.
        resolve:     Calls the SAME resolver both surfaces call, over the
                     board, and returns its result.
        rest_view:   Maps that result onto the REST body the route documents
                     (e.g. the ops layer's literal ``"none"`` rendered as
                     JSON ``null``); compared to the real response with the
                     golden comparator.
        cli_expectations:
                     Maps that result onto the lines or blocks the CLI's own
                     rendering must carry; each is looked for in the real
                     stdout as-is -- never parsed back out of it.
        expect_stdout_contains:
                     Seeded literals the CLI's stdout must carry -- proves
                     the seed reached the board both surfaces read, so a
                     comparison over an unseeded, all-``none`` board cannot
                     pass by accident.
    """

    verb: str
    script: str
    cli_args: CliArgsFn
    rest_route: str
    rest_query: dict
    resolve: ResolveFn
    rest_view: RestViewFn
    cli_expectations: CliExpectationsFn
    expect_stdout_contains: tuple[str, ...]
    seed: Optional[SeedFn] = None
    note: str = ""

    @property
    def kind(self) -> str:
        return "resolved-fields"


@dataclasses.dataclass(frozen=True)
class RefusalReadCase:
    """A malformed board both surfaces must refuse alike.

    Attributes:
        verb, script, cli_args, rest_route, rest_query, seed, note:
                     As for ``StdoutReadCase``.
        error_key:   The dual-channel machine key both surfaces must produce.
        cli_exit_code:
                     The exit code the script documents for this refusal.
        rest_status: The HTTP status the route documents for this refusal.
    """

    verb: str
    script: str
    cli_args: CliArgsFn
    rest_route: str
    rest_query: dict
    error_key: str
    cli_exit_code: int
    rest_status: int
    seed: Optional[SeedFn] = None
    note: str = ""

    @property
    def kind(self) -> str:
        return "refusal"


@dataclasses.dataclass(frozen=True)
class DelegatedReadCase:
    """A read verb whose golden pair ships in a dedicated integration module.

    Attributes:
        verb:   The census verb name.
        module: The module's path relative to ``team/``.
        test:   The name of the test function in that module that runs the
                real CLI script and the real REST route over the same board.
        note:   Why the pair needs its own module (what it has to parse).
    """

    verb: str
    module: str
    test: str
    note: str = ""

    @property
    def kind(self) -> str:
        return "delegated"


ReadParityCase = Union[StdoutReadCase, ResolvedReadCase, RefusalReadCase, DelegatedReadCase]


# ---------------------------------------------------------------------------
# Seeds: extra state on top of build_synthetic_board's shape
# ---------------------------------------------------------------------------


def _release_state_text(active_rc: str) -> str:
    """release-state.md in the shipped four-heading schema, with three
    fields set and ``Last Released`` legitimately ``none`` -- so the pair
    exercises both a set value and the unset sentinel in one board."""
    return (
        f"## Active RC\n{active_rc}\n\n"
        f"## RC Opened At\n{SEEDED_RC_OPENED_AT}\n\n"
        f"## RC Opened By Task\n{SEEDED_RC_OPENED_BY_TASK}\n\n"
        "## Last Released\nnone\n"
    )


def seed_release_state(root: pathlib.Path, project_dir: pathlib.Path) -> None:
    """A valid release-state.md with an RC in flight."""
    (project_dir / "release-state.md").write_text(
        _release_state_text(SEEDED_ACTIVE_RC), encoding="utf-8"
    )


def seed_malformed_release_state(root: pathlib.Path, project_dir: pathlib.Path) -> None:
    """A release-state.md whose ``## Active RC`` the schema refuses."""
    (project_dir / "release-state.md").write_text(
        _release_state_text(MALFORMED_ACTIVE_RC), encoding="utf-8"
    )


def seed_project_cfg_with_dev_tree(root: pathlib.Path, project_dir: pathlib.Path) -> None:
    """The shipped project.cfg template with ``dev_tree_path`` pointed at a
    plain directory inside the board.

    verify-rc-state.sh refuses before running any check when the project
    has no ``dev_tree_path``; a directory that is not a git repository is
    accepted and reported as a warning by its branch check, which keeps the
    board free of any git repository (nothing here is a checkout).
    """
    dev_tree = root / "dev-tree"
    dev_tree.mkdir(parents=True, exist_ok=True)
    text = (_REPO_ROOT / "project.cfg_example").read_text(encoding="utf-8")
    template_line = "\ndev_tree_path =\n"
    assert text.count(template_line) == 1, (
        "project.cfg_example no longer carries exactly one uncommented, empty "
        f"{template_line.strip()!r} line; update seed_project_cfg_with_dev_tree to match the template."
    )
    (project_dir / "project.cfg").write_text(
        text.replace(template_line, f"\ndev_tree_path = {dev_tree}\n"), encoding="utf-8"
    )


def seed_rc_state_board(root: pathlib.Path, project_dir: pathlib.Path) -> None:
    """What verify-rc-state.sh needs to run every check: a project.cfg with
    a dev tree, and a release-state.md for its consistency checks to read."""
    seed_project_cfg_with_dev_tree(root, project_dir)
    seed_release_state(root, project_dir)


def seed_deep_verify_ledger(root: pathlib.Path, project_dir: pathlib.Path) -> None:
    """Two deep-verify records, written through the ledger's own production
    writer so the file carries exactly the shape its reader expects."""
    from pgai_agent_kanban.deep_verify.ledger import (
        DeepVerifyRecord,
        append_record,
        ledger_path_for_project,
    )

    ledger_path = ledger_path_for_project(str(root), PROJECT_NAME)
    for entry in SEEDED_LEDGER_ENTRIES:
        append_record(ledger_path, DeepVerifyRecord(**entry))


# ---------------------------------------------------------------------------
# Resolvers and views: the shared resolver, and each surface's rendering
# of its result
# ---------------------------------------------------------------------------


def resolve_release_state(root: pathlib.Path) -> dict:
    """``pgai_agent_kanban.ops.read.get_release_state`` -- the one function
    release-state.sh and GET /release-state both call."""
    from pgai_agent_kanban.ops.context import OpsContext
    from pgai_agent_kanban.ops.read import get_release_state

    return get_release_state(OpsContext(kanban_root=root), PROJECT_NAME)


def release_state_rest_view(resolved: dict) -> dict:
    """GET /release-state's documented success body: the four fields under
    their snake_case names, a legitimately unset field rendered as JSON
    ``null`` rather than the ops layer's literal ``"none"``."""
    body: dict = {"project": PROJECT_NAME}
    for heading, key in RELEASE_STATE_CLI_KEYS:
        value = resolved[heading]
        body[key] = None if value.strip().lower() == "none" else value
    return body


def release_state_cli_expectations(resolved: dict) -> tuple[str, ...]:
    """release-state.sh's documented rendering: one ``key=value`` line per
    field, the unset sentinel printed as the literal ``none``."""
    return tuple(f"{key}={resolved[heading]}" for heading, key in RELEASE_STATE_CLI_KEYS)


def resolve_deep_verify_status(root: pathlib.Path) -> dict:
    """``pgai_agent_kanban.ops.read.get_deep_verify_status`` -- the one
    function deep-verify-status.sh and GET /deep-verify-status both call."""
    from pgai_agent_kanban.ops.context import OpsContext
    from pgai_agent_kanban.ops.read import get_deep_verify_status

    return get_deep_verify_status(OpsContext(kanban_root=root), PROJECT_NAME)


def deep_verify_status_rest_view(resolved: dict) -> dict:
    """GET /deep-verify-status's documented success body: the resolver's
    ``mode``/``entries``/``latest`` verbatim, plus the project name."""
    return {
        "project": PROJECT_NAME,
        "mode": resolved["mode"],
        "entries": resolved["entries"],
        "latest": resolved["latest"],
    }


def deep_verify_status_cli_expectations(resolved: dict) -> tuple[str, ...]:
    """deep-verify-status.sh's documented rendering: the ``mode:`` line, the
    ``latest:`` summary line, the ledger count line, and one block per
    record rendered by the ledger module's own ``format_record`` -- the
    same production renderer the script itself calls."""
    from pgai_agent_kanban.deep_verify.ledger import DeepVerifyRecord, format_record

    latest = resolved["latest"]
    latest_line = (
        "latest: never-run"
        if latest is None
        else (
            f"latest: {latest['verdict']} "
            f"({latest['timestamp']}, {latest['version']}, {latest['commit']})"
        )
    )
    return (
        f"mode: {resolved['mode']}",
        latest_line,
        f"Ledger ({len(resolved['entries'])} entries):",
        *(format_record(DeepVerifyRecord.from_dict(entry)) for entry in resolved["entries"]),
    )


# ---------------------------------------------------------------------------
# argv builders
# ---------------------------------------------------------------------------


def _args(*argv: str) -> CliArgsFn:
    """A board-independent argv."""
    return lambda root: list(argv)


_TASKS = task_ids_by_state()
_P = PROJECT_NAME


# ---------------------------------------------------------------------------
# The ledger
# ---------------------------------------------------------------------------

READ_PARITY_CASES: tuple = (
    # --- Envelope reads: stdout byte-for-byte -------------------------------
    StdoutReadCase(
        verb="show", script="show.sh",
        cli_args=_args("--project", _P, "--key", _TASKS["WORKING"]),
        rest_route="/show", rest_query={"project": _P, "key": _TASKS["WORKING"]},
        expect_stdout_contains=("## State\nWORKING",),
        note="show.sh and the route both call ops.read.get_task_status with the same project/key; stdout is the task's status.md",
    ),
    StdoutReadCase(
        verb="verify-rc-state", script="verify-rc-state.sh",
        cli_args=_args(_P),
        rest_route="/verify-rc-state", rest_query={"project": _P},
        expect_stdout_contains=(
            "--- Check a: release-state.md consistency ---",
            "--- Check f: Bundle invariants ---",
            "verify-rc-state: ",
        ),
        seed=seed_rc_state_board,
        note=(
            "the script takes its project as a positional argument; the route builds the "
            "same argv. Every check section and the summary line must be present, so a "
            "pre-flight refusal on both sides cannot pass as parity"
        ),
    ),
    # --- One resolver, two renderers ---------------------------------------
    ResolvedReadCase(
        verb="release-state", script="release-state.sh",
        cli_args=_args("--project", _P),
        rest_route="/release-state", rest_query={"project": _P},
        resolve=resolve_release_state,
        rest_view=release_state_rest_view,
        cli_expectations=release_state_cli_expectations,
        expect_stdout_contains=(f"active_rc={SEEDED_ACTIVE_RC}", "last_released=none"),
        seed=seed_release_state,
        note="both surfaces call ops.read.get_release_state; the CLI prints key=value, the route JSON with none -> null",
    ),
    RefusalReadCase(
        verb="release-state", script="release-state.sh",
        cli_args=_args("--project", _P),
        rest_route="/release-state", rest_query={"project": _P},
        error_key="release-state.invalid-field",
        cli_exit_code=3,
        rest_status=422,
        seed=seed_malformed_release_state,
        note=(
            "a release-state.md field the schema refuses is refused on both surfaces with "
            "the validating loader's own dual-channel block, never a guessed value"
        ),
    ),
    ResolvedReadCase(
        verb="deep-verify-status", script="deep-verify-status.sh",
        cli_args=_args("--project", _P),
        rest_route="/deep-verify-status", rest_query={"project": _P},
        resolve=resolve_deep_verify_status,
        rest_view=deep_verify_status_rest_view,
        cli_expectations=deep_verify_status_cli_expectations,
        expect_stdout_contains=(
            f"Ledger ({len(SEEDED_LEDGER_ENTRIES)} entries):",
            f"- Verdict: {SEEDED_LEDGER_ENTRIES[-1]['verdict']}",
        ),
        seed=seed_deep_verify_ledger,
        note="both surfaces call ops.read.get_deep_verify_status; the CLI renders records through the ledger's format_record",
    ),
    # --- Pairs that parse bespoke CLI text, shipped in their own module ----
    DelegatedReadCase(
        verb="why",
        module="tests/integration/test_golden_pair_harness.py",
        test="test_golden_pair_why_verdict_matches_between_real_cli_and_real_rest_route",
        note=(
            "why.sh renders a one-line verdict sentence; the pair matches it against the "
            "verdict entry GET /verdicts returns (and test_why_details_rest_parity.py "
            "covers the --details pane through the production block parser)"
        ),
    ),
    DelegatedReadCase(
        verb="show-queues",
        module="tests/integration/test_show_queues_rest_parity.py",
        test="test_per_agent_details_listing_matches_between_cli_and_rest",
        note=(
            "the compact pane carries wall-clock elapsed time, so only the --details "
            "listing is a stable pair; it is parsed into the resolver's own shape there"
        ),
    ),
    DelegatedReadCase(
        verb="rc-metrics",
        module="tests/integration/test_rc_metrics_rest_parity.py",
        test="test_rc_metrics_blocked_column_matches_between_cli_and_rest_with_a_blocked_task_on_the_board",
        note="show-metrics.sh renders a table; the pair reads its one data row against GET /metrics?format=json",
    ),
    # agent-metrics.sh renders per-agent rows and a span footer; each pair
    # reads them against GET /agent-metrics, one entry per span it proves.
    DelegatedReadCase(
        verb="agent-metrics",
        module="tests/integration/test_agent_metrics_rest_parity.py",
        test="test_agent_metrics_per_task_wall_matches_between_cli_and_rest",
        note="--window N over two rollups: every row field agrees, per-task wall is each agent's own",
    ),
    DelegatedReadCase(
        verb="agent-metrics",
        module="tests/integration/test_agent_metrics_rest_parity.py",
        test="test_all_shipped_span_matches_between_cli_and_rest_and_equals_the_hand_computation",
        note="--all / all=true over a 40-RC history: rows and footer counts agree and equal a hand computation",
    ),
    DelegatedReadCase(
        verb="agent-metrics",
        module="tests/integration/test_agent_metrics_rest_parity.py",
        test="test_cancelled_rollup_in_default_window_is_excluded_alike_and_removing_it_changes_no_agent_value",
        note="default window: a cancelled rollup inside it is excluded alike and removing it moves no value",
    ),
    DelegatedReadCase(
        verb="agent-metrics",
        module="tests/integration/test_agent_metrics_rest_parity.py",
        test="test_since_span_selects_the_same_rows_and_values_on_cli_and_rest",
        note="--since vX.Y.Z / since=vX.Y.Z: the same semver-bounded rows, values and footer counts",
    ),
    DelegatedReadCase(
        verb="agent-metrics",
        module="tests/integration/test_agent_metrics_rest_parity.py",
        test="test_last_span_selects_the_same_rows_and_values_on_cli_and_rest",
        note="--last N / last=N: the same newest-N shipped rows, values and footer counts",
    ),
)


def case_id(case) -> str:
    """The pytest id for a case: ``<verb>[<kind>]``, stable across runs."""
    return f"{case.verb}[{case.kind}]"


def verbs_with_a_case() -> frozenset[str]:
    """Every verb the ledger names at least once -- what the parity gate reads."""
    return frozenset(case.verb for case in READ_PARITY_CASES)
