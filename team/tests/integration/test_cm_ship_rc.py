"""
test_cm_ship_rc.py
==================
Integration tests for the ``ship-rc`` verb on a sandbox kanban root with
sandbox git repositories: ``team/scripts/ship-rc.sh`` (a thin wrapper that
reaches ``pgai_agent_kanban.cm.ship.ship_rc`` through ``pp_run_ops``) and
``POST /operations/ship-rc`` (which calls the same function in process).

Coverage:

  1. Route/CLI parity (tests/fixtures/route_cli_parity.py): the route calls
     ``cm.ship.ship_rc`` and starts no process, and the script reaches the
     same function through ``pp_run_ops``.
  2. CLI behaviour against the committed golden
     (tests/fixtures/golden/ship_rc_cli.json, captured from the script before
     its logic moved to Python). For every recorded scenario -- the dry-run
     plan, a real ship, a branch prefix, the RC branch checked out, the
     modify/delete auto-resolve, release-state.md already reset or absent,
     each release hook phase passing, failing, missing or not executable,
     every refusal, ``--help`` and the usage errors -- stdout, stderr, the
     exit code, every file under the project directory, the branch and tag
     list of the repository and of its origin, and the checked-out branch
     are unchanged. The scenario whose behaviour changed on purpose is
     listed in ``_CHANGED`` with what the port does instead.
     The scenarios for a project the shared push-decision resolver cannot
     resolve (``ship-without-project-cfg``, ``dev-tree-from-environment``,
     ``repo-root-override``, ``not-a-repository``) are recorded with the
     push gate applied: one skipped-unresolved-project line and no origin
     step, so origin's refs are left as built.
  3. The REST twin on its own sandbox: for the scenarios an HTTP caller can
     reach (the typed confirmation, ``dry_run`` as given), the response's
     exit code, stdout and stderr and every on-disk effect equal the CLI's
     recorded ones.
  4. The push gate under the git network stub (TestShipPushGate): no
     project.cfg, no projects.cfg registration, and a project.cfg without
     dev_tree_path -- each with the repository reachable only through
     PGAI_DEV_TREE_PATH or kanban.cfg [paths] dev_tree_path -- ship locally
     with zero git network calls and one skipped-unresolved-project line; a
     project registered in projects.cfg but not under the typed name is
     refused before any git; a registered project with no push_to_remote
     key still pushes main, the tag and the RC branch delete; and
     ``push_to_remote = false`` makes no network call.

Every root and repository is a throwaway directory under ``tmp_path``.
``origin`` is a bare repository beside the working one, so every push lands
in the sandbox; the push-gate tests put the git network stub ahead of git
on PATH, so their network verbs are logged and reach nothing. Nothing
reaches a network.
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

_SCRIPT = _TEAM_DIR / "scripts" / "ship-rc.sh"
_GOLDEN_FILE = _TEAM_DIR / "tests" / "fixtures" / "golden" / "ship_rc_cli.json"

_PROJECT = "ship-fixture"
_VERSION = "v0.4.0"
_TIMEOUT_SECONDS = 120

# Fixed commit identity and dates, and no user or system git configuration,
# so every commit -- the fixture's and the squash commit the ship makes --
# has the same id on every run.
_GIT_ENV = {
    "GIT_AUTHOR_NAME": "PGAI Test",
    "GIT_AUTHOR_EMAIL": "test@pgai.local",
    "GIT_COMMITTER_NAME": "PGAI Test",
    "GIT_COMMITTER_EMAIL": "test@pgai.local",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+00:00",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+00:00",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}

# Environment names that would route a fixture's git through the agent-lane
# remote guard or point a run at a live install; removed for every run.
_AMBIENT_NAMES = (
    "PGAI_AGENT_ROLE",
    "PGAI_GIT_BOUNDARY_GUARD_EVIDENCE_FILE",
    "PGAI_DEV_TREE_PATH",
    "PGAI_PROJECT_NAME",
    "PGAI_PROJECT_ROOT",
    "PGAI_TASKS_DIR",
    "REPO_ROOT",
)

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

# A hook that shows what it was run with: its environment, its working
# directory, a stderr line (merged into the prefixed output) and a last line
# without a newline.
_HOOK = (
    "#!/usr/bin/env bash\n"
    "echo \"phase={phase} version=$PGAI_TARGET_VERSION project=$PGAI_PROJECT_NAME\"\n"
    "echo \"project_root=$PGAI_PROJECT_ROOT\"\n"
    "echo \"dev_tree=$PGAI_DEV_TREE_PATH rc=$PGAI_RC_BRANCH kanban=$PGAI_KANBAN_ROOT\"\n"
    "echo \"cwd=$(pwd)\"\n"
    "echo \"a line on stderr\" >&2\n"
    "printf 'last line without a newline'\n"
    "exit {code}\n"
)

# Scenario options (every key optional):
#   args          CLI arguments (default: --project P --key V)
#   env           extra environment for the CLI run ("{REPO}" and "{BASE}"
#                 are replaced with the scenario's paths)
#   prefix        project.cfg branch_prefix
#   head          branch checked out: "main" (default) or "rc"
#   rc_local      whether <prefix>rc/<V> exists locally (default True)
#   rc_origin     whether <prefix>rc/<V> exists on origin (default True)
#   origin        whether the repository has an origin remote (default True)
#   tag           create the release tag <prefix><V> before the run
#   dirty         modify a tracked file before the run
#   diverged      a commit on <prefix>main (and origin) the RC lacks
#   conflict      "content": both sides edit a.txt; "modify-delete": main
#                 edits f.txt, the RC deletes it; "delete-modify": main
#                 deletes f.txt, the RC edits it
#   active        release-state.md Active RC (default V); "none" writes all
#                 three RC fields as none; None: no file
#   cfg           write project.cfg (default True)
#   dev_tree      write dev_tree_path into project.cfg (default True)
#   push          project.cfg push_to_remote value (default: key absent)
#   cfg_extra     extra project.cfg lines (a [hooks] section)
#   hooks         {phase: exit code} kanban-side hooks under projects/P/hooks/
#   in_repo_hooks {phase: "exec"|"noexec"} untracked hooks under the
#                 repository's .pgai/hooks/
#   abs_hook      {phase: exit code} hooks outside both trees, named by an
#                 absolute path in project.cfg [hooks]
#   rerun         run the CLI twice; the second run is the one recorded
#   rest          the REST twin can reach this scenario: its CLI arguments
#                 are --project, --key and optionally --dry-run
_SCENARIOS: dict[str, dict] = {
    "help": {"args": ["--help"]},
    "help-short": {"args": ["-h"]},
    "usage-missing-key": {"args": ["--project", _PROJECT]},
    "usage-missing-project": {"args": ["--key", _VERSION]},
    "usage-key-without-value": {"args": ["--project", _PROJECT, "--key"]},
    "usage-project-without-value": {"args": ["--project"]},
    "usage-unknown-flag": {"args": ["--project", _PROJECT, "--key", _VERSION, "--bogus"]},
    "usage-positional": {"args": ["--project", _PROJECT, "--key", _VERSION, "extra"]},
    "project-from-environment": {
        "args": ["--key", _VERSION, "--dry-run"],
        "env": {"PGAI_PROJECT_NAME": _PROJECT},
    },
    "project-prefix-resolves": {"args": ["--project", "ship", "--key", _VERSION, "--dry-run"], "rest": True},
    "project-not-registered": {
        "args": ["--project", "no-such-project", "--key", _VERSION],
        "rest": True,
    },
    "invalid-version": {"args": ["--project", _PROJECT, "--key", "0.4"], "rest": True},
    "invalid-branch-prefix": {"prefix": "bad/prefix", "rest": True},
    "dry-run": {"args": ["--project", _PROJECT, "--key", _VERSION, "--dry-run"], "rest": True},
    "dry-run-branch-prefix": {
        "prefix": "ai_",
        "args": ["--project", _PROJECT, "--key", _VERSION, "--dry-run"],
        "rest": True,
    },
    "not-a-repository": {"dev_tree": False, "env": {"REPO_ROOT": "{BASE}/not_a_repo"}},
    "dev-tree-from-environment": {"dev_tree": False, "env": {"PGAI_DEV_TREE_PATH": "{REPO}"}},
    "repo-root-override": {"dev_tree": False, "env": {"REPO_ROOT": "{REPO}"}},
    "dirty-worktree": {"dirty": True, "rest": True},
    "tag-exists": {"tag": True, "rest": True},
    "rc-missing-locally": {"rc_local": False, "rest": True},
    "rc-missing-on-origin": {"rc_origin": False, "rest": True},
    "ship": {"rest": True},
    "ship-twice": {"rerun": True},
    "ship-branch-prefix": {"prefix": "ai_", "rest": True},
    "ship-from-rc-branch": {"head": "rc", "rest": True},
    "ship-release-state-already-reset": {"active": "none", "rest": True},
    "ship-release-state-missing": {"active": None, "rest": True},
    "ship-without-project-cfg": {"cfg": False, "env": {"REPO_ROOT": "{REPO}"}},
    "ship-local-only": {"push": "false", "origin": False, "rc_origin": False, "rest": True},
    "fidelity-divergence": {"diverged": True, "rest": True},
    "content-conflict": {"conflict": "content", "rest": True},
    "modify-delete-auto-resolved": {"conflict": "modify-delete", "rest": True},
    "delete-modify-auto-resolved": {"conflict": "delete-modify", "rest": True},
    "hooks-all-pass": {"hooks": {"pre-squash": 0, "pre-tag": 0, "post-tag": 0}, "rest": True},
    "hook-pre-squash-fails": {"hooks": {"pre-squash": 3}, "rest": True},
    "hook-pre-tag-fails": {"hooks": {"pre-tag": 4}, "rest": True},
    "hook-post-tag-fails": {"hooks": {"post-tag": 5}, "rest": True},
    "hook-pre-squash-required-missing": {
        "cfg_extra": "[hooks]\ncm_release_pre_squash_hook_required = true\n",
        "rest": True,
    },
    "hook-pre-tag-required-missing": {
        "cfg_extra": "[hooks]\ncm_release_pre_tag_hook_required = True\n",
        "rest": True,
    },
    "hook-in-repo-pre-squash-not-executable": {"in_repo_hooks": {"pre-squash": "noexec"}, "rest": True},
    "hook-in-repo-pre-tag-not-executable": {"in_repo_hooks": {"pre-tag": "noexec"}, "rest": True},
    "hook-in-repo-post-tag-not-executable": {"in_repo_hooks": {"post-tag": "noexec"}, "rest": True},
    "hook-in-repo-pre-tag-runs": {"in_repo_hooks": {"pre-tag": "exec"}, "rest": True},
    "hook-cfg-absolute-path": {"abs_hook": {"pre-tag": 0}, "rest": True},
    "hook-cfg-absolute-path-missing-file": {
        "cfg_extra": "[hooks]\ncm_release_post_tag_hook = /nonexistent/ship-fixture-post-tag.sh\n",
        "rest": True,
    },
}


@pytest.fixture(autouse=True)
def _require_git() -> None:
    """Skip the module when git is absent; every scenario builds a repository."""
    require_binary("git", label="cm-ship-rc")


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _fixture_env() -> dict:
    env = {k: v for k, v in os.environ.items() if k not in _AMBIENT_NAMES}
    env.update(_GIT_ENV)
    return env


def _git(repo: pathlib.Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True, env=_fixture_env(),
    )
    return done.stdout


def _commit_all(repo: pathlib.Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


def _build_repo(base: pathlib.Path, spec: dict) -> pathlib.Path:
    """A working repository cloned from a bare ``origin``, holding <prefix>main and <prefix>rc/<V>.

    The history is built in a seed repository, cloned bare as ``origin``,
    and cloned again as the working repository, so no fixture step pushes
    or fetches.
    """
    prefix = spec.get("prefix", "")
    if prefix == "bad/prefix":
        prefix = ""
    main = f"{prefix}main"
    rc = f"{prefix}rc/{_VERSION}"
    seed = base / "seed"
    seed.mkdir(parents=True)
    _git(seed, "init", "-q", f"--initial-branch={main}")
    (seed / "README.md").write_text("# fixture\n", encoding="utf-8")
    (seed / "a.txt").write_text("alpha\n", encoding="utf-8")
    (seed / "f.txt").write_text("foxtrot\n", encoding="utf-8")
    _commit_all(seed, "Initial commit")

    conflict = spec.get("conflict")
    _git(seed, "checkout", "-q", "-b", rc)
    (seed / "feature.txt").write_text("the release's feature\n", encoding="utf-8")
    if conflict == "content":
        (seed / "a.txt").write_text("alpha, as the RC has it\n", encoding="utf-8")
    elif conflict == "modify-delete":
        (seed / "f.txt").unlink()
    elif conflict == "delete-modify":
        (seed / "f.txt").write_text("foxtrot, as the RC has it\n", encoding="utf-8")
    _commit_all(seed, "Add the release's feature")

    _git(seed, "checkout", "-q", main)
    if conflict == "content":
        (seed / "a.txt").write_text("alpha, as main has it\n", encoding="utf-8")
        _commit_all(seed, "Edit a.txt on main")
    elif conflict == "modify-delete":
        (seed / "f.txt").write_text("foxtrot, as main has it\n", encoding="utf-8")
        _commit_all(seed, "Edit f.txt on main")
    elif conflict == "delete-modify":
        (seed / "f.txt").unlink()
        _commit_all(seed, "Delete f.txt on main")
    if spec.get("diverged"):
        (seed / "hotfix.txt").write_text("a fix that landed on main mid-RC\n", encoding="utf-8")
        _commit_all(seed, "Hotfix on main")

    origin = base / "origin.git"
    subprocess.run(
        ["git", "clone", "-q", "--bare", str(seed), str(origin)],
        capture_output=True, text=True, check=True, env=_fixture_env(),
    )
    repo = base / "dev_repo"
    subprocess.run(
        ["git", "clone", "-q", "--branch", main, str(origin), str(repo)],
        capture_output=True, text=True, check=True, env=_fixture_env(),
    )
    if spec.get("rc_local", True):
        _git(repo, "branch", "-q", "--no-track", rc, f"origin/{rc}")
    if not spec.get("rc_origin", True):
        _git(origin, "branch", "-q", "-D", rc)
        _git(repo, "update-ref", "-d", f"refs/remotes/origin/{rc}")
    if not spec.get("origin", True):
        _git(repo, "remote", "remove", "origin")
    if spec.get("tag"):
        _git(repo, "tag", f"{prefix}{_VERSION}")
    if spec.get("head") == "rc":
        _git(repo, "checkout", "-q", rc)
    if spec.get("dirty"):
        (repo / "README.md").write_text("# fixture, edited and not committed\n", encoding="utf-8")
    for phase, mode in spec.get("in_repo_hooks", {}).items():
        hook = repo / ".pgai" / "hooks" / f"cm-release-{phase}.sh"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text(_HOOK.format(phase=phase, code=0), encoding="utf-8")
        hook.chmod(0o755 if mode == "exec" else 0o644)
    (base / "not_a_repo").mkdir()
    return repo


def _seed_root(base: pathlib.Path, repo: pathlib.Path, spec: dict) -> pathlib.Path:
    """A throwaway kanban root with one project whose RC is _VERSION."""
    root = base / "kanban_root"
    proj = root / "projects" / _PROJECT
    root.mkdir(parents=True)
    (root / "logs").mkdir()
    (root / "tmp").mkdir()
    (root / "kanban.cfg").write_text("[paths]\n\n[chain]\npm_mode = automatic\n", encoding="utf-8")
    (root / "projects.cfg").write_text(
        f"[project:{_PROJECT}]\npriority=1\nenabled=true\n\n[project:other-project]\npriority=2\n",
        encoding="utf-8",
    )
    (proj / "tasks").mkdir(parents=True)
    if spec.get("cfg", True):
        lines = ["[project]", f"project_name = {_PROJECT}"]
        if spec.get("dev_tree", True):
            lines.append(f"dev_tree_path = {repo}")
        lines += ["workflow_type = release", f"branch_prefix = {spec.get('prefix', '')}"]
        if "push" in spec:
            lines.append(f"push_to_remote = {spec['push']}")
        text = "\n".join(lines) + "\n"
        extra = spec.get("cfg_extra", "")
        abs_hooks = spec.get("abs_hook", {})
        if abs_hooks:
            extra += "[hooks]\n"
            for phase, code in abs_hooks.items():
                hook = base / "ops-hooks" / f"{phase}.sh"
                hook.parent.mkdir(parents=True, exist_ok=True)
                hook.write_text(_HOOK.format(phase=phase, code=code), encoding="utf-8")
                hook.chmod(0o755)
                extra += f"cm_release_{phase.replace('-', '_')}_hook = {hook}\n"
        if extra:
            text += "\n" + extra
        (proj / "project.cfg").write_text(text, encoding="utf-8")
    for phase, code in spec.get("hooks", {}).items():
        hook = proj / "hooks" / f"cm-release-{phase}.sh"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text(_HOOK.format(phase=phase, code=code), encoding="utf-8")
        hook.chmod(0o755)
    active = spec.get("active", _VERSION)
    if active is not None:
        opened = ("none", "none") if active == "none" else ("2026-01-01T00:00:00+00:00", "CM-20260101-000-open")
        (proj / "release-state.md").write_text(
            _RELEASE_STATE.format(active=active, opened_at=opened[0], opened_by=opened[1]),
            encoding="utf-8",
        )
    return root


def build_sandbox(base: pathlib.Path, name: str) -> tuple[pathlib.Path, pathlib.Path]:
    """Build scenario *name*'s repositories and kanban root under *base*."""
    spec = _SCENARIOS[name]
    base.mkdir(parents=True, exist_ok=True)
    repo = _build_repo(base, spec)
    return _seed_root(base, repo, spec), repo


# ---------------------------------------------------------------------------
# Normalising and snapshotting
# ---------------------------------------------------------------------------

_LEDGER_TS_RE = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)?")
# The refusal ledger names the code that raised the refusal (a script and
# function, or a module and function) and whether a test suite was running;
# both describe the caller rather than the refusal.
_LEDGER_SURFACE_RE = re.compile(r"^(## Refusal:.*\s—\s)\S+\s*$", re.M)
_LEDGER_CONTEXT_RE = re.compile(r"^(- Context:).*$", re.M)
# The fixture's own fixed timestamps, which are data rather than clock readings.
_FIXED_TIMES = ("2026-01-01T00:00:00+00:00", "2026-01-02T03:04:05+00:00")


def normalise(text: str, base: pathlib.Path) -> str:
    """Replace the run's sandbox path with a token."""
    return text.replace(str(base), "<BASE>")


def _normalise_ledger(text: str) -> str:
    for index, fixed in enumerate(_FIXED_TIMES):
        text = text.replace(fixed, f"<FIXED{index}>")
    text = _LEDGER_TS_RE.sub("<TS>", text)
    text = _LEDGER_SURFACE_RE.sub(r"\1<SURFACE>", text)
    text = _LEDGER_CONTEXT_RE.sub(r"\1 <CONTEXT>", text)
    for index, fixed in enumerate(_FIXED_TIMES):
        text = text.replace(f"<FIXED{index}>", fixed)
    return text


def _files_under(top: pathlib.Path, base: pathlib.Path) -> dict:
    files: dict = {}
    if not top.is_dir():
        return files
    for path in sorted(top.rglob("*")):
        if path.is_file():
            rel = normalise(str(path.relative_to(base)), base)
            text = normalise(path.read_text(encoding="utf-8", errors="replace"), base)
            if path.name == "refusal-ledger.md":
                text = _normalise_ledger(text)
            files[rel] = text
    return files


def _refs(repo: pathlib.Path) -> list[str]:
    return _git(
        repo, "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads", "refs/tags"
    ).splitlines()


def snapshot(base: pathlib.Path, root: pathlib.Path, repo: pathlib.Path) -> dict:
    """Every file ship-rc may touch, the branches and tags here and on origin, and HEAD."""
    head = subprocess.run(
        ["git", "-C", str(repo), "symbolic-ref", "--short", "HEAD"],
        capture_output=True, text=True, check=False, env=_fixture_env(),
    ).stdout.strip()
    return {
        "files": _files_under(root / "projects" / _PROJECT, base),
        "refs": _refs(repo),
        "origin_refs": _refs(base / "origin.git"),
        "head": head,
        "status": _git(repo, "status", "--porcelain"),
    }


# ---------------------------------------------------------------------------
# Running the CLI
# ---------------------------------------------------------------------------


def _expand(value: str, base: pathlib.Path, repo: pathlib.Path) -> str:
    return value.replace("{REPO}", str(repo)).replace("{BASE}", str(base))


def _cli_env(base: pathlib.Path, root: pathlib.Path, repo: pathlib.Path, spec: dict) -> dict:
    extra = {
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
        "KANBAN_ROOT": str(root),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
        **_GIT_ENV,
    }
    for key, value in spec.get("env", {}).items():
        extra[key] = _expand(value, base, repo)
    home = base / "home"
    home.mkdir(exist_ok=True)
    return build_scrubbed_env(
        home_dir=home,
        extra_path_dirs=[pathlib.Path(sys.executable).parent],
        extra_env=extra,
    )


def _default_args() -> list[str]:
    return ["--project", _PROJECT, "--key", _VERSION]


def _run_cli(script: pathlib.Path, base, root, repo, spec) -> subprocess.CompletedProcess:
    cwd = base / "elsewhere"
    cwd.mkdir(exist_ok=True)
    return subprocess.run(
        ["bash", str(script), *spec.get("args", _default_args())],
        stdin=subprocess.DEVNULL,
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
        "stdout": normalise(run.stdout, base),
        "stderr": normalise(run.stderr, base),
        "after": snapshot(base, root, repo),
    }


def _golden() -> dict:
    return json.loads(_GOLDEN_FILE.read_text(encoding="utf-8"))


# Scenarios whose behaviour changed on purpose. Their recorded pre-port
# outcome stays in the golden file's "scenarios" next to the expected one
# under "changed".
_CHANGED = {
    "ship-local-only": (
        "push_to_remote = false used to be ignored, so a repository with no "
        "origin was refused at the origin branch check (exit 1); the port "
        "skips every origin step for such a project and ships locally"
    ),
}


def expected(name: str) -> dict:
    """The recorded outcome *name* must produce: pre-port, or the listed change."""
    golden = _golden()
    if name in _CHANGED:
        return golden["changed"][name]["outcome"]
    return golden["scenarios"][name]


# ---------------------------------------------------------------------------
# 1. Route/CLI parity
# ---------------------------------------------------------------------------


def test_route_and_cli_reach_cm_ship_ship_rc(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The route calls ship_rc with no process; the script reaches it via pp_run_ops.

    The scenario is the malformed-version refusal: ship_rc decides it before
    its first git command, so the route's run starts no process at all, and
    the CLI's run reaches the function and refuses the same way.
    """
    from tests.fixtures.route_cli_parity import assert_route_cli_parity, build_api_client

    for var in _AMBIENT_NAMES:
        monkeypatch.delenv(var, raising=False)
    spec = _SCENARIOS["invalid-version"]
    rest_base, cli_base = tmp_path / "rest", tmp_path / "cli"
    rest_root, _ = build_sandbox(rest_base, "invalid-version")
    cli_root, cli_repo = build_sandbox(cli_base, "invalid-version")
    result = assert_route_cli_parity(
        "ship-rc",
        build_api_client(rest_root),
        json={"project": _PROJECT, "key": "0.4", "confirm": f"ship-rc {_PROJECT} 0.4"},
        cli_args=spec["args"],
        env=_cli_env(cli_base, cli_root, cli_repo, spec),
        scratch_dir=tmp_path / "scratch",
    )

    assert result.target.dotted == "pgai_agent_kanban.cm.ship.ship_rc"
    assert result.cli is not None
    body = result.response.json()
    assert result.response.status_code == 500, body
    assert body["exit_code"] == result.cli.completed.returncode == 1
    assert normalise(body["stdout"], rest_base) == normalise(result.cli.completed.stdout, cli_base)
    assert normalise(body["stderr"], rest_base) == normalise(result.cli.completed.stderr, cli_base)
    assert body["error"] == "ship_rc.refused"


# ---------------------------------------------------------------------------
# 2. CLI behaviour equals the recorded pre-port behaviour
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(_SCENARIOS))
def test_cli_matches_the_recorded_behaviour(tmp_path: pathlib.Path, name: str) -> None:
    assert run_scenario(tmp_path, name) == expected(name)


def test_every_scenario_is_recorded() -> None:
    golden = _golden()
    assert set(golden["scenarios"]) == set(_SCENARIOS)
    assert set(golden["changed"]) == set(_CHANGED)
    for name, change in golden["changed"].items():
        assert change["reason"] == _CHANGED[name]


def test_local_only_ship_touches_no_origin() -> None:
    """push_to_remote=false: tagged and squashed locally, every origin step narrated as skipped."""
    golden = _golden()
    before = golden["scenarios"]["ship-local-only"]
    after = expected("ship-local-only")
    assert before["rc"] == 1 and "does not exist on origin" in before["stderr"]
    assert after["rc"] == 0, after
    assert f"refs/tags/{_VERSION}" in " ".join(after["after"]["refs"])
    assert after["after"]["origin_refs"] == before["after"]["origin_refs"]
    for kind, skipped in (
        ("check", f"git ls-remote --exit-code --heads origin rc/{_VERSION}"),
        ("fetch", "git fetch origin --tags"),
        ("pull", "git merge --ff-only origin/main"),
        ("push", f"git push origin main refs/tags/{_VERSION}"),
        ("push", f"git push origin --delete rc/{_VERSION}"),
    ):
        assert f"[push_to_remote=false] skipping origin {kind} for {_PROJECT}: {skipped}" in after["stdout"]


def test_dry_run_changes_nothing(tmp_path: pathlib.Path) -> None:
    """The plan names the branches and tag, and the run leaves every file and ref as built."""
    base = tmp_path / "built"
    root, repo = build_sandbox(base, "dry-run")
    untouched = snapshot(base, root, repo)
    outcome = run_scenario(tmp_path / "run", "dry-run")
    assert outcome["rc"] == 0
    assert outcome["stdout"].endswith("  (dry-run: exiting before any git step)\n")
    assert outcome["after"] == {**untouched, "files": outcome["after"]["files"]}
    assert outcome["after"]["files"] == untouched["files"]


# ---------------------------------------------------------------------------
# 3. The REST twin leaves what the CLI leaves
# ---------------------------------------------------------------------------


def _rest_body(spec: dict) -> dict:
    args = spec.get("args", _default_args())
    project = args[args.index("--project") + 1]
    key = args[args.index("--key") + 1]
    body: dict = {"project": project, "key": key, "confirm": f"ship-rc {project} {key}"}
    if "--dry-run" in args:
        body["dry_run"] = True
    return body


#: The HTTP status a REST scenario's refusal answers with when it is not 500:
#: an unknown project is a not-found refusal (adapter.http_status_for).
_REST_REFUSAL_STATUS = {"project-not-registered": 404}


@pytest.mark.parametrize("name", sorted(n for n, s in _SCENARIOS.items() if s.get("rest")))
def test_rest_twin_matches_the_recorded_cli_behaviour(
    tmp_path: pathlib.Path, name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.fixtures.route_cli_parity import build_api_client

    spec = _SCENARIOS[name]
    assert not spec.get("env"), "a REST scenario needs no environment of its own"
    for var in _AMBIENT_NAMES:
        monkeypatch.delenv(var, raising=False)
    for var, value in _GIT_ENV.items():
        monkeypatch.setenv(var, value)
    root, repo = build_sandbox(tmp_path, name)
    monkeypatch.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(root / "tmp"))
    response = build_api_client(root).post("/operations/ship-rc", json=_rest_body(spec))
    body = response.json()
    recorded = expected(name)

    assert body["exit_code"] == recorded["rc"], body
    refused_status = _REST_REFUSAL_STATUS.get(name, 500)
    assert response.status_code == (200 if recorded["rc"] == 0 else refused_status), body
    assert normalise(body["stdout"], tmp_path) == recorded["stdout"]
    assert normalise(body["stderr"], tmp_path) == recorded["stderr"]
    assert snapshot(tmp_path, root, repo) == recorded["after"]


# ---------------------------------------------------------------------------
# 4. The push gate under the git network stub
# ---------------------------------------------------------------------------

_PUSH_GATE_SKIP = "skipped-unresolved-project"


def _gate_skip_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if _PUSH_GATE_SKIP in line]


class _ShipGateCase:
    """One push-gate shape: sandbox root and repositories, the git network stub on PATH."""

    def __init__(self, tmp_path: pathlib.Path, shape: str, via: str = "environment") -> None:
        spec: dict = {}
        if shape == "no-project-cfg":
            spec = {"cfg": False}
        elif shape == "no-dev-tree-path":
            spec = {"dev_tree": False, "push": "true"}
        elif shape in ("no-registry", "not-registered"):
            spec = {"push": "true"}
        elif shape == "local-only":
            spec = {"push": "false"}
        elif shape != "push-default":
            raise ValueError(f"unknown push-gate shape {shape!r}")
        self.base = tmp_path / "sandbox"
        self.base.mkdir(parents=True)
        self.repo = _build_repo(self.base, spec)
        self.root = _seed_root(self.base, self.repo, spec)
        if shape == "no-registry":
            (self.root / "projects.cfg").unlink()
        elif shape == "not-registered":
            (self.root / "projects.cfg").write_text("[project:another-project]\npriority=1\n", encoding="utf-8")
        env: dict = {}
        if shape in ("no-project-cfg", "no-dev-tree-path", "no-registry", "not-registered"):
            # The only ways an unresolved project's run could reach a real
            # checkout; neither may make it pushable.
            if via == "environment":
                env["PGAI_DEV_TREE_PATH"] = "{REPO}"
            else:
                (self.root / "kanban.cfg").write_text(
                    f"[paths]\ndev_tree_path = {self.repo}\n\n[chain]\npm_mode = automatic\n",
                    encoding="utf-8",
                )
        self.spec = {"env": env}
        self.stub_log = tmp_path / "git-network-calls.log"
        self.stub_bin = tmp_path / "stub-bin"
        # A pushing run must get past its origin check and fetch to reach the
        # pushes it is judged on; the stub logs every one and contacts nothing.
        install_git_network_stub(self.stub_bin, self.stub_log, succeed_verbs=("ls-remote", "fetch", "push"))
        self.origin_before = _refs(self.base / "origin.git")

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


def _ship_out(run: subprocess.CompletedProcess) -> str:
    return run.stdout + run.stderr


class TestShipPushGate:
    """ship-rc pushes only for a project the shared push resolver resolves."""

    @pytest.mark.parametrize("via", ["environment", "kanban-cfg"])
    @pytest.mark.parametrize(
        ("shape", "token"),
        [
            ("no-project-cfg", "missing project-cfg"),
            ("no-registry", "missing registration"),
            ("no-dev-tree-path", "missing dev-tree-path"),
        ],
    )
    def test_unresolved_project_ships_locally_without_contacting_a_remote(
        self, tmp_path: pathlib.Path, shape: str, token: str, via: str
    ) -> None:
        case = _ShipGateCase(tmp_path, shape, via)
        run = case.run()

        assert run.returncode == 0, f"{shape}: ship-rc failed\n{_ship_out(run)}"
        assert case.network_calls() == [], f"{shape}: ship-rc made a git network call\n{_ship_out(run)}"
        skips = _gate_skip_lines(_ship_out(run))
        assert len(skips) == 1 and token in skips[0], (
            f"{shape}: expected one {_PUSH_GATE_SKIP} line naming {token!r}\n{_ship_out(run)}"
        )
        assert skips[0].startswith("[ship-rc] "), skips[0]
        assert f"refs/tags/{_VERSION}" in " ".join(_refs(case.repo)), _ship_out(run)
        assert _refs(case.base / "origin.git") == case.origin_before

    def test_project_registered_under_another_name_is_refused_before_any_git(self, tmp_path: pathlib.Path) -> None:
        case = _ShipGateCase(tmp_path, "not-registered")
        run = case.run()

        assert run.returncode == 1, _ship_out(run)
        assert "project_resolve.not_found" in run.stderr, _ship_out(run)
        assert case.network_calls() == []
        assert f"refs/tags/{_VERSION}" not in " ".join(_refs(case.repo))

    def test_registered_project_without_push_key_still_pushes(self, tmp_path: pathlib.Path) -> None:
        case = _ShipGateCase(tmp_path, "push-default")
        run = case.run()

        assert run.returncode == 0, _ship_out(run)
        calls = [line.split() for line in case.network_calls()]
        assert ["git", "-C", str(case.repo), "push", "origin", "main", f"refs/tags/{_VERSION}"] in calls, calls
        assert ["git", "-C", str(case.repo), "push", "origin", "--delete", f"rc/{_VERSION}"] in calls, calls
        assert _gate_skip_lines(_ship_out(run)) == []
        assert "Push policy" not in run.stdout

    def test_push_to_remote_false_stays_local(self, tmp_path: pathlib.Path) -> None:
        case = _ShipGateCase(tmp_path, "local-only")
        run = case.run()

        assert run.returncode == 0, _ship_out(run)
        assert case.network_calls() == [], _ship_out(run)
        assert _gate_skip_lines(_ship_out(run)) == []
        assert "[ship-rc] Push policy: push_to_remote=false" in run.stdout
        assert f"refs/tags/{_VERSION}" in " ".join(_refs(case.repo))


def record_golden(work_dir: pathlib.Path, out_file: Optional[pathlib.Path] = None) -> dict:
    """Run every scenario against the script on disk and return (or write) the record.

    Used to capture the golden file; not a test.
    """
    scenarios = {name: run_scenario(work_dir / name, name) for name in sorted(_SCENARIOS)}
    record = {"scenarios": scenarios, "changed": {}}
    if out_file is not None:
        out_file.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return record
