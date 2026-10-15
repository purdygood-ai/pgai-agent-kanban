#!/usr/bin/env python3
"""
check_docs_citations.py
========================
Seconds-fast, install-free authoring-time check for the invariant
``team/config/docs-manifest.txt`` states in its own header: every
``docs/<path>.md`` citation reachable from the governance files this
kanban installs (the four flat root files — ``README.md``,
``DIRECTIVES.md``, ``OVERVIEW.md``, ``SOP-core.md`` — plus every
``team/roles/*.md`` file, and every document THEY in turn cite) must be
named in the manifest, or the citation dangles the moment an operator
installs or upgrades — ``install.sh`` and ``team/scripts/upgrade.sh`` both
copy exactly the manifest's list into ``docs/`` and silently skip anything
that citation quietly assumed was there.

A citation counts however it is spelled. From outside ``docs/`` (a role
file, a flat root governance file) only the literal ``docs/<path>.md``
prefix counts. A document that already lives inside ``docs/`` also has a
bare sibling filename (`` `operator-troubleshooting.md` ``) and a
relative markdown link (``[name.md](name.md)``) available to it, and
both count too — resolved against the citing document's own directory,
and only when that target is a real file under ``docs/`` (what tells a
genuine sibling-document reference apart from an unrelated ``<name>.md``
mention: a task's ``status.md``, a template, a repository-root
document). ``team/config/docs-manifest.txt``'s own
``EXCLUDED-CITATION-TARGETS`` line names the small set of recorded
exclusions a delivered document inside ``docs/`` is still allowed to
name by one of those two relaxed spellings without the mention becoming
a finding — see that file's header. The allowance covers the relaxed
spellings only: a ``docs/<name>`` prefixed citation of one of those
names is a finding like any other unlisted page, the same verdict
``team/tests/unit/test_installed_governance_docs_citations_resolve.py``
reaches on an installed root, where that page does not exist.

WHY THIS SCRIPT EXISTS SEPARATELY FROM THE BACKSTOP TEST
----------------------------------------------------------
``team/tests/unit/test_installed_governance_docs_citations_resolve.py``
proves the same property, but only by actually running ``install.sh`` and
``upgrade.sh`` end to end into throwaway roots — a real, valuable, but
expensive proof (seconds of subprocess work) nobody can afford to run on
every edit to a role file or a docs/ page. This script proves the same
invariant directly against the checked-out source tree: no subprocess, no
throwaway root, no install — it reads ``team/config/docs-manifest.txt``
and walks the same seed set and the same transitive citation closure the
backstop test walks, checking each citation against the manifest's
entries instead of against files copied onto a fresh install. Both this
script and the backstop test call the one shared implementation of the
citation regex and the closure walk
(``pgai_agent_kanban.lib.docs_citation_scan``) — see that module's
docstring for why one implementation serves both.

This check reports; it never edits the manifest and it has no auto-fix
mode. Deciding that a new document should be installed is an authoring
decision — a check that silently added the line would install documents
nobody meant to ship.

Exit codes
----------
  0   Every reachable docs/ citation is named in the manifest.
  1   One or more reachable docs/ citations are not named in the manifest;
      see stdout for each one's citing file, line number, and the exact
      manifest line that clears it (or, for a recorded exclusion cited
      with the docs/ prefix, how to reword the citation instead).
  2   Usage error or internal failure (manifest missing or empty, governance
      root not found, or the walk found zero citations to check — the last
      of which means the seed-file list or the citation regex itself is
      broken, not that the tree has nothing to cite).

Usage
-----
  python3 team/scripts/check_docs_citations.py [--repo-root PATH]
                                               [--team-dir PATH]
                                               [--manifest PATH]

  --repo-root PATH   Repository root docs/ citations resolve against
                     (default: the parent of this script's team/ directory).
  --team-dir PATH    Root of the team/ directory to seed the walk from
                     (default: parent of this script's directory, i.e.
                     team/ relative to team/scripts/).
  --manifest PATH    The docs manifest to check citations against (default:
                     <team-dir>/config/docs-manifest.txt).

Examples
--------
  # From the repo root (the common case — no flags needed):
  python3 team/scripts/check_docs_citations.py

  # Against an explicit checkout:
  python3 team/scripts/check_docs_citations.py --repo-root /path/to/repo
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# team/ import path -- needed for the shared citation-scan module.
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent  # team/scripts
_TEAM_DIR_DEFAULT = _SCRIPT_DIR.parent  # team/
_team_str = str(_TEAM_DIR_DEFAULT)
if _team_str not in sys.path:
    sys.path.insert(0, _team_str)

from pgai_agent_kanban.lib.docs_citation_scan import (  # noqa: E402
    ClosureReport,
    governance_seed_files,
    walk_docs_citation_closure,
)

_DOCS_PREFIX = "docs/"

# A name on the manifest's EXCLUDED-CITATION-TARGETS line is a document a
# delivered page inside docs/ already names -- with the "Repository
# checkout only" caveat the reader needs -- by a bare sibling filename or
# a relative markdown link, the two relaxed spellings the scan reads only
# inside docs/. It is NOT a blanket allowance for every recorded exclusion
# in the manifest header's prose; the manifest's own comment states
# exactly which names are on it and why, and it is deliberately small.
# The allowance covers the relaxed spellings only. A prefixed
# `docs/<name>` citation of one of those names is a finding unless <name>
# is itself a manifest member: that is the spelling the installed-root
# walk resolves, and on an installed root the page is not there.
_EXCLUDED_CITATION_TARGETS_RE = re.compile(
    r"^#\s*EXCLUDED-CITATION-TARGETS:\s*(.+)$", re.MULTILINE
)


def _load_manifest_entries(manifest_path: Path) -> set[str]:
    """Parse team/config/docs-manifest.txt: one entry per line, '#'
    starts a comment, blank lines ignored. Mirrors install.sh's own
    manifest-parsing loop exactly (see install.sh's "Install the docs/
    reference set" section) so this script and the installer never
    disagree about what a line in the manifest means."""
    entries: set[str] = set()
    for raw_line in manifest_path.read_text(encoding="utf-8").splitlines():
        entry = raw_line.split("#", 1)[0].strip()
        if entry:
            entries.add(entry)
    return entries


def _load_excluded_citation_targets(manifest_path: Path) -> set[str]:
    """Parse the manifest's ``# EXCLUDED-CITATION-TARGETS:`` line (inside
    its "Recorded exclusions" section) into the set of basenames a
    delivered document inside docs/ may mention by a relaxed spelling (a
    bare sibling filename or a relative markdown link) without that
    mention counting as a dangling citation. A ``docs/``-prefixed citation
    of one of these names is not covered. Missing the line entirely is
    not an error -- an empty set, same as a manifest with no recorded
    exclusions yet."""
    match = _EXCLUDED_CITATION_TARGETS_RE.search(
        manifest_path.read_text(encoding="utf-8")
    )
    if not match:
        return set()
    return {name.strip() for name in match.group(1).split(",") if name.strip()}


def run_check(
    repo_root: Path, team_dir: Path, manifest_path: Path
) -> tuple[int, ClosureReport | None, str | None]:
    """Run the citation-vs-manifest closure walk.

    Returns (exit_code, report, setup_error). *report* is populated only
    when the walk actually ran (exit_code 0 or 1); *setup_error* is
    populated only on a usage/internal failure (exit_code 2).
    """
    if not manifest_path.is_file():
        return (
            2,
            None,
            f"Docs manifest not found at {manifest_path}. This file is "
            "required and ships with the kanban repo.",
        )

    manifest_entries = _load_manifest_entries(manifest_path)
    if not manifest_entries:
        return (
            2,
            None,
            f"Docs manifest at {manifest_path} is empty or contains only "
            "comments. At least one docs/ path is required.",
        )
    excluded_citation_targets = _load_excluded_citation_targets(manifest_path)

    seeds = governance_seed_files(team_dir)
    if not seeds:
        return (
            2,
            None,
            f"No governance seed files found under {team_dir}. Expected "
            "the flat root files (README.md, DIRECTIVES.md, OVERVIEW.md, "
            "SOP-core.md) and/or a roles/ directory there.",
        )

    def display_name(path: Path) -> str:
        try:
            return str(path.relative_to(repo_root))
        except ValueError:
            return str(path)

    def manifest_relative(cited: str) -> str:
        return cited[len(_DOCS_PREFIX) :]

    def is_resolved(cited: str) -> bool:
        # The prefixed `docs/<path>.md` spelling: resolved by manifest
        # membership alone, exactly what an installed root carries.
        return manifest_relative(cited) in manifest_entries

    def is_resolved_relaxed(cited: str) -> bool:
        # A bare sibling filename or a relative markdown link inside docs/.
        # A mention naming a recorded exclusion (docs-manifest.txt's
        # EXCLUDED-CITATION-TARGETS line) is a deliberate, caveated
        # pointer at a checkout-only page, not an omission. Matched on
        # basename so a nested relative form (`api/<name>.md`) is
        # allowed the same way a bare sibling one is.
        if is_resolved(cited):
            return True
        return manifest_relative(cited).rsplit("/", 1)[-1] in excluded_citation_targets

    def resolved_target(cited: str) -> Path | None:
        # walk_docs_citation_closure calls resolved_target only for a
        # citation is_resolved (or is_resolved_relaxed) already accepted. A
        # relaxed mention accepted only because its target is a recorded
        # exclusion (not a manifest
        # member) is, by definition, a document nothing installs -- there
        # is no further edge under the delivered set to follow, so the
        # walk stops there rather than reading a checkout-only document's
        # own further citations, which say nothing about what an installed
        # root can reach.
        if manifest_relative(cited) not in manifest_entries:
            return None
        target = repo_root / cited
        return target if target.is_file() else None

    report = walk_docs_citation_closure(
        seeds,
        display_name=display_name,
        is_resolved=is_resolved,
        resolved_target=resolved_target,
        docs_root=repo_root / "docs",
        is_resolved_relaxed=is_resolved_relaxed,
    )

    if report.citations_checked == 0:
        return (
            2,
            None,
            "The walk scanned every governance seed file but found zero "
            "docs/ citations to check — the seed-file list or the "
            "citation regex is broken, not that the tree has nothing to "
            "cite.",
        )

    return (1 if report.findings else 0, report, None)


def _print_report(
    report: ClosureReport,
    manifest_path: Path,
    excluded_citation_targets: frozenset[str] = frozenset(),
) -> None:
    if not report.findings:
        print(
            f"check_docs_citations: ok — {len(report.scanned_files)} "
            f"governance file(s) scanned, {report.citations_checked} "
            f"docs/ citation(s) checked, all named in {manifest_path}.",
            flush=True,
        )
        return

    print(
        f"check_docs_citations: FAIL — {len(report.findings)} docs/ "
        f"citation(s) reachable from the governance files are not named "
        f"in {manifest_path}.\n",
        flush=True,
    )
    print("Cause(s):", flush=True)
    for finding in report.findings:
        print(
            f"  - {finding.source}:{finding.line} cites {finding.cited!r}, "
            f"which {manifest_path} does not name",
            flush=True,
        )

    cited_rels = {finding.cited[len(_DOCS_PREFIX) :] for finding in report.findings}
    # A recorded exclusion is checkout-only by decision; the fix for a
    # prefixed citation of one is rewording the citation, never adding the
    # page to the delivery list.
    excluded_rels = sorted(
        rel for rel in cited_rels if rel.rsplit("/", 1)[-1] in excluded_citation_targets
    )
    remedy_lines = sorted(cited_rels - set(excluded_rels))
    print("\nTo fix:", flush=True)
    step = 1
    if remedy_lines:
        print(f"  {step}. Add the following line(s) to {manifest_path}:", flush=True)
        for line in remedy_lines:
            print(f"       {line}", flush=True)
        step += 1
    if excluded_rels:
        print(
            f"  {step}. These are recorded exclusions in {manifest_path} "
            "(EXCLUDED-CITATION-TARGETS) -- repository checkout only, never "
            "delivered. Do not add them to the manifest. Drop the docs/ "
            "prefix from the citation and say the page is in the repository "
            "checkout only (from a page inside docs/, a bare sibling "
            "filename carrying that caveat is allowed), or remove the "
            "citation:",
            flush=True,
        )
        for rel in excluded_rels:
            print(f"       {_DOCS_PREFIX}{rel}", flush=True)
        step += 1
    print(
        f"  {step}. Re-run: python3 team/scripts/check_docs_citations.py",
        flush=True,
    )
    print(
        "\nRule: every docs/<path>.md citation reachable from the "
        "installed governance files (the flat root files plus "
        "roles/*.md) and their own transitive citations must be named in "
        f"{manifest_path}, or install.sh/upgrade.sh silently omit it from "
        "an installed or upgraded root.",
        flush=True,
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="check_docs_citations.py",
        description=(
            "Check that every docs/<path>.md citation reachable from this "
            "kanban's governance files (the flat root files plus "
            "roles/*.md, and their own transitive citations) is named in "
            "team/config/docs-manifest.txt — the single source install.sh "
            "and upgrade.sh both read to deliver docs/. A citation counts "
            "however it is spelled: the literal docs/ prefix always, and "
            "-- when the citing document already lives inside docs/ -- a "
            "bare sibling filename or a relative markdown link too, "
            "resolved against that document's own directory. Runs "
            "directly against the source tree; no install, no subprocess. "
            "On a finding, prints the citing file, its line number, the "
            "cited path, and how to clear it: for a cited page not yet "
            "delivered, the exact manifest line to add; for a recorded "
            "exclusion (EXCLUDED-CITATION-TARGETS), never a manifest line, "
            "but a reworded citation (drop the docs/ prefix and say the "
            "page is in the repository checkout only) or a removed one. "
            "Reports only — it never edits the manifest."
        ),
    )
    parser.add_argument(
        "--repo-root",
        metavar="PATH",
        help=(
            "Repository root docs/ citations resolve against (default: "
            "the parent of this script's team/ directory)."
        ),
    )
    parser.add_argument(
        "--team-dir",
        metavar="PATH",
        help=(
            "Root of the team/ directory to seed the walk from (default: "
            "parent of this script's directory)."
        ),
    )
    parser.add_argument(
        "--manifest",
        metavar="PATH",
        help=(
            "Docs manifest to check citations against (default: "
            "<team-dir>/config/docs-manifest.txt)."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the docs-manifest citation check and print each dangling citation.

    Exit codes: 0 every reachable ``docs/`` citation is named in the manifest;
    1 one or more are not, each printed with its citing file, line and the
    manifest line that clears it; 2 the team directory was not found, or a
    usage error (argparse).
    """
    args = _parse_args(argv)

    team_dir = Path(args.team_dir).resolve() if args.team_dir else _TEAM_DIR_DEFAULT
    repo_root = Path(args.repo_root).resolve() if args.repo_root else team_dir.parent
    manifest_path = (
        Path(args.manifest).resolve() if args.manifest else team_dir / "config" / "docs-manifest.txt"
    )

    if not team_dir.is_dir():
        print(
            f"check_docs_citations: ERROR: team directory not found: {team_dir}\n"
            "Use --team-dir to specify an alternative path.",
            file=sys.stderr,
        )
        return 2

    print(
        f"check_docs_citations: scanning governance files under {team_dir} "
        f"against {manifest_path}",
        flush=True,
    )

    exit_code, report, setup_error = run_check(repo_root, team_dir, manifest_path)

    if setup_error is not None:
        print(f"check_docs_citations: ERROR: {setup_error}", file=sys.stderr)
        return exit_code

    assert report is not None  # exit_code in {0, 1} always carries a report
    _print_report(
        report,
        manifest_path,
        frozenset(_load_excluded_citation_targets(manifest_path)),
    )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
