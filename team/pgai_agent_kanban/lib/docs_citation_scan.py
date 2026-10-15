"""
docs_citation_scan.py — Shared docs/<path>.md citation walk.

Two callers share this module rather than each keeping its own copy of the
citation regex and the closure walk (docs/coding-standards.md directive 1 —
one implementation per operation; two independent parsers drift until they
disagree about what counts as a citation):

  1. ``team/scripts/check_docs_citations.py`` — a seconds-fast,
     install-free check that walks the SOURCE tree (``team/`` as the seed
     root, the repository root as where a ``docs/<path>.md`` citation
     resolves) and reports every reachable citation that
     ``team/config/docs-manifest.txt`` does not name.
  2. ``team/tests/unit/test_installed_governance_docs_citations_resolve.py``
     — the release-time backstop that runs ``install.sh`` / ``upgrade.sh``
     end to end into a throwaway root and reports every reachable citation
     that does not resolve to a real file under that INSTALLED root.

Both callers seed the walk from the same governance set: the four flat
root governance files (``README.md``, ``DIRECTIVES.md``, ``OVERVIEW.md``,
``SOP-core.md``) plus every role file. What differs between the two
callers is what "resolved" means (manifest membership vs. on-disk
existence under an installed root) and where a resolved citation's own
content is read from next to continue the walk — so
``walk_docs_citation_closure`` takes both as caller-supplied predicates
rather than hardcoding either resolution scheme. Everything about WHERE
citations live and HOW the walk is structured — the regex, the seed-file
rule, the line-numbered scan, the breadth-first closure — lives here once.

The source tree's own layout is what makes one seed-file helper serve
both callers unchanged: ``team/`` holds the same four flat governance
names and the same ``roles/`` subdirectory that ``install.sh`` copies
onto an installed root's top level (see ``install.sh``'s ``team/$item``
loop). A caller resolving citations from the checked-out source tree
passes ``team/`` itself as ``governance_root``; a caller resolving
citations from an installed root passes that root. No name mapping is
needed — only the parent directory differs.

MATCHING A CITATION HOWEVER IT IS SPELLED
-------------------------------------------
A citation from outside the documentation directory only ever carries
the literal ``docs/<path>.md`` prefix, and only that form is matched
there. A document that already lives inside the documentation directory
also has two relaxed spellings available to it — a bare sibling filename
and a relative markdown link — and ``scan_citations``' optional
``docs_root`` parameter opts a caller into recognising both, scoped to
exactly the files that live under that root and resolved against each
such file's own directory. ``check_docs_citations.py`` opts in (the
seconds-fast source-tree check named in the bug this widening fixes);
``test_installed_governance_docs_citations_resolve.py`` does not, and
keeps its original prefixed-only behaviour unchanged — a deliberate,
narrower scope for a later ticket to widen if the installed-root
property turns out to need it too, not an oversight. See
``scan_citations`` and ``_normalize_relative_citation`` below for the
on-disk-existence guard that keeps an unrelated ``<name>.md`` mention
(a task's ``status.md``, a template, a repository-root document) from
being read as a documentation citation just because it shares docs/'s
file extension.

Every occurrence records which spelling produced it
(``CitationOccurrence.relaxed``), and ``walk_docs_citation_closure``
accepts a separate ``is_resolved_relaxed`` predicate for the relaxed
spellings. A caller that wants an allowance to cover only a bare or
relative mention -- never the prefixed ``docs/<path>.md`` form the
installed-root walk itself sees -- passes it there; a caller that passes
only ``is_resolved`` judges every spelling the same way.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

# ---------------------------------------------------------------------------
# The one citation pattern in the tree.
#
# Loose match for a docs/ citation: letters, digits, '.', '_', '-', and '/'
# (for a nested path like docs/api/<name>.md), ending in literal ".md".
# Deliberately loose (docs/coding-standards.md "keep regex loose") — this
# scans prose, not a fixed grammar, so it favors catching a citation over
# missing one to a stricter character class.
# ---------------------------------------------------------------------------
DOCS_CITATION_RE: re.Pattern[str] = re.compile(r"docs/[A-Za-z0-9._/-]+\.md")

# ---------------------------------------------------------------------------
# The two relaxed spellings a citation carries when the citing document
# already lives inside the documentation directory itself: a bare sibling
# filename (`` `operator-troubleshooting.md` ``) and a relative markdown
# link (`[operator-troubleshooting.md](operator-troubleshooting.md)`).
# Neither carries the literal "docs/" prefix DOCS_CITATION_RE requires, so
# both are scanned only when scan_citations is told the file it is reading
# is itself under a documentation root (its ``docs_root`` parameter) —
# never for a bare markdown filename mentioned from outside that root (a
# role file, a task README), which is not a documentation citation at all.
#
# _MARKDOWN_LINK_TARGET_RE reads a link's href only, not its link text, so
# a link whose text and href repeat the same target (the natural way an
# author writes a bare-filename-looking link) is read as one candidate
# rather than validated twice; scan_citations' own per-file dedup below
# still collapses either spelling to a single entry when both normalize to
# the same sibling path, which is what keeps a `[name.md](name.md)` link
# from producing two findings.
#
# _RELATIVE_MD_FILENAME_RE excludes a preceding '/', '.', '-' or word
# character (docs/coding-standards.md "keep regex loose" — the exclusion
# is deliberately narrow) so the tail of an already-matched
# `docs/<name>.md` citation is never re-matched here as a second, bare
# occurrence of the same file.
# ---------------------------------------------------------------------------
_MARKDOWN_LINK_TARGET_RE: re.Pattern[str] = re.compile(r"\[[^\]\n]*\]\(([^)\s]+\.md)\)")
_RELATIVE_MD_FILENAME_RE: re.Pattern[str] = re.compile(
    r"(?<![\w/.-])([A-Za-z0-9._-]+\.md)(?![\w-])"
)

# "README.md" is excluded from the BARE form only (still recognised as a
# markdown-link target, a much less ambiguous signal). It is the one
# basename genuinely common to a documentation index page AND many other
# kinds of file a documentation page's own prose legitimately names in
# passing — a task's README, a project's README, the installed root's own
# flat README.md among the other governance filenames a manifest example
# lists. A bare mention resolving on-disk under docs_root is not enough to
# disambiguate those from a real docs/README.md self-reference, so the
# bare form is held to the same bar coding-standards.md "keep regex loose"
# still respects: prefer under-matching a single, uniquely ambiguous name
# over flooding the report with citations no author meant.
_AMBIGUOUS_BARE_FILENAMES: frozenset[str] = frozenset({"readme.md"})

# ---------------------------------------------------------------------------
# The governance seed set — the four flat root files an installed root
# carries at its top level, mirroring install.sh's team/ item loop (roles
# is installed as a directory; these four names are installed as flat
# files at the root).
# ---------------------------------------------------------------------------
ROOT_GOVERNANCE_FILES: tuple[str, ...] = (
    "README.md",
    "DIRECTIVES.md",
    "OVERVIEW.md",
    "SOP-core.md",
)


def governance_seed_files(
    governance_root: Path, *, roles_dirname: str = "roles"
) -> list[Path]:
    """Every file that can itself carry a docs/ citation: the four flat
    root governance files plus every role file, resolved under
    *governance_root*.

    Works unchanged for either caller: pass an installed root to seed from
    an installed tree, or pass the source tree's ``team/`` directory to
    seed from the checkout — both lay the four flat names and the
    ``roles/`` directory out identically, one hop under the parent that
    differs (an install root vs. ``team/``).
    """
    seeds = [
        governance_root / name
        for name in ROOT_GOVERNANCE_FILES
        if (governance_root / name).is_file()
    ]
    roles_dir = governance_root / roles_dirname
    if roles_dir.is_dir():
        seeds.extend(sorted(roles_dir.glob("*.md")))
    return seeds


@dataclass(frozen=True)
class CitationOccurrence:
    """One docs/<path>.md citation found in one file, with the 1-based
    line number it first appears on in that spelling.

    *relaxed* is False for the literal ``docs/<path>.md`` prefix and True
    for a bare sibling filename or a relative markdown link (normalized to
    the same ``docs/<path>.md`` form in *cited*), so a caller can judge
    the two spellings differently."""

    line: int
    cited: str
    relaxed: bool = False


def _is_within(candidate: Path, root: Path) -> bool:
    """True when *candidate* is *root* itself or a descendant of it, both
    already resolved absolute paths."""
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def _normalize_relative_citation(
    citing_dir: Path, raw_target: str, docs_root: Path
) -> Optional[str]:
    """Resolve a bare filename or a markdown link's relative href — text a
    documentation-directory file wrote with no literal "docs/" prefix —
    against *citing_dir*, and return it in the same "docs/<path>.md" form
    DOCS_CITATION_RE produces, so neither caller's is_resolved nor
    resolved_target needs to know which spelling produced a citation.

    Returns None when *raw_target* is a URL (carries a scheme), resolves
    outside *docs_root* (not a sibling document, out of scope for this
    check), or does not exist on disk under *docs_root* at all. That last
    guard is what tells a genuine sibling-document reference (a real file
    the checkout carries, whether or not it is delivered — exactly the
    shape of a citation this check exists to catch) apart from a bare
    "<name>.md" token in prose naming something else entirely: a runtime
    state file (`release-state.md`), a task artifact (`status.md`), a
    template (`BUG-TEMPLATE.md`), or a repository-root document
    (`README.md`, `CHANGELOG.md`) that happens to share docs/'s file
    extension but was never meant as a documentation citation. A citation
    to a document the checkout genuinely carries still resolves this way
    even when that document is not manifest-listed — on-disk existence
    disambiguates what counts as a citation at all; manifest membership
    (the caller's own ``is_resolved``) is the separate, later question of
    whether it is delivered.
    """
    if "://" in raw_target or raw_target.startswith("/"):
        return None
    target_abs = (citing_dir / raw_target).resolve()
    if not _is_within(target_abs, docs_root) or not target_abs.is_file():
        return None
    return "docs/" + target_abs.relative_to(docs_root).as_posix()


def scan_citations(
    path: Path, *, docs_root: Optional[Path] = None
) -> list[CitationOccurrence]:
    """Return every distinct docs/<path>.md citation textually present in
    *path*, each paired with the 1-based line number of its first
    occurrence in the file.

    One entry per distinct cited path and spelling per file — a citation
    repeated later in the same file, in the same spelling, does not
    produce a second occurrence, matching the dedup-per-file behaviour the
    closure walk needs to avoid re-reporting the same finding once per
    repeated line. A file that names the same document both ways (a bare
    mention on one line, a prefixed ``docs/<path>.md`` on another) yields
    one occurrence of each, so a prefixed citation is never hidden behind
    an earlier relaxed mention of the same target.

    *docs_root* opts into the two relaxed spellings — a bare sibling
    filename and a relative markdown link — described at
    ``_MARKDOWN_LINK_TARGET_RE`` above, and is consulted only when *path*
    itself resolves under it: the scope guard that keeps a bare markdown
    filename mentioned from a role file or a task README (neither under
    *docs_root*) from ever being read as a documentation citation. Passing
    ``None`` (the default) reproduces the original prefixed-only behaviour
    unchanged, which is what every caller that has not opted in keeps
    getting.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    # Keyed on (cited, relaxed): see the docstring on why the two
    # spellings of one target are kept apart.
    first_line: dict[tuple[str, bool], int] = {}

    docs_root_resolved = docs_root.resolve() if docs_root is not None else None
    scan_relative_forms = docs_root_resolved is not None and _is_within(
        path.resolve(), docs_root_resolved
    )

    for line_no, line_text in enumerate(text.splitlines(), start=1):
        for match in DOCS_CITATION_RE.finditer(line_text):
            cited = match.group(0)
            first_line.setdefault((cited, False), line_no)

        if not scan_relative_forms:
            continue

        # Link targets first (never excluded — see _AMBIGUOUS_BARE_FILENAMES
        # above), then bare-token matches with the ambiguous-basename set
        # dropped, unioned into one candidate set so a name reachable both
        # ways (e.g. a genuine `[README.md](README.md)` link whose text
        # also stands alone as a bare token elsewhere on the line) is still
        # read as one citation via the outer per-file dict below.
        raw_targets = {
            m.group(1) for m in _MARKDOWN_LINK_TARGET_RE.finditer(line_text)
        }
        raw_targets.update(
            m.group(1)
            for m in _RELATIVE_MD_FILENAME_RE.finditer(line_text)
            if m.group(1).lower() not in _AMBIGUOUS_BARE_FILENAMES
        )
        for raw_target in raw_targets:
            cited = _normalize_relative_citation(
                path.parent, raw_target, docs_root_resolved
            )
            if cited is not None:
                first_line.setdefault((cited, True), line_no)

    return [
        CitationOccurrence(line=line_no, cited=cited, relaxed=relaxed)
        for (cited, relaxed), line_no in sorted(first_line.items())
    ]


@dataclass
class ClosureFinding:
    """One docs/ citation the walk could not resolve."""

    source: str
    line: int
    cited: str


@dataclass
class ClosureReport:
    """What a docs-citation closure walk verified, so a reader never sees
    a bare pass/fail."""

    scanned_files: list[str] = field(default_factory=list)
    citations_checked: int = 0
    findings: list[ClosureFinding] = field(default_factory=list)

    def describe(self) -> str:
        """Return a multi-line, human-readable summary of the scan.

        Names the governance files scanned and the number of distinct citations
        checked, then lists each citation that did not resolve as
        ``<source>:<line> cites <path>``, or says every citation resolved.
        """
        lines = [
            f"Scanned {len(self.scanned_files)} governance file(s) "
            f"(transitive closure): {', '.join(sorted(self.scanned_files))}",
            f"Checked {self.citations_checked} distinct docs/ citation(s).",
        ]
        if self.findings:
            lines.append(f"{len(self.findings)} citation(s) did NOT resolve:")
            for finding in self.findings:
                lines.append(
                    f"  - {finding.source}:{finding.line} cites "
                    f"{finding.cited!r}, which did not resolve"
                )
        else:
            lines.append("Every citation resolved.")
        return "\n".join(lines)


def walk_docs_citation_closure(
    seed_files: Iterable[Path],
    *,
    display_name: Callable[[Path], str],
    is_resolved: Callable[[str], bool],
    resolved_target: Callable[[str], Optional[Path]],
    docs_root: Optional[Path] = None,
    is_resolved_relaxed: Optional[Callable[[str], bool]] = None,
) -> ClosureReport:
    """Breadth-first walk of the transitive closure of docs/ citation
    edges, starting at *seed_files*.

    A resolved citation is itself scanned for further citations (this is
    what catches a document that itself cites a further docs/ path
    without hardcoding that edge) — the walk is bounded by the set of
    files actually visited, so it always terminates.

    Args:
        seed_files: The files to start the walk from (see
            ``governance_seed_files``).
        display_name: Formats a visited ``Path`` for the report (a
            caller-relative display form — e.g. relative to a repo root
            or an installed root).
        is_resolved: Given the citation exactly as matched (e.g.
            ``"docs/coding-standards.md"``), return True when it counts as
            resolved. A False result is recorded as a finding.
        resolved_target: Given the same citation string, return the real
            ``Path`` to read next to continue the walk one hop deeper, or
            ``None`` when there is nothing to read (nothing under this
            checkout/root answers to that citation, so there is no further
            edge to follow). Consulted only for a citation ``is_resolved``
            has already accepted — an unresolved citation is recorded as a
            finding directly, and ``resolved_target`` is never called for it.
        docs_root: Forwarded to ``scan_citations`` unchanged for every file
            the walk visits — opts every visited file into the two relaxed
            citation spellings (bare sibling filename, relative markdown
            link) when that file itself lives under this root. ``None``
            (the default) keeps every existing caller's behaviour exactly
            as it was: prefixed ``docs/<path>.md`` citations only.
        is_resolved_relaxed: Used in place of *is_resolved* for an
            occurrence spelled as a bare sibling filename or a relative
            markdown link (``CitationOccurrence.relaxed``); a prefixed
            ``docs/<path>.md`` occurrence always goes to *is_resolved*.
            ``None`` (the default) judges both spellings with
            *is_resolved*.
    """
    report = ClosureReport()
    seen: set[Path] = set()
    checked: set[str] = set()
    queue: list[Path] = list(seed_files)

    while queue:
        current = queue.pop()
        if current in seen:
            continue
        seen.add(current)
        if not current.is_file():
            continue

        rel = display_name(current)
        report.scanned_files.append(rel)

        for occurrence in scan_citations(current, docs_root=docs_root):
            checked.add(occurrence.cited)
            judge = (
                is_resolved_relaxed
                if occurrence.relaxed and is_resolved_relaxed is not None
                else is_resolved
            )
            if judge(occurrence.cited):
                target = resolved_target(occurrence.cited)
                if target is not None and target not in seen:
                    queue.append(target)
            else:
                report.findings.append(
                    ClosureFinding(
                        source=rel, line=occurrence.line, cited=occurrence.cited
                    )
                )

    report.citations_checked = len(checked)
    return report
