"""
test_cm_cancel_rc.py
====================
Integration tests for the ``cancel-rc`` verb on a sandbox kanban root with a
sandbox git repository: ``team/scripts/cm/cancel-rc.sh`` (a thin wrapper
that reaches ``pgai_agent_kanban.cm.cancel.cancel_rc`` through
``pp_run_ops``) and ``POST /operations/cancel-rc`` (which calls the same
function in process).

Coverage:

  1. Route/CLI parity (tests/fixtures/route_cli_parity.py): the route calls
     ``cm.cancel.cancel_rc`` and starts no process, and the script reaches
     the same function through ``pp_run_ops``.
  2. CLI behaviour against the committed golden
     (tests/fixtures/golden/cancel_rc_cli.json, captured from the script
     before its logic moved to Python). For every recorded scenario -- a
     cancel from the main branch and from the RC branch, a re-run, the
     already-cancelled no-op, a partial recovery, a branch prefix, the
     interactive prompt answered yes / no / not at all, push_to_remote on a
     repository with no origin, every refusal, ``--help`` and the usage
     errors -- stdout, stderr, the exit code, release-state.md, the
     per-RC JSON and its cancelled-RC archive, every task status.md and
     queue file, and the branch list are unchanged.
     The scenarios for a project the shared push-decision resolver cannot
     resolve (``missing-project-cfg``, ``missing-dev-tree-path``,
     ``repo-root-override``) are recorded with the push gate applied: one
     skipped-unresolved-project line, printed before the dev-tree gate.
     ``already-cancelled`` is recorded with its leftover ``in_progress``
     per-RC record closed and archived: a re-run closes a record an earlier,
     part-way run left open.
  3. The REST twin on its own sandbox: for the scenarios an HTTP caller can
     reach (always confirmed), the response's exit code, stdout and stderr
     and every on-disk effect equal the CLI's recorded ones.
  4. The push gate under the git network stub (TestCancelPushGate): no
     project.cfg, an unregistered project, and a project.cfg without
     dev_tree_path -- with REPO_ROOT and PGAI_DEV_TREE_PATH naming the
     repository, or PGAI_DEV_TREE_PATH alone -- make zero git network calls
     and log one skipped-unresolved-project line; a registered project with
     no push_to_remote key still fetches and deletes the RC branch on
     origin; and ``push_to_remote = false`` makes no network call, not even
     the origin branch check.

Every root and repository is a throwaway directory under ``tmp_path``. No
scenario configures an ``origin`` remote: ``git ls-remote origin`` fails
locally, every ``push_to_remote = true`` scenario runs under a non-CM
``PGAI_AGENT_ROLE`` so the fetch is skipped, and the only ``origin/<main>``
ref is a local one written with ``git update-ref``. The push-gate tests put
the git network stub ahead of git on PATH, so their network verbs are
logged and reach nothing. Nothing reaches a network.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
from typing import Optional

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from tests.fixtures.binary_guard import require_binary  # noqa: E402
from tests.fixtures.git_network_stub import install_git_network_stub  # noqa: E402
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env  # noqa: E402

_SCRIPT = _TEAM_DIR / "scripts" / "cm" / "cancel-rc.sh"
_CM_PY_SRC = _TEAM_DIR / "pgai_agent_kanban" / "cm"
_GOLDEN_FILE = _TEAM_DIR / "tests" / "fixtures" / "golden" / "cancel_rc_cli.json"

_PROJECT = "cancel-fixture"
_VERSION = "v0.4.0"
_TIMEOUT_SECONDS = 120

# Fixed commit identity and dates, so a fixture repository is the same on
# every run.
_GIT_ENV = {
    "GIT_AUTHOR_NAME": "PGAI Test",
    "GIT_AUTHOR_EMAIL": "test@pgai.local",
    "GIT_COMMITTER_NAME": "PGAI Test",
    "GIT_COMMITTER_EMAIL": "test@pgai.local",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+00:00",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+00:00",
}

_RELEASE_STATE = (
    "# Release State\n\n"
    "## Active RC\n{active}\n\n"
    "## RC Opened At\n{opened_at}\n\n"
    "## RC Opened By Task\n{opened_by}\n\n"
    "## Last Released\nv0.3.2\n\n"
    "## HALT Event\n"
    "- at: 2026-01-02T03:04:05+00:00\n"
    "- reason: an appended block every rewrite keeps\n"
)

_JSON_RECORD = (
    '{\n  "closed_at": null,\n  "opened_at": "2026-01-01T00:00:00+00:00",\n'
    '  "outcome": "in_progress",\n  "rc": "' + _VERSION + '"\n}\n'
)

# Tasks seeded on every root: (task id, README release line, ## State).
_TASKS = (
    ("CODER-20260101-001-pending", _VERSION, "BACKLOG"),
    ("CODER-20260101-002-working", _VERSION, "WORKING"),
    ("CODER-20260101-003-finished", _VERSION, "DONE"),
    ("CODER-20260101-004-dropped", _VERSION, "WONT-DO"),
    ("CODER-20260101-005-lowercase", _VERSION, "done"),
    ("CODER-20260101-006-other-rc", "v0.5.0", "BACKLOG"),
    ("CODER-20260101-007-no-state", _VERSION, None),
)

_QUEUE = (
    "# Coder Backlog\n\n## Queue\n"
    "- [ ] CODER-20260101-001-pending — pending\n"
    "- [A] CODER-20260101-002-working — working\n"
    "- [x] CODER-20260101-003-finished — finished\n"
)

# Scenario options (every key optional):
#   args          CLI arguments (default: --project P --key V --yes)
#   env           extra environment for the CLI run
#   stdin         text fed to the CLI (default: none -- stdin is /dev/null)
#   prefix        project.cfg branch_prefix
#   push          project.cfg push_to_remote ("true"/"false"; default "false")
#   head          branch checked out: "main", "rc" or another name
#   rc_branch     whether rc/<V> exists locally (default True)
#   origin_main   write a local refs/remotes/origin/<main> ref
#   active        release-state.md Active RC (default V); None: no file
#   cfg           write project.cfg (default True)
#   dev_tree      write dev_tree_path into project.cfg (default True)
#   json_record   seed release-state/<V>.json (default True)
#   rerun         run the CLI twice; the second run is the one recorded
#   rest          the REST twin can reach this scenario (always confirmed)
_SCENARIOS: dict[str, dict] = {
    "help": {"args": ["--help"]},
    "usage-missing-key": {"args": ["--project", _PROJECT, "--yes"]},
    "usage-missing-project": {"args": ["--key", _VERSION, "--yes"]},
    "usage-project-without-value": {"args": ["--key", _VERSION, "--project"]},
    "usage-key-without-value": {"args": ["--project", _PROJECT, "--key"]},
    "usage-unknown-flag": {"args": ["--project", _PROJECT, "--key", _VERSION, "--bogus"]},
    "usage-positional": {"args": [_PROJECT, _VERSION]},
    "project-from-environment": {
        "args": ["--key", _VERSION, "-y"],
        "env": {"PGAI_PROJECT_NAME": _PROJECT},
    },
    "invalid-version": {"args": ["--project", _PROJECT, "--key", "0.4", "--yes"], "rest": True},
    "missing-project-cfg": {"cfg": False, "rest": True},
    "missing-dev-tree-path": {"dev_tree": False, "rest": True},
    "repo-root-override": {"dev_tree": False, "env": {"REPO_ROOT": "{REPO}"}},
    "missing-release-state": {"active": None, "rest": True},
    "wrong-branch": {"head": "feature/elsewhere", "rest": True},
    "active-rc-mismatch": {"active": "v9.9.9", "rest": True},
    "already-cancelled": {"active": "none", "rc_branch": False, "rest": True},
    "active-none-branch-left": {"active": "none", "rest": True},
    "cancel-on-main": {"rest": True},
    "cancel-on-rc-branch": {"head": "rc", "rest": True},
    "cancel-twice": {"rerun": True},
    "no-json-record": {"json_record": False, "rest": True},
    "branch-prefix": {"prefix": "ai_", "rest": True},
    "prompt-answered-yes": {"args": ["--project", _PROJECT, "--key", _VERSION], "stdin": "y\n"},
    "prompt-answered-padded-yes": {"args": ["--project", _PROJECT, "--key", _VERSION], "stdin": "  YeS \n"},
    "prompt-answered-no": {"args": ["--project", _PROJECT, "--key", _VERSION], "stdin": "n\n"},
    "prompt-unanswered": {"args": ["--project", _PROJECT, "--key", _VERSION]},
    "prompt-answer-without-newline": {"args": ["--project", _PROJECT, "--key", _VERSION], "stdin": "y"},
    "push-true-non-cm-role": {
        "push": "true",
        "origin_main": True,
        "env": {"PGAI_AGENT_ROLE": "tester"},
    },
    "push-true-from-rc-branch": {
        "push": "true",
        "origin_main": True,
        "head": "rc",
        "env": {"PGAI_AGENT_ROLE": "tester"},
    },
    "push-true-no-origin-main": {"push": "true", "env": {"PGAI_AGENT_ROLE": "tester"}},
}


@pytest.fixture(autouse=True)
def _require_git() -> None:
    """Skip the module when git is absent; every scenario builds a repository."""
    require_binary("git", label="cm-cancel-rc")


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _git(repo: pathlib.Path, *args: str) -> str:
    env = {**os.environ, **_GIT_ENV}
    done = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True, env=env,
    )
    return done.stdout


def _build_repo(base: pathlib.Path, spec: dict) -> pathlib.Path:
    """A local repository with no remote, holding <prefix>main and <prefix>rc/<V>."""
    prefix = spec.get("prefix", "")
    main = f"{prefix}main"
    rc = f"{prefix}rc/{_VERSION}"
    repo = base / "dev_repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", f"--initial-branch={main}")
    (repo / "README.md").write_text("# fixture\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "Initial commit")
    if spec.get("rc_branch", True):
        _git(repo, "branch", rc)
    if spec.get("origin_main"):
        _git(repo, "update-ref", f"refs/remotes/origin/{main}", main)
    head = spec.get("head", "main")
    if head == "rc":
        _git(repo, "checkout", "-q", rc)
    elif head != "main":
        _git(repo, "checkout", "-q", "-b", head)
    return repo


def _seed_root(base: pathlib.Path, repo: pathlib.Path, spec: dict) -> pathlib.Path:
    """A throwaway kanban root with one project whose RC is _VERSION."""
    root = base / "kanban_root"
    proj = root / "projects" / _PROJECT
    (proj / "tasks" / "queues").mkdir(parents=True)
    (proj / "release-state").mkdir()
    (root / "logs").mkdir()
    (root / "tmp").mkdir()
    (root / "kanban.cfg").write_text("[paths]\n\n[chain]\npm_mode = automatic\n", encoding="utf-8")
    (root / "projects.cfg").write_text(
        f"[project:{_PROJECT}]\npriority=1\nenabled=true\n", encoding="utf-8"
    )
    if spec.get("cfg", True):
        lines = ["[project]", f"project_name = {_PROJECT}"]
        if spec.get("dev_tree", True):
            lines.append(f"dev_tree_path = {repo}")
        lines += ["workflow_type = release", f"branch_prefix = {spec.get('prefix', '')}",
                  f"push_to_remote = {spec.get('push', 'false')}  # sandbox"]
        (proj / "project.cfg").write_text("\n".join(lines) + "\n", encoding="utf-8")
    active = spec.get("active", _VERSION)
    if active is not None:
        opened = ("none", "none") if active == "none" else ("2026-01-01T00:00:00+00:00", "CM-20260101-000-open")
        (proj / "release-state.md").write_text(
            _RELEASE_STATE.format(active=active, opened_at=opened[0], opened_by=opened[1]),
            encoding="utf-8",
        )
    if spec.get("json_record", True):
        (proj / "release-state" / f"{_VERSION}.json").write_text(_JSON_RECORD, encoding="utf-8")
    for task_id, release, state in _TASKS:
        task_dir = proj / "tasks" / task_id
        task_dir.mkdir()
        (task_dir / "README.md").write_text(
            f"# Task: {task_id}\n\n## Release Version\n{release}\n", encoding="utf-8"
        )
        state_block = f"## State\n{state}\n\n" if state is not None else ""
        (task_dir / "status.md").write_text(
            f"# Status\n\n## Task\n{task_id}\n\n{state_block}## Summary\nfixture\n",
            encoding="utf-8",
        )
    (proj / "tasks" / "queues" / "coder_backlog.md").write_text(_QUEUE, encoding="utf-8")
    # A live install carries its own copy of the cm helpers under the root.
    cm_dir = root / "pgai_agent_kanban" / "cm"
    cm_dir.mkdir(parents=True)
    for src in sorted(_CM_PY_SRC.glob("*.py")):
        (cm_dir / src.name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return root


def build_sandbox(base: pathlib.Path, name: str) -> tuple[pathlib.Path, pathlib.Path]:
    """Build scenario *name*'s repository and kanban root under *base*."""
    spec = _SCENARIOS[name]
    base.mkdir(parents=True, exist_ok=True)
    repo = _build_repo(base, spec)
    return _seed_root(base, repo, spec), repo


# ---------------------------------------------------------------------------
# Normalising and snapshotting
# ---------------------------------------------------------------------------

_SHA_RE = re.compile(r"\(was [0-9a-f]{4,40}\)")
_CANCELLED_AT_RE = re.compile(r"Cancelled at: \S+")
_UTC_RE = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")
_UTC_FILE_RE = re.compile(r"\d{4}-\d\d-\d\d-\d\d-\d\d-\d\dZ")


def normalise(text: str, root: pathlib.Path, repo: pathlib.Path) -> str:
    """Replace run-specific paths, commit ids and clock readings with tokens."""
    text = text.replace(str(repo), "<REPO>").replace(str(root), "<ROOT>")
    text = _SHA_RE.sub("(was <SHA>)", text)
    text = _CANCELLED_AT_RE.sub("Cancelled at: <NOW>", text)
    text = _UTC_RE.sub("<UTC>", text)
    return _UTC_FILE_RE.sub("<UTC>", text)


def snapshot(root: pathlib.Path, repo: pathlib.Path) -> dict:
    """Every file cancel-rc may touch or must leave alone, plus the branch list."""
    proj = root / "projects" / _PROJECT
    files: dict = {}
    for path in sorted(proj.rglob("*")):
        if path.is_file():
            rel = normalise(str(path.relative_to(root)), root, repo)
            files[rel] = normalise(path.read_text(encoding="utf-8"), root, repo)
    refs = _git(repo, "for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes")
    head = subprocess.run(
        ["git", "-C", str(repo), "symbolic-ref", "--short", "HEAD"],
        capture_output=True, text=True, check=False,
    ).stdout.strip()
    return {"files": files, "refs": refs.splitlines(), "head": head}


# ---------------------------------------------------------------------------
# Running the CLI
# ---------------------------------------------------------------------------


def _cli_env(base: pathlib.Path, root: pathlib.Path, repo: pathlib.Path, spec: dict) -> dict:
    extra = {
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
        "KANBAN_ROOT": str(root),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
    }
    for key, value in spec.get("env", {}).items():
        extra[key] = value.replace("{REPO}", str(repo))
    home = base / "home"
    home.mkdir(exist_ok=True)
    return build_scrubbed_env(
        home_dir=home,
        extra_path_dirs=[pathlib.Path(sys.executable).parent],
        extra_env=extra,
    )


def _default_args() -> list[str]:
    return ["--project", _PROJECT, "--key", _VERSION, "--yes"]


def _run_cli(script: pathlib.Path, base, root, repo, spec) -> subprocess.CompletedProcess:
    stdin = spec.get("stdin")
    cwd = base / "elsewhere"
    cwd.mkdir(exist_ok=True)
    return subprocess.run(
        ["bash", str(script), *spec.get("args", _default_args())],
        input=stdin if stdin is not None else None,
        stdin=None if stdin is not None else subprocess.DEVNULL,
        capture_output=True, text=True, env=_cli_env(base, root, repo, spec),
        cwd=str(cwd), timeout=_TIMEOUT_SECONDS,
    )


def run_scenario(base: pathlib.Path, name: str, script: pathlib.Path = _SCRIPT) -> dict:
    """Build scenario *name* under *base*, run the CLI, return the normalised outcome."""
    spec = _SCENARIOS[name]
    root, repo = build_sandbox(base, name)
    run = _run_cli(script, base, root, repo, spec)
    if spec.get("rerun"):
        run = _run_cli(script, base, root, repo, spec)
    return {
        "rc": run.returncode,
        "stdout": normalise(run.stdout, root, repo),
        "stderr": normalise(run.stderr, root, repo),
        "after": snapshot(root, repo),
    }


def _golden() -> dict:
    return json.loads(_GOLDEN_FILE.read_text(encoding="utf-8"))


# The one deliberate change to the CLI's text: --force joined the usage
# line, the short usage block and --help. Applied to the recorded outcome of
# every scenario, so each difference from the pre-port behaviour is listed
# here rather than folded into the golden file.
_FORCE_FLAG_ADDITIONS = (
    ("[--yes] [--help]", "[--yes] [--force] [--help]"),
    (
        "  --yes             skip interactive confirmation prompt\n",
        "  --yes             skip interactive confirmation prompt\n"
        "  --force           bypass the Active-RC-matches-version check (not the prompt)\n",
    ),
    (
        "  --yes             skip the interactive confirmation prompt\n",
        "  --yes             skip the interactive confirmation prompt\n"
        "  --force           bypass only the Active-RC-matches-version check; the\n"
        "                    prompt still runs unless --yes is also given. The RC\n"
        "                    branch and its per-RC record are cleaned up and\n"
        "                    release-state.md, which then names a different RC,\n"
        "                    is left unchanged\n",
    ),
)


# The --help footprint names the project's configured git remote as
# <remote> (project.cfg [project] git_remote_name, origin when absent)
# instead of a literal origin.
_REMOTE_NAME_HELP_CHANGES = (
    (
        "FOOTPRINT — what this script touches vs. leaves alone:\n",
        "FOOTPRINT — what this script touches vs. leaves alone. <remote> is the\n"
        "project's git remote: project.cfg [project] git_remote_name, origin when\n"
        "that key is absent or blank.\n",
    ),
    (
        "    origin/rc/<version>          deleted (git push origin --delete)\n",
        "    <remote>/rc/<version>        deleted (git push <remote> --delete)\n",
    ),
    (
        "fast-forward pulled from origin when push_to_remote=true\n",
        "fast-forward pulled from <remote> when push_to_remote=true\n",
    ),
    (
        "    main branch on origin        never pushed",
        "    main branch on <remote>      never pushed",
    ),
)


def expected(name: str) -> dict:
    """The recorded pre-port outcome of *name*, with the --force additions and
    the --help remote-name changes applied."""
    recorded = dict(_golden()["scenarios"][name])
    for stream in ("stdout", "stderr"):
        for old, new in _FORCE_FLAG_ADDITIONS + _REMOTE_NAME_HELP_CHANGES:
            recorded[stream] = recorded[stream].replace(old, new)
    return recorded


# ---------------------------------------------------------------------------
# 1. Route/CLI parity
# ---------------------------------------------------------------------------


def test_route_and_cli_reach_cm_cancel_cancel_rc(tmp_path: pathlib.Path) -> None:
    """The route calls cancel_rc with no process; the script reaches it via pp_run_ops.

    The scenario is the malformed-version refusal: cancel_rc decides it
    before its first git command, so the route's run starts no process at
    all, and the CLI's run reaches the function and refuses the same way.
    """
    from tests.fixtures.route_cli_parity import assert_route_cli_parity, build_api_client

    spec = _SCENARIOS["invalid-version"]
    rest_root, rest_repo = build_sandbox(tmp_path / "rest", "invalid-version")
    cli_root, cli_repo = build_sandbox(tmp_path / "cli", "invalid-version")
    result = assert_route_cli_parity(
        "cancel-rc",
        build_api_client(rest_root),
        json={"project": _PROJECT, "key": "0.4", "yes": True},
        cli_args=spec["args"],
        env=_cli_env(tmp_path / "cli", cli_root, cli_repo, spec),
        scratch_dir=tmp_path / "scratch",
    )

    assert result.target.dotted == "pgai_agent_kanban.cm.cancel.cancel_rc"
    assert result.cli is not None
    body = result.response.json()
    assert result.response.status_code == 500, body
    assert body["exit_code"] == result.cli.completed.returncode == 1
    assert normalise(body["stdout"], rest_root, rest_repo) == normalise(
        result.cli.completed.stdout, cli_root, cli_repo
    )
    assert normalise(body["stderr"], rest_root, rest_repo) == normalise(
        result.cli.completed.stderr, cli_root, cli_repo
    )
    assert body["error"] == "cancel_rc.refused"


# ---------------------------------------------------------------------------
# 2. CLI behaviour equals the recorded pre-port behaviour
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(_SCENARIOS))
def test_cli_matches_the_recorded_behaviour(tmp_path: pathlib.Path, name: str) -> None:
    assert run_scenario(tmp_path, name) == expected(name)


def test_every_scenario_is_recorded() -> None:
    assert set(_golden()["scenarios"]) == set(_SCENARIOS)


# ---------------------------------------------------------------------------
# 3. The REST twin leaves what the CLI leaves
# ---------------------------------------------------------------------------


def _rest_body(spec: dict) -> dict:
    args = spec.get("args", _default_args())
    key = args[args.index("--key") + 1]
    return {"project": _PROJECT, "key": key, "yes": True}


@pytest.mark.parametrize("name", sorted(n for n, s in _SCENARIOS.items() if s.get("rest")))
def test_rest_twin_matches_the_recorded_cli_behaviour(
    tmp_path: pathlib.Path, name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.fixtures.route_cli_parity import build_api_client

    spec = _SCENARIOS[name]
    monkeypatch.delenv("PGAI_AGENT_ROLE", raising=False)
    monkeypatch.delenv("REPO_ROOT", raising=False)
    root, repo = build_sandbox(tmp_path, name)
    response = build_api_client(root).post("/operations/cancel-rc", json=_rest_body(spec))
    body = response.json()
    recorded = expected(name)

    assert body["exit_code"] == recorded["rc"], body
    assert response.status_code == (200 if recorded["rc"] == 0 else 500), body
    assert normalise(body["stdout"], root, repo) == recorded["stdout"]
    assert normalise(body["stderr"], root, repo) == recorded["stderr"]
    assert snapshot(root, repo) == recorded["after"]


# ---------------------------------------------------------------------------
# 4. The push gate under the git network stub
# ---------------------------------------------------------------------------

_PUSH_GATE_SKIP = "skipped-unresolved-project"


def _gate_skip_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if _PUSH_GATE_SKIP in line]


class _CancelGateCase:
    """One push-gate shape: sandbox root and repository, the git network stub on PATH."""

    def __init__(self, tmp_path: pathlib.Path, shape: str, env: dict) -> None:
        # origin_main gives a pushing run's fast-forward a local ref to use.
        spec: dict = {"push": "true", "origin_main": True}
        if shape == "no-project-cfg":
            spec["cfg"] = False
        elif shape == "no-dev-tree-path":
            spec["dev_tree"] = False
        elif shape == "local-only":
            spec["push"] = "false"
        elif shape not in ("not-registered", "push-default"):
            raise ValueError(f"unknown push-gate shape {shape!r}")
        self.base = tmp_path / "sandbox"
        self.base.mkdir(parents=True)
        self.repo = _build_repo(self.base, spec)
        self.root = _seed_root(self.base, self.repo, spec)
        cfg = self.root / "projects" / _PROJECT / "project.cfg"
        if shape == "not-registered":
            (self.root / "projects.cfg").write_text("[project:another-project]\npriority=1\n", encoding="utf-8")
        elif shape == "push-default":
            kept = cfg.read_text(encoding="utf-8").splitlines(keepends=True)
            cfg.write_text("".join(l for l in kept if not l.startswith("push_to_remote")), encoding="utf-8")
        self.spec = {"env": env}
        self.stub_log = tmp_path / "git-network-calls.log"
        self.stub_bin = tmp_path / "stub-bin"
        # A pushing run must get past its fetch and origin check to reach the
        # remote delete it is judged on; the stub logs every one and contacts
        # nothing.
        install_git_network_stub(self.stub_bin, self.stub_log, succeed_verbs=("ls-remote", "fetch", "push"))

    def run(self) -> subprocess.CompletedProcess:
        env = _cli_env(self.base, self.root, self.repo, self.spec)
        env["PATH"] = f"{self.stub_bin}{os.pathsep}{env['PATH']}"
        cwd = self.base / "elsewhere"
        cwd.mkdir(exist_ok=True)
        return subprocess.run(
            ["bash", str(_SCRIPT), *_default_args()],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, env=env,
            cwd=str(cwd), timeout=_TIMEOUT_SECONDS,
        )

    def network_calls(self) -> list[str]:
        if not self.stub_log.is_file():
            return []
        return [line for line in self.stub_log.read_text(encoding="utf-8").splitlines() if line]


def _cancel_out(run: subprocess.CompletedProcess) -> str:
    return run.stdout + run.stderr


_BOTH_CHECKOUT_NAMES = {"REPO_ROOT": "{REPO}", "PGAI_DEV_TREE_PATH": "{REPO}"}


class TestCancelPushGate:
    """cancel-rc touches origin only for a project the shared push resolver resolves."""

    @pytest.mark.parametrize(
        ("shape", "token", "env", "rc"),
        [
            ("no-project-cfg", "missing project-cfg", _BOTH_CHECKOUT_NAMES, 0),
            ("not-registered", "missing registration", _BOTH_CHECKOUT_NAMES, 0),
            ("no-dev-tree-path", "missing dev-tree-path", _BOTH_CHECKOUT_NAMES, 0),
            ("no-project-cfg", "missing project-cfg", {"PGAI_DEV_TREE_PATH": "{REPO}"}, 1),
            ("no-dev-tree-path", "missing dev-tree-path", {"PGAI_DEV_TREE_PATH": "{REPO}"}, 1),
        ],
        ids=[
            "no-project-cfg-repo-root", "not-registered-repo-root", "no-dev-tree-path-repo-root",
            "no-project-cfg-env-only", "no-dev-tree-path-env-only",
        ],
    )
    def test_unresolved_project_contacts_no_remote(
        self, tmp_path: pathlib.Path, shape: str, token: str, env: dict, rc: int
    ) -> None:
        """With a repository named, the cancel completes locally; with PGAI_DEV_TREE_PATH alone it refuses."""
        case = _CancelGateCase(tmp_path, shape, env)
        run = case.run()

        assert run.returncode == rc, f"{shape}: unexpected exit\n{_cancel_out(run)}"
        assert case.network_calls() == [], f"{shape}: cancel-rc made a git network call\n{_cancel_out(run)}"
        skips = _gate_skip_lines(_cancel_out(run))
        assert len(skips) == 1 and token in skips[0], (
            f"{shape}: expected one {_PUSH_GATE_SKIP} line naming {token!r}\n{_cancel_out(run)}"
        )
        assert skips[0].startswith("[cm-cancel-rc] "), skips[0]
        branches = _git(case.repo, "for-each-ref", "--format=%(refname)", "refs/heads")
        assert (f"refs/heads/rc/{_VERSION}" in branches) is (rc != 0), branches

    def test_registered_project_without_push_key_still_deletes_on_origin(self, tmp_path: pathlib.Path) -> None:
        case = _CancelGateCase(tmp_path, "push-default", {})
        run = case.run()

        assert run.returncode == 0, _cancel_out(run)
        calls = [line.split() for line in case.network_calls()]
        assert ["git", "-C", str(case.repo), "fetch", "origin"] in calls, calls
        assert ["git", "-C", str(case.repo), "push", "origin", "--delete", f"rc/{_VERSION}"] in calls, calls
        assert _gate_skip_lines(_cancel_out(run)) == []
        assert "[cm-cancel-rc] Push policy: push_to_remote=true" in run.stdout

    def test_push_to_remote_false_stays_local(self, tmp_path: pathlib.Path) -> None:
        case = _CancelGateCase(tmp_path, "local-only", {})
        run = case.run()

        assert run.returncode == 0, _cancel_out(run)
        assert case.network_calls() == [], _cancel_out(run)
        assert _gate_skip_lines(_cancel_out(run)) == []
        assert "[cm-cancel-rc] Push policy: push_to_remote=false" in run.stdout
