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

Confinement (before anything is read, written or deleted):
    - PROJECT and KEY must each be one plain name
      (``api.validation.require_component``): no ``/``, ``\\``, ``..``,
      NUL or other control character, glob character or leading ``-``.
      Anything else is refused with a ``resolve_item.project_invalid`` /
      ``resolve_item.key_invalid`` dual-channel ``OpsError`` -- the same
      rule the operator API applies to the same fields, so the CLI scripts
      that reach these functions refuse alike.
    - KEY is escaped (``glob.escape``) before it becomes a glob prefix.
    - Every matched path's realpath must lie inside the tree being searched
      (the project root, or the project's archive tree); a match that a
      symlink carries outside it is refused (``resolve_item.path_outside_tree``)
      before its state is read.
    - A task folder's documents are held to the same rule against the folder
      itself (:func:`require_task_document_inside`): a ``status.md`` -- read
      here for the task's state, and rewritten by close, wontdo and reset --
      or a ``README.md`` (checked by the read callers before they open it)
      that is a symlink out of its task folder is refused the same way, and
      nothing is read from it.

Archive fallback (read callers only):
    resolve_item(..., include_archive=True) runs the same four steps against
    the project's archive tree, <kanban_root>/archive/<project>/, when -- and
    only when -- nothing in the live project matches KEY. A live match, an
    ambiguous live prefix and a live task folder without status.md are all
    answered from the live project exactly as before; the archive is never
    consulted for them. The default is include_archive=False, so the write
    operations (close, wontdo, delete, reset) and the resolve CLI shim the
    bash write scripts call keep resolving live items only: an archived item
    is read by ID, never changed through these verbs.

Return value on success:
    A ResolveResult namedtuple with four fields:
        item_type  — 'task' | 'bug' | 'priority' | 'requirement'
        path       — pathlib.Path to the item (directory for tasks; file for intake)
        state      — str, the current state from ## State (tasks) or ## Status (intake)
        archived   — bool, True when the item was found in the project's archive

Exceptions:
    NotFound   — no match found (equivalent to bash return 3)
    Ambiguous  — prefix matched multiple items (equivalent to bash return 2);
                 the .candidates list carries all matches; .path and .state
                 describe the first (alphabetically sorted) match — callers
                 that treat ambiguous as a soft warning can still use those.
    OpsError   — argument error or I/O failure (equivalent to bash return 1)

resolve_project_name(ctx, name) resolves a typed --project value against the
projects.cfg registry by exact match, then unambiguous hyphen-boundary prefix
-- the Python twin of pp_resolve_project_name in lib/project_paths.sh -- and
refuses with the same project_resolve.not_found / project_resolve.ambiguous
dual-channel blocks.

CLI usage (bash delegation):
    python3 -m pgai_agent_kanban.ops.resolve PROJECT_ROOT KEY
    Emits the same three stdout lines as the bash resolve_item.
    Exit codes: 0 success, 1 error, 2 ambiguous, 3 not-found.
"""

from __future__ import annotations

import glob
import os
import pathlib
import shlex
import sys
from typing import NamedTuple

# Run by file path (``python3 .../resolve.py``) or loaded from a file
# outside its package, this module has no parent package and the interpreter
# puts only its own directory on sys.path, so the package imports below would
# not resolve. Put this file's own package root -- the directory holding the
# pgai_agent_kanban/ this file sits in -- at the front of sys.path so the
# imports resolve from the same tree, never another. Imported as a package
# member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

# Module import, not ``from ... import read_state``: task_status imports
# ops.context, whose package __init__ imports this module, so a name import
# here would fail whenever task_status is the first module loaded.
from pgai_agent_kanban import task_status as _task_status  # noqa: E402
from pgai_agent_kanban.api.validation import ComponentRefused, require_component  # noqa: E402
from pgai_agent_kanban.lib import ini  # noqa: E402
from pgai_agent_kanban.lib.error_contract import format_error_contract  # noqa: E402
from pgai_agent_kanban.lib.md_fields import read_field as read_md_field  # noqa: E402
from pgai_agent_kanban.ops.context import OpsContext  # noqa: E402
from pgai_agent_kanban.ops.errors import Ambiguous, NotFound, OpsError  # noqa: E402


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
        archived:   True when the item was found in the project's archive
                    (``resolve_item(..., include_archive=True)`` and the key
                    matched nothing live); False for a live item.
    """

    item_type: str
    path: pathlib.Path
    state: str
    archived: bool = False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve_item(
    ctx: OpsContext,
    project: str,
    key: str,
    *,
    include_archive: bool = False,
) -> ResolveResult:
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

    With *include_archive*, a KEY that matches nothing live is looked up
    the same way (same four steps, same exact-then-prefix rules) in the
    project's archive tree, ``<kanban_root>/archive/<project>/``; a match
    there comes back with ``archived=True``. The live project always wins:
    the archive is consulted only after the live search found nothing.

    Args:
        ctx:             OpsContext carrying the kanban root path.
        project:         Project name as registered in projects.cfg.
        key:             Task folder name (e.g. CODER-20260622-001-foo) or
                         intake file base name (e.g. BUG-0042).
        include_archive: Fall back to the project's archive when KEY is not
                         live. Read callers (show, GET /show) pass True;
                         the default False keeps every write operation on
                         live items only.

    Returns:
        ResolveResult with item_type, path, state and archived on success.

    Raises:
        NotFound:  When no match is found in tasks/, bugs/, priority/, or
                   requirements/ (nor, with *include_archive*, in the
                   project's archive).
        Ambiguous: When a prefix glob matches more than one item.  The
                   exception carries a ``candidates`` list (all matches) and
                   a ``result`` ResolveResult pointing to the first
                   (alphabetically sorted) candidate so show-style callers
                   can still proceed.
        OpsError:  When PROJECT or KEY is not one plain name (the dual-
                   channel ``resolve_item.project_invalid`` /
                   ``resolve_item.key_invalid`` block, raised before any
                   filesystem access), a match resolves outside the tree
                   searched (``resolve_item.path_outside_tree``), the
                   project root does not exist, or a task directory is
                   missing its status.md.
    """
    _require_plain_project(ctx, project)
    project_root = ctx.project_root(project)
    _require_plain_key(key, project_root)

    if not project_root.is_dir():
        raise OpsError(
            f"resolve_item: project root does not exist or is not a directory: {project_root}"
        )

    live = _search_tree(project_root, key)
    if live is not None:
        return live

    if include_archive:
        archive_tree = _archive_tree(ctx, project, key)
        if archive_tree is not None:
            archived = _search_tree(archive_tree, key, archived=True)
            if archived is not None:
                return archived

    # --- Not found ---
    raise NotFound(
        f"resolve_item: item not found for key {key!r} "
        f"(searched tasks/, bugs/, priority/, requirements/ under {project_root})"
    )


def _search_tree(root: pathlib.Path, key: str, *, archived: bool = False) -> "ResolveResult | None":
    """Resolve KEY in one project tree (live or archived); ``None`` when nothing matches.

    Runs the four resolution steps of :func:`resolve_item` over *root*.
    *archived* is copied onto the result and names the tree in the
    ambiguity message (``archive tasks/`` rather than ``tasks/``).

    Raises:
        Ambiguous: a prefix matched more than one item in one category.
        OpsError:  the matched task directory has no status.md.
    """
    where = "archive " if archived else ""

    # --- 1. Task folder: <root>/tasks/<KEY>/ ---
    task_matches = _collect_prefix_matches(root / "tasks", key, suffix="", is_dir=True, tree=root)

    if task_matches:
        task_dir = task_matches[0]
        status_file = task_dir / "status.md"
        if not status_file.is_file():
            raise OpsError(
                f"resolve_item: task directory found but status.md is missing: {status_file}"
            )
        require_task_document_inside(status_file, task_dir)
        state = _read_task_state(status_file)
        result = ResolveResult(item_type="task", path=task_dir, state=state, archived=archived)

        if len(task_matches) > 1:
            raise Ambiguous(
                f"resolve_item: ambiguous key {key!r} — {len(task_matches)} task directories "
                f"match in {where}tasks/; resolving to first match: {task_dir}",
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
        matches = _collect_prefix_matches(root / subdir, key, suffix=".md", is_dir=False, tree=root)

        if not matches:
            continue

        candidate = matches[0]
        state = _read_intake_state(candidate)
        result = ResolveResult(item_type=type_name, path=candidate, state=state, archived=archived)

        if len(matches) > 1:
            raise Ambiguous(
                f"resolve_item: ambiguous key {key!r} — {len(matches)} files match in "
                f"{where}{subdir}/; resolving to first match: {candidate}",
                candidates=matches,
                result=result,
            )
        return result

    return None


def _archive_tree(ctx: OpsContext, project: str, key: str) -> "pathlib.Path | None":
    """The project's archive tree to search for KEY, or ``None`` when there is none to search.

    ``None`` when the archive directory does not exist, when *project* is
    not a single safe path component (``archive.layout`` refuses to build a
    path for it), or when KEY is not a single path component -- a key
    carrying ``/``, ``\\`` or ``..`` is never joined onto an archive path,
    so the fallback cannot read outside ``<kanban_root>/archive/<project>/``.
    """
    if not key or "/" in key or "\\" in key or ".." in key or "\0" in key:
        return None
    # Imported here: the archive package's run module imports this module,
    # so a module-level import would be circular.
    from pgai_agent_kanban.archive.layout import ArchiveLayoutError, archive_root

    try:
        root = archive_root(ctx.kanban_root, project)
    except ArchiveLayoutError:
        return None
    return root if root.is_dir() else None


def resolve_project_name(ctx: OpsContext, name: str) -> str:
    """Resolve a typed project name to a registered one: exact, then unambiguous prefix.

    The Python twin of ``pp_resolve_project_name`` in
    ``team/scripts/lib/project_paths.sh``, the first step every project-
    scoped operator script takes, so a surface that calls an ops function
    directly (the operator API) resolves ``--project`` the way the CLI does
    and refuses with the same three-line dual-channel block.

    Rules (identical to the bash resolver):
      - ``projects.cfg`` absent: *name* is returned unchanged -- nothing to
        resolve against, and the operation's own directory check is the
        backstop.
      - An exact match against a registered name wins.
      - Otherwise a registered name matches when it continues *name* with a
        literal hyphen (``<name>-...``), the same boundary ``resolve_item``
        uses for keys. Exactly one such match resolves; none is
        ``NotFound``; two or more is ``Ambiguous``.

    Registered names are the ``[project:<name>]`` sections of projects.cfg,
    in file order, read through ``pgai_agent_kanban.lib.ini``.

    Args:
        ctx:  OpsContext carrying the kanban root path.
        name: The project name or prefix as the caller typed it.

    Returns:
        The canonical registered project name.

    Raises:
        NotFound:  No registered project matches. ``str(exc)`` is the house-
                   shape block (``project_resolve.not_found``) the bash
                   resolver prints.
        Ambiguous: Two or more registered projects share the prefix.
                   ``str(exc)`` is the ``project_resolve.ambiguous`` block;
                   ``candidates`` lists the matching names.
        OpsError:  projects.cfg exists but cannot be parsed.
    """
    cfg = ctx.kanban_root / "projects.cfg"
    if not cfg.is_file():
        return name

    try:
        entries = ini.projects_registry(ctx.kanban_root)
    except ini.IniError as exc:
        raise OpsError(
            f"resolve_project_name: cannot read the project registry {cfg}: {exc}"
        ) from exc
    names = [entry.name for entry in sorted(entries, key=lambda entry: entry.section_index)]

    if name in names:
        return name

    matches = [candidate for candidate in names if candidate.startswith(f"{name}-")]
    if len(matches) == 1:
        return matches[0]

    if not matches:
        if names:
            remedy = f"Check the project name against projects.cfg (registered: {', '.join(names)})."
        else:
            remedy = (
                f"No project is registered in {cfg}. Register one first: "
                f"team/scripts/add-project.sh --project {name}."
            )
        # BLOCK-WORTHY (docs/blocking-site-census.md): an unregistered name
        # has no directory an operation should ever write to -- refused
        # rather than concatenated into a path, exactly as the bash resolver
        # refuses.
        # CENSUS-ANCHOR: ops-resolve-project-not-found-01
        raise NotFound(format_error_contract(
            "project_resolve.not_found",
            f"pp_resolve_project_name: no project matches '{name}' in {cfg}.",
            remedy,
        ))

    # BLOCK-WORTHY (docs/blocking-site-census.md): picking one of several
    # prefix matches would risk operating on the wrong project's data, so
    # this names them all and refuses, exactly as the bash resolver refuses.
    # CENSUS-ANCHOR: ops-resolve-project-ambiguous-01
    raise Ambiguous(
        format_error_contract(
            "project_resolve.ambiguous",
            f"pp_resolve_project_name: '{name}' matches multiple registered projects: "
            f"{', '.join(matches)}.",
            f"Re-run with a longer, unambiguous prefix or the full name, e.g. --project {matches[0]}.",
        ),
        candidates=matches,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _require_plain_project(ctx: OpsContext, project: str) -> None:
    """Refuse a PROJECT that is not one plain name, before any path is built from it.

    Raises:
        OpsError: the ``resolve_item.project_invalid`` dual-channel block,
            whose remedy lists the registered project names.
    """
    try:
        require_component(project, "project")
    except ComponentRefused as exc:
        registry = shlex.quote(str(ctx.kanban_root / "projects.cfg"))
        # BLOCK-WORTHY (docs/blocking-site-census.md): an invariant by
        # design -- the project name becomes the directory every item path
        # is joined under, so anything but one plain name could point the
        # read, rewrite or delete outside the kanban root.
        # CENSUS-ANCHOR: ops-resolve-project-not-component-01
        raise OpsError(exc.block("resolve_item", f"grep -o '^\\[project:[^]]*\\]' {registry}")) from exc


def _require_plain_key(key: str, project_root: pathlib.Path) -> None:
    """Refuse a KEY that is not one plain name, before it is joined or globbed.

    Raises:
        OpsError: the ``resolve_item.key_invalid`` dual-channel block, whose
            remedy lists the items the project holds.
    """
    try:
        require_component(key, "key")
    except ComponentRefused as exc:
        listing = " ".join(
            shlex.quote(str(project_root / sub)) for sub in ("tasks", "bugs", "priority", "requirements")
        )
        # BLOCK-WORTHY (docs/blocking-site-census.md): an invariant by
        # design -- the key becomes a path component and a glob prefix, so a
        # traversal, absolute or glob-shaped key would reach files outside
        # the project (or walk the filesystem) before any guard ran.
        # CENSUS-ANCHOR: ops-resolve-key-not-component-01
        raise OpsError(exc.block("resolve_item", f"ls {listing}")) from exc


def _require_inside(path: pathlib.Path, tree: pathlib.Path) -> None:
    """Refuse *path* when its realpath is not *tree*'s realpath or below it.

    A plain key cannot name a path outside the tree by itself; this catches
    the one remaining way out -- a symlink in the tree that points
    elsewhere -- before the match is read, rewritten or deleted.

    Raises:
        OpsError: the ``resolve_item.path_outside_tree`` dual-channel block.
    """
    real = os.path.realpath(path)
    real_tree = os.path.realpath(tree)
    if real == real_tree or real.startswith(real_tree.rstrip(os.sep) + os.sep):
        return
    # BLOCK-WORTHY (docs/blocking-site-census.md): an invariant by design --
    # the resolved item is what close, wontdo, reset and delete rewrite or
    # remove, so one that lies outside the project tree is never handed to
    # them, nor read for show.
    # CENSUS-ANCHOR: ops-resolve-path-outside-tree-01
    raise OpsError(format_error_contract(
        "resolve_item.path_outside_tree",
        f"resolve_item: {path} resolves to {real}, outside the tree it was found in "
        f"({real_tree}); an item is only read, written or deleted inside its own "
        "project tree. Nothing was read, written or run.",
        f"ls -ld {shlex.quote(str(path))}",
    ))


def require_task_document_inside(document: pathlib.Path, task_dir: pathlib.Path) -> None:
    """Refuse a task document whose realpath is not inside its task folder.

    The resolver confines the task folder to its tree; this holds the files
    inside it -- ``status.md``, ``README.md`` -- to the folder the same way,
    so a document that is a symlink to a file elsewhere is never read,
    rewritten or returned. Call it before opening the document. One check
    shared by the resolver (``status.md``) and the read surfaces
    (``ops.read``: show and the README route).

    Raises:
        OpsError: the ``resolve_item.path_outside_tree`` dual-channel block.
    """
    _require_inside(document, task_dir)


def _collect_prefix_matches(
    base_dir: pathlib.Path,
    key: str,
    suffix: str,
    is_dir: bool,
    *,
    tree: "pathlib.Path | None" = None,
) -> list[pathlib.Path]:
    """Collect sorted matching paths for KEY under BASE_DIR.

    Tries an exact match (BASE_DIR/KEY<suffix>) first, then a prefix-glob
    (BASE_DIR/KEY-*<suffix>).  The trailing hyphen in the glob enforces a key
    boundary: ROLE-YYYYMMDD-002 matches ROLE-YYYYMMDD-002-<slug> but NOT
    ROLE-YYYYMMDD-0020-<slug>, because the latter does not start with
    ROLE-YYYYMMDD-002 followed by a hyphen.  KEY is escaped before it is
    globbed, so it only ever matches literally.

    Args:
        base_dir: Directory to search.
        key:      Bare key string (no suffix).
        suffix:   File suffix for intake items (e.g. ".md"); empty string for
                  task directories.
        is_dir:   True when matching directories; False when matching files.
        tree:     The project (or archive) tree being searched; every match
                  must resolve inside it. Default: *base_dir* itself.

    Returns:
        Sorted, deduplicated list of matching Path objects.  Empty list when
        the base directory does not exist or no matches are found.

    Raises:
        OpsError: a match resolves outside *tree* (``_require_inside``).
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

    # Prefix-glob: BASE_DIR/KEY-*<suffix>, the key itself matched literally.
    glob_pattern = f"{glob.escape(key)}-*{suffix}"
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
    for p in unique:
        _require_inside(p, base_dir if tree is None else tree)
    return unique


def _read_task_state(status_file: pathlib.Path) -> str:
    """Read the ## State value from a task's status.md.

    Args:
        status_file: Path to the status.md file.

    Returns:
        The state string (e.g. 'WORKING', 'DONE') with its letter case as
        written -- the CLI shim prints it verbatim.  Empty string when the
        ## State heading is absent.
    """
    return _task_status.read_state(status_file, upper=False)


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
    return read_md_field(intake_file, "Status") or read_md_field(intake_file, "State")


# ---------------------------------------------------------------------------
# CLI entrypoint (bash delegation shim)
# ---------------------------------------------------------------------------


_CLI_HELP = """\
usage: resolve.py PROJECT_ROOT KEY

Resolve a task, bug, priority or requirement key to the item it names, the
way the bash resolve_item does: tasks/<KEY>/, then bugs/<KEY>.md, then
priority/<KEY>.md, then requirements/<KEY>.md, each by exact name first and
then by an unambiguous KEY-* prefix.

Positional arguments (both required):
  PROJECT_ROOT  the project's root directory, normally
                <kanban_root>/projects/<project>/ (any directory holding
                tasks/, bugs/, priority/ and requirements/ is searched
                directly)
  KEY           the item key -- a task folder name, or a bug, priority or
                requirement file name without .md -- or a hyphen-bounded
                prefix of one

Prints three lines on a match: the item type (task, bug, priority or
requirement), the item's absolute path, and its current state.

Live items only: the bash write scripts (close, delete, reset) resolve
through this command, so the project's archive is never searched here.

KEY must be one plain name -- no '/', '\\', '..', NUL or other control
character, glob character (* ? [ ]) or leading '-' -- and is matched
literally; a match that resolves (through a symlink) outside PROJECT_ROOT
is refused. Both refusals print a dual-channel error/message/remedy block
and exit 1.

Options:
  -h, --help    print this help and exit 0

Exit codes: 0 one match; 1 argument or filesystem error; 2 ambiguous
prefix (the first match is printed, the candidates go to stderr);
3 not found.
"""


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

    if args and args[0] in ("-h", "--help"):
        print(_CLI_HELP, end="")
        return 0

    if len(args) != 2:
        print("resolve_item: PROJECT_ROOT and KEY arguments are required", file=sys.stderr)
        return 1

    project_root_str, key = args[0], args[1]

    if not key:
        print("resolve_item: KEY argument is required", file=sys.stderr)
        return 1

    project_root = pathlib.Path(project_root_str)
    try:
        _require_plain_key(key, project_root)
    except OpsError as exc:
        print(exc, file=sys.stderr)
        return 1
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
    try:
        task_matches = _collect_prefix_matches(tasks_dir, key, suffix="", is_dir=True, tree=project_root)
    except OpsError as exc:
        print(exc, file=sys.stderr)
        return 1
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
        try:
            matches = _collect_prefix_matches(intake_dir, key, suffix=".md", is_dir=False, tree=project_root)
        except OpsError as exc:
            print(exc, file=sys.stderr)
            return 1
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
