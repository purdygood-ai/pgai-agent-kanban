"""
test_version_stamp_split.py
============================
Behavioral acceptance tests for the VERSION / VERSION_DETAIL stamp split.

WHAT IS TESTED
--------------
This file covers the stamp behaviours listed below:

1. **tag-exact deposit** — When the dev tree HEAD is at an exact tag, VERSION
   equals the clean tag (e.g. ``ai_vX.Y.Z``) and VERSION_DETAIL carries the
   same tag string plus the deposit SHA.

2. **tag+1 deposit** — When the dev tree HEAD is one commit past a tag (a
   polish commit), VERSION equals the CLEAN tag (no describe suffix), while
   VERSION_DETAIL carries the full ``<tag>-1-g<sha>`` describe string and the
   deposit SHA.  This is the load-bearing assertion: the clean-VERSION invariant
   holds even when the deposit is not tag-exact.

3. **--stamp-version override** — When the caller passes an explicit stamp, it
   is written to VERSION verbatim.  VERSION_DETAIL is NOT written (the explicit
   value is clean by definition).

4. **Divergence advisory** — The staged-vs-published advisory message prints
   in the tag+1 case.  This regression-locks the advisory: the stamp split must
   not suppress the advisory that informs the operator of a non-exact deposit.

5. **Status-bar renderer** — The status-right.sh renderer outputs the clean
   VERSION value when the kanban root contains the split-written VERSION file.
   The renderer is NOT modified; it reads VERSION directly.

6. **One shared implementation** — install.sh and upgrade.sh both call
   ``stamp_version_files`` from ``team/scripts/lib/version_stamp.sh``.  The
   split write logic is not duplicated.

7. **Installed tag names VERSION_DETAIL** — When the caller passes the exact
   tag it laid down (a version-targeted install), VERSION_DETAIL starts with
   that tag even with no branch prefix supplied and an older bare ``v`` tag
   reachable, and even when the source is a detached worktree whose ``.git``
   is a pointer file.

8. **Plain stamp prefers a prefixed tag** — With neither a branch prefix
   nor an installed tag (a plain install from a checkout), VERSION_DETAIL
   describes from the nearest prefixed release tag (``ai_v1.1.0``) rather
   than an older bare ``v`` tag, both at the tag and one commit past it.

9. **Targeted-upgrade call shape** — A detached worktree source with a
   branch prefix and the resolved tag (how a version-targeted upgrade
   calls the helper) writes VERSION_DETAIL starting with that tag.

10. **Worktree source without an installed tag** — A linked-worktree source
    (``.git`` is a pointer file) stamped with no installed tag, both in a
    non-targeted upgrade's shape (branch prefix only) and in a plain
    install's shape (nothing passed), replaces a stale pre-seeded
    VERSION_DETAIL with one naming the tag at the worktree's HEAD.

11. **Non-git source** — A source tree with no ``.git`` at all still has its
    committed VERSION copied, gets no VERSION_DETAIL written, and does not
    keep a stale pre-seeded VERSION_DETAIL naming another release.

12. **--stamp-version keeps no stale detail** — An override deposit into a
    root pre-seeded with an older release's VERSION_DETAIL writes VERSION
    verbatim and leaves no VERSION_DETAIL behind, from a git source and from
    a source with no ``.git``.

13. **upgrade.sh from a dev tree with no .git** — The real upgrade.sh, run
    against a sandbox root from a dev tree that is not a git checkout,
    writes the ``--stamp-version`` value, or without it copies the tree's
    committed VERSION, clears the previous deposit's VERSION_DETAIL either
    way, and narrates the VERSION it wrote.

FIXTURE DESIGN
--------------
Tests (1), (2), and (4) require a real git repository with tags and commits.
Each test builds a throwaway git repo under ``tmp_path`` (pytest-managed,
respects PGAI_AGENT_KANBAN_TEMP_DIR).

Tests (1)–(4) exercise the shared helper ``team/scripts/lib/version_stamp.sh``
via bash directly (the same pattern as test_version_sh_bash.py).

Test (4) additionally exercises upgrade.sh with a synthetic sandbox (same
pattern as test_upgrade_fossil_advisory.py) to confirm the advisory appears in
the upgrade output.

Test (13) runs the real upgrade.sh the same way, against a dev tree with no
``.git`` at all, in an environment built from an explicit set with every
live-install root variable pointed at the sandbox or absent.

Test (5) exercises ``team/scripts/dashboard/status-right.sh`` in minimal mode.

Test (6) is a source-code inspection (grep) — not a behavioral run.

NAMING CONVENTION
-----------------
Function names describe the behavior under test.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import textwrap
from collections.abc import Callable

import pytest

from tests.unit.shell_harness import run_bash
from tests.fixtures.binary_guard import require_binary
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env


@pytest.fixture(autouse=True)
def _require_git() -> None:
    """Skip every test in this module when git is not on PATH.

    Every test here builds, inspects, or drives a real git repository; a
    module-wide autouse guard keeps that requirement in one place instead
    of repeating a per-test check at each git-dependent call site.
    """
    require_binary("git", label="version-stamp-split")

# ---------------------------------------------------------------------------
# Paths to the real scripts under test
# ---------------------------------------------------------------------------

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_LIB_DIR = _SCRIPTS_DIR / "lib"

_VERSION_STAMP_SH = _LIB_DIR / "version_stamp.sh"
_STATUS_RIGHT_SH = _SCRIPTS_DIR / "dashboard" / "status-right.sh"
_REAL_UPGRADE_SH = _SCRIPTS_DIR / "upgrade.sh"
_REAL_ARGPARSE_SH = _LIB_DIR / "argparse.sh"
_REAL_ENV_BOOTSTRAP_SH = _LIB_DIR / "env_bootstrap.sh"
_REAL_INI_PARSER_SH = _LIB_DIR / "ini_parser.sh"
_REAL_PROJECT_PATHS_SH = _LIB_DIR / "project_paths.sh"
_REAL_ERROR_CONTRACT_SH = _LIB_DIR / "error_contract.sh"
_REAL_TEMP_SH = _LIB_DIR / "temp.sh"
_REAL_VERSION_STAMP_SH = _LIB_DIR / "version_stamp.sh"
_REAL_SUBAGENT_DEPLOY_SH = _LIB_DIR / "subagent_deploy.sh"
_REAL_SCRIPT_COMMON_SH = _LIB_DIR / "script_common.sh"


# ---------------------------------------------------------------------------
# Git fixture helpers
# ---------------------------------------------------------------------------


def _make_git_repo(tmp_path: pathlib.Path, subdir: str = "repo") -> pathlib.Path:
    """Create a minimal git repo with one initial commit.

    Returns the repo root.
    """
    repo = tmp_path / subdir
    repo.mkdir(parents=True, exist_ok=True)

    def _git(*args: str) -> str:
        r = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            check=True,
        )
        return r.stdout.strip()

    _git("init", "-b", "main")
    _git("config", "user.email", "test@example.com")
    _git("config", "user.name", "Test Fixture")
    return repo


def _git_cmd(repo: pathlib.Path, *args: str) -> str:
    r = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return r.stdout.strip()


def _build_tag_exact_repo(tmp_path: pathlib.Path) -> tuple[pathlib.Path, str]:
    """Build a repo where HEAD is at an exact tag.

    Returns (repo_path, tag_name).

    The tag uses the 'v[0-9]*' shape so it is matched by the --match filter
    in stamp_version_files (which uses --match 'v[0-9]*' to exclude alias tags
    like 'latest').
    """
    repo = _make_git_repo(tmp_path, "tag_exact_repo")
    (repo / "v.txt").write_text("initial\n", encoding="utf-8")
    _git_cmd(repo, "add", "v.txt")
    _git_cmd(repo, "commit", "-m", "Initial commit")
    tag = "v1.19.0"
    _git_cmd(repo, "tag", tag)
    return repo, tag


def _build_tag_plus_one_repo(tmp_path: pathlib.Path) -> tuple[pathlib.Path, str, str]:
    """Build a repo where HEAD is one commit past a tag.

    Returns (repo_path, base_tag, full_describe).
    full_describe will be '<tag>-1-g<sha>'.

    The tag uses the 'v[0-9]*' shape so it is matched by the --match filter
    in stamp_version_files (which uses --match 'v[0-9]*' to exclude alias tags
    like 'latest').
    """
    repo = _make_git_repo(tmp_path, "tag_plus_one_repo")
    (repo / "v.txt").write_text("initial\n", encoding="utf-8")
    _git_cmd(repo, "add", "v.txt")
    _git_cmd(repo, "commit", "-m", "Initial commit")
    tag = "v1.19.0"
    _git_cmd(repo, "tag", tag)

    # One polish commit past the tag.
    (repo / "polish.txt").write_text("polish commit\n", encoding="utf-8")
    _git_cmd(repo, "add", "polish.txt")
    _git_cmd(repo, "commit", "-m", "Polish commit after tag")

    full_describe = _git_cmd(repo, "describe", "--tags", "--match", "v[0-9]*")
    return repo, tag, full_describe


# ---------------------------------------------------------------------------
# Test 1: tag-exact deposit
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _VERSION_STAMP_SH.exists(),
    reason=f"version_stamp.sh not found at {_VERSION_STAMP_SH}",
)
def test_tag_exact_deposit_writes_clean_tag_to_VERSION(
    tmp_path: pathlib.Path,
) -> None:
    """At a tag-exact checkout, VERSION equals the clean tag with no describe suffix.

    The clean tag and the full describe are identical at an exact tag, so
    VERSION and the first token of VERSION_DETAIL are the same string.

    Assertions:
    - VERSION contains exactly the tag name (no -N-gSHA suffix).
    - VERSION_DETAIL contains the tag name (no suffix here either).
    - VERSION_DETAIL contains the deposit SHA.
    """
    repo, tag = _build_tag_exact_repo(tmp_path)
    kanban_root = tmp_path / "kanban_root"
    kanban_root.mkdir()

    deposit_sha = _git_cmd(repo, "rev-parse", "HEAD")

    result = run_bash(
        tmp_path,
        f"source {_VERSION_STAMP_SH!s} && stamp_version_files '{repo!s}' '{kanban_root!s}'",
    )
    assert result.returncode == 0, (
        f"stamp_version_files exited non-zero: {result.stderr}"
    )

    version_file = kanban_root / "VERSION"
    detail_file = kanban_root / "VERSION_DETAIL"

    assert version_file.exists(), "VERSION was not written"
    stamped = version_file.read_text(encoding="utf-8").strip()
    assert stamped == tag, (
        f"Tag-exact deposit: VERSION must equal the clean tag.\n"
        f"Expected: {tag!r}\nGot: {stamped!r}"
    )

    assert detail_file.exists(), "VERSION_DETAIL was not written"
    detail = detail_file.read_text(encoding="utf-8").strip()
    assert tag in detail, (
        f"VERSION_DETAIL must carry the tag string; got: {detail!r}"
    )
    assert deposit_sha[:7] in detail or deposit_sha in detail, (
        f"VERSION_DETAIL must carry the deposit SHA; got: {detail!r}"
    )


# ---------------------------------------------------------------------------
# Test 2: tag+1 deposit — the load-bearing assertion
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _VERSION_STAMP_SH.exists(),
    reason=f"version_stamp.sh not found at {_VERSION_STAMP_SH}",
)
def test_tag_plus_one_deposit_writes_clean_tag_to_VERSION(
    tmp_path: pathlib.Path,
) -> None:
    """Past-a-tag deposit: VERSION is the CLEAN tag; VERSION_DETAIL carries the full describe.

    This is the load-bearing assertion: even when the dev tree is one commit
    past the last tag, the stamp split strips the describe suffix so that
    operator-facing surfaces show the clean version label.

    Assertions:
    - VERSION contains exactly the base tag (NO describe suffix like -1-gABCDEF).
    - VERSION_DETAIL contains the full describe string (WITH the suffix).
    - VERSION_DETAIL contains the deposit SHA.
    - The full describe string is NOT identical to the base tag (fixture sanity).
    """
    repo, base_tag, full_describe = _build_tag_plus_one_repo(tmp_path)
    kanban_root = tmp_path / "kanban_root"
    kanban_root.mkdir()

    deposit_sha = _git_cmd(repo, "rev-parse", "HEAD")

    # Sanity: the fixture's describe must differ from the base tag.
    assert full_describe != base_tag, (
        f"Fixture error: expected describe to differ from the base tag; "
        f"describe={full_describe!r}, tag={base_tag!r}"
    )
    assert full_describe.startswith(base_tag + "-"), (
        f"Fixture error: describe must start with base tag + '-'; "
        f"describe={full_describe!r}"
    )

    result = run_bash(
        tmp_path,
        f"source {_VERSION_STAMP_SH!s} && stamp_version_files '{repo!s}' '{kanban_root!s}'",
    )
    assert result.returncode == 0, (
        f"stamp_version_files exited non-zero: {result.stderr}"
    )

    version_file = kanban_root / "VERSION"
    detail_file = kanban_root / "VERSION_DETAIL"

    assert version_file.exists(), "VERSION was not written"
    stamped = version_file.read_text(encoding="utf-8").strip()

    # Load-bearing assertion: VERSION must be the CLEAN tag with no describe suffix.
    assert stamped == base_tag, (
        f"Tag+1 deposit: VERSION must be the CLEAN tag (no describe suffix).\n"
        f"Expected: {base_tag!r}\n"
        f"Got:      {stamped!r}\n"
        f"Full describe was: {full_describe!r}\n"
        f"This is the split's whole purpose: VERSION shows ai_vX.Y.Z, "
        f"not ai_vX.Y.Z-1-g<sha>."
    )
    assert "-g" not in stamped, (
        f"VERSION must not contain a git commit suffix; got: {stamped!r}"
    )

    assert detail_file.exists(), "VERSION_DETAIL was not written"
    detail = detail_file.read_text(encoding="utf-8").strip()

    # VERSION_DETAIL must carry the full describe (with the suffix).
    assert full_describe in detail, (
        f"VERSION_DETAIL must carry the full describe string.\n"
        f"Expected to find: {full_describe!r}\nIn: {detail!r}"
    )

    # VERSION_DETAIL must carry the deposit SHA.
    assert deposit_sha[:7] in detail or deposit_sha in detail, (
        f"VERSION_DETAIL must carry the deposit SHA.\n"
        f"SHA: {deposit_sha!r}\nVERSION_DETAIL: {detail!r}"
    )


# ---------------------------------------------------------------------------
# Test 3: --stamp-version override
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _VERSION_STAMP_SH.exists(),
    reason=f"version_stamp.sh not found at {_VERSION_STAMP_SH}",
)
def test_stamp_version_override_writes_explicit_value_to_VERSION_unchanged(
    tmp_path: pathlib.Path,
) -> None:
    """--stamp-version override writes the explicit value to VERSION verbatim.

    When an explicit stamp value is passed, VERSION receives it unchanged.
    VERSION_DETAIL is NOT written (the explicit value is clean by definition
    and carries no describe suffix to record).

    Assertions:
    - VERSION contains exactly the explicit value.
    - VERSION_DETAIL does not exist.
    """
    repo, _tag = _build_tag_exact_repo(tmp_path)
    kanban_root = tmp_path / "kanban_root"
    kanban_root.mkdir()

    explicit_stamp = "ai_v1.19.0-rc.1"

    result = run_bash(
        tmp_path,
        f"source {_VERSION_STAMP_SH!s} && stamp_version_files '{repo!s}' '{kanban_root!s}' '{explicit_stamp}'",
    )
    assert result.returncode == 0, (
        f"stamp_version_files (override) exited non-zero: {result.stderr}"
    )

    version_file = kanban_root / "VERSION"
    detail_file = kanban_root / "VERSION_DETAIL"

    assert version_file.exists(), "VERSION was not written on --stamp-version path"
    stamped = version_file.read_text(encoding="utf-8").strip()
    assert stamped == explicit_stamp, (
        f"--stamp-version override: VERSION must equal the explicit value exactly.\n"
        f"Expected: {explicit_stamp!r}\nGot: {stamped!r}"
    )

    assert not detail_file.exists(), (
        f"VERSION_DETAIL must NOT be written for an explicit stamp override.\n"
        f"Found: {detail_file.read_text(encoding='utf-8')!r}"
    )


# ---------------------------------------------------------------------------
# Test 4: divergence advisory still prints in the tag+1 case (regression lock)
# ---------------------------------------------------------------------------


def _build_advisory_sandbox(tmp_path: pathlib.Path) -> dict[str, pathlib.Path]:
    """Build a minimal upgrade.sh sandbox with a real git repo for advisory testing.

    The git repo has:
    - A tag ai_v1.0.0 on commit A (simulating the latest published tag).
    - refs/remotes/origin/main pointing at commit A.
    - A polish commit B past the tag (tag+1 state).
    - HEAD on main at commit B.

    This triggers the divergence advisory because:
      git describe --tags → ai_v1.0.0-1-g<sha>  (not equal to ai_v1.0.0)
      get_latest_released_tag → ai_v1.0.0        (the tag merged into origin/main)

    Returns a dict with keys:
      "kanban_root"  — installed kanban root path
      "dev_tree"     — dev tree (the git repo)
      "pgai_temp"    — temp dir
    """
    kanban_root = tmp_path / "kanban_root"
    dev_tree = tmp_path / "dev_tree"
    pgai_temp = tmp_path / "pgai_temp"

    kanban_root.mkdir()
    dev_tree.mkdir()
    pgai_temp.mkdir()

    # --- Build the git repo inside dev_tree ---
    def _git(*args: str) -> str:
        r = subprocess.run(
            ["git", "-C", str(dev_tree), *args],
            capture_output=True,
            text=True,
            check=True,
        )
        return r.stdout.strip()

    _git("init", "-b", "main")
    _git("config", "user.email", "test@example.com")
    _git("config", "user.name", "Test Fixture")

    # Commit A: tagged as the latest released version.
    # Note: get_latest_released_tag() in version.sh matches '^v[0-9]+...' (no ai_ prefix),
    # so we use a bare v-tag here so the advisory fires.
    (dev_tree / "a.txt").write_text("a\n", encoding="utf-8")
    _git("add", "a.txt")
    _git("commit", "-m", "Initial commit")
    _git("tag", "v1.0.0")
    tag_sha = _git("rev-parse", "HEAD")

    # Set origin/main to commit A so get_latest_released_tag returns v1.0.0.
    _git("update-ref", "refs/remotes/origin/main", tag_sha)

    # Commit B: one polish commit past the tag — the tag+1 state.
    (dev_tree / "b.txt").write_text("polish\n", encoding="utf-8")
    _git("add", "b.txt")
    _git("commit", "-m", "Polish commit past tag")

    _populate_upgrade_sandbox(kanban_root, dev_tree)
    (kanban_root / "VERSION").write_text("v1.0.0\n", encoding="utf-8")

    return {
        "kanban_root": kanban_root,
        "dev_tree": dev_tree,
        "pgai_temp": pgai_temp,
    }


def _populate_upgrade_sandbox(kanban_root: pathlib.Path, dev_tree: pathlib.Path) -> None:
    """Lay down the minimum an upgrade.sh run needs in an installed root and a dev tree.

    The installed root gets the real upgrade.sh and its libs under
    ``scripts/`` plus a ``kanban.cfg`` naming the dev tree.  The dev tree
    gets the real upgrade.sh, its libs, the shared version_stamp.sh
    helper, the dashboard version lib, and stubs for every path the
    deposit loop walks.  Neither directory is touched beyond that, so the
    caller decides whether the dev tree is a git checkout and what VERSION
    either side holds.
    """
    # --- Kanban root: scripts/ with real upgrade.sh and libs ---
    installed_scripts = kanban_root / "scripts"
    installed_lib = installed_scripts / "lib"
    installed_lib.mkdir(parents=True)

    for lib_src in [
        _REAL_ARGPARSE_SH,
        _REAL_ENV_BOOTSTRAP_SH,
        _REAL_INI_PARSER_SH,
        _REAL_PROJECT_PATHS_SH,
        _REAL_ERROR_CONTRACT_SH,
        _REAL_TEMP_SH,
        _REAL_SCRIPT_COMMON_SH,
    ]:
        if lib_src.exists():
            shutil.copy2(lib_src, installed_lib / lib_src.name)

    installed_upgrade = installed_scripts / "upgrade.sh"
    shutil.copy2(_REAL_UPGRADE_SH, installed_upgrade)
    installed_upgrade.chmod(0o755)

    # --- Kanban root: kanban.cfg pointing at dev_tree ---
    (kanban_root / "kanban.cfg").write_text(
        textwrap.dedent(
            f"""\
            [paths]
            dev_tree_path = {dev_tree}
            """
        ),
        encoding="utf-8",
    )

    # --- Dev tree: install.sh stub ---
    dev_install = dev_tree / "install.sh"
    dev_install.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    dev_install.chmod(0o755)

    # --- Dev tree: team/scripts/ with the new upgrade.sh and real libs ---
    dev_scripts = dev_tree / "team" / "scripts"
    dev_lib = dev_scripts / "lib"
    dev_lib.mkdir(parents=True)

    dev_upgrade = dev_scripts / "upgrade.sh"
    shutil.copy2(_REAL_UPGRADE_SH, dev_upgrade)
    dev_upgrade.chmod(0o755)

    for lib_src in [
        _REAL_ARGPARSE_SH,
        _REAL_ENV_BOOTSTRAP_SH,
        _REAL_INI_PARSER_SH,
        _REAL_PROJECT_PATHS_SH,
        _REAL_ERROR_CONTRACT_SH,
        _REAL_TEMP_SH,
        _REAL_SCRIPT_COMMON_SH,
    ]:
        if lib_src.exists():
            shutil.copy2(lib_src, dev_lib / lib_src.name)

    # Copy the shared version_stamp.sh helper so the dev-tree upgrade.sh can source it.
    if _REAL_VERSION_STAMP_SH.exists():
        shutil.copy2(_REAL_VERSION_STAMP_SH, dev_lib / _REAL_VERSION_STAMP_SH.name)

    # Copy the shared subagent_deploy.sh helper so the dev-tree upgrade.sh can
    # source it (required — the provider-agent deploy step fails loud if absent).
    if _REAL_SUBAGENT_DEPLOY_SH.exists():
        shutil.copy2(_REAL_SUBAGENT_DEPLOY_SH, dev_lib / _REAL_SUBAGENT_DEPLOY_SH.name)

    # Copy the version.sh dashboard lib so get_latest_released_tag is available.
    _dash_lib_src = _SCRIPTS_DIR / "dashboard" / "lib" / "version.sh"
    if _dash_lib_src.exists():
        dash_lib_dst = dev_scripts / "dashboard" / "lib"
        dash_lib_dst.mkdir(parents=True)
        shutil.copy2(_dash_lib_src, dash_lib_dst / "version.sh")

    # --- Dev tree: minimal stubs for the deposit loop ---
    for doc_name in ["README.md", "DIRECTIVES.md", "OVERVIEW.md", "SOP-core.md"]:
        (dev_tree / "team" / doc_name).write_text(f"# {doc_name} stub\n", encoding="utf-8")
    for sub_name in ["roles", "pm-agent", "workflows", "pgai_agent_kanban", "halt_after", "templates", "demos"]:
        sub = dev_tree / "team" / sub_name
        sub.mkdir(parents=True, exist_ok=True)
        (sub / ".gitkeep").write_text("", encoding="utf-8")

    # --- Dev tree: subagents/MANIFEST.txt stub ---
    (dev_tree / "subagents").mkdir()
    (dev_tree / "subagents" / "MANIFEST.txt").write_text("# Empty manifest\n", encoding="utf-8")


@pytest.mark.skipif(
    not _REAL_UPGRADE_SH.exists(),
    reason=f"Real upgrade.sh not found at {_REAL_UPGRADE_SH}",
)
def test_divergence_advisory_still_prints_on_tag_plus_one_deposit(
    tmp_path: pathlib.Path,
) -> None:
    """Regression lock: the staged-vs-published divergence advisory prints on a tag+1 deposit.

    When the dev tree is one commit past the latest tag merged into origin/main,
    upgrade.sh must print the advisory line "deploying <describe> ; latest published
    tag is <tag>".  The stamp split must not suppress this advisory.

    Assertions:
    - upgrade.sh exits 0.
    - The advisory message is present in the output.
    - VERSION contains the CLEAN tag (no describe suffix).
    - VERSION_DETAIL contains the full describe string.
    """
    sandbox = _build_advisory_sandbox(tmp_path)
    kanban_root = sandbox["kanban_root"]
    dev_tree = sandbox["dev_tree"]
    pgai_temp = sandbox["pgai_temp"]

    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()

    env = dict(os.environ)
    env.pop("PGAI_DEV_TREE_PATH", None)
    env.update(
        {
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(kanban_root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(pgai_temp),
            "HOME": str(fake_home),
        }
    )

    result = subprocess.run(
        [
            "bash",
            str(kanban_root / "scripts" / "upgrade.sh"),
            "--force",
            "--no-backup",
            "--dev-tree",
            str(dev_tree),
            # No --stamp-version: use real git describe to trigger the advisory.
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )

    assert result.returncode == 0, (
        f"upgrade.sh failed (exit {result.returncode}).\n"
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )

    combined = result.stdout + result.stderr

    # The divergence advisory must appear.
    assert "deploying" in combined and "latest published tag" in combined, (
        f"Divergence advisory must print when deployed tree is past the latest tag.\n"
        f"Expected text containing 'deploying ... latest published tag ...'\n"
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )

    # VERSION must be the clean tag (the split is active).
    version_file = kanban_root / "VERSION"
    assert version_file.exists(), "VERSION was not written"
    stamped = version_file.read_text(encoding="utf-8").strip()
    assert stamped == "v1.0.0", (
        f"VERSION must be the clean tag even on a tag+1 deposit.\n"
        f"Expected: 'v1.0.0'\nGot: {stamped!r}"
    )
    assert "-g" not in stamped, (
        f"VERSION must not contain a git commit suffix; got: {stamped!r}"
    )

    # VERSION_DETAIL must be written with the full describe.
    detail_file = kanban_root / "VERSION_DETAIL"
    assert detail_file.exists(), "VERSION_DETAIL was not written on a tag+1 deposit"
    detail = detail_file.read_text(encoding="utf-8").strip()
    assert "v1.0.0-1-g" in detail, (
        f"VERSION_DETAIL must carry the full describe string.\n"
        f"Got: {detail!r}"
    )


# ---------------------------------------------------------------------------
# Test 5: status-bar renderer shows the clean VERSION value
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _STATUS_RIGHT_SH.exists(),
    reason=f"status-right.sh not found at {_STATUS_RIGHT_SH}",
)
def test_status_bar_renderer_shows_clean_version_from_VERSION_file(
    tmp_path: pathlib.Path,
) -> None:
    """status-right.sh renders the clean VERSION value without modification.

    Places a clean VERSION file in a synthetic kanban root and invokes
    status-right.sh in minimal (no-tmux) mode.  The renderer reads VERSION
    directly and must output its contents as the version segment.

    The renderer is NOT modified in this task; this test confirms the renderer
    already works correctly with the clean VERSION value written by the split.

    Assertions:
    - The script output contains the clean version string.
    - The script output does NOT contain any describe suffix (e.g. -1-gABCDEF).
    """
    clean_version = "ai_v1.19.0"

    kanban_root = tmp_path / "kanban_root"
    kanban_root.mkdir()
    (kanban_root / "VERSION").write_text(f"{clean_version}\n", encoding="utf-8")

    # Create minimal directories the renderer may probe.
    for d in ["logs", "locks", "projects"]:
        (kanban_root / d).mkdir()

    result = run_bash(
        tmp_path,
        f"PGAI_AGENT_KANBAN_ROOT_PATH='{kanban_root!s}' "
        f"PGAI_DEV_TREE_PATH='' "
        f"bash {_STATUS_RIGHT_SH!s}",
        extra_env={
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(kanban_root),
            "PGAI_DEV_TREE_PATH": "",
            "TERM": "dumb",
        },
        timeout=15,
    )

    # The renderer may exit non-zero in minimal mode (missing tmux, missing
    # data.sh, etc.) — that is acceptable.  What matters is that the clean
    # version appears in whatever output was produced, not that the full
    # status bar renders completely.
    output = result.stdout + result.stderr
    assert clean_version in output, (
        f"status-right.sh output must contain the clean version '{clean_version}'.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    # Ensure no describe suffix leaked through.
    assert "-g" not in output.split(clean_version)[-1].split("\n")[0], (
        f"status-right.sh output must not show a git describe suffix after the clean version.\n"
        f"output: {output!r}"
    )


# ---------------------------------------------------------------------------
# Test 6: single implementation — install.sh and upgrade.sh both call the helper
# ---------------------------------------------------------------------------


def test_install_sh_sources_version_stamp_helper(tmp_path: pathlib.Path) -> None:
    """install.sh sources team/scripts/lib/version_stamp.sh.

    Confirms the single-implementation requirement: the stamp write in install.sh
    delegates to the shared helper, not an inline duplicate.
    """
    install_sh = _TEAM_DIR.parent / "install.sh"
    assert install_sh.exists(), f"install.sh not found at {install_sh}"
    text = install_sh.read_text(encoding="utf-8")
    assert "version_stamp.sh" in text, (
        f"install.sh must source version_stamp.sh (the shared helper);\n"
        f"found no reference to 'version_stamp.sh' in {install_sh}"
    )
    assert "stamp_version_files" in text, (
        f"install.sh must call stamp_version_files (the shared helper function);\n"
        f"found no call to 'stamp_version_files' in {install_sh}"
    )


def test_upgrade_sh_sources_version_stamp_helper(tmp_path: pathlib.Path) -> None:
    """upgrade.sh sources team/scripts/lib/version_stamp.sh.

    Confirms the single-implementation requirement: the stamp write in upgrade.sh
    delegates to the shared helper, not an inline duplicate.
    """
    assert _REAL_UPGRADE_SH.exists(), f"upgrade.sh not found at {_REAL_UPGRADE_SH}"
    text = _REAL_UPGRADE_SH.read_text(encoding="utf-8")
    assert "version_stamp.sh" in text, (
        f"upgrade.sh must source version_stamp.sh (the shared helper);\n"
        f"found no reference to 'version_stamp.sh' in {_REAL_UPGRADE_SH}"
    )
    assert "stamp_version_files" in text, (
        f"upgrade.sh must call stamp_version_files (the shared helper function);\n"
        f"found no call to 'stamp_version_files' in {_REAL_UPGRADE_SH}"
    )


# ---------------------------------------------------------------------------
# Test 7: an installed tag names VERSION_DETAIL, prefix or not
# ---------------------------------------------------------------------------


def _build_bare_then_prefixed_tag_repo(tmp_path: pathlib.Path) -> pathlib.Path:
    """Build a repo with a bare ``v1.0.0`` tag and ``ai_v1.1.0`` on a later commit.

    A prefix-less describe (``--match 'v[0-9]*'``) from the later commit
    walks back to ``v1.0.0``; only a describe that knows the installed tag
    lands on ``ai_v1.1.0``.
    """
    repo = _make_git_repo(tmp_path, "bare_then_prefixed_repo")
    (repo / "v.txt").write_text("first\n", encoding="utf-8")
    _git_cmd(repo, "add", "v.txt")
    _git_cmd(repo, "commit", "-m", "First release")
    _git_cmd(repo, "tag", "v1.0.0")
    (repo / "v.txt").write_text("second\n", encoding="utf-8")
    _git_cmd(repo, "commit", "-am", "Second release")
    _git_cmd(repo, "tag", "ai_v1.1.0")
    return repo


@pytest.mark.skipif(
    not _VERSION_STAMP_SH.exists(),
    reason=f"version_stamp.sh not found at {_VERSION_STAMP_SH}",
)
def test_installed_tag_names_version_detail_from_a_detached_worktree(
    tmp_path: pathlib.Path,
) -> None:
    """A version-targeted laydown stamps VERSION_DETAIL with the tag it installed.

    The source is a detached ``git worktree`` at ``ai_v1.1.0`` (its ``.git``
    is a pointer file, as in a version-targeted install), no branch prefix
    is passed (no project is registered on a fresh install), and the bare
    ``v1.0.0`` tag is reachable.  VERSION_DETAIL must still start with
    ``ai_v1.1.0`` and carry the tag's commit as the deposit SHA.
    """
    repo = _build_bare_then_prefixed_tag_repo(tmp_path)
    worktree = tmp_path / "laydown_worktree"
    _git_cmd(repo, "worktree", "add", "--detach", str(worktree), "ai_v1.1.0")
    assert (worktree / ".git").is_file(), "fixture expects a worktree .git pointer file"
    tag_sha = _git_cmd(repo, "rev-parse", "ai_v1.1.0^{commit}")

    kanban_root = tmp_path / "kanban_root"
    kanban_root.mkdir()

    result = run_bash(
        tmp_path,
        f"source {_VERSION_STAMP_SH!s} && "
        f"stamp_version_files '{worktree!s}' '{kanban_root!s}' '' '' 'ai_v1.1.0'",
    )
    assert result.returncode == 0, (
        f"stamp_version_files exited non-zero: {result.stderr}"
    )

    detail_file = kanban_root / "VERSION_DETAIL"
    assert detail_file.exists(), "VERSION_DETAIL was not written for an installed tag"
    detail = detail_file.read_text(encoding="utf-8")
    assert detail == f"ai_v1.1.0  deposit-sha={tag_sha}\n", (
        f"VERSION_DETAIL must name the installed tag and its commit; got: {detail!r}"
    )
    assert (kanban_root / "VERSION").read_text(encoding="utf-8").strip() == "ai_v1.1.0", (
        "with no committed VERSION, VERSION falls back to the installed tag"
    )


# ---------------------------------------------------------------------------
# Test 8: a plain stamp (no prefix, no installed tag) prefers a prefixed tag
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _VERSION_STAMP_SH.exists(),
    reason=f"version_stamp.sh not found at {_VERSION_STAMP_SH}",
)
def test_plain_stamp_names_prefixed_release_tag_over_older_bare_tag(
    tmp_path: pathlib.Path,
) -> None:
    """A plain install from a checkout stamps the prefixed tag at HEAD.

    install.sh without a version argument passes neither a branch prefix
    (no project is registered yet) nor an installed tag.  With ``v1.0.0``
    on an older commit and ``ai_v1.1.0`` at HEAD, VERSION_DETAIL must name
    ``ai_v1.1.0`` rather than walking back to the bare tag.
    """
    repo = _build_bare_then_prefixed_tag_repo(tmp_path)
    head_sha = _git_cmd(repo, "rev-parse", "HEAD")
    kanban_root = tmp_path / "kanban_root"
    kanban_root.mkdir()

    result = run_bash(
        tmp_path,
        f"source {_VERSION_STAMP_SH!s} && "
        f"stamp_version_files '{repo!s}' '{kanban_root!s}'",
    )
    assert result.returncode == 0, (
        f"stamp_version_files exited non-zero: {result.stderr}"
    )

    detail = (kanban_root / "VERSION_DETAIL").read_text(encoding="utf-8")
    assert detail == f"ai_v1.1.0  deposit-sha={head_sha}\n", (
        f"VERSION_DETAIL must name the prefixed tag at HEAD; got: {detail!r}"
    )


@pytest.mark.skipif(
    not _VERSION_STAMP_SH.exists(),
    reason=f"version_stamp.sh not found at {_VERSION_STAMP_SH}",
)
def test_plain_stamp_past_a_prefixed_tag_describes_from_that_tag(
    tmp_path: pathlib.Path,
) -> None:
    """One commit past ``ai_v1.1.0``, VERSION_DETAIL describes from it.

    The bare ``v1.0.0`` is further back; the plain stamp must still use the
    prefixed tag as the describe base (``ai_v1.1.0-1-g<sha>``).
    """
    repo = _build_bare_then_prefixed_tag_repo(tmp_path)
    (repo / "v.txt").write_text("third\n", encoding="utf-8")
    _git_cmd(repo, "commit", "-am", "Polish past the prefixed tag")
    kanban_root = tmp_path / "kanban_root"
    kanban_root.mkdir()

    result = run_bash(
        tmp_path,
        f"source {_VERSION_STAMP_SH!s} && "
        f"stamp_version_files '{repo!s}' '{kanban_root!s}'",
    )
    assert result.returncode == 0, (
        f"stamp_version_files exited non-zero: {result.stderr}"
    )

    detail = (kanban_root / "VERSION_DETAIL").read_text(encoding="utf-8")
    assert detail.startswith("ai_v1.1.0-1-g"), (
        f"VERSION_DETAIL must describe from the prefixed tag; got: {detail!r}"
    )


# ---------------------------------------------------------------------------
# Test 9: the version-targeted upgrade call shape stamps the resolved tag
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _VERSION_STAMP_SH.exists(),
    reason=f"version_stamp.sh not found at {_VERSION_STAMP_SH}",
)
def test_targeted_upgrade_call_shape_stamps_resolved_tag_from_worktree(
    tmp_path: pathlib.Path,
) -> None:
    """upgrade.sh's version-targeted stamp writes VERSION_DETAIL from a worktree.

    A version-targeted upgrade lays down from a detached worktree (``.git``
    is a pointer file) and passes the project's branch prefix together with
    the tag it resolved.  VERSION_DETAIL must be written and start with
    that tag.
    """
    repo = _build_bare_then_prefixed_tag_repo(tmp_path)
    worktree = tmp_path / "upgrade_worktree"
    _git_cmd(repo, "worktree", "add", "--detach", str(worktree), "ai_v1.1.0")
    assert (worktree / ".git").is_file(), "fixture expects a worktree .git pointer file"
    tag_sha = _git_cmd(repo, "rev-parse", "ai_v1.1.0^{commit}")

    kanban_root = tmp_path / "kanban_root"
    kanban_root.mkdir()

    result = run_bash(
        tmp_path,
        f"source {_VERSION_STAMP_SH!s} && "
        f"stamp_version_files '{worktree!s}' '{kanban_root!s}' '' 'ai_' 'ai_v1.1.0'",
    )
    assert result.returncode == 0, (
        f"stamp_version_files exited non-zero: {result.stderr}"
    )

    detail_file = kanban_root / "VERSION_DETAIL"
    assert detail_file.exists(), "VERSION_DETAIL was not written from the worktree"
    assert detail_file.read_text(encoding="utf-8") == (
        f"ai_v1.1.0  deposit-sha={tag_sha}\n"
    )


# ---------------------------------------------------------------------------
# Test 10: a worktree source without an installed tag still writes the detail
# ---------------------------------------------------------------------------

_STALE_DETAIL = "ai_v1.0.0  deposit-sha=0000old\n"


def _build_worktree_with_committed_version(
    tmp_path: pathlib.Path,
) -> tuple[pathlib.Path, str]:
    """Build a detached worktree at ``ai_v1.1.0`` whose tree commits VERSION.

    The repo carries a committed ``VERSION`` of ``v1.1.0`` and a bare
    ``v1.0.0`` tag on its first commit, then ``ai_v1.1.0`` on the second.
    The worktree's ``.git`` is a gitdir pointer file, as for any linked
    worktree.  Returns (worktree_path, tag_commit_sha).
    """
    repo = _make_git_repo(tmp_path, "committed_version_repo")
    (repo / "VERSION").write_text("v1.1.0\n", encoding="utf-8")
    _git_cmd(repo, "add", "VERSION")
    _git_cmd(repo, "commit", "-m", "First release")
    _git_cmd(repo, "tag", "v1.0.0")
    (repo / "f.txt").write_text("x\n", encoding="utf-8")
    _git_cmd(repo, "add", "f.txt")
    _git_cmd(repo, "commit", "-m", "Second release")
    _git_cmd(repo, "tag", "ai_v1.1.0")

    worktree = tmp_path / "source_worktree"
    _git_cmd(repo, "worktree", "add", "--detach", str(worktree), "ai_v1.1.0")
    assert (worktree / ".git").is_file(), "fixture expects a worktree .git pointer file"
    return worktree, _git_cmd(repo, "rev-parse", "ai_v1.1.0^{commit}")


@pytest.mark.skipif(
    not _VERSION_STAMP_SH.exists(),
    reason=f"version_stamp.sh not found at {_VERSION_STAMP_SH}",
)
@pytest.mark.parametrize(
    "call_args",
    [
        pytest.param("'' 'ai_'", id="non-targeted-upgrade-shape"),
        pytest.param("'' '' ''", id="plain-install-shape"),
    ],
)
def test_worktree_source_without_installed_tag_replaces_stale_version_detail(
    tmp_path: pathlib.Path,
    call_args: str,
) -> None:
    """A linked-worktree source stamps VERSION_DETAIL with no installed tag.

    A plain install or a non-targeted upgrade run from a linked worktree
    passes no installed tag.  The worktree is still a git checkout, so
    VERSION_DETAIL must describe it — naming ``ai_v1.1.0`` and its commit —
    and replace the older release's VERSION_DETAIL already in the root.
    VERSION is the committed file, copied verbatim.
    """
    worktree, tag_sha = _build_worktree_with_committed_version(tmp_path)
    kanban_root = tmp_path / "kanban_root"
    kanban_root.mkdir()
    (kanban_root / "VERSION_DETAIL").write_text(_STALE_DETAIL, encoding="utf-8")

    result = run_bash(
        tmp_path,
        f"source {_VERSION_STAMP_SH!s} && "
        f"stamp_version_files '{worktree!s}' '{kanban_root!s}' {call_args}",
    )
    assert result.returncode == 0, (
        f"stamp_version_files exited non-zero: {result.stderr}"
    )

    assert (kanban_root / "VERSION").read_text(encoding="utf-8") == "v1.1.0\n", (
        "VERSION must be the worktree's committed VERSION, copied verbatim"
    )
    detail_file = kanban_root / "VERSION_DETAIL"
    assert detail_file.exists(), "VERSION_DETAIL was not written from the worktree"
    detail = detail_file.read_text(encoding="utf-8")
    assert detail == f"ai_v1.1.0  deposit-sha={tag_sha}\n", (
        f"VERSION_DETAIL must describe the worktree laid down, replacing the "
        f"stale {_STALE_DETAIL.strip()!r}; got: {detail!r}"
    )


# ---------------------------------------------------------------------------
# Test 11: a non-git source writes no detail and keeps no stale one
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _VERSION_STAMP_SH.exists(),
    reason=f"version_stamp.sh not found at {_VERSION_STAMP_SH}",
)
def test_non_git_source_copies_version_and_removes_stale_version_detail(
    tmp_path: pathlib.Path,
) -> None:
    """A source tree with no ``.git`` copies VERSION and leaves no VERSION_DETAIL.

    With no git checkout there is nothing to describe, so no VERSION_DETAIL
    is written — and the one an earlier release left in the root must not
    survive to sit beside the new VERSION naming the older release.
    """
    source = tmp_path / "plain_source"
    source.mkdir()
    (source / "VERSION").write_text("v1.1.0\n", encoding="utf-8")
    assert not (source / ".git").exists(), "fixture expects no .git at all"

    kanban_root = tmp_path / "kanban_root"
    kanban_root.mkdir()
    (kanban_root / "VERSION_DETAIL").write_text(_STALE_DETAIL, encoding="utf-8")

    result = run_bash(
        tmp_path,
        f"source {_VERSION_STAMP_SH!s} && "
        f"stamp_version_files '{source!s}' '{kanban_root!s}' '' '' ''",
    )
    assert result.returncode == 0, (
        f"stamp_version_files exited non-zero: {result.stderr}"
    )

    assert (kanban_root / "VERSION").read_text(encoding="utf-8") == "v1.1.0\n", (
        "VERSION must be the source tree's committed VERSION, copied verbatim"
    )
    detail_file = kanban_root / "VERSION_DETAIL"
    assert not detail_file.exists(), (
        f"A non-git source must leave no VERSION_DETAIL in the root.\n"
        f"Found: {detail_file.read_text(encoding='utf-8')!r}"
    )


# ---------------------------------------------------------------------------
# Test 12: a --stamp-version deposit keeps no stale detail
# ---------------------------------------------------------------------------


def _build_plain_source(tmp_path: pathlib.Path) -> pathlib.Path:
    """Build a source tree with a committed VERSION and no ``.git`` at all."""
    source = tmp_path / "plain_source"
    source.mkdir()
    (source / "VERSION").write_text("v1.1.0\n", encoding="utf-8")
    return source


@pytest.mark.skipif(
    not _VERSION_STAMP_SH.exists(),
    reason=f"version_stamp.sh not found at {_VERSION_STAMP_SH}",
)
@pytest.mark.parametrize(
    "build_source",
    [
        pytest.param(lambda p: _build_tag_exact_repo(p)[0], id="git-source"),
        pytest.param(_build_plain_source, id="non-git-source"),
    ],
)
def test_stamp_version_override_removes_stale_version_detail(
    tmp_path: pathlib.Path,
    build_source: Callable[[pathlib.Path], pathlib.Path],
) -> None:
    """A --stamp-version deposit leaves no VERSION_DETAIL naming an older release.

    The override writes VERSION verbatim and produces no VERSION_DETAIL of
    its own.  A VERSION_DETAIL an earlier deposit left in the root must not
    survive beside the stamped VERSION, whether or not the source tree is a
    git checkout: it would name a release that is no longer installed.
    """
    source = build_source(tmp_path)
    kanban_root = tmp_path / "kanban_root"
    kanban_root.mkdir()
    stale_detail = "ai_v1.0.0  deposit-sha=old\n"
    (kanban_root / "VERSION_DETAIL").write_text(stale_detail, encoding="utf-8")

    explicit_stamp = "v9.9.9"
    result = run_bash(
        tmp_path,
        f"source {_VERSION_STAMP_SH!s} && "
        f"stamp_version_files '{source!s}' '{kanban_root!s}' '{explicit_stamp}'",
    )
    assert result.returncode == 0, (
        f"stamp_version_files (override) exited non-zero: {result.stderr}"
    )

    assert (kanban_root / "VERSION").read_text(encoding="utf-8") == f"{explicit_stamp}\n", (
        "--stamp-version override: VERSION must equal the explicit value exactly"
    )
    detail_file = kanban_root / "VERSION_DETAIL"
    assert not detail_file.exists(), (
        f"A --stamp-version deposit must leave no VERSION_DETAIL in the root; "
        f"the stale {stale_detail.strip()!r} survived as "
        f"{detail_file.read_text(encoding='utf-8')!r}"
    )


# ---------------------------------------------------------------------------
# Test 13: upgrade.sh stamps VERSION from a dev tree with no .git
# ---------------------------------------------------------------------------

_SEEDED_ROOT_VERSION = "v2.35.2"
_SEEDED_ROOT_DETAIL = "ai_v2.35.2  deposit-sha=old\n"


def _build_non_git_upgrade_sandbox(
    tmp_path: pathlib.Path, committed_version: str
) -> dict[str, pathlib.Path]:
    """Build an installed root and a dev tree that is not a git checkout.

    The dev tree is what an archive export of a checkout looks like: the
    tracked files, a committed ``VERSION`` reading ``committed_version``,
    and no ``.git`` of either shape.  The installed root holds an older
    VERSION and the VERSION_DETAIL an earlier deposit left beside it.
    """
    kanban_root = tmp_path / "kanban_root"
    dev_tree = tmp_path / "plain_dev_tree"
    pgai_temp = tmp_path / "pgai_temp"
    for directory in (kanban_root, dev_tree, pgai_temp):
        directory.mkdir()

    _populate_upgrade_sandbox(kanban_root, dev_tree)
    (dev_tree / "VERSION").write_text(f"{committed_version}\n", encoding="utf-8")
    (kanban_root / "VERSION").write_text(f"{_SEEDED_ROOT_VERSION}\n", encoding="utf-8")
    (kanban_root / "VERSION_DETAIL").write_text(_SEEDED_ROOT_DETAIL, encoding="utf-8")
    assert not (dev_tree / ".git").exists(), "fixture expects a dev tree with no .git"

    return {
        "kanban_root": kanban_root,
        "dev_tree": dev_tree,
        "pgai_temp": pgai_temp,
    }


def _run_sandboxed_upgrade(
    sandbox: dict[str, pathlib.Path],
    tmp_path: pathlib.Path,
    extra_args: list[str],
) -> subprocess.CompletedProcess:
    """Run the installed upgrade.sh against the sandbox root only.

    The environment is built from an explicit set rather than inherited,
    so all five names that otherwise resolve to the operator's live
    install are set to the sandbox (KANBAN_ROOT, PGAI_AGENT_KANBAN_ROOT_PATH,
    PGAI_AGENT_KANBAN_TEMP_DIR) or absent (PGAI_TASKS_DIR, PGAI_PROJECT_ROOT),
    and PGAI_DEV_TREE_PATH cannot override ``--dev-tree``.
    ``--wake-tier none`` keeps the run from installing any crontab.
    """
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir(exist_ok=True)
    env = build_scrubbed_env(
        home_dir=fake_home,
        extra_env={
            "KANBAN_ROOT": str(sandbox["kanban_root"]),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(sandbox["kanban_root"]),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(sandbox["pgai_temp"]),
        },
    )
    return subprocess.run(
        [
            "bash",
            str(sandbox["kanban_root"] / "scripts" / "upgrade.sh"),
            "--dev-tree",
            str(sandbox["dev_tree"]),
            "--force",
            "--no-backup",
            "--wake-tier",
            "none",
            *extra_args,
        ],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        cwd=str(sandbox["dev_tree"]),
    )


@pytest.mark.skipif(
    not _REAL_UPGRADE_SH.exists(),
    reason=f"Real upgrade.sh not found at {_REAL_UPGRADE_SH}",
)
def test_upgrade_from_non_git_tree_writes_stamp_version_override(
    tmp_path: pathlib.Path,
) -> None:
    """upgrade.sh --stamp-version from a tree with no .git writes the override.

    The override is the operator's explicit identity for the deposit; it
    must land in VERSION and clear the previous deposit's VERSION_DETAIL
    whether or not the dev tree is a git checkout, exactly as install.sh
    does from the same tree.  The run must also say what it wrote.
    """
    sandbox = _build_non_git_upgrade_sandbox(tmp_path, committed_version="v3.0.0")
    kanban_root = sandbox["kanban_root"]

    result = _run_sandboxed_upgrade(sandbox, tmp_path, ["--stamp-version", "v9.9.9"])
    combined = result.stdout + result.stderr

    assert result.returncode == 0, (
        f"upgrade.sh failed (exit {result.returncode}).\n"
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )
    assert (kanban_root / "VERSION").read_text(encoding="utf-8") == "v9.9.9\n", (
        f"VERSION must hold the --stamp-version value from a tree with no .git; "
        f"got {(kanban_root / 'VERSION').read_text(encoding='utf-8')!r}"
    )
    detail_file = kanban_root / "VERSION_DETAIL"
    assert not detail_file.exists(), (
        f"An override deposit must leave no VERSION_DETAIL; the seeded "
        f"{_SEEDED_ROOT_DETAIL.strip()!r} survived as "
        f"{detail_file.read_text(encoding='utf-8')!r}"
    )
    assert "VERSION updated" in combined, (
        f"upgrade.sh must narrate the VERSION it wrote.\n"
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )


@pytest.mark.skipif(
    not _REAL_UPGRADE_SH.exists(),
    reason=f"Real upgrade.sh not found at {_REAL_UPGRADE_SH}",
)
def test_upgrade_from_non_git_tree_copies_committed_version_and_clears_stale_detail(
    tmp_path: pathlib.Path,
) -> None:
    """A normal upgrade from a tree with no .git deposits the tree's committed VERSION.

    With no git checkout there is nothing to describe, so VERSION is the
    committed file copied verbatim and no VERSION_DETAIL is written; the
    one the previous deposit left must not survive to name a release that
    is no longer installed.  The run must also say what it wrote.
    """
    committed_version = "v3.0.0"
    sandbox = _build_non_git_upgrade_sandbox(tmp_path, committed_version=committed_version)
    kanban_root = sandbox["kanban_root"]

    result = _run_sandboxed_upgrade(sandbox, tmp_path, [])
    combined = result.stdout + result.stderr

    assert result.returncode == 0, (
        f"upgrade.sh failed (exit {result.returncode}).\n"
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )
    assert (kanban_root / "VERSION").read_text(encoding="utf-8") == f"{committed_version}\n", (
        f"VERSION must be the dev tree's committed VERSION from a tree with no .git; "
        f"got {(kanban_root / 'VERSION').read_text(encoding='utf-8')!r}"
    )
    detail_file = kanban_root / "VERSION_DETAIL"
    assert not detail_file.exists(), (
        f"A deposit from a tree with no .git must leave no VERSION_DETAIL; the "
        f"seeded {_SEEDED_ROOT_DETAIL.strip()!r} survived as "
        f"{detail_file.read_text(encoding='utf-8')!r}"
    )
    assert "VERSION updated" in combined, (
        f"upgrade.sh must narrate the VERSION it wrote.\n"
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )
