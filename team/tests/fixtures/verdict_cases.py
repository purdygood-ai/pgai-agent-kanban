"""
verdict_cases.py
================
Fixture boards and scenario runner for the lane-verdict verb: ``why.sh``,
``verdict-report.sh`` and ``GET /verdicts``, all three answered by
``pgai_agent_kanban.ops.verdict.resolve_verdicts``.

WHAT A SCENARIO IS
------------------
A builder that lays out a throwaway kanban root (projects.cfg, project
directories, task status files, locks, queues, requirements bundles, HALT /
HALT-AFTER / DEFER-LOW-BUGS sentinels, release-state.md) and a list of
commands run against it. A command is either a CLI run (``why`` or
``report``, with arguments) or a REST request (``rest``, with query
parameters) sent through an in-process ``TestClient``.

:func:`run_scenario` builds the board, runs every command and returns one
normalised outcome per command: exit code, stdout and stderr for a CLI run;
HTTP status and the raw response body for a REST request. The golden file
``tests/fixtures/golden/verdicts_cli.json`` holds those outcomes as the
scripts and the route produced them before the verdict logic moved to
Python; ``tests/unit/test_ops_verdict.py`` replays every scenario and
compares.

DETERMINISM
-----------
- ``VERDICT_NOW_EPOCH`` pins "now" for the verdict itself (task time in
  state, the STUCK-LOCK lock age); every board's timestamps are laid out
  relative to :data:`NOW`.
- The ``--details`` lock readings use the wall clock, as they always have:
  ``age_seconds`` and ``held_seconds`` numbers are replaced by ``<N>``, and
  the ``acquired_at`` of a lock held "three hours" is stamped at build time
  (token ``AGO``) so the rendered ``3h 0m`` is stable.
- The board root, this process's pid (a live lock holder, token ``LIVE``)
  and the ``AGO`` stamp are replaced by placeholders.

Every CLI run gets an explicit, minimal environment (``build_scrubbed_env``);
every REST request runs with the kanban root and ``VERDICT_NOW_EPOCH`` set
in this process's environment for the duration of the request only.
"""

from __future__ import annotations

import contextlib
import datetime
import os
import pathlib
import re
import subprocess
import sys
from typing import Callable, Iterator, Optional

_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env  # noqa: E402

WHY_SCRIPT = _TEAM_DIR / "scripts" / "why.sh"
REPORT_SCRIPT = _TEAM_DIR / "scripts" / "verdict-report.sh"
GOLDEN_FILE = _TEAM_DIR / "tests" / "fixtures" / "golden" / "verdicts_cli.json"

#: The pinned "now" (VERDICT_NOW_EPOCH) every board is laid out against.
NOW = 2_000_000_000
#: A pid above the kernel's pid_max ceiling: never answers kill -0.
DEAD_PID = "999999999"

_SCRIPTS = {"why": WHY_SCRIPT, "report": REPORT_SCRIPT}


# ---------------------------------------------------------------------------
# Board building
# ---------------------------------------------------------------------------


def stamp(epoch: float) -> str:
    """ISO-8601 UTC ``YYYY-MM-DDThh:mm:ssZ`` for *epoch*."""
    return datetime.datetime.fromtimestamp(int(epoch), datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write(path: pathlib.Path, text: str, mtime: Optional[int] = None) -> pathlib.Path:
    """Write *text* (UTF-8, no newline translation) at *path*, optionally setting its mtime."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def projects_cfg(root: pathlib.Path, entries: list) -> None:
    """Register projects: each entry is ``(name, priority, enabled)``; enabled None omits the key."""
    lines = ["# Format: INI-style", ""]
    for name, priority, enabled in entries:
        lines.append(f"[project:{name}]")
        lines.append(f"priority={priority}")
        if enabled is not None:
            lines.append(f"enabled={'true' if enabled else 'false'}")
        lines.append("")
    write(root / "projects.cfg", "\n".join(lines))


def project(root: pathlib.Path, name: str) -> pathlib.Path:
    path = root / "projects" / name
    (path / "tasks").mkdir(parents=True, exist_ok=True)
    return path


def task(proj: pathlib.Path, task_id: str, state: str, mtime: Optional[int] = None, *, body: Optional[str] = None) -> None:
    """A task folder whose status.md carries *state* under ``## State`` (or *body* verbatim)."""
    text = body if body is not None else f"# Status\n\n## State\n{state}\n\n## Summary\nfixture\n"
    write(proj / "tasks" / task_id / "status.md", text, mtime)


def lock(root: pathlib.Path, name: str, record: str, mtime: Optional[int] = None) -> None:
    write(root / "locks" / f"{name}.lock", record, mtime)


def record(holder: str, pid: str, op: str, task_id: str, acquired_at: Optional[str]) -> str:
    text = f"holder={holder}\npid={pid}\nop={op}\ntask={task_id}\n"
    if acquired_at is not None:
        text += f"acquired_at={acquired_at}\n"
    return text


def release_state(proj: pathlib.Path, active_rc: str, extra: str = "") -> None:
    write(
        proj / "release-state.md",
        "# Release State\n\n"
        f"## Active RC\n{active_rc}\n\n"
        "## Last Released\nv0.9.0\n\n"
        "## RC Opened At\n2026-01-01T00:00:00Z\n\n"
        "## RC Opened By Task\nCM-20260101-001-open\n" + extra,
    )


def decision(lane: str, version: str, outcome: str) -> str:
    return (
        "\n## OVERWATCH Decision\n"
        f"Timestamp: 2026-01-02T00:00:00Z\nLane: {lane}\nVersion: {version}\nOutcome: {outcome}\n"
    )


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

Tokens = dict


def _build_walk(root: pathlib.Path, tokens: Tokens) -> None:
    """Every verdict word and its causes, across one registry walk."""
    projects_cfg(
        root,
        [
            ("alpha", 1, True), ("beta", 2, True), ("gamma", 3, False), ("delta", 4, True),
            ("epsilon", 5, None), ("zeta", 6, True), ("eta", 7, True), ("theta", 8, True),
            ("iota", 9, True), ("kappa", 10, True), ("lambda", 11, True), ("mu", 12, True),
            ("nu", 13, True), ("xi", 14, True), ("omicron", 15, True), ("pi", 15, True),
        ],
    )
    alpha = project(root, "alpha")
    task(alpha, "CODER-20260101-001-running", "WORKING", NOW - 300)
    task(alpha, "CODER-20260101-002-queued", "BACKLOG", NOW - 9000)
    task(alpha, "CODER-20260101-003-done", "DONE", NOW - 100)

    beta = project(root, "beta")
    task(beta, "CODER-20260101-011-b", "WORKING", NOW - 7200)
    task(beta, "CODER-20260101-010-a", "WORKING", NOW - 7200)
    task(beta, "CODER-20260101-012-lower", "working", NOW - 60)

    gamma = project(root, "gamma")
    task(gamma, "CODER-20260101-020-busy", "WORKING", NOW - 60)

    # delta is registered with no directory.

    epsilon = project(root, "epsilon")
    task(epsilon, "WRITER-20260101-030-old", "BLOCKED", NOW - 100)
    task(epsilon, "WRITER-20260101-031-new", "BLOCKED", NOW - 50)
    task(epsilon, "WRITER-20260101-032-queued", "BACKLOG", NOW - 50)

    zeta = project(root, "zeta")
    write(zeta / "HALT", "\n  \t\n  # Ship policy halt: regression in v1.0.0\r\nsecond line\n")
    task(zeta, "CODER-20260101-040-working", "WORKING", NOW - 60)

    eta = project(root, "eta")
    write(eta / "HALT", "rc\n")
    write(eta / "overwatch" / "resolving.md", "lane: A\nattempt:   2\nversion: v1.2.3\nstarted_at: 2026-01-01T00:00:00Z\n")
    release_state(eta, "v1.2.3")

    theta = project(root, "theta")
    write(theta / "HALT", "# ship-policy\n")
    release_state(
        theta,
        "v2.0.0",
        decision("B", "v1.9.0", "older decision")
        + decision("C", "v2.0.0", "escalated to a human: " + ("the fidelity gate refused the squash; " * 8)),
    )

    iota = project(root, "iota")
    write(iota / "HALT", "")
    release_state(iota, "v3.0.0", decision("A", "v2.9.9", "recovered"))

    kappa = project(root, "kappa")
    task(kappa, "PM-20260101-050-queued", "BACKLOG", NOW - 9000)
    lock(root, "repo-wake-kappa", record("wake-coder", DEAD_PID, "wake-chain:kappa", "none", stamp(NOW - 40000)), NOW - 30000)

    lam = project(root, "lambda")
    task(lam, "PM-20260101-060-waiting", "WAITING", NOW - 9000)
    lock(root, "overwatch-lambda", "", NOW - 10)

    mu = project(root, "mu")
    task(mu, "PM-20260101-070-queued", "BACKLOG", NOW - 9000)
    lock(root, "repo-wake-mu", record("wake-pm", tokens["LIVE"], "wake-chain:mu", "none", stamp(NOW - 30)), NOW - 10)

    nu = project(root, "nu")
    task(nu, "CODER-20260101-080-done", "DONE", NOW - 10)
    task(nu, "CODER-20260101-081-wontdo", "WONT-DO", NOW - 10)
    task(nu, "archive", "WORKING", NOW - 10)
    write(nu / "tasks" / "queues" / "status.md", "## State\nWORKING\n")

    xi = project(root, "xi")
    task(xi, "CM-20260101-090-queued", "BACKLOG", NOW - 9000)
    lock(root, "repo-wake-xi", record("wake-cm", tokens["LIVE"], "wake-chain:xi", "CM-9", stamp(NOW - 26000)), NOW - 25000)

    omicron = project(root, "omicron")
    task(omicron, "T-crlf", "", NOW - 10, body="## State\r\nWORKING\r\n")
    task(omicron, "T-spaced", "", NOW - 20, body="## Summary\nx\n## State\n\n   BLOCKED   \n## Notes\n")
    task(omicron, "T-empty-state", "", NOW - 30, body="## State\n\n## Summary\nnothing\n")
    task(omicron, "T-quoted", "", NOW - 40, body="## State\nDONE's\n")

    pi = project(root, "pi")
    task(pi, "T-states-heading", "", NOW - 120, body="## States\nWORKING\n")
    write(pi / "tasks" / "status.md", "## State\nBLOCKED\n", NOW - 5)


def _build_details_deferred(root: pathlib.Path, tokens: Tokens) -> None:
    """Deferred by a campaign file, an Active RC set, HALT-AFTER pinned, every detail populated."""
    projects_cfg(root, [("alpha", 1, True), ("bystander", 2, True)])
    alpha = project(root, "alpha")
    project(root, "bystander")
    write(alpha / "project.cfg", "[project]\nname = alpha\n\n[locks]\nstale_lock_reap_after_wake_periods = 200\n")
    write(alpha / "DEFER-LOW-BUGS", "# armed by defer.sh\nuntil=v9.9.9\n")
    release_state(alpha, "v1.2.3")
    write(alpha / "HALT-AFTER", "rc:v1.2.3\n")
    task(alpha, "CODER-1", "WORKING", NOW - 60)
    task(alpha, "CODER-2", "DONE", NOW - 60)
    task(alpha, "CODER-3", "", NOW - 60, body="## State\n\n## Summary\n")
    task(alpha, "PM-1", "BACKLOG", NOW - 60)
    lock(root, "repo-wake-alpha", record("wake-coder", tokens["LIVE"], "wake-chain:alpha", "none", tokens["AGO"]))
    lock(root, "overwatch-alpha", record("overwatch", DEAD_PID, "sweep", "OVERWATCH-1", None))
    lock(root, "stray-alpha", "")
    lock(root, "repo-wake-bystander", record("wake-pm", DEAD_PID, "wake-chain:bystander", "none", None))
    write(
        alpha / "tasks" / "queues" / "coder_backlog.md",
        "# Coder backlog\n\n## Queue\n"
        "- [x] CODER-2 — done\n"
        "- [A] CODER-1 — working\n"
        "-[ ] CODER-3 blank state\n"
        "  -  [W]   PM-1   spaced\n"
        "- [B] CODER-404 missing\n"
        "- [?] CODER-2\n"
        "- [x]CODER-9 no space, not an entry\n"
        "- [ ] CODER-1 trailing\n",
    )
    write(alpha / "tasks" / "queues" / "pm_backlog.md", "# PM backlog\n\n## Queue\n")
    write(alpha / "tasks" / "queues" / "writer_backlog.md", "- [ ] WRITER-1\n")
    write(alpha / "tasks" / "queues" / "notes.md", "- [ ] NOT-A-QUEUE\n")
    write(alpha / "requirements" / "v1.0.0-bugfix-bundle-20260101.md", "# Bundle\n\n## Status\nopen\n")
    write(alpha / "requirements" / "v1.0.1-priority-bundle-20260102.md", "# Bundle\n\n## Status\nDone\n")
    write(alpha / "requirements" / "v1.0.2-bugfix-bundle-20260103.md", "# Bundle without status\n")
    write(alpha / "requirements" / "v1.0.3-priority-bundle-20260104.md", "## Status\n**running**\n")
    write(alpha / "requirements" / "v2.0.0-feature.md", "## Status\nopen\n")


def _build_details_plain(root: pathlib.Path, tokens: Tokens) -> None:
    """Not deferred (project.cfg says so), Active RC none, HALTed with an event, nothing else."""
    projects_cfg(root, [("beta", 1, True)])
    beta = project(root, "beta")
    write(beta / "project.cfg", "[release]\ndeferred_bugs = false\n")
    release_state(beta, "none")
    write(beta / "HALT", "coder\n")


def _build_details_kanban_tier(root: pathlib.Path, tokens: Tokens) -> None:
    """Deferred from kanban.cfg, no release-state.md, an empty queues directory, a role HALT-AFTER."""
    projects_cfg(root, [("gamma", 1, True)])
    write(root / "kanban.cfg", "[release]\ndeferred_bugs = true\ndeferred_bug_level = medium  # mid\ndefer_piggyback = false\n")
    gamma = project(root, "gamma")
    (gamma / "tasks" / "queues").mkdir(parents=True)
    write(gamma / "HALT-AFTER", "pm\n")
    task(gamma, "WRITER-1", "BLOCKED", NOW - 60)
    lock(root, "repo-wake-gamma", record("wake-writer", DEAD_PID, "wake-chain:gamma", "WRITER-1", tokens["AGO"]), NOW - 60)


def _build_details_root_campaign(root: pathlib.Path, tokens: Tokens) -> None:
    """A root-scoped campaign file and a malformed Active RC; queued work beside a stale lock."""
    projects_cfg(root, [("delta", 1, True)])
    write(root / "DEFER-LOW-BUGS", "until = v3.1.4  \n")
    delta = project(root, "delta")
    write(delta / "release-state.md", "## Active RC\n  2.0  \n")
    task(delta, "PM-1", "WAITING", NOW - 60)
    lock(root, "repo-wake-delta", record("wake-pm", DEAD_PID, "wake-chain:delta", "none", stamp(NOW - 50000)), NOW - 40000)


def _build_details_malformed_campaign(root: pathlib.Path, tokens: Tokens) -> None:
    """A campaign file with no until= line: the posture refuses to resolve."""
    projects_cfg(root, [("epsilon", 1, True)])
    eps = project(root, "epsilon")
    write(eps / "DEFER-LOW-BUGS", "armed without a target\n")
    task(eps, "CODER-1", "BLOCKED", NOW - 60)
    write(eps / "tasks" / "queues" / "coder_backlog.md", "- [B] CODER-1\n")


def _build_details_invalid_level(root: pathlib.Path, tokens: Tokens) -> None:
    """deferred_bug_level=critical in project.cfg: the posture refuses to resolve."""
    projects_cfg(root, [("zeta", 1, True)])
    zeta = project(root, "zeta")
    write(zeta / "project.cfg", "[release]\ndeferred_bugs = true\ndeferred_bug_level = critical\n")


def _build_empty_registry(root: pathlib.Path, tokens: Tokens) -> None:
    write(root / "projects.cfg", "# Format: INI-style\n# nothing registered yet\n")


def _build_no_registry(root: pathlib.Path, tokens: Tokens) -> None:
    (root / "projects").mkdir(parents=True)


#: name -> (builder, commands). A command is ("why"|"report", [args]) or ("rest", {params}).
SCENARIOS: dict[str, tuple[Callable[[pathlib.Path, Tokens], None], list]] = {
    "walk": (
        _build_walk,
        [
            ("why", []),
            ("report", []),
            ("rest", {}),
            ("why", ["--project", "alpha"]),
            ("why", ["--project", "beta"]),
            ("why", ["--project", "gamma"]),
            ("why", ["--project", "epsilon"]),
            ("why", ["--project", "zeta"]),
            ("why", ["--project", "eta"]),
            ("why", ["--project", "theta"]),
            ("why", ["--project", "iota"]),
            ("why", ["--project", "kappa"]),
            ("why", ["--project", "lambda"]),
            ("why", ["--project", "mu"]),
            ("why", ["--project", "nu"]),
            ("why", ["--project", "xi"]),
            ("why", ["--project", "omicron"]),
            ("why", ["--project", "pi"]),
            ("why", ["--project", "ome"]),
            ("why", ["--project", "delta"]),
            ("why", ["--project", "nothere"]),
            ("why", ["--details"]),
            ("report", ["--project", "kappa"]),
            ("report", ["--project", "delta"]),
            ("report", ["--project", "delta", "--details"]),
            ("report", ["--project", "nothere"]),
            ("report", ["--details"]),
            ("rest", {"project": "xi"}),
            ("rest", {"project": "delta"}),
            ("rest", {"project": "delta", "details": "true"}),
            ("rest", {"project": "nothere"}),
            ("rest", {"details": "true"}),
        ],
    ),
    "details-deferred": (
        _build_details_deferred,
        [
            ("why", ["--project", "alpha", "--details"]),
            ("report", ["--project", "alpha", "--details"]),
            ("rest", {"project": "alpha", "details": "true"}),
            ("why", []),
            ("report", []),
        ],
    ),
    "details-plain": (
        _build_details_plain,
        [
            ("why", ["--project", "beta", "--details"]),
            ("report", ["--project", "beta", "--details"]),
            ("rest", {"project": "beta", "details": "true"}),
        ],
    ),
    "details-kanban-tier": (
        _build_details_kanban_tier,
        [
            ("why", ["--project", "gamma", "--details"]),
            ("report", ["--project", "gamma", "--details"]),
            ("rest", {"project": "gamma", "details": "true"}),
        ],
    ),
    "details-root-campaign": (
        _build_details_root_campaign,
        [
            ("why", ["--project", "delta", "--details"]),
            ("report", ["--project", "delta", "--details"]),
            ("rest", {"project": "delta", "details": "true"}),
        ],
    ),
    "details-malformed-campaign": (
        _build_details_malformed_campaign,
        [
            ("why", ["--project", "epsilon"]),
            ("why", ["--project", "epsilon", "--details"]),
            ("report", ["--project", "epsilon", "--details"]),
            ("rest", {"project": "epsilon", "details": "true"}),
        ],
    ),
    "details-invalid-level": (
        _build_details_invalid_level,
        [
            ("why", ["--project", "zeta", "--details"]),
            ("report", ["--project", "zeta", "--details"]),
            ("rest", {"project": "zeta", "details": "true"}),
        ],
    ),
    "empty-registry": (
        _build_empty_registry,
        [("why", []), ("report", []), ("rest", {})],
    ),
    "no-registry": (
        _build_no_registry,
        [("why", []), ("report", []), ("rest", {})],
    ),
    "help": (
        _build_empty_registry,
        [("why", ["--help"]), ("report", ["--help"]), ("why", ["--bogus"]), ("report", ["-h"])],
    ),
}


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------


def cli_env(home: pathlib.Path, root: pathlib.Path) -> dict:
    """The minimal environment every CLI run gets."""
    return build_scrubbed_env(
        home_dir=home,
        extra_env={
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
            "VERDICT_NOW_EPOCH": str(NOW),
        },
    )


def run_cli(home: pathlib.Path, root: pathlib.Path, script: pathlib.Path, args: list) -> subprocess.CompletedProcess:
    """Run *script* on *root*; stdout and stderr decoded with no newline translation."""
    run = subprocess.run(
        ["bash", str(script), *args],
        capture_output=True,
        env=cli_env(home, root),
        cwd=str(root),
        stdin=subprocess.DEVNULL,
        timeout=120,
    )
    run.stdout = run.stdout.decode("utf-8", errors="replace")
    run.stderr = run.stderr.decode("utf-8", errors="replace")
    return run


#: Names a request must not inherit from this process: the ambient project
#: and the [wake] budget overrides the stale-lock threshold reads first.
_REST_CLEARED = ("PGAI_PROJECT_NAME", "MAX_RUNTIME_SECONDS", "MAX_TASK_SECONDS", "KILL_GRACE_SECONDS")


@contextlib.contextmanager
def rest_environment(root: pathlib.Path) -> Iterator[None]:
    """Point this process at *root* (and pin VERDICT_NOW_EPOCH) for one request."""
    names = ("PGAI_AGENT_KANBAN_ROOT_PATH", "KANBAN_ROOT", "VERDICT_NOW_EPOCH", *_REST_CLEARED)
    saved = {name: os.environ.get(name) for name in names}
    os.environ["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(root)
    os.environ["KANBAN_ROOT"] = str(root)
    os.environ["VERDICT_NOW_EPOCH"] = str(NOW)
    for name in _REST_CLEARED:
        os.environ.pop(name, None)
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def api_client(root: pathlib.Path):
    """An in-process client rooted at *root*, the board GET /verdicts reads."""
    from fastapi.testclient import TestClient

    from pgai_agent_kanban.api.app import create_app
    from pgai_agent_kanban.api.config import ApiConfig

    cfg = ApiConfig(host="127.0.0.1", port=0, kanban_root=root)
    return TestClient(create_app(cfg=cfg), base_url="http://127.0.0.1", raise_server_exceptions=True)


_NUMBER_FIELDS_RE = (
    (re.compile(r"(age_seconds|held_seconds)=-?[0-9]+"), r"\1=<N>"),
    (re.compile(r'"(age_seconds|held_seconds)":-?[0-9]+'), r'"\1":<N>'),
)


def normalise(text: str, root: pathlib.Path, tokens: Tokens) -> str:
    """Replace the board root, the token values and the wall-clock lock numbers."""
    text = text.replace(str(root), "<ROOT>")
    for pattern, replacement in _NUMBER_FIELDS_RE:
        text = pattern.sub(replacement, text)
    for token, value in tokens.items():
        text = re.sub(rf"(?<![0-9]){re.escape(value)}(?![0-9])", f"<{token}>", text)
    return text


def command_label(command: tuple) -> str:
    kind, args = command
    if kind == "rest":
        query = "&".join(f"{key}={value}" for key, value in args.items())
        return f"GET /verdicts{'?' + query if query else ''}"
    return " ".join([f"{kind}.sh" if kind == "why" else "verdict-report.sh", *args])


def run_scenario(tmp_path: pathlib.Path, name: str, client=None) -> dict:
    """Build *name*'s board under *tmp_path*, run its commands, return the normalised outcomes."""
    builder, commands = SCENARIOS[name]
    import time

    tokens = {"LIVE": str(os.getpid()), "AGO": stamp(time.time() - 3 * 3600 - 30)}
    root = tmp_path / f"root-{name}"
    root.mkdir(parents=True)
    (root / "tmp").mkdir()
    home = tmp_path / f"home-{name}"
    home.mkdir()
    builder(root, tokens)
    client = client or api_client(root)
    outcomes: dict = {}
    for command in commands:
        kind, args = command
        label = command_label(command)
        if kind == "rest":
            with rest_environment(root):
                response = client.get("/verdicts", params=args)
            outcomes[label] = {"status": response.status_code, "body": normalise(response.text, root, tokens)}
        else:
            run = run_cli(home, root, _SCRIPTS[kind], args)
            outcomes[label] = {
                "rc": run.returncode,
                "stdout": normalise(run.stdout, root, tokens),
                "stderr": normalise(run.stderr, root, tokens),
            }
    return outcomes
