"""
action_parity_cases.py
======================
The action-verb parity ledger: one declared case per operator action verb,
read by two consumers that must agree on it.

  1. ``team/tests/integration/test_action_parity_fixtures.py`` parametrizes
     over ``ACTION_PARITY_CASES`` and runs each one on the golden-pair
     harness -- the REAL production CLI script on one synthetic board and
     the REAL production REST entrypoint (a loopback uvicorn subprocess on
     an ephemeral port) on an identically seeded second board -- asserting
     that the two surfaces produce the same on-disk effect, or refuse the
     same malformed parameter with the same dual-channel payload.
  2. ``team/scripts/lint_api_parity.py``'s census-walk gate imports this
     module and reports, for every ``action``-class row in
     ``team/config/operator_verbs.manifest``, the absence of a case here as
     an ``action_fixture_missing`` finding -- so a new action verb cannot
     ship without a parity fixture, and a case cannot name a verb the census
     does not carry.

Because a case declared here IS a test that runs, "declared" and
"exercised" cannot drift apart: the gate reads the same tuple pytest
parametrizes over.

WHY TWO KINDS OF CASE
---------------------
An **effect** case proves that a REST action changed the board exactly as
its CLI twin did -- the assertion the requirements document singles out as
the one that catches "a route that returns 200 while writing nothing". It
compares the set of files each surface created, changed or removed, by
content, after normalising the two volatile things two boards legitimately
differ in (their own root path, and wall-clock timestamps a script stamps
into what it writes).

A **refusal** case proves that a malformed parameter is refused identically:
the same machine ``error`` key, ``message`` and ``remedy`` where the script
emits the dual-channel house shape -- read from the REST body's own
top-level keys, since every action route lifts the script's triple there --
or, for the scripts that still refuse with plain stderr text, the same exit
code and byte-identical stderr, with the REST envelope's ``message`` equal
to that text under the route's fallback key. ``error_key=None`` records that
second, weaker contract explicitly rather than skipping the verb; the
verification lane can file the missing dual-channel refusal against the
script from that record.

Every action verb carries at least one case; verbs whose effect is cheap to
observe carry an effect case (most of them), verbs whose real effect needs a
git checkout, an RC, or a long-running lane (unwind-rc, ship-rc, cancel-rc,
deep-verify, cold-build-now, wake-now) carry a refusal case instead. Every
verb whose script refuses dual-channel ALSO carries a refusal case, so the
top-level ``error`` key is proven per route: for the scripts that resolve
``--project`` through the shared resolver that is a project registered in
no board (``project_resolve.not_found``). The two global sentinel verbs
(halt-global, unhalt-global) take no parameter to malform; their only
refusals are an absent or unwritable kanban root, which is an environment
fault rather than a parameter, so they carry an effect case alone.

CONSTRAINTS EVERY CASE HONOURS
------------------------------
- Only the production script and the production route are invoked; nothing
  here re-implements either.
- A case that arms a HALT / HALT-AFTER / DEFER-LOW-BUGS sentinel arms it
  inside the fixture's own synthetic board -- ``seed`` and every script run
  receive that board as their kanban root -- never at the live kanban root
  or a live project root. The test module removes every such sentinel from
  both boards when the case ends, whatever the outcome.
- This module imports no pytest and starts no process, so the parity gate
  can import it from a lint run.
"""

from __future__ import annotations

import dataclasses
import pathlib
import shutil
import subprocess
from typing import Callable, Optional, Union

from tests.fixtures.golden_board import PROJECT_NAME, task_ids_by_state

# team/ is three levels up from this file (fixtures/ -> tests/ -> team/); the
# repository root, where the two *_example config templates live, is its
# parent. Same self-location convention as golden_board.py.
_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent
_REPO_ROOT = _TEAM_DIR.parent

# A well-formed version far ahead of any real release, so a HALT-AFTER or
# DEFER-LOW-BUGS armed on a synthetic board can never be mistaken for a
# real one if it were ever inspected by hand.
PARITY_UNTIL_VERSION = "v2.30.0"

# The one malformed --until value the halt-after and defer refusal cases
# send to both surfaces: not vX.Y.Z, and normalisation of a leading 'v'
# cannot rescue it.
MALFORMED_VERSION = "2.x"

# A project name registered in no synthetic board, for refusals that must
# fail project resolution on both surfaces.
GHOST_PROJECT = "no-such-project-in-parity-board"

# Names the seeds below give to the state they add on top of
# build_synthetic_board's own shape.
ADOPTED_PROJECT = "parity-adopted-project"
CREATED_PROJECT = "parity-created-project"
STALE_LOCK_NAME = f"wake-{PROJECT_NAME}"
INTAKE_FILENAME = "BUG-7777-parity-intake.md"
INTAKE_CONTENT = (
    f"# {INTAKE_FILENAME[:-3]}\n\n"
    f"**Bug ID:** {INTAKE_FILENAME[:-3]}\n"
    "**Filed By:** action-parity fixture\n"
    "**Date:** 2026-01-01\n"
    "**Severity:** low\n\n"
    "## Status\nopen\n\n"
    "## Symptom\nFixture-only intake payload; not a real defect.\n"
)

# The sentinel files a parity case may arm and must never leak: their
# basenames, looked for at the board root and under every projects/<name>/.
SENTINEL_BASENAMES: tuple[str, ...] = ("HALT", "HALT-AFTER", "DEFER-LOW-BUGS")


# ---------------------------------------------------------------------------
# Case records
# ---------------------------------------------------------------------------

SeedFn = Callable[[pathlib.Path, pathlib.Path], None]
"""(board_root, project_dir) -> None: adds state on top of the golden board.
Called once per board, so an effect case's two boards are seeded alike."""

CliArgsFn = Callable[[pathlib.Path], list[str]]
"""(board_root) -> argv after the script path. A callable rather than a
tuple because one verb (intake) names a source file that lives under the
CLI board."""


@dataclasses.dataclass(frozen=True)
class EffectParityCase:
    """A REST action whose on-disk effect must equal its CLI twin's.

    Attributes:
        verb:           The census verb name (``team/config/operator_verbs.manifest``).
        script:         Script path relative to the board's ``scripts/`` link.
        cli_args:       Builds the CLI argv (after the script path) for a board.
        rest_route:     The REST twin's route, e.g. ``/operations/halt``.
        rest_body:      The JSON body the REST side sends.
        expect_changed: Board-relative paths that MUST appear in the effect --
                        the non-triviality guard: an effect comparison over
                        an empty change set would pass even if both surfaces
                        wrote nothing.
        seed:           Optional extra board state both boards receive.

    Two things the two boards legitimately differ in are normalised by the
    test module before comparison, for every case: each board's own root
    path, and wall-clock timestamps a script stamps into what it writes.
    """

    verb: str
    script: str
    cli_args: CliArgsFn
    rest_route: str
    rest_body: dict
    expect_changed: tuple[str, ...]
    seed: Optional[SeedFn] = None

    @property
    def kind(self) -> str:
        return "effect"


@dataclasses.dataclass(frozen=True)
class RefusalParityCase:
    """A malformed parameter both surfaces must refuse alike.

    Attributes:
        verb:       The census verb name.
        script:     Script path relative to the board's ``scripts/`` link.
        cli_args:   Builds the refused CLI argv for a board.
        rest_route: The REST twin's route.
        rest_body:  The JSON body carrying the same malformed parameter.
        error_key:  The dual-channel machine key both surfaces must produce.
                    ``None`` records that the script refuses with plain
                    stderr text and carries no house-shape triple, so the
                    fixture asserts exit-code and stderr equality instead.
        channel:    Where the REST side's refusal is observable:
                    ``"response"`` -- in the HTTP response body (the common
                    case); ``"detached-log"`` -- the route dispatches the
                    script detached and returns 200 immediately, so the
                    refusal is read from the log file the response names,
                    once the detached child has exited.
        seed:       Optional extra board state.
        note:       Why this case is a refusal case (or why ``error_key`` is
                    None) -- surfaced in the test id's failure output.
    """

    verb: str
    script: str
    cli_args: CliArgsFn
    rest_route: str
    rest_body: dict
    error_key: Optional[str]
    channel: str = "response"
    seed: Optional[SeedFn] = None
    note: str = ""

    @property
    def kind(self) -> str:
        return "refusal"


ActionParityCase = Union[EffectParityCase, RefusalParityCase]


# ---------------------------------------------------------------------------
# Seeds: extra state on top of build_synthetic_board's shape
# ---------------------------------------------------------------------------


def seed_project_halt(root: pathlib.Path, project_dir: pathlib.Path) -> None:
    """Arm a project-scoped HALT (what unhalt.sh removes)."""
    (project_dir / "HALT").write_text("", encoding="utf-8")


def seed_root_halt(root: pathlib.Path, project_dir: pathlib.Path) -> None:
    """Arm the fleet-scoped HALT at the board root (what unhalt-global.sh removes)."""
    (root / "HALT").write_text("", encoding="utf-8")


def seed_project_cfg(root: pathlib.Path, project_dir: pathlib.Path) -> None:
    """Give the golden project a project.cfg (the shipped template verbatim).

    set-version-ceiling.sh edits it in place; unwind-rc.sh and cancel-rc.sh
    load it before validating anything else, so a refusal case for either
    needs it present to reach the parameter check under test.
    """
    shutil.copy(_REPO_ROOT / "project.cfg_example", project_dir / "project.cfg")


def seed_kanban_cfg_active_codex(root: pathlib.Path, project_dir: pathlib.Path) -> None:
    """Seed kanban.cfg from the shipped template with ``active = codex`` so
    that switching to ``claude`` is a real change, not a no-op rewrite."""
    text = (_REPO_ROOT / "kanban.cfg_example").read_text(encoding="utf-8")
    assert text.count("\nactive = claude\n") == 1, (
        "kanban.cfg_example no longer carries exactly one 'active = claude' line; "
        "update seed_kanban_cfg_active_codex to match the template."
    )
    (root / "kanban.cfg").write_text(
        text.replace("\nactive = claude\n", "\nactive = codex\n"), encoding="utf-8"
    )


def seed_unregistered_project_dir(root: pathlib.Path, project_dir: pathlib.Path) -> None:
    """A project directory that exists on disk but is not in projects.cfg --
    what add-project.sh registers."""
    (root / "projects" / ADOPTED_PROJECT).mkdir(parents=True, exist_ok=True)


def seed_registered_project_dir(root: pathlib.Path, project_dir: pathlib.Path) -> None:
    """A registered project with one file inside -- what remove-project.sh
    deletes and unregisters. The file makes the directory's removal visible
    in a file-level effect snapshot."""
    adopted = root / "projects" / ADOPTED_PROJECT
    adopted.mkdir(parents=True, exist_ok=True)
    (adopted / "README.md").write_text("parity fixture project\n", encoding="utf-8")
    with (root / "projects.cfg").open("a", encoding="utf-8") as fh:
        fh.write(f"[project:{ADOPTED_PROJECT}]\npriority = 2\nenabled = true\n\n")


def seed_registered_project_with_active_rc(root: pathlib.Path, project_dir: pathlib.Path) -> None:
    """A registered project whose release-state.md records an RC in flight --
    what remove-project refuses with or without --force."""
    seed_registered_project_dir(root, project_dir)
    (root / "projects" / ADOPTED_PROJECT / "release-state.md").write_text(
        f"# Release State\n\n## Active RC\n{PARITY_UNTIL_VERSION}\n", encoding="utf-8"
    )


def _dead_pid() -> int:
    """The pid of a process that has already exited, so a lock naming it is stale."""
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


def seed_stale_lock(root: pathlib.Path, project_dir: pathlib.Path) -> None:
    """A lock file whose recorded holder pid is dead -- what release-locks.sh
    --release releases without --force. Shape: lock_provenance.sh's four
    provenance fields, ``*-<project>.lock`` naming."""
    locks = root / "locks"
    locks.mkdir(parents=True, exist_ok=True)
    (locks / f"{STALE_LOCK_NAME}.lock").write_text(
        f"holder=parity-fixture\npid={_dead_pid()}\nop=wake\ntask=none\n",
        encoding="utf-8",
    )


def seed_empty_locks_dir(root: pathlib.Path, project_dir: pathlib.Path) -> None:
    """An empty locks/ directory, so release-locks.sh's not-found refusal
    names a lock that is missing rather than a directory that is."""
    (root / "locks").mkdir(parents=True, exist_ok=True)


def seed_intake_source(root: pathlib.Path, project_dir: pathlib.Path) -> None:
    """The source file intake.sh deposits, kept OUTSIDE the project tree (a
    board-root ``inbox/`` directory) so it is not itself part of the effect."""
    inbox = root / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / INTAKE_FILENAME).write_text(INTAKE_CONTENT, encoding="utf-8")


def intake_source_path(root: pathlib.Path) -> pathlib.Path:
    return root / "inbox" / INTAKE_FILENAME


# ---------------------------------------------------------------------------
# argv builders
# ---------------------------------------------------------------------------


def _args(*argv: str) -> CliArgsFn:
    """A board-independent argv."""
    return lambda root: list(argv)


_TASKS = task_ids_by_state()
_P = PROJECT_NAME
_GOLDEN_PROJECT_REL = f"projects/{_P}"


# ---------------------------------------------------------------------------
# The ledger
# ---------------------------------------------------------------------------

ACTION_PARITY_CASES: tuple = (
    # --- HALT family --------------------------------------------------------
    EffectParityCase(
        verb="halt", script="halt.sh",
        cli_args=_args("--project", _P),
        rest_route="/operations/halt", rest_body={"project": _P},
        expect_changed=(f"{_GOLDEN_PROJECT_REL}/HALT",),
    ),
    EffectParityCase(
        verb="unhalt", script="unhalt.sh",
        cli_args=_args("--project", _P),
        rest_route="/operations/unhalt", rest_body={"project": _P},
        expect_changed=(f"{_GOLDEN_PROJECT_REL}/HALT",),
        seed=seed_project_halt,
    ),
    RefusalParityCase(
        verb="halt", script="halt.sh",
        cli_args=_args("--project", GHOST_PROJECT),
        rest_route="/operations/halt",
        rest_body={"project": GHOST_PROJECT},
        error_key="project_resolve.not_found",
        note="project resolution is the script's first refusal; the route lifts it to top-level keys",
    ),
    RefusalParityCase(
        verb="unhalt", script="unhalt.sh",
        cli_args=_args("--project", GHOST_PROJECT),
        rest_route="/operations/unhalt",
        rest_body={"project": GHOST_PROJECT},
        error_key="project_resolve.not_found",
        note="project resolution is the script's first refusal; the route lifts it to top-level keys",
    ),
    EffectParityCase(
        verb="halt-global", script="halt-global.sh",
        cli_args=_args(),
        rest_route="/operations/halt-global", rest_body={},
        expect_changed=("HALT",),
    ),
    EffectParityCase(
        verb="unhalt-global", script="unhalt-global.sh",
        cli_args=_args(),
        rest_route="/operations/unhalt-global", rest_body={},
        expect_changed=("HALT",),
        seed=seed_root_halt,
    ),
    # The requirements document's named case: REST halt-after with an until
    # version arms byte-identical sentinel content to the CLI's --until.
    EffectParityCase(
        verb="halt-after", script="halt-after.sh",
        cli_args=_args("--project", _P, "--key", "rc", "--until", PARITY_UNTIL_VERSION),
        rest_route="/operations/halt-after",
        rest_body={"project": _P, "key": "rc", "until": PARITY_UNTIL_VERSION},
        expect_changed=(f"{_GOLDEN_PROJECT_REL}/HALT-AFTER",),
    ),
    RefusalParityCase(
        verb="halt-after", script="halt-after.sh",
        cli_args=_args("--project", _P, "--key", "rc", "--until", MALFORMED_VERSION),
        rest_route="/operations/halt-after",
        rest_body={"project": _P, "key": "rc", "until": MALFORMED_VERSION},
        error_key="halt-after.until_malformed",
        note="a malformed --until is refused at write time, before any sentinel is written",
    ),
    # The CLI's minimal form: --key is optional on the command line (it
    # defaults to rc), so the REST body must arm the same sentinel with the
    # same two values and no key -- the route omits --key from the argv and
    # lets the script apply its own default.
    EffectParityCase(
        verb="halt-after", script="halt-after.sh",
        cli_args=_args("--project", _P, "--until", PARITY_UNTIL_VERSION),
        rest_route="/operations/halt-after",
        rest_body={"project": _P, "until": PARITY_UNTIL_VERSION},
        expect_changed=(f"{_GOLDEN_PROJECT_REL}/HALT-AFTER",),
    ),
    RefusalParityCase(
        verb="halt-after", script="halt-after.sh",
        cli_args=_args("--project", _P, "--until", MALFORMED_VERSION),
        rest_route="/operations/halt-after",
        rest_body={"project": _P, "until": MALFORMED_VERSION},
        error_key="halt-after.until_malformed",
        note=(
            "the CLI's minimal form with a malformed --until and no --key must reach "
            "the script's own write-time refusal over REST too, not a framework 422"
        ),
    ),
    # --- Deferral posture ---------------------------------------------------
    EffectParityCase(
        verb="defer", script="defer.sh",
        cli_args=_args("--project", _P, "--until", PARITY_UNTIL_VERSION),
        rest_route="/operations/defer",
        rest_body={"project": _P, "until": PARITY_UNTIL_VERSION},
        expect_changed=(f"{_GOLDEN_PROJECT_REL}/DEFER-LOW-BUGS",),
    ),
    RefusalParityCase(
        verb="defer", script="defer.sh",
        cli_args=_args("--project", _P, "--until", MALFORMED_VERSION),
        rest_route="/operations/defer",
        rest_body={"project": _P, "until": MALFORMED_VERSION},
        error_key="defer.until_malformed",
        note="defer.sh's write-time --until check; the route forwards the value verbatim",
    ),
    EffectParityCase(
        verb="undefer", script="undefer.sh",
        cli_args=_args("--project", _P),
        rest_route="/operations/undefer", rest_body={"project": _P},
        expect_changed=(f"{_GOLDEN_PROJECT_REL}/DEFER-LOW-BUGS",),
    ),
    RefusalParityCase(
        verb="undefer", script="undefer.sh",
        cli_args=_args("--project", GHOST_PROJECT),
        rest_route="/operations/undefer",
        rest_body={"project": GHOST_PROJECT},
        error_key="project_resolve.not_found",
        note="project resolution is the script's first refusal; the route lifts it to top-level keys",
    ),
    # --- Long-running / detached lanes: refusal only ------------------------
    RefusalParityCase(
        verb="deep-verify", script="deep-verify.sh",
        cli_args=_args("--project", GHOST_PROJECT),
        rest_route="/operations/deep-verify", rest_body={"project": GHOST_PROJECT},
        error_key="project_resolve.not_found",
        channel="detached-log",
        note=(
            "the route dispatches deep-verify.sh detached and returns 200 as soon as "
            "the child starts; the script's refusal is observable only in the log file "
            "the response names, which is where this case reads it"
        ),
    ),
    RefusalParityCase(
        verb="cold-build-now", script="cold-build-now.sh",
        cli_args=_args("--project", GHOST_PROJECT),
        rest_route="/operations/cold-build-now", rest_body={"project": GHOST_PROJECT},
        error_key="project_resolve.not_found",
        channel="detached-log",
        note="a named alias for deep-verify.sh --project; same detached dispatch, same log channel",
    ),
    RefusalParityCase(
        verb="wake-now", script="wake-now.sh",
        cli_args=_args("--agent", "no-such-agent"),
        rest_route="/operations/wake-now", rest_body={"agent": "no-such-agent"},
        error_key="wake_now.unknown_agent",
        note="firing a real agent wake is not a fixture-sized effect; the agent-name refusal is",
    ),
    # --- Locks --------------------------------------------------------------
    EffectParityCase(
        verb="release-locks", script="release-locks.sh",
        cli_args=_args("--release", STALE_LOCK_NAME),
        rest_route="/operations/release-locks", rest_body={"release": STALE_LOCK_NAME},
        expect_changed=(f"locks/{STALE_LOCK_NAME}.lock",),
        seed=seed_stale_lock,
    ),
    RefusalParityCase(
        verb="release-locks", script="release-locks.sh",
        cli_args=_args("--release", "no-such-lock"),
        rest_route="/operations/release-locks", rest_body={"release": "no-such-lock"},
        error_key="release_locks.not_found",
        seed=seed_empty_locks_dir,
    ),
    # --- Release mechanics (need a git checkout for any effect): refusal ----
    RefusalParityCase(
        verb="unwind-rc", script="unwind-rc.sh",
        cli_args=_args("--project", _P, "--key", "banana", "--confirm"),
        rest_route="/operations/unwind-rc",
        # The route's confirm gate must be satisfied byte-for-byte or the
        # request never reaches the script (post_unwind_rc); --confirm is the
        # flag the route passes once it is.
        rest_body={"project": _P, "key": "banana", "confirm": f"unwind-rc {_P} banana"},
        error_key=None,
        seed=seed_project_cfg,
        note="unwind-rc.sh refuses a malformed version with plain stderr text (no dual-channel block)",
    ),
    RefusalParityCase(
        verb="ship-rc", script="ship-rc.sh",
        cli_args=_args("--project", _P, "--key", "banana"),
        rest_route="/operations/ship-rc",
        # The route's confirm gate must be satisfied byte-for-byte or the
        # request never reaches the script (post_ship_rc).
        rest_body={"project": _P, "key": "banana", "confirm": f"ship-rc {_P} banana"},
        error_key=None,
        note="ship-rc.sh refuses a malformed version with plain stderr text (no dual-channel block)",
    ),
    RefusalParityCase(
        verb="cancel-rc", script="cm/cancel-rc.sh",
        cli_args=_args("--project", _P, "--key", "banana", "--yes"),
        rest_route="/operations/cancel-rc",
        rest_body={"project": _P, "key": "banana", "yes": True},
        error_key=None,
        seed=seed_project_cfg,
        note=(
            "cm/cancel-rc.sh refuses with plain stderr; its twin lifts that text to "
            "'message' under the route's own fallback key, which the CLI never prints"
        ),
    ),
    # --- Task and intake items ---------------------------------------------
    EffectParityCase(
        verb="reset", script="reset.sh",
        cli_args=_args("--project", _P, "--key", _TASKS["BLOCKED"]),
        rest_route="/operations/reset", rest_body={"project": _P, "key": _TASKS["BLOCKED"]},
        expect_changed=(f"{_GOLDEN_PROJECT_REL}/tasks/{_TASKS['BLOCKED']}/status.md",),
    ),
    EffectParityCase(
        verb="close", script="close.sh",
        cli_args=_args("--project", _P, "--key", _TASKS["WORKING"]),
        rest_route="/operations/close", rest_body={"project": _P, "key": _TASKS["WORKING"]},
        expect_changed=(f"{_GOLDEN_PROJECT_REL}/tasks/{_TASKS['WORKING']}/status.md",),
    ),
    EffectParityCase(
        verb="wontdo", script="wontdo.sh",
        cli_args=_args("--project", _P, "--key", _TASKS["BACKLOG"]),
        rest_route="/operations/wontdo", rest_body={"project": _P, "key": _TASKS["BACKLOG"]},
        expect_changed=(f"{_GOLDEN_PROJECT_REL}/tasks/{_TASKS['BACKLOG']}/status.md",),
    ),
    EffectParityCase(
        verb="delete", script="delete.sh",
        cli_args=_args("--project", _P, "--key", _TASKS["WONT-DO"], "--force"),
        rest_route="/operations/delete",
        rest_body={"project": _P, "key": _TASKS["WONT-DO"], "force": True},
        expect_changed=(f"{_GOLDEN_PROJECT_REL}/tasks/{_TASKS['WONT-DO']}/status.md",),
    ),
    EffectParityCase(
        verb="intake", script="intake.sh",
        cli_args=lambda root: ["--project", _P, str(intake_source_path(root))],
        rest_route="/operations/intake",
        rest_body={"project": _P, "filename": INTAKE_FILENAME, "content": INTAKE_CONTENT},
        expect_changed=(f"{_GOLDEN_PROJECT_REL}/bugs/{INTAKE_FILENAME}",),
        seed=seed_intake_source,
    ),
    RefusalParityCase(
        verb="reset", script="reset.sh",
        cli_args=_args("--project", GHOST_PROJECT, "--key", _TASKS["BLOCKED"]),
        rest_route="/operations/reset",
        rest_body={"project": GHOST_PROJECT, "key": _TASKS["BLOCKED"]},
        error_key="project_resolve.not_found",
        note="project resolution is the script's first refusal; the route lifts it to top-level keys",
    ),
    RefusalParityCase(
        verb="close", script="close.sh",
        cli_args=_args("--project", GHOST_PROJECT, "--key", _TASKS["WORKING"]),
        rest_route="/operations/close",
        rest_body={"project": GHOST_PROJECT, "key": _TASKS["WORKING"]},
        error_key="project_resolve.not_found",
        note="project resolution is the script's first refusal; the route lifts it to top-level keys",
    ),
    RefusalParityCase(
        verb="wontdo", script="wontdo.sh",
        cli_args=_args("--project", GHOST_PROJECT, "--key", _TASKS["BACKLOG"]),
        rest_route="/operations/wontdo",
        rest_body={"project": GHOST_PROJECT, "key": _TASKS["BACKLOG"]},
        error_key="project_resolve.not_found",
        note="project resolution is the script's first refusal; the route lifts it to top-level keys",
    ),
    RefusalParityCase(
        verb="delete", script="delete.sh",
        cli_args=_args("--project", GHOST_PROJECT, "--key", _TASKS["WONT-DO"], "--force"),
        rest_route="/operations/delete",
        rest_body={"project": GHOST_PROJECT, "key": _TASKS["WONT-DO"], "force": True},
        error_key="project_resolve.not_found",
        note="project resolution is the script's first refusal; the route lifts it to top-level keys",
    ),
    RefusalParityCase(
        verb="intake", script="intake.sh",
        cli_args=lambda root: ["--project", GHOST_PROJECT, str(intake_source_path(root))],
        rest_route="/operations/intake",
        rest_body={"project": GHOST_PROJECT, "filename": INTAKE_FILENAME, "content": INTAKE_CONTENT},
        error_key="project_resolve.not_found",
        seed=seed_intake_source,
        note="project resolution is the script's first refusal; the route lifts it to top-level keys",
    ),
    # --- Configuration ------------------------------------------------------
    EffectParityCase(
        verb="set-version-ceiling", script="set-version-ceiling.sh",
        cli_args=_args("--project", _P, "--minor", "5"),
        rest_route="/operations/set-version-ceiling", rest_body={"project": _P, "minor": 5},
        expect_changed=(f"{_GOLDEN_PROJECT_REL}/project.cfg",),
        seed=seed_project_cfg,
    ),
    RefusalParityCase(
        verb="set-version-ceiling", script="set-version-ceiling.sh",
        cli_args=_args("--project", GHOST_PROJECT, "--minor", "5"),
        rest_route="/operations/set-version-ceiling",
        rest_body={"project": GHOST_PROJECT, "minor": 5},
        error_key="project_resolve.not_found",
        note="project resolution is the script's first refusal; the route lifts it to top-level keys",
    ),
    EffectParityCase(
        verb="switch-provider", script="switch-provider.sh",
        cli_args=_args("--provider", "claude"),
        rest_route="/operations/switch-provider", rest_body={"provider": "claude"},
        expect_changed=("kanban.cfg",),
        seed=seed_kanban_cfg_active_codex,
    ),
    # --- Project registry ---------------------------------------------------
    EffectParityCase(
        verb="create-project", script="create-project.sh",
        cli_args=_args("--project", CREATED_PROJECT),
        rest_route="/operations/create-project", rest_body={"project": CREATED_PROJECT},
        expect_changed=("projects.cfg", f"projects/{CREATED_PROJECT}/project.cfg"),
    ),
    EffectParityCase(
        verb="add-project", script="add-project.sh",
        cli_args=_args("--project", ADOPTED_PROJECT),
        rest_route="/operations/add-project", rest_body={"project": ADOPTED_PROJECT},
        expect_changed=("projects.cfg",),
        seed=seed_unregistered_project_dir,
    ),
    RefusalParityCase(
        verb="create-project", script="create-project.sh",
        # -1 is malformed on both surfaces (the body field is an int).
        cli_args=_args("--project", CREATED_PROJECT, "--max-minor", "-1"),
        rest_route="/operations/create-project",
        rest_body={"project": CREATED_PROJECT, "max_minor": -1},
        error_key=None,
        note="create_project refuses a ceiling that is not a non-negative integer with a plain ERROR: line",
    ),
    RefusalParityCase(
        verb="add-project", script="add-project.sh",
        cli_args=_args("--project", ADOPTED_PROJECT),
        rest_route="/operations/add-project", rest_body={"project": ADOPTED_PROJECT},
        error_key=None,
        note="add_project refuses a project with no directory (exit 2) with plain ERROR: lines",
    ),
    EffectParityCase(
        verb="remove-project", script="remove-project.sh",
        cli_args=_args("--project", ADOPTED_PROJECT),
        rest_route="/operations/remove-project", rest_body={"project": ADOPTED_PROJECT},
        # The default mode unregisters only: projects.cfg changes, the
        # project's README.md stays (it is absent from the effect).
        expect_changed=("projects.cfg",),
        seed=seed_registered_project_dir,
    ),
    RefusalParityCase(
        verb="remove-project", script="remove-project.sh",
        cli_args=_args("--project", ADOPTED_PROJECT),
        rest_route="/operations/remove-project", rest_body={"project": ADOPTED_PROJECT},
        error_key="remove_project.active_rc",
        seed=seed_registered_project_with_active_rc,
        note="the Active-RC guard refuses the unregister-only mode",
    ),
    RefusalParityCase(
        verb="remove-project", script="remove-project.sh",
        cli_args=_args("--project", ADOPTED_PROJECT, "--force"),
        rest_route="/operations/remove-project",
        rest_body={
            "project": ADOPTED_PROJECT,
            "force": True,
            "confirm": f"remove-project {ADOPTED_PROJECT}",
        },
        error_key="remove_project.active_rc",
        seed=seed_registered_project_with_active_rc,
        note="the Active-RC guard holds with --force / a confirmed force: true",
    ),
    EffectParityCase(
        verb="remove-project", script="remove-project.sh",
        cli_args=_args("--project", ADOPTED_PROJECT, "--force"),
        rest_route="/operations/remove-project",
        # force is the destructive escalation and must be confirmed byte-for-byte.
        rest_body={
            "project": ADOPTED_PROJECT,
            "force": True,
            "confirm": f"remove-project {ADOPTED_PROJECT}",
        },
        expect_changed=("projects.cfg", f"projects/{ADOPTED_PROJECT}/README.md"),
        seed=seed_registered_project_dir,
    ),
    # --- Archive ------------------------------------------------------------
    # --count 1 keeps the newest task (WONT-DO) live; of the older five only
    # the DONE task is finished, so exactly it moves, with its ledger row.
    EffectParityCase(
        verb="archive", script="archive.sh",
        cli_args=_args("--project", _P, "--count", "1"),
        rest_route="/operations/archive", rest_body={"project": _P, "count": 1},
        expect_changed=(
            f"{_GOLDEN_PROJECT_REL}/tasks/{_TASKS['DONE']}/status.md",
            f"archive/{_P}/tasks/{_TASKS['DONE']}/status.md",
            f"archive/{_P}/ARCHIVE-LEDGER.tsv",
        ),
    ),
    RefusalParityCase(
        verb="archive", script="archive.sh",
        cli_args=_args("--project", GHOST_PROJECT, "--dry-run"),
        rest_route="/operations/archive",
        rest_body={"project": GHOST_PROJECT, "dry_run": True},
        error_key="project_resolve.not_found",
        note="run_archive resolves the project first on both surfaces; the route lifts the block to top-level keys",
    ),
)


def case_id(case) -> str:
    """The pytest id for a case: ``<verb>[<kind>]``, stable across runs."""
    return f"{case.verb}[{case.kind}]"


def verbs_with_a_case() -> frozenset[str]:
    """Every verb the ledger names at least once -- what the parity gate reads."""
    return frozenset(case.verb for case in ACTION_PARITY_CASES)
