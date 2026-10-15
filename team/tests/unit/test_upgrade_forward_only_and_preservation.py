"""
test_upgrade_forward_only_and_preservation.py
===============================================
Gated fixtures for upgrade.sh's version-targeted forward-only behavior
(requirements/v2.2.0-version-targeted-install-upgrade.md, upgrade-side
acceptance criteria 1, 4, 5, 6).

WHAT IS TESTED
--------------
1. **Forward-only sequence.** With an install at vA and a source repo
   tagged vA < vB < vC: ``upgrade.sh vB`` lands vB's tree while vC still
   exists in the source repo; ``upgrade.sh vA`` (older than installed) and
   ``upgrade.sh vB`` (equal re-lay) are both refused with the documented
   messages and leave the install unchanged; ``upgrade.sh vC`` then lands
   vC.

2. **Identity-summary narration.** A successful version-targeted upgrade's
   stdout narrates ``upgraded <installed> -> <target> (from tag <tag>)``.

3. **Protected-file preservation.** secrets, shell-env, kanban.cfg,
   pseudocron.env, projects.cfg, and the entire projects/ tree (including a
   nested per-project project.cfg) are byte-identical before and after a
   version-targeted upgrade — the version-targeted code path does not
   change the preservation contract.

4. **Source-cleanliness.** The source repo's checked-out branch and
   ``git worktree list`` output are byte-identical before/after both a
   successful version-targeted upgrade and a refused (older-target) one.

5. **VERSION_DETAIL names the resolved tag.** A version-targeted upgrade
   lays down from a detached worktree yet still writes VERSION_DETAIL,
   starting with the tag it resolved and carrying that tag's commit.

SANDBOX DESIGN
--------------
Two throwaway trees are built per test, mirroring the two-phase sandbox in
test_upgrade_two_phase_behavioral.py and the tagged-repo builder in
test_install_version_targeting.py:

  - A "source repo" (the operator's dev tree): a full copy of this dev
    tree, then three commits on top — each changing ONLY the committed
    VERSION file, tagged ascending ("vA" < "vB" < "vC") — checked out on
    "main" at the newest (vC) commit. Because the copy is real,
    unmodified content, a version-targeted upgrade against this repo
    exercises the real resolve/materialize/laydown code path end-to-end
    (version_resolve.sh, version_stamp.sh, the subagent manifest,
    team/templates/retired-files.txt, etc.), not a synthetic stand-in.

  - An "installed kanban root": a minimal live-layout install containing
    only the installed upgrade.sh (phase-1 bootstrap) plus the library
    helpers it sources directly (argparse.sh, env_bootstrap.sh,
    ini_parser.sh, project_paths.sh, temp.sh, version_stamp.sh,
    subagent_deploy.sh) and a committed VERSION file recording the
    "currently installed" version. This mirrors
    _build_two_phase_sandbox's kanban_root in
    test_upgrade_two_phase_behavioral.py.

Every upgrade.sh invocation passes ``--dev-tree <source repo>`` explicitly
(in addition to the outer PGAI_DEV_TREE_PATH being scrubbed from the
inherited environment) so the sandbox's own dev tree is used regardless of
what the host shell has configured. PGAI_AGENT_KANBAN_ROOT_PATH and HOME
are redirected under tmp_path for every run; no network access is
required (the fixture repo is fully local).

NAMING CONVENTION
-----------------
Function and fixture names describe the behavior under test, not the
requirements-doc ticket that motivated them.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess

import pytest
from tests.fixtures.binary_guard import require_binary


@pytest.fixture(autouse=True)
def _require_git() -> None:
    """Skip every test in this module when git is not on PATH.

    Every test here builds, inspects, or drives a real git repository; a
    module-wide autouse guard keeps that requirement in one place instead
    of repeating a per-test check at each git-dependent call site.
    """
    require_binary("git", label="upgrade-forward-only-and-preservation")

# ---------------------------------------------------------------------------
# Paths to the real scripts and libraries under test
# ---------------------------------------------------------------------------

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent
_REPO_ROOT = _TEAM_DIR.parent
_REAL_UPGRADE_SH = _TEAM_DIR / "scripts" / "upgrade.sh"

_REAL_LIBS = [
    _TEAM_DIR / "scripts" / "lib" / "argparse.sh",
    _TEAM_DIR / "scripts" / "lib" / "env_bootstrap.sh",
    _TEAM_DIR / "scripts" / "lib" / "ini_parser.sh",
    _TEAM_DIR / "scripts" / "lib" / "project_paths.sh",
    _TEAM_DIR / "scripts" / "lib" / "error_contract.sh",
    _TEAM_DIR / "scripts" / "lib" / "temp.sh",
    _TEAM_DIR / "scripts" / "lib" / "version_stamp.sh",
    _TEAM_DIR / "scripts" / "lib" / "subagent_deploy.sh",
    _TEAM_DIR / "scripts" / "lib" / "script_common.sh",
]

_TIMEOUT = 120

# Ascending fixture tags standing in for the requirements doc's "vA < vB < vC".
# Namespaced away from this project's own real release tags (v0.x/v1.x/v2.x)
# so there is no risk of colliding with a genuine semver tag if fixture
# construction is ever pointed at a non-throwaway repo by mistake. Deliberately
# distinct from test_install_version_targeting.py's fixture tags so the two
# suites' failure output is never ambiguous about which side produced it.
_TAG_A = "v200.0.0"
_TAG_B = "v200.1.0"
_TAG_C = "v200.2.0"
_ASCENDING_TAGS = [_TAG_A, _TAG_B, _TAG_C]

_FIXTURE_COPY_IGNORE = shutil.ignore_patterns(
    ".git", "__pycache__", "*.pyc", ".pytest_cache"
)


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------


def _run_git(repo: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )


def _git_state_snapshot(repo: pathlib.Path) -> tuple[str, str]:
    """Return (checked-out branch, worktree list) for *repo*.

    These two values must be byte-identical before and after any upgrade.sh
    invocation against *repo* (as --dev-tree), whether the invocation lands
    a version, is refused by the forward-only rule, or fails outright — the
    operator's own checkout must never be touched.
    """
    branch = _run_git(repo, "symbolic-ref", "--short", "HEAD").stdout
    worktrees = _run_git(repo, "worktree", "list", "--porcelain").stdout
    return branch, worktrees


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _build_tagged_source_repo(
    tmp_path: pathlib.Path,
    subdir: str,
    tags: list[str] = _ASCENDING_TAGS,
) -> pathlib.Path:
    """Build a throwaway git repo standing in for the operator's dev tree.

    A full copy of this dev tree (so upgrade.sh and every library it
    sources at version-targeted-upgrade time — version_resolve.sh,
    version_stamp.sh, the subagents manifest, team/templates/, etc. — is
    real, unmodified content), with one commit per tag in *tags* (ascending
    order), each commit's only change being the committed VERSION file set
    to that tag's value. The repo's final checked-out branch is "main",
    pointing at the LAST (newest) tag's commit — this is what lets a
    version-targeted upgrade to an OLDER tag be a meaningful "lands the
    older tag's tree even though the checked-out branch sits at the
    newest" proof, mirroring
    test_install_version_targeting.py's _build_tagged_source_repo.
    """
    repo = tmp_path / subdir
    shutil.copytree(_REPO_ROOT, repo, ignore=_FIXTURE_COPY_IGNORE)

    _run_git(repo, "init", "-b", "main")
    _run_git(repo, "config", "user.email", "fixture@example.com")
    _run_git(repo, "config", "user.name", "Fixture Repo")

    for tag in tags:
        (repo / "VERSION").write_text(f"{tag}\n", encoding="utf-8")
        _run_git(repo, "add", "-A")
        _run_git(repo, "commit", "-m", f"Set VERSION to {tag}")
        _run_git(repo, "tag", tag)

    return repo


def _build_installed_kanban_root(
    tmp_path: pathlib.Path, subdir: str, installed_version: str
) -> pathlib.Path:
    """Build a minimal live-layout install rooted at *subdir* under *tmp_path*.

    Contains the installed (phase-1) upgrade.sh plus the library helpers it
    sources directly, and a committed VERSION file recording the version
    the fixture pretends is "currently installed". Mirrors
    _build_two_phase_sandbox's kanban_root builder in
    test_upgrade_two_phase_behavioral.py.
    """
    kanban_root = tmp_path / subdir
    lib_dir = kanban_root / "scripts" / "lib"
    lib_dir.mkdir(parents=True)

    for lib_src in _REAL_LIBS:
        assert lib_src.exists(), (
            f"fixture setup: expected library source file missing: {lib_src} "
            f"(the installed sandbox would silently omit it, producing a "
            f"confusing 'No such file or directory' from the sourcing "
            f"script instead of a clear fixture-setup failure)"
        )
        shutil.copy2(lib_src, lib_dir / lib_src.name)

    installed_upgrade = kanban_root / "scripts" / "upgrade.sh"
    shutil.copy2(_REAL_UPGRADE_SH, installed_upgrade)
    installed_upgrade.chmod(0o755)

    (kanban_root / "VERSION").write_text(f"{installed_version}\n", encoding="utf-8")

    return kanban_root


# Relative paths (from kanban_root) covering every protected-file category
# named in the requirements doc: secrets, kanban.cfg, shell-env, projects/
# (including a per-project project.cfg, which is protected dynamically via
# the "projects/<name>/project.cfg" pattern), plus pseudocron.env which is
# statically protected alongside them.
_PROTECTED_FIXTURE_CONTENT: dict[str, str] = {
    "kanban.cfg": (
        "# fixture kanban.cfg — must not change across a version-targeted upgrade\n"
        "[paths]\n"
        "placeholder = true\n"
    ),
    "secrets": (
        "# fixture secrets — must not change across a version-targeted upgrade\n"
        "API_KEY=fixture-marker-value\n"
    ),
    "shell-env": (
        "# fixture shell-env — must not change across a version-targeted upgrade\n"
        "export FIXTURE_MARKER=1\n"
    ),
    "pseudocron.env": (
        "# fixture pseudocron.env — must not change across a version-targeted upgrade\n"
        "FIXTURE_MARKER=1\n"
    ),
    "projects.cfg": (
        "# fixture projects.cfg — must not change across a version-targeted upgrade\n"
        "[general]\n"
        "placeholder = true\n"
    ),
    "projects/demo-project/project.cfg": (
        "# fixture per-project config — must not change across a version-targeted upgrade\n"
        "[project]\n"
        "placeholder = true\n"
    ),
    "projects/demo-project/state.txt": (
        "fixture per-project state — must not change across a version-targeted upgrade\n"
    ),
}


def _populate_protected_fixture_content(kanban_root: pathlib.Path) -> None:
    for rel_path, text in _PROTECTED_FIXTURE_CONTENT.items():
        dst = kanban_root / rel_path
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(text, encoding="utf-8")


def _read_protected_fixture_content(kanban_root: pathlib.Path) -> dict[str, str]:
    return {
        rel_path: (kanban_root / rel_path).read_text(encoding="utf-8")
        for rel_path in _PROTECTED_FIXTURE_CONTENT
    }


def _run_upgrade(
    kanban_root: pathlib.Path,
    dev_tree: pathlib.Path,
    tmp_path: pathlib.Path,
    *,
    version_arg: str | None = None,
    home: pathlib.Path | None = None,
    pgai_temp: pathlib.Path | None = None,
) -> subprocess.CompletedProcess:
    """Run the real installed upgrade.sh out of *kanban_root* against *dev_tree*.

    Uses ``--force --no-backup`` (non-interactive, skip tarball creation) and
    an explicit ``--dev-tree`` pointing at *dev_tree* so the sandbox's own
    dev tree is used regardless of what the host environment has configured.
    PGAI_DEV_TREE_PATH is stripped from the inherited environment for the
    same reason. *home* / *pgai_temp* default to fresh subdirectories under
    *tmp_path* when a caller does not supply its own (callers running a
    sequence of upgrades against the same kanban_root pass the same
    home/pgai_temp across calls so state — e.g. the crontab-tier
    detection — persists exactly as it would for a real repeated upgrade).
    """
    if home is None:
        home = tmp_path / "home"
    if pgai_temp is None:
        pgai_temp = tmp_path / "pgai_temp"
    home.mkdir(parents=True, exist_ok=True)
    pgai_temp.mkdir(parents=True, exist_ok=True)

    args = ["bash", str(kanban_root / "scripts" / "upgrade.sh")]
    if version_arg is not None:
        args.append(version_arg)
    args += ["--force", "--no-backup", "--dev-tree", str(dev_tree)]

    env = dict(os.environ)
    env.pop("PGAI_DEV_TREE_PATH", None)
    env.update(
        {
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(kanban_root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(pgai_temp),
            "HOME": str(home),
        }
    )

    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
        env=env,
    )


def _combined_output(result: subprocess.CompletedProcess) -> str:
    return f"{result.stdout}\n{result.stderr}"


def _installed_version(kanban_root: pathlib.Path) -> str:
    return (kanban_root / "VERSION").read_text(encoding="utf-8").strip()


# ---------------------------------------------------------------------------
# Acceptance 1 (upgrade-side AC1): the full forward-only sequence — land vB
# while vC exists, refuse older (vA) and equal-relay (vB) with the
# documented messages, then land vC.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _REAL_UPGRADE_SH.exists(),
    reason=f"Real upgrade.sh not found at {_REAL_UPGRADE_SH}; skipping.",
)
def test_forward_only_sequence_lands_vb_refuses_older_and_equal_relay_then_lands_vc(
    tmp_path: pathlib.Path,
) -> None:
    """``upgrade.sh vB`` lands vB while vC exists; ``upgrade.sh vA`` (older)
    and ``upgrade.sh vB`` (equal re-lay) are both refused with the
    documented messages and change nothing; ``upgrade.sh vC`` then lands
    vC.

    Step order (a single sequence against one evolving install, per the
    Anti-pattern 3 "multi-cycle" exception — the whole sequence lives in
    one test function and is documented here):
      1. Install starts at vA (fixture VERSION, no upgrade run yet).
      2. upgrade.sh vB -> succeeds, VERSION becomes vB.
      3. upgrade.sh vA -> refused (older than installed vB), VERSION stays vB.
      4. upgrade.sh vB -> refused (equal re-lay of installed vB), VERSION stays vB.
      5. upgrade.sh vC -> succeeds, VERSION becomes vC.
    """
    source_repo = _build_tagged_source_repo(tmp_path, "source_repo")
    kanban_root = _build_installed_kanban_root(
        tmp_path, "kanban_root", installed_version=_TAG_A
    )
    home = tmp_path / "home"
    pgai_temp = tmp_path / "pgai_temp"

    # Step 2: land vB while vC still exists in the source repo.
    result_b = _run_upgrade(
        kanban_root, source_repo, tmp_path,
        version_arg=_TAG_B, home=home, pgai_temp=pgai_temp,
    )
    output_b = _combined_output(result_b)
    assert result_b.returncode == 0, f"upgrade.sh {_TAG_B} failed:\n{output_b}"
    assert f"resolved to tag '{_TAG_B}'" in output_b, output_b
    assert _installed_version(kanban_root) == _TAG_B, (
        f"Installed VERSION must equal the requested tag {_TAG_B!r} after "
        f"landing it, even though the newer tag {_TAG_C!r} also exists in "
        f"the source repo.\nGot: {_installed_version(kanban_root)!r}"
    )
    existing_tags = _run_git(source_repo, "tag", "-l").stdout.split()
    assert _TAG_C in existing_tags, (
        f"{_TAG_C!r} must still exist in the source repo after landing "
        f"{_TAG_B!r} — landing an older-than-newest tag must not delete or "
        f"hide newer tags.\nExisting tags: {existing_tags}"
    )

    # Step 3: refuse an older-than-installed target (vA < installed vB).
    result_older = _run_upgrade(
        kanban_root, source_repo, tmp_path,
        version_arg=_TAG_A, home=home, pgai_temp=pgai_temp,
    )
    output_older = _combined_output(result_older)
    assert result_older.returncode != 0, (
        f"upgrade.sh {_TAG_A} must be refused: {_TAG_A!r} is older than the "
        f"installed version {_TAG_B!r}.\n{output_older}"
    )
    assert "is OLDER than the installed version" in output_older, output_older
    assert (
        "upgrade.sh enforces a forward-only rule: it never moves an existing "
        "installation backwards in version." in output_older
    ), output_older
    assert "There is no --force override for direction" in output_older, output_older
    assert f"./install.sh {_TAG_A} --force" in output_older, (
        f"Refusal must point at the sanctioned downgrade path (a fresh "
        f"install targeting the requested version).\n{output_older}"
    )
    assert "Nothing has been deposited" in output_older, output_older
    assert _installed_version(kanban_root) == _TAG_B, (
        f"Installed VERSION must remain {_TAG_B!r} after the refused "
        f"older-target upgrade — nothing may be deposited on refusal.\n"
        f"Got: {_installed_version(kanban_root)!r}"
    )

    # Step 4: refuse an equal-version re-lay (vB re-lay while installed is vB).
    result_equal = _run_upgrade(
        kanban_root, source_repo, tmp_path,
        version_arg=_TAG_B, home=home, pgai_temp=pgai_temp,
    )
    output_equal = _combined_output(result_equal)
    assert result_equal.returncode != 0, (
        f"upgrade.sh {_TAG_B} must be refused as an equal-version re-lay "
        f"(installed is already {_TAG_B!r}).\n{output_equal}"
    )
    assert "is ALREADY the installed version" in output_equal, output_equal
    assert (
        "upgrade.sh's forward-only rule refuses same-version re-lays as well "
        "as downgrades." in output_equal
    ), output_equal
    assert "./install.sh --force" in output_equal, (
        f"Equal-relay refusal must point at install.sh --force as the "
        f"repair path.\n{output_equal}"
    )
    assert "Nothing has been deposited" in output_equal, output_equal
    assert _installed_version(kanban_root) == _TAG_B, (
        f"Installed VERSION must remain {_TAG_B!r} after the refused "
        f"equal-relay upgrade.\nGot: {_installed_version(kanban_root)!r}"
    )

    # Step 5: land vC.
    result_c = _run_upgrade(
        kanban_root, source_repo, tmp_path,
        version_arg=_TAG_C, home=home, pgai_temp=pgai_temp,
    )
    output_c = _combined_output(result_c)
    assert result_c.returncode == 0, f"upgrade.sh {_TAG_C} failed:\n{output_c}"
    assert f"resolved to tag '{_TAG_C}'" in output_c, output_c
    assert _installed_version(kanban_root) == _TAG_C, (
        f"Installed VERSION must equal {_TAG_C!r} after landing it.\n"
        f"Got: {_installed_version(kanban_root)!r}"
    )


# ---------------------------------------------------------------------------
# Acceptance 2 (upgrade-side AC4/identity): the success summary narrates
# "upgraded <installed> -> <target> (from tag <tag>)".
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _REAL_UPGRADE_SH.exists(),
    reason=f"Real upgrade.sh not found at {_REAL_UPGRADE_SH}; skipping.",
)
def test_upgrade_summary_narrates_installed_arrow_target_from_tag(
    tmp_path: pathlib.Path,
) -> None:
    """A successful version-targeted upgrade's stdout narrates the
    installed -> target transition and names the resolved tag, in the
    exact form: ``upgraded <installed> -> <target> (from tag <tag>)``."""
    source_repo = _build_tagged_source_repo(tmp_path, "source_repo")
    kanban_root = _build_installed_kanban_root(
        tmp_path, "kanban_root", installed_version=_TAG_A
    )

    result = _run_upgrade(kanban_root, source_repo, tmp_path, version_arg=_TAG_B)
    output = _combined_output(result)

    assert result.returncode == 0, f"upgrade.sh {_TAG_B} failed:\n{output}"

    expected_narration = f"upgraded {_TAG_A} -> {_TAG_B} (from tag {_TAG_B})"
    assert expected_narration in output, (
        f"Expected the upgrade summary to narrate {expected_narration!r}.\n"
        f"Full output:\n{output}"
    )


# ---------------------------------------------------------------------------
# Acceptance 3 (upgrade-side AC5): protected files (secrets, kanban.cfg,
# shell-env, pseudocron.env, projects.cfg, projects/ including a nested
# per-project project.cfg) are byte-identical before and after a
# version-targeted upgrade.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _REAL_UPGRADE_SH.exists(),
    reason=f"Real upgrade.sh not found at {_REAL_UPGRADE_SH}; skipping.",
)
def test_protected_files_unchanged_across_version_targeted_upgrade(
    tmp_path: pathlib.Path,
) -> None:
    """secrets, shell-env, kanban.cfg, pseudocron.env, projects.cfg, and the
    entire projects/ tree (including a nested per-project project.cfg) are
    byte-identical before and after a successful version-targeted upgrade —
    confirming the version-targeted code path introduces no change to the
    existing preservation contract."""
    source_repo = _build_tagged_source_repo(tmp_path, "source_repo")
    kanban_root = _build_installed_kanban_root(
        tmp_path, "kanban_root", installed_version=_TAG_A
    )
    _populate_protected_fixture_content(kanban_root)
    before = _read_protected_fixture_content(kanban_root)

    result = _run_upgrade(kanban_root, source_repo, tmp_path, version_arg=_TAG_B)
    output = _combined_output(result)
    assert result.returncode == 0, (
        f"Setup: the version-targeted upgrade must itself succeed.\n{output}"
    )
    assert _installed_version(kanban_root) == _TAG_B, (
        f"Setup: VERSION must have advanced to {_TAG_B!r}.\n"
        f"Got: {_installed_version(kanban_root)!r}"
    )

    after = _read_protected_fixture_content(kanban_root)
    for rel_path in _PROTECTED_FIXTURE_CONTENT:
        assert after[rel_path] == before[rel_path], (
            f"Protected path {rel_path!r} changed across a version-targeted "
            f"upgrade.\nBefore:\n{before[rel_path]!r}\nAfter:\n{after[rel_path]!r}"
        )


# ---------------------------------------------------------------------------
# Acceptance 4 (upgrade-side AC4): source-cleanliness — the source repo's
# checked-out branch and worktree list are byte-identical before/after both
# a success and a refused (older-target) upgrade.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _REAL_UPGRADE_SH.exists(),
    reason=f"Real upgrade.sh not found at {_REAL_UPGRADE_SH}; skipping.",
)
def test_source_repo_branch_and_worktree_list_unchanged_across_success_and_refusal_upgrades(
    tmp_path: pathlib.Path,
) -> None:
    """The source repo's checked-out branch and ``git worktree list``
    output are byte-identical before any upgrade.sh run, after a
    successful version-targeted upgrade, and after a refused
    (older-than-installed) one — proving the operator's checkout is never
    touched on either path, and no detached worktree is leaked."""
    source_repo = _build_tagged_source_repo(tmp_path, "source_repo")
    kanban_root = _build_installed_kanban_root(
        tmp_path, "kanban_root", installed_version=_TAG_B
    )

    baseline = _git_state_snapshot(source_repo)

    success_result = _run_upgrade(
        kanban_root, source_repo, tmp_path, version_arg=_TAG_C
    )
    assert success_result.returncode == 0, (
        f"Setup: the success-path upgrade must itself succeed.\n"
        f"{_combined_output(success_result)}"
    )
    after_success = _git_state_snapshot(source_repo)
    assert after_success == baseline, (
        f"Source repo's checked-out branch / worktree list changed after a "
        f"SUCCESSFUL version-targeted upgrade.\n"
        f"Before: {baseline}\nAfter:  {after_success}"
    )

    # Installed is now vC; requesting vA (older than installed) is refused.
    failure_result = _run_upgrade(
        kanban_root, source_repo, tmp_path, version_arg=_TAG_A
    )
    assert failure_result.returncode != 0, (
        f"Setup: the refusal-path upgrade must itself fail.\n"
        f"{_combined_output(failure_result)}"
    )
    after_failure = _git_state_snapshot(source_repo)
    assert after_failure == baseline, (
        f"Source repo's checked-out branch / worktree list changed after a "
        f"REFUSED (older-target) version-targeted upgrade.\n"
        f"Before: {baseline}\nAfter:  {after_failure}"
    )

    # No leftover worktree metadata after either run.
    assert (
        "worktree " in after_failure[1]
        and after_failure[1].count("worktree ") == 1
    ), (
        f"Exactly one worktree (the source repo's own) must be registered "
        f"after both runs — no leaked detached worktrees from either the "
        f"success or the refusal path.\ngit worktree list --porcelain:\n"
        f"{after_failure[1]}"
    )


# ---------------------------------------------------------------------------
# A version-targeted upgrade stamps VERSION_DETAIL with the resolved tag.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _REAL_UPGRADE_SH.exists(),
    reason=f"Real upgrade.sh not found at {_REAL_UPGRADE_SH}; skipping.",
)
def test_version_targeted_upgrade_stamps_resolved_tag_in_version_detail(
    tmp_path: pathlib.Path,
) -> None:
    """``upgrade.sh vB`` writes VERSION_DETAIL naming vB and vB's commit.

    The upgrade lays down from a detached worktree of the source repo, whose
    ``.git`` is a pointer file; the checked-out branch sits at the newer vC.
    VERSION_DETAIL must still be written and start with the resolved tag.
    """
    source_repo = _build_tagged_source_repo(tmp_path, "source_repo")
    tag_sha = _run_git(source_repo, "rev-parse", f"{_TAG_B}^{{commit}}").stdout.strip()
    kanban_root = _build_installed_kanban_root(
        tmp_path, "kanban_root", installed_version=_TAG_A
    )

    result = _run_upgrade(kanban_root, source_repo, tmp_path, version_arg=_TAG_B)
    output = _combined_output(result)

    assert result.returncode == 0, f"upgrade.sh {_TAG_B} failed:\n{output}"
    assert _installed_version(kanban_root) == _TAG_B, output

    detail_file = kanban_root / "VERSION_DETAIL"
    assert detail_file.exists(), (
        f"VERSION_DETAIL was not written by a version-targeted upgrade:\n{output}"
    )
    assert detail_file.read_text(encoding="utf-8") == (
        f"{_TAG_B}  deposit-sha={tag_sha}\n"
    ), detail_file.read_text(encoding="utf-8")
