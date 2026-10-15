#!/usr/bin/env python3
"""
fixed_in_writeback.py — Record which release fixed each bug it shipped.

The release engine's finalize stage stamps ``## Fixed In: <version>`` on the
bug files a release closed. Which bugs a release closed is established by
comparing two moments rather than by reading the ledger once: a bug whose
``## Status`` is ``running`` just before bundled-item promotion belongs to the
release candidate being shipped, and the ones promotion then moves to
``done`` are exactly the bugs this release closed. A ``done`` bug outside that
set shipped in an earlier release (or was closed by hand) and is never
stamped here, even when its ``## Fixed In`` is empty — stamping it would
record the wrong version.

Two subcommands, one per moment:

    python3 -m pgai_agent_kanban.cm.fixed_in_writeback running <bugs_dir>
        Print the file name of every BUG-*.md in <bugs_dir> whose
        ## Status is 'running', one per line, sorted. Prints nothing (exit
        0) when the directory is absent or holds no running bug.

    python3 -m pgai_agent_kanban.cm.fixed_in_writeback stamp <bugs_dir> <version> [<bug-file> ...]
        For each named bug file (a bare file name inside <bugs_dir>, as
        'running' printed it) whose ## Status is now 'done' and whose
        ## Fixed In is absent or empty, write <version> into ## Fixed In.
        An already-set ## Fixed In is never overwritten. Bug files not named
        are never written. The last line of output is always the result
        line:

            Fixed-In writeback: N stamped, A already set, D not done, E earlier done bug(s) left unstamped.

        where E counts done bugs outside the named set whose ## Fixed In is
        still empty (the ones this run deliberately did not touch).

        A named bug file no longer in <bugs_dir> is looked up by the same
        file name in the project's archive when <bugs_dir> is a live
        project's ledger (<kanban_root>/projects/<project>/bugs ->
        <kanban_root>/archive/<project>/bugs), and is stamped there in place
        by the same rules, with a line naming it:

            Archived bug read from the archive: BUG-NNNN-short-slug.md

        A named bug in neither place is reported as no longer present and
        counted as failed. Only named bugs are looked up in the archive; the
        E count and the narration below read the live ledger alone.

        Just before the result line, once stamping is finished, every
        BUG-*.md in <bugs_dir> whose ## Status is 'done' and whose
        ## Fixed In is still absent or empty is named on its own line:

            Done bug without ## Fixed In: BUG-NNNN-short-slug.md (back-fill with restamp-fixed-in.sh and the release that closed it)

        so a stamp an earlier release missed shows up at every later release
        instead of staying silent. The narration only reads bug files: it
        writes none, changes no count on the result line, and never changes
        the exit code. When no such bug exists it prints nothing, so the
        output is exactly the per-file warnings and the result line.

Invoke it through ``pp_run_ops`` (team/scripts/lib/pp_run_ops.sh) so the
package resolves from the tree the release engine runs from, whatever the
caller's working directory holds.

Exit codes:
    0  Completed; every eligible bug was stamped (or none was eligible).
    1  At least one named bug file could not be read or written; the result
       line still reports what did land.
    2  Usage error (unknown subcommand, missing argument).
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
from typing import NamedTuple

# Run by file path (``python3 .../fixed_in_writeback.py``) or loaded from a file
# outside its package, this module has no parent package and the interpreter
# puts only its own directory on sys.path, so the package import below would
# not resolve. Put this file's own package root -- the directory holding the
# pgai_agent_kanban/ this file sits in -- at the front of sys.path so the
# import resolves from the same tree, never another. Imported as a package
# member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.archive.layout import CATEGORY_BUGS, archive_layout_for_category_dir  # noqa: E402
from pgai_agent_kanban.cm.changelog_writer import parse_bug_file  # noqa: E402

_BUG_GLOB = "BUG-*.md"

# The ## Fixed In section: heading line, then its body up to the next "## "
# heading or end of file. Tolerates trailing whitespace on the heading.
_FIXED_IN_SECTION_RE = re.compile(
    r"(^##\s+Fixed\s+In\s*$)([\s\S]*?)(?=^##\s+|\Z)",
    re.MULTILINE,
)
_HTML_COMMENT_RE = re.compile(r"<!--[\s\S]*?-->")


class WritebackResult(NamedTuple):
    """Counts from one stamp run; see the module docstring for each field."""

    stamped: int
    already_set: int
    not_done: int
    earlier_unstamped: int
    failed: int

    def summary_line(self) -> str:
        """The one-line result the release log and closing summary quote."""
        line = (
            f"Fixed-In writeback: {self.stamped} stamped, "
            f"{self.already_set} already set, {self.not_done} not done, "
            f"{self.earlier_unstamped} earlier done bug(s) left unstamped."
        )
        if self.failed:
            line = line[:-1] + f", {self.failed} failed."
        return line


def running_bug_names(bugs_dir: pathlib.Path) -> list[str]:
    """Return the sorted file names of bugs in *bugs_dir* at ## Status running.

    An unparseable bug file is reported on stderr and left out; an absent
    directory yields an empty list.
    """
    if not bugs_dir.is_dir():
        return []
    names: list[str] = []
    for bug_path in sorted(bugs_dir.glob(_BUG_GLOB), key=lambda p: p.name):
        try:
            if parse_bug_file(bug_path).status == "running":
                names.append(bug_path.name)
        except (OSError, UnicodeDecodeError) as exc:
            print(f"WARNING: could not read {bug_path.name}: {exc}", file=sys.stderr)
    return names


def done_bugs_without_fixed_in(bugs_dir: pathlib.Path) -> list[str]:
    """Return the sorted file names of done bugs in *bugs_dir* lacking ## Fixed In.

    A bug qualifies when its ## Status is 'done' and its ## Fixed In is absent
    or empty (whitespace and template comments only). Read-only: no file is
    written. An unreadable bug file is left out silently -- the stamp run that
    precedes this scan already warned about it -- and an absent directory
    yields an empty list.
    """
    if not bugs_dir.is_dir():
        return []
    names: list[str] = []
    for bug_path in sorted(bugs_dir.glob(_BUG_GLOB), key=lambda p: p.name):
        try:
            rec = parse_bug_file(bug_path)
        except (OSError, UnicodeDecodeError):
            continue
        if rec.status == "done" and not rec.fixed_in:
            names.append(bug_path.name)
    return names


def missing_fixed_in_lines(bugs_dir: pathlib.Path) -> list[str]:
    """The narration lines naming each done bug that still lacks ## Fixed In.

    Empty when there is none. Never raises on a filesystem problem: the
    narration is advisory and must not change the stamp run's outcome.
    """
    try:
        names = done_bugs_without_fixed_in(bugs_dir)
    except OSError:
        return []
    return [
        f"Done bug without ## Fixed In: {name} "
        "(back-fill with restamp-fixed-in.sh and the release that closed it)"
        for name in names
    ]


def write_fixed_in(bug_path: pathlib.Path, version: str) -> bool:
    """Write *version* into *bug_path*'s ## Fixed In when that section is empty.

    An empty section (only whitespace and HTML comments) is filled in place,
    after its template comment when there is one; an absent section is
    appended at the end of the file. Returns True when the file was written,
    False when the section already carried a value.
    """
    text = bug_path.read_text(encoding="utf-8")
    match = _FIXED_IN_SECTION_RE.search(text)
    if match is None:
        bug_path.write_text(
            text.rstrip("\n") + f"\n\n## Fixed In\n{version}\n", encoding="utf-8"
        )
        return True

    body = match.group(2)
    if _HTML_COMMENT_RE.sub("", body).strip():
        return False

    comment = _HTML_COMMENT_RE.search(body)
    if comment is not None:
        insert_at = match.start(2) + comment.end()
        new_text = text[:insert_at] + f"\n{version}\n" + text[insert_at:]
    else:
        heading_end = match.start(2)
        new_text = text[:heading_end] + f"\n{version}\n" + text[heading_end:].lstrip("\n")
    bug_path.write_text(new_text, encoding="utf-8")
    return True


def replace_fixed_in(bug_path: pathlib.Path, old_version: str, new_version: str) -> bool:
    """Rewrite *bug_path*'s ## Fixed In from *old_version* to *new_version*.

    The section is rewritten only when its value (HTML comments and
    surrounding whitespace removed) is exactly *old_version*; the value is
    swapped in place and every other byte of the file is left as it was.
    Returns True when the file was written, False when the section is
    absent or carries any other value (including none).
    """
    text = bug_path.read_text(encoding="utf-8")
    match = _FIXED_IN_SECTION_RE.search(text)
    if match is None:
        return False

    body = match.group(2)
    if _HTML_COMMENT_RE.sub("", body).strip() != old_version:
        return False

    # The value is the only text outside comments, so its one occurrence
    # outside every comment span is the one to swap.
    comment_spans = [m.span() for m in _HTML_COMMENT_RE.finditer(body)]
    start = body.find(old_version)
    while start != -1 and any(lo <= start < hi for lo, hi in comment_spans):
        start = body.find(old_version, start + 1)
    if start == -1:
        return False

    at = match.start(2) + start
    new_text = text[:at] + new_version + text[at + len(old_version):]
    bug_path.write_text(new_text, encoding="utf-8")
    return True


def stamp_fixed_in(
    bugs_dir: pathlib.Path, version: str, bug_names: list[str]
) -> WritebackResult:
    """Stamp ## Fixed In: *version* on the named bugs that are now done.

    *bug_names* are bare file names inside *bugs_dir* (any directory part is
    ignored, so a name can never reach outside it). A named file no longer
    in *bugs_dir* is read from the same-named file in the project's archive
    when *bugs_dir* has one, and stamped there in place by the same rules.
    Per-file problems are reported on stdout and counted in ``failed``;
    they never stop the run.
    """
    wanted = {pathlib.Path(n).name for n in bug_names if n.strip()}
    stamped = already_set = not_done = earlier_unstamped = failed = 0

    for bug_path in sorted(bugs_dir.glob(_BUG_GLOB), key=lambda p: p.name):
        try:
            rec = parse_bug_file(bug_path)
        except (OSError, UnicodeDecodeError) as exc:
            print(f"WARNING: could not read {bug_path.name}: {exc}", flush=True)
            if bug_path.name in wanted:
                failed += 1
            continue

        if bug_path.name not in wanted:
            if rec.status == "done" and not rec.fixed_in:
                earlier_unstamped += 1
            continue
        if rec.status != "done":
            not_done += 1
            continue
        if rec.fixed_in:
            already_set += 1
            continue
        try:
            if write_fixed_in(bug_path, version):
                stamped += 1
            else:
                already_set += 1
        except OSError as exc:
            print(f"WARNING: could not write {bug_path.name}: {exc}", flush=True)
            failed += 1

    layout = archive_layout_for_category_dir(bugs_dir, CATEGORY_BUGS)
    for name in sorted(wanted - {p.name for p in bugs_dir.glob(_BUG_GLOB)}):
        archived = layout.bugs / name if layout is not None else None
        if archived is None or not archived.is_file():
            print(f"WARNING: bug file no longer present: {name}", flush=True)
            failed += 1
            continue
        print(f"Archived bug read from the archive: {name}", flush=True)
        try:
            rec = parse_bug_file(archived)
            if rec.status != "done":
                not_done += 1
            elif rec.fixed_in or not write_fixed_in(archived, version):
                already_set += 1
            else:
                stamped += 1
        except (OSError, UnicodeDecodeError) as exc:
            print(f"WARNING: could not read or write archived {name}: {exc}", flush=True)
            failed += 1

    return WritebackResult(stamped, already_set, not_done, earlier_unstamped, failed)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.cm.fixed_in_writeback",
        description=(
            "Stamp ## Fixed In: <version> on the bugs a release closed. Run "
            "'running' before bundled-item promotion to capture the release's "
            "bugs, then 'stamp' after it with the names 'running' printed."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    running = sub.add_parser(
        "running",
        help="Print the file names of bugs whose ## Status is 'running'.",
        description="Print the file names of bugs whose ## Status is 'running', one per line.",
    )
    running.add_argument("bugs_dir", type=pathlib.Path, help="The project's bugs/ directory.")

    stamp = sub.add_parser(
        "stamp",
        help="Stamp ## Fixed In on the named bugs that are now 'done'.",
        description=(
            "Write <version> into ## Fixed In on each named bug whose ## Status "
            "is 'done' and whose ## Fixed In is empty. Never overwrites a set "
            "value; never writes a bug that is not named."
        ),
    )
    stamp.add_argument("bugs_dir", type=pathlib.Path, help="The project's bugs/ directory.")
    stamp.add_argument("version", help="The release version to record, e.g. v1.2.3.")
    stamp.add_argument(
        "bug_files",
        nargs="*",
        help="Bare bug file names inside bugs_dir, as 'running' printed them.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; see the module docstring for subcommands and exit codes."""
    args = _build_parser().parse_args(argv)

    if args.command == "running":
        for name in running_bug_names(args.bugs_dir):
            print(name)
        return 0

    if not args.bugs_dir.is_dir():
        print(f"Fixed-In writeback: 0 stamped; no bugs directory at {args.bugs_dir}.")
        return 0
    result = stamp_fixed_in(args.bugs_dir, args.version, args.bug_files)
    # Narration goes before the result line, which must stay last: the
    # release finalizer reads the final 'Fixed-In writeback:' line.
    for line in missing_fixed_in_lines(args.bugs_dir):
        print(line, flush=True)
    print(result.summary_line(), flush=True)
    return 1 if result.failed else 0


if __name__ == "__main__":
    sys.exit(main())
