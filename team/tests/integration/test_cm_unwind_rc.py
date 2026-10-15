"""
test_cm_unwind_rc.py
====================
Integration tests for the ``unwind-rc`` verb on a sandbox kanban root with a
sandbox git repository: ``team/scripts/unwind-rc.sh`` (a thin wrapper that
reaches ``pgai_agent_kanban.cm.unwind.unwind_rc`` through ``pp_run_ops``)
and ``POST /operations/unwind-rc`` (which calls the same function in
process).

Coverage:

  1. Route/CLI parity (tests/fixtures/route_cli_parity.py): the route calls
     ``cm.unwind.unwind_rc`` and starts no process, and the script reaches
     the same function through ``pp_run_ops``.
  2. CLI behaviour against the committed golden
     (tests/fixtures/golden/unwind_rc_cli.json, captured from the script
     before its logic moved to Python). For every recorded scenario -- the
     dry-run plan, a confirmed unwind, a ``--force`` unwind, a re-run, the
     interactive prompt answered yes / no / not at all, a branch prefix, an
     RC branch that is checked out, a tracked requirements file, the per-RC
     record absent / already cancelled / unreadable, every refusal,
     ``--help`` and the usage errors -- stdout, stderr, the exit code, every
     file under the project directory, the backup tree and the branch list
     are unchanged. The scenarios whose pre-port run stopped part-way on a
     malformed file are listed in ``_CHANGED`` with what the port does
     instead. The scenarios for a project the shared push-decision resolver
     cannot resolve (``missing-project-cfg``, ``missing-dev-tree-path``,
     ``project-dir-missing``, ``dev-tree-from-environment``,
     ``repo-root-override``) are recorded with the push gate applied: one
     skipped-unresolved-project line, printed before the repository check,
     and no origin check in the plan. Every scenario that renames the
     requirements file SUPERSEDED, or finds it already renamed, is recorded
     with that file's ``## Status`` closed to ``wont-do``.
  3. The REST twin on its own sandbox: for the scenarios an HTTP caller can
     reach (``confirm`` true, ``force`` as given), the response's exit code,
     stdout and stderr and every on-disk effect equal the CLI's recorded
     ones.
  4. The push gate under the git network stub (TestUnwindPushGate): for
     ``push_to_remote = false`` and for each unresolved shape -- no
     project.cfg, no projects.cfg registering the project, a project.cfg
     without dev_tree_path while PGAI_DEV_TREE_PATH names the repository --
     a confirmed unwind and a dry run make zero git network calls, the
     local unwind completes, and an unresolved shape logs exactly one
     skipped-unresolved-project line naming the missing piece. A registered
     project with no push_to_remote key still checks origin and deletes the
     RC branch there.

Every root and repository is a throwaway directory under ``tmp_path``. No
scenario configures an ``origin`` remote, so ``git ls-remote origin`` fails
locally and no remote branch is ever deleted. The push-gate tests put the
git network stub ahead of git on PATH, so their network verbs are logged
and reach nothing. Nothing reaches a network.
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

_SCRIPT = _TEAM_DIR / "scripts" / "unwind-rc.sh"
_CM_PY_SRC = _TEAM_DIR / "pgai_agent_kanban" / "cm"
_GOLDEN_FILE = _TEAM_DIR / "tests" / "fixtures" / "golden" / "unwind_rc_cli.json"

_PROJECT = "unwind-fixture"
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

_JSON_RECORDS = {
    "in_progress": (
        '{\n  "closed_at": null,\n  "opened_at": "2026-01-01T00:00:00+00:00",\n'
        '  "outcome": "in_progress",\n  "rc": "' + _VERSION + '"\n}\n'
    ),
    "cancelled": (
        '{\n  "closed_at": "2026-01-01T01:00:00Z",\n  "opened_at": "2026-01-01T00:00:00+00:00",\n'
        '  "outcome": "cancelled",\n  "rc": "' + _VERSION + '"\n}\n'
    ),
    "unreadable": "{ not json\n",
}

# Tasks seeded on every root: (task id, README release line, status.md body
# or None for a folder with no status.md).
_STATUS = "# Status\n\n## Task\n{tid}\n\n## State\n{state}\n\n## Summary\nfixture\n"
_TASKS = (
    ("CODER-20260101-001-pending", _VERSION, _STATUS.format(tid="CODER-20260101-001-pending", state="BACKLOG")),
    ("CODER-20260101-002-working", _VERSION, _STATUS.format(tid="CODER-20260101-002-working", state="WORKING")),
    ("CODER-20260101-003-finished", _VERSION, _STATUS.format(tid="CODER-20260101-003-finished", state="DONE")),
    ("CODER-20260101-004-dropped", _VERSION, _STATUS.format(tid="CODER-20260101-004-dropped", state="WONT-DO")),
    ("CODER-20260101-005-other-rc", "v0.5.0", _STATUS.format(tid="CODER-20260101-005-other-rc", state="BACKLOG")),
    ("CODER-20260101-006-no-status", _VERSION, None),
    (
        "CODER-20260101-007-cancelled-before",
        _VERSION,
        _STATUS.format(tid="CODER-20260101-007-cancelled-before", state="WONT-DO")
        + "\n## Cancellation Note (2026-01-01T00:00:00+00:00)\nan earlier unwind\n",
    ),
    ("TESTER-20260101-008-blocked", _VERSION, _STATUS.format(tid="TESTER-20260101-008-blocked", state="BLOCKED")),
    ("CLAUDE-20260101-010-legacy", _VERSION, _STATUS.format(tid="CLAUDE-20260101-010-legacy", state="WORKING")),
)

_QUEUES = {
    "coder_backlog.md": (
        "# Coder Backlog\n\n## Queue\n"
        "- [ ] CODER-20260101-001-pending — pending " + _VERSION + "\n"
        "- [A] CODER-20260101-002-working — working on " + _VERSION + "\n"
        "- [x] CODER-20260101-003-finished — finished\n"
        "- [ ] CODER-20260101-005-other-rc — the next RC, v0.5.0\n"
        "-\t[W] CODER-20260101-006-no-status\n"
        "  - [B] CODER-20260101-007-cancelled-before — indented " + _VERSION + "\n"
    ),
    "tester_backlog.md": (
        "# Tester Backlog\n\n## Queue\n"
        "- [B] TESTER-20260101-008-blocked — blocked on " + _VERSION + "\n"
        "- [B] TESTER-20260101-008-blocked-twin — a longer id, unterminated, " + _VERSION
    ),
    "pm_backlog.md": "# PM Backlog\n\n## Queue\n",
    "priority_backlog.md": (
        "# Priority Backlog\n\n## Queue\n"
        "- [x] PRIORITY-0001-alpha\n"
        "- [x] PRIORITY-0002-delta\n"
        "[x] PRIORITY-0001-alpha (a line without its list dash)\n"
    ),
    "bug_backlog.md": (
        "# Bug Backlog\n\n## Queue\n"
        "- [x] BUG-0001-beta\n"
        "- [ ] BUG-0002-gamma\n"
    ),
}

_REQUIREMENTS_NAME = f"{_VERSION}-bugfix-bundle-20260101.md"


def _requirements_text(root: pathlib.Path) -> str:
    proj = root / "projects" / _PROJECT
    return (
        f"# {_VERSION}-bugfix-bundle\n\n## Status\nrunning\n\n"
        "## Bundled Items\n\n"
        "- [2026-01-01T00:00:00+00:00] wake(pm): piggybacking 1 deferred bug "
        "(trigger: BUG-0001-beta medium) (`[2026-01-01T00:00:00+00:00] wake(pm): "
        "piggybacking 1 deferred bug (trigger: BUG-0001-beta medium)`)\n"
        f"- PRIORITY-0001-alpha.md (`{proj}/priority/PRIORITY-0001-alpha.md`)\n"
        f"- BUG-0001-beta.md (`{proj}/bugs/BUG-0001-beta.md`)\n"
        f"- BUG-0002-gamma.md (`{proj}/bugs/BUG-0002-gamma.md`)\n"
        "- PRIORITY-0002-delta.md\n"
        "- BUG-0003-missing.md\n\n"
        "## Acceptance Criteria\n- PRIORITY-0009-not-bundled is outside the section\n"
    )


_INTAKE_FILES = {
    "priority/PRIORITY-0001-alpha.md": "# PRIORITY-0001-alpha\n\n## Status\ndone\n\n## Body\nalpha\n",
    "priority/PRIORITY-0002-delta.md": "# PRIORITY-0002-delta\n\n## Status\nrunning\n",
    "bugs/BUG-0001-beta.md": "# BUG-0001-beta\n\n## Status\nrunning\n\n**Severity:** medium\n",
    "bugs/BUG-0002-gamma.md": "# BUG-0002-gamma\n\n## Status\nopen\n",
}

_DISCOVERY_FILES = (
    f"bundle-{_VERSION}.json",
    "bundle-v0.5.0.json",
    f"nested/{_VERSION}-cache",
    f"a/b/{_VERSION}-deep",
)

_PLAN_MARKERS = {
    ".materialized.plan-one": f"version: {_VERSION}\n",
    ".materialized.plan-two": "version: v0.5.0\n",
    "plan-three.md": f"version: {_VERSION}\n",
}

# Scenario options (every key optional):
#   args          CLI arguments (default: --project P --key V --confirm)
#   env           extra environment for the CLI run
#   stdin         text fed to the CLI (default: none -- stdin is /dev/null)
#   prefix        project.cfg branch_prefix
#   halt          "project" (default), "global" or None
#   head          branch checked out: "main", "rc" or another name
#   rc_branch     whether <prefix>rc/<V> exists locally (default True)
#   main_branch   whether <prefix>main exists (default True)
#   tag           create the release tag <prefix><V> (a shipped version)
#   active        release-state.md Active RC (default V); None: no file
#   project_dir   create projects/<P>/ (default True)
#   cfg           write project.cfg (default True)
#   dev_tree      write dev_tree_path into project.cfg (default True)
#   push          write push_to_remote = <value> into project.cfg (default:
#                 no key, which the push decision reads as push)
#   registered    write projects.cfg registering the project (default True)
#   json_record   a key of _JSON_RECORDS (default "in_progress"); None: none
#   extra_tasks   additional (task id, release, status.md text) tuples
#   intake        extra intake files {relative path: text} (overrides too)
#   superseded    the requirements file is already renamed SUPERSEDED
#   tracked       make the project directory a git repository with the
#                 requirements file committed (the rename goes through git mv)
#   rerun         run the CLI twice; the second run is the one recorded
#   rest          the REST twin can reach this scenario: its CLI arguments
#                 carry --confirm, and the body is confirm=true (+ force)
_SCENARIOS: dict[str, dict] = {
    "help": {"args": ["--help"]},
    "usage-missing-key": {"args": ["--project", _PROJECT, "--confirm"]},
    "usage-missing-project": {"args": ["--key", _VERSION, "--confirm"]},
    "usage-key-without-value": {"args": ["--project", _PROJECT, "--key"]},
    "usage-unknown-flag": {"args": ["--project", _PROJECT, "--key", _VERSION, "--bogus"]},
    "usage-positional": {"args": ["--project", _PROJECT, "--key", _VERSION, "extra"]},
    "project-from-environment": {
        "args": ["--key", _VERSION, "--confirm"],
        "env": {"PGAI_PROJECT_NAME": _PROJECT},
    },
    "project-prefix-resolves": {
        "args": ["--project", "unwind", "--key", _VERSION, "--dry-run"],
    },
    "project-not-registered": {
        "args": ["--project", "no-such-project", "--key", _VERSION, "--confirm"],
        "rest": True,
    },
    "invalid-version": {"args": ["--project", _PROJECT, "--key", "0.4", "--confirm"], "rest": True},
    "missing-project-cfg": {"cfg": False, "rest": True},
    "missing-dev-tree-path": {"dev_tree": False, "rest": True},
    "dev-tree-from-environment": {
        "dev_tree": False,
        "args": ["--project", _PROJECT, "--key", _VERSION, "--dry-run"],
        "env": {"PGAI_DEV_TREE_PATH": "{REPO}"},
    },
    "repo-root-override": {
        "dev_tree": False,
        "args": ["--project", _PROJECT, "--key", _VERSION, "--dry-run"],
        "env": {"REPO_ROOT": "{REPO}"},
    },
    "project-dir-missing": {
        "project_dir": False,
        "halt": "global",
        "env": {"REPO_ROOT": "{REPO}"},
    },
    "no-halt": {"halt": None, "rest": True},
    "global-halt-dry-run": {
        "halt": "global",
        "args": ["--project", _PROJECT, "--key", _VERSION, "--dry-run"],
    },
    "shipped-tag": {"tag": True, "rest": True},
    "missing-release-state": {"active": None, "rest": True},
    "active-rc-none": {"active": "none", "rest": True},
    "active-rc-mismatch": {"active": "v9.9.9", "rest": True},
    "active-rc-mismatch-force": {
        "active": "v9.9.9",
        "args": ["--project", _PROJECT, "--key", _VERSION, "--force"],
    },
    "active-rc-mismatch-confirm-force": {
        "active": "v9.9.9",
        "args": ["--project", _PROJECT, "--key", _VERSION, "--confirm", "--force"],
        "rest": True,
    },
    "dry-run": {"args": ["--project", _PROJECT, "--key", _VERSION, "--dry-run"]},
    "dry-run-with-confirm-and-force": {
        "args": ["--project", _PROJECT, "--key", _VERSION, "--dry-run", "--confirm", "--force"],
    },
    "unwind-confirmed": {"rest": True},
    "unwind-force-only": {"args": ["--project", _PROJECT, "--key", _VERSION, "--force"]},
    "unwind-twice": {"rerun": True},
    "unwind-on-rc-branch": {"head": "rc", "rest": True},
    "unwind-no-rc-branch": {"rc_branch": False, "rest": True},
    "unwind-branch-prefix": {"prefix": "ai_", "rest": True},
    "unwind-no-main-branch": {"main_branch": False, "head": "rc", "rest": True},
    "unwind-tracked-requirements": {"tracked": True, "rest": True},
    "requirements-already-superseded": {"superseded": True, "rest": True},
    "json-absent": {"json_record": None, "rest": True},
    "json-already-cancelled": {"json_record": "cancelled", "rest": True},
    "json-unreadable": {"json_record": "unreadable", "rest": True},
    "prompt-answered-yes": {"args": ["--project", _PROJECT, "--key", _VERSION], "stdin": "yes\n"},
    "prompt-answered-padded-yes": {"args": ["--project", _PROJECT, "--key", _VERSION], "stdin": "  yes \n"},
    "prompt-answered-y": {"args": ["--project", _PROJECT, "--key", _VERSION], "stdin": "y\n"},
    "prompt-unanswered": {"args": ["--project", _PROJECT, "--key", _VERSION]},
    "prompt-answer-without-newline": {"args": ["--project", _PROJECT, "--key", _VERSION], "stdin": "yes"},
    "task-without-state-heading": {
        "extra_tasks": (
            ("CODER-20260101-009-no-state", _VERSION, "# Status\n\n## Task\nCODER-20260101-009-no-state\n"),
        ),
        "rest": True,
    },
    "intake-without-status-heading": {
        "intake": {"bugs/BUG-0001-beta.md": "# BUG-0001-beta\n\nno status section here\n"},
        "rest": True,
    },
}


@pytest.fixture(autouse=True)
def _require_git() -> None:
    """Skip the module when git is absent; every scenario builds a repository."""
    require_binary("git", label="cm-unwind-rc")


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
    """A local repository with no remote, holding <prefix>main, <prefix>rc/<V> and feature branches."""
    prefix = spec.get("prefix", "")
    main = f"{prefix}main" if spec.get("main_branch", True) else f"{prefix}trunk"
    rc = f"{prefix}rc/{_VERSION}"
    repo = base / "dev_repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", f"--initial-branch={main}")
    (repo / "README.md").write_text("# fixture\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "Initial commit")
    if spec.get("rc_branch", True):
        _git(repo, "branch", rc)
    # Feature branches: one per task of the RC (orphans the unwind deletes),
    # one for a task of another RC, one with a non-matching shape.
    _git(repo, "branch", "feature/CLAUDE-20260101-001-pending")
    _git(repo, "branch", "feature/CLAUDE-20260101-010-legacy")
    _git(repo, "branch", "feature/CLAUDE-20260101-099-elsewhere")
    _git(repo, "branch", "feature/CODER-20260101-002-working")
    if spec.get("tag"):
        _git(repo, "tag", f"{prefix}{_VERSION}")
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
    root.mkdir(parents=True)
    (root / "logs").mkdir()
    (root / "tmp").mkdir()
    (root / "kanban.cfg").write_text("[paths]\n\n[chain]\npm_mode = automatic\n", encoding="utf-8")
    if spec.get("registered", True):
        (root / "projects.cfg").write_text(
            f"[project:{_PROJECT}]\npriority=1\nenabled=true\n\n[project:other-project]\npriority=2\n",
            encoding="utf-8",
        )
    # A live install carries its own copy of the cm helpers under the root.
    cm_dir = root / "pgai_agent_kanban" / "cm"
    cm_dir.mkdir(parents=True)
    for src in sorted(_CM_PY_SRC.glob("*.py")):
        (cm_dir / src.name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    halt = spec.get("halt", "project")
    if halt == "global":
        (root / "HALT").write_text("", encoding="utf-8")
    if not spec.get("project_dir", True):
        return root

    for sub in ("tasks/queues/plans", "release-state", "requirements", "priority", "bugs", ".discovery-state"):
        (proj / sub).mkdir(parents=True)
    if halt == "project":
        (proj / "HALT").write_text("", encoding="utf-8")
    if spec.get("cfg", True):
        lines = ["[project]", f"project_name = {_PROJECT}"]
        if spec.get("dev_tree", True):
            lines.append(f"dev_tree_path = {repo}")
        lines += ["workflow_type = release", f"branch_prefix = {spec.get('prefix', '')}"]
        if "push" in spec:
            lines.append(f"push_to_remote = {spec['push']}")
        (proj / "project.cfg").write_text("\n".join(lines) + "\n", encoding="utf-8")
    active = spec.get("active", _VERSION)
    if active is not None:
        opened = ("none", "none") if active == "none" else ("2026-01-01T00:00:00+00:00", "CM-20260101-000-open")
        (proj / "release-state.md").write_text(
            _RELEASE_STATE.format(active=active, opened_at=opened[0], opened_by=opened[1]),
            encoding="utf-8",
        )
    record = spec.get("json_record", "in_progress")
    if record is not None:
        (proj / "release-state" / f"{_VERSION}.json").write_text(_JSON_RECORDS[record], encoding="utf-8")
    for task_id, release, status in (*_TASKS, *spec.get("extra_tasks", ())):
        task_dir = proj / "tasks" / task_id
        task_dir.mkdir()
        (task_dir / "README.md").write_text(
            f"# Task: {task_id}\n\n## Release Version\n{release}\n", encoding="utf-8"
        )
        if status is not None:
            (task_dir / "status.md").write_text(status, encoding="utf-8")
    for name, text in _QUEUES.items():
        (proj / "tasks" / "queues" / name).write_text(text, encoding="utf-8")
    for name, text in _PLAN_MARKERS.items():
        (proj / "tasks" / "queues" / "plans" / name).write_text(text, encoding="utf-8")
    requirements_name = _REQUIREMENTS_NAME
    if spec.get("superseded"):
        requirements_name = requirements_name[:-3] + ".SUPERSEDED-on-cancel-2026-01-01T00:00:00+00:00.md"
    (proj / "requirements" / requirements_name).write_text(_requirements_text(root), encoding="utf-8")
    (proj / "requirements" / "v0.5.0-bugfix-bundle-20260102.md").write_text(
        "# v0.5.0\n\n## Bundled Items\n- BUG-0002-gamma.md\n", encoding="utf-8"
    )
    for rel, text in {**_INTAKE_FILES, **spec.get("intake", {})}.items():
        (proj / rel).write_text(text, encoding="utf-8")
    for rel in _DISCOVERY_FILES:
        path = proj / ".discovery-state" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("cache\n", encoding="utf-8")
    if spec.get("tracked"):
        _git(proj, "init", "-q", "--initial-branch=main")
        _git(proj, "add", f"requirements/{_REQUIREMENTS_NAME}")
        _git(proj, "commit", "-q", "-m", "Track the requirements file")
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
_FULL_SHA_RE = re.compile(r"\b[0-9a-f]{40}\b")
_LOCAL_TS_RE = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d")
_UTC_RE = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")
_BACKUP_TS_RE = re.compile(r"backup-\d{8}T\d{6}")
# The fixture's own fixed timestamps, which are data rather than clock readings.
_FIXED_TIMES = ("2026-01-01T00:00:00+00:00", "2026-01-02T03:04:05+00:00", "2026-01-01T01:00:00Z")


def normalise(text: str, root: pathlib.Path, repo: pathlib.Path) -> str:
    """Replace run-specific paths, commit ids and clock readings with tokens."""
    text = text.replace(str(repo), "<REPO>").replace(str(root), "<ROOT>")
    text = _SHA_RE.sub("(was <SHA>)", text)
    text = _FULL_SHA_RE.sub("<SHA>", text)
    text = _BACKUP_TS_RE.sub("backup-<BTS>", text)
    for index, fixed in enumerate(_FIXED_TIMES):
        text = text.replace(fixed, f"<FIXED{index}>")
    text = _LOCAL_TS_RE.sub("<TS>", text)
    text = _UTC_RE.sub("<UTC>", text)
    for index, fixed in enumerate(_FIXED_TIMES):
        text = text.replace(f"<FIXED{index}>", fixed)
    return text


def _files_under(top: pathlib.Path, root: pathlib.Path, repo: pathlib.Path) -> dict:
    files: dict = {}
    if not top.is_dir():
        return files
    for path in sorted(top.rglob("*")):
        if ".git" in path.relative_to(top).parts:
            continue
        if path.is_file():
            rel = normalise(str(path.relative_to(root)), root, repo)
            files[rel] = normalise(path.read_text(encoding="utf-8", errors="replace"), root, repo)
    return files


def snapshot(root: pathlib.Path, repo: pathlib.Path) -> dict:
    """Every file unwind-rc may touch or must leave alone, the backup, and the branch list."""
    proj = root / "projects" / _PROJECT
    refs = _git(repo, "for-each-ref", "--format=%(refname)", "refs/heads", "refs/tags")
    head = subprocess.run(
        ["git", "-C", str(repo), "symbolic-ref", "--short", "HEAD"],
        capture_output=True, text=True, check=False,
    ).stdout.strip()
    tracked = ""
    if (proj / ".git").is_dir():
        tracked = _git(proj, "status", "--porcelain")
    return {
        "files": _files_under(proj, root, repo),
        "backup": _files_under(root / "tmp", root, repo),
        "refs": refs.splitlines(),
        "head": head,
        "tracked": normalise(tracked, root, repo),
    }


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
    return ["--project", _PROJECT, "--key", _VERSION, "--confirm"]


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


# Scenarios whose behaviour changed on purpose. The script ran under
# ``set -e``, so a malformed file stopped it part-way with whatever exit
# code the failing command returned; the port warns about the file, leaves
# it alone and finishes the unwind. Their recorded pre-port outcome stays in
# the golden file's "scenarios" next to the expected one under "changed".
_CHANGED = {
    "task-without-state-heading": (
        "a status.md with no ## State heading stopped the script at step d "
        "(exit 3) after its warning; the port warns, leaves the file whole and "
        "finishes steps d-l"
    ),
    "intake-without-status-heading": (
        "an intake file with no ## Status heading stopped the script silently "
        "at step i (exit 1); the port warns that ## Status could not be reset "
        "and finishes steps i-l"
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


def test_route_and_cli_reach_cm_unwind_unwind_rc(tmp_path: pathlib.Path) -> None:
    """The route calls unwind_rc with no process; the script reaches it via pp_run_ops.

    The scenario is the malformed-version refusal: unwind_rc decides it
    before its first git command, so the route's run starts no process at
    all, and the CLI's run reaches the function and refuses the same way.
    """
    from tests.fixtures.route_cli_parity import assert_route_cli_parity, build_api_client

    spec = _SCENARIOS["invalid-version"]
    rest_root, rest_repo = build_sandbox(tmp_path / "rest", "invalid-version")
    cli_root, cli_repo = build_sandbox(tmp_path / "cli", "invalid-version")
    result = assert_route_cli_parity(
        "unwind-rc",
        build_api_client(rest_root),
        json={"project": _PROJECT, "key": "0.4", "confirm": True},
        cli_args=spec["args"],
        env=_cli_env(tmp_path / "cli", cli_root, cli_repo, spec),
        scratch_dir=tmp_path / "scratch",
    )

    assert result.target.dotted == "pgai_agent_kanban.cm.unwind.unwind_rc"
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
    assert body["error"] == "unwind_rc.refused"


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


@pytest.mark.parametrize("name", sorted(_CHANGED))
def test_a_changed_scenario_matches_the_pre_port_run_up_to_where_it_stopped(name: str) -> None:
    """Everything the script printed before it stopped is printed unchanged; then the unwind finishes."""
    golden = _golden()
    before = golden["scenarios"][name]
    after = golden["changed"][name]["outcome"]
    assert golden["changed"][name]["reason"] == _CHANGED[name]
    assert before["rc"] != 0 and after["rc"] == 0
    assert after["stdout"].startswith(before["stdout"])
    assert after["stderr"].startswith(before["stderr"])
    assert "unwind-rc.sh complete — all steps (a-l) finished." in after["stdout"]


def test_dry_run_changes_nothing(tmp_path: pathlib.Path) -> None:
    """The plan names every store and the run leaves every file and branch as built."""
    root, repo = build_sandbox(tmp_path / "built", "dry-run")
    untouched = snapshot(root, repo)
    outcome = run_scenario(tmp_path / "run", "dry-run")
    assert outcome["rc"] == 0
    assert outcome["stdout"].endswith("Dry run complete.  No changes were made.\n")
    assert outcome["after"]["files"] == untouched["files"]
    assert outcome["after"]["refs"] == untouched["refs"]
    assert outcome["after"]["backup"] == {}


# ---------------------------------------------------------------------------
# 3. The REST twin leaves what the CLI leaves
# ---------------------------------------------------------------------------


def _rest_body(spec: dict) -> dict:
    args = spec.get("args", _default_args())
    body = {
        "project": args[args.index("--project") + 1],
        "key": args[args.index("--key") + 1],
        "confirm": True,
    }
    if "--force" in args:
        body["force"] = True
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
    assert "--confirm" in spec.get("args", _default_args()), "a REST scenario is a confirmed one"
    for var in ("PGAI_DEV_TREE_PATH", "REPO_ROOT", "PGAI_PROJECT_NAME"):
        monkeypatch.delenv(var, raising=False)
    root, repo = build_sandbox(tmp_path, name)
    monkeypatch.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(root / "tmp"))
    response = build_api_client(root).post("/operations/unwind-rc", json=_rest_body(spec))
    body = response.json()
    recorded = expected(name)

    assert body["exit_code"] == recorded["rc"], body
    refused_status = _REST_REFUSAL_STATUS.get(name, 500)
    assert response.status_code == (200 if recorded["rc"] == 0 else refused_status), body
    assert normalise(body["stdout"], root, repo) == recorded["stdout"]
    assert normalise(body["stderr"], root, repo) == recorded["stderr"]
    assert snapshot(root, repo) == recorded["after"]


# ---------------------------------------------------------------------------
# 4. The push gate under the git network stub
# ---------------------------------------------------------------------------

_PUSH_GATE_SKIP = "skipped-unresolved-project"
_RC_REF = f"refs/heads/rc/{_VERSION}"


def _gate_skip_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if _PUSH_GATE_SKIP in line]


class _UnwindGateCase:
    """One push-gate shape: sandbox root and repository, the git network stub on PATH."""

    _SHAPES = {
        "push-default": {},
        "local-only": {"push": "false"},
        "no-project-cfg": {"cfg": False},
        "not-registered": {"registered": False},
        "no-dev-tree-path": {"dev_tree": False},
        "registered-elsewhere": {},
    }

    def __init__(self, tmp_path: pathlib.Path, shape: str, env: dict) -> None:
        if shape not in self._SHAPES:
            raise ValueError(f"unknown push-gate shape {shape!r}")
        spec = dict(self._SHAPES[shape])
        self.base = tmp_path / "sandbox"
        self.base.mkdir(parents=True)
        self.repo = _build_repo(self.base, spec)
        self.root = _seed_root(self.base, self.repo, spec)
        if shape == "registered-elsewhere":
            (self.root / "projects.cfg").write_text("[project:another-project]\npriority=1\n", encoding="utf-8")
        self.spec = {"env": env}
        self.stub_log = tmp_path / "git-network-calls.log"
        self.stub_bin = tmp_path / "stub-bin"
        # A pushing run must get past its origin check to reach the remote
        # delete it is judged on; the stub logs both and contacts nothing.
        install_git_network_stub(self.stub_bin, self.stub_log, succeed_verbs=("ls-remote", "push"))

    def run(self, *extra: str) -> subprocess.CompletedProcess:
        env = _cli_env(self.base, self.root, self.repo, self.spec)
        env["PATH"] = f"{self.stub_bin}{os.pathsep}{env['PATH']}"
        cwd = self.base / "elsewhere"
        cwd.mkdir(exist_ok=True)
        return subprocess.run(
            ["bash", str(_SCRIPT), "--project", _PROJECT, "--key", _VERSION, *extra],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, env=env,
            cwd=str(cwd), timeout=_TIMEOUT_SECONDS,
        )

    def network_calls(self) -> list[str]:
        if not self.stub_log.is_file():
            return []
        return [line for line in self.stub_log.read_text(encoding="utf-8").splitlines() if line]

    def rc_branch_exists(self) -> bool:
        return _RC_REF in _git(self.repo, "for-each-ref", "--format=%(refname)", "refs/heads").split()

    def active_rc(self) -> str:
        text = (self.root / "projects" / _PROJECT / "release-state.md").read_text(encoding="utf-8")
        return text.split("## Active RC\n", 1)[1].split("\n", 1)[0]


def _both(run: subprocess.CompletedProcess) -> str:
    return run.stdout + run.stderr


_REPO_BY_BOTH_NAMES = {"REPO_ROOT": "{REPO}", "PGAI_DEV_TREE_PATH": "{REPO}"}
_REPO_BY_ENV = {"PGAI_DEV_TREE_PATH": "{REPO}"}
_LOCAL_ONLY_POLICY = (
    "[unwind-rc] Push policy: push_to_remote=false — skipping origin operations. Local cleanup only."
)

# (shape, missing-piece token, environment naming the repository)
_UNRESOLVED_SHAPES = [
    ("no-project-cfg", "missing project-cfg", _REPO_BY_BOTH_NAMES),
    ("not-registered", "missing registration", _REPO_BY_ENV),
    ("no-dev-tree-path", "missing dev-tree-path", _REPO_BY_ENV),
]
_UNRESOLVED_IDS = [shape for shape, _token, _env in _UNRESOLVED_SHAPES]


class TestUnwindPushGate:
    """unwind-rc touches origin only for a project the shared push resolver answers push."""

    def test_push_to_remote_false_stays_local(self, tmp_path: pathlib.Path) -> None:
        case = _UnwindGateCase(tmp_path, "local-only", {})
        run = case.run("--confirm")

        assert run.returncode == 0, _both(run)
        assert case.network_calls() == [], _both(run)
        assert _gate_skip_lines(_both(run)) == []
        assert _LOCAL_ONLY_POLICY in run.stdout.splitlines()
        assert f"     - Delete remote branch: origin/rc/{_VERSION}  [skipped — push_to_remote=false]" in run.stdout
        assert (
            f"  [push_to_remote=false] skipping origin push for {_PROJECT}: "
            f"git push origin --delete rc/{_VERSION} (Step c)"
        ) in run.stdout.splitlines()
        assert "unwind-rc.sh complete — all steps (a-l) finished." in run.stdout
        assert not case.rc_branch_exists()
        assert case.active_rc() == "none"

    @pytest.mark.parametrize(("shape", "token", "env"), _UNRESOLVED_SHAPES, ids=_UNRESOLVED_IDS)
    def test_unresolved_project_contacts_no_remote_and_unwinds_locally(
        self, tmp_path: pathlib.Path, shape: str, token: str, env: dict
    ) -> None:
        case = _UnwindGateCase(tmp_path, shape, env)
        run = case.run("--confirm")

        assert run.returncode == 0, f"{shape}: unexpected exit\n{_both(run)}"
        assert case.network_calls() == [], f"{shape}: unwind-rc made a git network call\n{_both(run)}"
        skips = _gate_skip_lines(_both(run))
        assert len(skips) == 1 and token in skips[0], (
            f"{shape}: expected one {_PUSH_GATE_SKIP} line naming {token!r}\n{_both(run)}"
        )
        assert skips[0].startswith(f"[unwind-rc] {_PUSH_GATE_SKIP}: project '{_PROJECT}': {token}: "), skips[0]
        assert skips[0].endswith("; nothing pushed and no remote contacted"), skips[0]
        assert _LOCAL_ONLY_POLICY not in run.stdout
        assert (
            f"  [unresolved project] skipping origin push for {_PROJECT}: "
            f"git push origin --delete rc/{_VERSION} (Step c)"
        ) in run.stdout.splitlines()
        assert "unwind-rc.sh complete — all steps (a-l) finished." in run.stdout
        assert not case.rc_branch_exists()
        assert case.active_rc() == "none"

    def test_no_project_cfg_without_repo_root_refuses_after_its_skip_line(self, tmp_path: pathlib.Path) -> None:
        """PGAI_DEV_TREE_PATH alone names no repository for a project with no project.cfg."""
        case = _UnwindGateCase(tmp_path, "no-project-cfg", _REPO_BY_ENV)
        run = case.run("--confirm")

        assert run.returncode == 1, _both(run)
        assert case.network_calls() == [], _both(run)
        skips = _gate_skip_lines(run.stdout)
        assert len(skips) == 1 and "missing project-cfg" in skips[0], run.stdout
        assert "ERROR: could not load project config for" in run.stderr
        assert case.rc_branch_exists() and case.active_rc() == _VERSION

    def test_a_project_absent_from_projects_cfg_refuses_without_a_network_call(
        self, tmp_path: pathlib.Path
    ) -> None:
        case = _UnwindGateCase(tmp_path, "registered-elsewhere", _REPO_BY_ENV)
        run = case.run("--confirm")

        assert run.returncode == 1, _both(run)
        assert case.network_calls() == [], _both(run)
        assert "ERROR: could not resolve project context for" in run.stderr
        assert case.rc_branch_exists()

    def test_registered_project_without_push_key_still_deletes_on_origin(self, tmp_path: pathlib.Path) -> None:
        case = _UnwindGateCase(tmp_path, "push-default", {})
        run = case.run("--confirm")

        assert run.returncode == 0, _both(run)
        calls = [line.split() for line in case.network_calls()]
        ls_remote = ["git", "-C", str(case.repo), "ls-remote", "--exit-code", "--heads", "origin", f"rc/{_VERSION}"]
        assert ls_remote in calls, calls
        assert ["git", "-C", str(case.repo), "push", "origin", "--delete", f"rc/{_VERSION}"] in calls, calls
        assert _gate_skip_lines(_both(run)) == []
        assert "Push policy" not in run.stdout
        assert f"     - Delete remote branch: origin/rc/{_VERSION}  [EXISTS]" in run.stdout
        assert f"  Remote branch origin/rc/{_VERSION} deleted." in run.stdout
        assert not case.rc_branch_exists()

    @pytest.mark.parametrize(
        ("shape", "env", "skip"),
        [
            ("local-only", {}, "push_to_remote=false"),
            *[(shape, env, "unresolved project") for shape, _token, env in _UNRESOLVED_SHAPES],
        ],
        ids=["local-only", *_UNRESOLVED_IDS],
    )
    def test_a_dry_run_for_a_project_that_does_not_push_makes_no_ls_remote(
        self, tmp_path: pathlib.Path, shape: str, env: dict, skip: str
    ) -> None:
        case = _UnwindGateCase(tmp_path, shape, env)
        run = case.run("--dry-run")

        assert run.returncode == 0, _both(run)
        assert case.network_calls() == [], _both(run)
        assert f"     - Delete remote branch: origin/rc/{_VERSION}  [skipped — {skip}]" in run.stdout
        assert run.stdout.endswith("Dry run complete.  No changes were made.\n")
        assert case.rc_branch_exists()

    def test_a_dry_run_for_a_project_that_pushes_checks_origin(self, tmp_path: pathlib.Path) -> None:
        """The control for the test above: the plan's origin check is what the gate suppresses."""
        case = _UnwindGateCase(tmp_path, "push-default", {})
        run = case.run("--dry-run")

        assert run.returncode == 0, _both(run)
        verbs = [line.split()[3] for line in case.network_calls()]
        assert verbs == ["ls-remote"], case.network_calls()
        assert case.rc_branch_exists()
