"""
test_upgrade_noarg_forward_only.py
==================================
Behavioral tests for upgrade.sh's forward-only rule on the no-argument path.

An upgrade run without a version argument deposits whatever the dev tree has
checked out. It must refuse, before the backup and before anything is
deposited, when that tree's version is OLDER than the installed VERSION, and
must keep upgrading as before when the tree is at an EQUAL or NEWER version.

WHAT IS TESTED
--------------
1. **Older dev tree refused.** Installed vB, dev tree checked out at the older
   tag vA: ``upgrade.sh --force`` exits non-zero, every file under the
   sandbox kanban root is byte-identical afterwards, no backup tarball is
   written, and stderr carries the dual-channel block (stable ``error`` key,
   a ``message`` naming both versions, a ``remedy`` with no placeholder).
2. **The remedy runs as printed.** Pasting the refusal's ``remedy`` into a
   shell checks out the main branch (at the newest tag) and completes the
   upgrade.
3. **Version from the release tag.** A dev tree with no VERSION file is
   identified by ``git describe`` against the release tag pattern, and an
   older one is refused the same way.
4. **Equal and newer allowed.** The dev tree at the installed version, and at
   a newer one, upgrades: exit 0 and VERSION stamped with the deposited
   version.
5. **The depositing phase holds the rule on its own.** Running the dev
   tree's script directly as phase 2 refuses the older tree too.

The fixture tags order differently as text and as versions (v300.9.0 sorts
after v300.10.0 as a string), so a string compare would let the downgrade
through.

SANDBOX
-------
- A tagged source repo, built once per module and only read afterwards: a
  copy of this repository with one commit per fixture tag, each setting the
  committed VERSION, and branch "main" at the newest tag. Each test clones
  it into its own tmp_path and checks out the tag it needs.
- An installed kanban root per test: the real upgrade.sh plus a copy of the
  real scripts/lib/, and a VERSION file holding the "installed" version.
- Every run uses an environment built from an explicit set: HOME inside the
  sandbox, the kanban root and temp dir pointing into it, and
  CLAUDE_CONFIG_DIR, PGAI_PROJECT_NAME, PGAI_PROJECT_ROOT and PGAI_TASKS_DIR
  absent. ``--wake-tier none`` keeps the crontab untouched.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest
from tests.fixtures.binary_guard import require_binary
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent
_REPO_ROOT = _TEAM_DIR.parent
_REAL_UPGRADE_SH = _TEAM_DIR / "scripts" / "upgrade.sh"
_REAL_LIB_DIR = _TEAM_DIR / "scripts" / "lib"

_TIMEOUT = 300

_TAG_OLD = "v300.9.0"
_TAG_MID = "v300.10.0"
_TAG_NEW = "v300.11.0"
_ASCENDING_TAGS = [_TAG_OLD, _TAG_MID, _TAG_NEW]

_ERROR_KEY = "upgrade.dev-tree-older-than-installed"

_COPY_IGNORE = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", ".pytest_cache")

pytestmark = pytest.mark.skipif(
    not _REAL_UPGRADE_SH.exists(),
    reason=f"Real upgrade.sh not found at {_REAL_UPGRADE_SH}; skipping.",
)


@pytest.fixture(autouse=True)
def _require_git() -> None:
    """Every test here builds and drives a real git repository."""
    require_binary("git", label="upgrade-noarg-forward-only")


def _git(repo: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )


@pytest.fixture(scope="module")
def tagged_source_repo(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    """A read-only tagged copy of this repository; tests clone it, never edit it."""
    repo = tmp_path_factory.mktemp("noarg_forward_only") / "source_repo"
    shutil.copytree(_REPO_ROOT, repo, ignore=_COPY_IGNORE)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "fixture@example.com")
    _git(repo, "config", "user.name", "Fixture Repo")
    for tag in _ASCENDING_TAGS:
        (repo / "VERSION").write_text(f"{tag}\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", f"Set VERSION to {tag}")
        _git(repo, "tag", tag)
    return repo


def _clone_dev_tree(source: pathlib.Path, tmp_path: pathlib.Path, tag: str) -> pathlib.Path:
    """Clone *source* under *tmp_path* and check out *tag* (detached)."""
    dev_tree = tmp_path / "dev_tree"
    subprocess.run(
        ["git", "clone", "-q", str(source), str(dev_tree)],
        capture_output=True,
        text=True,
        check=True,
    )
    _git(dev_tree, "checkout", "-q", tag)
    return dev_tree


def _build_installed_root(tmp_path: pathlib.Path, installed_version: str) -> pathlib.Path:
    """A live-layout root: installed upgrade.sh, its scripts/lib/, a VERSION."""
    root = tmp_path / "kanban_root"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    shutil.copytree(_REAL_LIB_DIR, scripts / "lib", ignore=_COPY_IGNORE)
    shutil.copy2(_REAL_UPGRADE_SH, scripts / "upgrade.sh")
    (scripts / "upgrade.sh").chmod(0o755)
    (root / "kanban.cfg").write_text("[paths]\n", encoding="utf-8")
    (root / "secrets").write_text("FIXTURE_TOKEN=not-a-real-value\n", encoding="utf-8")
    (root / "VERSION").write_text(f"{installed_version}\n", encoding="utf-8")
    return root


def _sandbox_env(tmp_path: pathlib.Path, root: pathlib.Path) -> dict:
    home = tmp_path / "home"
    temp_dir = tmp_path / "kanban_tmp"
    home.mkdir(exist_ok=True)
    temp_dir.mkdir(exist_ok=True)
    return build_scrubbed_env(
        home_dir=home,
        extra_env={
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(temp_dir),
        },
    )


def _run(argv: list[str], env: dict, cwd: pathlib.Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(a) for a in argv],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
        env=env,
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
    )


def _run_noarg_upgrade(
    tmp_path: pathlib.Path, root: pathlib.Path, dev_tree: pathlib.Path
) -> subprocess.CompletedProcess:
    """The operator invocation: the installed script, no version argument."""
    return _run(
        [
            "bash",
            root / "scripts" / "upgrade.sh",
            "--force",
            "--wake-tier",
            "none",
            "--dev-tree",
            dev_tree,
        ],
        _sandbox_env(tmp_path, root),
        tmp_path,
    )


def _snapshot(root: pathlib.Path) -> dict[str, bytes | None]:
    """Every path under *root*: file bytes, or None for a directory."""
    snapshot: dict[str, bytes | None] = {}
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root))
        snapshot[rel] = None if path.is_dir() else path.read_bytes()
    return snapshot


def _error_contract_fields(stderr: str) -> dict[str, str]:
    """The ``error:`` / ``message:`` / ``remedy:`` lines of the emitted block."""
    fields: dict[str, str] = {}
    for line in stderr.splitlines():
        for label in ("error", "message", "remedy"):
            prefix = f"{label}:"
            if line.startswith(prefix):
                fields[label] = line[len(prefix):].strip()
    return fields


def _diagnostic(result: subprocess.CompletedProcess) -> str:
    return (
        f"exit code: {result.returncode}\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )


def _installed_version(root: pathlib.Path) -> str:
    return (root / "VERSION").read_text(encoding="utf-8").strip()


def _assert_refused_unchanged(
    result: subprocess.CompletedProcess,
    root: pathlib.Path,
    before: dict[str, bytes | None],
    dev_version: str,
    installed_version: str,
) -> dict[str, str]:
    assert result.returncode != 0, (
        f"An upgrade from a dev tree at {dev_version} over an install at "
        f"{installed_version} must be refused.\n{_diagnostic(result)}"
    )
    after = _snapshot(root)
    changed = sorted(
        rel for rel in set(before) | set(after) if before.get(rel, b"") != after.get(rel, b"")
    )
    assert not changed, (
        f"The refused upgrade changed paths under the kanban root: {changed[:20]}\n"
        f"{_diagnostic(result)}"
    )
    fields = _error_contract_fields(result.stderr)
    assert fields.get("error") == _ERROR_KEY, (
        f"stderr must carry the error key {_ERROR_KEY!r}; parsed {fields!r}.\n"
        f"{_diagnostic(result)}"
    )
    message = fields.get("message", "")
    assert dev_version in message and installed_version in message, (
        f"The message must name the dev tree version {dev_version} and the "
        f"installed version {installed_version}; got {message!r}"
    )
    return fields


def test_noarg_upgrade_refuses_dev_tree_older_than_installed(
    tagged_source_repo: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """Installed vB, dev tree at vA: refused, nothing written, both versions named."""
    dev_tree = _clone_dev_tree(tagged_source_repo, tmp_path, _TAG_OLD)
    root = _build_installed_root(tmp_path, _TAG_MID)
    before = _snapshot(root)

    result = _run_noarg_upgrade(tmp_path, root, dev_tree)

    fields = _assert_refused_unchanged(result, root, before, _TAG_OLD, _TAG_MID)
    remedy = fields.get("remedy", "")
    assert remedy and "<" not in remedy and ">" not in remedy, (
        f"The remedy must be a command with no placeholder; got {remedy!r}"
    )
    assert str(dev_tree) in remedy and str(root) in remedy, (
        f"The remedy must name this dev tree and kanban root; got {remedy!r}"
    )
    backups = sorted((tmp_path / "home").glob("pgai-kanban-backup-*"))
    assert not backups, (
        f"The refusal must come before the pre-upgrade backup; found {backups}"
    )


def test_noarg_refusal_remedy_completes_the_upgrade_when_run_as_printed(
    tagged_source_repo: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """The printed remedy checks out main (the newest tag) and upgrades."""
    dev_tree = _clone_dev_tree(tagged_source_repo, tmp_path, _TAG_OLD)
    root = _build_installed_root(tmp_path, _TAG_MID)
    refused = _run_noarg_upgrade(tmp_path, root, dev_tree)
    remedy = _error_contract_fields(refused.stderr).get("remedy", "")
    assert refused.returncode != 0 and remedy, (
        f"Setup: the older dev tree must be refused with a remedy.\n{_diagnostic(refused)}"
    )

    result = _run(["bash", "-c", remedy], _sandbox_env(tmp_path, root), tmp_path)

    assert result.returncode == 0, (
        f"The remedy {remedy!r} must complete the upgrade.\n{_diagnostic(result)}"
    )
    assert _installed_version(root) == _TAG_NEW, (
        f"After the remedy VERSION must be {_TAG_NEW}; got {_installed_version(root)!r}"
    )


def test_noarg_upgrade_refuses_older_dev_tree_identified_by_release_tag(
    tagged_source_repo: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """With no VERSION file in the dev tree, git describe supplies its version."""
    dev_tree = _clone_dev_tree(tagged_source_repo, tmp_path, _TAG_OLD)
    (dev_tree / "VERSION").unlink()
    root = _build_installed_root(tmp_path, _TAG_MID)
    before = _snapshot(root)

    result = _run_noarg_upgrade(tmp_path, root, dev_tree)

    _assert_refused_unchanged(result, root, before, _TAG_OLD, _TAG_MID)


@pytest.mark.parametrize(
    ("installed_version", "dev_tree_tag"),
    [(_TAG_MID, _TAG_MID), (_TAG_OLD, _TAG_NEW)],
    ids=["equal-version", "newer-version"],
)
def test_noarg_upgrade_allows_dev_tree_equal_to_or_newer_than_installed(
    tagged_source_repo: pathlib.Path,
    tmp_path: pathlib.Path,
    installed_version: str,
    dev_tree_tag: str,
) -> None:
    """An equal or newer dev tree upgrades: exit 0, VERSION is the deposited version."""
    dev_tree = _clone_dev_tree(tagged_source_repo, tmp_path, dev_tree_tag)
    root = _build_installed_root(tmp_path, installed_version)

    result = _run_noarg_upgrade(tmp_path, root, dev_tree)

    assert result.returncode == 0, (
        f"An upgrade from {dev_tree_tag} over {installed_version} must succeed.\n"
        f"{_diagnostic(result)}"
    )
    assert _installed_version(root) == dev_tree_tag, (
        f"VERSION must be stamped {dev_tree_tag}; got {_installed_version(root)!r}"
    )


def test_phase2_body_refuses_older_dev_tree_without_phase1(
    tagged_source_repo: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """The dev tree's own script, run directly as phase 2, refuses the older tree."""
    dev_tree = _clone_dev_tree(tagged_source_repo, tmp_path, _TAG_OLD)
    root = _build_installed_root(tmp_path, _TAG_MID)
    before = _snapshot(root)

    result = _run(
        [
            "bash",
            dev_tree / "team" / "scripts" / "upgrade.sh",
            "--phase2",
            "--phase2-protocol",
            "1",
            "--kanban-root",
            root,
            "--no-backup",
            "--force",
            "--wake-tier",
            "none",
        ],
        _sandbox_env(tmp_path, root),
        tmp_path,
    )

    _assert_refused_unchanged(result, root, before, _TAG_OLD, _TAG_MID)
