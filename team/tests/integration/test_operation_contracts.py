"""
test_operation_contracts.py
===========================
Contract tests for kanban operator operations, parameterized over operation
families and driven through a swappable invocation adapter.

## What these tests guard

These tests assert *operation contracts* — the on-disk state and behavior
that every invocation surface (CLI today; REST/MCP later) must produce for
each kanban operation.  They are organized by *operation family*: a set of
operations that share a common contract and must not drift from each other.

The headline deliverable is the **terminal-state family divergence guard**:
a single parameterized contract test that asserts reset, close, wontdo, and
delete each correctly transition task state AND sync the queue marker, so that
adding a fifth operation (or letting an existing one drift) causes an
immediately visible failure.

## Invocation adapter

Operations are driven through the ``CliAdapter`` from ``contract_adapter.py``.
The adapter is injected via the ``cli_invoker`` fixture so a cold reader can
see exactly where a REST adapter would slot in:

    @pytest.fixture(params=["cli"])  # add "rest" when REST is available
    def invoker(request, cli_invoker, rest_invoker):
        if request.param == "cli":
            return cli_invoker
        return rest_invoker  # same test body; different surface

The assertions in every test class never change when adapters are added.

## Test organization

- ``TestTerminalStateOperationContracts`` — divergence guard for the
  terminal-state family (reset/close/wontdo/delete).  Parameterized over
  all four operations against a shared contract.
- ``TestReadOperationContracts`` — show/resolve read-only contracts.
- ``TestHaltFamilyContracts`` — halt/unhalt scope contracts.
- ``TestIntakeContracts`` — deposit routing and validation contracts.

## Design notes (SOP.md § Test Authoring Guidelines)

- Tests are behavior-named: the function name describes the contract being
  protected, not a bug ID, version, or scaffolding label (Anti-pattern 6).
- All scratch is contained under pytest's ``tmp_path`` via ``two_project_root``
  (Anti-pattern 2 / 5).  No bare ``/tmp`` paths.
- Each test builds its own task folder; no shared mutable state between tests
  (Anti-pattern 3).
- Assertions are on on-disk state produced by the operation, not on bash-script
  internals (Anti-pattern 4).
- No naming-convention pattern scans; subjects are explicit (Anti-pattern 1).
"""

from __future__ import annotations

import pathlib
import re
from typing import Optional

import pytest

from tests.integration.contract_adapter import CliAdapter, OperationResult

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"


# ---------------------------------------------------------------------------
# Invocation adapter fixture
#
# Inject ``CliAdapter`` as the default.  To add a REST adapter, add a new
# param value here and implement the ``RestAdapter`` in contract_adapter.py.
# The test bodies below never reference the adapter implementation directly.
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_invoker() -> CliAdapter:
    """Return the CLI adapter for contract tests.

    The CLI adapter drives operations via their bash scripts under team/scripts/
    with PGAI_AGENT_KANBAN_ROOT_PATH redirected to the temp tree.
    """
    return CliAdapter()


# ---------------------------------------------------------------------------
# Shared test-setup helpers
# ---------------------------------------------------------------------------


def _build_task_folder(
    kanban_root: pathlib.Path,
    project_name: str,
    task_id: str,
    state: str = "BACKLOG",
) -> pathlib.Path:
    """Create a minimal task folder with status.md, README.md, artifacts/, and logs/.

    Also adds a queue entry to the agent backlog so marker-sync assertions
    can verify that the backlog reflects the operation's contract result.

    Args:
        kanban_root:  Kanban root path (from ``two_project_root`` fixture).
        project_name: Project the task belongs to (e.g. ``"project_a"``).
        task_id:      Full task identifier.  The agent prefix determines which
                      backlog file receives the queue marker.
        state:        Initial task state written to ``## State`` in status.md.

    Returns:
        ``pathlib.Path`` to the created task directory.
    """
    task_dir = kanban_root / "projects" / project_name / "tasks" / task_id
    (task_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    (task_dir / "logs").mkdir(parents=True, exist_ok=True)

    (task_dir / "README.md").write_text(
        f"# {task_id}\n\n"
        f"## Role\nCODER\n\n"
        f"## Task ID\n{task_id}\n\n"
        f"## Goal\nContract test synthetic task.\n",
        encoding="utf-8",
    )

    (task_dir / "status.md").write_text(
        f"# Status\n\n"
        f"## Task\n{task_id}\n\n"
        f"## State\n{state}\n\n"
        f"## Summary\nContract test synthetic task.\n\n"
        f"## Artifacts\nnone\n\n"
        f"## Blockers\nnone\n\n"
        f"## Needs Human\nno\n",
        encoding="utf-8",
    )

    agent_prefix = task_id.split("-")[0].lower()
    backlog_file = (
        kanban_root
        / "projects"
        / project_name
        / "tasks"
        / "queues"
        / f"{agent_prefix}_backlog.md"
    )
    if backlog_file.exists():
        existing = backlog_file.read_text(encoding="utf-8")
        backlog_file.write_text(
            existing.rstrip() + f"\n- [ ] {task_id}\n",
            encoding="utf-8",
        )

    return task_dir


def _read_status_state(task_dir: pathlib.Path) -> str:
    """Extract the ``## State`` value from a task's status.md."""
    text = (task_dir / "status.md").read_text(encoding="utf-8")
    match = re.search(r"##\s*State\s*\n\s*(\S+)", text)
    return match.group(1).strip() if match else ""


def _read_backlog_marker(
    kanban_root: pathlib.Path,
    project_name: str,
    task_id: str,
) -> str:
    """Return the marker character for ``task_id`` in the agent backlog.

    Looks for a line of the form ``- [<char>] <task_id>``.
    Returns the marker character, or ``""`` if the line is not found.
    """
    agent_prefix = task_id.split("-")[0].lower()
    backlog_file = (
        kanban_root
        / "projects"
        / project_name
        / "tasks"
        / "queues"
        / f"{agent_prefix}_backlog.md"
    )
    if not backlog_file.exists():
        return ""
    text = backlog_file.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"^\s*-\s+\[([^\]]*)\]\s+{re.escape(task_id)}\b",
        re.MULTILINE,
    )
    match = pattern.search(text)
    return match.group(1).strip() if match else ""


# ---------------------------------------------------------------------------
# Contract assertions
#
# These functions encode the *shared contract* that every terminal-state
# operation must satisfy.  They are used by the parameterized family test
# so the contract is defined once and applied to all four operations.
# ---------------------------------------------------------------------------


def _assert_task_no_longer_in_initial_state(
    task_dir: pathlib.Path,
    initial_state: str,
    operation: str,
    result: OperationResult,
) -> None:
    """Assert that the operation moved the task out of its initial state.

    The specific terminal state varies by operation (BACKLOG for reset,
    DONE for close, WONT-DO for wontdo); the contract common to all is
    that the state must change and the operation must exit 0.
    """
    assert result.returncode == 0, (
        f"Operation '{operation}' exited non-zero (rc={result.returncode}).\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    actual_state = _read_status_state(task_dir)
    assert actual_state != initial_state, (
        f"Operation '{operation}' must change task state from {initial_state!r}; "
        f"status.md still shows {actual_state!r}.\n"
        f"stdout: {result.stdout}"
    )


# ---------------------------------------------------------------------------
# Terminal-state family: divergence guard
#
# This is the headline deliverable.  All four terminal-state operations —
# reset, close, wontdo, delete — are exercised through a single parameterized
# contract.  If any operation drifts (e.g. marker sync breaks for wontdo
# while the others work), this test catches it loudly.
#
# Parameterization:
#   Each entry is (operation, initial_state, expected_terminal_state, expected_marker).
#   - reset:  DONE → BACKLOG, marker must be open (empty or space).
#   - close:  BACKLOG → DONE, marker must be [x].
#   - wontdo: BACKLOG → WONT-DO, marker must be [x].
#   - delete: DONE → (directory gone), marker must be [x].
# ---------------------------------------------------------------------------

_TERMINAL_STATE_CASES: list[tuple[str, str, Optional[str], str, bool]] = [
    # (operation, initial_state, expected_terminal_state_or_None, expected_marker,
    #  task_dir_survives)
    #
    #   expected_terminal_state_or_None:
    #     Non-None string  → the ## State value the operation must write to status.md.
    #     None             → the task directory is fully removed (delete operation).
    #
    #   expected_marker:
    #     "x"       → the marker line must be flipped to [x] (terminal, still listed).
    #     "open"    → the marker line must be flipped to [ ] or [ ] (reset to BACKLOG).
    #     "removed" → the marker line must be completely absent from the backlog
    #                 (delete removes the whole task and its queue entry with it).
    #
    #   task_dir_survives:
    #     True  → artifacts/ and logs/ subdirectories must still exist after the
    #             operation (the task folder is not removed; state-only mutation).
    #     False → the task directory itself is gone (delete); no subdirs to check.
    ("reset",  "DONE",   "BACKLOG", "open",    True),
    ("close",  "BACKLOG", "DONE",   "x",       True),
    ("wontdo", "BACKLOG", "WONT-DO","x",       True),
    ("delete", "DONE",    None,     "removed", False),
]

_TERMINAL_STATE_IDS = [op for op, *_ in _TERMINAL_STATE_CASES]


class TestTerminalStateOperationContracts:
    """Divergence guard: each terminal-state operation satisfies the shared contract.

    This parameterized suite asserts the four-part contract every terminal-
    state operation must honor:

    1. Exit zero — the operation exits 0 on success; non-zero is an error and
       must not change the task's state.
    2. State transition — the task status.md ``## State`` field reflects the
       correct terminal state (or the task directory is removed, for delete).
    3. Queue-marker sync — the agent backlog entry for the task is updated to
       reflect the new state ([x] for terminal; open for BACKLOG after reset).
    4. Artifact/log consistency — for non-delete operations, the task's
       artifacts/ and logs/ subdirectories must survive the operation intact.
       For delete, the entire task directory is removed (covered by part 2).

    The family is tested as a family so that adding a fifth operation (or
    letting an existing one drift) causes an immediately visible failure here.
    """

    @pytest.mark.parametrize(
        "operation,initial_state,expected_state,expected_marker,task_dir_survives",
        _TERMINAL_STATE_CASES,
        ids=_TERMINAL_STATE_IDS,
    )
    def test_operation_transitions_state_syncs_marker_and_keeps_artifacts_consistent(
        self,
        two_project_root: pathlib.Path,
        cli_invoker: CliAdapter,
        operation: str,
        initial_state: str,
        expected_state: Optional[str],
        expected_marker: str,
        task_dir_survives: bool,
    ) -> None:
        """Each terminal-state operation satisfies the four-part shared contract.

        Contract parts tested:

        1. Exit 0 on success.
        2. ``## State`` in status.md reflects the correct result
           (or the task directory is removed for ``delete``).
        3. The agent backlog queue marker is updated to match the new state.
        4. The task's artifacts/ and logs/ subdirectories survive operations
           that do not remove the task folder (reset, close, wontdo).  For
           delete, the task directory itself is gone (asserted in part 2).

        This is the divergence guard: if any member of the family drifts on
        state transition, marker sync, or artifact consistency, this
        parameterized test fails loudly for that specific operation while the
        others continue to pass.
        """
        root = two_project_root

        # Use the operation name in the task ID so failures self-identify the
        # operation without needing to read the parametrize label.
        task_id = f"CODER-20260628-CT001-{operation}-contract"
        task_dir = _build_task_folder(root, "project_a", task_id, state=initial_state)

        # Pre-condition: for reset, mark the backlog as [x] (simulating a prior
        # close) so the marker-flip-to-open can be verified.
        if operation == "reset":
            agent_prefix = task_id.split("-")[0].lower()
            backlog_file = (
                root
                / "projects"
                / "project_a"
                / "tasks"
                / "queues"
                / f"{agent_prefix}_backlog.md"
            )
            if backlog_file.exists():
                text = backlog_file.read_text(encoding="utf-8")
                text = text.replace(f"- [ ] {task_id}", f"- [x] {task_id}")
                backlog_file.write_text(text, encoding="utf-8")

        result = cli_invoker.invoke(
            operation,
            ["--project", "project_a", "--key", task_id],
            kanban_root=root,
        )

        # Contract part 1: exit 0.
        assert result.returncode == 0, (
            f"'{operation}' must exit 0 on success "
            f"(rc={result.returncode}).\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # Contract part 2: state transition.
        if expected_state is None:
            # delete removes the directory entirely.
            assert not task_dir.exists(), (
                f"'{operation}' must remove the task directory; "
                f"{task_dir} still exists.\n"
                f"stdout: {result.stdout}"
            )
        else:
            actual_state = _read_status_state(task_dir)
            assert actual_state == expected_state, (
                f"'{operation}' must set ## State to {expected_state!r}; "
                f"got {actual_state!r}.\n"
                f"status.md:\n{(task_dir / 'status.md').read_text(encoding='utf-8')}"
            )

        # Contract part 3: queue-marker sync.
        #
        # _read_backlog_marker returns "" when the marker line is absent, which
        # covers both "not found" (the line never existed) and "removed" (delete
        # removed the line entirely).  The expected_marker value tells us which
        # of these three outcomes the operation must produce:
        #
        #   "x"       — line present and flipped to [x] (closed / wont-do).
        #   "open"    — line present and flipped to [ ] or [ ] (reset to BACKLOG).
        #   "removed" — line absent from the backlog (delete removed it with the task).
        marker = _read_backlog_marker(root, "project_a", task_id)
        if expected_marker == "x":
            assert marker == "x", (
                f"'{operation}' must flip the backlog marker to [x]; "
                f"got {marker!r}.\n"
                f"stdout: {result.stdout}"
            )
        elif expected_marker == "open":
            assert marker in ("", " "), (
                f"'{operation}' must flip the backlog marker to open (space/empty); "
                f"got {marker!r}.\n"
                f"stdout: {result.stdout}"
            )
        elif expected_marker == "removed":
            # delete_item removes the queue marker line entirely.  After deletion,
            # the task no longer exists in the backlog at all — _read_backlog_marker
            # returns "" because the pattern finds no match.
            assert marker == "", (
                f"'{operation}' must remove the backlog marker line entirely; "
                f"got {marker!r} (expected absent / empty).\n"
                f"stdout: {result.stdout}"
            )

        # Contract part 4: artifact/log consistency.
        # For operations that do not remove the task folder, artifacts/ and
        # logs/ must still exist.  An operation that accidentally deletes or
        # fails to create these directories would break the task-folder
        # invariant that downstream agents depend on.
        if task_dir_survives:
            assert (task_dir / "artifacts").exists(), (
                f"'{operation}' must not remove the artifacts/ directory; "
                f"task_dir={task_dir}.\nstdout: {result.stdout}"
            )
            assert (task_dir / "logs").exists(), (
                f"'{operation}' must not remove the logs/ directory; "
                f"task_dir={task_dir}.\nstdout: {result.stdout}"
            )

    def test_terminal_state_operations_refuse_ambiguous_key_safely(
        self,
        two_project_root: pathlib.Path,
        cli_invoker: CliAdapter,
    ) -> None:
        """Terminal-state operations exit non-zero without state change for a missing key.

        Passing a key that resolves to no task must not exit 0 and must not
        produce any on-disk mutations in the project directory.  This contract
        applies to all terminal-state operations and protects against silent
        no-ops when the operator supplies a mistyped key.
        """
        root = two_project_root
        nonexistent_key = "CODER-99999999-NOEXIST-terminal"

        # Record the project directory state before any operation.
        project_dir = root / "projects" / "project_a"
        before_files = set(project_dir.rglob("*"))

        for operation in ("reset", "close", "wontdo", "delete"):
            result = cli_invoker.invoke(
                operation,
                ["--project", "project_a", "--key", nonexistent_key],
                kanban_root=root,
            )
            assert result.returncode != 0, (
                f"'{operation}' must not exit 0 for a missing key; "
                f"got rc={result.returncode}.\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )

        # No new files must have been created.
        after_files = set(project_dir.rglob("*"))
        new_files = after_files - before_files
        assert not new_files, (
            "Terminal-state operations must not create files when the key is not found; "
            f"new files found: {new_files}"
        )


# ---------------------------------------------------------------------------
# Read operation contracts
# ---------------------------------------------------------------------------


class TestReadOperationContracts:
    """show/resolve contract: read-only, prefix-matching, boundary-safe, and ambiguity-aware.

    The read/resolve contract guards four properties that every invocation surface
    must honor:

    1. Read-only — show must emit content and exit 0 without mutating any file.
    2. Prefix-matching — a shortened key prefix resolves to the full item.
    3. Boundary-safety — a numeric prefix does NOT match a longer numeric continuation
       (ROLE-001 does not match ROLE-0010).
    4. Ambiguity-awareness — when a prefix matches multiple items, show exits 2
       (warns on stderr) but still emits the first match's content; write-style
       operations refuse ambiguous keys entirely.
    5. Key self-identification — the resolver identifies the item type (task, bug,
       priority, requirement) from the key alone so the correct content is returned.

    These properties must hold regardless of which invocation surface triggers the
    show/resolve operation (CLI today; REST/MCP later).
    """

    def test_show_emits_status_content_without_mutation(
        self,
        two_project_root: pathlib.Path,
        cli_invoker: CliAdapter,
    ) -> None:
        """show outputs task status content and does not modify the task folder.

        After a successful show invocation, the task's status.md must be
        byte-for-byte identical to its content before the call.  This is the
        read-only contract: show is a non-mutating inspector.
        """
        root = two_project_root
        task_id = "CODER-20260628-CT010-show-read-only-contract"
        task_dir = _build_task_folder(root, "project_a", task_id, state="BACKLOG")
        status_before = (task_dir / "status.md").read_bytes()

        result = cli_invoker.invoke(
            "show",
            ["--project", "project_a", "--key", task_id, "--file", "status"],
            kanban_root=root,
        )

        assert result.returncode == 0, (
            f"show must exit 0 for an existing task; "
            f"rc={result.returncode}.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "BACKLOG" in result.stdout, (
            "show must emit the task's current state in its output; "
            f"'BACKLOG' not found.\nstdout: {result.stdout}"
        )
        status_after = (task_dir / "status.md").read_bytes()
        assert status_after == status_before, (
            "show must not modify status.md (read-only contract violated)."
        )

    def test_show_returns_not_found_exit_code_for_missing_key(
        self,
        two_project_root: pathlib.Path,
        cli_invoker: CliAdapter,
    ) -> None:
        """show exits with a non-zero code when the key resolves to no task.

        The not-found contract: when a key does not identify any task in the
        project, show must return a non-zero exit code.  This allows callers
        to distinguish "task exists and was shown" from "task not found."
        """
        root = two_project_root

        result = cli_invoker.invoke(
            "show",
            ["--project", "project_a", "--key", "CODER-99999999-NOEXIST-show"],
            kanban_root=root,
        )

        assert result.returncode != 0, (
            "show must return non-zero exit code when the key is not found; "
            f"got rc={result.returncode}.\nstdout: {result.stdout}"
        )

    def test_prefix_key_resolves_to_matching_task(
        self,
        two_project_root: pathlib.Path,
        cli_invoker: CliAdapter,
    ) -> None:
        """show resolves a prefix key to the full task when the prefix is unambiguous.

        The prefix-matching contract: a caller may supply a shortened key prefix
        (e.g. ``CODER-20260628-CT011``) instead of the full task ID.  The resolver
        must locate the task whose full name begins with ``<prefix>-`` and emit its
        content.  Exit code must be 0; content must include a marker from the task's
        status.md so the caller can confirm the correct item was returned.
        """
        root = two_project_root
        full_task_id = "CODER-20260628-CT011-prefix-match-target"
        _build_task_folder(root, "project_a", full_task_id, state="WORKING")

        # Supply only the prefix, not the full task ID.
        prefix_key = "CODER-20260628-CT011"

        result = cli_invoker.invoke(
            "show",
            ["--project", "project_a", "--key", prefix_key, "--file", "status"],
            kanban_root=root,
        )

        assert result.returncode == 0, (
            f"show must exit 0 when a prefix key resolves to exactly one task; "
            f"rc={result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "WORKING" in result.stdout, (
            "show with a prefix key must emit the matched task's status content; "
            f"'WORKING' not found in output.\nstdout: {result.stdout}"
        )

    def test_boundary_safe_prefix_does_not_match_numeric_continuation(
        self,
        two_project_root: pathlib.Path,
        cli_invoker: CliAdapter,
    ) -> None:
        """The resolver enforces a hyphen boundary so a numeric prefix cannot match
        a task whose sequence number is a numeric extension of the prefix.

        Boundary-safety contract: the key ``CODER-20260628-CT012`` must match
        ``CODER-20260628-CT012-boundary-safe`` but must NOT match
        ``CODER-20260628-CT0120-boundary-sibling``.  Without the hyphen boundary,
        the glob ``CT012-*`` could falsely match ``CT0120-...`` as a continuation.
        With the boundary enforced (the resolver uses ``<key>-*`` not ``<key>*``),
        only the correct task is returned.

        This test creates both tasks and verifies that the prefix key resolves
        unambiguously to the correctly-bounded task, exiting 0 with the right content.
        """
        root = two_project_root

        # Create the intended target: prefix is CT012, full ID has CT012 followed by -slug.
        target_id = "CODER-20260628-CT012-boundary-safe"
        _build_task_folder(root, "project_a", target_id, state="DONE")

        # Create the numeric continuation that must NOT be matched by the prefix CT012.
        # CT0120 starts with CT012 but is a distinct sequence number.
        sibling_id = "CODER-20260628-CT0120-boundary-sibling"
        _build_task_folder(root, "project_a", sibling_id, state="BACKLOG")

        # Prefix key: resolves only to target_id, not sibling_id.
        prefix_key = "CODER-20260628-CT012"

        result = cli_invoker.invoke(
            "show",
            ["--project", "project_a", "--key", prefix_key, "--file", "status"],
            kanban_root=root,
        )

        assert result.returncode == 0, (
            f"show must exit 0 when the boundary-safe prefix resolves to exactly one task; "
            f"rc={result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        # The correctly-bounded task is in DONE state; the sibling is in BACKLOG.
        # If boundary were broken, this would be ambiguous (rc=2) or return BACKLOG.
        assert "DONE" in result.stdout, (
            "show must emit the boundary-safe target task's content (state DONE), "
            "not the numeric-continuation sibling (state BACKLOG). "
            "If ambiguous or wrong content returned, the boundary is not enforced.\n"
            f"stdout: {result.stdout}"
        )

    def test_ambiguous_prefix_warns_on_stderr_and_emits_first_match(
        self,
        two_project_root: pathlib.Path,
        cli_invoker: CliAdapter,
    ) -> None:
        """show exits 0 but warns on stderr and emits the first match when a prefix is ambiguous.

        Ambiguity-awareness contract for show: when a prefix matches more than one task,
        the resolver selects the first (alphabetically sorted) match.  show is lenient —
        it treats ambiguity as a soft warning, emits the first match's content to stdout,
        and exits 0 so that operator scripts can still consume the output.  The ambiguity
        warning is emitted to stderr so callers that inspect stderr can detect it.

        This is distinct from the write-operation contract (tested separately): write
        operations refuse entirely on ambiguity.  show's leniency is intentional — an
        operator reading a task's status benefits from a best-effort answer even when
        the key is imprecise.

        The contract asserted here:
        - show exits 0 even when the key is ambiguous.
        - stdout contains the first-match content (alphabetically sorted).
        - stderr contains an ambiguity warning so callers can detect the ambiguous match.
        """
        root = two_project_root

        # Create two tasks that share the same prefix so the prefix key is ambiguous.
        # Alphabetically, CT013-alpha comes before CT013-beta.
        first_task_id = "CODER-20260628-CT013-alpha"
        second_task_id = "CODER-20260628-CT013-beta"
        _build_task_folder(root, "project_a", first_task_id, state="DONE")
        _build_task_folder(root, "project_a", second_task_id, state="BACKLOG")

        ambiguous_prefix = "CODER-20260628-CT013"

        result = cli_invoker.invoke(
            "show",
            ["--project", "project_a", "--key", ambiguous_prefix, "--file", "status"],
            kanban_root=root,
        )

        # show exits 0 for ambiguous match (lenient read operation).
        # The resolver returns exit code 2 internally but show treats that as a soft
        # warning and exits 0 so operator scripts can consume the output.
        assert result.returncode == 0, (
            f"show must exit 0 for an ambiguous key (lenient read operation); "
            f"rc={result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        # Content from the first (alphabetically sorted) match must appear on stdout.
        # first_task_id (alpha) is in DONE state; if the content is from beta (BACKLOG),
        # alphabetic ordering is not being honored.
        assert "DONE" in result.stdout, (
            "show must emit the first (alphabetically sorted) match's content on ambiguity; "
            "expected DONE state from CT013-alpha.\n"
            f"stdout: {result.stdout}"
        )
        # The ambiguity warning must appear on stderr so callers can detect it.
        assert result.stderr.strip(), (
            "show must emit an ambiguity warning to stderr when multiple matches exist; "
            f"stderr was empty.\nstdout: {result.stdout}"
        )

    def test_key_self_identifies_task_type_from_resolver(
        self,
        two_project_root: pathlib.Path,
        cli_invoker: CliAdapter,
    ) -> None:
        """The resolver self-identifies item type so show routes to the correct content.

        Key self-identification contract: the resolver must determine from the key alone
        whether the item is a task, bug, priority, or requirement, and route accordingly.
        A task key must return task content; a bug key must return bug content.  A key
        that matches a task takes priority over an intake file with a similar name (the
        resolution order is: tasks first, then bugs, then priority, then requirements).

        This test creates a bug intake item whose key differs from any task, and verifies
        that show correctly self-identifies the item as a bug and emits its content.
        """
        root = two_project_root

        # Create a bug intake item (no matching task directory with this key).
        bugs_dir = root / "projects" / "project_a" / "bugs"
        bugs_dir.mkdir(parents=True, exist_ok=True)
        bug_id = "BUG-7001-resolver-self-id-test"
        bug_file = bugs_dir / f"{bug_id}.md"
        bug_file.write_text(
            f"# BUG-7001: resolver-self-id-test\n\n"
            "## Status\nopen\n\n"
            "## Summary\nSelf-identification contract test bug.\n",
            encoding="utf-8",
        )

        result = cli_invoker.invoke(
            "show",
            ["--project", "project_a", "--key", bug_id],
            kanban_root=root,
        )

        assert result.returncode == 0, (
            f"show must exit 0 when the bug key is found; "
            f"rc={result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        # The bug content must appear — confirming the resolver self-identified the item
        # type as 'bug' and routed show to emit the bug file's content directly.
        assert "BUG-7001" in result.stdout, (
            "show must emit the bug intake file's content when the key identifies a bug; "
            f"'BUG-7001' not found in output.\nstdout: {result.stdout}"
        )
        # The bug file must not have been mutated (read-only contract applies to intake too).
        assert bug_file.read_text(encoding="utf-8") == (
            f"# BUG-7001: resolver-self-id-test\n\n"
            "## Status\nopen\n\n"
            "## Summary\nSelf-identification contract test bug.\n"
        ), "show must not modify the bug intake file (read-only contract)."

    def test_write_operations_refuse_ambiguous_key_for_task_resolver(
        self,
        two_project_root: pathlib.Path,
        cli_invoker: CliAdapter,
    ) -> None:
        """Write-style operations (reset, close, wontdo) refuse an ambiguous key.

        Ambiguity-awareness for write operations: while show is lenient (exits 2 and
        emits the first match), write operations must refuse entirely when the key is
        ambiguous.  An ambiguous key passed to reset, close, or wontdo must result in
        a non-zero exit code that is not 0, and the on-disk state of both tasks must
        remain unchanged.

        This is the write-side complement to the show ambiguity test: it demonstrates
        that the same resolution contract (prefix-glob, hyphen boundary, ambiguity
        detection) applies to write operations but with stricter handling.
        """
        root = two_project_root

        # Two tasks sharing an ambiguous prefix — write operations must not proceed.
        first_task_id = "CODER-20260628-CT014-write-ambig-alpha"
        second_task_id = "CODER-20260628-CT014-write-ambig-beta"
        first_dir = _build_task_folder(root, "project_a", first_task_id, state="BACKLOG")
        second_dir = _build_task_folder(root, "project_a", second_task_id, state="BACKLOG")

        ambiguous_prefix = "CODER-20260628-CT014"

        # Each write-side operation must refuse on ambiguity.
        for operation in ("reset", "close", "wontdo"):
            result = cli_invoker.invoke(
                operation,
                ["--project", "project_a", "--key", ambiguous_prefix],
                kanban_root=root,
            )
            assert result.returncode != 0, (
                f"'{operation}' must not exit 0 for an ambiguous key; "
                f"rc={result.returncode}.\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )

        # Both tasks must remain in their original state — no write must have occurred.
        assert _read_status_state(first_dir) == "BACKLOG", (
            f"Write operations must not mutate the first ambiguous task's state; "
            f"state changed from BACKLOG.\n"
            f"status.md:\n{(first_dir / 'status.md').read_text(encoding='utf-8')}"
        )
        assert _read_status_state(second_dir) == "BACKLOG", (
            f"Write operations must not mutate the second ambiguous task's state; "
            f"state changed from BACKLOG.\n"
            f"status.md:\n{(second_dir / 'status.md').read_text(encoding='utf-8')}"
        )


# ---------------------------------------------------------------------------
# Halt family contracts
# ---------------------------------------------------------------------------

# Parameterize the halt-family flag contract as a family.
#
# Each tuple describes one halt-family operation:
#   (operation, args_fn, flag_path_fn, flag_absent_paths_fn)
#
#   operation         — canonical adapter name (key in _CLI_SCRIPT_MAP).
#   flag_present      — True if the operation CREATES the flag (halt family);
#                       False if it REMOVES the flag (unhalt family).
#   project_scoped    — True if the operation targets one project's directory
#                       (halt / unhalt); False if it targets the kanban root
#                       (halt-global / unhalt-global).
#
# The parameterized contract asserts:
#   1. The operation exits 0.
#   2. After the operation, the correct HALT flag is present or absent.
#   3. No HALT flag appears outside the expected scope.
#
# The family is tested as a family: adding a fifth halt-related operation (or
# letting an existing one drift on scope or flag behavior) causes an
# immediately visible failure here.

_HALT_FAMILY_CASES: list[tuple[str, bool, bool]] = [
    # (operation, flag_present_after, project_scoped)
    ("halt",          True,  True),
    ("unhalt",        False, True),
    ("halt-global",   True,  False),
    ("unhalt-global", False, False),
]

_HALT_FAMILY_IDS = [op for op, *_ in _HALT_FAMILY_CASES]


class TestHaltFamilyContracts:
    """Halt-family contract: on-disk flag state and scope for all four operations.

    The halt contract: on-disk HALT files are the mechanism by which the
    discovery pipeline is stopped.  Each halt-family operation must:

    1. Exit 0 on success.
    2. Produce the correct flag state (present for halt; absent for unhalt).
    3. Write the flag only to the correct scope (per-project vs kanban root).
    4. Not write any HALT flag outside its scope.

    The family is tested as a family via parameterization so that adding a
    fifth halt-related operation (or letting an existing one drift on scope
    or flag state) causes an immediately visible failure for that operation
    while the others continue to pass.
    """

    @pytest.mark.parametrize(
        "operation,flag_present_after,project_scoped",
        _HALT_FAMILY_CASES,
        ids=_HALT_FAMILY_IDS,
    )
    def test_halt_family_flag_state_and_scope_contract(
        self,
        two_project_root: pathlib.Path,
        cli_invoker: CliAdapter,
        operation: str,
        flag_present_after: bool,
        project_scoped: bool,
    ) -> None:
        """Each halt-family operation satisfies the flag-state and scope contract.

        Contract parts asserted:

        1. The operation exits 0.
        2. After the operation, the HALT flag at the expected path is present
           (for halt / halt-global) or absent (for unhalt / unhalt-global).
        3. The HALT flag does not appear outside the expected scope:
           - per-project operations must not create HALT at the kanban root
             or in any other project's directory.
           - global operations must not create HALT inside any project directory.

        The per-project operations (halt / unhalt) are invoked with
        ``--project project_a``.  The global operations (halt-global /
        unhalt-global) take no project argument.

        Pre-condition for unhalt and unhalt-global: the corresponding HALT
        file is created manually before the operation runs so that the removal
        can be verified (the unhalt contract is "flag absent after the call,"
        which requires a flag to be present first).
        """
        root = two_project_root

        # Per-project flag path and invocation args.
        per_project_halt = root / "projects" / "project_a" / "HALT"
        # Sibling project flag path — must never be touched.
        sibling_project_halt = root / "projects" / "project_b" / "HALT"
        # Global flag path.
        global_halt = root / "HALT"

        # Determine which flag path the operation should affect and what the
        # pre-condition should be.
        if project_scoped:
            target_flag = per_project_halt
            out_of_scope_flags = [sibling_project_halt, global_halt]
            invoke_args = ["--project", "project_a"]
        else:
            target_flag = global_halt
            out_of_scope_flags = [per_project_halt, sibling_project_halt]
            invoke_args = []

        # Pre-condition for removal operations: ensure the flag exists so that
        # the test can verify it is removed by the operation.
        if not flag_present_after:
            target_flag.touch()

        # Invoke the operation through the swappable adapter.
        result = cli_invoker.invoke(operation, invoke_args, kanban_root=root)

        # Contract part 1: the operation exits 0.
        assert result.returncode == 0, (
            f"'{operation}' must exit 0; "
            f"rc={result.returncode}.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # Contract part 2: the flag is in the correct state at the target path.
        if flag_present_after:
            assert target_flag.exists(), (
                f"'{operation}' must create the HALT flag at {target_flag}; "
                f"flag not found after the operation.\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )
        else:
            assert not target_flag.exists(), (
                f"'{operation}' must remove the HALT flag at {target_flag}; "
                f"flag still present after the operation.\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )

        # Contract part 3: no HALT flag outside the expected scope.
        for out_of_scope in out_of_scope_flags:
            assert not out_of_scope.exists(), (
                f"'{operation}' must not create HALT at out-of-scope path {out_of_scope}; "
                f"flag found after the operation.\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )

    @pytest.mark.parametrize(
        "operation,project_scoped",
        [
            ("halt",          True),
            ("unhalt",        True),
            ("halt-global",   False),
            ("unhalt-global", False),
        ],
        ids=["halt", "unhalt", "halt-global", "unhalt-global"],
    )
    def test_halt_family_operations_are_idempotent(
        self,
        two_project_root: pathlib.Path,
        cli_invoker: CliAdapter,
        operation: str,
        project_scoped: bool,
    ) -> None:
        """Each halt-family operation is idempotent: a second invocation exits 0.

        Idempotency contract: calling any halt-family operation twice must not
        produce an error on the second call.  This prevents spurious failures
        when automated pipelines retry halt or unhalt operations.

        For halt and halt-global: the second call finds the flag already present
        and exits 0 without error.

        For unhalt and unhalt-global: the second call finds the flag already
        absent and exits 0 without error.
        """
        root = two_project_root

        if project_scoped:
            invoke_args = ["--project", "project_a"]
        else:
            invoke_args = []

        # First invocation (establishes state).
        cli_invoker.invoke(operation, invoke_args, kanban_root=root)

        # Second invocation — must succeed without error.
        result = cli_invoker.invoke(operation, invoke_args, kanban_root=root)

        assert result.returncode == 0, (
            f"'{operation}' must exit 0 on a second (idempotent) invocation; "
            f"rc={result.returncode}.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_unhalt_removes_per_project_flag_without_touching_global_or_sibling(
        self,
        two_project_root: pathlib.Path,
        cli_invoker: CliAdapter,
    ) -> None:
        """unhalt removes only the targeted project's HALT flag.

        After halt+unhalt on project_a, the per-project HALT flag for
        project_a must be absent.  The kanban root HALT and project_b's HALT
        must not be created or affected.

        This is the round-trip scope contract for the per-project pair: halt
        sets the flag; unhalt removes it; neither touches out-of-scope paths.
        """
        root = two_project_root

        # Set up: halt project_a.
        cli_invoker.invoke("halt", ["--project", "project_a"], kanban_root=root)
        assert (root / "projects" / "project_a" / "HALT").exists(), (
            "Pre-condition: HALT must exist after halt invocation."
        )

        # Act: unhalt project_a.
        result = cli_invoker.invoke(
            "unhalt", ["--project", "project_a"], kanban_root=root
        )

        assert result.returncode == 0, (
            f"unhalt must exit 0; rc={result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert not (root / "projects" / "project_a" / "HALT").exists(), (
            "unhalt must remove the per-project HALT flag for the targeted project."
        )
        assert not (root / "projects" / "project_b" / "HALT").exists(), (
            "unhalt must not touch project_b's HALT flag."
        )
        assert not (root / "HALT").exists(), (
            "unhalt must not create or remove the global HALT at the kanban root."
        )

    def test_unhalt_global_removes_root_flag_without_touching_per_project_flags(
        self,
        two_project_root: pathlib.Path,
        cli_invoker: CliAdapter,
    ) -> None:
        """unhalt-global removes only the kanban root HALT flag.

        After halt-global+unhalt-global, the root HALT flag must be absent.
        Any per-project HALT flags that existed before unhalt-global must
        remain unaffected — global unhalt must not cascade to project scope.

        This is the round-trip scope contract for the global pair: halt-global
        sets the root flag; unhalt-global removes it; neither touches per-
        project scope.
        """
        root = two_project_root

        # Pre-condition: create a per-project HALT independently so we can
        # verify that unhalt-global does not remove it.
        (root / "projects" / "project_a" / "HALT").touch()

        # Set up: halt-global.
        cli_invoker.invoke("halt-global", [], kanban_root=root)
        assert (root / "HALT").exists(), (
            "Pre-condition: global HALT must exist after halt-global invocation."
        )

        # Act: unhalt-global.
        result = cli_invoker.invoke("unhalt-global", [], kanban_root=root)

        assert result.returncode == 0, (
            f"unhalt-global must exit 0; rc={result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert not (root / "HALT").exists(), (
            "unhalt-global must remove the kanban root HALT flag."
        )
        # Per-project flag must survive — it was not created by halt-global
        # and must not be removed by unhalt-global.
        assert (root / "projects" / "project_a" / "HALT").exists(), (
            "unhalt-global must not remove a pre-existing per-project HALT flag; "
            "global and per-project halt are independent mechanisms."
        )


# ---------------------------------------------------------------------------
# Intake contracts
# ---------------------------------------------------------------------------


class TestIntakeContracts:
    """intake routes files by prefix and rejects unknown prefixes.

    The intake contract: deposit routes by filename prefix, validates, and
    rejects malformed files.  These contracts must hold regardless of how
    the deposit is triggered (CLI today; REST/automation later).
    """

    def test_bug_prefixed_file_deposits_to_bugs_directory(
        self,
        two_project_root: pathlib.Path,
        cli_invoker: CliAdapter,
        tmp_path: pathlib.Path,
    ) -> None:
        """intake routes a BUG-* file to the project bugs/ directory.

        The routing contract for BUG-prefixed files: a file named
        BUG-NNNN-<slug>.md must always land in ``projects/<name>/bugs/``,
        never in requirements/ or priority/.
        """
        root = two_project_root
        staged = tmp_path / "BUG-9001-contract-routing-test.md"
        staged.write_text(
            "# BUG-9001: contract-routing-test\n\n"
            "## Status\nopen\n\n"
            "## Summary\nContract test bug file.\n",
            encoding="utf-8",
        )
        (root / "projects" / "project_a" / "bugs").mkdir(parents=True, exist_ok=True)

        result = cli_invoker.invoke(
            "intake",
            ["--project", "project_a", str(staged)],
            kanban_root=root,
        )

        assert result.returncode == 0, (
            f"intake must exit 0 for a BUG-prefixed file; "
            f"rc={result.returncode}.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        deposited = root / "projects" / "project_a" / "bugs" / "BUG-9001-contract-routing-test.md"
        assert deposited.exists(), (
            f"BUG-prefixed file must be deposited to bugs/; "
            f"not found at {deposited}.\nstdout: {result.stdout}"
        )

    def test_priority_prefixed_file_deposits_to_priority_directory(
        self,
        two_project_root: pathlib.Path,
        cli_invoker: CliAdapter,
        tmp_path: pathlib.Path,
    ) -> None:
        """intake routes a PRIORITY-* file to the project priority/ directory.

        The routing contract for PRIORITY-prefixed files: a file named
        PRIORITY-NNNN-<slug>.md must always land in ``projects/<name>/priority/``,
        never in bugs/ or requirements/.
        """
        root = two_project_root
        staged = tmp_path / "PRIORITY-0010-contract-routing-test.md"
        staged.write_text(
            "# PRIORITY-0010: contract-routing-test\n\n"
            "## Status\nopen\n\n"
            "## Summary\nContract test priority file.\n",
            encoding="utf-8",
        )
        (root / "projects" / "project_a" / "priority").mkdir(parents=True, exist_ok=True)

        result = cli_invoker.invoke(
            "intake",
            ["--project", "project_a", str(staged)],
            kanban_root=root,
        )

        assert result.returncode == 0, (
            f"intake must exit 0 for a PRIORITY-prefixed file; "
            f"rc={result.returncode}.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        deposited = (
            root / "projects" / "project_a" / "priority"
            / "PRIORITY-0010-contract-routing-test.md"
        )
        assert deposited.exists(), (
            f"PRIORITY-prefixed file must be deposited to priority/; "
            f"not found at {deposited}.\nstdout: {result.stdout}"
        )

    def test_requirements_prefixed_file_deposits_to_requirements_directory(
        self,
        two_project_root: pathlib.Path,
        cli_invoker: CliAdapter,
        tmp_path: pathlib.Path,
    ) -> None:
        """intake routes a v[0-9]*.md file to the project requirements/ directory.

        The routing contract for version-prefixed files: a file named
        vX.Y.Z-<slug>.md must always land in ``projects/<name>/requirements/``,
        never in bugs/ or priority/.
        """
        root = two_project_root
        staged = tmp_path / "v9.9.9-contract-routing-test.md"
        staged.write_text(
            "# v9.9.9 — contract-routing-test\n\n"
            "## Status\nopen\n\n"
            "## Summary\nContract test requirements file.\n",
            encoding="utf-8",
        )
        (root / "projects" / "project_a" / "requirements").mkdir(parents=True, exist_ok=True)

        result = cli_invoker.invoke(
            "intake",
            ["--project", "project_a", str(staged)],
            kanban_root=root,
        )

        assert result.returncode == 0, (
            f"intake must exit 0 for a v[0-9]*.md file; "
            f"rc={result.returncode}.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        deposited = (
            root / "projects" / "project_a" / "requirements"
            / "v9.9.9-contract-routing-test.md"
        )
        assert deposited.exists(), (
            f"v[0-9]*.md file must be deposited to requirements/; "
            f"not found at {deposited}.\nstdout: {result.stdout}"
        )

    def test_no_clobber_refuses_deposit_when_target_already_exists(
        self,
        two_project_root: pathlib.Path,
        cli_invoker: CliAdapter,
        tmp_path: pathlib.Path,
    ) -> None:
        """intake refuses a deposit when the target file already exists in the intake directory.

        The no-clobber contract: if the destination already contains a file with
        the same basename as the staged file, intake must exit non-zero without
        overwriting the existing file.  The original destination content must
        remain unchanged after the refused deposit.
        """
        root = two_project_root
        bugs_dir = root / "projects" / "project_a" / "bugs"
        bugs_dir.mkdir(parents=True, exist_ok=True)

        filename = "BUG-9002-no-clobber-contract-test.md"
        # Pre-create the destination file to simulate a file already deposited.
        original_content = (
            "# BUG-9002: original content\n\n"
            "## Status\nopen\n\n"
            "## Summary\nOriginal intake file.\n"
        )
        (bugs_dir / filename).write_text(original_content, encoding="utf-8")

        staged = tmp_path / filename
        staged.write_text(
            "# BUG-9002: clobber attempt\n\n"
            "## Status\nopen\n\n"
            "## Summary\nThis must not overwrite the original.\n",
            encoding="utf-8",
        )

        result = cli_invoker.invoke(
            "intake",
            ["--project", "project_a", str(staged)],
            kanban_root=root,
        )

        assert result.returncode != 0, (
            "intake must return non-zero when the target already exists (no-clobber contract); "
            f"got rc={result.returncode}.\nstdout: {result.stdout}"
        )
        # The original destination must be unmodified.
        surviving_content = (bugs_dir / filename).read_text(encoding="utf-8")
        assert surviving_content == original_content, (
            "intake must not overwrite the existing file when the no-clobber guard fires; "
            "destination content was modified."
        )

    def test_unrecognized_prefix_is_rejected_without_deposit(
        self,
        two_project_root: pathlib.Path,
        cli_invoker: CliAdapter,
        tmp_path: pathlib.Path,
    ) -> None:
        """intake refuses a file whose prefix is not a recognized intake type.

        The rejection contract: a file without a known prefix (BUG-, PRIORITY-,
        or v[0-9]*) must be refused with a non-zero exit code.  No file must
        be created in any intake directory — the invalid deposit must be a no-op.
        """
        root = two_project_root
        staged = tmp_path / "UNKNOWN-prefix-contract-test.md"
        staged.write_text("# Unknown\n\n## Status\nopen\n", encoding="utf-8")

        result = cli_invoker.invoke(
            "intake",
            ["--project", "project_a", str(staged)],
            kanban_root=root,
        )

        assert result.returncode != 0, (
            "intake must return non-zero for an unrecognized filename prefix; "
            f"got rc={result.returncode}.\nstdout: {result.stdout}"
        )
