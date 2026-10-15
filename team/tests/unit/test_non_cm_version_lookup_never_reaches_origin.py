"""
test_non_cm_version_lookup_never_reaches_origin.py
====================================================
Behavioral coverage for the wake-dispatch export of ``PGAI_GIT_LOCAL_ONLY``
(scripts/lib/wake_guard_wiring.sh's wd_install_git_boundary_guard) as seen
by its consumer, ``pp_last_released_version`` (scripts/lib/project_paths.sh):
a version lookup made by a non-CM agent wake never reaches for origin, and
therefore never spends a git-boundary-guard evidence record on a fetch the
guard would have refused anyway.

WHY THE LOOKUP IS DRIVEN THROUGH THE REAL WIRING AND THE REAL SHIM
------------------------------------------------------------------
test_wake_guard_wiring.py proves the export happens; this module proves
the export has the intended effect on the lookup the flag exists for, end
to end: the real wd_install_git_boundary_guard (pointed at the real shim
directory, scripts/wake/git-guard), then the real pp_last_released_version
against a throwaway project whose dev tree is a real git repository with
one release tag and a remote that resolves OUTSIDE the framework's
throwaway root (an scp-style URL -- the guard never exempts it), so any
fetch that IS attempted is refused and recorded exactly as it would be in
a live dispatch.

WHY TWO SHAPES OF project_paths.sh
----------------------------------
pp_last_released_version also skips its fetch on its own when
PGAI_AGENT_ROLE names a non-CM role. That check lives in the code the
dispatched task sources, and an installed copy of project_paths.sh may
predate it; the wake-layer flag lives in the launcher and holds regardless.
Each lookup scenario therefore runs twice: once against this tree's
project_paths.sh and once against a copy with the role condition removed
(the flag-only shape), and the non-CM verdict must be the same for both --
that is what makes the suppression independent of which copy is on disk.

A network-intercepting stub git (tests/fixtures/git_network_stub.py) sits
behind the shim so a CM dispatch's passed-through fetch is observed rather
than performed: it is logged and fails offline, never touching a real
remote.

Covers:
  - a non-CM dispatch resolves the tagged version, adds zero evidence
    records and makes zero network attempts, against both shapes;
  - a CM dispatch has no PGAI_GIT_LOCAL_ONLY in its environment, reaches
    the fetch (observed on the stub's log), resolves the same version, and
    records nothing;
  - the flag suppresses only the lookup's own reach: a genuine non-CM
    fetch against the same repository is still refused with exactly one
    evidence record.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import textwrap

import pytest

from tests.fixtures.binary_guard import require_binary
from tests.fixtures.git_network_stub import install_git_network_stub

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_WAKE_GUARD_WIRING_LIB = _TEAM_DIR / "scripts" / "lib" / "wake_guard_wiring.sh"
_WAKE_SCRIPT_DIR = _TEAM_DIR / "scripts" / "wake"
_LIB_DIR = _TEAM_DIR / "scripts" / "lib"
_PROJECT_PATHS = _LIB_DIR / "project_paths.sh"

# The exact condition pp_last_released_version guards its fetch with, and
# the flag-only shape an installed copy that predates the role check carries.
_ROLE_AWARE_CONDITION = (
    'if [[ -z "${PGAI_GIT_LOCAL_ONLY:-}" && '
    '( -z "$_plrv_role_lc" || "$_plrv_role_lc" == "cm" ) ]]; then'
)
_FLAG_ONLY_CONDITION = 'if [[ -z "${PGAI_GIT_LOCAL_ONLY:-}" ]]; then'

_TAG = "v1.2.3"
_PROJECT = "probe"

# Names whose ambient values (from the agent wake that may have launched
# pytest) would change what "no flag", "no role" and "no pin" mean below.
_SCRUBBED_NAMES = (
    "GIT_GUARD_REAL_GIT",
    "PGAI_GIT_GUARD_DEPTH",
    "PGAI_GIT_LOCAL_ONLY",
    "PGAI_AGENT_ROLE",
    "PGAI_GIT_BOUNDARY_GUARD_EVIDENCE_FILE",
    "PGAI_TASK_ID",
    "PGAI_TASKS_DIR",
    "PGAI_PROJECT_ROOT",
    "PGAI_DEV_TREE_PATH",
    "PGAI_PROJECT_NAME",
)


@pytest.fixture(autouse=True)
def _require_git() -> None:
    require_binary("git", label="non-cm-version-lookup")


def _git(real_git: str, repo: pathlib.Path, *args: str) -> None:
    subprocess.run(
        [real_git, "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    )


def _build_board(tmp_path: pathlib.Path, real_git: str) -> pathlib.Path:
    """A throwaway kanban root with one release-workflow project whose dev
    tree is a real repository carrying one release tag and a remote the
    guard can never exempt (an scp-style URL is not a filesystem path)."""
    root = tmp_path / "board"
    (root / "tmp").mkdir(parents=True)
    (root / "projects" / _PROJECT).mkdir(parents=True)
    repo = root / "repo"
    repo.mkdir()
    subprocess.run([real_git, "init", "-q", "--initial-branch=main", str(repo)],
                   capture_output=True, text=True, check=True)
    _git(real_git, repo, "-c", "user.email=t@example.invalid", "-c", "user.name=t",
         "commit", "-q", "--allow-empty", "-m", "init")
    _git(real_git, repo, "tag", _TAG)
    _git(real_git, repo, "remote", "add", "origin", "git@example.invalid:org/repo.git")

    (root / "kanban.cfg").write_text(
        f"[paths]\ntmp_root = {root}\ntmp_subdir = tmp\n", encoding="utf-8"
    )
    (root / "projects.cfg").write_text(
        f"[project:{_PROJECT}]\npriority=1\ndescription=probe\nenabled=true\n",
        encoding="utf-8",
    )
    (root / "projects" / _PROJECT / "project.cfg").write_text(
        "[project]\n"
        f"project_name = {_PROJECT}\n"
        f"dev_tree_path = {repo}\n"
        "git_repo_url = git@example.invalid:org/repo.git\n"
        "git_remote_name = origin\n"
        "workflow_type = release\n"
        "branch_prefix =\n"
        "push_to_remote = true\n",
        encoding="utf-8",
    )
    return root


def _flag_only_project_paths(tmp_path: pathlib.Path) -> pathlib.Path:
    """A copy of project_paths.sh whose fetch is gated on the flag alone,
    placed beside symlinks to its sibling libs so its self-location of
    ini_parser.sh / temp.sh / error_contract.sh still resolves."""
    lib = tmp_path / "flag-only-lib"
    lib.mkdir()
    for sibling in _LIB_DIR.glob("*.sh"):
        if sibling.name != _PROJECT_PATHS.name:
            (lib / sibling.name).symlink_to(sibling)
    text = _PROJECT_PATHS.read_text(encoding="utf-8")
    assert text.count(_ROLE_AWARE_CONDITION) == 1, (
        "project_paths.sh no longer carries exactly one copy of the fetch "
        f"condition this fixture rewrites:\n  {_ROLE_AWARE_CONDITION}\n"
        "Update _ROLE_AWARE_CONDITION to the current spelling."
    )
    copy = lib / _PROJECT_PATHS.name
    copy.write_text(text.replace(_ROLE_AWARE_CONDITION, _FLAG_ONLY_CONDITION), encoding="utf-8")
    return copy


def _run_lookup(
    tmp_path: pathlib.Path,
    *,
    subagent: str,
    project_paths: pathlib.Path,
    extra_shell: str = "",
) -> tuple[subprocess.CompletedProcess, pathlib.Path, pathlib.Path]:
    """Install the real guard wiring for *subagent*, source *project_paths*
    and run pp_last_released_version, with a network-intercepting stub git
    behind whatever the wiring puts on PATH.

    Returns (completed process, evidence file path, stub network log path).
    The evidence file is where the wiring itself points the guard
    (<task_log_dir>/git-boundary-guard.jsonl); it exists only if a record
    was appended.
    """
    stub_dir = tmp_path / "stub-bin"
    net_log = tmp_path / "network-calls.log"
    stub_env = install_git_network_stub(stub_dir, net_log)
    # Build the fixture through the stub: it hands every non-network verb
    # to a real git it resolved by content, so no guard shim on the
    # session's own PATH (an agent wake puts one first) is ever consulted.
    root = _build_board(tmp_path, str(stub_dir / "git"))
    task_log_dir = tmp_path / "task" / "logs"
    task_log_dir.mkdir(parents=True)
    evidence = task_log_dir / "git-boundary-guard.jsonl"

    env = {k: v for k, v in os.environ.items() if k not in _SCRUBBED_NAMES}
    env.update(stub_env)
    env["KANBAN_ROOT"] = str(root)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(root)
    env["PGAI_AGENT_KANBAN_TEMP_DIR"] = str(root / "tmp")

    harness = textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -uo pipefail
        log() {{ echo "LOG: $*" >&2; }}
        # shellcheck source=/dev/null
        source "{_WAKE_GUARD_WIRING_LIB}"
        SCRIPT_DIR="{_WAKE_SCRIPT_DIR}"
        SUBAGENT="{subagent}"
        task_id="SYNTH-000-fixture"
        task_log_dir="{task_log_dir}"
        wd_install_git_boundary_guard
        export PGAI_TASK_ID="$task_id"
        echo "PGAI_GIT_LOCAL_ONLY=[${{PGAI_GIT_LOCAL_ONLY:-}}]"
        # shellcheck source=/dev/null
        source "{project_paths}"
        {extra_shell}
        echo "version=[$(pp_last_released_version {_PROJECT})]"
    """)
    result = subprocess.run(
        ["bash", "-c", harness], capture_output=True, text=True, timeout=60, env=env,
    )
    return result, evidence, net_log


def _records(evidence: pathlib.Path) -> list[dict]:
    if not evidence.exists():
        return []
    return [json.loads(line) for line in evidence.read_text(encoding="utf-8").splitlines() if line.strip()]


def _network_calls(net_log: pathlib.Path) -> str:
    return net_log.read_text(encoding="utf-8") if net_log.exists() else ""


# ---------------------------------------------------------------------------
# Non-CM dispatch: same answer, zero records, zero reaches -- both shapes.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("shape", ["role-aware", "flag-only"])
@pytest.mark.parametrize("subagent", ["coder", "tester", "pm"])
def test_non_cm_dispatch_resolves_version_without_records_or_network(
    tmp_path: pathlib.Path, subagent: str, shape: str
) -> None:
    project_paths = _PROJECT_PATHS if shape == "role-aware" else _flag_only_project_paths(tmp_path)
    result, evidence, net_log = _run_lookup(
        tmp_path, subagent=subagent, project_paths=project_paths
    )
    assert result.returncode == 0, result.stderr
    assert "PGAI_GIT_LOCAL_ONLY=[1]" in result.stdout, (
        f"the wiring must export PGAI_GIT_LOCAL_ONLY=1 for role '{subagent}'.\n{result.stdout}"
    )
    assert f"version=[{_TAG}]" in result.stdout, (
        f"a non-CM lookup must still resolve the tagged version {_TAG} from "
        f"local refs ({shape} project_paths.sh).\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert _records(evidence) == [], (
        f"a non-CM version lookup must add ZERO evidence records ({shape} "
        f"project_paths.sh, role '{subagent}'); got:\n{evidence.read_text(encoding='utf-8')}"
    )
    assert "fetch" not in _network_calls(net_log), (
        f"a non-CM version lookup must never reach for origin; the stub logged:\n{_network_calls(net_log)}"
    )


# ---------------------------------------------------------------------------
# CM dispatch: no flag, the fetch is reached, same answer, nothing recorded.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("shape", ["role-aware", "flag-only"])
def test_cm_dispatch_has_no_flag_and_reaches_the_fetch(
    tmp_path: pathlib.Path, shape: str
) -> None:
    project_paths = _PROJECT_PATHS if shape == "role-aware" else _flag_only_project_paths(tmp_path)
    result, evidence, net_log = _run_lookup(
        tmp_path, subagent="cm", project_paths=project_paths
    )
    assert result.returncode == 0, result.stderr
    assert "PGAI_GIT_LOCAL_ONLY=[]" in result.stdout, (
        f"PGAI_GIT_LOCAL_ONLY must be absent from a CM dispatch's environment.\n{result.stdout}"
    )
    calls = _network_calls(net_log)
    assert "fetch origin --tags --quiet" in calls, (
        f"a CM lookup must reach its best-effort fetch ({shape} project_paths.sh); "
        f"the stub logged:\n{calls!r}"
    )
    assert f"version=[{_TAG}]" in result.stdout, (
        f"a CM lookup must resolve the same version a non-CM one does.\n{result.stdout}"
    )
    assert _records(evidence) == [], (
        "a CM dispatch is never guarded, so nothing may be recorded; got:\n"
        f"{evidence.read_text(encoding='utf-8') if evidence.exists() else ''}"
    )


# ---------------------------------------------------------------------------
# The flag suppresses the lookup's own reach, never the guard.
# ---------------------------------------------------------------------------


def test_genuine_non_cm_fetch_is_still_refused_with_exactly_one_record(
    tmp_path: pathlib.Path,
) -> None:
    # _build_board places the dev tree at <tmp_path>/board/repo.
    repo = tmp_path / "board" / "repo"
    result, evidence, net_log = _run_lookup(
        tmp_path,
        subagent="coder",
        project_paths=_PROJECT_PATHS,
        extra_shell=(
            f'git -C "{repo}" fetch origin >/dev/null 2>&1; '
            'echo "genuine-fetch-rc=[$?]"'
        ),
    )
    assert result.returncode == 0, result.stderr
    assert "genuine-fetch-rc=[0]" not in result.stdout, (
        f"a genuine non-CM fetch must be refused (non-zero exit).\n{result.stdout}"
    )
    records = _records(evidence)
    assert len(records) == 1, (
        "a genuine non-CM fetch must append exactly one evidence record even "
        f"with PGAI_GIT_LOCAL_ONLY set; got {len(records)}:\n"
        f"{evidence.read_text(encoding='utf-8') if evidence.exists() else ''}"
    )
    assert records[0]["role"] == "coder" and "fetch" in records[0]["argv"], records[0]
    assert "fetch" not in _network_calls(net_log), (
        "neither the lookup's skipped fetch nor the refused genuine fetch may reach "
        f"the binary behind the shim; the stub logged:\n{_network_calls(net_log)}"
    )
    # The lookup itself, in the same environment, still stayed local.
    assert f"version=[{_TAG}]" in result.stdout, result.stdout
