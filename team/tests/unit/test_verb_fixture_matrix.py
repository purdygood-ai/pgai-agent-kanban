"""
test_verb_fixture_matrix.py
============================
The per-verb fixture matrix: one data-driven suite that iterates the
operator verb manifest (``team/config/operator_verbs.manifest``, read
through ``pgai_agent_kanban.lib.operator_verb_manifest``) and asserts every
contract clause against every verb it declares. A verb added to the
manifest with no matching scenario entry makes this suite fail, naming the
verb -- that failure mode is the whole point: the matrix is the
specification made executable, so a verb that drifts out of contract fails
loudly instead of decaying silently.

Columns asserted, one per verb:

  help          --help exits 0 and lists exactly the verb's declared flags
                (every declared flag appears as a "--flag" token -- the "h"
                entry, the --help alias, as the "-h" on the "--help, -h"
                line; no flag declared for a DIFFERENT verb, drawn from the
                manifest's own flag universe, leaks into this verb's help
                text).
  unknown-flag  An undeclared flag is refused with the uniform
                "<script>: unknown argument: --<flag>" message and a
                non-zero exit -- shared infrastructure
                (operator_args_validate_known) that covers every verb
                identically, so this column has 100% coverage with no
                per-verb scenario data.
  dual-channel  A CLI invocation that reaches one of the verb's own
                declared refusals emits all three dual-channel fields
                (error:/message:/remedy:) with a remedy containing no
                angle-bracket placeholder.
  idempotence   A verb the manifest declares "idempotent" produces no
                second write and exits 0 on a repeat invocation with the
                same arguments.
  REST parity   A verb declaring a REST twin reproduces, over HTTP, the
                same machine error key its CLI produces for at least one
                shared refusal.

A COLUMN THE MATRIX CANNOT COVER FOR A GIVEN VERB IS RECORDED, NOT SKIPPED
---------------------------------------------------------------------------
Three verbs' contract predates this matrix in a way that keeps a column
from applying cleanly, and each is handled by recording an explicit,
verified reason rather than a silent omission:

  - create-project.sh was once declared "idempotent" in the manifest, but
    its own regression test (team/tests/integration/test_create_project.py::
    test_create_project_refuses_duplicate_project_directory) locks in a
    non-zero exit on a re-run against an existing directory -- a
    deliberate, already-shipped safety contract the matrix must not
    weaken. The manifest's own idempotence-column header names this exact
    situation ("a dedicated per-verb behavioural fixture matrix is the
    authoritative source for idempotence -- treat a mismatch there as the
    correction, not this column") and licenses correcting the column
    rather than the behaviour, so the manifest's create-project row reads
    "non-idempotent" (team/config/operator_verbs.manifest) and the
    idempotence column below has no entry to check for it.
  - Several verbs shipped before this release's dual-channel-refusal
    convention (release-locks' REST twin, create-project, add-project,
    remove-project) validate their own arguments with plain
    stderr lines rather than the shared ``pgai_emit_error_contract``
    emitter -- confirmed by reading each script's own refusal sites (grep
    for ``pgai_emit_error_contract`` / ``ERROR:``) rather than assumed.
    Each is recorded in ``_DUAL_CHANNEL_SCENARIOS`` and/or
    ``_REST_PARITY_SCENARIOS`` as a ``NotApplicable`` entry naming the
    concrete reason; the matrix's coverage test still requires every
    active verb to have an entry (a verb that is quietly missing from the
    dict fails loudly), so nothing here is a silent skip. show-queues and
    verify-rc-state route their project argument through
    ``pp_resolve_project_name`` and speak the dual-channel contract for
    their own argument-validation refusals; halt-global, unhalt-global,
    and wake-now now route their own argument-validation refusals through
    ``pgai_emit_error_contract`` directly. All five carry a real
    ``DualChannelCase`` below instead of a ``NotApplicable`` entry, but
    all five ALSO remain ``NotApplicable`` in ``_REST_PARITY_SCENARIOS``
    (``_NO_REST_PARITY_OUT_OF_SCOPE`` for all five): their refusals are
    covered on the CLI side only, with no REST-parity assertion wired for
    any of them.
  - ``why`` (GET /verdicts) and ``deep-verify`` (POST /operations/deep-
    verify) have a CLI-reachable ``project_resolve.not_found`` refusal
    (covered in ``_DUAL_CHANNEL_SCENARIOS``) that is NOT reproduced over
    HTTP: /verdicts pre-validates project registration at the API layer
    and returns a generic 422 rather than forwarding to why.sh (see
    ``reads.py::get_verdicts``), and POST /operations/deep-verify
    dispatches the script detached and returns before the script's own
    validation can be observed synchronously (see the module docstring's
    "Long-running verbs" section in operations.py). Both are recorded as
    ``NotApplicable`` in ``_REST_PARITY_SCENARIOS`` only -- their CLI dual
    -channel coverage is unaffected.

None of the above weakens an assertion to make a verb pass (roles/CODER.md,
"Constraints"): every entry above is either a verified pre-existing
behavioural fact recorded with its concrete reason, or (create-project) a
manifest metadata correction explicitly licensed by the manifest's own
documented policy.

Fixtures
--------
All CLI invocations run against a throwaway ``tmp_path`` kanban root built
by this module's own small fixture-builders (``_register_project``,
``_write_task``, ...) -- the same shape ``test_defer_operator_verbs.py`` and
``test_rest_twins_action_verbs.py`` already use. All REST invocations use a
FastAPI ``TestClient`` (no live socket) against the same throwaway root,
with scripts resolved from the real ``team/`` dev tree -- the split
``test_rest_twins_read_verbs.py`` documents. No bare ``/tmp`` paths; no live
kanban root is ever touched.

See ``docs/test-authoring-guidelines.md`` for the anti-patterns this suite
is held to. The verb-name loop below is the one shape that guideline's
Anti-pattern 1 explicitly carves out an exception for ("only when the test
genuinely intends to cover every current and future symbol matching the
pattern") -- the verb list is read from the manifest and never hardcoded
in this test file, so a new verb is covered without editing it.
"""

from __future__ import annotations

import atexit
import dataclasses
import hashlib
import os
import pathlib
import re
import subprocess
import sys
from typing import Callable, Optional

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_THIS_FILE = pathlib.Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/tests/unit/ -> team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"

if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from pgai_agent_kanban.lib.operator_verb_manifest import (  # noqa: E402
    NO_OPERATOR_VALID_FLAGS_ARRAY,
    Verb,
    load_manifest,
)

_MANIFEST: dict[str, Verb] = load_manifest()
_ACTIVE_VERB_NAMES: list[str] = [
    name for name, v in _MANIFEST.items() if v.status == "active"
]

# The full flag universe declared anywhere in the manifest -- used by the
# help column to assert a verb's help text carries no OTHER verb's flag.
_ALL_DECLARED_FLAGS: set[str] = {f for v in _MANIFEST.values() for f in v.flags}

_UNKNOWN_FLAG_PROBE = "zz-nonexistent-flag-for-matrix-probe"
_PLACEHOLDER_RE = re.compile(r"<[^<>]+>")


def _flag_token_re(flag: str) -> re.Pattern:
    """Compile a regex matching '--<flag>' as a whole token (no partial
    match against a longer flag sharing the same prefix, e.g. '--minor'
    must not match inside a hypothetical '--minor-ceiling')."""
    return re.compile(rf"--{re.escape(flag)}(?![\w-])")


def _declared_flag_listed_re(flag: str) -> re.Pattern:
    """Compile the regex that finds a verb's own declared flag in its help.

    The "h" entry a verb declares so that -h parses is the --help alias: help
    lists it as the "-h" on the "--help, -h" line, never as a "--h" option of
    its own. Every other flag is a whole "--<flag>" token (_flag_token_re).
    """
    if flag == "h":
        return re.compile(r"(?<![\w-])-h(?![\w-])")
    return _flag_token_re(flag)


# ---------------------------------------------------------------------------
# Shared fixture builders (mirrors test_defer_operator_verbs.py /
# test_rest_twins_action_verbs.py's own helpers -- kept local per Anti-
# pattern 3's function-scope preference rather than imported cross-module).
# ---------------------------------------------------------------------------


def _env_for(kanban_root: pathlib.Path, extra: Optional[dict] = None) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PGAI_PROJECT_NAME", None)
    env.pop("PGAI_DEV_TREE_PATH", None)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    env["KANBAN_ROOT"] = str(kanban_root)
    if extra:
        env.update(extra)
    return env


def _run(
    script: pathlib.Path,
    args: list[str],
    kanban_root: pathlib.Path,
    extra_env: Optional[dict] = None,
    timeout: int = 20,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(script), *args],
        capture_output=True,
        text=True,
        env=_env_for(kanban_root, extra_env),
        timeout=timeout,
    )


def _write_projects_cfg(root: pathlib.Path, projects: list[tuple[str, int]]) -> None:
    lines = []
    for name, priority in projects:
        lines.append(f"[project:{name}]")
        lines.append(f"priority={priority}")
        lines.append("")
    root.mkdir(parents=True, exist_ok=True)
    (root / "projects.cfg").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _register_project(root: pathlib.Path, name: str, sibling: str = "sibling-proj") -> pathlib.Path:
    """Register *name* (plus a distinct sibling) in projects.cfg and create
    its directory. A sibling project is always present so a not-found probe
    against a THIRD, never-registered name has a non-empty registry to miss
    against (pp_resolve_project_name pass-through-when-empty would otherwise
    swallow the refusal -- see project_paths.sh's own docstring)."""
    _write_projects_cfg(root, [(name, 1), (sibling, 2)])
    project_dir = root / "projects" / name
    project_dir.mkdir(parents=True, exist_ok=True)
    (root / "projects" / sibling).mkdir(parents=True, exist_ok=True)
    return project_dir


def _write_task(project_dir: pathlib.Path, task_id: str, state: str, role: str = "CODER") -> pathlib.Path:
    task_dir = project_dir / "tasks" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "status.md").write_text(
        f"## State\n{state}\n\n## Role\n{role}\n\n## Summary\nfixture task\n\n"
        "## Artifacts\nnone\n\n## Blockers\nnone\n\n## Needs Human\nno\n",
        encoding="utf-8",
    )
    (task_dir / "README.md").write_text(
        f"## Owner\nClaude\n\n## Role\n{role}\n", encoding="utf-8"
    )
    queues_dir = project_dir / "tasks" / "queues"
    queues_dir.mkdir(parents=True, exist_ok=True)
    backlog = queues_dir / f"{role.lower()}_backlog.md"
    marker = "[ ]" if state in ("BACKLOG", "WAITING") else "[x]"
    backlog.write_text(f"- {marker} {task_id} -- fixture task\n", encoding="utf-8")
    return task_dir


def _hash_tree(root: pathlib.Path, exclude: frozenset[str] = frozenset()) -> dict[str, str]:
    """Hash every file under *root*, keyed by path relative to root, skipping
    any relative path in *exclude* (audit-trail files a verb legitimately
    appends to on every successful run, e.g. logs/reset.log -- excluding
    them keeps the check aimed at "no second write to the verb's own
    state", not "the tree never appends an audit line")."""
    hashes: dict[str, str] = {}
    if not root.exists():
        return hashes
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(root))
        if rel in exclude:
            continue
        hashes[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def _extract_dual_channel(stderr: str) -> Optional[dict[str, str]]:
    """Parse the three dual-channel fields from *stderr*, tolerant of the
    house shape's colon-alignment whitespace. Returns None if any of the
    three fields is absent."""
    fields: dict[str, str] = {}
    for name in ("error", "message", "remedy"):
        m = re.search(rf"^{name}:\s*(.*)$", stderr, re.MULTILINE)
        if m:
            fields[name] = m.group(1).strip()
    if set(fields) != {"error", "message", "remedy"}:
        return None
    return fields


def _make_client(
    sandbox_root: pathlib.Path, monkeypatch: pytest.MonkeyPatch, *, in_process: bool = False
):
    """FastAPI TestClient whose data resolves against sandbox_root.

    For a route that shells to its script, scripts resolve to the real team/
    dev tree while data resolves against sandbox_root -- the split
    test_rest_twins_read_verbs.py's own docstring documents. For a route
    that calls its ops function in process (*in_process*, a manifest row
    whose ``impl`` is ``module:``), the API's own kanban_root is the
    sandbox."""
    from pgai_agent_kanban.api.app import create_app
    from pgai_agent_kanban.api.config import ApiConfig
    from fastapi.testclient import TestClient

    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(sandbox_root))
    monkeypatch.setenv("KANBAN_ROOT", str(sandbox_root))
    monkeypatch.delenv("PGAI_PROJECT_NAME", raising=False)
    monkeypatch.delenv("PGAI_DEV_TREE_PATH", raising=False)

    api_root = sandbox_root if in_process else _TEAM_DIR
    cfg = ApiConfig(host="127.0.0.1", port=0, kanban_root=api_root)
    return TestClient(create_app(cfg=cfg), raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# Scenario data types
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class NotApplicable:
    """This column does not apply to this verb -- reason is mandatory and is
    surfaced in the test's own assertion message, never a silent skip."""

    reason: str


@dataclasses.dataclass(frozen=True)
class DualChannelCase:
    """A CLI invocation expected to reach a dual-channel refusal.

    extra_args: CLI arguments appended after '--project <ghost>' (or, for
                verbs with no --project flag at all, the full argument list
                a caller supplies instead -- see build_args).
    build_args: when set, overrides extra_args entirely and receives
                (ghost_project_name, real_project_name) to build the full
                argv itself (used by verbs whose refusal trigger is not
                project-based, e.g. release-locks).
    error_key:  substring expected in the resolved 'error' dual-channel
                field.
    setup:      optional extra fixture setup beyond _register_project.
    """

    error_key: str
    extra_args: tuple[str, ...] = ()
    build_args: Optional[Callable[[str, str], list[str]]] = None
    setup: Optional[Callable[[pathlib.Path], None]] = None


@dataclasses.dataclass(frozen=True)
class IdempotentCase:
    """A safe, self-contained repeat-invocation scenario for a verb the
    manifest declares idempotent."""

    setup: Callable[[pathlib.Path], tuple[list[str], str]]  # -> (argv, project_dir_name or "")
    volatile: frozenset[str] = frozenset()


@dataclasses.dataclass(frozen=True)
class RestParityCase:
    """A refusal reachable identically via the CLI and via the verb's REST
    twin, comparing the shared machine error key."""

    method: str  # "GET" | "POST"
    route: str
    extra_args: tuple[str, ...] = ()
    rest_fields: dict = dataclasses.field(default_factory=dict)
    error_key: str = "project_resolve.not_found"
    is_query: bool = False  # GET verbs pass rest_fields as query params
    rest_fields_builder: Optional[Callable[[str], dict]] = None
    # When set, overrides extra_args entirely to build the CLI argv (ghost
    # project name in) -- used by verbs whose refusal trigger needs a
    # positional argument rather than a flag (intake.sh's source file).
    build_cli_args: Optional[Callable[[str], list[str]]] = None


# ---------------------------------------------------------------------------
# Per-verb scenario registries
#
# Each dict is keyed by every ACTIVE verb name the manifest declares. The
# coverage tests below (test_every_active_verb_has_a_*_entry) fail, naming
# the verb, when a key is missing -- this is what makes "add a bogus verb to
# the manifest" fail loudly rather than being silently skipped by the loop.
# ---------------------------------------------------------------------------

_NO_DUAL_CHANNEL = (
    "{script} validates its own arguments with plain stderr lines, not the "
    "shared pgai_emit_error_contract/dual-channel emitter (confirmed by "
    "reading every 'exit 1'/'ERROR:' site in the script); there is no "
    "reachable error:/message:/remedy: refusal to assert on for this verb "
    "in the current tree."
)


def _dual_channel_restore_writable_at_exit(root: pathlib.Path) -> None:
    """Schedule `root` back to a writable mode at interpreter shutdown.

    A read-only `root` is the whole point of the two setup functions below
    (it is what forces the sentinel write/removal to fail), but leaving it
    read-only past the end of the CLI invocation would still needlessly
    force the framework's own post-suite `pgai_temp_cleanup_tests` step to
    fall back on its own chmod-then-retry recovery rather than removing the
    subtree on the first attempt. This restoration is a separate shell
    command that runs after this pytest process has already exited, so an
    ``atexit`` callback (which fires during THIS process's shutdown,
    strictly before that later shell step runs) is what makes it land in
    time -- a courtesy that keeps every scenario's leftover from lingering
    a moment longer than it needs to, not a load-bearing precondition for
    that later sweep step, which recovers on its own either way."""
    atexit.register(os.chmod, root, 0o755)


def _dual_channel_setup_halt_global(root: pathlib.Path) -> None:
    """Force halt-global.sh's sentinel-write refusal rather than its
    KANBAN_ROOT-missing one: KANBAN_ROOT is always a real, present directory
    by the time this scenario's argv runs (the shared harness below
    re-creates `root` via `_register_project` whenever `projects.cfg` is
    absent, so a setup that removed `root` outright would just be undone),
    so the reachable refusal here is the HALT sentinel's write failing, not
    the directory being absent. Writing `projects.cfg` first satisfies the
    harness's own "already registered" check; making `root` read-only
    afterward (still present and listable, just not writable) forces
    `halt_global`'s `HALT.touch()` to raise -- the second of the script's
    two refusal sites. The KANBAN_ROOT-missing site is demonstrated directly
    against a scratch root instead (see
    test_kanban_root_missing_refusal_never_creates_the_root_on_repeat_invocation
    below)."""
    (root / "projects.cfg").write_text("", encoding="utf-8")
    os.chmod(root, 0o555)
    _dual_channel_restore_writable_at_exit(root)


def _dual_channel_setup_unhalt_global(root: pathlib.Path) -> None:
    """Mirrors `_dual_channel_setup_halt_global`: a pre-existing HALT file
    plus a read-only KANBAN_ROOT forces `unhalt_global`'s `HALT.unlink()` to
    raise (removing a file needs write permission on its parent directory),
    exercising the sentinel-removal refusal rather than the already-clear
    idempotent exit."""
    (root / "projects.cfg").write_text("", encoding="utf-8")
    (root / "HALT").write_text("", encoding="utf-8")
    os.chmod(root, 0o555)
    _dual_channel_restore_writable_at_exit(root)


_DUAL_CHANNEL_SCENARIOS: dict[str, "DualChannelCase | NotApplicable"] = {
    "halt": DualChannelCase(error_key="project_resolve.not_found"),
    "unhalt": DualChannelCase(error_key="project_resolve.not_found"),
    "halt-global": DualChannelCase(
        error_key="halt_global.sentinel_create_failed",
        build_args=lambda ghost, real: [],
        setup=_dual_channel_setup_halt_global,
    ),
    "unhalt-global": DualChannelCase(
        error_key="unhalt_global.sentinel_remove_failed",
        build_args=lambda ghost, real: [],
        setup=_dual_channel_setup_unhalt_global,
    ),
    "halt-after": DualChannelCase(
        error_key="project_resolve.not_found", extra_args=("--key", "rc")
    ),
    "defer": DualChannelCase(
        error_key="project_resolve.not_found", extra_args=("--until", "v9.9.9")
    ),
    "undefer": DualChannelCase(error_key="project_resolve.not_found"),
    "deep-verify": DualChannelCase(error_key="project_resolve.not_found"),
    "cold-build-now": DualChannelCase(error_key="project_resolve.not_found"),
    "wake-now": DualChannelCase(
        error_key="wake_now.agent_required",
        # wake-now.sh takes no --project flag at all -- build_args supplies
        # its full argv directly (see DualChannelCase's own docstring).
        # No arguments reaches the "neither --agent nor --all" refusal,
        # one of the verb's five distinct argument-validation causes.
        build_args=lambda ghost, real: [],
    ),
    "show": DualChannelCase(
        error_key="project_resolve.not_found", extra_args=("--key", "anything")
    ),
    "show-queues": DualChannelCase(error_key="project_resolve.not_found"),
    "why": DualChannelCase(error_key="project_resolve.not_found"),
    "release-locks": DualChannelCase(
        error_key="release_locks.not_found",
        build_args=lambda ghost, real: ["--release", "no-such-lock-in-matrix"],
    ),
    "unwind-rc": DualChannelCase(
        error_key="project_resolve.not_found", extra_args=("--key", "v9.9.9")
    ),
    "verify-rc-state": DualChannelCase(
        error_key="project_resolve.not_found",
        build_args=lambda ghost, real: [ghost],
    ),
    "reset": DualChannelCase(
        error_key="project_resolve.not_found", extra_args=("--key", "anything")
    ),
    "close": DualChannelCase(
        error_key="project_resolve.not_found", extra_args=("--key", "anything")
    ),
    "wontdo": DualChannelCase(
        error_key="project_resolve.not_found", extra_args=("--key", "anything")
    ),
    "delete": DualChannelCase(
        error_key="project_resolve.not_found", extra_args=("--key", "anything")
    ),
    "intake": DualChannelCase(
        error_key="project_resolve.not_found",
        build_args=lambda ghost, real: ["--project", ghost, "/nonexistent/intake-source-file.md"],
    ),
    "set-version-ceiling": DualChannelCase(
        error_key="project_resolve.not_found", extra_args=("--minor", "5")
    ),
    "switch-provider": DualChannelCase(
        error_key="switch_provider.unknown_provider",
        build_args=lambda ghost, real: ["--provider", "nobody"],
    ),
    "create-project": NotApplicable(_NO_DUAL_CHANNEL.format(script="create-project.sh")),
    "add-project": NotApplicable(_NO_DUAL_CHANNEL.format(script="add-project.sh")),
    "remove-project": NotApplicable(_NO_DUAL_CHANNEL.format(script="remove-project.sh")),
    "ship-rc": DualChannelCase(
        error_key="project_resolve.not_found", extra_args=("--key", "v9.9.9")
    ),
    "rc-metrics": NotApplicable(
        _NO_DUAL_CHANNEL.format(script="dashboard/show-metrics.sh")
    ),
    "agent-metrics": NotApplicable(
        _NO_DUAL_CHANNEL.format(script="dashboard/agent-metrics.sh")
    ),
    "cancel-rc": NotApplicable(_NO_DUAL_CHANNEL.format(script="cm/cancel-rc.sh")),
    # release-state.sh / deep-verify-status.sh route --project through
    # pp_resolve_project_name exactly like show-queues.sh / why.sh -- a
    # ghost project name refuses project_resolve.not_found before either
    # script's own Python heredoc ever runs.
    "release-state": DualChannelCase(error_key="project_resolve.not_found"),
    "deep-verify-status": DualChannelCase(error_key="project_resolve.not_found"),
    # archive.sh hands --project to run_archive, which resolves it through
    # ops.resolve.resolve_project_name before reading or moving anything.
    "archive": DualChannelCase(
        error_key="project_resolve.not_found", extra_args=("--dry-run",)
    ),
}


# ---------------------------------------------------------------------------
# halt-global / unhalt-global: a refusal whose subject is a missing
# KANBAN_ROOT must never create it as a side effect.
#
# Both verbs' kanban_root_missing guard fires precisely because KANBAN_ROOT
# does not exist. pgai_emit_error_contract's refusal-ledger recording used
# to create the parent directory tree on its way to writing
# projects/<name>/refusal-ledger.md, which meant a SECOND identical
# invocation found a real (if fabricated) directory and passed the guard.
# This is reachable only with PGAI_PROJECT_NAME set (the framework's own
# wake scripts and shell-env export it); with it unset, ledger recording is
# skipped entirely by design and the defect never appears -- both shapes
# are exercised below, as the reproduction and its negative control.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "script_name, error_key",
    [
        ("halt-global.sh", "halt_global.kanban_root_missing"),
        ("unhalt-global.sh", "unhalt_global.kanban_root_missing"),
    ],
)
def test_kanban_root_missing_refusal_never_creates_the_root_on_repeat_invocation(
    tmp_path: pathlib.Path, script_name: str, error_key: str
) -> None:
    absent_root = tmp_path / "absent-kanban-root"
    script = _SCRIPTS_DIR / script_name
    env_extra = {"PGAI_PROJECT_NAME": "matrix-probe-project"}

    first = _run(script, [], absent_root, extra_env=env_extra)
    assert first.returncode == 1, (
        f"{script_name} exited {first.returncode} on a missing KANBAN_ROOT "
        f"(expected 1).\nstdout: {first.stdout}\nstderr: {first.stderr}"
    )
    first_fields = _extract_dual_channel(first.stderr)
    assert first_fields is not None, f"no dual-channel fields in stderr: {first.stderr}"
    assert first_fields["error"] == error_key
    assert not absent_root.exists(), (
        f"{script_name}'s first refusal must not create KANBAN_ROOT, but "
        f"{absent_root} exists after it: "
        f"{sorted(str(p) for p in absent_root.rglob('*'))}"
    )

    second = _run(script, [], absent_root, extra_env=env_extra)
    assert second.returncode == 1, (
        f"{script_name}'s SECOND invocation exited {second.returncode}, not "
        "1 -- this is the exact regression this test pins: a refusal "
        "recorded on the first call fabricated KANBAN_ROOT, so the second "
        "call passed the guard and reported success against a directory "
        f"that was never a real kanban root.\nstdout: {second.stdout}\n"
        f"stderr: {second.stderr}"
    )
    second_fields = _extract_dual_channel(second.stderr)
    assert second_fields is not None, f"no dual-channel fields in stderr: {second.stderr}"
    assert second_fields["error"] == error_key
    assert not absent_root.exists(), (
        f"{script_name}'s second refusal must still find no KANBAN_ROOT, "
        f"but {absent_root} exists after two invocations: "
        f"{sorted(str(p) for p in absent_root.rglob('*'))}"
    )


def test_kanban_root_missing_refusal_negative_control_without_project_name(
    tmp_path: pathlib.Path,
) -> None:
    """Without PGAI_PROJECT_NAME, refusal-ledger recording is skipped
    entirely by design (no project in scope) -- the fabricated-root defect
    never reproduces in this shape, so both invocations must refuse
    identically and the root must never exist, exactly as with
    PGAI_PROJECT_NAME set above."""
    absent_root = tmp_path / "absent-kanban-root-no-project"
    script = _SCRIPTS_DIR / "halt-global.sh"

    for _ in range(2):
        result = _run(script, [], absent_root)
        assert result.returncode == 1
        fields = _extract_dual_channel(result.stderr)
        assert fields is not None, f"no dual-channel fields in stderr: {result.stderr}"
        assert fields["error"] == "halt_global.kanban_root_missing"
        assert not absent_root.exists(), (
            f"halt-global.sh must not create {absent_root} even without "
            "PGAI_PROJECT_NAME set"
        )


_NO_IDEMPOTENCE_DECLARED = (
    "the manifest declares '{value}' idempotence for this verb, not "
    "'idempotent'; the repeat-invocation column does not apply."
)

_IDEMPOTENT_SCENARIOS: dict[str, "IdempotentCase | NotApplicable"] = {}


def _idempotent_halt(root: pathlib.Path) -> tuple[list[str], str]:
    _register_project(root, "idem-halt")
    return (["--project", "idem-halt"], "idem-halt")


def _idempotent_unhalt(root: pathlib.Path) -> tuple[list[str], str]:
    _register_project(root, "idem-unhalt")
    return (["--project", "idem-unhalt"], "idem-unhalt")


def _idempotent_halt_global(root: pathlib.Path) -> tuple[list[str], str]:
    return ([], "")


def _idempotent_unhalt_global(root: pathlib.Path) -> tuple[list[str], str]:
    return ([], "")


def _idempotent_halt_after(root: pathlib.Path) -> tuple[list[str], str]:
    _register_project(root, "idem-halt-after")
    return (["--project", "idem-halt-after", "--key", "coder"], "idem-halt-after")


def _idempotent_defer(root: pathlib.Path) -> tuple[list[str], str]:
    _register_project(root, "idem-defer")
    return (["--project", "idem-defer", "--until", "v9.9.9"], "idem-defer")


def _idempotent_undefer(root: pathlib.Path) -> tuple[list[str], str]:
    _register_project(root, "idem-undefer")
    return (["--project", "idem-undefer"], "idem-undefer")


def _idempotent_release_locks(root: pathlib.Path) -> tuple[list[str], str]:
    locks_dir = root / "locks"
    locks_dir.mkdir(parents=True, exist_ok=True)
    (locks_dir / "idem-lock.lock").write_text("", encoding="utf-8")
    return (["--release", "idem-lock"], "")


def _idempotent_unwind_rc(root: pathlib.Path) -> tuple[list[str], str]:
    # --dry-run: the full real re-open fixture is ticket 5's own deliverable
    # (task README "Notes"); this column only needs a safe, self-contained
    # repeat-invocation check, and a dry-run mutates nothing on either run.
    project_dir = _register_project(root, "idem-unwind-rc")
    dev_tree = root / "idem-unwind-rc-dev-tree"
    dev_tree.mkdir(parents=True, exist_ok=True)
    (project_dir / "project.cfg").write_text(
        "[project]\n"
        "project_name = idem-unwind-rc\n"
        f"dev_tree_path = {dev_tree}\n"
        "workflow_type = release\n",
        encoding="utf-8",
    )
    # unwind-rc.sh refuses to run at all unless the project (or the whole
    # kanban) is already halted -- a per-project HALT file satisfies that
    # pre-flight guard.
    (project_dir / "HALT").write_text("", encoding="utf-8")
    # Active RC must match the --key being unwound (v9.9.9) or the version-
    # mismatch guard refuses (bypassable with --force, which this scenario
    # deliberately avoids to keep the repeat invocation as inert as
    # possible).
    (project_dir / "release-state.md").write_text(
        "# Release State\n\n## Active RC\nv9.9.9\n", encoding="utf-8"
    )
    return (["--project", "idem-unwind-rc", "--key", "v9.9.9", "--dry-run"], "idem-unwind-rc")


def _idempotent_reset(root: pathlib.Path) -> tuple[list[str], str]:
    project_dir = _register_project(root, "idem-reset")
    _write_task(project_dir, "CODER-20260101-001-fixture", "BACKLOG")
    return (["--project", "idem-reset", "--key", "CODER-20260101-001-fixture"], "idem-reset")


def _idempotent_close(root: pathlib.Path) -> tuple[list[str], str]:
    project_dir = _register_project(root, "idem-close")
    _write_task(project_dir, "CODER-20260101-001-fixture", "WORKING")
    return (["--project", "idem-close", "--key", "CODER-20260101-001-fixture"], "idem-close")


def _idempotent_wontdo(root: pathlib.Path) -> tuple[list[str], str]:
    project_dir = _register_project(root, "idem-wontdo")
    _write_task(project_dir, "CODER-20260101-001-fixture", "WORKING")
    return (["--project", "idem-wontdo", "--key", "CODER-20260101-001-fixture"], "idem-wontdo")


def _idempotent_set_version_ceiling(root: pathlib.Path) -> tuple[list[str], str]:
    project_dir = _register_project(root, "idem-svc")
    (project_dir / "project.cfg").write_text(
        "[project]\nproject_name = idem-svc\n\n[versioning]\nmax_patch = 99\n",
        encoding="utf-8",
    )
    return (["--project", "idem-svc", "--minor", "5"], "idem-svc")


def _idempotent_switch_provider(root: pathlib.Path) -> tuple[list[str], str]:
    (root / "kanban.cfg").write_text(
        "[providers]\nactive = codex\n", encoding="utf-8"
    )
    return (["--provider", "claude"], "")


def _idempotent_add_project(root: pathlib.Path) -> tuple[list[str], str]:
    (root / "projects" / "idem-add").mkdir(parents=True, exist_ok=True)
    return (["--project", "idem-add"], "idem-add")


def _idempotent_remove_project(root: pathlib.Path) -> tuple[list[str], str]:
    _register_project(root, "idem-remove")
    return (["--project", "idem-remove"], "idem-remove")


def _idempotent_archive(root: pathlib.Path) -> tuple[list[str], str]:
    # Three finished tasks and --count 1: the first run archives the two
    # older ones (a real move, so the repeat is meaningful); the second
    # finds nothing left to select and moves nothing.
    project_dir = _register_project(root, "idem-archive")
    for seq in ("001", "002", "003"):
        _write_task(project_dir, f"CODER-20260101-{seq}-fixture", "DONE")
    return (["--project", "idem-archive", "--count", "1"], "idem-archive")


_IDEMPOTENT_BUILDERS: dict[str, Callable[[pathlib.Path], tuple[list[str], str]]] = {
    "halt": _idempotent_halt,
    "unhalt": _idempotent_unhalt,
    "halt-global": _idempotent_halt_global,
    "unhalt-global": _idempotent_unhalt_global,
    "halt-after": _idempotent_halt_after,
    "defer": _idempotent_defer,
    "undefer": _idempotent_undefer,
    "release-locks": _idempotent_release_locks,
    "unwind-rc": _idempotent_unwind_rc,
    "reset": _idempotent_reset,
    "close": _idempotent_close,
    "wontdo": _idempotent_wontdo,
    "set-version-ceiling": _idempotent_set_version_ceiling,
    "switch-provider": _idempotent_switch_provider,
    "add-project": _idempotent_add_project,
    "remove-project": _idempotent_remove_project,
    "archive": _idempotent_archive,
}

_RESET_VOLATILE = frozenset({"logs/reset.log"})

for _verb_name, _verb in _MANIFEST.items():
    if _verb.status != "active":
        continue
    if _verb.idempotence != "idempotent":
        _IDEMPOTENT_SCENARIOS[_verb_name] = NotApplicable(
            _NO_IDEMPOTENCE_DECLARED.format(value=_verb.idempotence)
        )
    elif _verb_name in _IDEMPOTENT_BUILDERS:
        volatile = _RESET_VOLATILE if _verb_name == "reset" else frozenset()
        _IDEMPOTENT_SCENARIOS[_verb_name] = IdempotentCase(
            setup=_IDEMPOTENT_BUILDERS[_verb_name], volatile=volatile
        )
    else:
        _IDEMPOTENT_SCENARIOS[_verb_name] = NotApplicable(
            f"manifest declares '{_verb_name}' idempotent but no repeat-"
            "invocation fixture is registered for it in this matrix -- "
            "coverage gap, not a verified behavioural fact."
        )


_NO_REST_PARITY_NO_KEY = (
    "{script} has no CLI-reachable dual-channel refusal to compare (see "
    "the dual-channel column's own reason for this verb); there is no "
    "shared machine error key for the REST-parity column to assert on."
)

_NO_REST_PARITY_OUT_OF_SCOPE = (
    "{script} now has a CLI-reachable dual-channel refusal (see the "
    "dual-channel column above), but it is reached only through a "
    "CLI-only fixture (a filesystem-permission trick or an empty argv) "
    "exercised directly against the script; wiring the equivalent "
    "REST-parity assertion is not wired: this refusal is covered on the "
    "CLI side only."
)

_REST_PARITY_SCENARIOS: dict[str, "RestParityCase | NotApplicable"] = {
    "halt": RestParityCase(method="POST", route="/operations/halt", rest_fields={}),
    "unhalt": RestParityCase(method="POST", route="/operations/unhalt", rest_fields={}),
    "halt-global": NotApplicable(_NO_REST_PARITY_OUT_OF_SCOPE.format(script="halt-global.sh")),
    "unhalt-global": NotApplicable(_NO_REST_PARITY_OUT_OF_SCOPE.format(script="unhalt-global.sh")),
    "halt-after": RestParityCase(
        method="POST", route="/operations/halt-after",
        extra_args=("--key", "rc"), rest_fields={"key": "rc"},
    ),
    "defer": RestParityCase(
        method="POST", route="/operations/defer",
        extra_args=("--until", "v9.9.9"), rest_fields={"until": "v9.9.9"},
    ),
    "undefer": RestParityCase(method="POST", route="/operations/undefer", rest_fields={}),
    "deep-verify": NotApplicable(
        "POST /operations/deep-verify dispatches the script detached and "
        "returns as soon as the subprocess starts (operations.py, 'Long-"
        "running verbs') -- a project-resolution refusal inside the "
        "detached script is not observable synchronously in the HTTP "
        "response, only via the project's deep-verify ledger after the "
        "run exits, so this comparison is not meaningful for this verb."
    ),
    "cold-build-now": NotApplicable(
        "POST /operations/cold-build-now dispatches the script detached and "
        "returns as soon as the subprocess starts (operations.py, 'Long-"
        "running verbs'; the script is a named alias for deep-verify.sh "
        "--project) -- a project-resolution refusal inside the detached "
        "script is not observable synchronously in the HTTP response, only "
        "via the captured log and the project's deep-verify ledger after "
        "the run exits, so this comparison is not meaningful for this verb."
    ),
    "wake-now": NotApplicable(_NO_REST_PARITY_OUT_OF_SCOPE.format(script="wake-now.sh")),
    "show": RestParityCase(
        method="GET", route="/show", is_query=True,
        extra_args=("--key", "anything"), rest_fields={"key": "anything"},
    ),
    "show-queues": NotApplicable(
        _NO_REST_PARITY_OUT_OF_SCOPE.format(script="dashboard/show-queues.sh")
    ),
    "why": NotApplicable(
        "GET /verdicts pre-validates project registration at the API "
        "layer and returns a generic 422 rather than forwarding to "
        "why.sh (reads.py::get_verdicts) -- the CLI's own "
        "project_resolve.not_found stderr is never reproduced over HTTP "
        "for this verb by design."
    ),
    # release-locks.sh takes no --project; its REST twin (the verb's
    # mutating --release mode) warns on the unknown "project" field the
    # shared harness always sends and proceeds, so the script's own
    # release_locks.not_found refusal reaches the HTTP response.
    "release-locks": RestParityCase(
        method="POST", route="/operations/release-locks",
        error_key="release_locks.not_found",
        build_cli_args=lambda ghost: ["--release", "no-such-lock-in-matrix"],
        rest_fields={"release": "no-such-lock-in-matrix"},
    ),
    "unwind-rc": RestParityCase(
        method="POST", route="/operations/unwind-rc",
        extra_args=("--key", "v9.9.9", "--confirm"),
        # The route's confirm gate must be satisfied or the request never
        # reaches the script's own project-resolution refusal.
        rest_fields_builder=lambda ghost: {"key": "v9.9.9", "confirm": f"unwind-rc {ghost} v9.9.9"},
    ),
    "verify-rc-state": NotApplicable(
        _NO_REST_PARITY_OUT_OF_SCOPE.format(script="verify-rc-state.sh")
    ),
    "reset": RestParityCase(
        method="POST", route="/operations/reset",
        extra_args=("--key", "anything"), rest_fields={"key": "anything"},
    ),
    "close": RestParityCase(
        method="POST", route="/operations/close",
        extra_args=("--key", "anything"), rest_fields={"key": "anything"},
    ),
    "wontdo": RestParityCase(
        method="POST", route="/operations/wontdo",
        extra_args=("--key", "anything"), rest_fields={"key": "anything"},
    ),
    "delete": RestParityCase(
        method="POST", route="/operations/delete",
        extra_args=("--key", "anything"), rest_fields={"key": "anything"},
    ),
    "intake": RestParityCase(
        method="POST", route="/operations/intake",
        rest_fields={"filename": "BUG-9999.md", "content": "fixture"},
        build_cli_args=lambda ghost: [
            "--project", ghost, "/nonexistent/matrix-intake-source.md",
        ],
    ),
    "set-version-ceiling": RestParityCase(
        method="POST", route="/operations/set-version-ceiling",
        extra_args=("--minor", "5"), rest_fields={"minor": 5},
    ),
    "switch-provider": RestParityCase(
        method="POST", route="/operations/switch-provider",
        rest_fields={"provider": "nobody"},
        error_key="switch_provider.unknown_provider",
        build_cli_args=lambda ghost: ["--provider", "nobody"],
    ),
    "create-project": NotApplicable(_NO_REST_PARITY_NO_KEY.format(script="create-project.sh")),
    "add-project": NotApplicable(_NO_REST_PARITY_NO_KEY.format(script="add-project.sh")),
    "remove-project": NotApplicable(_NO_REST_PARITY_NO_KEY.format(script="remove-project.sh")),
    "ship-rc": RestParityCase(
        method="POST", route="/operations/ship-rc",
        extra_args=("--key", "v9.9.9"),
        rest_fields={"key": "v9.9.9"},
        rest_fields_builder=lambda ghost: {
            "key": "v9.9.9",
            # The confirm-gate fires at the API layer BEFORE ship-rc.sh is
            # invoked (operations.py::post_ship_rc) -- it must be supplied
            # and byte-for-byte correct or the refusal never reaches the
            # script at all, which would make this comparison meaningless.
            "confirm": f"ship-rc {ghost} v9.9.9",
        },
    ),
    "rc-metrics": NotApplicable(
        _NO_REST_PARITY_NO_KEY.format(script="dashboard/show-metrics.sh")
    ),
    "agent-metrics": NotApplicable(
        _NO_REST_PARITY_NO_KEY.format(script="dashboard/agent-metrics.sh")
    ),
    "cancel-rc": NotApplicable(
        "cm/cancel-rc.sh has no CLI-reachable dual-channel refusal to "
        "compare (see the dual-channel column's own reason for this verb): "
        "its REST twin, POST /operations/cancel-rc, surfaces the script's "
        "plain-stderr refusal as JSON under the route's own fallback key "
        "(cancel_rc.refused), which the CLI never prints, so a shared "
        "machine key does not exist for this verb to compare across "
        "surfaces."
    ),
    "release-state": NotApplicable(
        "GET /release-state resolves 'project not found' through "
        "pgai_agent_kanban.ops.read.get_release_state's own directory-"
        "existence NotFound check (a Python OpsContext read, never "
        "pp_resolve_project_name) and returns its own machine key "
        "('release_state.project_not_found') rather than forwarding to "
        "release-state.sh -- the CLI's project_resolve.not_found is never "
        "reproduced over HTTP for this verb, the same shape 'why' already "
        "carries above for the identical reason."
    ),
    "deep-verify-status": NotApplicable(
        "GET /deep-verify-status resolves 'project not found' through "
        "pgai_agent_kanban.ops.read.get_deep_verify_status's own "
        "directory-existence NotFound check (a Python OpsContext read, "
        "never pp_resolve_project_name) and returns its own machine key "
        "('deep_verify_status.project_not_found') rather than forwarding "
        "to deep-verify-status.sh -- the CLI's project_resolve.not_found "
        "is never reproduced over HTTP for this verb, the same shape "
        "'why' already carries above for the identical reason."
    ),
    "archive": RestParityCase(
        method="POST", route="/operations/archive",
        extra_args=("--dry-run",), rest_fields={"dry_run": True},
    ),
}


def _verb_script(verb: Verb) -> pathlib.Path:
    assert verb.script is not None, f"active verb {verb.name!r} has no script in the manifest"
    return _TEAM_DIR / verb.script


# ---------------------------------------------------------------------------
# Coverage tests -- every active verb must have an entry in every registry.
# A verb added to the manifest with no matching entry fails here, naming it.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("verb_name", sorted(_ACTIVE_VERB_NAMES))
def test_every_active_verb_has_a_dual_channel_scenario_entry(verb_name: str) -> None:
    assert verb_name in _DUAL_CHANNEL_SCENARIOS, (
        f"verb {verb_name!r} is declared active in the manifest but has no "
        "entry in _DUAL_CHANNEL_SCENARIOS -- every active verb must be "
        "classified as either a DualChannelCase or an explicit "
        "NotApplicable(reason)."
    )


@pytest.mark.parametrize("verb_name", sorted(_ACTIVE_VERB_NAMES))
def test_every_active_verb_has_an_idempotence_scenario_entry(verb_name: str) -> None:
    assert verb_name in _IDEMPOTENT_SCENARIOS, (
        f"verb {verb_name!r} is declared active in the manifest but has no "
        "entry in _IDEMPOTENT_SCENARIOS."
    )


@pytest.mark.parametrize("verb_name", sorted(_ACTIVE_VERB_NAMES))
def test_every_active_verb_has_a_rest_parity_scenario_entry(verb_name: str) -> None:
    assert verb_name in _REST_PARITY_SCENARIOS, (
        f"verb {verb_name!r} is declared active in the manifest but has no "
        "entry in _REST_PARITY_SCENARIOS."
    )


def test_a_bogus_manifest_entry_is_named_by_the_coverage_tests(tmp_path: pathlib.Path) -> None:
    """Behavioural proof, not just a structural claim: load a manifest with
    one extra verb the scenario registries do not know about, and confirm
    the coverage check for it fails, naming the bogus verb -- the exact
    "adding a bogus verb entry to the manifest makes the suite fail naming
    that verb" acceptance criterion, exercised directly rather than
    inferred from the parametrization shape above."""
    from pgai_agent_kanban.lib.operator_verb_manifest import load_manifest as _load

    fixture = tmp_path / "bogus.manifest"
    real_path = None
    from pgai_agent_kanban.lib.operator_verb_manifest import default_manifest_path

    real_text = default_manifest_path().read_text(encoding="utf-8")
    fixture.write_text(
        real_text
        + "V\ttotally-bogus-verb-for-matrix-test\tactive\tscripts/does-not-exist.sh\thelp\tyes\tidempotent\tABSENT\tABSENT\tABSENT\taction\tABSENT\n",
        encoding="utf-8",
    )
    bogus_manifest = _load(fixture)
    bogus_active = {n for n, v in bogus_manifest.items() if v.status == "active"}

    missing_from_dual_channel = bogus_active - set(_DUAL_CHANNEL_SCENARIOS)
    assert missing_from_dual_channel == {"totally-bogus-verb-for-matrix-test"}, (
        "expected the bogus verb, and only the bogus verb, to be absent "
        f"from _DUAL_CHANNEL_SCENARIOS; got {missing_from_dual_channel}"
    )


# ---------------------------------------------------------------------------
# Column 1 -- help: exits 0, lists exactly the verb's declared flags
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("verb_name", sorted(_ACTIVE_VERB_NAMES))
def test_help_exits_zero_and_lists_exactly_its_declared_flags(
    verb_name: str, tmp_path: pathlib.Path
) -> None:
    verb = _MANIFEST[verb_name]
    script = _verb_script(verb)
    root = tmp_path / "kanban_root"
    root.mkdir()

    result = _run(script, ["--help"], root)
    assert result.returncode == 0, (
        f"{verb_name} --help exited {result.returncode}, expected 0.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    # Help text is checked across BOTH streams: the shared convention prints
    # to stdout, but at least one script (set-version-ceiling.sh) documents
    # a deliberate, intentional exception -- its usage text always prints to
    # stderr, exit 0 included (see the script's own "Usage / help" comment).
    # Checking the union is Postel's-law tolerant of that documented
    # variation without weakening what the assertion actually verifies.
    combined = result.stdout + result.stderr

    for flag in verb.flags:
        assert _declared_flag_listed_re(flag).search(combined), (
            f"{verb_name} --help does not list its own declared flag "
            f"'--{flag}':\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    other_flags = _ALL_DECLARED_FLAGS - set(verb.flags)
    leaked = [f for f in sorted(other_flags) if _flag_token_re(f).search(combined)]
    assert not leaked, (
        f"{verb_name} --help lists flag(s) {leaked} that are not in its "
        f"own declared flag set {sorted(verb.flags)} (manifest column) "
        "-- help must list exactly the verb's own flags:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# Column 2 -- unknown-flag: uniform refusal, 100% coverage (shared infra)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("verb_name", sorted(_ACTIVE_VERB_NAMES))
def test_undeclared_flag_is_refused_with_the_uniform_message(
    verb_name: str, tmp_path: pathlib.Path
) -> None:
    verb = _MANIFEST[verb_name]
    script = _verb_script(verb)
    root = tmp_path / "kanban_root"
    root.mkdir()

    result = _run(script, [f"--{_UNKNOWN_FLAG_PROBE}"], root)
    assert result.returncode != 0, (
        f"{verb_name} accepted an undeclared flag --{_UNKNOWN_FLAG_PROBE} "
        f"(exit 0): stdout={result.stdout!r} stderr={result.stderr!r}"
    )

    if verb.script in NO_OPERATOR_VALID_FLAGS_ARRAY:
        # ship-rc.sh is the one script in the manifest that parses its own
        # arguments by hand instead of declaring OPERATOR_VALID_FLAGS
        # (operator_verb_manifest.py's own NO_OPERATOR_VALID_FLAGS_ARRAY
        # set) -- it therefore does not route through the shared
        # operator_args_validate_known() and its uniform message. It still
        # refuses non-zero and names the offending flag in its own words.
        assert _UNKNOWN_FLAG_PROBE in result.stderr, (
            f"{verb_name}: hand-rolled arg parser did not name the "
            f"unrecognised flag in stderr:\n{result.stderr}"
        )
    else:
        script_basename = script.name
        expected = f"{script_basename}: unknown argument: --{_UNKNOWN_FLAG_PROBE}"
        assert expected in result.stderr, (
            f"{verb_name} did not emit the uniform unknown-argument message.\n"
            f"expected substring: {expected!r}\nstderr:\n{result.stderr}"
        )


# ---------------------------------------------------------------------------
# Column 3 -- dual-channel: error:/message:/remedy:, remedy has no <...>
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("verb_name", sorted(_ACTIVE_VERB_NAMES))
def test_declared_refusal_emits_dual_channel_fields_with_a_runnable_remedy(
    verb_name: str, tmp_path: pathlib.Path
) -> None:
    scenario = _DUAL_CHANNEL_SCENARIOS[verb_name]
    if isinstance(scenario, NotApplicable):
        assert scenario.reason, f"{verb_name}: NotApplicable reason must be non-empty"
        pytest.skip(f"{verb_name}: {scenario.reason}")

    verb = _MANIFEST[verb_name]
    script = _verb_script(verb)
    root = tmp_path / "kanban_root"
    root.mkdir()

    if scenario.setup is not None:
        scenario.setup(root)

    ghost = "definitely-unregistered-project-for-matrix"
    real = "registered-project-for-matrix"
    if scenario.build_args is not None:
        if not (root / "projects.cfg").exists():
            _register_project(root, real)
        argv = scenario.build_args(ghost, real)
    else:
        _register_project(root, real)
        argv = ["--project", ghost, *scenario.extra_args]

    result = _run(script, argv, root)
    assert result.returncode != 0, (
        f"{verb_name}: expected the refusal scenario to exit non-zero.\n"
        f"argv={argv}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    fields = _extract_dual_channel(result.stderr)
    assert fields is not None, (
        f"{verb_name}: stderr does not carry all three dual-channel fields "
        f"(error:/message:/remedy:):\n{result.stderr}"
    )
    assert scenario.error_key in fields["error"], (
        f"{verb_name}: expected error key containing {scenario.error_key!r}, "
        f"got {fields['error']!r}"
    )
    assert not _PLACEHOLDER_RE.search(fields["remedy"]), (
        f"{verb_name}: remedy field carries an angle-bracket placeholder: "
        f"{fields['remedy']!r}"
    )


# ---------------------------------------------------------------------------
# Column 4 -- idempotence: repeat invocation, no second write, exit 0
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("verb_name", sorted(_ACTIVE_VERB_NAMES))
def test_idempotent_verb_produces_no_second_write_on_repeat_invocation(
    verb_name: str, tmp_path: pathlib.Path
) -> None:
    scenario = _IDEMPOTENT_SCENARIOS[verb_name]
    if isinstance(scenario, NotApplicable):
        assert scenario.reason, f"{verb_name}: NotApplicable reason must be non-empty"
        pytest.skip(f"{verb_name}: {scenario.reason}")

    verb = _MANIFEST[verb_name]
    script = _verb_script(verb)
    root = tmp_path / "kanban_root"
    root.mkdir()

    argv, _project_name = scenario.setup(root)

    first = _run(script, argv, root)
    assert first.returncode == 0, (
        f"{verb_name}: first invocation must succeed to set up the repeat.\n"
        f"argv={argv}\nstdout:\n{first.stdout}\nstderr:\n{first.stderr}"
    )
    after_first = _hash_tree(root, scenario.volatile)

    second = _run(script, argv, root)
    assert second.returncode == 0, (
        f"{verb_name}: repeat invocation with identical arguments must "
        f"exit 0 (manifest declares this verb idempotent).\nargv={argv}\n"
        f"stdout:\n{second.stdout}\nstderr:\n{second.stderr}"
    )
    after_second = _hash_tree(root, scenario.volatile)

    assert after_second == after_first, (
        f"{verb_name}: repeat invocation produced a second write -- on-disk "
        "state differs between the two runs (manifest declares this verb "
        f"idempotent). Diff keys: "
        f"{set(after_first) ^ set(after_second) | {k for k in after_first if after_first.get(k) != after_second.get(k)}}"
    )


# ---------------------------------------------------------------------------
# Column 5 -- REST parity: CLI and HTTP agree on the shared machine error key
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("verb_name", sorted(_ACTIVE_VERB_NAMES))
def test_rest_twin_reproduces_the_cli_machine_error_key(
    verb_name: str, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scenario = _REST_PARITY_SCENARIOS[verb_name]
    if isinstance(scenario, NotApplicable):
        assert scenario.reason, f"{verb_name}: NotApplicable reason must be non-empty"
        pytest.skip(f"{verb_name}: {scenario.reason}")

    verb = _MANIFEST[verb_name]
    assert verb.rest_method != "ABSENT", (
        f"{verb_name}: has a RestParityCase but the manifest declares its "
        "REST columns ABSENT -- scenario/manifest disagreement."
    )
    script = _verb_script(verb)

    ghost = "definitely-unregistered-project-for-matrix"
    real = "registered-project-for-matrix"

    cli_root = tmp_path / "cli_root"
    cli_root.mkdir()
    _register_project(cli_root, real)
    if scenario.build_cli_args is not None:
        cli_argv = scenario.build_cli_args(ghost)
    else:
        cli_argv = ["--project", ghost, *scenario.extra_args]
    cli = _run(script, cli_argv, cli_root)
    assert cli.returncode != 0, (
        f"{verb_name}: CLI refusal scenario must exit non-zero for the "
        f"REST-parity comparison to mean anything.\nstderr:\n{cli.stderr}"
    )
    cli_fields = _extract_dual_channel(cli.stderr)
    assert cli_fields is not None, (
        f"{verb_name}: CLI side of the REST-parity comparison did not "
        f"produce dual-channel fields:\n{cli.stderr}"
    )

    http_root = tmp_path / "http_root"
    http_root.mkdir()
    _register_project(http_root, real)

    rest_fields = (
        scenario.rest_fields_builder(ghost)
        if scenario.rest_fields_builder is not None
        else scenario.rest_fields
    )
    with _make_client(
        http_root, monkeypatch, in_process=verb.impl.startswith("module:")
    ) as client:
        if scenario.method == "GET":
            params = {"project": ghost, **rest_fields}
            resp = client.get(scenario.route, params=params)
        else:
            body = {"project": ghost, **rest_fields}
            resp = client.post(scenario.route, json=body)

    body_json = resp.json()
    http_stderr = body_json.get("stderr", "")
    assert scenario.error_key in http_stderr, (
        f"{verb_name}: expected REST response stderr to carry the same "
        f"machine error key ({scenario.error_key!r}) the CLI produced.\n"
        f"HTTP status={resp.status_code} body={body_json!r}\n"
        f"CLI stderr:\n{cli.stderr}"
    )
    assert cli_fields["error"] in http_stderr, (
        f"{verb_name}: CLI error key {cli_fields['error']!r} not found in "
        f"REST stderr:\n{http_stderr!r}"
    )
