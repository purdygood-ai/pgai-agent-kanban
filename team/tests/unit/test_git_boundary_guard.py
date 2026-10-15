"""
test_git_boundary_guard.py
===========================
Behavioral coverage for the "CM is the ONLY toucher of any remote" guard
(team/scripts/lib/git_boundary_guard.sh and the executable shim
team/scripts/wake/git-guard/git). Every scenario runs the real shim as a
subprocess -- this is a boundary a non-CM agent hits from a shell, so the
test exercises the same surface, not the guard function's Python-side
reasoning about it.

WHY A STUB GIT, NOT THE REAL ONE, FOR PASS-THROUGH SCENARIOS
--------------------------------------------------------------
Every scenario that exercises a PASSED-THROUGH fetch/pull/push (the cm
role, the two fail-open paths, and the recursion guard) points the shim at
a stub "git" executable under tmp_path instead of the real git binary on
PATH. A real `git fetch`/`pull`/`push` is exactly the remote-touching
operation team/SOP-core.md reserves for CM -- this suite is CODER's own
test run, not a CM release task, so it must never perform one for real
regardless of --dry-run flags or a same-process PGAI_AGENT_ROLE=cm label;
the stub proves the shim executed the resolved binary with the right
argv, preserving its exit status/stdout/stderr, without ever touching
this repository's actual origin. Non-remote-verb scenarios (status) use
the real git binary, since `git status` never leaves the local checkout.

Covers:
  - a non-CM role's fetch/pull/push is refused with the dual-channel block
    and a non-zero exit;
  - the cm role's fetch/pull/push passes through to the resolved binary;
  - a non-remote subcommand passes through unchanged for every role;
  - a refused invocation appends one evidence record; a passed-through
    remote verb under cm records nothing;
  - the two fail-open paths (role undeterminable, evidence file
    unconfigured) never refuse and never invent a write target;
  - the shim resolves the real git binary even when its own directory
    sits ahead of the real git's directory on PATH (the recursion guard);
  - a remote verb preceded by one or more git global options (-C, -c,
    --no-pager, --git-dir=<d>) is refused and recorded identically to the
    bare verb, and a global option in front of a non-remote verb still
    passes through untouched.
  - the resolved real-git binary is used for the final hand-off exactly
    once, honoring an operator-supplied GIT_GUARD_REAL_GIT override when
    set and falling back to a filtered-PATH lookup when it is not;
  - the PGAI_GIT_GUARD_DEPTH recursion sentinel skips policy entirely on a
    seeded re-entrant invocation, handing off to the real binary straight
    away rather than evaluating the boundary a second time;
  - one guarded call costs at most a handful of short-lived descendant
    processes (never a second shim instance), observed with a live
    process-tree watchdog while the call is in flight;
  - the shim body itself never invokes the literal command name "git" --
    every internal use is the resolved binary variable.
  - a shim invocation whose caller is SIGKILLed (the same single-pid
    kill a caller's own timeout handling performs) dies with that
    caller instead of surviving, reparented to init.
  - a fetch/pull/push against a remote name that was never configured, or
    against a "-C" directory that no longer exists, is exempted rather
    than refused-and-recorded -- but ONLY when the repository path itself
    resolves under the throwaway root (see
    _git_boundary_guard_path_under_temp_root and
    _git_boundary_guard_is_unreachable_destination in
    team/scripts/lib/git_boundary_guard.sh); the identical shape against a
    repository OUTSIDE that root is refused and recorded like any other
    unresolved destination, and so is a destination that DOES resolve,
    just to somewhere outside the throwaway root.
  - a value-consuming fetch/pull/push option placed AHEAD of the remote
    name ("fetch --depth 5 origin", "push -o ci.skip origin <branch>",
    and the long/alternate spellings "push --push-option ...", "fetch
    --filter ...", "pull -s ...", "pull -X ...") never lets that option's
    own value be misread as the destination -- the real remote configured
    outside the throwaway root is still refused and recorded, exactly as
    the equivalent call without the option is, and the same holds for a
    value-consuming option this file's own skip list has never heard of:
    the repository-path bound above closes that residual class without
    depending on any option enumeration.
  - a configured throwaway root that merely contains a path component
    literally named "projects" -- at any depth, with anything other than
    exactly one component after it -- never widens the exemption up to
    that component's parent: a repository outside the root as configured
    is still refused and recorded. The one shape that DOES normalise
    upward is the framework's own per-project layout, a root of exactly
    "<parent>/projects/<name>" with nothing after <name> AND <parent>
    itself resolving at or below the framework's own temp root (never
    inferred from the configured root's own text -- see
    team/scripts/lib/temp.sh's pgai_configured_temp_root), which still
    exempts a sibling fixture elsewhere under the same framework root
    (see _git_boundary_guard_path_under_temp_root in
    team/scripts/lib/git_boundary_guard.sh).
  - the bare two-component SHAPE alone is never sufficient: a configured
    root of exactly "/projects/<name>" -- one component below the
    filesystem root, so naively stripping "/projects/<name>" yields the
    EMPTY STRING -- never widens to match every absolute path on the
    host, and a configured root sharing that same shape at a location the
    framework never produced (an operator's own directory that merely
    happens to end in "projects/<name>") never widens to that location's
    parent either; both leave the comparison at the root exactly as
    configured, un-widened.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import signal
import subprocess
import time

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_SHIM = _TEAM_DIR / "scripts" / "wake" / "git-guard" / "git"
_LIB = _TEAM_DIR / "scripts" / "lib" / "git_boundary_guard.sh"


@pytest.fixture(autouse=True)
def _scrub_guard_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every scenario in this module builds its own shim environment from
    scratch (see `_run_shim`'s `dict(os.environ)` base and the individual
    tests that seed GIT_GUARD_REAL_GIT / PGAI_GIT_GUARD_DEPTH on purpose),
    so an ambient copy of either variable in the session that launched
    pytest -- the normal shape of any non-CM agent wake -- silently
    changes what "no override" and "no recursion" mean for every other
    test. Clearing both here makes the module read the same whether
    pytest was launched from a clean shell or from an agent session that
    already exports them; `raising=False` keeps a clean shell a no-op.
    """
    monkeypatch.delenv("GIT_GUARD_REAL_GIT", raising=False)
    monkeypatch.delenv("PGAI_GIT_GUARD_DEPTH", raising=False)


def _proc_ppid(pid: int) -> int | None:
    """Reads /proc/<pid>/status and returns its PPid, or None if the
    process has already exited (a race is expected -- descendants of a
    fast-moving shim invocation come and go while this polls)."""
    try:
        with open(f"/proc/{pid}/status", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith("PPid:"):
                    return int(line.split()[1])
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return None
    return None


def _proc_cmdline(pid: int) -> str:
    try:
        with open(f"/proc/{pid}/cmdline", encoding="utf-8", errors="replace") as fh:
            return fh.read().replace("\x00", " ")
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return ""


def _descendant_pids(root_pid: int) -> list[int]:
    """Walks /proc once and returns every live descendant of *root_pid*,
    at any depth. Linux-specific (matches this project's Rocky Linux
    hosts) -- a snapshot, not a continuous trace, so the caller polls this
    repeatedly while the watched process is in flight."""
    all_pids = [int(p) for p in os.listdir("/proc") if p.isdigit()]
    children_of: dict[int, list[int]] = {}
    for pid in all_pids:
        ppid = _proc_ppid(pid)
        if ppid is not None:
            children_of.setdefault(ppid, []).append(pid)

    result: list[int] = []
    frontier = [root_pid]
    while frontier:
        current = frontier.pop()
        kids = children_of.get(current, [])
        result.extend(kids)
        frontier.extend(kids)
    return result


def _make_stub_git(tmp_path: pathlib.Path, *, exit_code: int = 0) -> pathlib.Path:
    """Writes an executable stub `git` that prints its argv (prefixed so a
    test can tell it apart from real git output) and exits with
    *exit_code* (default 0). Returns the directory holding it, for
    prepending to PATH."""
    stub_dir = tmp_path / "stub_bin"
    stub_dir.mkdir()
    stub_git = stub_dir / "git"
    stub_git.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'STUBGIT-INVOKED:%s\\n' \"$*\"\n"
        f"exit {exit_code}\n",
        encoding="utf-8",
    )
    stub_git.chmod(0o755)
    return stub_dir


def _run_shim(
    *args: str,
    role: str | None,
    evidence_file: pathlib.Path | None,
    task_id: str = "TEST-TASK",
    path_prepend_dirs: tuple[pathlib.Path, ...] = (),
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    if role is None:
        env.pop("PGAI_AGENT_ROLE", None)
    else:
        env["PGAI_AGENT_ROLE"] = role
    if evidence_file is None:
        env.pop("PGAI_GIT_BOUNDARY_GUARD_EVIDENCE_FILE", None)
    else:
        env["PGAI_GIT_BOUNDARY_GUARD_EVIDENCE_FILE"] = str(evidence_file)
    env["PGAI_TASK_ID"] = task_id
    if path_prepend_dirs:
        prefix = ":".join(str(d) for d in path_prepend_dirs)
        env["PATH"] = f"{prefix}:{env.get('PATH', '')}"
    if extra_env:
        env.update(extra_env)

    return subprocess.run(
        [str(_SHIM), *args],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
        cwd=str(_TEAM_DIR.parent),
    )


def _make_bare_repo_pair(root: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """Create a (local clone, bare origin) git repo pair under *root*,
    with "origin" configured as a remote of the local clone. Returns
    (local, origin). Uses the real system git directly (never the shim)
    for setup, the same way the suites that build their own bare-repo
    fixtures do."""
    origin = root / "origin.git"
    local = root / "local"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    subprocess.run(
        ["git", "init", "-q", "--initial-branch=main", str(local)], check=True
    )
    subprocess.run(
        ["git", "-C", str(local), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(local), "config", "user.name", "Test"], check=True
    )
    (local / "README.md").write_text("test\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(local), "add", "."], check=True)
    subprocess.run(["git", "-C", str(local), "commit", "-q", "-m", "initial"], check=True)
    subprocess.run(
        ["git", "-C", str(local), "remote", "add", "origin", str(origin)], check=True
    )
    return local, origin


def _read_evidence(evidence_file: pathlib.Path) -> list[dict]:
    if not evidence_file.exists():
        return []
    return [
        json.loads(line)
        for line in evidence_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_shim_is_syntactically_valid_bash() -> None:
    result = subprocess.run(["bash", "-n", str(_SHIM)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_library_is_syntactically_valid_bash() -> None:
    result = subprocess.run(["bash", "-n", str(_LIB)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_shim_is_mode_0755() -> None:
    mode = _SHIM.stat().st_mode & 0o777
    assert mode == 0o755, f"expected 0755, got {oct(mode)}"


@pytest.mark.parametrize(
    ("verb", "role", "extra_args"),
    [
        ("fetch", "coder", ("--all",)),
        ("pull", "writer", ()),
        ("push", "tester", ("origin", "main")),
    ],
)
def test_non_cm_role_refuses_remote_verb(
    tmp_path: pathlib.Path, verb: str, role: str, extra_args: tuple[str, ...]
) -> None:
    evidence = tmp_path / "evidence.jsonl"
    result = _run_shim(verb, *extra_args, role=role, evidence_file=evidence)

    assert result.returncode != 0
    assert "error:" in result.stderr
    assert "message:" in result.stderr
    assert "remedy:" in result.stderr
    assert "CM" in result.stderr


@pytest.mark.parametrize("verb", ["fetch", "pull", "push"])
def test_cm_role_remote_verbs_pass_through(tmp_path: pathlib.Path, verb: str) -> None:
    stub_dir = _make_stub_git(tmp_path)
    evidence = tmp_path / "evidence.jsonl"

    result = _run_shim(
        verb, "--dry-run", role="cm", evidence_file=evidence, path_prepend_dirs=(stub_dir,)
    )

    assert result.returncode == 0
    assert f"STUBGIT-INVOKED:{verb} --dry-run" in result.stdout
    assert _read_evidence(evidence) == []


def test_pass_through_preserves_nonzero_exit_status(tmp_path: pathlib.Path) -> None:
    """A pass-through is byte-identical to the resolved binary's own exit
    status, not merely "0 on success" -- a stub git that fails with a
    specific non-zero code (17, chosen to be distinguishable from the
    guard's own refusal code of 1) must have that exact code reach the
    shim's caller unchanged, for a cm-role remote verb that is let
    through."""
    stub_dir = _make_stub_git(tmp_path, exit_code=17)
    evidence = tmp_path / "evidence.jsonl"

    result = _run_shim(
        "push", "origin", "main", role="cm", evidence_file=evidence, path_prepend_dirs=(stub_dir,)
    )

    assert result.returncode == 17
    assert "STUBGIT-INVOKED:push origin main" in result.stdout
    assert _read_evidence(evidence) == []


def test_non_remote_subcommand_passes_through_for_every_role(tmp_path: pathlib.Path) -> None:
    evidence = tmp_path / "evidence.jsonl"
    direct = subprocess.run(
        ["git", "status", "--short"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(_TEAM_DIR.parent),
    )
    for role in ("coder", "cm", "writer", None):
        result = _run_shim("status", "--short", role=role, evidence_file=evidence)
        assert result.returncode == direct.returncode
        assert result.stdout == direct.stdout
    assert _read_evidence(evidence) == []


def test_refused_invocation_records_one_evidence_line(tmp_path: pathlib.Path) -> None:
    evidence = tmp_path / "evidence.jsonl"
    _run_shim("fetch", "--all", role="coder", evidence_file=evidence, task_id="CODER-TEST-001")

    records = _read_evidence(evidence)
    assert len(records) == 1
    record = records[0]
    assert record["role"] == "coder"
    assert record["task"] == "CODER-TEST-001"
    assert record["argv"] == ["git", "fetch", "--all"]
    assert "timestamp" in record and record["timestamp"]


GIT_GUARD_THROWAWAY_ROOT_ENV = "PGAI_AGENT_KANBAN_TEMP_DIR"


def test_push_to_remote_name_resolving_under_throwaway_root_is_not_refused(
    tmp_path: pathlib.Path,
) -> None:
    """A test's own `git push origin ...` into a bare repository it
    created under its own temp root -- the same shape every fixture that
    builds a synthetic (local clone, bare origin) pair for its own
    push/fetch/pull coverage uses -- completes instead of being refused,
    and the real push actually lands (proof the call reached real git, not
    merely that the guard stayed quiet)."""
    throwaway_root = tmp_path / "throwaway_root"
    throwaway_root.mkdir()
    local, origin = _make_bare_repo_pair(throwaway_root)
    evidence = tmp_path / "evidence.jsonl"

    result = _run_shim(
        "-C", str(local), "push", "origin", "HEAD",
        role="coder",
        evidence_file=evidence,
        extra_env={GIT_GUARD_THROWAWAY_ROOT_ENV: str(throwaway_root)},
    )

    assert result.returncode == 0, result.stderr
    assert _read_evidence(evidence) == []
    origin_head = subprocess.run(
        ["git", "-C", str(origin), "rev-parse", "HEAD"],
        capture_output=True, text=True, timeout=10,
    )
    assert origin_head.returncode == 0, "the push must have actually reached the bare origin"


def test_push_to_path_destination_under_throwaway_root_is_not_refused(
    tmp_path: pathlib.Path,
) -> None:
    """The same exemption applies when the destination is given as a raw
    path rather than a configured remote's short name (git's own
    remote-vs-path heuristic: any destination containing "/" is a path)."""
    throwaway_root = tmp_path / "throwaway_root"
    throwaway_root.mkdir()
    local, origin = _make_bare_repo_pair(throwaway_root)
    evidence = tmp_path / "evidence.jsonl"

    result = _run_shim(
        "-C", str(local), "push", str(origin), "HEAD",
        role="coder",
        evidence_file=evidence,
        extra_env={GIT_GUARD_THROWAWAY_ROOT_ENV: str(throwaway_root)},
    )

    assert result.returncode == 0, result.stderr
    assert _read_evidence(evidence) == []


def test_push_to_path_destination_outside_throwaway_root_is_still_refused(
    tmp_path: pathlib.Path,
) -> None:
    """A destination that is a real filesystem path -- not a URL, not
    scp-like syntax -- but outside the configured throwaway root is still
    refused with the dual-channel contract and one evidence record: the
    exemption is destination-scoped, not "any local path is fine"."""
    throwaway_root = tmp_path / "throwaway_root"
    throwaway_root.mkdir()
    outside_root = tmp_path / "not_the_throwaway_root"
    outside_root.mkdir()
    local, origin = _make_bare_repo_pair(outside_root)
    evidence = tmp_path / "evidence.jsonl"

    result = _run_shim(
        "-C", str(local), "push", str(origin), "HEAD",
        role="coder",
        evidence_file=evidence,
        extra_env={GIT_GUARD_THROWAWAY_ROOT_ENV: str(throwaway_root)},
    )

    assert result.returncode != 0
    assert "error:" in result.stderr
    records = _read_evidence(evidence)
    assert len(records) == 1
    assert records[0]["argv"][-3:] == ["push", str(origin), "HEAD"]


def test_push_outside_root_still_refused_when_root_has_projects_component_at_depth(
    tmp_path: pathlib.Path,
) -> None:
    """A configured throwaway root that merely CONTAINS a path component
    literally named "projects" -- here three levels deep, with two more
    components after it -- must not widen the exemption up to that
    component's parent. A repository living entirely outside the
    configured root is still refused, even though the root's own path
    happens to pass through a directory named "projects" on its way
    there."""
    sandbox = tmp_path / "sandbox"
    configured_root = sandbox / "projects" / "myproj" / "scratch"
    configured_root.mkdir(parents=True)
    outside_root = sandbox / "outside" / "work"
    outside_root.mkdir(parents=True)
    local, origin = _make_bare_repo_pair(outside_root)
    subprocess.run(
        ["git", "-C", str(origin), "config", "receive.advertisePushOptions", "true"],
        check=True,
    )
    origin_refs_before = subprocess.run(
        ["git", "-C", str(origin), "for-each-ref"],
        capture_output=True, text=True, timeout=10, check=True,
    ).stdout
    evidence = tmp_path / "evidence.jsonl"

    result = _run_shim(
        "-C", str(local), "push", "origin", "HEAD",
        role="coder",
        evidence_file=evidence,
        extra_env={GIT_GUARD_THROWAWAY_ROOT_ENV: str(configured_root)},
    )

    assert result.returncode != 0
    assert "error:" in result.stderr
    records = _read_evidence(evidence)
    assert len(records) == 1
    assert records[0]["argv"][-3:] == ["push", "origin", "HEAD"]
    origin_refs_after = subprocess.run(
        ["git", "-C", str(origin), "for-each-ref"],
        capture_output=True, text=True, timeout=10, check=True,
    ).stdout
    assert origin_refs_after == origin_refs_before


def test_push_to_sibling_of_bare_project_subtree_root_is_still_exempt(
    tmp_path: pathlib.Path,
) -> None:
    """The framework's own per-project layout -- a configured root of
    exactly `<framework_root>/projects/<name>`, with nothing after `<name>`
    -- still normalises up to the framework root, so a fixture living in a
    DIFFERENT corner of the same framework temp tree (its own pytest/
    scratch here) remains exempt and records nothing. This is the
    fixture-noise reduction the upward normalisation exists to preserve,
    proved on the one shape (bare two-component "projects/<name>") the
    anchor actually trusts."""
    framework_root = tmp_path / "framework_root"
    project_root = framework_root / "projects" / "myproj"
    project_root.mkdir(parents=True)
    sibling_subtree = framework_root / "pytest" / "some_fixture"
    sibling_subtree.mkdir(parents=True)
    local, origin = _make_bare_repo_pair(sibling_subtree)
    evidence = tmp_path / "evidence.jsonl"

    result = _run_shim(
        "-C", str(local), "push", "origin", "HEAD",
        role="coder",
        evidence_file=evidence,
        extra_env={GIT_GUARD_THROWAWAY_ROOT_ENV: str(project_root)},
    )

    assert result.returncode == 0, result.stderr
    assert _read_evidence(evidence) == []


def test_push_outside_root_still_refused_when_configured_root_is_bare_projects_name(
    tmp_path: pathlib.Path,
) -> None:
    """A configured throwaway root of exactly "/projects/<name>" -- one
    path component below the filesystem root -- strips down to the EMPTY
    STRING when the "/projects/<name>" suffix is removed. An empty (or
    bare "/") candidate parent must never serve as a path-prefix, since
    that degenerates into a glob matching every absolute path on the
    host: a repository entirely unrelated to the configured root stays
    refused and recorded, and the destination's refs are unchanged."""
    outside_root = tmp_path / "not_the_throwaway_root"
    outside_root.mkdir()
    local, origin = _make_bare_repo_pair(outside_root)
    subprocess.run(
        ["git", "-C", str(origin), "config", "receive.advertisePushOptions", "true"],
        check=True,
    )
    origin_refs_before = subprocess.run(
        ["git", "-C", str(origin), "for-each-ref"],
        capture_output=True, text=True, timeout=10, check=True,
    ).stdout
    evidence = tmp_path / "evidence.jsonl"

    result = _run_shim(
        "-C", str(local), "push", "origin", "HEAD",
        role="coder",
        evidence_file=evidence,
        extra_env={GIT_GUARD_THROWAWAY_ROOT_ENV: "/projects/name"},
    )

    assert result.returncode != 0
    assert "error:" in result.stderr
    records = _read_evidence(evidence)
    assert len(records) == 1
    assert records[0]["argv"][-3:] == ["push", "origin", "HEAD"]
    origin_refs_after = subprocess.run(
        ["git", "-C", str(origin), "for-each-ref"],
        capture_output=True, text=True, timeout=10, check=True,
    ).stdout
    assert origin_refs_after == origin_refs_before


def test_push_to_path_destination_inside_bare_projects_name_root_remains_exempt(
    tmp_path: pathlib.Path,
) -> None:
    """The same bare "/projects/<name>" root as the sibling test above
    must still exempt a destination that is genuinely INSIDE it -- the
    fix for the empty-widened-parent defect must not turn the configured
    root itself into a second casualty. A stub git stands in for the
    real binary here because "/projects/name/..." is not a real
    filesystem location this suite may create."""
    stub_dir = _make_stub_git(tmp_path)
    evidence = tmp_path / "evidence.jsonl"
    configured_root = "/projects/name"
    destination = f"{configured_root}/inside/work"

    result = _run_shim(
        "push", destination, "HEAD",
        role="coder",
        evidence_file=evidence,
        path_prepend_dirs=(stub_dir,),
        extra_env={GIT_GUARD_THROWAWAY_ROOT_ENV: configured_root},
    )

    assert result.returncode == 0, result.stderr
    assert _read_evidence(evidence) == []


def test_push_outside_root_still_refused_when_projects_parent_is_not_the_framework_root(
    tmp_path: pathlib.Path,
) -> None:
    """A configured root that shares the framework's own per-project SHAPE
    -- exactly "<parent>/projects/<name>" -- but whose <parent> is not the
    framework's own temp root (verified via
    team/scripts/lib/temp.sh's pgai_configured_temp_root, pointed here at a
    throwaway kanban.cfg whose configured root is a directory this
    operator-shaped root never touches) must not widen the exemption up
    to <parent>: a repository living under <parent> but outside
    <parent>/projects/<name> stays refused and recorded, exactly as it
    would if <parent> merely happened to contain a literal "projects"
    component. This is what distinguishes an operator's own directory
    that coincidentally ends in "projects/<name>" (this test) from the
    framework's own generated per-project layout (the sibling-subtree
    test above, where <parent> genuinely does resolve under the real
    provenance root)."""
    fake_kanban_root = tmp_path / "fake_kanban_root"
    fake_kanban_root.mkdir()
    real_framework_root = tmp_path / "real_framework_root"
    real_framework_root.mkdir()
    (fake_kanban_root / "kanban.cfg").write_text(
        "[paths]\n"
        f"tmp_root = {real_framework_root.parent}\n"
        f"tmp_subdir = {real_framework_root.name}\n",
        encoding="utf-8",
    )

    operator_root = tmp_path / "operator_scratch"
    configured_root = operator_root / "projects" / "myproj"
    configured_root.mkdir(parents=True)
    outside_root = operator_root / "outside" / "work"
    outside_root.mkdir(parents=True)
    local, origin = _make_bare_repo_pair(outside_root)
    subprocess.run(
        ["git", "-C", str(origin), "config", "receive.advertisePushOptions", "true"],
        check=True,
    )
    origin_refs_before = subprocess.run(
        ["git", "-C", str(origin), "for-each-ref"],
        capture_output=True, text=True, timeout=10, check=True,
    ).stdout
    evidence = tmp_path / "evidence.jsonl"

    result = _run_shim(
        "-C", str(local), "push", "origin", "HEAD",
        role="coder",
        evidence_file=evidence,
        extra_env={
            GIT_GUARD_THROWAWAY_ROOT_ENV: str(configured_root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(fake_kanban_root),
        },
    )

    assert result.returncode != 0
    assert "error:" in result.stderr
    records = _read_evidence(evidence)
    assert len(records) == 1
    assert records[0]["argv"][-3:] == ["push", "origin", "HEAD"]
    origin_refs_after = subprocess.run(
        ["git", "-C", str(origin), "for-each-ref"],
        capture_output=True, text=True, timeout=10, check=True,
    ).stdout
    assert origin_refs_after == origin_refs_before


def test_push_to_remote_name_resolving_outside_throwaway_root_is_still_refused(
    tmp_path: pathlib.Path,
) -> None:
    """Resolving a configured remote's short name to a path outside the
    throwaway root refuses exactly like a raw out-of-root path does --
    the resolution step does not itself widen the exemption."""
    throwaway_root = tmp_path / "throwaway_root"
    throwaway_root.mkdir()
    outside_root = tmp_path / "not_the_throwaway_root"
    outside_root.mkdir()
    local, _origin = _make_bare_repo_pair(outside_root)
    evidence = tmp_path / "evidence.jsonl"

    result = _run_shim(
        "-C", str(local), "push", "origin", "HEAD",
        role="coder",
        evidence_file=evidence,
        extra_env={GIT_GUARD_THROWAWAY_ROOT_ENV: str(throwaway_root)},
    )

    assert result.returncode != 0
    assert len(_read_evidence(evidence)) == 1


def test_push_to_remote_name_resolving_to_real_looking_url_is_still_refused(
    tmp_path: pathlib.Path,
) -> None:
    """A configured remote whose URL carries scp-like host syntax names a
    real network endpoint by construction -- refused regardless of the
    throwaway-root check, and regardless of the fact that the raw
    destination TOKEN on the command line ("origin") is just a short
    name."""
    throwaway_root = tmp_path / "throwaway_root"
    throwaway_root.mkdir()
    local = throwaway_root / "local"
    subprocess.run(
        ["git", "init", "-q", "--initial-branch=main", str(local)], check=True
    )
    subprocess.run(
        ["git", "-C", str(local), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(["git", "-C", str(local), "config", "user.name", "Test"], check=True)
    (local / "README.md").write_text("test\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(local), "add", "."], check=True)
    subprocess.run(["git", "-C", str(local), "commit", "-q", "-m", "initial"], check=True)
    subprocess.run(
        [
            "git", "-C", str(local), "remote", "add", "origin",
            "git@example.invalid:someorg/somerepo.git",
        ],
        check=True,
    )
    evidence = tmp_path / "evidence.jsonl"

    result = _run_shim(
        "-C", str(local), "push", "origin", "HEAD",
        role="coder",
        evidence_file=evidence,
        extra_env={GIT_GUARD_THROWAWAY_ROOT_ENV: str(throwaway_root)},
    )

    assert result.returncode != 0
    assert len(_read_evidence(evidence)) == 1


def test_push_with_no_explicit_destination_is_not_exempted(tmp_path: pathlib.Path) -> None:
    """A bare "git push" relying on the current branch's configured
    upstream is never exempted, even when that upstream is itself
    fixture-local -- the destination check only resolves an explicit
    token on the command line (see _git_boundary_guard_find_destination's
    own header for why), so this is the guard's documented conservative
    default rather than a gap."""
    throwaway_root = tmp_path / "throwaway_root"
    throwaway_root.mkdir()
    local, _origin = _make_bare_repo_pair(throwaway_root)
    subprocess.run(
        ["git", "-C", str(local), "push", "-q", "-u", "origin", "main"], check=True
    )
    evidence = tmp_path / "evidence.jsonl"

    result = _run_shim(
        "-C", str(local), "push",
        role="coder",
        evidence_file=evidence,
        extra_env={GIT_GUARD_THROWAWAY_ROOT_ENV: str(throwaway_root)},
    )

    assert result.returncode != 0
    assert len(_read_evidence(evidence)) == 1


def test_fetch_from_repo_with_no_remote_configured_is_not_refused_or_recorded(
    tmp_path: pathlib.Path,
) -> None:
    """A repository that never configured "origin" at all -- the exact
    shape of every pytest fixture that builds a synthetic dev tree for
    production code's own best-effort `git fetch origin --tags --quiet`
    (team/scripts/lib/project_paths.sh's pp_last_released_version) --
    passes through unrefused and unrecorded. Nothing this guard could
    refuse would prevent a crossing here: the real git binary this shim
    hands off to fails with the identical "No such remote" error on its
    own, before ever resolving a network destination, so refusing and
    recording it was pure noise.

    The repository lives INSIDE the throwaway root, which is now a
    requirement of this exemption rather than an incidental detail (see
    _git_boundary_guard_path_under_temp_root): every fixture this
    exemption exists for already lives under its own tmp_path by
    construction. The mirror case -- the identical unreachable-destination
    shape against a repository OUTSIDE the root -- is
    test_fetch_from_repo_with_no_remote_configured_outside_root_still_refused
    below, and is refused rather than exempted."""
    throwaway_root = tmp_path / "throwaway_root"
    throwaway_root.mkdir()
    local = throwaway_root / "no_origin_here"
    subprocess.run(["git", "init", "-q", str(local)], check=True)
    evidence = tmp_path / "evidence.jsonl"

    result = _run_shim(
        "-C", str(local), "fetch", "origin", "--tags", "--quiet",
        role="coder",
        evidence_file=evidence,
        extra_env={GIT_GUARD_THROWAWAY_ROOT_ENV: str(throwaway_root)},
    )

    # The real git binary still runs (and still fails, since there is
    # nothing to fetch from) -- what matters is that this guard never
    # intercepted it: no dual-channel refusal text, no evidence record.
    assert "git-boundary-guard" not in result.stderr
    assert _read_evidence(evidence) == []


def test_fetch_from_repo_with_no_remote_configured_outside_root_still_refused(
    tmp_path: pathlib.Path,
) -> None:
    """The SAME unreachable-destination shape as the exemption
    test above ("origin" never configured, so the probe fails with "No
    such remote") but with the repository OUTSIDE the throwaway root is
    refused and recorded rather than exempted. A guard whose failed probe
    exempts the WHOLE invocation regardless of where the repository lives
    -- an exemption not bound to repo_path residency at all -- lets this
    exact shape (a repo a non-CM role points anywhere it likes, with an
    unconfigured "origin") slip past the boundary unrefused and
    unrecorded on a real dev tree. It must refuse, exactly like a configured-but-remote
    "origin" outside the root does."""
    throwaway_root = tmp_path / "throwaway_root"
    throwaway_root.mkdir()
    outside_root = tmp_path / "not_the_throwaway_root"
    outside_root.mkdir()
    local = outside_root / "no_origin_here"
    subprocess.run(["git", "init", "-q", str(local)], check=True)
    evidence = tmp_path / "evidence.jsonl"

    result = _run_shim(
        "-C", str(local), "fetch", "origin", "--tags", "--quiet",
        role="coder",
        evidence_file=evidence,
        extra_env={GIT_GUARD_THROWAWAY_ROOT_ENV: str(throwaway_root)},
    )

    assert result.returncode != 0
    assert "error:" in result.stderr
    records = _read_evidence(evidence)
    assert len(records) == 1
    assert records[0]["argv"][-4:] == ["fetch", "origin", "--tags", "--quiet"]


def test_fetch_against_gone_working_directory_is_not_refused_or_recorded(
    tmp_path: pathlib.Path,
) -> None:
    """A "-C <path>" naming a directory that no longer exists -- a
    fixture repository torn down by its own teardown before a deferred
    best-effort fetch runs -- passes through unrefused and unrecorded, the
    other shape this exemption targets: the real git binary fails identically
    ("cannot change to") before it ever gets far enough to resolve a
    remote, real or fixture, so there is nothing to record.

    The gone directory's own path is still nested under the throwaway
    root, which is what the exemption is bound to now
    (_git_boundary_guard_path_under_temp_root) -- non-existence alone is
    not what makes this safe, residency is. The mirror case (the gone
    directory outside the root) is
    test_fetch_against_gone_working_directory_outside_root_still_refused
    below."""
    throwaway_root = tmp_path / "throwaway_root"
    throwaway_root.mkdir()
    gone = throwaway_root / "already_torn_down"
    evidence = tmp_path / "evidence.jsonl"

    result = _run_shim(
        "-C", str(gone), "fetch", "origin", "--tags", "--quiet",
        role="coder",
        evidence_file=evidence,
        extra_env={GIT_GUARD_THROWAWAY_ROOT_ENV: str(throwaway_root)},
    )

    assert "git-boundary-guard" not in result.stderr
    assert _read_evidence(evidence) == []


def test_fetch_against_gone_working_directory_outside_root_still_refused(
    tmp_path: pathlib.Path,
) -> None:
    """The same "-C <path> no longer exists" shape as the
    exemption test above, but the gone path is OUTSIDE the throwaway
    root, so it is refused rather than exempted -- proof the
    "cannot change to" shape is bound to repo_path residency exactly like
    the "no such remote" shape is, not merely to the probe's own failure
    text."""
    throwaway_root = tmp_path / "throwaway_root"
    throwaway_root.mkdir()
    outside_root = tmp_path / "not_the_throwaway_root"
    outside_root.mkdir()
    gone = outside_root / "already_torn_down"
    evidence = tmp_path / "evidence.jsonl"

    result = _run_shim(
        "-C", str(gone), "fetch", "origin", "--tags", "--quiet",
        role="coder",
        evidence_file=evidence,
        extra_env={GIT_GUARD_THROWAWAY_ROOT_ENV: str(throwaway_root)},
    )

    assert result.returncode != 0
    assert "error:" in result.stderr
    records = _read_evidence(evidence)
    assert len(records) == 1
    assert records[0]["argv"][-4:] == ["fetch", "origin", "--tags", "--quiet"]


def test_fetch_from_repo_with_configured_remote_outside_root_still_refused(
    tmp_path: pathlib.Path,
) -> None:
    """The unreachable-destination exemption above is narrow: a repo
    whose "origin" IS configured, just to somewhere outside the
    throwaway root, is still refused and recorded exactly as before --
    proof the fix does not widen the exemption to "remote resolution
    failed for any reason", only to the two shapes that prove nothing
    could ever be reached."""
    throwaway_root = tmp_path / "throwaway_root"
    throwaway_root.mkdir()
    outside_root = tmp_path / "not_the_throwaway_root"
    outside_root.mkdir()
    local, _origin = _make_bare_repo_pair(outside_root)
    evidence = tmp_path / "evidence.jsonl"

    result = _run_shim(
        "-C", str(local), "fetch", "origin", "--tags", "--quiet",
        role="coder",
        evidence_file=evidence,
        extra_env={GIT_GUARD_THROWAWAY_ROOT_ENV: str(throwaway_root)},
    )

    assert result.returncode != 0
    assert "error:" in result.stderr
    records = _read_evidence(evidence)
    assert len(records) == 1
    assert records[0]["argv"][-4:] == ["fetch", "origin", "--tags", "--quiet"]


def test_fetch_option_consumed_value_ahead_of_remote_still_refused(
    tmp_path: pathlib.Path,
) -> None:
    """A value-consuming option placed AHEAD of the remote name ("--depth
    5") must never let the destination scan read that value ("5") as the
    destination instead of "origin". Against an implementation whose scan
    does read it, "5" is not a configured remote, the probe against it
    fails with "No such remote", and that failure is misread as proof
    nothing could be reached at all -- exempting the whole invocation, including the real
    "origin" configured outside the throwaway root. This must refuse and
    record exactly like the equivalent bare "fetch origin" does."""
    throwaway_root = tmp_path / "throwaway_root"
    throwaway_root.mkdir()
    outside_root = tmp_path / "not_the_throwaway_root"
    outside_root.mkdir()
    local, _origin = _make_bare_repo_pair(outside_root)
    evidence = tmp_path / "evidence.jsonl"

    result = _run_shim(
        "-C", str(local), "fetch", "--depth", "5", "origin",
        role="coder",
        evidence_file=evidence,
        extra_env={GIT_GUARD_THROWAWAY_ROOT_ENV: str(throwaway_root)},
    )

    assert result.returncode != 0
    assert "error:" in result.stderr
    records = _read_evidence(evidence)
    assert len(records) == 1
    assert records[0]["argv"][-4:] == ["fetch", "--depth", "5", "origin"]


def test_push_option_consumed_value_ahead_of_remote_still_refused(
    tmp_path: pathlib.Path,
) -> None:
    """The same class of value-consuming option, on push, using a
    server-option shape ("-o ci.skip origin <branch>"): "ci.skip" is not
    a configured remote, so a destination scan that reads it as the
    destination probes it, fails with "No such remote", and lets
    the whole push -- including the real "origin" configured outside the
    throwaway root -- pass through unrefused and unrecorded. This must
    refuse and record exactly like the equivalent bare "push origin
    main" does."""
    throwaway_root = tmp_path / "throwaway_root"
    throwaway_root.mkdir()
    outside_root = tmp_path / "not_the_throwaway_root"
    outside_root.mkdir()
    local, _origin = _make_bare_repo_pair(outside_root)
    evidence = tmp_path / "evidence.jsonl"

    result = _run_shim(
        "-C", str(local), "push", "-o", "ci.skip", "origin", "main",
        role="coder",
        evidence_file=evidence,
        extra_env={GIT_GUARD_THROWAWAY_ROOT_ENV: str(throwaway_root)},
    )

    assert result.returncode != 0
    assert "error:" in result.stderr
    records = _read_evidence(evidence)
    assert len(records) == 1
    assert records[0]["argv"][-5:] == ["push", "-o", "ci.skip", "origin", "main"]


# The long/alternate spellings of the value-consuming-option class the two
# tests above cover in short form. "--push-option" is git's own long
# spelling of "-o" on push; "--filter"/"-s"/"-X" are additional
# value-consuming options this file's skip list must also account for. All
# four are exercised here as argv tuples (subcommand, verb args...,
# destination position) so the same parametrized test proves both halves
# of the bound: refused outside the throwaway root, exempt inside it.
_OPTION_VALUE_AHEAD_OF_REMOTE_INVOCATIONS = (
    pytest.param(("push", "--push-option", "ci.skip", "origin", "main"), id="push---push-option"),
    pytest.param(("fetch", "--filter", "blob:none", "origin"), id="fetch---filter"),
    pytest.param(("pull", "-s", "ours", "origin", "main"), id="pull--s"),
    pytest.param(("pull", "-X", "theirs", "origin", "main"), id="pull--X"),
)


@pytest.mark.parametrize("verb_args", _OPTION_VALUE_AHEAD_OF_REMOTE_INVOCATIONS)
def test_option_value_ahead_of_remote_invocation_outside_root_is_refused_and_ref_unchanged(
    tmp_path: pathlib.Path, verb_args: tuple[str, ...]
) -> None:
    """Each of these value-consuming-option invocations, run against a
    repository whose "origin" resolves OUTSIDE the throwaway root, must be
    refused with a non-zero exit, append exactly one evidence record, and
    leave the destination's refs unchanged -- proof this shim never hands
    the command to real git. A destination scan that misreads an option's
    own value ("ci.skip", "blob:none", "ours", "theirs") as the
    destination, and treats a failed probe against that non-remote token
    as proof nothing could be reached, must not let the whole invocation --
    including the real "origin" outside the throwaway root -- straight
    through unrefused."""
    throwaway_root = tmp_path / "throwaway_root"
    throwaway_root.mkdir()
    outside_root = tmp_path / "not_the_throwaway_root"
    outside_root.mkdir()
    local, origin = _make_bare_repo_pair(outside_root)
    subprocess.run(
        ["git", "-C", str(origin), "config", "receive.advertisePushOptions", "true"],
        check=True,
    )
    origin_refs_before = subprocess.run(
        ["git", "-C", str(origin), "for-each-ref"],
        capture_output=True, text=True, timeout=10, check=True,
    ).stdout
    evidence = tmp_path / "evidence.jsonl"

    result = _run_shim(
        "-C", str(local), *verb_args,
        role="coder",
        evidence_file=evidence,
        extra_env={GIT_GUARD_THROWAWAY_ROOT_ENV: str(throwaway_root)},
    )

    assert result.returncode != 0
    assert "error:" in result.stderr
    records = _read_evidence(evidence)
    assert len(records) == 1
    assert records[0]["argv"][-len(verb_args):] == list(verb_args)
    origin_refs_after = subprocess.run(
        ["git", "-C", str(origin), "for-each-ref"],
        capture_output=True, text=True, timeout=10, check=True,
    ).stdout
    assert origin_refs_after == origin_refs_before


@pytest.mark.parametrize("verb_args", _OPTION_VALUE_AHEAD_OF_REMOTE_INVOCATIONS)
def test_option_value_ahead_of_remote_invocation_inside_root_is_still_exempt(
    tmp_path: pathlib.Path, verb_args: tuple[str, ...]
) -> None:
    """The same value-consuming-option invocations, against a repository
    whose "origin" resolves INSIDE the throwaway root, must remain exempt
    and record nothing -- the fixture-noise reduction this guard exists to
    preserve holds regardless of where the remote name sits in the
    command."""
    throwaway_root = tmp_path / "throwaway_root"
    throwaway_root.mkdir()
    local, origin = _make_bare_repo_pair(throwaway_root)
    subprocess.run(
        ["git", "-C", str(origin), "config", "receive.advertisePushOptions", "true"],
        check=True,
    )
    evidence = tmp_path / "evidence.jsonl"

    result = _run_shim(
        "-C", str(local), *verb_args,
        role="coder",
        evidence_file=evidence,
        extra_env={GIT_GUARD_THROWAWAY_ROOT_ENV: str(throwaway_root)},
    )

    # Whether the real git invocation itself succeeds is not this guard's
    # concern (a "pull -s ours" with nothing to merge, for instance, may
    # simply report "Already up to date" -- what matters is that this
    # guard never intercepted it.
    assert "git-boundary-guard" not in result.stderr
    assert _read_evidence(evidence) == []


def test_fetch_unlisted_value_consuming_option_outside_root_still_refused(
    tmp_path: pathlib.Path,
) -> None:
    """Bounding the exemption to repo_path
    residency closes the class at its root, so it also catches a
    value-consuming option this file's skip list has never heard of and
    never will -- "--totally-unlisted-fetch-option" is not real git
    syntax and is not on _git_boundary_guard_find_destination's skip
    list on purpose. The destination scan still misreads the option's own
    value as the destination, the probe against it still fails with "No
    such remote", but the repository lives OUTSIDE the throwaway root, so
    the failed-probe exemption in _git_boundary_guard_is_fixture_local no
    longer applies regardless of which option produced the false
    destination. This is the test the option-list-only fix (extending
    _git_boundary_guard_find_destination alone, with no repo_path bound)
    cannot pass, since no list can ever name every git option in
    advance."""
    throwaway_root = tmp_path / "throwaway_root"
    throwaway_root.mkdir()
    outside_root = tmp_path / "not_the_throwaway_root"
    outside_root.mkdir()
    local, _origin = _make_bare_repo_pair(outside_root)
    evidence = tmp_path / "evidence.jsonl"

    result = _run_shim(
        "-C", str(local), "fetch", "--totally-unlisted-fetch-option", "some-value", "origin",
        role="coder",
        evidence_file=evidence,
        extra_env={GIT_GUARD_THROWAWAY_ROOT_ENV: str(throwaway_root)},
    )

    assert result.returncode != 0
    assert "error:" in result.stderr
    records = _read_evidence(evidence)
    assert len(records) == 1
    assert records[0]["argv"][-4:] == [
        "fetch", "--totally-unlisted-fetch-option", "some-value", "origin",
    ]


def test_push_unlisted_value_consuming_option_outside_root_still_refused(
    tmp_path: pathlib.Path,
) -> None:
    """The push-side mirror of the fetch case above: an option this file
    has never heard of ("--totally-unlisted-push-option") still resolves
    to a false destination, and the repo_path bound still refuses it
    against a repository OUTSIDE the throwaway root -- the general fix,
    not an enumeration."""
    throwaway_root = tmp_path / "throwaway_root"
    throwaway_root.mkdir()
    outside_root = tmp_path / "not_the_throwaway_root"
    outside_root.mkdir()
    local, _origin = _make_bare_repo_pair(outside_root)
    evidence = tmp_path / "evidence.jsonl"

    result = _run_shim(
        "-C", str(local), "push", "--totally-unlisted-push-option", "some-value", "origin", "main",
        role="coder",
        evidence_file=evidence,
        extra_env={GIT_GUARD_THROWAWAY_ROOT_ENV: str(throwaway_root)},
    )

    assert result.returncode != 0
    assert "error:" in result.stderr
    records = _read_evidence(evidence)
    assert len(records) == 1
    assert records[0]["argv"][-5:] == [
        "push", "--totally-unlisted-push-option", "some-value", "origin", "main",
    ]


def test_role_undeterminable_fails_open_and_records_unknown(tmp_path: pathlib.Path) -> None:
    stub_dir = _make_stub_git(tmp_path)
    evidence = tmp_path / "evidence.jsonl"

    result = _run_shim(
        "fetch", "--dry-run", role=None, evidence_file=evidence, path_prepend_dirs=(stub_dir,)
    )

    assert result.returncode == 0
    assert "STUBGIT-INVOKED:fetch --dry-run" in result.stdout
    records = _read_evidence(evidence)
    assert len(records) == 1
    assert records[0]["role"] == "unknown"


def test_missing_evidence_file_fails_open_without_recording(tmp_path: pathlib.Path) -> None:
    stub_dir = _make_stub_git(tmp_path)

    result = _run_shim(
        "fetch", "--dry-run", role="coder", evidence_file=None, path_prepend_dirs=(stub_dir,)
    )

    # Fails open: no evidence sink configured means the refusal never fires
    # and the command reaches the resolved binary, per the guard's
    # documented fail-open contract.
    assert result.returncode == 0
    assert "STUBGIT-INVOKED:fetch --dry-run" in result.stdout


@pytest.mark.parametrize(
    ("global_opts", "verb"),
    [
        (("-C", "{repo}"), "fetch"),
        (("-c", "a.b=c"), "push"),
        (("--no-pager",), "pull"),
        (("--git-dir={repo}/.git",), "fetch"),
    ],
    ids=["dash-C", "dash-c", "no-pager", "git-dir-equals"],
)
def test_non_cm_role_refuses_remote_verb_behind_global_options(
    tmp_path: pathlib.Path, global_opts: tuple[str, ...], verb: str
) -> None:
    """`git -C <dir> fetch`, `git -c a.b=c push`, `git --no-pager pull`,
    and `git --git-dir=<d> fetch` must be refused exactly like the bare
    verb -- the leading global option must not shift the guard's view of
    $1 away from the subcommand it actually enforces on."""
    repo = str(_TEAM_DIR.parent)
    args = [opt.format(repo=repo) for opt in global_opts] + [verb]
    evidence = tmp_path / "evidence.jsonl"

    result = _run_shim(*args, role="coder", evidence_file=evidence)

    assert result.returncode != 0
    assert "error:" in result.stderr
    assert "message:" in result.stderr
    assert "remedy:" in result.stderr
    assert "CM" in result.stderr

    records = _read_evidence(evidence)
    assert len(records) == 1
    assert records[0]["argv"] == ["git", *args]


@pytest.mark.parametrize(
    ("global_opts", "verb"),
    [
        (("-C", "{repo}"), "fetch"),
        (("-c", "a.b=c"), "push"),
        (("--no-pager",), "pull"),
        (("--git-dir={repo}/.git",), "fetch"),
    ],
    ids=["dash-C", "dash-c", "no-pager", "git-dir-equals"],
)
def test_cm_role_remote_verb_behind_global_options_passes_through(
    tmp_path: pathlib.Path, global_opts: tuple[str, ...], verb: str
) -> None:
    """The same four global-option shapes reach the resolved binary
    unchanged under role cm, with its exit status preserved and nothing
    recorded -- the widened verb scan must not widen what gets refused."""
    stub_dir = _make_stub_git(tmp_path)
    repo = str(_TEAM_DIR.parent)
    args = [opt.format(repo=repo) for opt in global_opts] + [verb, "--dry-run"]
    evidence = tmp_path / "evidence.jsonl"

    result = _run_shim(
        *args, role="cm", evidence_file=evidence, path_prepend_dirs=(stub_dir,)
    )

    assert result.returncode == 0
    assert f"STUBGIT-INVOKED:{' '.join(args)}" in result.stdout
    assert _read_evidence(evidence) == []


def test_global_option_in_front_of_non_remote_verb_passes_through_for_every_role(
    tmp_path: pathlib.Path,
) -> None:
    """A global option in front of a non-remote verb (e.g. `git -C <dir>
    status --short`) must still pass through untouched for every role --
    the wider scan finds the real verb, "status", and finding it must not
    turn into a refusal it never was."""
    evidence = tmp_path / "evidence.jsonl"
    repo = str(_TEAM_DIR.parent)
    args = ["-C", repo, "status", "--short"]
    direct = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(_TEAM_DIR.parent),
    )
    for role in ("coder", "cm", "writer", None):
        result = _run_shim(*args, role=role, evidence_file=evidence)
        assert result.returncode == direct.returncode
        assert result.stdout == direct.stdout
    assert _read_evidence(evidence) == []


def test_shim_resolves_stub_git_when_its_own_dir_leads_path(tmp_path: pathlib.Path) -> None:
    """The recursion guard: with the shim's own directory prepended to
    PATH ahead of the resolved git's directory (the exact arrangement the
    wake layer sets up for a non-CM task), the shim must still find and
    exec the NEXT git on PATH rather than calling itself."""
    stub_dir = _make_stub_git(tmp_path)
    evidence = tmp_path / "evidence.jsonl"

    result = _run_shim(
        "status",
        "--short",
        role="cm",
        evidence_file=evidence,
        path_prepend_dirs=(_SHIM.parent, stub_dir),
    )

    assert result.returncode == 0
    assert "STUBGIT-INVOKED:status --short" in result.stdout


def test_git_guard_real_git_override_is_honored(tmp_path: pathlib.Path) -> None:
    """GIT_GUARD_REAL_GIT, when set, is the ONLY resolution step -- no PATH
    walk needed to reach it. Proven here by pointing it at a stub binary
    that sits nowhere on PATH at all: if the shim ignored the override and
    fell back to a PATH lookup, this call would hit the real system git
    instead of the stub."""
    stub_dir = _make_stub_git(tmp_path)
    override_git = stub_dir / "git"

    env = dict(os.environ)
    env.pop("PGAI_AGENT_ROLE", None)
    env.pop("PGAI_GIT_BOUNDARY_GUARD_EVIDENCE_FILE", None)
    env.pop("PGAI_GIT_GUARD_DEPTH", None)
    env["PGAI_TASK_ID"] = "TEST-TASK"
    env["GIT_GUARD_REAL_GIT"] = str(override_git)

    result = subprocess.run(
        [str(_SHIM), "status", "--short"],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
        cwd=str(_TEAM_DIR.parent),
    )

    assert result.returncode == 0
    assert "STUBGIT-INVOKED:status --short" in result.stdout


def test_recursion_sentinel_skips_policy_on_seeded_reentry(tmp_path: pathlib.Path) -> None:
    """A seeded re-entrant invocation (PGAI_GIT_GUARD_DEPTH already set on
    entry, as it would be if some future edit reintroduced a bare git call
    inside the guard) exits straight to the resolved real binary without
    ever running git_boundary_guard_enforce -- a fetch that would normally
    be refused under role=coder passes through untouched, and the depth
    value the stub observes proves the sentinel never incremented it."""
    stub_dir = tmp_path / "stub_bin"
    stub_dir.mkdir()
    stub_git = stub_dir / "git"
    stub_git.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'STUBGIT-INVOKED:%s DEPTH=%s\\n' \"$*\" \"${PGAI_GIT_GUARD_DEPTH:-unset}\"\n"
        "exit 0\n",
        encoding="utf-8",
    )
    stub_git.chmod(0o755)
    evidence = tmp_path / "evidence.jsonl"

    env = dict(os.environ)
    env["PGAI_AGENT_ROLE"] = "coder"
    env["PGAI_GIT_BOUNDARY_GUARD_EVIDENCE_FILE"] = str(evidence)
    env["PGAI_TASK_ID"] = "TEST-TASK"
    env["PGAI_GIT_GUARD_DEPTH"] = "1"  # seed: simulate an already-guarded frame
    env["PATH"] = f"{stub_dir}:{env.get('PATH', '')}"

    result = subprocess.run(
        [str(_SHIM), "fetch", "--all"],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
        cwd=str(_TEAM_DIR.parent),
    )

    # Under role=coder this verb is normally refused (see
    # test_non_cm_role_refuses_remote_verb); the sentinel bypasses that
    # policy entirely on re-entry.
    assert result.returncode == 0
    assert "STUBGIT-INVOKED:fetch --all DEPTH=1" in result.stdout
    assert _read_evidence(evidence) == []


def test_process_watchdog_sees_no_second_shim_and_bounded_descendants(
    tmp_path: pathlib.Path,
) -> None:
    """One guarded call costs a handful of short-lived descendant
    processes, never a second shim: the shim's own directory sits ahead of
    the stub on PATH, the exact wake-time arrangement that lets one
    guarded call fork an unbounded storm if the shim ever re-invokes
    itself, and a live /proc watchdog samples the process tree while the
    stub sleeps so a regression that made the shim re-invoke itself would
    show up as an extra process running from this same shim path."""
    stub_dir = tmp_path / "stub_bin"
    stub_dir.mkdir()
    stub_git = stub_dir / "git"
    stub_git.write_text(
        "#!/usr/bin/env bash\n"
        "sleep 0.4\n"
        "printf 'STUBGIT-INVOKED:%s\\n' \"$*\"\n"
        "exit 0\n",
        encoding="utf-8",
    )
    stub_git.chmod(0o755)

    env = dict(os.environ)
    env.pop("PGAI_AGENT_ROLE", None)
    env.pop("PGAI_GIT_BOUNDARY_GUARD_EVIDENCE_FILE", None)
    env.pop("PGAI_GIT_GUARD_DEPTH", None)
    env.pop("GIT_GUARD_REAL_GIT", None)
    env["PGAI_TASK_ID"] = "TEST-TASK"
    env["PATH"] = f"{_SHIM.parent}:{stub_dir}:{env.get('PATH', '')}"

    proc = subprocess.Popen(
        [str(_SHIM), "status", "--short"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        cwd=str(_TEAM_DIR.parent),
    )

    max_descendants = 0
    second_shim_hits = 0
    deadline = time.monotonic() + 10
    try:
        while proc.poll() is None and time.monotonic() < deadline:
            pids = _descendant_pids(proc.pid)
            max_descendants = max(max_descendants, len(pids))
            for pid in pids:
                if str(_SHIM) in _proc_cmdline(pid):
                    second_shim_hits += 1
            time.sleep(0.02)
        stdout, _stderr = proc.communicate(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=10)

    assert proc.returncode == 0
    assert "STUBGIT-INVOKED:status --short" in stdout
    assert max_descendants <= 3, f"observed {max_descendants} descendant processes for one guarded call"
    assert second_shim_hits == 0, "a second shim process was observed while the guarded call was in flight"


def test_shim_hand_off_dies_when_its_caller_is_killed_by_a_timeout(
    tmp_path: pathlib.Path,
) -> None:
    """A git invocation through the shim completes or dies with the
    process that made it -- it must never survive as an orphan
    reparented to init.

    Reproduces the exact process topology a caller's own
    subprocess.run(..., timeout=N) creates when it fires: an intermediate
    process (P1, standing in for e.g. a dashboard script's own bash
    process) runs the shim as an ordinary FOREGROUND CHILD (a plain
    command, not `exec`, so P1 remains alive, blocked in wait()); P1 is
    then SIGKILLed directly -- the same single-pid kill a caller's timeout
    handling performs, never touching the shim or anything past it. A
    stub "git" that `exec`s into a long sleep (mirroring how the real git
    binary is a single process for every read-only, non-tty verb this
    shim ever passes through -- no fork of its own) stands in for a real
    git invocation still in flight at the moment P1 dies.

    Without the parent-death guard (_gbg_exec_real_git in the shim), the
    sleeping process is silently reparented to init (PPid becomes 1) and
    keeps sleeping to completion -- this assertion fails against a
    shim without that guard. With the guard, the kernel delivers SIGKILL to it the
    moment P1 dies, so nothing descended from P1 survives the kill.
    """
    stub_dir = tmp_path / "stub_bin"
    stub_dir.mkdir()
    stub_git = stub_dir / "git"
    marker = tmp_path / "stub-alive-marker"
    stub_git.write_text(
        "#!/usr/bin/env bash\n"
        f"touch {str(marker)!r}\n"
        "exec sleep 30\n",
        encoding="utf-8",
    )
    stub_git.chmod(0o755)

    env = dict(os.environ)
    env.pop("PGAI_AGENT_ROLE", None)
    env.pop("PGAI_GIT_BOUNDARY_GUARD_EVIDENCE_FILE", None)
    env.pop("PGAI_GIT_GUARD_DEPTH", None)
    env.pop("GIT_GUARD_REAL_GIT", None)
    env["PGAI_TASK_ID"] = "TEST-TASK"
    env["PATH"] = f"{stub_dir}:{env.get('PATH', '')}"

    # P1: an intermediate bash process that runs the shim as an ordinary
    # foreground child (fork, not exec) -- the trailing "; true" stops
    # bash from tail-call-exec'ing the shim invocation away, so P1 stays a
    # distinct, killable process for as long as its child (the shim, then
    # the stub) is in flight.
    p1 = subprocess.Popen(
        ["bash", "-c", '"$0" -C "$1" status --short; true', str(_SHIM), str(_TEAM_DIR.parent)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    survivors: list[int] = []
    try:
        deadline = time.monotonic() + 10
        while not marker.exists():
            assert time.monotonic() < deadline, "stub git never started -- test setup is broken"
            time.sleep(0.02)

        descendants = _descendant_pids(p1.pid)
        assert descendants, "the shim never forked a descendant to check -- test setup is broken"

        p1.kill()  # SIGKILL the direct caller only -- exactly what a single-pid timeout kill does
        p1.wait(timeout=10)

        deadline = time.monotonic() + 5
        survivors = [pid for pid in descendants if _proc_ppid(pid) is not None]
        while survivors and time.monotonic() < deadline:
            time.sleep(0.05)
            survivors = [pid for pid in descendants if _proc_ppid(pid) is not None]

        assert not survivors, (
            f"descendant(s) {[(pid, _proc_cmdline(pid)) for pid in survivors]} of the "
            f"killed caller (pid {p1.pid}) are still alive -- a shim invocation must die "
            "with the process that made it, not be reparented to init and leak"
        )
    finally:
        if p1.poll() is None:
            p1.kill()
            p1.wait(timeout=10)
        for pid in survivors:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


_SANCTIONED_GIT_TOKEN_IDIOMS = ("command -v git", "/usr/bin/git")


def test_shim_body_contains_no_bare_git_invocation() -> None:
    """docs/coding-standards.md's authoring contract for this file requires
    the literal command name "git" to never appear as an invocation in the
    shim body -- every internal use is the resolved-binary variable. The
    two sanctioned exceptions are the PATH-lookup idiom (`command -v git`,
    a name resolution, not an invocation) and the `/usr/bin/git` fallback
    path text; comment lines and echoed message strings are exempt outright
    per the acceptance criterion's own wording."""
    violations: list[str] = []
    for lineno, line in enumerate(_SHIM.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("echo "):
            continue  # message string line
        working = line
        for idiom in _SANCTIONED_GIT_TOKEN_IDIOMS:
            working = working.replace(idiom, "")
        if re.search(r"(?<![\w./$-])git(?![\w-])", working):
            violations.append(f"{lineno}: {line}")

    assert not violations, "bare git invocation(s) found outside sanctioned idioms:\n" + "\n".join(
        violations
    )


def test_shim_self_corrects_when_path_resolves_to_an_unrelated_shim_copy(
    tmp_path: pathlib.Path,
) -> None:
    """Guards against a specific way this shim could hang instead of
    terminate: if the resolved real-git path were ever cached and handed
    down for the recursion sentinel to reuse blindly, then a PATH entry
    that resolves to something ELSE behaving like this same shim (rather
    than the shim's own directory, which is correctly excluded) would make
    the sentinel re-exec that wrong target forever -- a single process
    spinning at full CPU with no children, not a fork storm, but just as
    fatal to the host. Never caching the resolved binary is what prevents
    this: each invocation re-derives it from ITS OWN current directory, so
    a second hop through an unrelated shim-shaped PATH entry corrects
    itself by excluding THAT entry's own directory and finding real git
    one hop later. This test copies the actual shim file to an unrelated
    directory (simulating a rogue PATH entry that happens to be another
    instance of this same script, not a symlink or alias of the shim's own
    directory) and proves the call still completes well within a timeout
    instead of spinning forever."""
    rogue_dir = tmp_path / "rogue_bin"
    rogue_dir.mkdir()
    rogue_shim = rogue_dir / "git"
    rogue_shim.write_bytes(_SHIM.read_bytes())
    rogue_shim.chmod(0o755)

    env = {
        "PATH": f"{rogue_dir}:/usr/bin:/bin",
        "PGAI_TASK_ID": "TEST-TASK",
        "HOME": os.environ.get("HOME", "/root"),
    }

    result = subprocess.run(
        [str(_SHIM), "-C", str(_TEAM_DIR.parent), "status", "--short"],
        capture_output=True,
        text=True,
        timeout=10,  # generous; a correct run completes in well under 1s
        env=env,
    )

    assert result.returncode == 0, result.stderr
