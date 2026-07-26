"""
resolve.py — Python implementation of the resolve_item key-resolution function.

resolve_item(ctx, project, key) is the single source of truth for key->item
resolution in the pgai_agent_kanban.ops package.  It is called by every write
operation (close_item, wontdo_item, delete_item, reset_item) and by the show
read operation.

Resolution order (mirrors the bash implementation in operator_ops.sh):
    1. tasks/<KEY>/           — task directory with status.md
    2. bugs/<KEY>.md          — bug intake file
    3. priority/<KEY>.md      — priority intake file
    4. requirements/<KEY>.md  — requirement intake file

Each step tries an exact match first, then a prefix-glob (KEY-*) that enforces
a hyphen boundary so ROLE-YYYYMMDD-002 never matches ROLE-YYYYMMDD-0020.

Return value on success:
    A ResolveResult namedtuple with three fields:
        item_type  — 'task' | 'bug' | 'priority' | 'requirement'
        path       — pathlib.Path to the item (directory for tasks; file for intake)
        state      — str, the current state from ## State (tasks) or ## Status (intake)

Exceptions:
    NotFound   — no match found (equivalent to bash return 3)
    Ambiguous  — prefix matched multiple items (equivalent to bash return 2);
                 the .candidates list carries all matches; .path and .state
                 describe the first (alphabetically sorted) match — callers
                 that treat ambiguous as a soft warning can still use those.
    OpsError   — argument error or I/O failure (equivalent to bash return 1)

CLI usage (bash delegation):
    python3 -m pgai_agent_kanban.ops.resolve PROJECT_ROOT KEY
    Emits the same three stdout lines as the bash resolve_item.
    Exit codes: 0 success, 1 error, 2 ambiguous, 3 not-found.
"""

from __future__ import annotations

import pathlib
import re
import sys
from typing import NamedTuple

from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.ops.errors import Ambiguous, NotFound, OpsError


# ---------------------------------------------------------------------------
# Public result type
# ---------------------------------------------------------------------------


class ResolveResult(NamedTuple):
    """Structured result returned by resolve_item on success or ambiguous match.

    Attributes:
        item_type:  One of 'task', 'bug', 'priority', 'requirement'.
        path:       Absolute path to the item.  A directory for tasks; a .md
                    file for intake items.
        state:      Current state string from the item's state/status field.
                    For tasks, read from ## State; for intake items, read from
                    ## Status (falling back to ## State).
    """

    item_type: str
    path: pathlib.Path
    state: str


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve_item(ctx: OpsContext, project: str, key: str) -> ResolveResult:
    """Resolve a KEY to its on-disk item under PROJECT_ROOT.

    This is the single source of truth for key->item resolution.  It is a
    pure read function: no filesystem mutation, no UI output.

    Resolution order:
        1. tasks/<KEY>/           — exact task directory match, then prefix-glob
        2. bugs/<KEY>.md          — exact match, then prefix-glob
        3. priority/<KEY>.md      — exact match, then prefix-glob
        4. requirements/<KEY>.md  — exact match, then prefix-glob

    Prefix-glob matching appends a trailing hyphen to the key before globbing
    (KEY-*) so that a numeric continuation (e.g. 0020) cannot match a shorter
    prefix (002).

    Args:
        ctx:     OpsContext carrying the kanban root path.
        project: Project name as registered in projects.cfg.
        key:     Task folder name (e.g. CODER-20260622-001-foo) or intake file
                 base name (e.g. an earlier defect).

    Returns:
        ResolveResult with item_type, path, and state on success.

    Raises:
        NotFound:  When no match is found in tasks/, bugs/, priority/, or
                   requirements/.
        Ambiguous: When a prefix glob matches more than one item.  The
                   exception carries a ``candidates`` list (all matches) and
                   a ``result`` ResolveResult pointing to the first
                   (alphabetically sorted) candidate so show-style callers
                   can still proceed.
        OpsError:  When the project root does not exist or a task directory
                   is missing its status.md.
    """
    project_root = ctx.project_root(project)

    if not project_root.is_dir():
        raise OpsError(
            f"resolve_item: project root does not exist or is not a directory: {project_root}"
        )

    # --- 1. Task folder: <project_root>/tasks/<KEY>/ ---
    tasks_dir = project_root / "tasks"
    task_matches = _collect_prefix_matches(tasks_dir, key, suffix="", is_dir=True)

    if task_matches:
        task_dir = task_matches[0]
        status_file = task_dir / "status.md"
        if not status_file.is_file():
            raise OpsError(
                f"resolve_item: task directory found but status.md is missing: {status_file}"
            )
        state = _read_task_state(status_file)
        result = ResolveResult(item_type="task", path=task_dir, state=state)

        if len(task_matches) > 1:
            raise Ambiguous(
                f"resolve_item: ambiguous key {key!r} — {len(task_matches)} task directories "
                f"match in tasks/; resolving to first match: {task_dir}",
                candidates=task_matches,
                result=result,
            )
        return result

    # --- 2-4. Intake files: bugs/, priority/, requirements/ ---
    for subdir, type_name in (
        ("bugs", "bug"),
        ("priority", "priority"),
        ("requirements", "requirement"),
    ):
        intake_dir = project_root / subdir
        matches = _collect_prefix_matches(intake_dir, key, suffix=".md", is_dir=False)

        if not matches:
            continue

        candidate = matches[0]
        state = _read_intake_state(candidate)
        result = ResolveResult(item_type=type_name, path=candidate, state=state)

        if len(matches) > 1:
            raise Ambiguous(
                f"resolve_item: ambiguous key {key!r} — {len(matches)} files match in "
                f"{subdir}/; resolving to first match: {candidate}",
                candidates=matches,
                result=result,
            )
        return result

    # --- Not found ---
    raise NotFound(
        f"resolve_item: item not found for key {key!r} "
        f"(searched tasks/, bugs/, priority/, requirements/ under {project_root})"
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _collect_prefix_matches(
    base_dir: pathlib.Path,
    key: str,
    suffix: str,
    is_dir: bool,
) -> list[pathlib.Path]:
    """Collect sorted matching paths for KEY under BASE_DIR.

    Tries an exact match (BASE_DIR/KEY<suffix>) first, then a prefix-glob
    (BASE_DIR/KEY-*<suffix>).  The trailing hyphen in the glob enforces a key
    boundary: ROLE-YYYYMMDD-002 matches ROLE-YYYYMMDD-002-<slug> but NOT
    ROLE-YYYYMMDD-0020-<slug>, because the latter does not start with
    ROLE-YYYYMMDD-002 followed by a hyphen.

    Args:
        base_dir: Directory to search.
        key:      Bare key string (no suffix).
        suffix:   File suffix for intake items (e.g. ".md"); empty string for
                  task directories.
        is_dir:   True when matching directories; False when matching files.

    Returns:
        Sorted, deduplicated list of matching Path objects.  Empty list when
        the base directory does not exist or no matches are found.
    """
    if not base_dir.is_dir():
        return []

    candidates: list[pathlib.Path] = []

    # Exact match: BASE_DIR/KEY<suffix>
    exact = base_dir / f"{key}{suffix}"
    if is_dir and exact.is_dir():
        candidates.append(exact)
    elif not is_dir and exact.is_file():
        candidates.append(exact)

    # Prefix-glob: BASE_DIR/KEY-*<suffix>
    glob_pattern = f"{key}-*{suffix}"
    for hit in base_dir.glob(glob_pattern):
        if is_dir and hit.is_dir():
            candidates.append(hit)
        elif not is_dir and hit.is_file():
            candidates.append(hit)

    # Sort for determinism; deduplicate (exact and glob cannot produce the
    # same path because the trailing - prevents KEY itself matching KEY-*).
    seen: set[pathlib.Path] = set()
    unique: list[pathlib.Path] = []
    for p in sorted(candidates):
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


def _read_task_state(status_file: pathlib.Path) -> str:
    """Read the ## State value from a task's status.md.

    Args:
        status_file: Path to the status.md file.

    Returns:
        The state string (e.g. 'WORKING', 'DONE').  Empty string when the
        ## State heading is absent.
    """
    try:
        text = status_file.read_text(encoding="utf-8")
    except OSError:
        return ""
    m = re.search(r"## State\n(?:[ \t]*\n)*([^\n]+)", text)
    return m.group(1).strip() if m else ""


def _read_intake_state(intake_file: pathlib.Path) -> str:
    """Read the ## Status (or ## State) value from an intake .md file.

    Intake files (bugs, priority, requirements) use ## Status as the primary
    state heading; ## State is accepted as a fallback for compatibility.

    Args:
        intake_file: Path to the intake .md file.

    Returns:
        The status string (e.g. 'open', 'done', 'running').  Empty string
        when neither heading is present.
    """
    try:
        text = intake_file.read_text(encoding="utf-8")
    except OSError:
        return ""
    m = re.search(r"## Status\n(?:[ \t]*\n)*([^\n]+)", text)
    if not m:
        m = re.search(r"## State\n(?:[ \t]*\n)*([^\n]+)", text)
    return m.group(1).strip() if m else ""


# ---------------------------------------------------------------------------
# CLI entrypoint (bash delegation shim)
# ---------------------------------------------------------------------------


def _cli_main(argv: list[str] | None = None) -> int:
    """CLI shim that replicates the bash resolve_item stdout/exit-code contract.

    Usage:
        python3 -m pgai_agent_kanban.ops.resolve PROJECT_ROOT KEY

    Stdout (on success, exit 0 or 2):
        Three lines:
            Line 1: item type — task | bug | priority | requirement
            Line 2: absolute path to the item
            Line 3: current state value

    Exit codes:
        0  Success (exactly one match)
        1  Argument / filesystem error
        2  Ambiguous prefix (multiple matches); first match emitted to stdout;
           warning emitted to stderr
        3  Not found
    """
    args = argv if argv is not None else sys.argv[1:]

    if len(args) != 2:
        print("resolve_item: PROJECT_ROOT and KEY arguments are required", file=sys.stderr)
        return 1

    project_root_str, key = args[0], args[1]

    if not key:
        print("resolve_item: KEY argument is required", file=sys.stderr)
        return 1

    project_root = pathlib.Path(project_root_str)
    if not project_root.is_dir():
        print(
            f"resolve_item: PROJECT_ROOT does not exist or is not a directory: {project_root_str}",
            file=sys.stderr,
        )
        return 1

    # Build a minimal OpsContext that treats project_root as the project root.
    # The CLI shim takes an absolute project root directly (not kanban_root + project
    # name), so we construct a synthetic context where the kanban_root is the
    # project root's grandparent and the project name is the parent/leaf components.
    #
    # Simplest approach: derive kanban_root and project from the path.
    # project_root is expected to be: <kanban_root>/projects/<project>/
    # We accept any structure by using project_root.parent.parent as kanban_root
    # and project_root.name as the project name.  When the path does not follow
    # this layout, use a direct resolver path instead.
    kanban_root = project_root.parent.parent
    project = project_root.name

    ctx = OpsContext(kanban_root=kanban_root)

    # Verify the project root derived from ctx matches the supplied path.
    # If not (e.g. the path layout is non-standard), fall back to a direct search.
    derived_root = ctx.project_root(project)
    if derived_root.resolve() != project_root.resolve():
        # Non-standard layout: treat project_root as the kanban_root and use a
        # dummy project name that maps back to the original path.
        # We achieve this by placing a synthetic "projects/<name>" structure.
        # Simplest fallback: search the project_root directly using the helpers.
        return _cli_search_direct(project_root, key)

    try:
        result = resolve_item(ctx, project, key)
        print(result.item_type)
        print(str(result.path))
        print(result.state)
        return 0
    except Ambiguous as exc:
        print(exc, file=sys.stderr)
        for c in exc.candidates:
            print(f"resolve_item:   {c}", file=sys.stderr)
        print(f"resolve_item: resolving to first match: {exc.result.path}", file=sys.stderr)
        print(exc.result.item_type)
        print(str(exc.result.path))
        print(exc.result.state)
        return 2
    except NotFound as exc:
        print(exc, file=sys.stderr)
        return 3
    except OpsError as exc:
        print(exc, file=sys.stderr)
        return 1


def _cli_search_direct(project_root: pathlib.Path, key: str) -> int:
    """Direct search fallback for non-standard project root paths.

    Used when the supplied PROJECT_ROOT does not follow the
    <kanban_root>/projects/<project>/ layout.

    Returns the same exit codes as _cli_main.
    """
    # --- Task folder ---
    tasks_dir = project_root / "tasks"
    task_matches = _collect_prefix_matches(tasks_dir, key, suffix="", is_dir=True)
    if task_matches:
        task_dir = task_matches[0]
        status_file = task_dir / "status.md"
        if not status_file.is_file():
            print(
                f"resolve_item: task directory found but status.md is missing: {status_file}",
                file=sys.stderr,
            )
            return 1
        state = _read_task_state(status_file)
        if len(task_matches) > 1:
            print(
                f"resolve_item: ambiguous key {key!r} — {len(task_matches)} task directories "
                f"match in tasks/:",
                file=sys.stderr,
            )
            for m in task_matches:
                print(f"resolve_item:   {m}", file=sys.stderr)
            print(f"resolve_item: resolving to first match: {task_dir}", file=sys.stderr)
            print("task")
            print(str(task_dir))
            print(state)
            return 2
        print("task")
        print(str(task_dir))
        print(state)
        return 0

    # --- Intake files ---
    for subdir, type_name in (
        ("bugs", "bug"),
        ("priority", "priority"),
        ("requirements", "requirement"),
    ):
        intake_dir = project_root / subdir
        matches = _collect_prefix_matches(intake_dir, key, suffix=".md", is_dir=False)
        if not matches:
            continue
        candidate = matches[0]
        state = _read_intake_state(candidate)
        if len(matches) > 1:
            print(
                f"resolve_item: ambiguous key {key!r} — {len(matches)} files match in {subdir}/:",
                file=sys.stderr,
            )
            for m in matches:
                print(f"resolve_item:   {m}", file=sys.stderr)
            print(f"resolve_item: resolving to first match: {candidate}", file=sys.stderr)
            print(type_name)
            print(str(candidate))
            print(state)
            return 2
        print(type_name)
        print(str(candidate))
        print(state)
        return 0

    # --- Not found ---
    print(
        f"resolve_item: item not found for key {key!r} "
        f"(searched tasks/, bugs/, priority/, requirements/ under {project_root})",
        file=sys.stderr,
    )
    return 3


if __name__ == "__main__":
    sys.exit(_cli_main())
