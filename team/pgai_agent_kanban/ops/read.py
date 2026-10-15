"""
read.py — Read facades for the pgai_agent_kanban.ops package.

Each function in this module is a thin facade over an existing Python read
module.  No read logic is reimplemented here: each function resolves the
necessary paths from ``ctx``, calls the underlying module function, and
returns its result.

Restrictions (enforced by acceptance criteria):
  - No shell-out to kanban scripts; reads are Python.
  - No mutation of kanban state.
  - Each function delegates to the module named in its docstring.

The Class-A read functions exported here:

  list_projects(ctx)
  get_halt_state(ctx, project=None)
  get_queues(ctx, project)
  get_task_status(ctx, project, key, file="status")
  get_release_state(ctx, project)
  get_deep_verify_status(ctx, project)
  get_attention(ctx, project=None)
  get_metrics(ctx, project, last_n=10)
  get_next_firings(ctx)

and the script-shaped reads, each a data function plus the render function
returning the ReadOutput (exit code, stdout, stderr) its script prints:

  get_status(ctx, project)              render_status(...)       kanban-status.sh
  get_test_report(ctx, project, key)    render_test_report(...)  show-test-report.sh
  get_task_readme(ctx, project, key)    render_task_readme(...)  show-readme.sh
  list_rejected(ctx, project=None)      render_rejected(...)     list-rejected.sh

``read_status(...)`` returns ``render_status``'s output together with the
snapshot it rendered, so ``GET /status`` can return structured fields beside
the summary text from the one ``get_status`` call.

The status summary's version lookups (lib.released_version) read local git
refs when a project names a git dev tree or the kanban root has no VERSION
file; every other read here is pure Python.
"""

from __future__ import annotations

import io
import os
import pathlib
import re
import sys
import time
from typing import Any, NamedTuple

from pgai_agent_kanban.lib import ini
from pgai_agent_kanban.lib.error_contract import format_error_contract
from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.ops.errors import (
    Ambiguous,
    CliRefusal,
    IoError,
    NotFound,
    OpsError,
    Refused,
    cli_exit_code,
)


# ---------------------------------------------------------------------------
# list_projects
# ---------------------------------------------------------------------------


def list_projects(ctx: OpsContext) -> list[str]:
    """Return the names of the projects registered in this kanban install.

    Delegates to ``pgai_agent_kanban.lib.ini.projects_registry``, the reader
    ``GET /projects`` and the bash ``projects_cfg_list`` agree with, so the
    names come back in projects.cfg priority order (ascending ``priority``,
    file order breaking ties).

    Args:
        ctx: OpsContext carrying the kanban root path.

    Returns:
        Project names in priority order.  Empty list when projects.cfg is
        absent or registers no project.

    Raises:
        IoError: When projects.cfg exists but cannot be parsed as INI.
    """
    try:
        return [entry.name for entry in ini.projects_registry(ctx.kanban_root)]
    except ini.IniError as exc:
        raise IoError(
            f"Cannot read the project registry {ctx.kanban_root / 'projects.cfg'}: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# get_halt_state
# ---------------------------------------------------------------------------


def get_halt_state(
    ctx: OpsContext,
    project: "str | None" = None,
) -> "tuple[str, str | None]":
    """Return the current halt state for the kanban or a named project.

    Delegates to ``dashboard.halt_state.compute_halt_state``.  The halt state
    describes whether the system (or project) is running normally, draining
    toward a halt, or fully halted.

    Args:
        ctx:     OpsContext carrying the kanban root path.
        project: Project name to check.  When ``None``, the global kanban root
                 is checked for HALT / HALT-AFTER sentinels.

    Returns:
        A ``(state, event)`` tuple where ``state`` is one of ``'halted'``,
        ``'draining'``, or ``'normal'``, and ``event`` is a lowercase string
        token or ``None``.

    Raises:
        NotFound: When ``project`` is given but does not exist under
            ``ctx.kanban_root/projects/``.
    """
    from pgai_agent_kanban.dashboard.halt_state import compute_halt_state

    if project is None:
        root = ctx.kanban_root
    else:
        root = ctx.project_root(project)
        if not root.exists():
            raise NotFound(f"Project not found: {project!r} (expected at {root})")

    return compute_halt_state(root)


# ---------------------------------------------------------------------------
# get_queues
# ---------------------------------------------------------------------------

#: Project name the no-project remedy suggests when projects.cfg registers none.
_EXAMPLE_PROJECT = "pgai-agent-kanban"


def no_project_refusal(ctx: OpsContext) -> str:
    """Return the ``show_queues.no_project`` dual-channel block.

    The refusal :func:`get_queues` raises when it is asked for an empty
    project -- what ``show-queues.sh`` and ``GET /show-queues`` print in
    compact mode when neither a project nor ``PGAI_PROJECT_NAME`` names one.
    The remedy is a command the reader can run as printed: it names the
    first registered project (projects.cfg priority order), or -- on a root
    whose projects.cfg registers none, including a single-project root with
    its queues at ``<root>/tasks/queues/`` -- how to register one first,
    because the queue board reads registered project directories only.

    Args:
        ctx: OpsContext carrying the kanban root path.

    Returns:
        The three-line ``error`` / ``message`` / ``remedy`` block.
    """
    try:
        registered = list_projects(ctx)
    except OpsError:
        registered = []

    message = (
        "show-queues.sh: no project specified; pass --project <name> or set "
        "PGAI_PROJECT_NAME. The compact board shows one project's queue counts, "
        "so with none named no queue was read."
    )
    if registered:
        example = registered[0]
        remedy = (
            f"Name a registered project ({', '.join(registered)}): "
            f"team/scripts/dashboard/show-queues.sh --project {example} "
            f"(REST: GET /show-queues?project={example}), or export "
            f"PGAI_PROJECT_NAME={example} before running it."
        )
    else:
        message += (
            f" No project is registered in {ctx.kanban_root / 'projects.cfg'}, and the "
            "board reads registered project directories (projects/<name>/tasks/queues/) only."
        )
        remedy = (
            f"Register the project, then name it: team/scripts/create-project.sh --project "
            f"{_EXAMPLE_PROJECT} && team/scripts/dashboard/show-queues.sh --project {_EXAMPLE_PROJECT}"
        )
    return format_error_contract("show_queues.no_project", message, remedy)


def get_queues(ctx: OpsContext, project: str) -> dict[str, Any]:
    """Return everything the queue-state pane shows for a named project.

    The one read behind ``show-queues.sh`` and ``GET /show-queues``: both
    call this function and render its result (the CLI and the REST compact
    mode through ``dashboard.queue_pane``; REST ``details=true`` returns
    ``details`` as JSON). Delegates to:

      - ``dashboard.task_counts.scan_task_counts`` -- per-role counters and
        the working task (the scan data.sh publishes for the dashboard);
      - ``dashboard.queue_details.resolve_queue_details`` -- the per-agent,
        per-state listing with queue-reconciliation warnings;
      - ``dashboard.halt_state.halt_flag_and_text`` -- the halt flag/text
        pair, with the kanban-wide HALT as fallback;
      - ``pm_status.scan_queue_files`` -- the raw queue entry lines.

    Args:
        ctx:     OpsContext carrying the kanban root path.
        project: Project name as registered in ``projects.cfg``. An empty
                 name is refused: there is no project-less queue state to
                 read, so no counts are ever returned for one.

    Returns:
        A JSON-serialisable dict:
          - ``"project"``: the project name.
          - ``"counts"``: ``{role: {stat: int}}`` for roles PM, CODER,
            WRITER, TESTER, CM and stats TOTAL, DONE, WORKING, BLOCKED,
            WAITING.
          - ``"working"``: ``{"task_id", "mtime", "role"}`` for the most
            recently touched WORKING task, or ``None``.
          - ``"halt"``: ``{"flag": "yes"|"no", "text": str}``.
          - ``"details"``: ``{agent: {state: [{"task_id", "warning"}]}}``
            (see ``resolve_queue_details``).
          - ``"entries"``: ``[{"queue", "task_line"}]``, every line of every
            ``<agent>_backlog.md`` in the project's ``tasks/queues/``.

    Raises:
        Refused:  When ``project`` is empty; ``str(exc)`` is the
            ``show_queues.no_project`` dual-channel block
            (:func:`no_project_refusal`).
        NotFound: When the project directory does not exist; ``str(exc)`` is
            the ``show_queues.project_directory_missing`` dual-channel block.
        IoError:  When the queue files cannot be read.
    """
    from pgai_agent_kanban.dashboard.halt_state import halt_flag_and_text
    from pgai_agent_kanban.dashboard.queue_details import resolve_queue_details
    from pgai_agent_kanban.dashboard.task_counts import scan_task_counts

    if not project.strip():
        # BLOCK-WORTHY (docs/blocking-site-census.md): with no project named
        # there is no queue state to read -- an empty name would otherwise
        # resolve to the projects/ directory itself (or, on a single-project
        # root, to nothing) and the pane would print a board of zeros it
        # never read, indistinguishable from a genuinely idle project.
        # CENSUS-ANCHOR: ops-read-queues-no-project-01
        raise Refused(no_project_refusal(ctx))

    _ensure_pm_agent_on_path(ctx)
    from pm_status import scan_queue_files  # type: ignore[import-not-found]

    project_root = ctx.project_root(project)
    if not project_root.is_dir():
        # BLOCK-WORTHY (docs/blocking-site-census.md): a queue pane that
        # cannot find the project directory it would render has nothing to
        # show -- refusing stops a board of zeros, indistinguishable from a
        # genuinely idle project, being rendered for a name nothing
        # registered.
        # CENSUS-ANCHOR: ops-read-queues-project-missing-01
        raise NotFound(format_error_contract(
            "show_queues.project_directory_missing",
            f"show-queues.sh: project directory not found: {project_root}.",
            "Check the project name against projects.cfg, or create it first with "
            f"team/scripts/create-project.sh --project {project}.",
        ))

    tasks_dir = ctx.tasks_dir(project)
    counts = scan_task_counts(tasks_dir)
    flag, text = halt_flag_and_text(project_root, ctx.kanban_root)
    try:
        entries = scan_queue_files(tasks_dir)
    except OSError as exc:
        raise IoError(
            f"Cannot read queue files for project {project!r}: {exc}"
        ) from exc

    working = counts.working
    return {
        "project": project,
        "counts": counts.queues,
        "working": (
            {"task_id": working.task_id, "mtime": working.mtime, "role": working.role}
            if working is not None
            else None
        ),
        "halt": {"flag": flag, "text": text},
        "details": resolve_queue_details(tasks_dir, tasks_dir / "queues"),
        "entries": entries,
    }


def render_queues(ctx: OpsContext, project: str, data_project: str, details: bool) -> Any:
    """Render the queue-state pane ``show-queues.sh`` prints, over :func:`get_queues`.

    Args:
        ctx:          OpsContext carrying the kanban root path.
        project:      The resolved ``--project`` value, or ``""``.
        data_project: *project*, else the caller's ``PGAI_PROJECT_NAME``, else ``""``.
        details:      The per-agent listing (``--details``) instead of the compact rows.

    Returns:
        ``dashboard.queue_pane.PaneOutput``: ``stdout`` is the pane, ``stderr``
        any warning line. Colours and pane width come from this process's
        environment.

    Raises:
        OpsError: as :func:`get_queues` raises for a project it cannot read.
    """
    from pgai_agent_kanban.dashboard import queue_pane

    plan = queue_pane.plan_queue_pane(ctx, project, data_project, details)
    queues = {name: get_queues(ctx, name) for name in plan.fetch}
    return queue_pane.render_queue_pane(ctx, plan, queues, environ=os.environ, now=time.time())


# ---------------------------------------------------------------------------
# get_task_status
# ---------------------------------------------------------------------------


#: The documents ``get_task_status`` can read from a task folder.
TASK_DOCUMENT_FILES: dict[str, str] = {"status": "status.md", "readme": "README.md"}


class TaskDocument(NamedTuple):
    """One task's or intake item's document, as ``show.sh`` emits it.

    Attributes:
        key:        The key as the caller typed it (possibly a prefix).
        item_type:  ``task``, ``bug``, ``priority`` or ``requirement``.
        state:      The item's state, as ``resolve_item`` read it.
        path:       The file whose content ``text`` holds.
        text:       That file's content, decoded as UTF-8 with line endings
                    kept as written.
        fields:     ``## Heading`` -> section body for every heading in the
                    document (``pm_status.get_status_fields``).
        candidates: Every item the key matched when it was an ambiguous
                    prefix (the first one is the item read); empty otherwise.
        ambiguity:  The resolver's ambiguity message, or ``""``.
        archived:   True when the key matched nothing live and the item was
                    read from the project's archive.
    """

    key: str
    item_type: str
    state: str
    path: pathlib.Path
    text: str
    fields: dict[str, str]
    candidates: tuple[pathlib.Path, ...]
    ambiguity: str
    archived: bool = False

    def ambiguity_notice(self) -> str:
        """The stderr lines a CLI prints when the key was ambiguous, or ``""``.

        The resolver's message, one ``resolve_item:   <path>`` line per
        candidate, and the ``resolving to first match`` line -- the notice
        show.sh has always printed before emitting the first match.
        """
        if not self.ambiguity:
            return ""
        item = self.path.parent if self.item_type == "task" else self.path
        return _ambiguity_notice(self.ambiguity, self.candidates, item)

    def archived_notice(self) -> str:
        """The stderr line marking an item read from the archive, or ``""``.

        ``archived: <key> is not in the live project; read from the archive:
        <item path>`` -- printed by show.sh and carried in GET /show's
        ``stderr`` beside the document, which itself is emitted unchanged.
        """
        if not self.archived:
            return ""
        item = self.path.parent if self.item_type == "task" else self.path
        return f"archived: {self.key} is not in the live project; read from the archive: {item}\n"


def _ambiguity_notice(message: str, candidates: "tuple[pathlib.Path, ...] | list[pathlib.Path]",
                      first_match: pathlib.Path) -> str:
    """The resolver's ambiguous-key notice, as the resolve CLI prints it to stderr.

    The resolver's message, one ``resolve_item:   <path>`` line per
    candidate, and ``resolve_item: resolving to first match: <path>``.
    """
    lines = [message]
    lines += [f"resolve_item:   {candidate}" for candidate in candidates]
    lines.append(f"resolve_item: resolving to first match: {first_match}")
    return "\n".join(lines) + "\n"


def get_task_status(
    ctx: OpsContext,
    project: str,
    key: str,
    file: str = "status",
) -> TaskDocument:
    """Return the document ``show`` emits for a task or intake item.

    The one read behind ``show.sh`` and ``GET /show``. Resolves *key* through
    ``ops.resolve.resolve_item`` -- a task folder first, then ``bugs/``,
    ``priority/`` and ``requirements/``, each by exact name then unambiguous
    prefix -- and reads the item's document:

      - a task: ``status.md`` (``file="status"``) or ``README.md``
        (``file="readme"``);
      - an intake item: its own ``.md`` file (``file`` is ignored).

    An ambiguous prefix is not an error: the first match (alphabetically) is
    read and the result's ``candidates`` / ``ambiguity`` say so, the way
    show.sh has always proceeded with a warning.

    A key matching nothing live is looked up the same way in the project's
    archive (``<kanban_root>/archive/<project>/``); an item found there is
    read from the archive and the result's ``archived`` is True
    (:meth:`TaskDocument.archived_notice` is the line callers print). A
    live item always wins over an archived one.

    Args:
        ctx:     OpsContext carrying the kanban root path.
        project: Project name as registered in ``projects.cfg``.
        key:     Task folder name, intake file name, or a prefix of either.
        file:    ``"status"`` (default) or ``"readme"``.

    Returns:
        The resolved item's document (see :class:`TaskDocument`).

    Raises:
        NotFound: When nothing matches *key*, live or archived.
        OpsError: When the project root does not exist, the matched task
            folder has no status.md, *file* is not ``status``/``readme``, the
            selected task document does not exist, or it resolves outside its
            task folder (``resolve_item.path_outside_tree``; nothing is read).
        IoError:  When the document cannot be read.
    """
    from pgai_agent_kanban.ops.resolve import require_task_document_inside, resolve_item

    if file not in TASK_DOCUMENT_FILES:
        raise OpsError(
            f"get_task_status: file must be one of {', '.join(TASK_DOCUMENT_FILES)}; got: {file!r}"
        )

    candidates: tuple[pathlib.Path, ...] = ()
    ambiguity = ""
    try:
        resolved = resolve_item(ctx, project, key, include_archive=True)
    except Ambiguous as exc:
        resolved = exc.result
        candidates = tuple(exc.candidates)
        ambiguity = str(exc)

    if resolved.item_type == "task":
        document = resolved.path / TASK_DOCUMENT_FILES[file]
        if not document.is_file():
            raise OpsError(f"{file}.md not found for task {key}: {document}")
        require_task_document_inside(document, resolved.path)
    else:
        document = resolved.path

    _ensure_pm_agent_on_path(ctx)
    from pm_status import get_status_fields  # type: ignore[import-not-found]

    try:
        text = document.read_bytes().decode("utf-8", errors="replace")
        fields = get_status_fields(document)
    except OSError as exc:
        raise IoError(f"Cannot read {document} for key {key!r}: {exc}") from exc

    return TaskDocument(
        key=key,
        item_type=resolved.item_type,
        state=resolved.state,
        path=document,
        text=text,
        fields=fields,
        candidates=candidates,
        ambiguity=ambiguity,
        archived=resolved.archived,
    )


# ---------------------------------------------------------------------------
# get_release_state
# ---------------------------------------------------------------------------


# The machine key and the fallback remedy BOTH renderers of a release-state
# refusal use (team/scripts/release-state.sh and GET /release-state in
# api/reads.py) when the Refused text carries no house-shape block of its own.
# ReleaseStateValidationError always carries one, so in practice the remedy
# printed is the validator's; these two exist so that even the fallback path
# is a single string shared by the two surfaces rather than one per renderer.
RELEASE_STATE_REFUSAL_ERROR_KEY = "release-state.invalid-field"
RELEASE_STATE_REFUSAL_FALLBACK_REMEDY = (
    "Fix the invalid field(s) in release-state.md, then retry."
)


def get_release_state(ctx: OpsContext, project: str) -> dict[str, str]:
    """Return the project's release-state.md fields, validated.

    Delegates to ``cm.read_state_field.load_release_state`` — the shared,
    schema-driven validating loader — rather than reading an independently
    chosen set of headings. The four fields returned are exactly the
    shipped release-state.md schema (``team/roles/CM.md``, "Release
    state"): ``## Active RC``, ``## RC Opened At``, ``## RC Opened By
    Task``, and ``## Last Released``.

    An earlier version of this function asked for five headings — "State",
    "Release Version", "Source Branch", "Opened By", "Opened At" — that do
    not exist under those names in the shipped schema, so every one of them
    silently resolved to the sentinel "none" without anyone having written
    an absent field on purpose. Naming the real four fields here, and
    routing them through the validating loader, means a field the schema
    does not recognise (a stray "NONE", an empty value, a malformed
    timestamp or version) now surfaces as a refusal instead of a silent,
    wrong "none".

    Fields returned (keyed by heading name):
        - ``"Active RC"``
        - ``"RC Opened At"``
        - ``"RC Opened By Task"``
        - ``"Last Released"``

    A legitimately unset field (e.g. a fresh project that has never opened
    an RC) is present in the returned dict with the literal value
    ``"none"`` — that is a validated value, not an absence.

    Args:
        ctx:     OpsContext carrying the kanban root path.
        project: Project name as registered in ``projects.cfg``.

    Returns:
        Dict mapping heading name to validated value string.

    Raises:
        NotFound: When the project does not exist.
        Refused:  When release-state.md fails schema validation — a field
            holds a value the schema does not recognise (see
            ``cm.read_state_field.ReleaseStateValidationError`` for the
            per-field detail carried in the message).
    """
    from pgai_agent_kanban.cm.read_state_field import (
        ReleaseStateValidationError,
        load_release_state,
    )

    project_root = ctx.project_root(project)
    if not project_root.exists():
        raise NotFound(f"Project not found: {project!r} (expected at {project_root})")

    state_file = ctx.release_state_path(project)

    try:
        return load_release_state(str(state_file))
    except ReleaseStateValidationError as exc:
        raise Refused(str(exc)) from exc


# ---------------------------------------------------------------------------
# get_deep_verify_status
# ---------------------------------------------------------------------------


_DEEP_VERIFY_MODE_ALIASES = {"true": "weekly", "false": "off"}


def _read_deep_verify_mode_raw(
    kanban_root: "pathlib.Path", project_root: "pathlib.Path"
) -> "tuple[str, str]":
    """Return ``(raw, source)`` for the project's ``[release] deep_verify``.

    Reads the chain ``team/scripts/lib/project_paths.sh``'s
    ``pp_deep_verify_mode`` reads, first non-blank value winning:

      1. the project's own config (``project.cfg``, or a legacy
         ``PROJECT.cfg``) ``[release] deep_verify``;
      2. ``<kanban_root>/kanban.cfg [release] deep_verify``, the
         kanban-wide value.

    ``source`` is the path of the file that supplied ``raw``. Both are
    ``""`` when neither file carries a non-blank value; the caller then
    applies the built-in default. The value stays literal -- the legacy
    ``true``/``false`` aliases are resolved by the caller (see
    ``_DEEP_VERIFY_MODE_ALIASES``). Pure-Python reads by the bash
    ``read_ini`` rules, through the one chain helper the ship-posture
    resolver also uses; no shell-out.
    """
    from pgai_agent_kanban.lib import project_cfg
    from pgai_agent_kanban.overwatch.resolution_matrix import _release_key_chain

    found = project_cfg.project_cfg_file(pathlib.Path(project_root))
    return _release_key_chain(
        str(found) if found is not None else None,
        str(pathlib.Path(kanban_root) / "kanban.cfg"),
        "deep_verify",
    )


def get_deep_verify_status(ctx: OpsContext, project: str) -> dict[str, Any]:
    """Return the project's DEEP-VERIFY ledger entries and current status.

    Delegates entirely to ``pgai_agent_kanban.deep_verify.ledger`` (the
    ledger's own ``parse_records``/``latest_record`` -- "the one reader
    every other consumer calls, never re-derived", per that module's own
    docstring) for the ledger data, and to
    ``pgai_agent_kanban.deep_verify.schedule.resolve_mode`` for the
    project's configured cadence, read by ``_read_deep_verify_mode_raw``
    through the same chain as the bash ``pp_deep_verify_mode``:
    ``project.cfg [release] deep_verify``, else ``kanban.cfg [release]
    deep_verify``, else ``weekly``.

    Returns:
        A dict with three keys:
          - ``"mode"``: ``"weekly"`` or ``"off"`` -- this project's
            resolved cadence.
          - ``"entries"``: list of every ledger record (dicts with
            ``timestamp``, ``project``, ``version``, ``commit``,
            ``verdict``, ``detail``), in file (append) order. Empty list
            when the project has never been deep-verified.
          - ``"latest"``: the most recent record dict, or ``None`` when
            never run ("found=false" in the bash adapter's vocabulary).

    Args:
        ctx:     OpsContext carrying the kanban root path.
        project: Project name as registered in ``projects.cfg``.

    Returns:
        See above.

    Raises:
        NotFound: When the project does not exist.
        Refused:  When the ledger file exists but is malformed (a record
            that does not parse, or a most-recent record missing a
            required field / carrying an out-of-vocabulary verdict -- see
            ``deep_verify.ledger.LedgerError``), or when the
            ``[release] deep_verify`` value that wins the chain (in
            project.cfg or kanban.cfg) is neither ``weekly``/``off`` nor
            one of the legacy boolean aliases (see
            ``deep_verify.schedule.InvalidDeepVerifyMode``); the message
            names the file and the key.
    """
    from pgai_agent_kanban.deep_verify.ledger import (
        LedgerError,
        ledger_path_for_project,
        latest_record,
        parse_records,
    )
    from pgai_agent_kanban.deep_verify.schedule import (
        InvalidDeepVerifyMode,
        resolve_mode,
    )

    project_root = ctx.project_root(project)
    if not project_root.exists():
        raise NotFound(f"Project not found: {project!r} (expected at {project_root})")

    raw_mode, mode_source = _read_deep_verify_mode_raw(ctx.kanban_root, project_root)
    aliased_mode = _DEEP_VERIFY_MODE_ALIASES.get(raw_mode, raw_mode)
    try:
        mode = resolve_mode(aliased_mode)
    except InvalidDeepVerifyMode as exc:
        raise Refused(
            f"{exc}; read from {mode_source} [release] deep_verify "
            "(aliases 'true'/'false' are also accepted)"
        ) from exc

    ledger_path = ledger_path_for_project(str(ctx.kanban_root), project)

    try:
        entries = parse_records(ledger_path)
        latest = latest_record(ledger_path)
    except LedgerError as exc:
        raise Refused(str(exc)) from exc

    return {"mode": mode, "entries": entries, "latest": latest}


# ---------------------------------------------------------------------------
# get_attention
# ---------------------------------------------------------------------------


def get_attention(
    ctx: OpsContext,
    project: "str | None" = None,
) -> str:
    """Return a formatted attention report for the kanban or a named project.

    Delegates to ``dashboard.scan_attention.scan_blocked_tasks`` and related
    scanners.  Captures their stdout output and returns it as a single string.

    The returned string contains sections for:
      - Blocked tasks (tasks with ``## State: BLOCKED`` and ``Needs Human: yes``).
      - Transient-blocked tasks (auto-retry blocks).
      - Quarantine alerts (files approaching or already quarantined).

    Args:
        ctx:     OpsContext carrying the kanban root path.
        project: Project name to scope the scan.  When ``None``, all
                 projects under ``ctx.kanban_root`` are scanned.

    Returns:
        Multi-line string containing the formatted attention report.
        An empty string when no attention items are found and nothing is printed.

    Raises:
        NotFound: When ``project`` is given but does not exist.
    """
    from pgai_agent_kanban.dashboard.scan_attention import (
        scan_blocked_tasks,
        scan_quarantine,
        scan_transient_tasks,
    )

    if project is not None:
        project_root = ctx.project_root(project)
        if not project_root.exists():
            raise NotFound(
                f"Project not found: {project!r} (expected at {project_root})"
            )
        tasks_root = str(ctx.tasks_dir(project))
        kanban_root = str(ctx.kanban_root)
    else:
        # Scan all projects — use the first project's tasks dir for blocked/transient,
        # and kanban_root for quarantine.  For a global scan covering multiple projects,
        # aggregate across all registered projects.
        tasks_root = str(ctx.kanban_root / "tasks")  # legacy single-project layout
        kanban_root = str(ctx.kanban_root)

    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        scan_blocked_tasks(tasks_root=tasks_root, use_color=False)
        scan_transient_tasks(tasks_root=tasks_root, use_color=False)
        scan_quarantine(kanban_root=kanban_root, use_color=False, threshold=3)
    finally:
        sys.stdout = old_stdout

    return buf.getvalue()


# ---------------------------------------------------------------------------
# get_metrics
# ---------------------------------------------------------------------------


def get_metrics(
    ctx: OpsContext,
    project: str,
    last_n: int = 10,
) -> str:
    """Return a formatted agent wake-time metrics table for a named project.

    Delegates to ``reports.agent_timing`` functions: collects wake log files,
    parses them, aggregates by (agent, project), and renders a plain-text
    table.  The ``last_n`` parameter controls how many log entries (by count)
    are included; when ``last_n <= 0`` all available log entries are returned.

    Reads from ``ctx.kanban_root/logs/`` — the standard location for wake
    batch logs.

    Args:
        ctx:    OpsContext carrying the kanban root path.
        project: Project name to filter metrics for.
        last_n: Maximum number of most-recent log entries to include.
                Defaults to 10.  Pass 0 for all entries.

    Returns:
        Formatted multi-line string ready to display, as produced by
        ``reports.agent_timing.render_table``.

    Raises:
        NotFound: When the project does not exist.
    """
    from pgai_agent_kanban.reports.agent_timing import (
        aggregate,
        collect_log_files,
        parse_log_file,
        render_table,
    )

    project_root = ctx.project_root(project)
    if not project_root.exists():
        raise NotFound(f"Project not found: {project!r} (expected at {project_root})")

    logs_dir = ctx.kanban_root / "logs"

    if not logs_dir.is_dir():
        return render_table({}, f"project={project}")

    log_files = collect_log_files(logs_dir)
    if not log_files:
        return render_table({}, f"project={project}")

    all_entries = []
    for lf in log_files:
        all_entries.extend(parse_log_file(lf, cutoff=None))

    # Filter to this project's entries.
    project_entries = [e for e in all_entries if e.project == project]

    # Apply last_n limit on entry count (most-recent entries by timestamp).
    if last_n > 0 and len(project_entries) > last_n:
        project_entries.sort(key=lambda e: e.ts)
        project_entries = project_entries[-last_n:]

    buckets = aggregate(project_entries)
    window_label = (
        f"project={project}, last {last_n} entries"
        if last_n > 0
        else f"project={project}, all entries"
    )
    return render_table(buckets, window_label)


# ---------------------------------------------------------------------------
# get_next_firings
# ---------------------------------------------------------------------------


def get_next_firings(ctx: OpsContext) -> dict[str, str]:
    """Return the next scheduled cron firing times for each agent.

    Delegates to ``dashboard.cron_firings.cron_firings``.  Reads the
    kanban's pseudocron schedule file (``pseudocron.cfg``) at
    ``ctx.kanban_root`` to obtain the crontab text.  Returns an empty dict
    when no schedule file is found, matching the graceful-degradation
    behavior of the bash dashboard scripts.

    Note: reading the system crontab (via ``crontab -l``) is only available
    in bash today — that path is a Class-A follow-up.  This facade reads
    ``pseudocron.cfg`` as the pure-Python alternative.

    Args:
        ctx: OpsContext carrying the kanban root path.

    Returns:
        Dict mapping agent name (str) to a human-readable firing label such
        as ``"in 6 min"``, ``"in 1 min"``, ``"now"``, or ``"Sun 4am"``.
        Empty dict when no schedule file is found or parsing fails.
    """
    from pgai_agent_kanban.dashboard.cron_firings import cron_firings

    # Locate the pseudocron.cfg schedule file at the kanban root.
    pseudocron_cfg = ctx.kanban_root / "pseudocron.cfg"
    if not pseudocron_cfg.is_file():
        # No schedule file present — degrade gracefully.
        return {}

    try:
        crontab_text = pseudocron_cfg.read_text(encoding="utf-8")
    except OSError:
        return {}

    if not crontab_text.strip():
        return {}

    # Locate cron_parser.py relative to this module's package root.
    # team/pgai_agent_kanban/ops/read.py -> team/ -> pm-agent/lib/cron_parser.py
    _pkg_root = pathlib.Path(__file__).parent.parent.parent  # team/
    cron_parser_path = _pkg_root / "pm-agent" / "lib" / "cron_parser.py"

    if not cron_parser_path.is_file():
        return {}

    return cron_firings(
        crontab_text=crontab_text,
        cron_parser_path=str(cron_parser_path),
    )


# ---------------------------------------------------------------------------
# Script-shaped reads: status summary, test report, task README, rejected
# ---------------------------------------------------------------------------
#
# Each read below has a data function (get_status, get_test_report,
# get_task_readme, list_rejected) and a render function returning the
# ReadOutput its CLI script prints -- kanban-status.sh, show-test-report.sh,
# show-readme.sh and list-rejected.sh reach the render function through
# pp_run_ops, and GET /status, /test-report, /projects/{p}/tasks/{k}/readme
# and /rejected call it in-process. A refusal is a CliRefusal carrying the
# exact stderr text and exit code the script has always produced; those
# texts name the script, because they are what the script prints.


class ReadOutput(NamedTuple):
    """What a script-shaped read prints and exits with.

    Attributes:
        exit_code: The script's exit code.
        stdout:    What it prints to stdout.
        stderr:    What it prints to stderr.
    """

    exit_code: int
    stdout: str
    stderr: str


def _read_text(path: pathlib.Path) -> str:
    """*path* decoded as UTF-8 (undecodable bytes replaced), line endings as written."""
    return path.read_bytes().decode("utf-8", errors="replace")


def _resolve_for_script(
    ctx: OpsContext, project: str, key: str, script: str
) -> "tuple[Any, str, int]":
    """Resolve *key* the way the resolve CLI the read scripts called reports it.

    Returns ``(result, notice, code)``: the resolved item, the ambiguity
    notice (``""`` unless the key was an ambiguous prefix) and ``2`` for an
    ambiguous prefix, else ``0``.

    Raises:
        CliRefusal: the project directory does not exist (exit 1); nothing
            matches *key* (exit 3, the resolver's message then
            ``<script>: key not found: <key>``); a matched task folder has no
            status.md (exit 1).
    """
    from pgai_agent_kanban.ops.resolve import resolve_item

    project_root = ctx.project_root(project)
    if not project_root.is_dir():
        raise CliRefusal(
            "resolve_item.project_root_missing",
            f"resolve_item: PROJECT_ROOT does not exist or is not a directory: {project_root}\n",
        )
    try:
        return resolve_item(ctx, project, key), "", 0
    except Ambiguous as exc:
        return exc.result, _ambiguity_notice(str(exc), exc.candidates, exc.result.path), 2
    except NotFound as exc:
        raise CliRefusal(
            "resolve_item.not_found", f"{exc}\n{script}: key not found: {key}\n", exit_code=3
        ) from exc
    except OpsError as exc:
        raise CliRefusal("resolve_item.failed", f"{exc}\n") from exc


def _script_output(render: "Any") -> ReadOutput:
    """Run *render* (returning ``(stdout, stderr, exit_code)``); a CliRefusal becomes its ReadOutput."""
    try:
        stdout, stderr, exit_code = render()
    except CliRefusal as exc:
        return ReadOutput(exit_code=exc.exit_code, stdout="", stderr=exc.text)
    return ReadOutput(exit_code=exit_code, stdout=stdout, stderr=stderr)


# --- status summary --------------------------------------------------------

_RELEASE_VERSION_RE = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+")

#: Directories under tasks/ that are never tasks.
_NON_TASK_DIRS = frozenset({"archive", "queues", "plans"})


def _blocked_task_ids(tasks_dir: pathlib.Path) -> tuple[str, ...]:
    """Task ids under *tasks_dir* whose ``## State`` first line is exactly ``BLOCKED``.

    Every ``status.md`` at most one directory below *tasks_dir* (symlinked
    directories are not entered), in path order; the id is the directory
    holding it, and ``archive``, ``queues`` and ``plans`` are skipped.
    """
    from pgai_agent_kanban.task_status import read_section

    candidates = [tasks_dir / "status.md"]
    try:
        with os.scandir(tasks_dir) as entries:
            candidates += [
                pathlib.Path(entry.path) / "status.md"
                for entry in entries
                if entry.is_dir(follow_symlinks=False)
            ]
    except OSError:
        return ()
    blocked: list[str] = []
    for status_file in sorted(candidates, key=str):
        task_id = status_file.parent.name
        if task_id in _NON_TASK_DIRS or not status_file.is_file():
            continue
        try:
            body = read_section(status_file.read_text(encoding="utf-8", errors="replace"), "State")
        except OSError:
            continue
        first = next((line.strip() for line in (body or "").splitlines() if line.strip()), "")
        if first == "BLOCKED":
            blocked.append(task_id)
    return tuple(blocked)


def get_status(ctx: OpsContext, project: str) -> Any:
    """Return what the status summary shows for *project*.

    The one read behind ``kanban-status.sh`` and ``GET /status``. Gathers:

      - the framework version (``lib.released_version.kanban_version``) and
        the project's last released version
        (``lib.released_version.last_released_version``, ``none`` when
        empty);
      - ``Active RC`` (whitespace removed; anything but ``vX.Y.Z`` reads as
        ``none``) and ``RC Opened At`` from the project's release-state.md,
        through ``cm.read_state_field``;
      - the halt flag (``dashboard.halt_state.halt_flag_and_text``, the
        project's sentinels with the kanban-wide HALT as fallback);
      - the task counts (``dashboard.task_counts.scan_task_counts``) and the
        tasks whose State is exactly ``BLOCKED``;
      - the number of files quarantined in the project's ``rejected/``
        (``dashboard._rejected.count_quarantined``);
      - the resolved ship posture, ``matrix`` or ``always``
        (``overwatch.resolution_matrix.resolve_ship_posture``, remote-lane
        guard applied). Any warning line the resolver writes is kept on the
        snapshot (``ship_posture_notice``) rather than written to stderr, so
        the CLI and the REST envelope carry it alike.

    A project with no directory reads as empty: zero counts, no RC,
    ``v0.0.0`` released, the posture from kanban.cfg alone. Nothing is
    written.

    Args:
        ctx:     OpsContext carrying the kanban root path.
        project: Project name.

    Returns:
        ``dashboard.status_summary.StatusSnapshot``.

    Raises:
        CliRefusal: *project* is empty (exit 1).
    """
    from pgai_agent_kanban.cm.read_state_field import read_state_field
    from pgai_agent_kanban.dashboard._rejected import count_quarantined
    from pgai_agent_kanban.dashboard.halt_state import halt_flag_and_text
    from pgai_agent_kanban.dashboard.status_summary import StatusSnapshot
    from pgai_agent_kanban.dashboard.task_counts import scan_task_counts
    from pgai_agent_kanban.lib.released_version import kanban_version, last_released_version
    from pgai_agent_kanban.overwatch.resolution_matrix import resolve_ship_posture

    if not project:
        raise CliRefusal(
            "kanban_status.no_project",
            "ERROR: no project specified; pass --project <name> or set PGAI_PROJECT_NAME\n",
        )

    release_state = str(ctx.release_state_path(project))
    active_rc = "".join(read_state_field(release_state, "Active RC").split())
    halt_flag, _halt_text = halt_flag_and_text(ctx.project_root(project), ctx.kanban_root)
    tasks_dir = ctx.tasks_dir(project)
    posture_notice = io.StringIO()
    ship_posture = resolve_ship_posture(project, str(ctx.kanban_root), stream=posture_notice)
    return StatusSnapshot(
        project=project,
        kanban_version=kanban_version(ctx.kanban_root),
        active_rc=active_rc if _RELEASE_VERSION_RE.fullmatch(active_rc) else "none",
        rc_opened_at=read_state_field(release_state, "RC Opened At"),
        last_released=last_released_version(ctx.kanban_root, project) or "none",
        halt_flag=halt_flag,
        counts=scan_task_counts(tasks_dir),
        blocked_tasks=_blocked_task_ids(tasks_dir),
        quarantined=count_quarantined(ctx.rejected_dir(project)),
        ship_posture=ship_posture,
        ship_posture_notice=posture_notice.getvalue(),
    )


class StatusRead(NamedTuple):
    """One status read: what the script prints, and the snapshot it rendered.

    Attributes:
        output:   The :class:`ReadOutput` ``kanban-status.sh`` prints.
        snapshot: The ``dashboard.status_summary.StatusSnapshot``
                  :func:`get_status` returned, or ``None`` when the read was
                  refused (``output`` then carries the refusal).
    """

    output: ReadOutput
    snapshot: Any


def read_status(
    ctx: OpsContext,
    project: str,
    *,
    no_color: bool = False,
    environ: "Any" = None,
    now: "int | None" = None,
) -> StatusRead:
    """Read *project*'s status once and render it, keeping the snapshot.

    :func:`render_status` is this function's ``output``; ``GET /status``
    uses both halves, so the summary text and the structured fields it
    returns beside it come from the same :func:`get_status` call. The
    resolver's warning lines (``StatusSnapshot.ship_posture_notice``) are
    the output's stderr; the exit code stays 0.

    Args:
        ctx:      OpsContext carrying the kanban root path.
        project:  Project name (``""`` is refused, exit 1).
        no_color: ``--no-color`` was given.
        environ:  Where ``NO_COLOR``, ``TERM`` and the
                  ``PGAI_DASHBOARD_COLOR_*`` names are read (default: this
                  process's environment).
        now:      The current time in epoch seconds (default: now).

    Returns:
        :class:`StatusRead`.
    """
    from pgai_agent_kanban.dashboard.status_summary import render_status_summary
    from pgai_agent_kanban.lib.metrics.common import color_enabled

    env = os.environ if environ is None else environ
    read: list[Any] = []

    def _render() -> "tuple[str, str, int]":
        snapshot = get_status(ctx, project)
        read.append(snapshot)
        text = render_status_summary(
            snapshot,
            use_color=color_enabled(no_color, env),
            now=int(time.time()) if now is None else now,
            environ=env,
        )
        return text, snapshot.ship_posture_notice, 0

    output = _script_output(_render)
    return StatusRead(output=output, snapshot=read[0] if read else None)


def render_status(
    ctx: OpsContext,
    project: str,
    *,
    no_color: bool = False,
    environ: "Any" = None,
    now: "int | None" = None,
) -> ReadOutput:
    """Return what ``kanban-status.sh`` prints for *project*, over :func:`get_status`.

    Args:
        ctx:      OpsContext carrying the kanban root path.
        project:  Project name (``""`` is refused, exit 1).
        no_color: ``--no-color`` was given.
        environ:  Where ``NO_COLOR``, ``TERM`` and the
                  ``PGAI_DASHBOARD_COLOR_*`` names are read (default: this
                  process's environment).
        now:      The current time in epoch seconds (default: now).

    Returns:
        The summary on stdout and exit 0 (any ship-posture warning on
        stderr), or the refusal. See :func:`read_status`.
    """
    return read_status(ctx, project, no_color=no_color, environ=environ, now=now).output


# --- TESTER report ---------------------------------------------------------

_RC_VERSION_KEY_RE = re.compile(r"v([0-9]+)\.([0-9]+)\.([0-9]+)")


class VerificationReport(NamedTuple):
    """A TESTER task's verification report, as ``show-test-report.sh`` emits it.

    Attributes:
        task:      The TESTER task folder name.
        path:      Its ``artifacts/report.md``.
        text:      The report, decoded as UTF-8 with line endings kept.
        notice:    The resolver's ambiguity notice, or ``""``.
        exit_code: ``2`` when the key was an ambiguous prefix, else ``0``.
    """

    task: str
    path: pathlib.Path
    text: str
    notice: str
    exit_code: int


def _report_of(task_dir: pathlib.Path, notice: str = "") -> pathlib.Path:
    """``<task_dir>/artifacts/report.md``, confined to *task_dir*.

    Refused (exit 3) when absent, and refused with
    ``resolve_item.path_outside_tree`` (exit 1, nothing read) when the
    report resolves outside its task folder -- a ``report.md`` that is a
    symlink to a file elsewhere is never opened or returned, the same rule
    the status and README reads apply.
    """
    report = task_dir / "artifacts" / "report.md"
    if not report.is_file():
        raise CliRefusal(
            "show_test_report.report_missing",
            f"{notice}show-test-report.sh: report not found for task {task_dir.name}: {report}\n",
            exit_code=3,
        )
    from pgai_agent_kanban.ops.resolve import require_task_document_inside

    try:
        require_task_document_inside(report, task_dir)
    except OpsError as exc:
        # BLOCK-WORTHY: an invariant by design -- a report read only ever
        # returns a file inside its own task folder, so a symlink out of the
        # tree is refused before anything is opened.
        # CENSUS-ANCHOR: ops-read-test-report-outside-tree-01
        raise CliRefusal("resolve_item.path_outside_tree", f"{notice}{exc}\n") from exc
    return report


def get_test_report(ctx: OpsContext, project: str, key: str) -> VerificationReport:
    """Return the TESTER report *key* names in *project*.

    The one read behind ``show-test-report.sh`` and ``GET /test-report``.

      - A ``vX.Y.Z`` key selects, among the folders under the project's
        ``tasks/`` named ``TESTER-...`` and containing ``-verify-X-Y-Z``,
        the greatest name (latest date, then highest sequence).
      - Any other key resolves through ``ops.resolve.resolve_item`` (exact,
        then unambiguous prefix); it must name a task whose folder starts
        ``TESTER-``. An ambiguous prefix reads the first match and reports
        exit code 2 with the resolver's notice.

    Either way the report is the task's ``artifacts/report.md``.

    Raises:
        CliRefusal: carrying show-test-report.sh's own stderr and exit code
            -- 3 for a missing tasks/ directory, no TESTER task for the
            version, a key matching nothing, a key that is not a task or
            not a TESTER task, or a missing report.md; 1 for a project
            directory that does not exist (key path) or a matched task
            folder without status.md.
    """
    match = _RC_VERSION_KEY_RE.fullmatch(key)
    if match:
        tasks_dir = ctx.project_root(project) / "tasks"
        if not tasks_dir.is_dir():
            raise CliRefusal(
                "show_test_report.tasks_dir_missing",
                f"show-test-report.sh: tasks directory not found: {tasks_dir}\n",
                exit_code=3,
            )
        verify = f"-verify-{match.group(1)}-{match.group(2)}-{match.group(3)}"
        with os.scandir(tasks_dir) as entries:
            matching = [
                entry.name
                for entry in entries
                if entry.is_dir(follow_symlinks=False)
                and entry.name.startswith("TESTER-")
                and verify in entry.name
            ]
        if not matching:
            raise CliRefusal(
                "show_test_report.no_tester_task",
                f"show-test-report.sh: no TESTER task found for RC version: {key}\n",
                exit_code=3,
            )
        latest = max(matching)
        report = _report_of(tasks_dir / latest)
        return VerificationReport(task=latest, path=report, text=_read_text(report), notice="", exit_code=0)

    resolved, notice, code = _resolve_for_script(ctx, project, key, "show-test-report.sh")
    if resolved.item_type != "task":
        raise CliRefusal(
            "show_test_report.not_a_task",
            f"{notice}show-test-report.sh: key does not resolve to a task: {key}\n",
            exit_code=3,
        )
    task = resolved.path.name
    if not task.startswith("TESTER-"):
        raise CliRefusal(
            "show_test_report.not_a_tester_task",
            f"{notice}show-test-report.sh: resolved task is not a TESTER task: {task}\n",
            exit_code=3,
        )
    report = _report_of(resolved.path, notice)
    return VerificationReport(task=task, path=report, text=_read_text(report), notice=notice, exit_code=code)


def render_test_report(ctx: OpsContext, project: str, key: str) -> ReadOutput:
    """Return what ``show-test-report.sh --project P --key K`` prints, over :func:`get_test_report`."""

    def _render() -> "tuple[str, str, int]":
        report = get_test_report(ctx, project, key)
        return report.text, report.notice, report.exit_code

    return _script_output(_render)


# --- task README -----------------------------------------------------------


class TaskReadme(NamedTuple):
    """A task's README.md, as ``show-readme.sh`` emits it.

    Attributes:
        path:   The README.md file.
        text:   Its content, decoded as UTF-8 with line endings kept.
        notice: The resolver's ambiguity notice, or ``""``.
    """

    path: pathlib.Path
    text: str
    notice: str


def get_task_readme(ctx: OpsContext, project: str, key: str) -> TaskReadme:
    """Return the README.md of the task *key* names in *project*.

    The one read behind ``show-readme.sh`` and
    ``GET /projects/{project}/tasks/{key}/readme``. *key* resolves through
    ``ops.resolve.resolve_item`` (exact, then unambiguous prefix; an
    ambiguous prefix reads the first match and carries the resolver's
    notice). READMEs are task artifacts, so a key naming a bug, priority or
    requirement item is refused.

    Raises:
        CliRefusal: carrying show-readme.sh's own stderr and exit code -- 3
            for a key matching nothing; 1 for a project directory that does
            not exist, a ledger item, a task folder without status.md, a
            task without README.md, or a status.md or README.md that resolves
            outside its task folder (nothing is read from it).
    """
    resolved, notice, _code = _resolve_for_script(ctx, project, key, "show-readme.sh")
    if resolved.item_type != "task":
        raise CliRefusal(
            "show_readme.not_a_task",
            f"{notice}show-readme.sh: {key} is a {resolved.item_type} item, not a task "
            "— READMEs are task artifacts.\n"
            f"show.sh already prints {key}'s document: show.sh --project {project} --key {key}\n",
        )
    readme = resolved.path / "README.md"
    if not readme.is_file():
        raise CliRefusal(
            "show_readme.readme_missing",
            f"{notice}show-readme.sh: README.md not found for task {key}: {readme}\n",
        )
    from pgai_agent_kanban.ops.resolve import require_task_document_inside

    try:
        require_task_document_inside(readme, resolved.path)
    except OpsError as exc:
        raise CliRefusal("resolve_item.path_outside_tree", f"{notice}{exc}\n") from exc
    return TaskReadme(path=readme, text=_read_text(readme), notice=notice)


def render_task_readme(ctx: OpsContext, project: str, key: str) -> ReadOutput:
    """Return what ``show-readme.sh --project P --key K`` prints, over :func:`get_task_readme`."""

    def _render() -> "tuple[str, str, int]":
        readme = get_task_readme(ctx, project, key)
        return readme.text, readme.notice, 0

    return _script_output(_render)


# --- quarantined intake files ------------------------------------------------


def _quarantine_timestamp(path: pathlib.Path) -> str:
    """*path*'s mtime as local ``YYYY-MM-DDTHH:MM``, or ``unknown``."""
    import datetime

    try:
        mtime = path.stat().st_mtime
    except OSError:
        return "unknown"
    return datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%dT%H:%M")


def list_rejected(ctx: OpsContext, project: "str | None" = None) -> list[Any]:
    """Return the intake files discovery quarantined, per project and directory.

    The one read behind ``list-rejected.sh`` and ``GET /rejected``. For each
    project directory under ``projects/`` (every non-hidden one, in name
    order, or only *project*): the files quarantined in its ``rejected/``
    directory and in the legacy ``<intake>/.rejected/`` directories
    (``dashboard._rejected.quarantined_files`` -- regular, non-hidden files,
    ``.reason`` sidecars excluded), grouped into one section per intake
    directory they came from -- ``priority``, ``bugs``, ``requirements``,
    then ``rejected`` for files of unknown origin -- each in name order,
    with their quarantine time and reason
    (``dashboard.rejected_listing.rejection_reason``). An origin with no
    such file contributes no section.

    Args:
        ctx:     OpsContext carrying the kanban root path.
        project: One project name, or ``None`` / ``""`` for every project.

    Returns:
        ``dashboard.rejected_listing.RejectedSection`` items in display order.

    Raises:
        CliRefusal: the kanban root has no ``projects/`` directory, or
            *project* names no directory under it (exit 1).
    """
    from pgai_agent_kanban.dashboard._rejected import INTAKE_DIRS, UNKNOWN_ORIGIN, quarantined_files
    from pgai_agent_kanban.dashboard.rejected_listing import (
        RejectedFile,
        RejectedSection,
        rejection_reason,
    )

    projects_dir = ctx.kanban_root / "projects"
    if not projects_dir.is_dir():
        raise CliRefusal(
            "list_rejected.no_projects_dir",
            "ERROR: no project specified and none resolvable from projects.cfg\n"
            f"       projects/ directory not found under {ctx.kanban_root}.\n"
            "       Create ${KANBAN_ROOT}/projects/<name>/ or migrate to the multi-project layout.\n",
        )
    if project:
        if not (projects_dir / project).is_dir():
            raise CliRefusal(
                "list_rejected.project_not_found",
                f"list-rejected.sh: error: project '{project}' not found under {projects_dir}\n",
            )
        names = [project]
    else:
        names = sorted(
            entry.name for entry in projects_dir.iterdir() if entry.is_dir() and not entry.name.startswith(".")
        )

    sections = []
    for name in names:
        by_origin: dict[str, list[pathlib.Path]] = {}
        for quarantined in quarantined_files(ctx.project_root(name), ctx.rejected_dir(name)):
            by_origin.setdefault(quarantined.origin, []).append(quarantined.path)
        for directory in (*INTAKE_DIRS, UNKNOWN_ORIGIN):
            paths = sorted(by_origin.get(directory, ()), key=lambda path: path.name)
            if paths:
                sections.append(RejectedSection(
                    project=name,
                    directory=directory,
                    files=tuple(
                        RejectedFile(path.name, _quarantine_timestamp(path), rejection_reason(path.name))
                        for path in paths
                    ),
                ))
    return sections


def render_rejected(ctx: OpsContext, project: "str | None" = None, *, environ: "Any" = None) -> ReadOutput:
    """Return what ``list-rejected.sh [--project P]`` prints, over :func:`list_rejected`.

    Colour follows ``dashboard.rejected_listing.listing_color_enabled`` on
    *environ* (default: this process's environment).
    """
    from pgai_agent_kanban.dashboard.rejected_listing import listing_color_enabled, render_rejected_listing

    env = os.environ if environ is None else environ

    def _render() -> "tuple[str, str, int]":
        sections = list_rejected(ctx, project)
        return render_rejected_listing(sections, use_color=listing_color_enabled(env)), "", 0

    return _script_output(_render)


# ---------------------------------------------------------------------------
# CLI (bash wrapper delegation)
# ---------------------------------------------------------------------------

#: Verbs ``python3 -m pgai_agent_kanban.ops`` routes to :func:`_cli_main`.
CLI_VERBS: tuple[str, ...] = (
    "get_task_status",
    "get_queues",
    "get_status",
    "get_test_report",
    "get_task_readme",
    "list_rejected",
)

_CLI_USAGE = (
    "  verbs: get_task_status KANBAN_ROOT PROJECT KEY [status|readme]\n"
    "         get_queues KANBAN_ROOT PROJECT DATA_PROJECT DETAILS\n"
    "         get_status KANBAN_ROOT PROJECT NO_COLOR\n"
    "         get_test_report KANBAN_ROOT PROJECT KEY\n"
    "         get_task_readme KANBAN_ROOT PROJECT KEY\n"
    "         list_rejected KANBAN_ROOT [PROJECT]"
)

#: Script-shaped verbs: name -> (accepted argument counts, ReadOutput producer).
_SCRIPT_VERBS: "dict[str, tuple[tuple[int, ...], Any]]" = {
    "get_status": ((3,), lambda ctx, a: render_status(ctx, a[1], no_color=a[2] == "1")),
    "get_test_report": ((3,), lambda ctx, a: render_test_report(ctx, a[1], a[2])),
    "get_task_readme": ((3,), lambda ctx, a: render_task_readme(ctx, a[1], a[2])),
    "list_rejected": ((1, 2), lambda ctx, a: render_rejected(ctx, a[1] if len(a) == 2 else "")),
}


def _write_stdout(text: str) -> None:
    """Write *text* to stdout as UTF-8, whatever the locale's encoding."""
    sys.stdout.flush()
    sys.stdout.buffer.write(text.encode("utf-8"))
    sys.stdout.buffer.flush()


def _cli_main(argv: "list[str] | None" = None) -> int:
    """CLI for the read verbs bash wrappers reach through ``pp_run_ops``.

    Invoked as ``python3 -m pgai_agent_kanban.ops <verb> [args...]``
    (``ops/__main__.py`` routes the verbs in :data:`CLI_VERBS` here):

    ``get_task_status KANBAN_ROOT PROJECT KEY [FILE]``
        Prints the document :func:`get_task_status` returns (FILE is
        ``status``, the default, or ``readme``) -- the output of
        ``show.sh``. An ambiguous key prints the resolver's notice to
        stderr first and still exits 0; an item read from the project's
        archive prints the ``archived:`` line to stderr first.

    ``get_queues KANBAN_ROOT PROJECT DATA_PROJECT DETAILS``
        Prints the queue pane (``dashboard.queue_pane``) over
        :func:`get_queues` results -- the output of ``show-queues.sh``.
        PROJECT is the resolved ``--project`` or ``""``; DATA_PROJECT is
        PROJECT, else the caller's ``PGAI_PROJECT_NAME``, else ``""``;
        DETAILS is ``1`` for the per-agent listing, anything else for the
        compact rows. Compact rows with an empty DATA_PROJECT are refused
        (``show_queues.no_project``) and nothing reaches stdout. Colours
        and pane width come from the environment.

    ``get_status KANBAN_ROOT PROJECT NO_COLOR``
        Prints the status summary :func:`render_status` renders -- the
        output of ``kanban-status.sh``. NO_COLOR is ``1`` for
        ``--no-color``.

    ``get_test_report KANBAN_ROOT PROJECT KEY``
        Prints the TESTER report :func:`get_test_report` reads -- the
        output of ``show-test-report.sh``.

    ``get_task_readme KANBAN_ROOT PROJECT KEY``
        Prints the task README :func:`get_task_readme` reads -- the output
        of ``show-readme.sh``.

    ``list_rejected KANBAN_ROOT [PROJECT]``
        Prints the quarantine inventory :func:`render_rejected` renders --
        the output of ``list-rejected.sh``; an empty or absent PROJECT
        lists every project.

    The four script-shaped verbs print their stderr, then their stdout,
    and exit with the code their script has always exited with.

    Exit codes: 0 success; 1 usage error or a generic OpsError; otherwise
    ``ops.errors.cli_exit_code`` for the error raised (NotFound 3,
    IoError 4). The error message goes to stderr.
    """
    args = sys.argv[1:] if argv is None else argv
    verb, rest = (args[0], args[1:]) if args else ("", [])

    try:
        if verb == "get_task_status":
            if len(rest) not in (3, 4):
                print(f"read get_task_status: KANBAN_ROOT, PROJECT and KEY arguments are required\n{_CLI_USAGE}",
                      file=sys.stderr)
                return 1
            ctx = OpsContext(kanban_root=pathlib.Path(rest[0]))
            document = get_task_status(ctx, rest[1], rest[2], rest[3] if len(rest) == 4 else "status")
            sys.stderr.write(document.ambiguity_notice())
            sys.stderr.write(document.archived_notice())
            _write_stdout(document.text)

        elif verb == "get_queues":
            if len(rest) != 4:
                print(f"read get_queues: KANBAN_ROOT, PROJECT, DATA_PROJECT and DETAILS arguments are required\n"
                      f"{_CLI_USAGE}", file=sys.stderr)
                return 1
            ctx = OpsContext(kanban_root=pathlib.Path(rest[0]))
            pane = render_queues(ctx, rest[1], rest[2], rest[3] == "1")
            sys.stderr.write(pane.stderr)
            _write_stdout(pane.stdout)

        elif verb in _SCRIPT_VERBS:
            counts, produce = _SCRIPT_VERBS[verb]
            if len(rest) not in counts:
                print(f"read {verb}: wrong number of arguments\n{_CLI_USAGE}", file=sys.stderr)
                return 1
            output = produce(OpsContext(kanban_root=pathlib.Path(rest[0])), rest)
            sys.stderr.write(output.stderr)
            sys.stderr.flush()
            _write_stdout(output.stdout)
            return output.exit_code

        else:
            print(f"read: unknown verb {verb!r}\n{_CLI_USAGE}", file=sys.stderr)
            return 1

    except OpsError as exc:
        print(exc, file=sys.stderr)
        return cli_exit_code(exc)

    return 0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _ensure_pm_agent_on_path(ctx: OpsContext) -> None:
    """Add the pm-agent directory to sys.path so pm_status is importable.

    ``pm_status.py`` lives in ``team/pm-agent/`` which is not a Python
    package directory — it uses an import of ``lib.config`` via a local
    sys.path manipulation.  We locate it relative to this file's package
    root so the import works regardless of the caller's working directory.

    This is called lazily (inside the functions that need it) so that the
    module import cost is deferred.
    """
    _pkg_root = pathlib.Path(__file__).parent.parent.parent  # team/
    pm_agent_dir = str(_pkg_root / "pm-agent")
    if pm_agent_dir not in sys.path:
        sys.path.insert(0, pm_agent_dir)
