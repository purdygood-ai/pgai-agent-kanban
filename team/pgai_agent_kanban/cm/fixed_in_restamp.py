#!/usr/bin/env python3
"""
fixed_in_restamp.py — Stamp a named release version onto a named set of bugs.

The operator-facing back-fill for ``## Fixed In``. The release engine stamps
the bugs a release closed at ship time (``fixed_in_writeback``); when that
step did not run or did not land for some release, this module records the
version afterwards, on exactly the bug files the operator names, through the
same ``write_fixed_in`` the release engine uses.

Rules, per named bug:

  - ``## Status`` is ``done`` and ``## Fixed In`` is absent or empty
    (whitespace and template comments only): the version is written.
  - ``## Fixed In`` already carries a value: never overwritten; reported as
    skipped, with the value it carries.
  - ``## Status`` is anything but ``done``: not written (a bug that has not
    shipped has no fixing release); reported as skipped, with its status.

Bug files not named are never read for writing and never written.

Correction mode (``--replace-from vA.B.C``) is the one way to change a value
that is already set, for a ``## Fixed In`` the release engine recorded
wrongly. It is opt-in and exact-match only:

  - ``## Status`` is ``done`` and ``## Fixed In`` is exactly ``vA.B.C``: the
    value is replaced with ``--version``; reported as replaced.
  - ``## Fixed In`` is empty: stamped, exactly as without the flag.
  - ``## Status`` is anything but ``done``: skipped, as without the flag.
  - ``## Fixed In`` carries any other value: the whole run is refused
    dual-channel with exit 2 before the first write, naming every such bug
    and its value, so a wrong list never corrects some bugs and not others.

Every input is validated before the first write. A missing or malformed
version, an empty bug list, an absent bugs directory, or a bug name that
matches no file (or more than one) refuses dual-channel with exit 2 and
writes nothing at all.

A bug may be named by its file name, that name without ``.md``, its bare
identifier, or a path whose last component is one of those -- for example
``BUG-NNNN`` or ``BUG-NNNN-short-slug.md``. Only the last path component is
used, so a name can never reach outside the bugs directory.

Archived bugs. When ``--bugs-dir`` is a live project's ledger
(``<kanban_root>/projects/<project>/bugs``), a name that matches no live
bug file is looked up in that project's archive
(``<kanban_root>/archive/<project>/bugs``) by the same rules, and an
archived bug is stamped (or replaced, or skipped) in place in the archive.
A live bug always wins: the archive is consulted only for a name with no
live match, so a name matching one live file is never ambiguous with an
archived one. A bugs directory not in that shape has no archive, and only
the live directory is searched.

Usage (normally reached through team/scripts/restamp-fixed-in.sh, which
resolves the project's bugs directory and composes a cwd-independent
PYTHONPATH):

    python3 -m pgai_agent_kanban.cm.fixed_in_restamp \\
        --bugs-dir <dir> --version vX.Y.Z [--replace-from vA.B.C] \\
        BUG-NNNN [BUG-NNNN ...]

Output: one line per named bug, then a result line:

    stamped: BUG-NNNN-short-slug.md
    skipped (already set: v1.2.2): BUG-NNNN-other.md
    skipped (status is running, not done): BUG-NNNN-third.md
    Fixed-In restamp v1.2.3: 1 stamped, 2 skipped.

A bug read from the archive carries `` (archived)`` after its file name,
e.g. ``stamped: BUG-NNNN-old-fix.md (archived)``.

In correction mode a replaced bug reads ``replaced (was v1.2.4): <file>``
and the result line also counts them:

    Fixed-In restamp v1.2.3: 0 stamped, 3 replaced from v1.2.4, 0 skipped.

Exit codes:
    0  Completed; every named bug was stamped, replaced or skipped.
    1  At least one named bug could not be read or written; the result line
       still reports what did land.
    2  Refused (bad invocation); nothing was written.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
from typing import NamedTuple

# Run by file path (``python3 .../fixed_in_restamp.py``) or loaded from a file
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

from pgai_agent_kanban.archive.layout import CATEGORY_BUGS, archive_layout_for_category_dir  # noqa: E402
from pgai_agent_kanban.cm.changelog_writer import parse_bug_file  # noqa: E402
from pgai_agent_kanban.cm.fixed_in_writeback import replace_fixed_in, write_fixed_in  # noqa: E402
from pgai_agent_kanban.lib.error_contract import emit_error_contract  # noqa: E402

EXIT_OK = 0
EXIT_WRITE_FAILED = 1
EXIT_REFUSED = 2

# A release version: vMAJOR.MINOR.PATCH. The leading 'v' is optional on
# input and always present in what is written.
_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$", re.IGNORECASE)
# A bare bug identifier, e.g. BUG-0123; matched case-insensitively.
_BUG_ID_RE = re.compile(r"^BUG-\d+$", re.IGNORECASE)

_SCRIPT = "team/scripts/restamp-fixed-in.sh"
_DEFAULT_PROJECT = "pgai-agent-kanban"


def _example(project: str | None) -> str:
    """A runnable invocation for a remedy, naming *project* when one is known."""
    return f"{_SCRIPT} --project {project or _DEFAULT_PROJECT} --version v1.4.0 BUG-0101 BUG-0102"  # example invocation


def _correction_example(project: str | None) -> str:
    """A runnable correction-mode invocation for a remedy."""
    return (
        f"{_SCRIPT} --project {project or _DEFAULT_PROJECT} "
        "--version v1.4.0 --replace-from v1.5.0 BUG-0101 BUG-0102"  # example invocation
    )


class RestampRefused(Exception):
    """A bad invocation; carries the dual-channel fields to emit."""

    def __init__(self, error: str, message: str, remedy: str) -> None:
        super().__init__(message)
        self.error = error
        self.message = message
        self.remedy = remedy


class BugOutcome(NamedTuple):
    """What happened to one named bug file."""

    name: str
    outcome: str  # "stamped", "replaced", "skipped" or "failed"
    detail: str  # the replaced value, or why it was skipped or failed; "" when stamped
    archived: bool = False  # the bug file was read from the project's archive

    def line(self) -> str:
        """The per-bug output line; an archived bug is marked ``(archived)``."""
        name = f"{self.name} (archived)" if self.archived else self.name
        if self.outcome == "stamped":
            return f"stamped: {name}"
        return f"{self.outcome} ({self.detail}): {name}"


def archive_bugs_dir(bugs_dir: pathlib.Path) -> pathlib.Path | None:
    """The archive's bugs directory for the live ledger *bugs_dir*, or None.

    *bugs_dir* is read as ``<kanban_root>/projects/<project>/bugs``; the
    archive returned is that same kanban root's
    ``archive/<project>/bugs``, whether or not it exists yet. ``None`` when
    *bugs_dir* is not in that shape, so no archive is ever searched for a
    directory that does not belong to a live project.
    """
    layout = archive_layout_for_category_dir(bugs_dir, CATEGORY_BUGS)
    return layout.bugs if layout is not None else None


def normalize_replace_from(raw: str, project: str | None = None) -> str:
    """Return the ``--replace-from`` value as a canonical ``vX.Y.Z``, or refuse.

    Accepts the same forms as ``normalize_version``.
    """
    value = raw.strip()
    if _VERSION_RE.match(value) is None:
        # BLOCK-WORTHY: correction overwrites only a value exactly equal to
        # this one; a value that is not a release version can match nothing
        # meaningful, and guessing what was meant could overwrite a correct
        # release record.
        raise RestampRefused(
            "fixed_in_restamp.replace_from_malformed",
            f"--replace-from {value!r} is not a release version (expected vX.Y.Z, e.g. v1.5.0).",
            _correction_example(project),
        )
    return normalize_version(value, project)


def check_correctable(
    bug_files: list[pathlib.Path],
    version: str,
    replace_from: str,
    project: str | None = None,
) -> None:
    """Refuse unless every named bug's ``## Fixed In`` is empty or *replace_from*.

    Runs before the first write. Every mismatching bug is collected first,
    so one refusal names them all with the value each carries. A file that
    cannot be read is left for ``restamp`` to report as failed.

    The remedy re-runs the correction on the bugs that do qualify, or, when
    none does, prints the named bugs' ``## Fixed In`` sections to inspect.
    """
    mismatched: list[str] = []
    correctable: list[str] = []
    for path in bug_files:
        try:
            current = parse_bug_file(path).fixed_in
        except (OSError, UnicodeDecodeError):
            correctable.append(path.stem)
            continue
        if current and current != replace_from:
            mismatched.append(f"{path.name} (has {current})")
        else:
            correctable.append(path.stem)
    if mismatched:
        if correctable:
            remedy = (
                f"{_SCRIPT} --project {project or _DEFAULT_PROJECT} --version {version} "
                f"--replace-from {replace_from} {' '.join(correctable)}"
            )
        else:
            remedy = f"grep -A2 '^## Fixed In' {' '.join(str(p) for p in bug_files)}"
        # BLOCK-WORTHY: correction mode may overwrite only the named wrong
        # value; overwriting any other value would destroy a release record
        # the operator did not ask to change.
        raise RestampRefused(
            "fixed_in_restamp.replace_from_mismatch",
            f"Correction mode replaces only '## Fixed In: {replace_from}', but "
            f"{'; '.join(mismatched)}. Nothing was written. Drop those bugs from "
            "the list, or name the value they carry in --replace-from.",
            remedy,
        )


def normalize_version(raw: str | None, project: str | None = None) -> str:
    """Return *raw* as a canonical ``vX.Y.Z``, or refuse.

    Surrounding whitespace and a missing or upper-case leading 'v' are
    tolerated; anything else is refused.
    """
    value = (raw or "").strip()
    if not value:
        # BLOCK-WORTHY: without a version there is nothing to record, and
        # guessing one would stamp a wrong release onto the ledger.
        raise RestampRefused(
            "fixed_in_restamp.version_missing",
            "No release version was given, so there is nothing to stamp.",
            _example(project),
        )
    match = _VERSION_RE.match(value)
    if match is None:
        # BLOCK-WORTHY: a malformed version written into ## Fixed In would
        # corrupt the ledger's release record for every bug named.
        raise RestampRefused(
            "fixed_in_restamp.version_malformed",
            f"Version {value!r} is not a release version (expected vX.Y.Z, e.g. v1.4.0).",
            _example(project),
        )
    return "v" + ".".join(match.groups())


def _name_candidates(bugs_dir: pathlib.Path, raw: str) -> list[pathlib.Path]:
    """Every bug file in *bugs_dir* the operator-supplied *raw* name can mean."""
    token = pathlib.Path(raw.strip()).name
    if not token:
        return []
    stem = token[:-3] if token.lower().endswith(".md") else token
    exact = bugs_dir / f"{stem}.md"
    if exact.is_file():
        return [exact]
    if _BUG_ID_RE.match(stem):
        prefix = stem.upper() + "-"
        return sorted(
            p for p in bugs_dir.glob("BUG-*.md") if p.name.upper().startswith(prefix)
        )
    return []


def resolve_bug_files(
    bugs_dir: pathlib.Path, names: list[str], project: str | None = None
) -> list[pathlib.Path]:
    """Resolve every operator-supplied bug name to exactly one file, or refuse.

    Each name is matched in *bugs_dir* first. A name with no live match is
    matched, by the same rules, in the project's archive
    (:func:`archive_bugs_dir`) when *bugs_dir* has one; the path returned
    for it is the archived file. A live match always wins.

    Every unresolvable name is collected first, so one refusal names them
    all. Duplicates collapse to one entry; order follows first mention.
    """
    wanted = [n for n in names if n.strip()]
    if not wanted:
        # BLOCK-WORTHY: a restamp with no bugs named would silently do
        # nothing while reporting success.
        raise RestampRefused(
            "fixed_in_restamp.no_bugs_named",
            "No bug files were named; name each bug to stamp by its id or file name.",
            _example(project),
        )
    if not bugs_dir.is_dir():
        # BLOCK-WORTHY: the ledger to stamp does not exist at this path, so
        # the project name or root is wrong; nothing can be resolved.
        raise RestampRefused(
            "fixed_in_restamp.bugs_dir_missing",
            f"No bugs directory at {bugs_dir}; the project name or kanban root is wrong.",
            f"ls {bugs_dir.parent.parent}",
        )

    archive_dir = archive_bugs_dir(bugs_dir)
    resolved: list[pathlib.Path] = []
    missing: list[str] = []
    ambiguous: list[str] = []
    for raw in wanted:
        found = _name_candidates(bugs_dir, raw)
        if not found and archive_dir is not None and archive_dir.is_dir():
            found = _name_candidates(archive_dir, raw)
        if not found:
            missing.append(raw)
        elif len(found) > 1:
            ambiguous.append(f"{raw} ({', '.join(p.name for p in found)})")
        elif found[0] not in resolved:
            resolved.append(found[0])

    if missing:
        searched = str(bugs_dir)
        listing = f"ls {bugs_dir}"
        if archive_dir is not None and archive_dir.is_dir():
            searched = f"{bugs_dir} or its archive {archive_dir}"
            listing = f"ls {bugs_dir} {archive_dir}"
        # BLOCK-WORTHY: a named bug that is not in the ledger means the list
        # is wrong; stamping the rest would record a partial, silent back-fill.
        raise RestampRefused(
            "fixed_in_restamp.bug_not_found",
            f"No bug file in {searched} matches: {', '.join(missing)}. Nothing was written.",
            listing,
        )
    if ambiguous:
        # BLOCK-WORTHY: an id that matches several files cannot be resolved
        # without guessing which bug the operator meant.
        raise RestampRefused(
            "fixed_in_restamp.bug_ambiguous",
            "Bug name matches more than one file: "
            f"{'; '.join(ambiguous)}. Name the full file instead. Nothing was written.",
            f"ls {bugs_dir}",
        )
    return resolved


def restamp(
    bug_files: list[pathlib.Path],
    version: str,
    replace_from: str | None = None,
    archive_dir: pathlib.Path | None = None,
) -> list[BugOutcome]:
    """Stamp *version* on each of *bug_files* per the module's rules.

    With *replace_from*, a done bug whose value is exactly *replace_from* is
    rewritten to *version* (call ``check_correctable`` first so no other
    value reaches here). Per-file read or write errors are recorded as
    ``failed`` and never stop the run.

    Each file is written where it is, so a file ``resolve_bug_files``
    found in the archive is stamped in place in the archive. Outcomes for
    files directly inside *archive_dir* (the ``archive_bugs_dir`` of the
    ledger) are marked archived; with no *archive_dir* none is.
    """
    outcomes: list[BugOutcome] = []
    for path in bug_files:
        archived = archive_dir is not None and path.parent == archive_dir
        outcomes.append(_restamp_one(path, version, replace_from)._replace(archived=archived))
    return outcomes


def _restamp_one(path: pathlib.Path, version: str, replace_from: str | None) -> BugOutcome:
    """The outcome of stamping one bug file; see :func:`restamp`."""
    try:
        rec = parse_bug_file(path)
        if replace_from is not None and rec.fixed_in == replace_from and rec.status == "done":
            if replace_fixed_in(path, replace_from, version):
                return BugOutcome(path.name, "replaced", f"was {replace_from}")
            return BugOutcome(path.name, "skipped", f"already set: {rec.fixed_in}")
        if rec.fixed_in:
            return BugOutcome(path.name, "skipped", f"already set: {rec.fixed_in}")
        if rec.status != "done":
            return BugOutcome(path.name, "skipped", f"status is {rec.status or 'unset'}, not done")
        if write_fixed_in(path, version):
            return BugOutcome(path.name, "stamped", "")
        return BugOutcome(path.name, "skipped", "already set")
    except (OSError, UnicodeDecodeError) as exc:
        return BugOutcome(path.name, "failed", str(exc))


def summary_line(
    version: str, outcomes: list[BugOutcome], replace_from: str | None = None
) -> str:
    """The closing result line, e.g. 'Fixed-In restamp v1.2.3: 1 stamped, 2 skipped.'

    In correction mode the replaced count follows the stamped count.
    """
    kinds = ("stamped", "replaced", "skipped", "failed")
    counts = {kind: sum(1 for o in outcomes if o.outcome == kind) for kind in kinds}
    line = f"Fixed-In restamp {version}: {counts['stamped']} stamped, "
    if replace_from is not None:
        line += f"{counts['replaced']} replaced from {replace_from}, "
    line += f"{counts['skipped']} skipped"
    if counts["failed"]:
        line += f", {counts['failed']} failed"
    return line + "."


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.cm.fixed_in_restamp",
        description=(
            "Stamp '## Fixed In: <version>' on the named bug files whose value "
            "is empty and whose status is done. Never overwrites a set value "
            "unless --replace-from names it exactly; never writes a bug that "
            "is not named."
        ),
    )
    parser.add_argument("--bugs-dir", type=pathlib.Path, required=True,
                        help="The project's live bugs/ directory. For "
                             "<kanban_root>/projects/<project>/bugs, a name with no "
                             "live match is looked up in "
                             "<kanban_root>/archive/<project>/bugs.")
    parser.add_argument("--version", default=None,
                        help="The release version to record, e.g. v1.4.0.")
    parser.add_argument("--replace-from", default=None, metavar="VERSION",
                        help="Correction mode: also overwrite a '## Fixed In' exactly "
                             "equal to this version; refuse the run if a named bug "
                             "carries any other set value.")
    parser.add_argument("--project", default=None,
                        help="Project name, used only to scope a refusal record.")
    parser.add_argument("bugs", nargs="*",
                        help="Bug ids or file names inside --bugs-dir (or its archive), "
                             "e.g. BUG-0123.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; see the module docstring for output and exit codes."""
    args = _build_parser().parse_args(argv)
    try:
        version = normalize_version(args.version, args.project)
        replace_from = None
        if args.replace_from is not None:
            replace_from = normalize_replace_from(args.replace_from, args.project)
        bug_files = resolve_bug_files(args.bugs_dir, args.bugs, args.project)
        if replace_from is not None:
            check_correctable(bug_files, version, replace_from, args.project)
    except RestampRefused as refusal:
        # BLOCK-WORTHY: every refusal above catches an invocation that would
        # write a wrong or partial release record onto the bug ledger; it is
        # raised before the first write, so nothing has changed on disk.
        emit_error_contract(refusal.error, refusal.message, refusal.remedy, project=args.project)
        # CENSUS-ANCHOR: fixed-in-restamp-01
        return EXIT_REFUSED

    outcomes = restamp(bug_files, version, replace_from, archive_bugs_dir(args.bugs_dir))
    for outcome in outcomes:
        print(outcome.line(), flush=True)
    print(summary_line(version, outcomes, replace_from), flush=True)
    return EXIT_WRITE_FAILED if any(o.outcome == "failed" for o in outcomes) else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
