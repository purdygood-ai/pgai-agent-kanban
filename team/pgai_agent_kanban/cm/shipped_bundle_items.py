#!/usr/bin/env python3
"""
shipped_bundle_items.py -- promote the items of shipped requirements bundles
from ``running`` to ``done``.

The recovery migration team/scripts/migrate/bug-status-done.sh closes
bug/priority items left at ``running`` although the bundle that carried them
has shipped. The script finds each project's requirements directory and the
versions whose release tag exists in the project's dev tree (the tag carries
the project's ``branch_prefix``, which the script strips, so the versions
arrive here as ``vX.Y.Z``); this module does the per-directory work.

What ``promote_shipped_bundle_items`` does for one requirements directory:

- Reads every ``*.md`` file whose name contains ``bundle`` (case-insensitive;
  ``README.md`` excluded), in name order. A file that cannot be read is
  warned about and skipped; every other one counts as processed.
- Takes the bundle's version from the start of its ``## Target Version``
  section (``v`` followed by digits and dots, case-insensitive). A bundle
  with none is reported as skipped.
- A bundle whose version is not in the shipped-tag set is passed over
  silently. An EMPTY set -- a dev tree with no release tag yet -- ships
  nothing: no bundle is promoted, and one line says so before the bundles
  are read. Only ``shipped_tags=None`` (the CLI's ``--treat-all-shipped``,
  which the script passes when the project has no dev tree to read tags
  from) treats every bundle as shipped.
- For a shipped bundle, reads each ``(`<path>`)`` reference in its
  ``## Bundled Items`` section (the section ends at the next ``##`` or
  ``###`` heading) and, for each referenced file that exists and whose
  ``## Status`` value line reads ``running`` (case-insensitive), rewrites
  that value to ``done`` in place, keeping the heading, the whitespace
  around the value and every other byte. Any other status is reported and
  left as it is; a missing or unreadable item is warned about and counted.
- With ``dry_run`` it writes nothing and reports what it would promote.

Every ``##`` section is read by ``pgai_agent_kanban.task_status.read_section``.
Output is one line per event, then a five-line count summary, all on the
given stream and flushed line by line. Running it again over promoted items
changes nothing.

Usage (CLI):
    python3 -m pgai_agent_kanban.cm.shipped_bundle_items [--dry-run] \\
        <requirements_dir> [<shipped_tag> ...]
    python3 -m pgai_agent_kanban.cm.shipped_bundle_items [--dry-run] \\
        --treat-all-shipped <requirements_dir>
    With no SHIPPED_TAG and no --treat-all-shipped nothing is promoted.
    Exits 0 after processing; 2 is a usage error (--treat-all-shipped
    given with tags). A failed item write is not caught: it ends the run
    with a traceback and exit 1.

Usage (import):
    from pgai_agent_kanban.cm.shipped_bundle_items import promote_shipped_bundle_items
    counts = promote_shipped_bundle_items(requirements_dir, {"v1.2.3"}, dry_run=True)
    counts = promote_shipped_bundle_items(requirements_dir, None, dry_run=True)  # every bundle
"""

from __future__ import annotations

import argparse
import dataclasses
import pathlib
import re
import sys
from typing import TextIO
from collections.abc import Iterable

# Run by file path (``python3 .../shipped_bundle_items.py``) this module has no
# parent package and the interpreter puts only its own directory on sys.path,
# so the package import below would not resolve. Put this file's own package
# root -- the directory holding the pgai_agent_kanban/ this file sits in -- at
# the front of sys.path so the import resolves from the same tree, never
# another. Imported as a package member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.task_status import read_section  # noqa: E402

__all__ = ["PromotionCounts", "promote_shipped_bundle_items", "main"]

# A version at the start of the ## Target Version body.
_TARGET_VERSION_RE = re.compile(r"v[\d.]+", re.IGNORECASE)

# An item path in backtick-paren notation on a ## Bundled Items line.
_ITEM_PATH_RE = re.compile(r"\(\`([^`]+)\`\)")

# The ## Status heading and a "running" value on the next line, the value
# rewritten in place to "done".
_STATUS_RUNNING_RE = re.compile(
    r"^(##\s+Status\s*\n\s*)running(\s*)$",
    re.MULTILINE | re.IGNORECASE,
)


@dataclasses.dataclass
class PromotionCounts:
    """The counts the summary prints. ``promoted`` counts items promoted,
    or that would be under ``dry_run``."""

    bundles_processed: int = 0
    bundles_shipped: int = 0
    promoted: int = 0
    skipped_not_running: int = 0
    skipped_missing: int = 0


def promote_shipped_bundle_items(
    requirements_dir,
    shipped_tags: Iterable[str] | None,
    dry_run: bool,
    out: TextIO | None = None,
) -> PromotionCounts:
    """Promote the ``running`` items of every shipped bundle under
    *requirements_dir* to ``done`` (module docstring). *shipped_tags* is the
    set of shipped ``vX.Y.Z`` versions: empty promotes nothing and says so;
    ``None`` treats every bundle as shipped. Writes nothing under *dry_run*.
    Reports on *out* (default stdout) and returns the counts it summarised."""
    stream = out if out is not None else sys.stdout

    def say(line: str) -> None:
        print(line, file=stream, flush=True)

    requirements_dir = pathlib.Path(requirements_dir)
    every_bundle_shipped = shipped_tags is None
    shipped = set() if every_bundle_shipped else set(shipped_tags)
    counts = PromotionCounts()

    if not every_bundle_shipped and not shipped:
        say("  no shipped release tag given: no bundle is treated as shipped, nothing is promoted")

    for bundle_file in sorted(requirements_dir.glob("*.md")):
        if bundle_file.name.lower() in ("readme.md",):
            continue
        if "bundle" not in bundle_file.name.lower():
            continue

        try:
            text = bundle_file.read_text(encoding="utf-8")
        except OSError as e:
            say(f"  WARN: cannot read {bundle_file.name}: {e}")
            continue

        counts.bundles_processed += 1

        tv_body = read_section(text, "Target Version")
        tv_m = _TARGET_VERSION_RE.match(tv_body) if tv_body else None
        if not tv_m:
            say(f"  skip (no ## Target Version): {bundle_file.name}")
            continue
        target_version = tv_m.group(0)

        if not every_bundle_shipped and target_version not in shipped:
            continue

        counts.bundles_shipped += 1
        say(f"  bundle: {bundle_file.name} (version={target_version}, SHIPPED)")

        items_body = read_section(text, "Bundled Items", end_at_subheading=True) or ""
        item_paths = []
        for line in items_body.splitlines():
            m = _ITEM_PATH_RE.search(line)
            if m:
                item_paths.append(m.group(1).strip())

        if not item_paths:
            say("    no bundled item paths found")
            continue

        for path_str in item_paths:
            p = pathlib.Path(path_str)
            if not p.exists():
                say(f"    WARN: item not found (skipped): {path_str}")
                counts.skipped_missing += 1
                continue

            try:
                content = p.read_text(encoding="utf-8")
            except OSError as e:
                say(f"    WARN: cannot read {p.name}: {e}")
                counts.skipped_missing += 1
                continue

            status_m = _STATUS_RUNNING_RE.search(content)
            if not status_m:
                cur_body = read_section(content, "Status")
                cur_val = cur_body.split()[0] if cur_body else "no-status-field"
                say(f"    skip (status={cur_val}): {p.name}")
                counts.skipped_not_running += 1
                continue

            if dry_run:
                say(f"    [DRY RUN] would promote (running -> done): {p.name}")
            else:
                new_content = (
                    content[: status_m.start()]
                    + status_m.group(1)
                    + "done"
                    + status_m.group(2)
                    + content[status_m.end() :]
                )
                p.write_text(new_content, encoding="utf-8")
                say(f"    promoted (running -> done): {p.name}")
            counts.promoted += 1

    say("")
    say(f"  Bundles processed: {counts.bundles_processed}")
    say(f"  Bundles shipped:   {counts.bundles_shipped}")
    say(f"  Items promoted:    {counts.promoted}")
    say(f"  Items skipped (not running): {counts.skipped_not_running}")
    say(f"  Items missing:     {counts.skipped_missing}")
    return counts


def main(argv: list | None = None) -> int:
    """CLI entry point -- see the module docstring's "Usage (CLI)"."""
    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.cm.shipped_bundle_items",
        description=(
            "Promote the bug/priority items of every shipped requirements bundle "
            "in REQUIREMENTS_DIR from 'running' to 'done'. A bundle is shipped "
            "when its Target Version is one of the SHIPPED_TAG values; with no "
            "tags given, nothing is promoted. --treat-all-shipped treats every "
            "bundle as shipped, for a project with no dev tree to read tags from."
        ),
    )
    parser.add_argument("--dry-run", action="store_true", help="Report what would change; write nothing.")
    parser.add_argument(
        "--treat-all-shipped",
        action="store_true",
        help="Treat every bundle as shipped (no dev tree to read tags from). Takes no SHIPPED_TAG.",
    )
    parser.add_argument("requirements_dir", help="The project's requirements/ directory.")
    parser.add_argument(
        "shipped_tags",
        nargs="*",
        metavar="SHIPPED_TAG",
        help="A shipped version, branch_prefix removed (vX.Y.Z).",
    )
    args = parser.parse_args(argv)
    if args.treat_all_shipped and args.shipped_tags:
        parser.error(
            "--treat-all-shipped takes no SHIPPED_TAG; pass the shipped versions " "or --treat-all-shipped, not both"
        )
    shipped_tags = None if args.treat_all_shipped else args.shipped_tags
    promote_shipped_bundle_items(args.requirements_dir, shipped_tags, args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
