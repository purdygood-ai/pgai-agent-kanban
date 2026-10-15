"""
test_verify_rc_state_two_project_prefix_fixture.py
====================================================
Integration tests for team/scripts/verify-rc-state.sh proving the checker's
branch-prefix handling and release-state field validation against BOTH
topologies (a project with [project] branch_prefix set, and a bare-prefix
project) side by side in one fixture kanban root -- the two-project shape
this coverage requires, widening the single-check precedent in
test_verify_rc_state_check_e_branch_prefix.py (which only ever builds one
project per test) into a shape that can catch one project's resolved
branch name leaking into the other's.

Coverage:
  - The same seeded inconsistency (a malformed Active RC field) produces the
    identical finding text on a prefixed and a bare-prefix project living in
    the same fixture kanban root, sourced from the one shared validator
    (pgai_agent_kanban.cm.read_state_field) -- proof neither project's
    result depends on a second, locally re-implemented format judgement.
  - The identical literal branch name ("rc/v0.0.9") produces the
    stray-unprefixed-branch [WARN] on the prefixed project (where it is a
    stray relative to the configured ai_rc/* glob) but produces no such
    warning on the bare-prefix project (where it is simply the correctly
    named, matching Active RC branch) -- run against both projects in the
    same fixture kanban root, so the two resolutions cannot be explained by
    two separate, unrelated fixture roots.
  - The script's exit-code contract (0 / 1 / 2) is unchanged, asserted
    directly against fixture runs built in this file rather than assumed
    from the pre-existing single-project suites.

Also covers --project-NAME resolution (added alongside the branch-prefix
coverage above rather than in a new file, per this suite's own existing-
test-home convention -- note this is a different "prefix" than the branch-
prefix coverage above: this is pp_resolve_project_name's exact-then-
unambiguous-prefix match against projects.cfg, not [project] branch_prefix):

  - An exact project name and an unambiguous prefix of that name resolve to
    the same project and produce byte-identical reports.
  - An unregistered project name refuses dual-channel (error:/message:/
    remedy:), exits 2, and renders no OK/WARN/ERROR report at all.

Design notes:
  - All tests use tmp_path for scratch; no bare /tmp paths.
  - verify-rc-state.sh is invoked via subprocess against a temporary kanban
    root and temporary local git repos -- never the live install.
  - Fixture construction mirrors
    test_verify_rc_state_check_e_branch_prefix.py's pattern, extended to
    register two projects (one prefixed, one bare) under the same kanban
    root instead of one project per fixture root.
"""

from __future__ import annotations

import os
import pathlib
import subprocess

from pgai_agent_kanban.cm.read_state_field import FieldKind, _ACCEPTED_FORMS
import pytest
from tests.fixtures.binary_guard import require_binary


@pytest.fixture(autouse=True)
def _require_git() -> None:
    """Skip every test in this module when git is not on PATH.

    Every test here builds, inspects, or drives a real git repository; a
    module-wide autouse guard keeps that requirement in one place instead
    of repeating a per-test check at each git-dependent call site.
    """
    require_binary("git", label="verify-rc-state-two-project-prefix-fixture")

# ---------------------------------------------------------------------------
# Path helpers — resolve from this file's own location.
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_VERIFY_SCRIPT = _TEAM_DIR / "scripts" / "verify-rc-state.sh"


def _init_local_repo(repo_path: pathlib.Path) -> None:
    """Initialise a minimal local git repo with one commit -- no remote."""
    repo_path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo_path, check=True,
    )
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo_path, check=True)
    (repo_path / "README.md").write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo_path, check=True)


def _create_branch(repo_path: pathlib.Path, branch_name: str) -> None:
    """Create branch_name at the repo's current HEAD without checking it out."""
    subprocess.run(["git", "branch", branch_name], cwd=repo_path, check=True)


def _write_project_cfg(
    proj_root: pathlib.Path,
    project_name: str,
    repo_path: pathlib.Path,
    branch_prefix: str = "",
) -> None:
    (proj_root / "tasks" / "queues").mkdir(parents=True, exist_ok=True)
    cfg_lines = [
        "[project]",
        f"project_name = {project_name}",
        f"dev_tree_path = {repo_path}",
        f"git_repo_url = git@example.com:{project_name}.git",
        "git_remote_name = origin",
        "workflow_type = release",
        "is_self_build = false",
        "push_to_remote = false",
    ]
    if branch_prefix:
        cfg_lines.append(f"branch_prefix = {branch_prefix}")
    cfg_lines += [
        "",
        "[versioning]",
        "max_patch = 99",
        "max_minor = 9",
        "max_major = 0",
        "",
    ]
    (proj_root / "project.cfg").write_text("\n".join(cfg_lines), encoding="utf-8")


def _build_dual_project_kanban_root(
    parent: pathlib.Path,
    prefixed_name: str,
    prefixed_repo: pathlib.Path,
    bare_name: str,
    bare_repo: pathlib.Path,
    branch_prefix: str = "ai_",
) -> pathlib.Path:
    """Build one fixture kanban root registering BOTH a prefixed project
    (branch_prefix set) and a bare-prefix project (no branch_prefix) side
    by side -- so a single test run can prove the script does not leak one
    project's prefix into the other's branch resolution."""
    root = parent / "kanban_root"
    (root / "logs").mkdir(parents=True, exist_ok=True)
    (root / "locks").mkdir(parents=True, exist_ok=True)
    (root / "kanban.cfg").write_text(
        "[paths]\n\n[chain]\npm_mode = automatic\n\n[wake]\nmax_tasks_per_wake = 1\n",
        encoding="utf-8",
    )
    (root / "projects.cfg").write_text(
        f"[project:{prefixed_name}]\npriority=1\ndescription=verify test (prefixed)\nenabled=true\n\n"
        f"[project:{bare_name}]\npriority=2\ndescription=verify test (bare)\nenabled=true\n",
        encoding="utf-8",
    )

    prefixed_proj = root / "projects" / prefixed_name
    _write_project_cfg(prefixed_proj, prefixed_name, prefixed_repo, branch_prefix)

    bare_proj = root / "projects" / bare_name
    _write_project_cfg(bare_proj, bare_name, bare_repo, branch_prefix="")

    return root


def _write_release_state(
    project_root: pathlib.Path,
    active_rc: str,
    last_released: str = "none",
) -> None:
    (project_root / "release-state.md").write_text(
        "# Release State\n\n"
        f"## Active RC\n{active_rc}\n\n"
        "## RC Opened At\nnone\n\n"
        "## RC Opened By Task\nnone\n\n"
        f"## Last Released\n{last_released}\n",
        encoding="utf-8",
    )


def _run_verify_rc_state(
    kanban_root: pathlib.Path, project_name: str
) -> subprocess.CompletedProcess:
    base_env = dict(os.environ)
    base_env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    base_env["KANBAN_ROOT"] = str(kanban_root)
    base_env.pop("PGAI_DEV_TREE_PATH", None)
    return subprocess.run(
        ["bash", str(_VERIFY_SCRIPT), project_name, "--verbose"],
        capture_output=True, text=True, env=base_env, timeout=60,
    )


# ---------------------------------------------------------------------------
# Malformed Active RC: identical finding text on both topologies.
# ---------------------------------------------------------------------------


def test_dual_fixture_malformed_active_rc_same_finding_text_on_both_projects(
    tmp_path: pathlib.Path,
) -> None:
    """A release-state.md with a malformed Active RC value ('NONE' instead
    of the schema's literal 'none' or a version) produces the identical
    [ERROR] accepted-forms text -- sourced from the one shared validator --
    on both the prefixed and the bare-prefix project in the same fixture
    kanban root, and exit code 1 on both."""
    prefixed_repo = tmp_path / "repo_prefixed"
    bare_repo = tmp_path / "repo_bare"
    _init_local_repo(prefixed_repo)
    _init_local_repo(bare_repo)

    kanban_root = _build_dual_project_kanban_root(
        tmp_path,
        "dualtest_prefixed_malformed", prefixed_repo,
        "dualtest_bare_malformed", bare_repo,
    )
    _write_release_state(
        kanban_root / "projects" / "dualtest_prefixed_malformed", "NONE"
    )
    _write_release_state(
        kanban_root / "projects" / "dualtest_bare_malformed", "NONE"
    )

    prefixed_result = _run_verify_rc_state(kanban_root, "dualtest_prefixed_malformed")
    bare_result = _run_verify_rc_state(kanban_root, "dualtest_bare_malformed")

    accepted_forms = _ACCEPTED_FORMS[FieldKind.VERSION_OR_NONE]

    for result in (prefixed_result, bare_result):
        assert result.returncode == 1, result.stdout + result.stderr
        error_lines = [
            line for line in result.stdout.splitlines()
            if line.startswith("[ERROR]") and "Active RC" in line
        ]
        assert error_lines, result.stdout
        assert accepted_forms in error_lines[0], error_lines[0]
        assert "'NONE'" in error_lines[0], error_lines[0]

    prefixed_error = next(
        line for line in prefixed_result.stdout.splitlines()
        if line.startswith("[ERROR]") and "Active RC" in line
    )
    bare_error = next(
        line for line in bare_result.stdout.splitlines()
        if line.startswith("[ERROR]") and "Active RC" in line
    )
    # Compare everything up to the trailing "— file: <path>" segment, since
    # the two release-state.md paths differ by project name/tmp_path even
    # though the seeded inconsistency and its finding text are identical.
    prefixed_msg = prefixed_error.split(" — file: ")[0]
    bare_msg = bare_error.split(" — file: ")[0]
    assert prefixed_msg == bare_msg, (
        "the same seeded inconsistency must produce byte-identical finding "
        f"text (aside from the file path) on both topologies:\n"
        f"prefixed: {prefixed_msg!r}\nbare:     {bare_msg!r}"
    )


# ---------------------------------------------------------------------------
# Stray-unprefixed-branch warning: fires on the prefixed project, does not
# fire on the bare-prefix project, for the identical literal branch name.
# ---------------------------------------------------------------------------


def test_dual_fixture_same_branch_name_warns_only_on_prefixed_project(
    tmp_path: pathlib.Path,
) -> None:
    """The literal branch name 'rc/v0.0.9' is a stray relative to the
    prefixed project's configured ai_rc/* glob (so it must [WARN]), but on
    the bare-prefix project in the SAME fixture kanban root it is simply
    the correctly-named Active RC branch (so it must produce no
    stray-branch warning at all) -- both projects run in one test to prove
    the prefixed project's glob does not leak into or otherwise affect the
    bare project's resolution."""
    prefixed_repo = tmp_path / "repo_prefixed"
    bare_repo = tmp_path / "repo_bare"
    _init_local_repo(prefixed_repo)
    _init_local_repo(bare_repo)

    # Prefixed project: active branch matches the configured prefix, plus an
    # orphaned bare rc/v0.0.9 branch left over from before the prefix.
    _create_branch(prefixed_repo, "ai_rc/v2.11.1")
    _create_branch(prefixed_repo, "rc/v0.0.9")

    # Bare project: rc/v0.0.9 is simply its Active RC branch, correctly named.
    _create_branch(bare_repo, "rc/v0.0.9")

    kanban_root = _build_dual_project_kanban_root(
        tmp_path,
        "dualtest_prefixed_stray", prefixed_repo,
        "dualtest_bare_stray", bare_repo,
    )
    _write_release_state(
        kanban_root / "projects" / "dualtest_prefixed_stray", "v2.11.1"
    )
    _write_release_state(
        kanban_root / "projects" / "dualtest_bare_stray", "v0.0.9"
    )

    prefixed_result = _run_verify_rc_state(kanban_root, "dualtest_prefixed_stray")
    bare_result = _run_verify_rc_state(kanban_root, "dualtest_bare_stray")

    # Prefixed project: [OK] for the active branch, [WARN] naming the stray.
    assert prefixed_result.returncode == 0, (
        prefixed_result.stdout + prefixed_result.stderr
    )
    assert (
        "[OK]    Active RC v2.11.1 has matching local branch: ai_rc/v2.11.1"
        in prefixed_result.stdout
    ), prefixed_result.stdout
    prefixed_warn_lines = [
        line for line in prefixed_result.stdout.splitlines()
        if line.startswith("[WARN]") and "rc/v0.0.9" in line
    ]
    assert prefixed_warn_lines, prefixed_result.stdout
    assert "ai_rc/v0.0.9" not in prefixed_warn_lines[0], prefixed_warn_lines[0]

    # Bare project: rc/v0.0.9 matches Active RC cleanly -- [OK], no WARN
    # naming rc/v0.0.9 at all (it is not a stray on this topology).
    assert bare_result.returncode == 0, bare_result.stdout + bare_result.stderr
    assert (
        "[OK]    Active RC v0.0.9 has matching local branch: rc/v0.0.9"
        in bare_result.stdout
    ), bare_result.stdout
    bare_warn_lines = [
        line for line in bare_result.stdout.splitlines()
        if line.startswith("[WARN]") and "rc/v0.0.9" in line
    ]
    assert not bare_warn_lines, (
        f"bare-prefix project must not warn about its own correctly-named "
        f"Active RC branch:\n{bare_result.stdout}"
    )


# ---------------------------------------------------------------------------
# Exit-code contract: 0 / 1 / 2, each asserted directly in this fixture.
# ---------------------------------------------------------------------------


def test_dual_fixture_exit_code_zero_with_warnings_only(
    tmp_path: pathlib.Path,
) -> None:
    """A run that produces WARN findings only (no ERROR) still exits 0 on
    both topologies -- WARN findings never affect the exit code."""
    prefixed_repo = tmp_path / "repo_prefixed"
    bare_repo = tmp_path / "repo_bare"
    _init_local_repo(prefixed_repo)
    _init_local_repo(bare_repo)

    # Active RC=none but a stray branch exists on each -- WARN only.
    _create_branch(prefixed_repo, "ai_rc/v0.0.1")
    _create_branch(bare_repo, "rc/v0.0.1")

    kanban_root = _build_dual_project_kanban_root(
        tmp_path,
        "dualtest_prefixed_exit0", prefixed_repo,
        "dualtest_bare_exit0", bare_repo,
    )
    _write_release_state(kanban_root / "projects" / "dualtest_prefixed_exit0", "none")
    _write_release_state(kanban_root / "projects" / "dualtest_bare_exit0", "none")

    prefixed_result = _run_verify_rc_state(kanban_root, "dualtest_prefixed_exit0")
    bare_result = _run_verify_rc_state(kanban_root, "dualtest_bare_exit0")

    assert prefixed_result.returncode == 0, (
        prefixed_result.stdout + prefixed_result.stderr
    )
    assert "[WARN]" in prefixed_result.stdout, prefixed_result.stdout
    assert bare_result.returncode == 0, bare_result.stdout + bare_result.stderr
    assert "[WARN]" in bare_result.stdout, bare_result.stdout


def test_dual_fixture_exit_code_one_with_any_error(
    tmp_path: pathlib.Path,
) -> None:
    """A run that produces at least one ERROR finding (Active RC names a
    version with no matching branch) exits 1 on both topologies."""
    prefixed_repo = tmp_path / "repo_prefixed"
    bare_repo = tmp_path / "repo_bare"
    _init_local_repo(prefixed_repo)
    _init_local_repo(bare_repo)
    # No matching rc branch created on either repo.

    kanban_root = _build_dual_project_kanban_root(
        tmp_path,
        "dualtest_prefixed_exit1", prefixed_repo,
        "dualtest_bare_exit1", bare_repo,
    )
    _write_release_state(kanban_root / "projects" / "dualtest_prefixed_exit1", "v9.9.9")
    _write_release_state(kanban_root / "projects" / "dualtest_bare_exit1", "v9.9.9")

    prefixed_result = _run_verify_rc_state(kanban_root, "dualtest_prefixed_exit1")
    bare_result = _run_verify_rc_state(kanban_root, "dualtest_bare_exit1")

    assert prefixed_result.returncode == 1, (
        prefixed_result.stdout + prefixed_result.stderr
    )
    assert "[ERROR]" in prefixed_result.stdout, prefixed_result.stdout
    assert bare_result.returncode == 1, bare_result.stdout + bare_result.stderr
    assert "[ERROR]" in bare_result.stdout, bare_result.stdout


def test_dual_fixture_exit_code_two_on_preflight_failure(
    tmp_path: pathlib.Path,
) -> None:
    """A pre-flight failure -- a project name not registered in this
    fixture kanban root -- exits 2, independent of either project's
    branch_prefix (neither project name is passed)."""
    prefixed_repo = tmp_path / "repo_prefixed"
    bare_repo = tmp_path / "repo_bare"
    _init_local_repo(prefixed_repo)
    _init_local_repo(bare_repo)

    kanban_root = _build_dual_project_kanban_root(
        tmp_path,
        "dualtest_prefixed_exit2", prefixed_repo,
        "dualtest_bare_exit2", bare_repo,
    )
    _write_release_state(kanban_root / "projects" / "dualtest_prefixed_exit2", "none")
    _write_release_state(kanban_root / "projects" / "dualtest_bare_exit2", "none")

    result = _run_verify_rc_state(kanban_root, "does_not_exist_in_this_fixture")

    assert result.returncode == 2, result.stdout + result.stderr


# ---------------------------------------------------------------------------
# --project-NAME resolution: exact name, unambiguous prefix, unknown name.
# ---------------------------------------------------------------------------

# Hyphenated so "nameprefix" is an unambiguous prefix per
# pp_resolve_project_name's hyphen-boundary rule (lib/project_paths.sh).
_NAME_PREFIX_PROJ = "nameprefix-target"
_NAME_PREFIX_SIBLING = "unrelated-sibling-project"


def test_exact_project_name_and_unambiguous_prefix_produce_identical_reports(
    tmp_path: pathlib.Path,
) -> None:
    """PROJECT=nameprefix-target (exact) and PROJECT=nameprefix
    (unambiguous prefix) resolve to the same project and produce
    byte-identical reports -- the resolver routes the positional project
    argument through exact-then-prefix matching before any path is built
    from it."""
    repo = tmp_path / "repo_nameprefix"
    sibling_repo = tmp_path / "repo_sibling_unused"
    _init_local_repo(repo)
    _init_local_repo(sibling_repo)

    kanban_root = _build_dual_project_kanban_root(
        tmp_path,
        _NAME_PREFIX_PROJ, repo,
        _NAME_PREFIX_SIBLING, sibling_repo,
        branch_prefix="",
    )
    _write_release_state(kanban_root / "projects" / _NAME_PREFIX_PROJ, "none")
    _write_release_state(kanban_root / "projects" / _NAME_PREFIX_SIBLING, "none")

    exact_result = _run_verify_rc_state(kanban_root, _NAME_PREFIX_PROJ)
    prefix_result = _run_verify_rc_state(kanban_root, "nameprefix")

    assert exact_result.returncode == prefix_result.returncode, (
        f"exact:\n{exact_result.stdout}{exact_result.stderr}\n"
        f"prefix:\n{prefix_result.stdout}{prefix_result.stderr}"
    )
    assert exact_result.stdout == prefix_result.stdout, (
        "an unambiguous prefix must produce the identical report as the "
        f"full name.\nexact:\n{exact_result.stdout}\n"
        f"prefix:\n{prefix_result.stdout}"
    )


def test_unregistered_project_name_refuses_dual_channel_with_no_report(
    tmp_path: pathlib.Path,
) -> None:
    """A project name that matches no registered project refuses with the
    resolver's dual-channel stop (error:/message:/remedy:), exits 2, and
    renders no OK/WARN/ERROR report line at all. Beyond the exit-2
    assertion in test_dual_fixture_exit_code_two_on_preflight_failure
    above, this test asserts the dual-channel fields and the absence of
    any report output."""
    repo = tmp_path / "repo_nameprefix_unknown"
    sibling_repo = tmp_path / "repo_sibling_unknown"
    _init_local_repo(repo)
    _init_local_repo(sibling_repo)

    kanban_root = _build_dual_project_kanban_root(
        tmp_path,
        _NAME_PREFIX_PROJ, repo,
        _NAME_PREFIX_SIBLING, sibling_repo,
        branch_prefix="",
    )
    _write_release_state(kanban_root / "projects" / _NAME_PREFIX_PROJ, "none")
    _write_release_state(kanban_root / "projects" / _NAME_PREFIX_SIBLING, "none")

    result = _run_verify_rc_state(kanban_root, "definitely-unregistered-project")

    assert result.returncode == 2, result.stdout + result.stderr
    assert "error:" in result.stderr, result.stderr
    assert "message:" in result.stderr, result.stderr
    assert "remedy:" in result.stderr, result.stderr
    for marker in ("[OK]", "[WARN]", "[ERROR]", "verify-rc-state:"):
        assert marker not in result.stdout, (
            f"expected no report output for an unresolved project name; "
            f"found {marker!r} in stdout:\n{result.stdout}"
        )
