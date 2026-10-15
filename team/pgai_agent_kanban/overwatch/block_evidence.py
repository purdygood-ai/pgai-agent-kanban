"""
block_evidence.py — what a block site knows when it writes a Block Record.

Every site that blocks a task records why through
``pgai_agent_kanban.overwatch.block_record`` (``block_record_write`` in
bash). That writer takes the reason code, the owner role and the evidence
as given; this module is where a block site gathers them, so every site
reads git, the dispatch ledger and the task README the same way:

* ``owner-role`` — the task's owning role, from the site's own candidates
  (the README ``## Role`` the wake already parsed, the queue agent) and
  then the task README beside status.md.
* ``evidence`` — the one-line ``evidence`` value, conventionally
  ``worktree=<path|gone>; branch=<name|none>; ahead=<n>; merged=<yes|no>``
  followed by ``batch_log=<path>#<from>-<to>`` and ``diag=<log>:<line>``
  when the site names a task directory or a diagnostic log.
* ``worktree-failure-code`` — ``source_branch_missing`` when the source
  branch is absent from the repository, ``worktree_create_failed``
  otherwise.
* ``zero-progress-code`` — ``provider_refusal`` or ``zero_progress`` for a
  task that hit the zero-progress ceiling (see below).

Placeholders
------------
A site never invents evidence it cannot know. Each token falls back to a
fixed placeholder instead:

    worktree=gone   no worktree path was given, or it no longer exists
    branch=none     no branch was given
    ahead=0         the branch, the source branch or the repository is
                    missing, so nothing can be counted
    merged=no       the same; a branch that cannot be read is not merged
    batch_log=none  the task has no usable dispatch ledger record
    diag=none       the diagnostic log was not given or does not exist
    span_s=unknown  the latest dispatch has no "returned" record yet

Provider refusal
----------------
A task that reached the zero-progress ceiling is classified
``provider_refusal`` rather than ``zero_progress`` when its latest
dispatch shows the provider never really ran it: the dispatch span (the
"dispatched" to "returned" ledger timestamps) is under ``--min-span``
seconds (default 60), or the last 50 lines of that dispatch's batch-log
span carry an authentication, model-not-found or usage-limit signature
(``REFUSAL_SIGNATURES``). Anything else is ``zero_progress``.

The dispatch ledger is ``<task_dir>/logs/dispatch.jsonl`` (written by the
wake around each provider run; see overwatch_protocol.sh's
``dispatch_record_*``). The latest record names the batch log and the byte
span; when it is still open (no "returned" line) the span runs to the end
of the batch log.

Every subcommand is best-effort and exits 0: a block site calls it while
already blocking a task, and a git, ledger or README read that fails
yields the placeholder, never an error that could disturb the block.

Usage (CLI; the bash wrappers in team/scripts/lib/block_record.sh call it):
    python3 -m pgai_agent_kanban.overwatch.block_evidence owner-role \\
        STATUS_FILE [CANDIDATE ...]
    python3 -m pgai_agent_kanban.overwatch.block_evidence evidence \\
        [--worktree PATH] [--repo PATH] [--branch NAME] [--source NAME] \\
        [--task-dir DIR] [--refusal-signals] [--diag-log PATH]
    python3 -m pgai_agent_kanban.overwatch.block_evidence \\
        worktree-failure-code REPO SOURCE_BRANCH
    python3 -m pgai_agent_kanban.overwatch.block_evidence \\
        zero-progress-code TASK_DIR [--min-span SECONDS]
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence

# Run by file path, this module has no parent package; put its own package
# root first on sys.path so the imports below resolve from the same tree.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.lib.timeutil import try_parse_iso  # noqa: E402
from pgai_agent_kanban.overwatch.block_record import (  # noqa: E402
    OWNER_ROLES,
    format_evidence,
    normalize_owner_role,
)
from pgai_agent_kanban.task_status import section_value  # noqa: E402

#: Seconds under which a dispatch counts as a provider refusal.
DEFAULT_MIN_SPAN_SECONDS = 60

#: Lines of the dispatch's batch-log span searched for a refusal signature.
TAIL_LINES = 50

#: Seconds any one git call may take before its evidence is a placeholder.
GIT_TIMEOUT_SECONDS = 30

#: Provider-refusal signatures, checked in this order; the first label whose
#: pattern matches the tail is reported. Case-insensitive.
REFUSAL_SIGNATURES: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    (
        "auth",
        re.compile(
            r"authentication[_ ]error|invalid[ _-]?(api[ _-]?key|x-api-key|bearer)"
            r"|unauthori[sz]ed|please run /login|not logged in"
            r"|oauth token (has )?expired",
            re.IGNORECASE,
        ),
    ),
    (
        "model_not_found",
        re.compile(
            r"model[ _-]?not[ _-]?found|not_found_error|unknown model|invalid model"
            r"|model\b[^\n]{0,80}\b(does not exist|not found|is not available)",
            re.IGNORECASE,
        ),
    ),
    (
        "usage_limit",
        re.compile(
            r"usage[ _-]?limit|insufficient_quota|quota[ _-]?(exceeded|reached)"
            r"|exceeded your (current )?quota|(hit|reached) your (usage )?limit"
            r"|credit balance is too low",
            re.IGNORECASE,
        ),
    ),
)

PLACEHOLDER_WORKTREE = "gone"
PLACEHOLDER_NONE = "none"
PLACEHOLDER_SPAN = "unknown"


# ---------------------------------------------------------------------------
# Owner role
# ---------------------------------------------------------------------------


def _readme_role(status_file: Path) -> str:
    readme = status_file.parent / "README.md"
    try:
        text = readme.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return section_value(text, "Role")


def resolve_owner_role(status_file: "Path | str", candidates: Sequence[str] = ()) -> str:
    """Return the task's owner role, or ``""`` when none is known.

    Each candidate is tried in order, then the ``## Role`` of the README
    beside *status_file*; the first one in OWNER_ROLES (any case) wins.
    """
    for value in [*candidates, _readme_role(Path(status_file))]:
        role = normalize_owner_role(value or "")
        if role in OWNER_ROLES:
            return role
    return ""


# ---------------------------------------------------------------------------
# Git evidence
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> Optional[subprocess.CompletedProcess]:
    try:
        return subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _branch_exists(cwd: Path, branch: str) -> bool:
    result = _git(cwd, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
    return result is not None and result.returncode == 0


def _git_dir(worktree: str, repo: str) -> Optional[Path]:
    """The directory git runs in: the worktree when it exists, else the repo."""
    for candidate in (worktree, repo):
        if candidate and Path(candidate).is_dir():
            return Path(candidate)
    return None


def git_evidence(worktree: str = "", repo: str = "", branch: str = "", source: str = "") -> list[str]:
    """Return the ``worktree``, ``branch``, ``ahead`` and ``merged`` tokens.

    ``ahead`` counts the branch's commits not on *source*; ``merged`` says
    whether the branch is an ancestor of *source*. Both need the branch,
    the source branch and a readable repository, and fall back to
    ``ahead=0`` / ``merged=no`` otherwise.
    """
    worktree_token = worktree if worktree and Path(worktree).is_dir() else PLACEHOLDER_WORKTREE
    ahead, merged = "0", "no"
    cwd = _git_dir(worktree, repo)
    if cwd is not None and branch and source and _branch_exists(cwd, branch) and _branch_exists(cwd, source):
        count = _git(cwd, "rev-list", "--count", f"refs/heads/{source}..refs/heads/{branch}")
        if count is not None and count.returncode == 0 and count.stdout.strip().isdigit():
            ahead = count.stdout.strip()
        ancestor = _git(cwd, "merge-base", "--is-ancestor", f"refs/heads/{branch}", f"refs/heads/{source}")
        if ancestor is not None and ancestor.returncode == 0:
            merged = "yes"
    return [
        f"worktree={worktree_token}",
        f"branch={branch or PLACEHOLDER_NONE}",
        f"ahead={ahead}",
        f"merged={merged}",
    ]


def worktree_failure_code(repo: str, source: str) -> str:
    """``source_branch_missing`` when *source* is absent from *repo*, else
    ``worktree_create_failed``.

    A repository that cannot be read leaves the cause unknown, so it is
    the generic ``worktree_create_failed``.
    """
    cwd = _git_dir("", repo)
    if cwd is None or not source:
        return "worktree_create_failed"
    probe = _git(cwd, "rev-parse", "--git-dir")
    if probe is None or probe.returncode != 0:
        return "worktree_create_failed"
    return "worktree_create_failed" if _branch_exists(cwd, source) else "source_branch_missing"


# ---------------------------------------------------------------------------
# Dispatch ledger
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Dispatch:
    """The task's latest dispatch, as the ledger records it."""

    batch_log: str
    offset_start: int
    offset_end: Optional[int]
    span_seconds: Optional[float]

    def resolved_end(self) -> Optional[int]:
        """The span's end: the recorded end, else the batch log's current size."""
        if self.offset_end is not None:
            return self.offset_end
        try:
            return Path(self.batch_log).stat().st_size
        except OSError:
            return None

    def token(self) -> str:
        """Return the dispatch span as a ``batch_log=<path>#<start>-<end>`` evidence token.

        ``<end>`` is the resolved end offset, or ``eof`` when the batch log can no
        longer be read.
        """
        end = self.resolved_end()
        return f"batch_log={self.batch_log}#{self.offset_start}-{end if end is not None else 'eof'}"


def _as_offset(value: object) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def latest_dispatch(task_dir: "Path | str") -> Optional[Dispatch]:
    """Return the task's latest dispatch, or None when there is no usable ledger.

    Torn or non-object lines are skipped. The span in seconds is measured
    from the latest "dispatched" record to a "returned" record after it;
    with no "returned" record yet it is None.
    """
    ledger = Path(task_dir) / "logs" / "dispatch.jsonl"
    try:
        lines = ledger.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    records = []
    for line in lines:
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.get("batch_log"):
            records.append(obj)
    if not records:
        return None

    latest = records[-1]
    dispatched = next((r for r in reversed(records) if r.get("event") == "dispatched"), None)
    span: Optional[float] = None
    if latest.get("event") == "returned" and dispatched is not None:
        start = try_parse_iso(str(dispatched.get("recorded_at", "")))
        end = try_parse_iso(str(latest.get("recorded_at", "")))
        if start is not None and end is not None and end >= start:
            span = (end - start).total_seconds()
    return Dispatch(
        batch_log=str(latest["batch_log"]),
        offset_start=_as_offset(latest.get("offset_start")) or 0,
        offset_end=_as_offset(latest.get("offset_end")),
        span_seconds=span,
    )


def _tail(text: str, lines: int = TAIL_LINES) -> str:
    return "\n".join(text.splitlines()[-lines:])


def dispatch_tail(dispatch: Dispatch, lines: int = TAIL_LINES) -> str:
    """The last *lines* lines of the dispatch's batch-log span ("" when unreadable)."""
    end = dispatch.resolved_end()
    try:
        with open(dispatch.batch_log, "rb") as fh:
            fh.seek(dispatch.offset_start)
            data = fh.read(None if end is None else max(0, end - dispatch.offset_start))
    except OSError:
        return ""
    return _tail(data.decode("utf-8", errors="replace"), lines)


def refusal_signature(text: str) -> Optional[str]:
    """The label of the first REFUSAL_SIGNATURES pattern found in *text*."""
    for label, pattern in REFUSAL_SIGNATURES:
        if pattern.search(text):
            return label
    return None


@dataclasses.dataclass(frozen=True)
class ZeroProgressVerdict:
    """Why a zero-progress block is, or is not, a provider refusal."""

    reason_code: str
    span_seconds: Optional[float]
    signature: Optional[str]

    def tokens(self) -> list[str]:
        """Return the verdict's evidence tokens: ``span_s=<seconds>`` and ``signature=<text>``.

        An unknown span reads ``unknown`` and a missing signature reads ``none``.
        """
        span = PLACEHOLDER_SPAN if self.span_seconds is None else str(int(self.span_seconds))
        return [f"span_s={span}", f"signature={self.signature or PLACEHOLDER_NONE}"]


def classify_zero_progress(
    task_dir: "Path | str",
    min_span_seconds: float = DEFAULT_MIN_SPAN_SECONDS,
) -> ZeroProgressVerdict:
    """Classify a zero-progress block as ``provider_refusal`` or ``zero_progress``.

    The tail searched is the latest dispatch's batch-log span. A task with
    no usable ledger has neither a span nor a tail to read, so it is
    ``zero_progress`` — another log (the current wake's, say) is not this
    task's evidence.
    """
    dispatch = latest_dispatch(task_dir)
    span = dispatch.span_seconds if dispatch is not None else None
    tail = dispatch_tail(dispatch) if dispatch is not None else ""
    signature = refusal_signature(tail)
    refused = signature is not None or (span is not None and span < min_span_seconds)
    return ZeroProgressVerdict(
        reason_code="provider_refusal" if refused else "zero_progress",
        span_seconds=span,
        signature=signature,
    )


# ---------------------------------------------------------------------------
# The evidence line
# ---------------------------------------------------------------------------


def diag_token(diag_log: str) -> str:
    """``diag=<log>:<line>`` naming the last line of *diag_log* (``diag=none`` when absent).

    A block site calls this right after logging why it blocks, so the last
    line is that diagnostic.
    """
    if not diag_log:
        return f"diag={PLACEHOLDER_NONE}"
    try:
        with open(diag_log, "rb") as fh:
            count = sum(1 for _ in fh)
    except OSError:
        return f"diag={PLACEHOLDER_NONE}"
    return f"diag={diag_log}:{count}" if count else f"diag={PLACEHOLDER_NONE}"


def build_evidence(
    *,
    worktree: str = "",
    repo: str = "",
    branch: str = "",
    source: str = "",
    task_dir: str = "",
    refusal_signals: bool = False,
    diag_log: Optional[str] = None,
    min_span_seconds: float = DEFAULT_MIN_SPAN_SECONDS,
) -> str:
    """Return the one-line ``evidence`` value for a block site.

    Always carries the four git tokens. With *task_dir*, adds the latest
    dispatch's ``batch_log`` span; with *refusal_signals* as well, adds
    ``span_s`` and ``signature`` (see ``classify_zero_progress``). With
    *diag_log* (even ``""``), adds ``diag``.
    """
    tokens = git_evidence(worktree, repo, branch, source)
    if task_dir:
        dispatch = latest_dispatch(task_dir)
        tokens.append(dispatch.token() if dispatch is not None else f"batch_log={PLACEHOLDER_NONE}")
        if refusal_signals:
            tokens += classify_zero_progress(task_dir, min_span_seconds).tokens()
    if diag_log is not None:
        tokens.append(diag_token(diag_log))
    return format_evidence(tokens)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="block_evidence",
        description=(
            "Gather what a block site records in a task's ## Block Record: the "
            "owner role, the evidence line, and the reason code for a worktree "
            "failure or a zero-progress block. Best-effort: every subcommand "
            "exits 0 and prints a documented placeholder for anything it "
            "cannot read."
        ),
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    o = sub.add_parser(
        "owner-role",
        help="print the task's owner role (empty when none is known)",
        description=(
            "Print the first CANDIDATE that is an owner role ("
            + "|".join(OWNER_ROLES)
            + ", any case), else the ## Role of the README beside STATUS_FILE, "
            "else nothing."
        ),
    )
    o.add_argument("status_file", metavar="STATUS_FILE", help="the task's status.md")
    o.add_argument("candidates", metavar="CANDIDATE", nargs="*", help="roles to try before the README")

    e = sub.add_parser(
        "evidence",
        help="print the evidence line",
        description=(
            "Print worktree=<path|gone>; branch=<name|none>; ahead=<n>; "
            "merged=<yes|no>, then batch_log=<path>#<from>-<to> with --task-dir, "
            "span_s and signature with --refusal-signals, and diag=<log>:<line> "
            "with --diag-log."
        ),
    )
    e.add_argument("--worktree", default="", help="the task worktree (gone when absent)")
    e.add_argument("--repo", default="", help="the repository to read when the worktree is gone")
    e.add_argument("--branch", default="", help="the task's feature branch")
    e.add_argument("--source", default="", help="the branch the feature branch merges into")
    e.add_argument("--task-dir", default="", help="the task folder whose logs/dispatch.jsonl names the batch log")
    e.add_argument(
        "--refusal-signals", action="store_true",
        help="with --task-dir: add the dispatch span (span_s) and any refusal signature",
    )
    e.add_argument("--diag-log", default=None, help="log whose last line is the block's diagnostic")
    e.add_argument(
        "--min-span", type=float, default=DEFAULT_MIN_SPAN_SECONDS,
        help=f"seconds under which a dispatch is a refusal (default {DEFAULT_MIN_SPAN_SECONDS})",
    )

    w = sub.add_parser(
        "worktree-failure-code",
        help="print source_branch_missing or worktree_create_failed",
        description=(
            "Print source_branch_missing when SOURCE_BRANCH is absent from the "
            "repository at REPO, else worktree_create_failed."
        ),
    )
    w.add_argument("repo", metavar="REPO", help="the repository the worktree was created from")
    w.add_argument("source", metavar="SOURCE_BRANCH", help="the branch the worktree starts from")

    z = sub.add_parser(
        "zero-progress-code",
        help="print provider_refusal or zero_progress",
        description=(
            "Print provider_refusal when the task's latest dispatch lasted under "
            "--min-span seconds or its batch-log tail carries an authentication, "
            "model-not-found or usage-limit signature; else zero_progress."
        ),
    )
    z.add_argument("task_dir", metavar="TASK_DIR", help="the task folder")
    z.add_argument(
        "--min-span", type=float, default=DEFAULT_MIN_SPAN_SECONDS,
        help=f"seconds under which a dispatch is a refusal (default {DEFAULT_MIN_SPAN_SECONDS})",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point; returns the exit code."""
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "owner-role":
        print(resolve_owner_role(args.status_file, args.candidates))
    elif args.command == "evidence":
        print(
            build_evidence(
                worktree=args.worktree,
                repo=args.repo,
                branch=args.branch,
                source=args.source,
                task_dir=args.task_dir,
                refusal_signals=args.refusal_signals,
                diag_log=args.diag_log,
                min_span_seconds=args.min_span,
            )
        )
    elif args.command == "worktree-failure-code":
        print(worktree_failure_code(args.repo, args.source))
    elif args.command == "zero-progress-code":
        print(classify_zero_progress(args.task_dir, args.min_span).reason_code)
    else:
        parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
