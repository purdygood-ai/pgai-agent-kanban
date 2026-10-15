"""
test_deposit_refuses_tree_without_stamp_helper.py
==================================================
Behavioral tests: both deposit scripts refuse a source tree that has no
``team/scripts/lib/version_stamp.sh`` before they deposit a single file.

WHAT IS TESTED
--------------
The VERSION stamp that ends every deposit calls ``stamp_version_files``,
defined only in the deposited tree's ``team/scripts/lib/version_stamp.sh``.
A tree without that helper must be refused up front, through the shared
dual-channel error contract, rather than deposited and then left with the
old VERSION when the stamp step cannot run.

1. **upgrade.sh** — run from a helper-less tree against an existing
   sandbox root, it exits non-zero, emits ``error`` / ``message`` /
   ``remedy`` with the message naming the missing helper's path and a
   remedy free of angle-bracket placeholders, and leaves every file under
   the root (VERSION, VERSION_DETAIL, scripts/) byte-for-byte unchanged.

2. **install.sh** — run from the same tree into the same kind of root
   (``--force`` so the existing-install guard does not answer first), it
   behaves the same way.

FIXTURE DESIGN
--------------
The source tree is an archive-style export of this repository: every
tracked file copied from the working tree (``git ls-files``), no ``.git``
of either shape, with ``team/scripts/lib/version_stamp.sh`` deleted. The
scripts under test are therefore the real ``install.sh`` and
``team/scripts/upgrade.sh`` as they stand on disk, and a run that got past
the refusal would deposit the whole tree -- which the root snapshot would
catch.

Every run happens in the project's full sandbox: the environment is built
from an explicit set (``build_scrubbed_env``) with KANBAN_ROOT,
PGAI_AGENT_KANBAN_ROOT_PATH and PGAI_AGENT_KANBAN_TEMP_DIR pointed at the
sandbox and PGAI_TASKS_DIR / PGAI_PROJECT_ROOT absent, HOME is a scratch
directory, upgrade.sh gets ``--wake-tier none`` and install.sh gets
``--no-system-cron``, so nothing reaches the live root or the OS crontab.
Each run starts in its own process group and the whole group is killed if
it outlives its timeout.
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import signal
import subprocess

import pytest

from pgai_agent_kanban.lib.error_contract import parse_error_contract
from tests.fixtures.binary_guard import require_binary
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent
_REPO_ROOT = _TEAM_DIR.parent
_REAL_INSTALL_SH = _REPO_ROOT / "install.sh"
_REAL_UPGRADE_SH = _TEAM_DIR / "scripts" / "upgrade.sh"

_STAMP_HELPER_RELATIVE = pathlib.Path("team/scripts/lib/version_stamp.sh")

# The placeholder shape team/scripts/lint_runnable_remedy.py rejects.
_PLACEHOLDER_RE = re.compile(r"<[^<>]+>")

# A deposit that should be refused finishes in a few seconds; one that got
# past the refusal deposits the whole tree. The bound only stops a hang.
_RUN_TIMEOUT_SECONDS = 600

_SEEDED_VERSION = "v1.0.0\n"
_SEEDED_VERSION_DETAIL = "ai_v1.0.0  deposit-sha=seeded\n"
_SEEDED_SCRIPT = "#!/usr/bin/env bash\necho seeded\n"

pytestmark = pytest.mark.skipif(
    not (_REAL_INSTALL_SH.is_file() and _REAL_UPGRADE_SH.is_file() and (_REPO_ROOT / ".git").exists()),
    reason=(
        f"needs a git checkout of the repository holding install.sh and "
        f"team/scripts/upgrade.sh (looked under {_REPO_ROOT})"
    ),
)


@pytest.fixture(scope="module")
def helper_less_tree(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    """An archive-style export of this repository without the stamp helper.

    Read-only for every test in this module: neither deposit script writes
    into its source tree, so one export serves both runs.
    """
    require_binary("git", label="deposit-refuses-tree-without-stamp-helper")
    listing = subprocess.run(
        ["git", "-C", str(_REPO_ROOT), "ls-files", "-z"],
        capture_output=True,
        text=True,
        check=True,
        cwd=str(_REPO_ROOT),
    )
    tree = tmp_path_factory.mktemp("export") / "source-tree"
    for relative in filter(None, listing.stdout.split("\0")):
        source = _REPO_ROOT / relative
        if not source.is_file():
            continue  # tracked but deleted in the working tree
        destination = tree / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    (tree / _STAMP_HELPER_RELATIVE).unlink()
    assert not (tree / ".git").exists(), "the export must not be a git checkout"
    assert (tree / "install.sh").is_file(), f"export is missing install.sh under {tree}"
    return tree


def _seed_root(root: pathlib.Path) -> None:
    """Lay down an existing install's identity files and one script."""
    (root / "scripts").mkdir(parents=True)
    (root / "VERSION").write_text(_SEEDED_VERSION, encoding="utf-8")
    (root / "VERSION_DETAIL").write_text(_SEEDED_VERSION_DETAIL, encoding="utf-8")
    (root / "scripts" / "seeded.sh").write_text(_SEEDED_SCRIPT, encoding="utf-8")


def _snapshot(root: pathlib.Path) -> dict[str, bytes]:
    """Every regular file under <root>, keyed by its root-relative path."""
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _sandbox_env(tmp_path: pathlib.Path, root: pathlib.Path) -> dict:
    """The full sandbox environment: all five live-install names handled."""
    home = tmp_path / "home"
    temp = tmp_path / "pgai_temp"
    home.mkdir()
    temp.mkdir()
    return build_scrubbed_env(
        home_dir=home,
        extra_env={
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(temp),
        },
    )


def _run(argv: list[str], *, env: dict, cwd: pathlib.Path) -> subprocess.CompletedProcess:
    """Run <argv> in its own process group; kill the whole group on a hang."""
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        cwd=str(cwd),
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=_RUN_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        proc.communicate()
        raise
    return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)


def _diagnostic(result: subprocess.CompletedProcess) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _assert_refused_before_deposit(
    result: subprocess.CompletedProcess,
    *,
    expected_error: str,
    missing_helper: pathlib.Path,
    root: pathlib.Path,
    before: dict[str, bytes],
) -> None:
    """The run refused through the error contract and touched nothing."""
    assert result.returncode != 0, (
        "a source tree without the stamp helper must be refused (non-zero exit)\n"
        + _diagnostic(result)
    )
    assert "command not found" not in result.stdout + result.stderr, (
        "the refusal must come before the stamp step, not from calling a "
        "function the missing helper would have defined\n" + _diagnostic(result)
    )

    contract = parse_error_contract(result.stderr)
    assert contract is not None, (
        "the refusal must carry the error / message / remedy contract on stderr\n"
        + _diagnostic(result)
    )
    remedy = contract["remedy"].splitlines()[0].strip() if contract["remedy"] else ""
    assert contract["error"] == expected_error, (
        f"expected error key {expected_error!r}, got {contract['error']!r}\n"
        + _diagnostic(result)
    )
    assert str(missing_helper) in contract["message"], (
        f"the message must name the missing helper path {missing_helper}\n"
        + _diagnostic(result)
    )
    assert remedy, "the remedy field must not be blank\n" + _diagnostic(result)
    assert not _PLACEHOLDER_RE.search(remedy), (
        f"the remedy must be runnable as printed (no angle-bracket placeholder): {remedy!r}\n"
        + _diagnostic(result)
    )

    after = _snapshot(root)
    assert after == before, (
        "nothing may be deposited before the refusal; files under the root changed: "
        f"added={sorted(set(after) - set(before))} "
        f"removed={sorted(set(before) - set(after))} "
        f"modified={sorted(k for k in set(before) & set(after) if before[k] != after[k])}\n"
        + _diagnostic(result)
    )


def test_upgrade_refuses_source_tree_without_stamp_helper_before_depositing(
    helper_less_tree: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """upgrade.sh from a helper-less tree exits non-zero with the root untouched."""
    root = tmp_path / "kanban_root"
    _seed_root(root)
    before = _snapshot(root)
    foreign_cwd = tmp_path / "elsewhere"
    foreign_cwd.mkdir()

    result = _run(
        [
            "bash",
            str(helper_less_tree / "team" / "scripts" / "upgrade.sh"),
            "--dev-tree",
            str(helper_less_tree),
            "--force",
            "--no-backup",
            "--wake-tier",
            "none",
        ],
        env=_sandbox_env(tmp_path, root),
        cwd=foreign_cwd,
    )

    _assert_refused_before_deposit(
        result,
        expected_error="upgrade.stamp-helper-missing",
        missing_helper=helper_less_tree / _STAMP_HELPER_RELATIVE,
        root=root,
        before=before,
    )


def test_install_refuses_source_tree_without_stamp_helper_before_depositing(
    helper_less_tree: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """install.sh from a helper-less tree exits non-zero with the root untouched."""
    root = tmp_path / "kanban_root"
    _seed_root(root)
    before = _snapshot(root)
    foreign_cwd = tmp_path / "elsewhere"
    foreign_cwd.mkdir()

    result = _run(
        [
            "bash",
            str(helper_less_tree / "install.sh"),
            "--force",
            "--no-system-cron",
        ],
        env=_sandbox_env(tmp_path, root),
        cwd=foreign_cwd,
    )

    _assert_refused_before_deposit(
        result,
        expected_error="install.stamp-helper-missing",
        missing_helper=helper_less_tree / _STAMP_HELPER_RELATIVE,
        root=root,
        before=before,
    )
