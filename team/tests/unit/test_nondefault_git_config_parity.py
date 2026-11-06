"""
test_nondefault_git_config_parity.py
====================================
On a project configured with ``branch_prefix = zz_`` and
``git_remote_name = upstream``, the bash resolvers, the Python resolvers and
every caller built on them answer from that configuration, and the paired
bash/Python callers answer alike.

The fixture (tests/fixtures/nondefault_git_config.py) gives each wrong
reading its own recognisable answer: an ``ai_`` or unprefixed reading finds
the decoy ``v9.0.0`` tags and the ``ai_feature/`` / ``feature/`` branches,
and a reading through ``origin`` finds ``v1.0.0`` and the tag only
``origin.git`` holds.

Covered:

  1. ``pp_branch_prefix`` / ``pp_git_remote_name`` and every reader in
     ``lib/branch_prefix.py`` / ``lib/git_remote.py``: ``zz_`` and
     ``upstream``.
  2. OVERWATCH: ``overwatch_branch_prefix`` (and the five prefix checks
     reading through it) and ``resolution_stage`` (the prefix, RC branch and
     base branch it hands the resolution matrix) agree.
  3. Framework version: ``dashboard/lib/version.sh`` and
     ``lib/released_version.py`` read the same ``upstream/zz_main`` refs and
     the same version; ``pp_last_released_version`` and
     ``last_released_version`` agree too.
  4. ``ops/write.py`` reset deletes ``zz_feature/<task>`` only.
  5. ``ops/rc_state.py`` matches the active RC to ``zz_rc/<version>``.
  6. ``cm/cancel.py`` resolves ``zz_`` and ``upstream`` and cancels
     ``zz_rc/<version>``.
  7. ``overwatch/block_requeue.py`` resolves a bare ``rc/<version>`` source
     branch to ``zz_rc/<version>``.
  8. ``GET /projects/{name}`` reports ``zz_``.
  9. ``pm_materialize.py`` names ``zz_rc/<version>`` and ``zz_main``.
 10. ``discovery_guards.sh`` asks ``upstream`` for a tag the dev tree lacks.
 11. ``wake_autonomous_scan.sh`` records ``upstream``'s URL as the bundle's
     Git Repo.
 12. The dashboard git panes (``git-status.sh``, ``git-recent-tags.sh``)
     list the RC branches and tags ``pp_branch_prefix`` names, and a prefix
     it refuses renders exactly as no prefix does -- never a ``zz/main``.
     ``git-status.sh``'s Recent Commits section names and lists ``zz_main``,
     never a bare ``main`` beside it, and ``main`` when no prefix is set.
 13. Every Python caller (reset, block requeue, the resolution stage,
     ``GET /projects/{name}``, ``pm_materialize.py``) reads plain, quoted,
     empty, inline-comment and wrong-section spellings as ``pp_branch_prefix``
     answers them, and a value it refuses as no prefix at all.

Every bash run gets an explicit environment pointing every root variable at
the fixture's throwaway root; no test contacts a network remote (both
remotes are local bare repositories) or the live install.
"""

from __future__ import annotations

import io
import pathlib
import re
import subprocess
import sys

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from pgai_agent_kanban.cm import cancel  # noqa: E402
from pgai_agent_kanban.lib import branch_prefix, git_remote, released_version  # noqa: E402
from pgai_agent_kanban.lib.project_cfg import project_cfg_file  # noqa: E402
from pgai_agent_kanban.ops import rc_state  # noqa: E402
from pgai_agent_kanban.ops.context import OpsContext  # noqa: E402
from pgai_agent_kanban.ops.write import reset_item  # noqa: E402
from pgai_agent_kanban.overwatch import block_requeue, resolution_stage  # noqa: E402
from pgai_agent_kanban.overwatch.block_record import BlockRecord  # noqa: E402
from pgai_agent_kanban.overwatch.resolution_matrix import ResolutionMatrixError  # noqa: E402
from tests.fixtures import nondefault_git_config as nd  # noqa: E402
from tests.fixtures.binary_guard import require_binary  # noqa: E402
from tests.fixtures.route_cli_parity import build_api_client  # noqa: E402

try:
    import pm_agent.pm_materialize as pm_materialize  # installed via pm_agent package
except ImportError:
    sys.path.insert(0, str(_TEAM_DIR / "pm-agent"))
    import pm_materialize  # type: ignore[no-redef]  # noqa: E402

_LIB = _TEAM_DIR / "scripts" / "lib"
_VERSION_SH = _TEAM_DIR / "scripts" / "dashboard" / "lib" / "version.sh"
_DASHBOARD = _TEAM_DIR / "scripts" / "dashboard"
_OVERWATCH_CONFIG = _LIB / "overwatch_config.sh"
_OVERWATCH_CHECKS = _LIB / "overwatch-checks"

# The OVERWATCH checks that read the branch prefix, and the variable each sets.
_OVERWATCH_PREFIX_CHECKS = {
    "check-orphan-rc-branches": "_CORB_BRANCH_PREFIX",
    "check-stale-worktrees": "_CSW_BRANCH_PREFIX",
    "check-stale-active-rc": "_CSARC_BRANCH_PREFIX",
    "check-changelog-drift": "_CCD_BRANCH_PREFIX",
    "check-transient-api-error": "_CTAE_BRANCH_PREFIX",
}

_RC_BRANCH = f"rc/{nd.RC_VERSION}"
_FEATURE_BRANCH = f"feature/{nd.TASK_ID}"


@pytest.fixture
def fx(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> nd.NonDefaultGitConfigRoot:
    """The fixture root, with this process's environment pointed at it."""
    require_binary("git", label="nondefault-git-config-parity")
    built = nd.build_nondefault_git_config_root(tmp_path)
    for name in (
        "PGAI_TASKS_DIR",
        "PGAI_PROJECT_ROOT",
        "PGAI_PROJECT_NAME",
        "PGAI_DEV_TREE_PATH",
        "PGAI_GIT_LOCAL_ONLY",
        "GIT_DIR",
        "OVERWATCH_PROJECT",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("KANBAN_ROOT", str(built.root))
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(built.root))
    monkeypatch.setenv("PGAI_WORKTREE_BASE", str(tmp_path / "worktrees"))
    return built


def _bash_value(fx: nd.NonDefaultGitConfigRoot, body: str, *args: str, **kwargs) -> str:
    """stdout of a bash run that must succeed, trailing newline removed."""
    result = fx.bash(body, *args, **kwargs)
    assert result.returncode == 0, (body, result.stdout, result.stderr)
    return result.stdout.rstrip("\n")


def _bash_prefix(fx: nd.NonDefaultGitConfigRoot) -> str:
    return _bash_value(fx, 'pp_branch_prefix "$1"', fx.project)


def _bash_remote(fx: nd.NonDefaultGitConfigRoot) -> str:
    return _bash_value(fx, 'pp_git_remote_name "$1"', fx.project)


def _bash_prefixed(fx: nd.NonDefaultGitConfigRoot, branch: str) -> str:
    return _bash_value(fx, 'pp_prefix_branch "$1" "$2"', fx.project, branch)


def _branches(fx: nd.NonDefaultGitConfigRoot) -> set[str]:
    return set(fx.git("for-each-ref", "--format=%(refname:short)", "refs/heads").splitlines())


# ---------------------------------------------------------------------------
# 1. The resolvers
# ---------------------------------------------------------------------------


def test_bash_resolvers_answer_the_configured_prefix_and_remote(fx) -> None:
    assert _bash_prefix(fx) == nd.BRANCH_PREFIX
    assert _bash_remote(fx) == nd.REMOTE_NAME
    assert _bash_prefixed(fx, _RC_BRANCH) == f"{nd.BRANCH_PREFIX}{_RC_BRANCH}"


def test_python_resolvers_agree_with_the_bash_resolvers(fx) -> None:
    cfg = project_cfg_file(fx.project_root)
    prefixes = {
        "branch_prefix_for_project_root": branch_prefix.branch_prefix_for_project_root(fx.project_root),
        "branch_prefix_from_cfg": branch_prefix.branch_prefix_from_cfg(cfg),
        "ini_branch_prefix_for_project_root": branch_prefix.ini_branch_prefix_for_project_root(fx.project_root),
        "ini_branch_prefix_from_cfg": branch_prefix.ini_branch_prefix_from_cfg(cfg),
        "bare_branch_prefix_from_cfg": branch_prefix.bare_branch_prefix_from_cfg(cfg),
        "resolve_branch_prefix": branch_prefix.resolve_branch_prefix(fx.project),
    }
    remotes = {
        "resolve_git_remote_name": git_remote.resolve_git_remote_name(fx.project_root, fx.project),
        "remote_name_from_cfg": git_remote.remote_name_from_cfg(cfg, fx.project),
    }
    assert prefixes == dict.fromkeys(prefixes, _bash_prefix(fx))
    assert remotes == dict.fromkeys(remotes, _bash_remote(fx))
    assert _bash_prefix(fx) == nd.BRANCH_PREFIX and _bash_remote(fx) == nd.REMOTE_NAME


# ---------------------------------------------------------------------------
# 2. OVERWATCH: the shared bash helper and resolution_stage.py
# ---------------------------------------------------------------------------


def _overwatch_helper_prefix(fx: nd.NonDefaultGitConfigRoot) -> str:
    """overwatch_branch_prefix run as a check runs it: the helper loads its own library."""
    return _bash_value(fx, f'source "{_OVERWATCH_CONFIG}"\noverwatch_branch_prefix "$1"', fx.project, prelude=False)


def _resolution_stage_evidence(fx: nd.NonDefaultGitConfigRoot, monkeypatch: pytest.MonkeyPatch):
    """The BlockEvidence run_resolution_stage hands the resolution matrix for the fixture's RC."""
    (fx.project_root / "HALT").write_text("# HALT created by CM at 2026-01-01T00:00:00Z\n", encoding="utf-8")
    seen = []

    def capture(evidence, **_kwargs):
        seen.append(evidence)
        raise ResolutionMatrixError("evidence captured; the matrix itself is not under test")

    monkeypatch.setattr(resolution_stage, "resolve_block", capture)
    outcome = resolution_stage.run_resolution_stage(fx.project, str(fx.root))
    assert outcome.status == resolution_stage.DECLINED, outcome
    assert len(seen) == 1, outcome
    return seen[0]


def test_overwatch_helper_and_resolution_stage_read_the_same_prefix(fx, monkeypatch) -> None:
    helper = _overwatch_helper_prefix(fx)
    evidence = _resolution_stage_evidence(fx, monkeypatch)
    assert helper == evidence.branch_prefix == nd.BRANCH_PREFIX
    assert evidence.rc_branch == _bash_prefixed(fx, _RC_BRANCH) == f"{nd.BRANCH_PREFIX}{_RC_BRANCH}"
    assert evidence.base_branch == _bash_prefixed(fx, "main") == f"{nd.BRANCH_PREFIX}main"


@pytest.mark.parametrize("check", sorted(_OVERWATCH_PREFIX_CHECKS))
def test_overwatch_prefix_checks_resolve_the_helper_answer(fx, check: str) -> None:
    text = (_OVERWATCH_CHECKS / f"{check}.sh").read_text(encoding="utf-8")
    resolver = re.search(r"^(_\w+_resolve_env)\(\)\s*\{", text, re.M)
    assert resolver, f"{check}.sh defines no _<abbr>_resolve_env function"
    var = _OVERWATCH_PREFIX_CHECKS[check]
    value = _bash_value(
        fx,
        f'source "{_OVERWATCH_CHECKS / check}.sh"\n{resolver.group(1)} || exit 1\nprintf "%s" "${{{var}-unset}}"',
        prelude=False,
    )
    assert value == _overwatch_helper_prefix(fx) == nd.BRANCH_PREFIX


# ---------------------------------------------------------------------------
# 3. Framework version: dashboard/lib/version.sh and lib/released_version.py
# ---------------------------------------------------------------------------


def test_version_sh_and_released_version_read_the_same_framework_refs(fx) -> None:
    bash_refs = _bash_value(fx, f'source "{_VERSION_SH}"\n_pgai_framework_release_refs "$1"', str(fx.root))
    remote, _, prefix = bash_refs.partition(" ")
    assert released_version._framework_refs(fx.root) == (remote, prefix) == (nd.REMOTE_NAME, nd.BRANCH_PREFIX)


def test_version_sh_and_released_version_report_the_same_framework_version(fx) -> None:
    bash_version = _bash_value(
        fx, f'source "{_VERSION_SH}"\nget_kanban_version "$1" "$2"', str(fx.root), str(fx.dev_tree)
    )
    python_version = released_version.kanban_version(fx.root, fx.dev_tree)
    assert bash_version == python_version == nd.RELEASED_VERSION


def test_bash_and_python_last_released_version_agree(fx) -> None:
    # Local-only, as every render path and non-CM wake runs it: no fetch.
    bash_version = _bash_value(fx, 'pp_last_released_version "$1"', fx.project, extra_env={"PGAI_GIT_LOCAL_ONLY": "1"})
    python_version = released_version.last_released_version(fx.root, fx.project)
    assert bash_version == python_version == nd.RELEASED_VERSION


# ---------------------------------------------------------------------------
# 4-7. Python callers that name prefixed branches in the dev tree
# ---------------------------------------------------------------------------


def test_reset_deletes_only_the_prefixed_feature_branch(fx) -> None:
    prefixed = _bash_prefixed(fx, _FEATURE_BRANCH)
    assert prefixed in _branches(fx)
    reset_item(OpsContext(kanban_root=fx.root), fx.project, nd.TASK_ID)
    remaining = _branches(fx)
    assert prefixed not in remaining
    assert {_FEATURE_BRANCH, f"ai_{_FEATURE_BRANCH}"} <= remaining


def test_verify_rc_state_matches_the_active_rc_to_the_prefixed_branch(fx) -> None:
    output = rc_state.verify_rc_state(OpsContext(kanban_root=fx.root), fx.project, verbose=True, environ={})
    rc_branch = _bash_prefixed(fx, _RC_BRANCH)
    assert f"Active RC {nd.RC_VERSION} has matching local branch: {rc_branch}" in output.stdout, output
    assert "Stray unprefixed" not in output.stdout, output.stdout


def test_cancel_reads_the_bash_prefix_and_remote(fx) -> None:
    ctx = OpsContext(kanban_root=fx.root)
    assert cancel.branch_prefix(ctx, fx.project) == _bash_prefix(fx) == nd.BRANCH_PREFIX
    assert cancel.git_remote_name(ctx, fx.project) == _bash_remote(fx) == nd.REMOTE_NAME


def test_cancel_removes_the_prefixed_rc_branch(fx) -> None:
    out, err = io.StringIO(), io.StringIO()
    outcome = cancel.cancel_rc(
        OpsContext(kanban_root=fx.root),
        fx.project,
        nd.RC_VERSION,
        yes=True,
        out=out.write,
        err=err.write,
    )
    rc_branch = _bash_prefixed(fx, _RC_BRANCH)
    assert outcome.rc_branch == rc_branch, (out.getvalue(), err.getvalue())
    assert rc_branch not in _branches(fx)
    assert fx.git("branch", "--show-current") == _bash_prefixed(fx, "main")


def test_block_requeue_resolves_a_bare_source_branch_with_the_prefix(fx) -> None:
    feature = _bash_prefixed(fx, _FEATURE_BRANCH)
    record = BlockRecord(
        reason_code="per_task_timeout",
        owner_role="CODER",
        attempts=1,
        first_blocked_at="2026-01-01T00:00:00+00:00",
        last_blocked_at="2026-01-01T00:00:00+00:00",
        cooldown_until="none",
        evidence=f"branch={feature}",
        resume="none",
    )
    target = block_requeue.resolve_git_target(fx.task_dir, record)
    assert target.source_written == _RC_BRANCH
    assert target.source == _bash_prefixed(fx, _RC_BRANCH) == f"{nd.BRANCH_PREFIX}{_RC_BRANCH}"
    assert (target.branch, target.branch_present) == (feature, True)


# ---------------------------------------------------------------------------
# 8. GET /projects/{name}
# ---------------------------------------------------------------------------


def test_project_metadata_route_reports_the_bash_prefix(fx) -> None:
    client = build_api_client(fx.root)
    assert client.get("/projects").json()["projects"] == [fx.project]
    response = client.get(f"/projects/{fx.project}")
    assert response.status_code == 200, response.text
    assert response.json()["branch_prefix"] == _bash_prefix(fx) == nd.BRANCH_PREFIX


# ---------------------------------------------------------------------------
# 9. pm_materialize.py
# ---------------------------------------------------------------------------


def test_materializer_names_the_branches_pp_prefix_branch_names(fx) -> None:
    team_root = str(fx.project_root)
    assert pm_materialize._resolve_branch_prefix(team_root) == _bash_prefix(fx)
    rc_branch = _bash_prefixed(fx, _RC_BRANCH)
    assert pm_materialize.determine_source_branch({}, team_root, "release", nd.RC_VERSION) == rc_branch
    assert pm_materialize.determine_source_branch({}, team_root, "feature") == rc_branch  # from the Active RC
    assert (
        pm_materialize.determine_source_branch({"cm_operation": "open-rc"}, team_root, "release", nd.RC_VERSION)
        == _bash_prefixed(fx, "main")
        == f"{nd.BRANCH_PREFIX}main"
    )


def test_materializer_ticket_text_names_the_configured_remote_and_prefix(fx) -> None:
    team_root = str(fx.project_root)
    remote = pm_materialize._resolve_git_remote_name(team_root)
    assert remote == _bash_remote(fx) == nd.REMOTE_NAME

    feature_tasks = [{"task_id": "CODER-20260101-001-work", "depends_on": [], "prerequisite_ids": []}]
    synthetic = pm_materialize.inject_feature_workflow_tasks(
        feature_tasks,
        source_branch="feature/shared",
        test_required=False,
        parent_branch=f"{nd.BRANCH_PREFIX}main",
        date_str="20260101",
        base_seq=2,
        owner="CLAUDE",
        git_remote=remote,
        branch_prefix=pm_materialize._resolve_branch_prefix(team_root),
    )
    ticket = synthetic[0]
    text = " ".join([ticket["goal"], ticket["required_output"], *ticket["constraints"], *ticket["acceptance_criteria"]])
    assert "origin" not in text, text
    assert f"git ls-remote {nd.REMOTE_NAME} feature/shared returns a commit SHA." in ticket["acceptance_criteria"]
    assert f"Push 'feature/shared' to {nd.REMOTE_NAME} after creating it." in ticket["constraints"]
    assert f"not from {nd.BRANCH_PREFIX}main." in ticket["constraints"][0]

    release_tasks = [{"task_id": "CODER-20260101-001-work", "depends_on": [], "prerequisite_ids": []}]
    pm_materialize.inject_cm_bookends(
        release_tasks,
        "v1.2.3",
        "20260101",
        2,
        "CLAUDE",
        git_remote=remote,
        test_required=False,
    )
    release = next(t for t in release_tasks if t.get("cm_operation") == "release")
    assert f"pushed to {nd.REMOTE_NAME} when" in release["acceptance_criteria"][0]
    assert "origin" not in release["acceptance_criteria"][0]


def test_verify_rc_state_warnings_name_the_configured_remote_main_ref(fx, monkeypatch) -> None:
    cfg = project_cfg_file(fx.project_root)
    main_ref = f"{nd.REMOTE_NAME}/{nd.BRANCH_PREFIX}main"
    assert rc_state._remote_main_ref(fx.project, cfg) == main_ref

    for latest in ("v0.0.0", "v9.9.9"):
        monkeypatch.setattr(rc_state, "last_released_version", lambda *_a, _v=latest: _v)
        out = io.StringIO()
        report = rc_state._Report(out, verbose=False)
        rc_state._check_a(report, fx.root, fx.project, fx.project_root / "release-state.md", main_ref)
        warnings = [line for line in out.getvalue().splitlines() if line.startswith("[WARN]")]
        assert warnings and all(main_ref in line and "origin" not in line for line in warnings), out.getvalue()


# ---------------------------------------------------------------------------
# 10-11. Bash callers that name the remote
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tag", "expected_rc"),
    [(nd.UPSTREAM_ONLY_TAG, "RC=0"), (nd.ORIGIN_ONLY_TAG, "RC=1")],
    ids=["held-by-upstream", "held-by-origin-only"],
)
def test_discovery_tag_lookup_asks_the_configured_remote(fx, tag: str, expected_rc: str) -> None:
    result = fx.bash(
        f'source "{_LIB / "discovery.sh"}"\n_disc_git_tag_exists "$1" "$2" "$3"\necho "RC=$?"',
        tag,
        str(fx.dev_tree),
        fx.project,
    )
    assert result.stdout.endswith(f"{expected_rc}\n"), (result.stdout, result.stderr)


def test_bug_bundle_records_the_configured_remote_url(fx) -> None:
    (fx.project_root / "bugs" / "BUG-0001-sample.md").write_text("# Sample bug\n", encoding="utf-8")
    body = f"""
log() {{ printf '%s\\n' "$*" >&2; }}
source "{_LIB / "wake_autonomous_scan.sh"}"
get_release_state_field() {{ case "$1" in "Active RC") echo none ;; "Last Released") echo {nd.RELEASED_VERSION} ;; esac; }}
TEAM_ROOT="$1"
_CURRENT_PROJECT="$2"
pp_load_config "$2"
scan_and_bundle_bugs
echo "RC=$?"
"""
    result = fx.bash(body, str(fx.root), fx.project)
    assert "RC=0" in result.stdout, (result.stdout, result.stderr)
    # The URL of the remote the Python resolver names.
    expected = fx.git("remote", "get-url", git_remote.resolve_git_remote_name(fx.project_root, fx.project))
    assert expected == fx.upstream_url
    assert f"project Git Repo = {expected}\n" in result.stderr, result.stderr
    bundle = next((fx.project_root / "requirements" / "priority").glob("v1.1.1-bugfix-bundle-*.md"))
    assert f"## Git Repo\n{expected}\n" in bundle.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 12. Dashboard git panes
# ---------------------------------------------------------------------------

_RULE_RE = re.compile(r"^[─═]+$")
_RECENT_TAG_RE = re.compile(r"^\s+(\S+)\s+(?:\d{4}-\d{2}-\d{2}|unknown)$")

# Values pp_branch_prefix refuses: a slash, and a single-quoted value.
_INVALID_PREFIXES = ("zz/", "'zz_'")


def _set_branch_prefix(fx: nd.NonDefaultGitConfigRoot, value: str | None) -> None:
    """Rewrite the fixture's ``branch_prefix`` line; ``None`` removes the key."""
    cfg = fx.project_root / "project.cfg"
    text = re.sub(r"(?m)^branch_prefix\s*=.*\n", "", cfg.read_text(encoding="utf-8"))
    if value is not None:
        text = text.replace("[project]\n", f"[project]\nbranch_prefix = {value}\n", 1)
    cfg.write_text(text, encoding="utf-8")


def _pane(fx: nd.NonDefaultGitConfigRoot, script: str) -> str:
    """stdout of one dashboard git pane for the fixture project, without colour."""
    result = subprocess.run(
        [str(_DASHBOARD / script), "--project", fx.project],
        capture_output=True,
        text=True,
        env=fx.env({"NO_COLOR": "1"}),
        cwd=str(fx.root),
        timeout=60,
    )
    assert result.returncode == 0, (script, result.stdout, result.stderr)
    return result.stdout


def _git_status_sections(text: str) -> dict[str, list[str]]:
    """git-status.sh's section headings, each with its non-blank body lines."""
    sections: dict[str, list[str]] = {}
    lines = text.splitlines()
    current = None
    for index, line in enumerate(lines):
        if index + 1 < len(lines) and line.strip() and _RULE_RE.match(lines[index + 1]):
            current = sections.setdefault(line.strip(), [])
        elif current is not None and not _RULE_RE.match(line):
            if not line.strip():
                current = None
            else:
                current.append(line.strip())
    return sections


def _git_status_names(text: str) -> dict[str, list[str]]:
    """The branch lines, RC branch names and tag names git-status.sh rendered."""
    sections = _git_status_sections(text)
    return {
        "branch": sections["Branch"],
        "rc": [line.split()[0] for line in sections["In-flight RC Branches"]],
        "tags": " ".join(sections["Recent Tags (last 5)"]).split(),
    }


def _recent_tag_names(text: str) -> list[str]:
    """The tag names git-recent-tags.sh rendered, in order."""
    return [m.group(1) for m in map(_RECENT_TAG_RE.match, text.splitlines()) if m]


_UNPREFIXED_MAIN_SUBJECT = "unprefixed main only"


def _add_unprefixed_main(fx: nd.NonDefaultGitConfigRoot) -> None:
    """A bare ``main`` beside ``zz_main``: forked at its first commit, with one
    commit of its own, so ``zz_main``'s second commit is reachable from it alone."""
    first = fx.git("rev-list", "--max-parents=0", f"{nd.BRANCH_PREFIX}main")
    own = fx.git("commit-tree", "-p", first, "-m", _UNPREFIXED_MAIN_SUBJECT, f"{first}^{{tree}}")
    fx.git("branch", "main", own)


def _recent_commits(text: str, branch: str) -> list[str]:
    """The commit subjects git-status.sh listed under ``Recent Commits (<branch>, ...)``."""
    sections = _git_status_sections(text)
    heading = f"Recent Commits ({branch}, last 5)"
    assert heading in sections, sorted(sections)
    return [line.split(None, 1)[1] for line in sections[heading]]


def _subjects(fx: nd.NonDefaultGitConfigRoot, branch: str) -> list[str]:
    return fx.git("log", "--format=%s", "-5", branch).splitlines()


def test_dashboard_git_panes_list_what_pp_branch_prefix_names(fx) -> None:
    prefix = _bash_prefix(fx)
    assert prefix == nd.BRANCH_PREFIX
    prefixed_tags = fx.git("tag", "--list", f"{prefix}*", "--sort=-version:refname").splitlines()
    status = _git_status_names(_pane(fx, "git-status.sh"))
    assert status["rc"] == [_bash_prefixed(fx, _RC_BRANCH)], status
    assert status["tags"] == prefixed_tags[:5], status
    assert _recent_tag_names(_pane(fx, "git-recent-tags.sh")) == prefixed_tags[:10]


def test_git_status_recent_commits_read_the_prefixed_main_branch(fx) -> None:
    _add_unprefixed_main(fx)
    main_branch = _bash_prefixed(fx, "main")
    assert main_branch == f"{nd.BRANCH_PREFIX}main"
    text = _pane(fx, "git-status.sh")

    commits = _recent_commits(text, main_branch)
    assert commits == _subjects(fx, main_branch), commits
    assert "second" in commits  # reachable from zz_main only
    assert _UNPREFIXED_MAIN_SUBJECT not in commits
    assert "branch not found" not in text
    assert "Recent Commits (main," not in text


@pytest.mark.parametrize("value", [None, ""], ids=["absent", "empty"])
def test_git_status_recent_commits_read_main_when_no_prefix_is_set(fx, value: str | None) -> None:
    _add_unprefixed_main(fx)
    _set_branch_prefix(fx, value)
    assert _bash_prefix(fx) == ""
    text = _pane(fx, "git-status.sh")

    commits = _recent_commits(text, "main")
    assert commits == _subjects(fx, "main"), commits
    assert commits[0] == _UNPREFIXED_MAIN_SUBJECT
    assert "second" not in commits


@pytest.mark.parametrize("invalid", _INVALID_PREFIXES, ids=["slash", "single-quoted"])
def test_dashboard_git_panes_render_a_refused_prefix_as_no_prefix(fx, invalid: str) -> None:
    # Branches a raw reading of "zz/" would find and print.
    fx.git("branch", "zz/main")
    fx.git("branch", f"zz/rc/{nd.RC_VERSION}")

    _set_branch_prefix(fx, None)
    unset_status = _git_status_names(_pane(fx, "git-status.sh"))
    unset_tags = _recent_tag_names(_pane(fx, "git-recent-tags.sh"))

    _set_branch_prefix(fx, invalid)
    refused = fx.bash('pp_branch_prefix "$1"', fx.project)
    assert refused.returncode != 0, (refused.stdout, refused.stderr)
    status = _git_status_names(_pane(fx, "git-status.sh"))
    tags = _recent_tag_names(_pane(fx, "git-recent-tags.sh"))

    assert status == unset_status
    assert tags == unset_tags
    named = [*status["branch"], *status["rc"], *status["tags"], *tags]
    assert not [name for name in named if "zz/" in name], status


# ---------------------------------------------------------------------------
# 13. Every Python caller reads each spelling as pp_branch_prefix does
# ---------------------------------------------------------------------------

# (case id, branch_prefix value as written, section it is written under,
#  pp_branch_prefix's answer or None where it refuses)
_CALLER_CASES = [
    ("plain", "zz_", "project", "zz_"),
    ("double-quoted", '"zz_"', "project", "zz_"),
    ("empty", "", "project", ""),
    ("inline-comment", "zz_  # note", "project", "zz_"),
    ("single-quoted", "'zz_'", "project", None),
    ("invalid", "zz/", "project", None),
    ("wrong-section", "zz_", "other", ""),
]


def _write_prefix_case(fx: nd.NonDefaultGitConfigRoot, value: str | None, section: str = "project") -> None:
    """Write ``branch_prefix = value`` under *section* (``None`` writes no key at all)."""
    _set_branch_prefix(fx, value if section == "project" else None)
    if value is not None and section != "project":
        cfg = fx.project_root / "project.cfg"
        cfg.write_text(cfg.read_text(encoding="utf-8") + f"[{section}]\nbranch_prefix = {value}\n", encoding="utf-8")


def _caller_answers(fx: nd.NonDefaultGitConfigRoot, monkeypatch: pytest.MonkeyPatch) -> dict:
    """What each read-only Python caller builds from the fixture's current project.cfg."""
    team_root = str(fx.project_root)
    record = BlockRecord(
        reason_code="per_task_timeout",
        owner_role="CODER",
        attempts=1,
        first_blocked_at="2026-01-01T00:00:00+00:00",
        last_blocked_at="2026-01-01T00:00:00+00:00",
        cooldown_until="none",
        evidence=f"branch={nd.BRANCH_PREFIX}{_FEATURE_BRANCH}",
        resume="none",
    )
    evidence = _resolution_stage_evidence(fx, monkeypatch)
    response = build_api_client(fx.root).get(f"/projects/{fx.project}")
    return {
        "block_requeue source": block_requeue.resolve_git_target(fx.task_dir, record, require_source=False).source,
        "resolution_stage": (evidence.branch_prefix, evidence.rc_branch, evidence.base_branch),
        "GET /projects/{name}": (response.status_code, response.json().get("branch_prefix")),
        "pm_materialize prefix": pm_materialize._resolve_branch_prefix(team_root),
        "pm_materialize rc": pm_materialize.determine_source_branch({}, team_root, "release", nd.RC_VERSION),
        "pm_materialize main": pm_materialize.determine_source_branch(
            {"cm_operation": "open-rc"}, team_root, "release", nd.RC_VERSION
        ),
    }


@pytest.mark.parametrize(
    ("value", "section", "answer"), [c[1:] for c in _CALLER_CASES], ids=[c[0] for c in _CALLER_CASES]
)
def test_python_callers_read_each_spelling_as_pp_branch_prefix(fx, monkeypatch, capsys, value, section, answer) -> None:
    # The reference: pp_branch_prefix's answer written plainly, or no key at all where it refuses.
    _write_prefix_case(fx, answer)
    expected = _caller_answers(fx, monkeypatch)

    _write_prefix_case(fx, value, section)
    bash = fx.bash('pp_branch_prefix "$1"', fx.project)
    assert (bash.returncode, bash.stdout) == ((1, "") if answer is None else (0, f"{answer}\n")), bash.stderr
    capsys.readouterr()
    assert _caller_answers(fx, monkeypatch) == expected
    warned = "falling back to no prefix" in capsys.readouterr().err
    assert warned == (answer is None)

    prefix = answer or ""
    assert expected["pm_materialize rc"] == f"{prefix}rc/{nd.RC_VERSION}"  # zz_rc/v1.2.0 for "zz_  # note"
    assert expected["GET /projects/{name}"] == (200, prefix)

    # ops reset (destructive, so last): deletes the feature branch under the prefix it read, and only that one.
    feature_branches = {f"{p}{_FEATURE_BRANCH}" for p in ("", "ai_", nd.BRANCH_PREFIX)}
    reset_item(OpsContext(kanban_root=fx.root), fx.project, nd.TASK_ID)
    assert feature_branches - _branches(fx) == {f"{prefix}{_FEATURE_BRANCH}"}
