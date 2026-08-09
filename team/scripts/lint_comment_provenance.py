#!/usr/bin/env python3
"""
lint_comment_provenance.py
==========================
Lint gate: comments, docstrings, help text, log lines, and output strings in
production code must describe behavior — not process history.

Specifically, this linter flags lines in code files that contain internal
provenance references:

  - Bug IDs:       ``BUG-NNNN`` (e.g. BUG-0042, BUG-0362)
  - Task IDs:      ``CODER-YYYYMMDD-NNN``, ``WRITER-YYYYMMDD-NNN``, etc.
                   (six-role agent prefixes followed by a date and sequence)
  - Requirement versions cited as provenance:
                   ``v1.23.4-priority-bundle`` or ``v1.23.4-bug-bundle``
                   (version-bundle references that name an internal RC artifact)
  - Requirement-Goal citations: ``vX.Y.Z Goal N`` (e.g. ``v2.0.0 Goal 4``,
                   ``requirements v2.0.0 Goal 6``) — a release version paired
                   with a requirements-document "Goal" section number, cited
                   as the reason a behavior exists rather than a description
                   of the behavior itself.
  - Priority IDs:  ``PRIORITY-NNNN`` (e.g. PRIORITY-0004)

These references encode *why* the code was written rather than *what* it does.
They belong in the git commit message and the kanban task folder, not in the
source code that downstream readers and operators will see long after the
context has been squashed away.

Allowed exceptions (per coding-standards.md Directive 8 and CODER.md):
  1. **Format or usage examples** — lines that contain the provenance token
     only inside a documented example string, such as
     ``--key BUG-0042`` in a ``--help`` block where BUG-NNNN is the
     *format* of a key, not a reference to a specific bug.
     Signal: the token appears alongside "e.g.", "e.g,", "example:", "e.g.:",
     "Examples", or is inside a usage-block format string (``BUG-NNNN``
     with four literal N's).
  2. **Skip annotations citing an OPEN follow-up bug** — pytest ``skip()``
     calls or ``SKIP:`` comments that reference a real BUG-NNNN.  These are
     verified by the separate lint_skip_bug_gate.sh; they are exempt here.
     Signal: the line contains ``pytest.mark.skip`` or ``skip(`` or ``SKIP:``.
  3. **External references** — references to upstream issues, CVEs, or RFCs
     where the external identifier is being cited, not an internal kanban ID.
     Signal: the token is preceded or followed by ``CVE-``, ``RFC``, ``GH#``,
     or ``upstream``.
  4. **BUG-NNNN / PRIORITY-NNNN inside the prose surface** — release notes
     exist precisely to record which bugs and priorities a release
     addressed ("Bug Fixes", "Bugs Skipped", "Known Issues", "What
     Shipped"). A ``BUG-0052`` reference in ``release-notes/vX.Y.Z.md`` is
     the sanctioned, intended content of that surface, not a leaked
     internal provenance token — so files scanned as the *prose* surface
     (``--prose-dirs``, ``release-notes/`` by default) are exempt from the
     BUG-ID and PRIORITY-ID patterns specifically. Task IDs, version-bundle
     references, and Requirement-Goal citations are still enforced in
     prose — those name an internal build sequence, not a resolved item,
     and remain exactly the "why, not what" leak this lint exists to catch.
     This exception applies only to files gathered from the prose surface;
     the code surface enforces the full pattern set unchanged.

Per-line opt-out: add the comment
  ``# provenance-allowlist: <justification>``
within 5 lines BEFORE the flagged line to suppress that specific finding.

Scope
-----
  team/scripts/      — all .py and .sh files (non-recursively, top-level only)
  team/scripts/lib/  — all .py and .sh files (non-recursively)
  team/scripts/cm/   — all .py and .sh files (non-recursively)
  team/scripts/dashboard/lib/  — all .py and .sh files (non-recursively)
  team/pgai_agent_kanban/  — all .py files (recursively), excluding:
                             test files (*test_*.py, tests/ directories),
                             conftest.py
  release-notes/     — all .md files (non-recursively) — release-summary
                       prose is a shipped surface too: a ledger ID pasted
                       into a release note is exactly the same "why, not
                       what" leak this lint exists to catch in code, EXCEPT
                       for BUG-NNNN / PRIORITY-NNNN references, which this
                       surface exists to legitimately record (see
                       Exception 4 above).

Override the code surfaces via ``--code-dirs PATH [PATH ...]`` and the
prose surfaces via ``--prose-dirs PATH [PATH ...]``. The same provenance
patterns and the same allowed exceptions (format/usage examples, skip
annotations, external references, per-line opt-out) apply to both, with
one deliberate asymmetry: the prose surface additionally exempts BUG-ID
and PRIORITY-ID references (Exception 4) since that surface's job is to
name resolved bugs and priorities by ID. CHANGELOG.md is intentionally
out of scope here — its own generator (``changelog_writer.py``) already
gates provenance by emitting only public ``KI-`` identifiers, never raw
internal ledger IDs.

Test files (team/tests/, fixtures/, conftest.py) are deliberately excluded:
test and fixture code legitimately cites bug IDs in skip annotations and
in docstrings that document what behavior the test guards.  The
lint_skip_bug_gate.sh handles the subset of skip-citation requirements.

Exit codes
----------
  0   No findings.
  1   One or more findings (see stdout).
  2   Usage error or internal failure.

Diff-scope mode
---------------
The whole-directory scan above answers "is the tree clean?". A second,
narrower invocation mode answers "did this task's own diff introduce a
violation?" — the shape the CODER/WRITER completion gate needs (seconds,
not a tree scan), so a task is never blocked by pre-existing litter it
did not write.

  python3 scripts/lint_comment_provenance.py --diff-scope \\
      --worktree PATH --source-branch BRANCH --feature-branch BRANCH

Only lines *added or changed* by ``--feature-branch``'s diff against
``--source-branch`` (a three-dot / merge-base diff, computed inside
``--worktree``) are inspected. Lines outside the diff — including
pre-existing provenance litter elsewhere in the tree — are never
reported. Diff-scope mode reuses the exact same detection rule-source
(patterns, exceptions, opt-out window) as the whole-directory scan: a
rule added to ``_PROVENANCE_PATTERNS`` is enforced by both modes with no
forked copy. The finding-report shape (file:line, offending reference,
reword remedy) and exit codes (0/1/2) are identical between the two
modes. The prose-surface exception (Exception 4 above) also applies here:
a diffed line inside ``release-notes/`` is treated as the prose surface
and exempted from the BUG-ID / PRIORITY-ID patterns, matching the
whole-directory scan's behavior for the same file.

Usage
-----
  python3 scripts/lint_comment_provenance.py [--code-dirs PATH [PATH ...]]
                                             [--prose-dirs PATH [PATH ...]]
                                             [--verbose]

  python3 scripts/lint_comment_provenance.py --diff-scope
                                             --worktree PATH
                                             --source-branch BRANCH
                                             --feature-branch BRANCH
                                             [--verbose]

  --code-dirs PATH ...    Code directories to scan (defaults described above).
  --prose-dirs PATH ...   Release-summary prose directories to scan (defaults
                           to release-notes/ relative to the repo root).
  --diff-scope             Lint only the lines added/changed by
                           --feature-branch's diff against --source-branch,
                           instead of the whole-directory scan. Requires
                           --worktree, --source-branch, and --feature-branch.
  --worktree PATH          Git worktree containing both branches
                           (--diff-scope only).
  --source-branch BRANCH   Branch the diff is computed against, e.g.
                           rc/vX.Y.Z (--diff-scope only).
  --feature-branch BRANCH  Branch whose diff is linted, e.g.
                           feature/CODER-YYYYMMDD-NNN (--diff-scope only).
  --verbose, -v            Print each file name as it is scanned.

Examples
--------
  # From repo root (team/ as cwd):
  python3 scripts/lint_comment_provenance.py

  # With explicit dirs:
  python3 scripts/lint_comment_provenance.py \\
      --code-dirs team/scripts team/pgai_agent_kanban \\
      --prose-dirs release-notes

  # Diff-scope: lint only what a feature branch's diff introduced.
  python3 scripts/lint_comment_provenance.py --diff-scope \\
      --worktree /path/to/worktree \\
      --source-branch rc/vX.Y.Z \\
      --feature-branch feature/CODER-YYYYMMDD-NNN-slug
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

# ---------------------------------------------------------------------------
# Provenance token patterns
# ---------------------------------------------------------------------------

# Matches internal bug IDs: BUG-NNNN where NNNN is one or more digits.
_BUG_ID: re.Pattern[str] = re.compile(r"\bBUG-\d+\b", re.IGNORECASE)

# Matches task IDs: AGENT-YYYYMMDD-NNN-slug forms (six kanban agent prefixes).
# Agent prefixes: CODER, WRITER, TESTER, CM, PM, PO.
_TASK_ID: re.Pattern[str] = re.compile(
    r"\b(?:CODER|WRITER|TESTER|CM|PM|PO)-\d{8}-\d+\b",
    re.IGNORECASE,
)

# Matches PRIORITY-NNNN internal identifiers.
_PRIORITY_ID: re.Pattern[str] = re.compile(r"\bPRIORITY-\d+\b", re.IGNORECASE)

# Matches internal version-bundle references: vX.Y.Z-priority-bundle or
# vX.Y.Z-bug-bundle (internal RC artifact citations as provenance).
_VERSION_BUNDLE: re.Pattern[str] = re.compile(
    r"\bv\d+\.\d+\.\d+-(?:priority|bug)-bundle\b",
    re.IGNORECASE,
)

# Matches a release version paired with a requirements-document "Goal N"
# citation on the same line: vX.Y.Z Goal N, requirements vX.Y.Z Goal N, or
# vX.Y.Z <a few descriptive words> Goal N. The bounded gap between the
# version and "Goal N" allows the descriptive phrase real citations use
# (e.g. "v2.0.0 autonomous-block-resolution Goal 4") while still requiring
# both tokens to appear close together on one line, not coincidentally far
# apart in unrelated prose.
_VERSION_GOAL: re.Pattern[str] = re.compile(
    r"\bv\d+\.\d+\.\d+\b.{0,60}?\bGoal\s+\d+\b",
    re.IGNORECASE,
)

_PROVENANCE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (_BUG_ID, "BUG-ID"),
    (_TASK_ID, "task-ID"),
    (_PRIORITY_ID, "PRIORITY-ID"),
    (_VERSION_BUNDLE, "version-bundle"),
    (_VERSION_GOAL, "requirement-Goal"),
]

# Provenance labels exempted specifically on the prose surface (see
# Exception 4 in the module docstring). Release notes are the sanctioned
# place to record which bugs/priorities a release addressed, so BUG-ID and
# PRIORITY-ID references there are legitimate content, not a leak. Task IDs,
# version-bundle references, and Requirement-Goal citations are NOT in this
# set — those still name an internal build sequence and remain enforced on
# the prose surface exactly as they are on the code surface.
_PROSE_SURFACE_EXEMPT_LABELS: frozenset[str] = frozenset({"BUG-ID", "PRIORITY-ID"})

# Relative-path prefix (POSIX-style, repo-root-relative) treated as the
# prose surface in diff-scope mode, which has no ``--prose-dirs`` flag of
# its own. Kept in sync with the whole-directory scan's default
# ``prose_dirs`` (``release-notes/`` under the repo root).
_RELEASE_NOTES_PROSE_PREFIX = "release-notes/"

# ---------------------------------------------------------------------------
# Allowlist / exception patterns
# ---------------------------------------------------------------------------

# Exception 1: format or usage examples.
# A line that contains "e.g.", "example:", "Examples", format placeholders like
# "BUG-NNNN", or is a usage-example block is likely documentation, not live code.
_EXAMPLE_MARKERS: re.Pattern[str] = re.compile(
    r"(?:e\.g\.|e\.g,|example:|Examples?\b|BUG-NNNN|PRIORITY-NNNN|TASK-ID)",
    re.IGNORECASE,
)

# Exception 2: pytest skip / SKIP annotation lines.
_SKIP_ANNOTATION: re.Pattern[str] = re.compile(
    r"""(?:pytest\.mark\.skip|skip\s*\(|SKIP\s*:|\bxfail\b)""",
    re.IGNORECASE,
)

# Exception 3: external references (CVE, RFC, GH, upstream).
_EXTERNAL_REF: re.Pattern[str] = re.compile(
    r"""(?:CVE-\d|RFC\s*\d|GH#\d|upstream|external)""",
    re.IGNORECASE,
)

# Combined exception checker.
_EXCEPTION_PATTERNS: list[re.Pattern[str]] = [
    _EXAMPLE_MARKERS,
    _SKIP_ANNOTATION,
    _EXTERNAL_REF,
]

# Per-line opt-out marker (within 5 lines before the flagged line).
_OPT_OUT: re.Pattern[str] = re.compile(
    r"#\s*provenance-allowlist\s*:", re.IGNORECASE
)
_OPT_OUT_WINDOW = 5

# ---------------------------------------------------------------------------
# File-level whitelists: certain files are excluded entirely from the scan.
# ---------------------------------------------------------------------------

# Filenames whose content is inherently about internal IDs (e.g. the file
# that routes bug promotion, or this lint script's own regex patterns).
_FILE_WHITELIST: frozenset[str] = frozenset({
    "lint_comment_provenance.py",  # this file's own pattern strings
    "promote_bundled_items.py",    # CM tool that reads/routes BUG-NNNN IDs
    "changelog_writer.py",         # reads bug IDs from ledger to build CHANGELOG
})

# Path segment exclusions: any file whose path contains these segments is skipped.
_PATH_EXCLUDE_SEGMENTS: tuple[str, ...] = (
    "tests/",
    "test/",
    "fixtures/",
    "conftest",
    "__pycache__",
)


def _is_excluded_relpath(relpath: str) -> bool:
    """Return True if *relpath* (POSIX-style, relative to a repo root) should
    be excluded from scanning.

    Shared by both the whole-directory scan (via ``_is_excluded_path``) and
    the diff-scope scan (``_diff_scan``, which only ever has relative paths
    coming out of ``git diff``/``git show``) — one exclusion rule-source for
    both invocation modes.
    """
    name = relpath.rsplit("/", 1)[-1]
    if name in _FILE_WHITELIST:
        return True
    return any(seg in relpath for seg in _PATH_EXCLUDE_SEGMENTS)


def _is_excluded_path(path: Path, repo_root: Path) -> bool:
    """Return True if *path* should be excluded from the scan."""
    # Build the repo-relative path string for segment matching.
    try:
        rel = str(path.relative_to(repo_root)).replace("\\", "/")
    except ValueError:
        rel = str(path).replace("\\", "/")
    return _is_excluded_relpath(rel)


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


class _Finding(NamedTuple):
    """One provenance-reference finding, structured for both display and
    remedy-command derivation (see docs/coding-standards.md "Gates prescribe,
    not just diagnose")."""

    path: Path
    lineno: int
    label: str
    token: str


def _has_opt_out(lines: list[str], flagged_lineno: int) -> bool:
    """Return True if an opt-out marker appears within _OPT_OUT_WINDOW lines
    before *flagged_lineno* (1-based line number).
    """
    start = max(0, flagged_lineno - 1 - _OPT_OUT_WINDOW)
    end = flagged_lineno - 1
    for line in lines[start:end]:
        if _OPT_OUT.search(line):
            return True
    return False


def _is_exception(line: str) -> bool:
    """Return True if *line* matches any allowed-exception pattern."""
    for pat in _EXCEPTION_PATTERNS:
        if pat.search(line):
            return True
    return False


# ---------------------------------------------------------------------------
# Per-file scanner
# ---------------------------------------------------------------------------


def _scan_lines(
    path: Path,
    lines: list[str],
    allowed_linenos: "set[int] | None" = None,
    prose_surface: bool = False,
) -> list[_Finding]:
    """Scan *lines* (already split, 1-indexed by position) for provenance
    violations and return the structured findings.

    This is the single detection rule-source shared by every invocation
    mode: the whole-directory scan (``_scan_file``) and the diff-scope scan
    (``_diff_scan``) both call this function with the exact same pattern
    list, exception filters, and opt-out window. A rule added to
    ``_PROVENANCE_PATTERNS`` (or a new exception) is therefore enforced by
    both modes identically — there is no forked or duplicated rule copy.

    *allowed_linenos*, when given, restricts findings to lines whose
    1-based line number is a member of the set — this is how diff-scope
    mode judges only the lines a feature branch's diff actually
    added/changed, while still using the full file's ``lines`` (not just
    the diff hunks) so the opt-out lookback window can see context lines
    the diff itself did not touch. When ``None`` (the whole-directory scan
    default), every non-blank line is eligible.

    *prose_surface*, when True, marks *lines* as coming from the prose
    surface (release-notes by default) rather than the code surface. On the
    prose surface, labels in ``_PROSE_SURFACE_EXEMPT_LABELS`` (BUG-ID,
    PRIORITY-ID) are silently skipped — release notes are the sanctioned
    place to record which bugs/priorities a release addressed, so those
    two reference types are not a provenance leak there (see Exception 4 in
    the module docstring). Every other pattern (task-ID, version-bundle,
    requirement-Goal) is still enforced identically to the code surface.
    """
    findings: list[_Finding] = []

    for idx, line in enumerate(lines):
        lineno = idx + 1

        if allowed_linenos is not None and lineno not in allowed_linenos:
            continue

        # Skip blank lines.
        stripped = line.strip()
        if not stripped:
            continue

        # Check each provenance pattern.
        for pat, label in _PROVENANCE_PATTERNS:
            if not pat.search(line):
                continue

            # Prose-surface exception: release notes legitimately cite
            # resolved bug/priority IDs. Skip this pattern (not the whole
            # line) so other patterns still get a chance to flag the line.
            if prose_surface and label in _PROSE_SURFACE_EXEMPT_LABELS:
                continue

            # Apply exception filters.
            if _is_exception(line):
                break

            # Apply per-line opt-out.
            if _has_opt_out(lines, lineno):
                break

            match_token = pat.search(line).group(0)  # type: ignore[union-attr]
            findings.append(_Finding(path, lineno, label, match_token))
            break  # one finding per line is sufficient

    return findings


def _scan_file(
    filepath: Path, verbose: bool = False, prose_surface: bool = False
) -> list[_Finding]:
    """Scan *filepath* and return a list of structured findings.

    Read errors are reported as a synthetic finding (label ``READ-ERROR``,
    token the exception text) so they still surface in the failure output
    without requiring a second return type.
    """
    if verbose:
        print(f"  scanning: {filepath}", flush=True)

    try:
        text = filepath.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [_Finding(filepath, 0, "READ-ERROR", str(exc))]

    lines = text.splitlines(keepends=True)
    return _scan_lines(filepath, lines, prose_surface=prose_surface)


# ---------------------------------------------------------------------------
# Diff-scope mode: lint only the lines a feature branch's diff added/changed
# against its source branch, reusing _scan_lines as the single rule-source.
# ---------------------------------------------------------------------------

_DIFF_FILE_HEADER: re.Pattern[str] = re.compile(r"^\+\+\+ (?:b/(.+)|(/dev/null))$")
_DIFF_HUNK_HEADER: re.Pattern[str] = re.compile(
    r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@"
)

# Suffixes eligible for diff-scope scanning: the same code (.py/.sh) and
# prose (.md) surfaces the whole-directory scan covers, minus the
# directory restriction — a diff can touch a file anywhere in the tree, and
# the completion gate must judge whatever the task actually wrote.
_DIFF_SCOPE_SUFFIXES: tuple[str, ...] = (".py", ".sh", ".md")


def _run_git_diff(worktree: Path, source_branch: str, feature_branch: str) -> str:
    """Return the zero-context unified diff of *feature_branch* against
    *source_branch*'s merge-base (three-dot diff) inside *worktree*.

    Three-dot (not two-dot) diff scopes the comparison to commits unique to
    *feature_branch* since it branched from *source_branch* — the writer's
    own changes, not incidental drift *source_branch* picked up afterward.
    ``--unified=0`` suppresses context lines so every hunk header's
    ``+start`` maps directly onto added-line numbers with no context-line
    arithmetic required by the caller.
    """
    cmd = [
        "git",
        "-C",
        str(worktree),
        "diff",
        "--unified=0",
        f"{source_branch}...{feature_branch}",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"git diff {source_branch}...{feature_branch} failed "
            f"(exit {result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout


def _parse_diff_added_lines(diff_text: str) -> dict[str, set[int]]:
    """Parse a zero-context unified diff and return {relpath: {added lineno, ...}}.

    Only ``+`` lines (additions/changes as they land in the new file) are
    recorded; ``-`` lines (deletions) do not advance the new-file line
    counter and are never eligible for a diff-scope finding, since a
    deleted line cannot be a violation the feature branch introduced.
    """
    added: dict[str, set[int]] = {}
    current_file: str | None = None
    current_line: int | None = None

    for line in diff_text.splitlines():
        file_match = _DIFF_FILE_HEADER.match(line)
        if file_match:
            current_file = file_match.group(1)  # None for /dev/null (deleted file)
            current_line = None
            continue

        hunk_match = _DIFF_HUNK_HEADER.match(line)
        if hunk_match:
            current_line = int(hunk_match.group(1))
            continue

        if current_file is None or current_line is None:
            continue

        if line.startswith("+++") or line.startswith("---"):
            continue

        if line.startswith("+"):
            added.setdefault(current_file, set()).add(current_line)
            current_line += 1
        elif line.startswith("-"):
            pass  # deletion: does not occupy a line number in the new file
        else:
            # --unified=0 should never emit a bare context line, but stay
            # defensive rather than silently mis-tracking line numbers.
            current_line += 1

    return added


def _read_ref_file(worktree: Path, ref: str, relpath: str) -> str | None:
    """Return the text content of *relpath* as of *ref* inside *worktree*'s
    git repo, or None if the path does not exist at that ref (e.g. it was
    deleted by the diff — nothing to lint)."""
    cmd = ["git", "-C", str(worktree), "show", f"{ref}:{relpath}"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return None
    return result.stdout


def _diff_scan(
    worktree: Path,
    source_branch: str,
    feature_branch: str,
    verbose: bool = False,
) -> list[_Finding]:
    """Lint only the lines *feature_branch*'s diff against *source_branch*
    added or changed, inside *worktree*.

    Reuses ``_scan_lines`` — the identical detection rule-source
    ``_scan_file`` uses for the whole-directory scan — restricted to each
    file's diff-added line numbers. Pre-existing provenance litter outside
    the diff, in either untouched files or untouched lines of touched
    files, is never inspected.

    A diffed path under ``release-notes/`` is treated as the prose surface
    (see ``_RELEASE_NOTES_PROSE_PREFIX``), matching the whole-directory
    scan's default ``prose_dirs`` — so a task that edits release notes gets
    the same BUG-ID / PRIORITY-ID exemption here that the whole-directory
    scan applies to the same file.
    """
    diff_text = _run_git_diff(worktree, source_branch, feature_branch)
    added_by_file = _parse_diff_added_lines(diff_text)

    findings: list[_Finding] = []
    for relpath in sorted(added_by_file):
        if not relpath.endswith(_DIFF_SCOPE_SUFFIXES):
            continue
        if _is_excluded_relpath(relpath):
            continue

        if verbose:
            print(f"  scanning (diff-scope): {relpath}", flush=True)

        content = _read_ref_file(worktree, feature_branch, relpath)
        if content is None:
            continue  # deleted by the diff (or otherwise unreadable) -- skip

        lines = content.splitlines(keepends=True)
        findings.extend(
            _scan_lines(
                worktree / relpath,
                lines,
                allowed_linenos=added_by_file[relpath],
                prose_surface=relpath.startswith(_RELEASE_NOTES_PROSE_PREFIX),
            )
        )

    return findings


# ---------------------------------------------------------------------------
# Remedy block (docs/coding-standards.md "Gates prescribe, not just diagnose")
# ---------------------------------------------------------------------------


def _build_causes(findings: list[_Finding]) -> list[str]:
    """Return one cause bullet per finding, each naming file:line and the token."""
    causes: list[str] = []
    for f in findings:
        if f.label == "READ-ERROR":
            causes.append(f"{f.path}: could not be read — {f.token}")
            continue
        causes.append(
            f"{f.path}:{f.lineno}: [{f.label}] provenance reference '{f.token}' "
            "in code — comments and output must describe behavior, not internal IDs"
        )
    return causes


def _build_fix_commands(findings: list[_Finding], verify_command: str) -> list[str]:
    """Return the numbered, verbatim-runnable fix commands for *findings*.

    Each genuine finding (READ-ERROR entries are skipped — there is no
    derivable fix for an unreadable file) gets its own single-line ``sed``
    command that inserts the per-line opt-out marker
    (``# provenance-allowlist: <justification>``) directly above the
    offending line, in the same file, using a filled-in example
    justification (the house placeholder law's only permitted textual slot:
    a justification only a human ultimately owns, but never printed as a
    bare ``<placeholder>``).

    Findings are sorted by (path, descending line number) so that, within a
    single file, earlier insertions (at higher line numbers) never shift the
    line numbers still pending insertion (at lower line numbers) — an
    ascending-order insert would invalidate every subsequent line number in
    the same file.

    *verify_command* is appended verbatim as the final command — the caller
    builds it to match the invocation mode that produced *findings* (the
    whole-directory scan's ``--code-dirs``/``--prose-dirs`` re-run, or
    diff-scope's ``--diff-scope --worktree ... --source-branch ...
    --feature-branch ...`` re-run), proving the fix cleared the gate under
    the same mode that flagged it.
    """
    commands: list[str] = []
    genuine = [f for f in findings if f.label != "READ-ERROR"]
    ordered = sorted(genuine, key=lambda f: (str(f.path), -f.lineno))

    justification = "retained intentionally as a deliberate internal identifier reference"
    for f in ordered:
        commands.append(
            f"sed -i '{f.lineno}i# provenance-allowlist: {justification}' \"{f.path}\""
        )

    commands.append(verify_command)
    return commands


def _format_remedy_block(findings: list[_Finding], verify_command: str) -> str:
    """Assemble the full three-part remedy block required by
    docs/coding-standards.md ("Gates prescribe, not just diagnose"): cause(s)
    with concrete location, a numbered fix block of verbatim-runnable
    commands ending with the re-lint check, then the one-line rule the gate
    enforces.
    """
    causes = _build_causes(findings)
    commands = _build_fix_commands(findings, verify_command)

    lines = [
        f"lint_comment_provenance: FAIL — {len(findings)} finding(s)",
        "",
        "Cause(s):",
    ]
    for cause in causes:
        lines.append(f"  - {cause}")
    lines.append("")
    lines.append("To fix:")
    for i, command in enumerate(commands, start=1):
        lines.append(f"  {i}. {command}")
    lines.append("")
    lines.append(
        "Rule: comments, docstrings, help text, log lines, and output strings in "
        "production code must describe behavior, not process history — internal "
        "bug/task/priority IDs and version-bundle references belong in the commit "
        "message and task state, never in shipped code."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


_CODE_SUFFIXES: tuple[str, ...] = (".py", ".sh")
_PROSE_SUFFIXES: tuple[str, ...] = (".md",)


def _collect_files(
    scan_dirs: list[Path],
    repo_root: Path,
    suffixes: tuple[str, ...] = _CODE_SUFFIXES,
) -> list[Path]:
    """Collect all scannable files from *scan_dirs*, applying exclusions.

    *suffixes* selects which file extensions are gathered — code call sites
    pass the default ``.py``/``.sh`` pair; prose call sites (release-summary
    directories) pass ``(".md",)``. The exclusion rules (file whitelist,
    path-segment exclusions) apply identically regardless of suffix set.
    """
    collected: list[Path] = []
    seen: set[Path] = set()

    for scan_dir in scan_dirs:
        if not scan_dir.is_dir():
            continue
        for suffix in suffixes:
            for p in scan_dir.rglob(f"*{suffix}"):
                if p in seen:
                    continue
                seen.add(p)
                if not p.is_file():
                    continue
                if _is_excluded_path(p, repo_root):
                    continue
                collected.append(p)

    return sorted(collected)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="lint_comment_provenance.py",
        description=(
            "Enforce the comment-provenance coding standard: code files must not "
            "cite internal bug IDs, task IDs, priority IDs, or version-bundle names. "
            "These identifiers belong in commit messages and task state, not in "
            "source code. "
            "Exits 0 when no violations are found; exits 1 when violations are found."
        ),
    )
    parser.add_argument(
        "--code-dirs",
        metavar="PATH",
        nargs="*",
        default=None,
        help=(
            "Code directories to scan for provenance violations. "
            "Default: team/scripts/, team/scripts/lib/, team/scripts/cm/, "
            "team/pgai_agent_kanban/ (non-test Python/shell files) relative "
            "to the repo root inferred from this script's location."
        ),
    )
    parser.add_argument(
        "--prose-dirs",
        metavar="PATH",
        nargs="*",
        default=None,
        help=(
            "Release-summary prose directories to scan for provenance "
            "violations (.md files). Default: release-notes/ relative to "
            "the repo root inferred from this script's location. "
            "CHANGELOG.md is out of scope (gated separately by "
            "changelog_writer.py's public-ID emission)."
        ),
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print each file name as it is scanned.",
    )
    parser.add_argument(
        "--diff-scope",
        action="store_true",
        help=(
            "Lint only the lines added/changed by --feature-branch's diff "
            "against --source-branch, instead of the whole-directory scan. "
            "Requires --worktree, --source-branch, and --feature-branch."
        ),
    )
    parser.add_argument(
        "--worktree",
        metavar="PATH",
        default=None,
        help="Git worktree containing both branches (--diff-scope only).",
    )
    parser.add_argument(
        "--source-branch",
        metavar="BRANCH",
        default=None,
        help="Branch the diff is computed against, e.g. rc/vX.Y.Z (--diff-scope only).",
    )
    parser.add_argument(
        "--feature-branch",
        metavar="BRANCH",
        default=None,
        help=(
            "Branch whose diff is linted, e.g. feature/CODER-YYYYMMDD-NNN "
            "(--diff-scope only)."
        ),
    )
    return parser.parse_args(argv)


def _run_diff_scope(args: argparse.Namespace) -> int:
    """Handle the ``--diff-scope`` invocation mode: lint only the lines
    *args.feature_branch*'s diff against *args.source_branch* added/changed,
    inside *args.worktree*. Returns the process exit code (0/1/2), matching
    the whole-directory scan's contract.
    """
    missing = [
        flag
        for flag, val in (
            ("--worktree", args.worktree),
            ("--source-branch", args.source_branch),
            ("--feature-branch", args.feature_branch),
        )
        if not val
    ]
    if missing:
        print(
            "lint_comment_provenance: --diff-scope requires "
            + ", ".join(missing),
            file=sys.stderr,
        )
        return 2

    worktree = Path(args.worktree).resolve()
    if not worktree.is_dir():
        print(
            f"lint_comment_provenance: --worktree {worktree} is not a directory",
            file=sys.stderr,
        )
        return 2

    print(
        f"lint_comment_provenance: scanning diff-scope "
        f"{args.source_branch}...{args.feature_branch} in {worktree}",
        flush=True,
    )

    try:
        findings = _diff_scan(
            worktree, args.source_branch, args.feature_branch, verbose=args.verbose
        )
    except RuntimeError as exc:
        print(f"lint_comment_provenance: {exc}", file=sys.stderr)
        return 2

    if findings:
        lint_script = Path(__file__).resolve()
        verify_command = (
            f'python3 "{lint_script}" --diff-scope --worktree "{worktree}" '
            f'--source-branch "{args.source_branch}" '
            f'--feature-branch "{args.feature_branch}"'
        )
        print("\n" + _format_remedy_block(findings, verify_command), flush=True)
        return 1

    print(
        "lint_comment_provenance: OK (diff-scope) — 0 findings in "
        f"{args.source_branch}...{args.feature_branch}.",
        flush=True,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.diff_scope:
        return _run_diff_scope(args)

    # Infer the repo root: team/scripts/lint_comment_provenance.py → repo root.
    script_dir = Path(__file__).resolve().parent  # team/scripts/
    repo_root = script_dir.parent.parent           # repo root

    # Resolve code directories to scan.
    if args.code_dirs is not None:
        code_dirs = [Path(d).resolve() for d in args.code_dirs if d]
    else:
        code_dirs = [
            script_dir,                          # team/scripts/ (non-recursive via rglob but excludes tests)
            script_dir / "lib",                  # team/scripts/lib/
            script_dir / "cm",                   # team/scripts/cm/
            repo_root / "team" / "pgai_agent_kanban",  # package (rglob, non-test)
        ]

    # Resolve release-summary prose directories to scan. Release notes are a
    # shipped surface: a pasted task-ID or version-bundle reference here is
    # the same provenance leak the code scan exists to catch, just in prose
    # form instead of a comment. BUG-ID / PRIORITY-ID references are the one
    # exception (Exception 4) -- release notes exist to name resolved bugs.
    if args.prose_dirs is not None:
        prose_dirs = [Path(d).resolve() for d in args.prose_dirs if d]
    else:
        prose_dirs = [
            repo_root / "release-notes",         # release-notes/ (non-recursive, .md)
        ]

    code_files = _collect_files(code_dirs, repo_root, _CODE_SUFFIXES)
    prose_files = _collect_files(prose_dirs, repo_root, _PROSE_SUFFIXES)
    prose_file_set = set(prose_files)
    files = sorted(set(code_files) | prose_file_set)

    print(
        f"lint_comment_provenance: scanning {len(files)} file(s) in "
        + ", ".join(str(d) for d in code_dirs if d.is_dir())
        + " (code); "
        + ", ".join(str(d) for d in prose_dirs if d.is_dir())
        + " (prose)",
        flush=True,
    )

    all_findings: list[_Finding] = []
    for filepath in files:
        # Files gathered from --prose-dirs get the prose-surface exemption
        # (Exception 4): BUG-ID / PRIORITY-ID references are the sanctioned
        # content of that surface, not a leak. Code files enforce the full
        # pattern set unchanged.
        all_findings.extend(
            _scan_file(
                filepath, verbose=args.verbose, prose_surface=filepath in prose_file_set
            )
        )

    if all_findings:
        lint_script = Path(__file__).resolve()
        code_dirs_arg = " ".join(f'"{d}"' for d in code_dirs)
        prose_dirs_arg = " ".join(f'"{d}"' for d in prose_dirs)
        verify_command = (
            f'python3 "{lint_script}" --code-dirs {code_dirs_arg} '
            f'--prose-dirs {prose_dirs_arg}'
        )
        print(
            "\n" + _format_remedy_block(all_findings, verify_command),
            flush=True,
        )
        return 1

    print(
        f"lint_comment_provenance: OK — 0 findings across {len(files)} file(s).",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
