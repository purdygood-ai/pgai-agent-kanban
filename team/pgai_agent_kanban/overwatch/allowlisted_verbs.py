"""
allowlisted_verbs.py — the operator data actions OVERWATCH's block triage may
run for an ``agent_operator_data_action`` block, and the checks each one must
pass before it runs.

An agent that cannot finish without an operator data action blocks with
``--reason-code agent_operator_data_action`` and typed ``--action-args``
(``verb=<name> <arg>=<value> ...``); the status-writer tool validates them
against :data:`pgai_agent_kanban.overwatch.block_record.ACTION_VERBS` — the
allowlist of verbs and their typed-argument validators — and stores them in
the Block Record's ``evidence`` line. This module is the other half of that
allowlist: for each verb it names the entry point the verb stands for, the
checks on the things the arguments point at, and how the verb is run.

Every value read from a Block Record is untrusted. Before anything runs:

1. :func:`validated_args` re-validates the record's arguments with
   :func:`~pgai_agent_kanban.overwatch.block_record.parse_action_args`: a
   record that names no verb (a prose-only remedy), a verb that is not
   allowlisted, a verb with no runner here, a missing or extra argument, or
   a value of the wrong shape is refused.
2. :func:`target_problems` checks what the arguments point at; any problem
   refuses.
3. :func:`run_allowlisted_verb` repeats both checks immediately before the
   run, so a target that changed since planning is never acted on.

A verb is run in-process through the Python body its entry point reaches,
with each typed argument passed as its own value. No shell, no command
string and no PATH lookup is involved, so no argument can be read as
anything but the value it is.

The allowlist
-------------
``restamp-fixed-in`` (entry point ``restamp-fixed-in.sh``)
    Arguments: ``project`` (a project name), ``version`` (``vX.Y.Z``) and
    ``bugs`` (comma-separated ``BUG-NNNN`` ids). Targets: the project is
    registered in projects.cfg by its exact name (when projects.cfg exists)
    and has a ``bugs/`` directory; every bug id resolves to exactly one bug
    file there, and every one of those bugs has ``## Status`` done. Run: the
    script's own body, :mod:`pgai_agent_kanban.cm.fixed_in_restamp`
    (``resolve_bug_files`` then ``restamp``), which stamps ``## Fixed In``
    on each named bug whose value is empty and never overwrites a value
    already set. Changes are the stamped (and replaced) bugs, so a re-run
    of a completed restamp reports 0 changes.

Adding a verb means adding its typed arguments to ``ACTION_VERBS`` and its
:class:`AllowlistedVerb` to :data:`VERBS` together; a verb in one and not
the other is never run.

Usage (import):
    from pgai_agent_kanban.overwatch.allowlisted_verbs import (
        VerbRefused, validated_args, target_problems, run_allowlisted_verb,
    )
    args = validated_args(record)                  # raises VerbRefused
    problems = target_problems(kanban_root, args)  # [] when every target checks out
    run = run_allowlisted_verb(kanban_root, args)  # raises VerbRefused
    run.changes, run.summary
"""

from __future__ import annotations

import dataclasses
import pathlib
import sys
from pathlib import Path
from typing import Callable, Mapping

# Run by file path (``python3 .../allowlisted_verbs.py``) or loaded from a
# file outside its package, this module has no parent package and the
# interpreter puts only its own directory on sys.path, so the package imports
# below would not resolve. Put this file's own package root -- the directory
# holding the pgai_agent_kanban/ this file sits in -- at the front of sys.path
# so the imports resolve from the same tree, never another. Imported as a
# package member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.cm.changelog_writer import parse_bug_file  # noqa: E402
from pgai_agent_kanban.cm.fixed_in_restamp import (  # noqa: E402
    RestampRefused,
    normalize_version,
    resolve_bug_files,
    restamp,
    summary_line,
)
from pgai_agent_kanban.lib import ini  # noqa: E402
from pgai_agent_kanban.overwatch.block_record import (  # noqa: E402
    ACTION_VERB_KEY,
    ACTION_VERBS,
    ActionArgsError,
    BlockRecord,
    format_action_args,
    parse_action_args,
    parse_evidence,
    read_action_args,
)

#: The bug status a restamp target must have.
DONE_STATUS = "done"


class VerbRefused(Exception):
    """An operator data action that must not run; the message names every
    reason, in plain language, for the escalation's diagnosis."""


@dataclasses.dataclass(frozen=True)
class VerbRun:
    """What one run of an allowlisted verb did.

    Fields:
        verb        the verb that ran.
        invocation  the equivalent entry-point command line, for narration
                    and evidence only — it is never executed.
        lines       the verb's per-target output lines.
        summary     its closing result line.
        changes     how many targets the run changed (0 on a re-run of a
                    completed action).
        failed      how many targets could not be read or written.
    """

    verb: str
    invocation: str
    lines: tuple[str, ...]
    summary: str
    changes: int
    failed: int

    def as_text(self) -> str:
        """The run as evidence text: the invocation, each line, the summary."""
        return "\n".join((f"$ {self.invocation}", *self.lines, self.summary)) + "\n"


@dataclasses.dataclass(frozen=True)
class AllowlistedVerb:
    """One runnable allowlisted verb.

    Fields:
        name          the ``verb=`` value (a key of ``ACTION_VERBS``).
        entry_point   the operator script the verb stands for.
        target_check  ``(kanban_root, args) -> [problem, ...]``: what the
                      arguments point at, checked; ``[]`` when all is well.
        runner        ``(kanban_root, args) -> VerbRun``: the run itself,
                      called only after both checks pass.
    """

    name: str
    entry_point: str
    target_check: Callable[[Path, Mapping[str, str]], list]
    runner: Callable[[Path, Mapping[str, str]], VerbRun]


# ---------------------------------------------------------------------------
# restamp-fixed-in
# ---------------------------------------------------------------------------


def _bug_ids(args: Mapping[str, str]) -> list[str]:
    return [part for part in args["bugs"].split(",") if part]


def _bugs_dir(kanban_root: Path, project: str) -> Path:
    return kanban_root / "projects" / project / "bugs"


def _project_problems(kanban_root: Path, project: str) -> list[str]:
    """Why *project* is not an existing project, or [] when it is."""
    registry = kanban_root / "projects.cfg"
    if registry.is_file():
        try:
            names = [entry.name for entry in ini.projects_registry(kanban_root)]
        except ini.IniError as exc:
            return [f"the project registry {registry} cannot be read ({exc})"]
        if project not in names:
            registered = ", ".join(names) or "none"
            return [f"project {project!r} is not registered in {registry} (registered: {registered})"]
    bugs_dir = _bugs_dir(kanban_root, project)
    if not bugs_dir.is_dir():
        return [f"project {project!r} has no bug ledger at {bugs_dir}"]
    return []


def _restamp_targets(kanban_root: Path, args: Mapping[str, str]) -> list[str]:
    """The project exists; every bug resolves to exactly one file and is done."""
    project = args["project"]
    problems = _project_problems(kanban_root, project)
    if problems:
        return problems
    try:
        files = resolve_bug_files(_bugs_dir(kanban_root, project), _bug_ids(args), project)
    except RestampRefused as exc:
        return [exc.message]
    for path in files:
        try:
            status = parse_bug_file(path).status
        except (OSError, UnicodeDecodeError) as exc:
            problems.append(f"{path.name} cannot be read ({exc})")
            continue
        if status != DONE_STATUS:
            problems.append(f"{path.stem} has status {status or 'unset'}, not {DONE_STATUS}")
    return problems


def _restamp_run(kanban_root: Path, args: Mapping[str, str]) -> VerbRun:
    """Stamp ``## Fixed In`` through the restamp body; never overwrites a set value."""
    project = args["project"]
    bugs = _bug_ids(args)
    try:
        version = normalize_version(args["version"], project)
        files = resolve_bug_files(_bugs_dir(kanban_root, project), bugs, project)
    except RestampRefused as exc:
        raise VerbRefused(f"restamp-fixed-in refused its arguments: {exc.message}") from exc
    outcomes = restamp(files, version)
    return VerbRun(
        verb="restamp-fixed-in",
        invocation=f"restamp-fixed-in.sh --project {project} --version {version} {' '.join(bugs)}",
        lines=tuple(outcome.line() for outcome in outcomes),
        summary=summary_line(version, outcomes),
        changes=sum(1 for o in outcomes if o.outcome in ("stamped", "replaced")),
        failed=sum(1 for o in outcomes if o.outcome == "failed"),
    )


#: The runnable allowlist, keyed by verb name. Every key is also a key of
#: ACTION_VERBS (its typed-argument validators); see the module docstring.
VERBS: dict[str, AllowlistedVerb] = {
    "restamp-fixed-in": AllowlistedVerb(
        name="restamp-fixed-in",
        entry_point="restamp-fixed-in.sh",
        target_check=_restamp_targets,
        runner=_restamp_run,
    ),
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validated_args(record: BlockRecord) -> dict[str, str]:
    """The record's typed action arguments, re-validated and canonical.

    Raises:
        VerbRefused: the record names no verb (a prose-only remedy), names
            a verb that is not allowlisted or has no runner, or carries
            arguments that do not validate.
    """
    named = parse_evidence(record.evidence).get(ACTION_VERB_KEY, "").strip()
    if not named:
        raise VerbRefused(
            "the Block Record names no verb= action argument, so the remedy is prose only and nothing is run"
        )
    args = read_action_args(record)
    if not args:
        raise VerbRefused(
            f"verb {named!r} is not allowlisted (allowlisted: {', '.join(sorted(VERBS))}), so nothing is run"
        )
    try:
        canonical = parse_action_args(format_action_args(args))
    except ActionArgsError as exc:
        raise VerbRefused(f"the action arguments do not validate: {exc}") from exc
    if canonical[ACTION_VERB_KEY] not in VERBS or canonical[ACTION_VERB_KEY] not in ACTION_VERBS:
        raise VerbRefused(
            f"verb {canonical[ACTION_VERB_KEY]!r} has no runner (allowlisted: {', '.join(sorted(VERBS))}), "
            "so nothing is run"
        )
    return canonical


def target_problems(kanban_root: "Path | str", args: Mapping[str, str]) -> list[str]:
    """Why the targets of validated *args* do not check out; [] when they do."""
    return VERBS[args[ACTION_VERB_KEY]].target_check(Path(kanban_root), args)


def run_allowlisted_verb(kanban_root: "Path | str", args: Mapping[str, str]) -> VerbRun:
    """Re-check *args* and their targets, then run the verb once.

    Raises:
        VerbRefused: the arguments or a target no longer check out; nothing
            was run.
    """
    try:
        canonical = parse_action_args(format_action_args(dict(args)))
    except ActionArgsError as exc:
        raise VerbRefused(f"the action arguments do not validate: {exc}") from exc
    verb = VERBS.get(canonical[ACTION_VERB_KEY])
    if verb is None:
        raise VerbRefused(f"verb {canonical[ACTION_VERB_KEY]!r} has no runner, so nothing is run")
    problems = verb.target_check(Path(kanban_root), canonical)
    if problems:
        raise VerbRefused("; ".join(problems))
    return verb.runner(Path(kanban_root), canonical)
