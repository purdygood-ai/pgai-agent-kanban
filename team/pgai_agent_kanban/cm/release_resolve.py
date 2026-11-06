"""
release_resolve.py -- what cm/release.sh does instead of halting when its
squash into main conflicts, or when the release tag is already on the
project's remote.

Both are mechanical failures that do no damage once the release backs out,
so neither stops the project on its own:

``squash-conflict`` (trigger 5)
    The release has already backed the squash out (main is at its
    pre-release commit). This asks the resolution matrix whether the RC may
    be cancelled and rebundled
    (:func:`pgai_agent_kanban.overwatch.resolution_stage.resolve_squash_conflict`,
    the same CANCEL-AND-REBUNDLE drill the decision wake's resolution stage
    runs on a ship-policy HALT), and writes one operator inbox entry naming
    every conflicting path either way. ``outcome=cancelled`` means the RC
    is gone and its cargo is back in the queue: the release exits without a
    HALT. ``outcome=halt`` means no row matched or the cancel failed, and
    the release writes its trigger 5 HALT with the reason.

``tag-on-remote`` (trigger 7)
    The release tag is already on the remote, so the release stops before
    cutting it. This writes one operator inbox entry naming the tag and the
    two ways to answer (cancel the RC, or remove the stray remote tag). The
    release then exits without a HALT, and its EXIT trap returns main to its
    pre-release commit, so no branch, tag or release-state.md changes.

Every entry is written once: an open entry asking the same question is
reused, so a release run again on the same failure adds none.

Usage (CLI, consumed by team/scripts/lib/cm/release_resolve.sh)::

    python3 -m pgai_agent_kanban.cm.release_resolve squash-conflict --kanban-root DIR --project NAME \\
        --version V --repo-root DIR --rc-branch B --main-branch M --main-sha SHA --rc-tip SHA \\
        --path P [--path P ...]
    python3 -m pgai_agent_kanban.cm.release_resolve tag-on-remote --kanban-root DIR --project NAME \\
        --version V --repo-root DIR --tag T --remote R

Each prints ``key=value`` lines. ``squash-conflict`` prints ``outcome=``
(``cancelled`` or ``halt``), ``reason=``, ``ledger_entry=`` (``none`` when no
drill ran), ``entry_id=`` (``none`` when the inbox write failed) and one
``action_error=`` line per drill step that failed. ``tag-on-remote`` prints
``entry_id=``. An inbox write that fails is narrated on stderr and never
changes the outcome. A refusal of bad input prints the dual-channel
``error:`` / ``message:`` / ``remedy:`` block on stderr and exits 1, having
written nothing; 2 is a usage error.
"""

from __future__ import annotations

import argparse
import dataclasses
import pathlib
import shlex
import sys

# Run by file path the module has no parent package; put this tree's own
# package root first so the package imports resolve from the same tree.
# Imported as a package member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.lib.error_contract import emit_error_contract  # noqa: E402
from pgai_agent_kanban.lib.version_arg import RELEASE_VERSION_HELP, checked_release_version  # noqa: E402
from pgai_agent_kanban.ops.context import OpsContext  # noqa: E402
from pgai_agent_kanban.ops.errors import ContractError  # noqa: E402
from pgai_agent_kanban.overwatch import inbox as operator_inbox  # noqa: E402
from pgai_agent_kanban.overwatch.halt_provenance import installed_script_command  # noqa: E402
from pgai_agent_kanban.overwatch.resolution_stage import cancel_landed, resolve_squash_conflict  # noqa: E402

__all__ = [
    "OUTCOME_CANCELLED",
    "OUTCOME_HALT",
    "ReleaseResolveError",
    "SquashConflictResult",
    "resolve_squash_conflict_for_release",
    "write_tag_on_remote_entry",
]

#: The squash conflict was resolved: the RC is cancelled, its cargo requeued.
OUTCOME_CANCELLED = "cancelled"
#: The squash conflict was not resolved: the release writes its trigger 5 HALT.
OUTCOME_HALT = "halt"

#: The longest path list an inbox question quotes in full; longer lists end
#: with a count of the rest (every path is still named on the release's own
#: stderr narration).
_PATHS_SHOWN = 50

_HELP_COMMAND = "python3 -m pgai_agent_kanban.cm.release_resolve --help"
#: The error key of a malformed --version.
_BAD_VERSION_KEY = "release-resolve.bad-version"


class ReleaseResolveError(ContractError):
    """Bad input: nothing was written.

    Carries the dual-channel fields (``error``, ``message``, ``remedy``).
    """


@dataclasses.dataclass(frozen=True)
class SquashConflictResult:
    """What resolving one squash conflict did."""

    outcome: str
    reason: str
    ledger_entry: str | None
    entry_id: str | None
    inbox_error: str | None
    action_errors: tuple


def _checked_paths(paths: list) -> list:
    cleaned = [" ".join(str(p).split()) for p in paths or []]
    cleaned = [p for p in cleaned if p]
    if not cleaned:
        raise ReleaseResolveError(
            "release-resolve.no-conflict-paths",
            "No conflicting path was given, so the squash conflict cannot be described; the release names "
            "every unmerged path (git diff --name-only --diff-filter=U) with one --path each.",
            _HELP_COMMAND,
        )
    return cleaned


def _path_list(paths: list) -> str:
    shown = paths[:_PATHS_SHOWN]
    text = ", ".join(shown)
    if len(paths) > len(shown):
        text += f" and {len(paths) - len(shown)} more"
    return text


def _inbox_list_command(ctx: OpsContext, project: str) -> str:
    return installed_script_command(ctx.kanban_root, "inbox.sh", "--project", project, "list")


def _write_entry(ctx: OpsContext, project: str, **fields) -> tuple:
    """Write one inbox entry once; ``(entry_id, error)``, error None on success."""
    try:
        entry = operator_inbox.write_entry(ctx, project, once=True, **fields)
    except ContractError as exc:
        return None, f"{exc.error}: {exc.message}"
    return entry.entry_id, None


def resolve_squash_conflict_for_release(
    kanban_root: str,
    project: str,
    version: str,
    *,
    rc_branch: str,
    main_branch: str,
    main_sha: str,
    rc_tip: str,
    paths: list,
    repo_root: str | None = None,
) -> SquashConflictResult:
    """Cancel and rebundle *version*'s RC after its squash conflicted, when
    the matrix allows it, and leave one inbox entry naming every path.

    The caller has already backed the squash out (*main_branch* is at
    *main_sha*). *rc_tip* is the RC branch's tip commit, named in the entry
    so work made directly on the cancelled branch stays recoverable.
    """
    # CONVERT: a malformed version is refused before anything is written;
    # main() narrates the refusal and release_resolve.sh carries on.
    version = checked_release_version(version, ReleaseResolveError, _BAD_VERSION_KEY, _HELP_COMMAND)
    paths = _checked_paths(paths)
    ctx = OpsContext(kanban_root=kanban_root)
    repo = repo_root or "<dev tree>"
    outcome = resolve_squash_conflict(project, str(ctx.kanban_root), version, repo_root=repo_root)
    cancelled = cancel_landed(outcome, project, version)
    conflict = f"The squash of {rc_branch} into {main_branch} conflicted on {len(paths)} path(s): {_path_list(paths)}."
    close_hint = (
        f"List this entry with `{_inbox_list_command(ctx, project)}` and close it with the "
        "same script's close command."
    )
    if cancelled:
        recover = f"git -C {shlex.quote(repo)} branch {shlex.quote(rc_branch + '-cancelled')} {shlex.quote(rc_tip)}"
        fields = dict(
            source=f"rc:{version}",
            question=(
                f"{conflict} {version} was cancelled and its cargo returned for rebundling from the current "
                f"{main_branch}. Does anything on {main_branch} need to change before the rebundled RC ships?"
            ),
            meanwhile=(
                f"Cancelled {version} without a HALT through the resolution stage's CANCEL-AND-REBUNDLE drill "
                f"(ledger entry {outcome.ledger_entry_id}): its open tasks were closed WONT-DO and its cargo "
                f"returned to open, so discovery bundles it again. {main_branch} was left at {main_sha}; the "
                f"cancelled RC's tip was {rc_tip}."
            ),
            how_to_answer=(
                "Nothing is required when the rebundled RC squashes cleanly. To look at the cancelled RC's work, "
                f"recreate its branch with `{recover}`. {close_hint}"
            ),
        )
        result_outcome = OUTCOME_CANCELLED
    else:
        cancel_cmd = installed_script_command(
            ctx.kanban_root, "cm/cancel-rc.sh", "--project", project, "--key", version, "--yes"
        )
        unhalt_cmd = installed_script_command(ctx.kanban_root, "unhalt.sh", "--project", project)
        fields = dict(
            source=f"rc:{version}",
            question=(
                f"{conflict} {version} could not be cancelled and rebundled automatically ({outcome.reason}), "
                "so the project is HALTed on trigger 5. Cancel the RC, or resolve the conflict and release again?"
            ),
            meanwhile=(
                f"The squash was backed out: {main_branch} is at {main_sha} and nothing was committed or tagged. "
                "The project's HALT stops its chain; other projects keep running."
            ),
            how_to_answer=(
                f"To rebundle the cargo, run `{cancel_cmd}` and then `{unhalt_cmd}`. To keep the RC, resolve the "
                f"conflict on {rc_branch} through a task, then run `{unhalt_cmd}`. {close_hint}"
            ),
        )
        result_outcome = OUTCOME_HALT
    entry_id, inbox_error = _write_entry(ctx, project, **fields)
    return SquashConflictResult(
        outcome=result_outcome,
        reason=outcome.reason,
        ledger_entry=outcome.ledger_entry_id,
        entry_id=entry_id,
        inbox_error=inbox_error,
        action_errors=tuple(outcome.action_errors),
    )


def write_tag_on_remote_entry(
    kanban_root: str,
    project: str,
    version: str,
    *,
    tag: str,
    remote: str,
    repo_root: str | None = None,
) -> str:
    """Write the inbox entry for a release tag already on the remote; its ID.

    Raises :class:`~pgai_agent_kanban.ops.errors.ContractError` when the
    version is malformed or the inbox refuses the write.
    """
    # CONVERT: a malformed version is refused before anything is written;
    # main() narrates the refusal and release_resolve.sh carries on.
    version = checked_release_version(version, ReleaseResolveError, _BAD_VERSION_KEY, _HELP_COMMAND)
    ctx = OpsContext(kanban_root=kanban_root)
    repo = shlex.quote(repo_root or "<dev tree>")
    cancel_cmd = installed_script_command(
        ctx.kanban_root, "cm/cancel-rc.sh", "--project", project, "--key", version, "--yes"
    )
    entry = operator_inbox.write_entry(
        ctx,
        project,
        source=f"rc:{version}",
        question=(
            f"Release tag {tag} is already on {remote}, so {version} was not tagged or shipped again. Did "
            f"{version} already ship (cancel this RC), or is the tag on {remote} stray (remove it and release again)?"
        ),
        meanwhile=(
            "Stopped the release before cutting the tag, without a HALT: main was returned to its pre-release "
            "commit, no local tag was made, and the RC branch and release-state.md are unchanged. The rest of "
            "the project kept running."
        ),
        how_to_answer=(
            f"Confirm with `git -C {repo} ls-remote --tags {shlex.quote(remote)} {shlex.quote(tag)}`. If {version} "
            f"already shipped, run `{cancel_cmd}`. If the tag is stray, remove it from {remote} by hand "
            f"(`git -C {repo} push {shlex.quote(remote)} --delete refs/tags/{shlex.quote(tag)}`) and the next "
            f"release run ships {version}. List this entry with `{_inbox_list_command(ctx, project)}` and close it "
            "with the same script's close command."
        ),
        once=True,
    )
    return entry.entry_id


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="release_resolve",
        description=(
            "What cm/release.sh does instead of halting when its squash into main conflicts (cancel and "
            "rebundle the RC when the resolution matrix allows it) or when the release tag is already on the "
            "remote (an operator inbox entry); each leaves one operator inbox entry."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name, helptext in (
        ("squash-conflict", "cancel and rebundle the RC after a squash conflict, or say why not"),
        ("tag-on-remote", "write the inbox entry for a release tag already on the remote"),
    ):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--kanban-root", required=True, help="the kanban root")
        p.add_argument("--project", required=True, help="the project, e.g. pgai-agent-kanban")
        p.add_argument("--version", required=True, help=RELEASE_VERSION_HELP)
        p.add_argument("--repo-root", required=True, help="the dev tree the release runs in (its REPO_ROOT)")
        if name == "squash-conflict":
            p.add_argument("--rc-branch", required=True, help="the RC branch, e.g. ai_rc/v1.2.3")
            p.add_argument("--main-branch", required=True, help="the main branch, e.g. ai_main")
            p.add_argument("--main-sha", required=True, help="the commit main was restored to")
            p.add_argument("--rc-tip", required=True, help="the RC branch's tip commit")
            p.add_argument("--path", action="append", default=[], help="one conflicting path; repeat for each")
        else:
            p.add_argument("--tag", required=True, help="the release tag, e.g. ai_v1.2.3")
            p.add_argument("--remote", required=True, help="the remote, e.g. origin")
    return parser


def main(argv: list | None = None) -> int:
    """Run one subcommand and print its ``key=value`` lines.

    Returns 0 when the subcommand ran (whatever its outcome), 1 when an
    input or the inbox write was refused (the dual-channel block is on
    stderr and nothing was written); argparse exits 2 on a usage error.
    """
    args = _parser().parse_args(argv)
    try:
        if args.command == "squash-conflict":
            result = resolve_squash_conflict_for_release(
                args.kanban_root,
                args.project,
                args.version,
                rc_branch=args.rc_branch,
                main_branch=args.main_branch,
                main_sha=args.main_sha,
                rc_tip=args.rc_tip,
                paths=args.path,
                repo_root=args.repo_root or None,
            )
            print(f"outcome={result.outcome}")
            print(f"reason={' '.join(result.reason.split())}")
            print(f"ledger_entry={result.ledger_entry or 'none'}")
            print(f"entry_id={result.entry_id or 'none'}")
            for err in result.action_errors:
                print(f"action_error={' '.join(str(err).split())}")
            if result.inbox_error:
                sys.stderr.write(f"release_resolve: the operator inbox entry was not written: {result.inbox_error}\n")
        else:
            entry_id = write_tag_on_remote_entry(
                args.kanban_root,
                args.project,
                args.version,
                tag=args.tag,
                remote=args.remote,
                repo_root=args.repo_root or None,
            )
            print(f"entry_id={entry_id}")
    except ContractError as exc:
        # CONVERT: a refused input or inbox write is narrated and the
        # release carries on -- release_resolve.sh reads a non-zero status
        # as "not resolved" (a squash conflict then HALTs on trigger 5, a
        # tag on the remote still stops without one); nothing was written.
        emit_error_contract(exc.error, exc.message, exc.remedy)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
