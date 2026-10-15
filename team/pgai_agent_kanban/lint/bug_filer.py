"""
bug_filer.py -- the hygiene lint's aggregate bug filer.

team/scripts/lint_hygiene.py emits one finding PER INSTANCE of a hygiene
violation -- one provenance citation on one line, one version-named test
function, one drifted comment -- and both gated runners merge those
instances into the shared quality-class findings file
(team/scripts/lib/gated_runner_lint.sh's schema) alongside every other
quality-class lint's own findings. Filing one bug per instance would turn
each finding into its own report, each costing a full bundle slot, a
decomposition, and a CODER wake for what amounts to a one-line reword.
This module reads that findings file, groups the
hygiene-lint instances by their own "class" field (lint_hygiene.py's
registered classes -- provenance, version-named-test, comment-drift, and
any future registrant), and files AT MOST ONE bug per class per run,
listing every instance of that class in the one report -- deduplicated
against a bug already open or running for the same class, the same "cites
an OPEN bug" rule team/roles/TESTER.md's Step 6 states and the DEEP-VERIFY
tier's own pgai_agent_kanban.deep_verify.bug_filer already follows for its
own dedup key (a failing (engine, flavor) pair set). This module factors
the parts that behave identically to that filer -- BUG-NNNN claiming,
scanning bugs/ for a still-live dedup candidate -- into the shared
pgai_agent_kanban.lib.bug_filing module rather than reimplementing them
(docs/coding-standards.md directive 1).

Only hygiene-lint findings are this filer's concern. A findings file also
carries findings from every other quality-class lint the gated runners
run (the dual-channel-pair lint, the census-compliance lint, the coarse
pass/fail lints wrapped by gated_runner_run_quality_lint) -- those carry
a different shape entirely (no "class" field, or a "class" field with
different semantics) and are silently skipped: this module only acts on a
finding whose "lint" field is exactly "hygiene".

This filer never blocks anything and never touches an RC: it writes at
most a handful of new files into the project's bugs/ directory (one per
hygiene class that has fresh findings this run), and nothing else -- no
release-state.md, no task status.md, no queue file, no HALT-family file,
no branch. A findings file with zero hygiene-lint findings files nothing
and returns cleanly (see run_hygiene_findings_filer's empty-input case).

Severity
--------
Every filed report rates "low" against team/SOP-core.md's "Rating a
Finding" rubric. lint_hygiene.py's own findings are never a reason to fail
a build (its process exit code is always 0 except a malformed sidecar), so
by the rubric's own test -- does the defect break functionality, with no
workaround? -- a hygiene finding can never be more than cosmetic: the
affected code runs correctly either way, findings only concern comments,
naming, and provenance litter. This module does not re-derive severity per
finding; nothing in a hygiene finding's own shape would justify rating one
class above another.

Filed-bug shape
----------------
Follows team/templates/project/release/BUG-TEMPLATE.md's header/section
order, exactly as pgai_agent_kanban.deep_verify.bug_filer's own report
does, with two additive sections this module owns: "## Hygiene Lint
Class" (the single class name the bug covers -- the dedup key) and
"## Findings" (one line per instance the run named, "file:line -- cause") and
"## Observed At Commit" (the git commit this filing observed the surviving
findings at -- see "Tree-cleanliness gate" below). The `**Filed By:**` line
follows the self-documenting label convention (team/roles/TESTER.md): the
token `hygiene-lint (autonomous)`, then ` -- ` and one plain sentence
naming how many instances of which class were found.

Repository root resolution
---------------------------
Every gate below needs the checkout whose commits the findings describe --
the tree the findings were *scanned* from, not necessarily the tree this
module's own file happens to ship in. Resolved in this order:

1. An explicit *repo_root* argument -- the caller already knows the exact
   tree it scanned (this is how the test suite exercises a throwaway git
   repository, and how any future caller with its own scan root should
   call in).
2. The `PGAI_DEV_TREE_PATH` environment variable, when set -- the gated
   runners (team/scripts/run-unit-tests.sh, run-integration-tests.sh) and
   team/scripts/deep-verify.sh all export this to their own checkout root
   before running anything that observes commits, so a filer invoked from
   either runner already has the right answer sitting in the environment
   without needing to guess.
3. This module's own self-located checkout guess (`_REPO_ROOT`), kept only
   for an ad hoc invocation that supplies neither of the above.

That third form is directory-depth-sensitive: it assumes its sibling
`scripts/lint_hygiene.py` sits two path segments below the checkout root
(`team/scripts/`), true in a dev-tree checkout and false in the live
install, where the same file ships one segment below the install root
(`scripts/`) with no wrapping `team/` directory. Guessing from the module's
own path alone therefore lands on a real directory that is simply not a
git repository at all when this code runs from the live install -- a
structural mismatch, not a transient probe failure. Nothing in this module
tries to repair that arithmetic for every possible install shape; instead,
`file_or_cite_hygiene_bugs` refuses to file anything at all when no commit
resolves for whatever root it was handed (see "Commit resolution gate"
below), so a wrong guess never becomes a bug nobody can reproduce.

Commit resolution gate
------------------------
Before any per-class filing decision, this run's own commit is resolved
once via `_git_head_commit`. When it cannot be resolved -- the root is not
a git repository, has no commits yet, or git is missing from PATH --
`file_or_cite_hygiene_bugs` files nothing at all this run and prints the
reason to stderr (`_note_unresolvable_commit`), rather than stamping the
literal `unknown` on a filed bug that nobody can check against any tree.
This is the fail-closed counterpart to the two gates below, which are
deliberately fail-open for a different, narrower question -- "is this
specific finding provably wrong" -- that only makes sense once a commit to
check it against actually exists.

Tree-cleanliness gate
----------------------
A finding only earns a durable bug when the file it cites carries no
uncommitted change relative to HEAD. A finding whose site lives in an
agent's in-progress working-copy edit is transient by definition -- the
running agent fixes or discards the edit in the same session, and the
runner's own printed output and findings JSON already carry the finding
for that purpose. This module checks tree state once per run (a single
`git status --porcelain` call against every file the findings mention) and
drops any finding whose file is dirty before grouping into a filing
decision; a class left with no surviving findings is filed nowhere. A bug
that IS filed records the commit it was observed at.

Commit-content proof gate
--------------------------
The tree-cleanliness gate answers "is this file dirty right now" -- it does
not answer "does the commit this filing is about to stamp actually contain
the text this finding cites". Those two questions can come apart: a filing
can record an `## Observed At Commit` whose tree, read back with `git show
<commit>:<file>`, does not contain the literal a finding names at the line
it names. Whatever the exact mechanism -- a scan and a git query resolving
different roots, or a findings path-shape mismatch against `git status
--porcelain`'s own output -- the class of defect is the same either way:
trusting that a finding describes the commit the filer can see, without
checking, files a bug nobody can reproduce from the commit it names.

This section closes that gap directly rather than by relying solely on the
tree-cleanliness gate above: after the dirty-file filter, and right before
a class is grouped into a filing decision, every surviving finding is
re-read at the commit this run is about to stamp
(`_finding_provable_at_commit`, one `git show` per distinct file, cached
per run). A finding whose file resolves at that commit (the git object is
readable) but whose own cited line does not contain the token it names is
dropped -- the same "unactionable, so file nothing" outcome the
tree-cleanliness gate already applies to a dirty file, and for the same
reason: a bug nobody can reproduce from the commit it names costs a
decomposition and an implementer's session for nothing. A class left with
no provable findings is filed nowhere, exactly like a class left with no
clean findings, and this run's stderr says so (see
`_note_unprovable_findings`) so the outcome is visible in the run's own
output, not only inferred from an empty result.

This gate is deliberately narrower than "the file must exist here" in one
respect: when the file cannot be read at the recorded commit AT ALL (no
git repository, git missing from PATH, or the path simply is not present
in that commit's tree -- the last of these matches a caller passing a
synthetic finding whose file was never real to begin with, as this
module's own test suite does for pure aggregation coverage), the finding
is treated as provable, mirroring the tree-cleanliness gate's own
"unanswerable is not a reason to change filing behaviour" stance above.
The gate exists to catch "the commit's tree disagrees with what was
filed", not to demand every finding's file be independently resolvable
outside of the scan that produced it.

Usage (import):
    from pgai_agent_kanban.lint.bug_filer import file_or_cite_hygiene_bugs
    results = file_or_cite_hygiene_bugs(
        bugs_dir, project="demo", findings_path="/path/to/findings.json")
    # results: list[FilingResult], one entry per hygiene class that had at
    # least one finding surviving the tree-cleanliness gate (empty list
    # when the findings file carries no hygiene-lint findings at all, when
    # every hygiene-lint finding's file is dirty, or when no commit
    # resolves for the run at all -- see "Commit resolution gate" above).

Usage (CLI, consumed by team/scripts/lib/lint_bug_filer.sh via
team/scripts/lib/pp_run_ops.sh):
    python3 -m pgai_agent_kanban.lint.bug_filer \\
        <bugs_dir> <project> <findings_file>
    Prints one "key=value"-per-line block per class result, blocks
    separated by a blank line:
        class=<name>
        outcome=filed|duplicate
        bug_id=BUG-NNNN
        bug_path=<abs path, or empty for outcome=duplicate>
        count=<N>
    Prints nothing (and exits 0) when the findings file carries no
    hygiene-lint findings, or cannot be read at all -- a missing or
    malformed findings file is not this filer's failure to report loudly
    about (see module docstring's "never blocks anything"); the runner
    that would have produced it already reports its own diagnostics.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Run by file path (``python3 .../bug_filer.py``) or loaded from a file
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

from pgai_agent_kanban.lib.bug_filing import import_bug_scanner, iter_open_or_running_bugs  # noqa: E402

# ---------------------------------------------------------------------------
# Reproduction-command class check: ask team/scripts/lint_hygiene.py which
# --classes tokens it actually accepts, rather than keeping a second copy of
# its registry here (a second copy would drift -- see
# _registered_lint_classes' own docstring).
# ---------------------------------------------------------------------------

# This file sits at team/pgai_agent_kanban/lint/bug_filer.py; the sibling
# lint script always ships at team/scripts/lint_hygiene.py in the same
# checkout. Self-locate from this file's own path (never from a scan-root
# or dev-tree candidate list) so the right sibling is found in every tree
# that carries this module -- see team/roles/CODER.md, "Each tree finds
# itself".
_LINT_HYGIENE_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "lint_hygiene.py"

# The checkout root: the directory containing "team/", one level above
# _LINT_HYGIENE_SCRIPT's own "team/scripts" directory. Self-located the same
# way as that sibling path above. This is the LAST-RESORT fallback only --
# see _resolve_repo_root and the module docstring's "Repository root
# resolution" -- because this arithmetic assumes a "team/scripts/" depth
# that does not hold in the live install's flattened "scripts/" layout.
_REPO_ROOT = _LINT_HYGIENE_SCRIPT.parents[2]


def _resolve_repo_root(repo_root: "str | Path | None") -> Path:
    """Resolve the checkout whose commits this run's findings should be
    observed against. See the module docstring's "Repository root
    resolution" section for the full reasoning; this is the mechanism:

    1. *repo_root*, when the caller passed one explicitly.
    2. The `PGAI_DEV_TREE_PATH` environment variable, when set -- the
       gated runners already export this to their own scan root.
    3. This module's own self-located guess (_REPO_ROOT), kept for an ad
       hoc invocation that supplies neither -- correct inside a dev-tree
       checkout, and answered safely even when wrong, because
       file_or_cite_hygiene_bugs refuses to file anything when the root
       this function returns yields no resolvable commit.
    """
    if repo_root is not None:
        return Path(repo_root)
    env_root = os.environ.get("PGAI_DEV_TREE_PATH")
    if env_root:
        return Path(env_root)
    return _REPO_ROOT


# ---------------------------------------------------------------------------
# Dedup key: this filer's own "## Hygiene Lint Class" section, read the same
# narrow way pgai_agent_kanban.deep_verify.bug_filer reads its own
# "## Deep-Verify Pairs" section -- one section, specific to this filer's
# report shape, never the full BUG-TEMPLATE.md field set.
#
# Dedup-against-running -- decision recorded here (settled, not narrowed)
# ------------------------------------------------------------------------
# find_existing_bug_for_class treats a bug whose "## Status" is "running"
# the same as one that is "open": either suppresses a fresh filing for that
# class. That choice has a real cost: a "running" bug is flipped to "done"
# by the release step at ship, so a newly-discovered site of the same class
# found by a later verification run -- while the earlier bug is still
# running, and therefore still suppressing -- gets filed nowhere by this
# module; a verifier has had to hand-file a bug directly for exactly that
# reason before.
#
# This module deliberately leaves the behaviour as-is rather than narrowing
# it (e.g. requiring every current finding's rendered line to already
# appear in the running bug's own recorded "## Findings" section before
# treating it as covered). Two reasons:
#
# 1. The module's entire reason to exist is collapsing dozens of
#    one-bug-per-finding hygiene reports (see module docstring) into one
#    bug per class per run. A coverage-diff narrowing re-opens exactly that
#    door: a running bug's own "## Findings" snapshot goes stale the moment
#    an unrelated edit shifts a line number, even when the underlying
#    instance did not change, so the narrowed check would start filing a
#    fresh "duplicate-in-substance" bug most runs -- the flood this module
#    was built to stop, reintroduced through the coverage check instead of
#    through per-instance filing.
# 2. Nothing is lost permanently under the current behaviour, only delayed
#    by at most one RC cycle: once the running bug ships and its Status
#    flips to "done", it stops matching ELIGIBLE_DEDUP_STATUSES
#    (pgai_agent_kanban.lib.bug_filing), so the very next run that still
#    finds the class red files a fresh bug covering whatever instances are
#    still outstanding at that point -- new sites included. A quality-class
#    finding healing on a later turn rather than necessarily this one is
#    the kanban's own standing operating doctrine (team/SOP.md, "The
#    Loop"). The interim gap -- a newly-discovered instance goes untracked
#    by an automated filing while the older bug is still in flight -- is
#    the known, accepted cost, and a verifier's ability to hand-file a bug
#    directly is the documented remedy for that window, not a symptom of
#    something broken.
#
# This is the "left as-is, reason written into the filer module" branch of
# that decision, not the "narrowed" branch.
# ---------------------------------------------------------------------------

_HYGIENE_CLASS_SECTION = "## Hygiene Lint Class"
_HYGIENE_CLASS_RE = re.compile(
    r"^##\s+Hygiene Lint Class\s*\n\s*(\S+)", re.MULTILINE | re.IGNORECASE
)


def _read_hygiene_class(text: str) -> "str | None":
    match = _HYGIENE_CLASS_RE.search(text)
    return match.group(1).strip() if match else None


def find_existing_bug_for_class(bugs_dir: str, cls: str) -> "str | None":
    """Return the BUG-NNNN id of an open/running bug in *bugs_dir* whose
    recorded '## Hygiene Lint Class' equals *cls*, or None when no such bug
    exists. Scans in filename-sorted order (via iter_open_or_running_bugs)
    for a deterministic pick."""
    for bug_id, text in iter_open_or_running_bugs(bugs_dir):
        if _read_hygiene_class(text) == cls:
            return bug_id
    return None


# ---------------------------------------------------------------------------
# Findings-file reading (team/scripts/lib/gated_runner_lint.sh's schema)
# ---------------------------------------------------------------------------


def _load_hygiene_findings(findings_path: str) -> "dict[str, list[dict]]":
    """Read the gated-runner findings file at *findings_path* and group
    every finding whose "lint" field is "hygiene" by its own "class" field
    (lint_hygiene.py's per-finding dict shape -- see its module docstring's
    "Findings output"). Findings from any other lint are not this filer's
    concern and are ignored -- see module docstring.

    Returns {} when the file is missing, unreadable, malformed, or carries
    no hygiene-lint findings -- callers treat that identically to "no
    findings" (see file_or_cite_hygiene_bugs's empty-input case): a
    findings-file problem is the runner's own diagnostic to report, not
    this filer's.
    """
    try:
        with open(findings_path, "r", encoding="utf-8") as handle:
            doc = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}

    findings = doc.get("findings", []) if isinstance(doc, dict) else []
    grouped: "dict[str, list[dict]]" = defaultdict(list)
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        if finding.get("lint") != "hygiene":
            continue
        cls = finding.get("class")
        if not cls:
            continue
        grouped[cls].append(finding)
    return dict(grouped)


# ---------------------------------------------------------------------------
# Tree-cleanliness gate
#
# A finding whose cited file carries an uncommitted change is transient by
# definition: the agent that produced it fixes or discards the edit in the
# same session, and the runner's own printed output and findings JSON
# already carry it for that purpose (see module docstring). A durable bug
# in bugs/ is for a defect that exists in committed history, so a ticket
# raised from it has real work to do. This section answers exactly one
# question -- "is this finding's file unmodified relative to HEAD?" -- and
# never blocks anything: an inability to answer it (no git repository, git
# missing from PATH) is treated as "yes, clean", which reproduces this
# filer's behaviour from before this gate existed.
# ---------------------------------------------------------------------------


def _git_head_commit(repo_root: Path) -> "str | None":
    """Return the full HEAD commit hash of the git repository at
    *repo_root*, or None when it cannot be determined (not a git
    repository, detached with no commits yet, or git missing from PATH).
    file_or_cite_hygiene_bugs treats None here as the one reason to change
    filing behaviour -- see its own "Commit resolution gate" -- because
    this specific value answers "is there a commit to check anything
    against at all", not "does this commit's tree disagree with a
    finding" (the narrower question the two gates below this one answer,
    which stay fail-open on their own inability to check).
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    commit = result.stdout.strip()
    return commit or None


def _dirty_relative_paths(repo_root: Path, paths: "set[str]") -> "set[str]":
    """Return the subset of *paths* (each a POSIX-style path relative to
    *repo_root*, the same string lint_hygiene.py writes into a finding's
    "file" field) that carry an uncommitted change relative to HEAD --
    staged, unstaged, or untracked. One `git status --porcelain` call
    covers every path in *paths* at once, so a run with many findings in
    the same file costs a single subprocess call rather than one per
    finding.

    Returns an empty set -- fail open, "nothing is dirty" -- when *paths*
    is empty, *repo_root* is not a git repository, or git is not on PATH;
    see this section's own module-level docstring for why an unanswerable
    check never suppresses a filing this module would otherwise have made.
    """
    if not paths:
        return set()
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "status", "--porcelain", "--untracked-files=all", "--", *sorted(paths)],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    if result.returncode != 0:
        return set()

    dirty: "set[str]" = set()
    for line in result.stdout.splitlines():
        # Porcelain v1 format: two status characters, one separating space,
        # then the path (a rename reports "orig -> new"; take the new
        # name, the path the finding's line number is measured against).
        if len(line) < 4:
            continue
        reported = line[3:]
        if " -> " in reported:
            reported = reported.rsplit(" -> ", 1)[1]
        if len(reported) >= 2 and reported[0] == '"' and reported[-1] == '"':
            reported = reported[1:-1]
        if reported in paths:
            dirty.add(reported)
    return dirty


# ---------------------------------------------------------------------------
# Commit-content proof gate -- see the module docstring section of the
# same name for the reasoning; this is the mechanism.
# ---------------------------------------------------------------------------


def _read_file_at_commit(repo_root: Path, commit: str, rel_path: str) -> "str | None":
    """Return *rel_path*'s full text content as committed at *commit* in the
    git repository at *repo_root*, or None when it cannot be read -- *commit*
    does not resolve, *rel_path* is not present in that commit's tree,
    *repo_root* is not a git repository, or git is missing from PATH. Callers
    treat None as "cannot answer", never as "absent" -- see
    _finding_provable_at_commit for why that distinction matters here.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "show", f"{commit}:{rel_path}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _finding_provable_at_commit(
    repo_root: Path, commit: str, finding: dict, file_cache: "dict[str, str | None]"
) -> bool:
    """Return False only when *finding*'s own file resolves at *commit* but
    the cited line does not carry the token the finding names -- a real,
    resolvable commit whose tree disagrees with the finding. Returns True
    (provable) in every other case,
    including when the file, line, or token cannot be checked at all: a
    missing "line" or "token" field, a line number past the file's own
    length, or *_read_file_at_commit* returning None (commit or file
    unresolvable) -- each of those is "cannot prove it is wrong", not "proof
    it is wrong", and this gate only drops the latter (see the module
    docstring's "deliberately narrower than 'the file must exist here'").

    *file_cache* is a plain dict the caller owns across one run's worth of
    calls, keyed by *rel_path*, so a class with many findings in the same
    file costs one `git show` per distinct file rather than one per
    finding.
    """
    file_rel = finding.get("file")
    token = finding.get("token")
    if not file_rel or not token:
        return True
    try:
        line_no = int(finding.get("line"))
    except (TypeError, ValueError):
        return True
    if line_no < 1:
        return True

    if file_rel not in file_cache:
        file_cache[file_rel] = _read_file_at_commit(repo_root, commit, file_rel)
    content = file_cache[file_rel]
    if content is None:
        return True

    lines = content.splitlines()
    if line_no > len(lines):
        return True
    return token in lines[line_no - 1]


def _note_unprovable_findings(cls: str, commit: str, dropped_count: int) -> None:
    """Print one diagnostic line to stderr when the commit-content proof
    gate drops every one of a class's surviving findings (or some of a
    mixed set) -- so a run's own output says a class was skipped for this
    reason, not just silently returns nothing. Never raises, never affects
    the caller's return value or exit code -- this is narration, not
    control flow.
    """
    print(
        f"[bug_filer] class '{cls}': {dropped_count} finding(s) did not "
        f"survive the commit-content proof gate at {commit} -- their cited "
        "file:line did not carry the text they named in that commit's tree. "
        "Filing nothing for those instances (see bug_filer.py's "
        "'Commit-content proof gate' section).",
        file=sys.stderr,
    )


def _note_unresolvable_commit(root: Path) -> None:
    """Print one diagnostic line to stderr when this run cannot resolve any
    commit for *root* at all -- it is not a git repository, has no commits
    yet, or git is missing from PATH -- so the run's own output names why
    nothing was filed this run, rather than an empty result with no
    explanation (see the module docstring's "Repository root resolution"
    and "Commit resolution gate"). Never raises, never affects the
    caller's return value or exit code -- narration, not control flow,
    exactly like _note_unprovable_findings above.
    """
    print(
        f"[bug_filer] cannot resolve a commit for '{root}' -- it is not a "
        "git repository, has no commits yet, or git is not on PATH. Filing "
        "nothing this run: a bug that cannot name the commit its findings "
        "were observed at cannot be reproduced by anyone (see bug_filer.py's "
        "'Repository root resolution' section).",
        file=sys.stderr,
    )


# ---------------------------------------------------------------------------
# Bug content rendering
# ---------------------------------------------------------------------------


def _finding_line(finding: dict) -> str:
    location = f"{finding.get('file', '(unknown file)')}:{finding.get('line', '?')}"
    detail = finding.get("cause") or finding.get("label") or "hygiene finding"
    return f"- `{location}` — {detail}"


def _remedy_line(index: int, finding: dict) -> str:
    remedy = finding.get("remedy") or "see the finding's own cause above"
    return f"{index}. {remedy}"


def _slug_for_class(cls: str) -> str:
    flavor_slug = re.sub(r"[^a-z0-9]+", "-", cls.lower()).strip("-") or "class"
    return f"hygiene-lint-{flavor_slug}"


_registered_lint_classes_cache: "list[str] | None | bool" = False  # False = not yet queried


def _registered_lint_classes() -> "list[str] | None":
    """Return the class tokens team/scripts/lint_hygiene.py's own --classes
    parser accepts, read live from the script itself (the same
    importlib.util.spec_from_file_location load team/tests/unit/
    test_lint_hygiene.py uses, since the script is a standalone file rather
    than an importable package member). Returns None when the script
    cannot be found or fails to load -- _reproduction_line() then always
    renders the class-free command, which stays runnable even without an
    answer to "is this class registered".

    This is deliberately NOT a second copy of the registry: a hardcoded
    list here would drift from lint_hygiene.py's own _REGISTRY exactly the
    way this filer's Reproduction line drifted from it before -- one class
    ("hygiene-sidecar", the malformed-sidecar finding) was never a
    registered scan class in the first place, so it never had a --classes
    spelling to hardcode.

    Cached at module scope: one filing run may render several classes'
    bugs, and the script's registry cannot change mid-run.
    """
    global _registered_lint_classes_cache
    if _registered_lint_classes_cache is not False:
        return _registered_lint_classes_cache
    try:
        spec = importlib.util.spec_from_file_location("lint_hygiene", _LINT_HYGIENE_SCRIPT)
        if spec is None or spec.loader is None:
            _registered_lint_classes_cache = None
        else:
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)  # type: ignore[union-attr]
            _registered_lint_classes_cache = module.registered_class_names()
    except Exception:
        _registered_lint_classes_cache = None
    return _registered_lint_classes_cache


def _reproduction_line(cls: str) -> str:
    """Build the '## Reproduction' step for *cls*. When *cls* is one of the
    lint's registered scan classes (queried live via
    _registered_lint_classes, never assumed), the command narrows to that
    class exactly as before. Otherwise -- a class with no --classes
    spelling, such as "hygiene-sidecar" (the sidecar is loaded and
    validated on every invocation regardless of which classes --classes
    selects, so it was never registered as a selectable class) -- the
    command drops --classes entirely rather than passing a token the
    parser rejects. Either way the command still runs and its output still
    carries the finding(s) named above it."""
    registered = _registered_lint_classes()
    if registered is not None and cls in registered:
        return (
            f"1. Run `python3 team/scripts/lint_hygiene.py --classes {cls}` and "
            "observe the finding(s) named above.\n"
        )
    return (
        "1. Run `python3 team/scripts/lint_hygiene.py` and observe the "
        f"finding(s) named above -- the '{cls}' check runs on every "
        "invocation, whatever --classes selects.\n"
    )


def _render_bug_content(
    *,
    bug_id: str,
    slug: str,
    project: str,
    cls: str,
    findings: list,
    filed_at: str,
    commit: str,
) -> str:
    count = len(findings)
    finding_lines = "\n".join(_finding_line(f) for f in findings)
    remedy_lines = "\n".join(_remedy_line(i, f) for i, f in enumerate(findings, start=1))
    files_involved = "\n".join(
        f"- {path}"
        for path in sorted({f.get("file", "(unknown file)") for f in findings})
    )

    return (
        f"# {bug_id}-{slug}\n\n"
        f"**Bug ID:** {bug_id}-{slug}\n"
        f"**Filed By:** hygiene-lint (autonomous) — {count} '{cls}' hygiene "
        f"finding(s) in {project}.\n"
        f"**Date:** {filed_at}\n"
        "**Severity:** low\n\n"
        "## Status\nopen\n\n"
        "## Category\nhygiene-lint\n\n"
        "---\n\n"
        "## Symptom\n"
        f"team/scripts/lint_hygiene.py's '{cls}' class reported {count} "
        "instance(s) across the scanned tree in one run, every one of them "
        f"committed at the observed commit below. See Findings below "
        "for every instance's file and line.\n\n"
        "## Expected\n"
        f"No unsuppressed '{cls}' hygiene findings.\n\n"
        "## Actual\n"
        f"{finding_lines}\n\n"
        "## Reproduction\n"
        f"{_reproduction_line(cls)}\n"
        "## Files Involved\n"
        f"{files_involved}\n\n"
        "## Hypothesis (optional)\n"
        "Speculative -- see each finding's own remedy line below for the "
        "runnable fix (reword the line, or suppress it deliberately with a "
        "justification via --add-suppression).\n\n"
        "---\n\n"
        f"{_HYGIENE_CLASS_SECTION}\n"
        f"{cls}\n\n"
        "## Observed At Commit\n"
        f"{commit}\n\n"
        "## Findings\n"
        f"{finding_lines}\n\n"
        "## Remedies\n"
        "Run each command below from the repository root of a checkout that carries "
        "the finding it names -- the paths inside each command are repo-relative, not "
        "tied to the tree this bug was filed from.\n\n"
        f"{remedy_lines}\n"
    )


# ---------------------------------------------------------------------------
# Public result type and entry point
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FilingResult:
    """Outcome of one hygiene class's filing decision within a
    file_or_cite_hygiene_bugs() call.

    cls       -- the hygiene class this result covers.
    outcome   -- "filed" (a new bug file was written) or "duplicate" (an
                 existing open/running bug already covers this class;
                 nothing was written).
    bug_id    -- "BUG-NNNN" for either outcome.
    bug_path  -- absolute path to the filed file ("filed" only); None for
                 "duplicate" (no file was written).
    count     -- number of findings of this class in this run.
    """

    cls: str
    outcome: str
    bug_id: str
    bug_path: "str | None"
    count: int


def file_or_cite_hygiene_bugs(
    bugs_dir: str,
    *,
    project: str,
    findings_path: str,
    filed_at: "str | None" = None,
    repo_root: "str | Path | None" = None,
) -> list:
    """File at most one aggregated bug per hygiene class present in the
    findings file at *findings_path*, or cite an existing open/running bug
    that already covers a class instead of filing a duplicate.

    Before grouping into a filing decision, every finding whose "file" sits
    in an uncommitted edit (staged, unstaged, or untracked -- see
    _dirty_relative_paths) relative to *repo_root* HEAD is dropped: a
    finding that exists only in a working-copy edit is transient by
    definition and must not become a durable bug (see the tree-cleanliness
    gate section above). Every finding that survives that filter is then
    checked against the commit this run is about to stamp: a finding whose
    file resolves at that commit but whose own cited line does not carry
    the token it names is also dropped (see the module docstring's
    "Commit-content proof gate" -- the run's own stderr narrates when this
    happens). A class left with zero surviving findings after
    either filter is not filed and not cited -- the same as if it had never
    appeared in the findings file at all. *repo_root* is resolved via
    _resolve_repo_root -- an explicit value here takes precedence (tests do
    this to exercise a throwaway git repository), then PGAI_DEV_TREE_PATH,
    then this module's own checkout guess (see the module docstring's
    "Repository root resolution").

    Before any per-class decision, this run's own commit is resolved once
    against that root. When it cannot be resolved at all -- the root is not
    a git repository, has no commits yet, or git is missing from PATH --
    this function files nothing for the entire run and says why on stderr
    (see the module docstring's "Commit resolution gate" and
    _note_unresolvable_commit): a bug that cannot name the commit its
    findings were observed at cannot be reproduced by anyone, so it is
    never worth stamping "## Observed At Commit" with the literal
    "unknown".

    A bug that IS filed names the commit it was observed at (git rev-parse
    HEAD against the resolved root) in its own "## Observed At Commit"
    section.

    Creates *bugs_dir* if it does not yet exist (a fresh project's first
    hygiene filing has no bugs/ directory yet). Never touches anything
    outside *bugs_dir* -- no ledger write, no release-state.md, no task or
    queue file, no HALT-family signal file (see module docstring).

    Returns an empty list when the findings file carries no hygiene-lint
    findings (including when it is missing or malformed) -- zero classes
    means zero bugs, not an empty report (one bug per class is a ceiling,
    not a target). Also returns an empty list, with the reason
    printed to stderr, when no commit resolves for the run.
    """
    grouped = _load_hygiene_findings(findings_path)
    if not grouped:
        return []

    root = _resolve_repo_root(repo_root)
    commit = _git_head_commit(root)
    if commit is None:
        _note_unresolvable_commit(root)
        return []

    all_files = {
        f.get("file") for findings in grouped.values() for f in findings if f.get("file")
    }
    dirty_files = _dirty_relative_paths(root, all_files)

    os.makedirs(bugs_dir, exist_ok=True)
    claim_next_bug_id, release_bug_id_claim = import_bug_scanner()
    filed_at = filed_at or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    proof_cache: "dict[str, str | None]" = {}
    results: list = []
    for cls in sorted(grouped):
        findings = [f for f in grouped[cls] if f.get("file") not in dirty_files]
        if not findings:
            # Every instance of this class lives in an uncommitted edit --
            # nothing survives the tree-cleanliness gate, so this class is
            # skipped exactly as if it had no findings this run.
            continue

        provable = [
            f for f in findings if _finding_provable_at_commit(root, commit, f, proof_cache)
        ]
        if len(provable) != len(findings):
            _note_unprovable_findings(cls, commit, len(findings) - len(provable))
        findings = provable
        if not findings:
            # Every surviving finding failed the commit-content proof gate --
            # this class's own bug would have named a commit that disagrees
            # with what it lists; file nothing for it this run.
            continue

        existing = find_existing_bug_for_class(bugs_dir, cls)
        if existing:
            results.append(
                FilingResult(cls=cls, outcome="duplicate", bug_id=existing, bug_path=None, count=len(findings))
            )
            continue

        slug = _slug_for_class(cls)
        bug_id, bug_path, lock_path = claim_next_bug_id(
            bugs_dir, slug, claimed_by="hygiene-lint-aggregate-filer"
        )
        content = _render_bug_content(
            bug_id=bug_id,
            slug=slug,
            project=project,
            cls=cls,
            findings=findings,
            filed_at=filed_at,
            commit=commit,
        )
        try:
            Path(bug_path).write_text(content, encoding="utf-8")
        finally:
            release_bug_id_claim(lock_path)

        results.append(
            FilingResult(cls=cls, outcome="filed", bug_id=bug_id, bug_path=str(bug_path), count=len(findings))
        )

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


_CLI_HELP = """\
usage: bug_filer.py <bugs_dir> <project> <findings_file>

File, or cite, one aggregated hygiene bug per lint class a gated run's
findings file holds.

Positional arguments (all three required, in this order):
  bugs_dir       the project's bugs/ directory
  project        the project name the run checked
  findings_file  the gated runner's quality findings file (JSON lines)

Prints one block of key=value lines per class the run covered: class,
outcome, bug_id, bug_path, count, then a blank line.

Options:
  -h, --help     print this help and exit 0

Exit codes: 0 done; 2 wrong argument list.
"""


def main(argv: "list | None" = None) -> int:
    """CLI: bug_filer.py <bugs_dir> <project> <findings_file>

    Prints one block of "key=value" lines per hygiene class this run
    covered (see module docstring's "Usage (CLI)"). Used by
    team/scripts/lib/lint_bug_filer.sh, which cannot import Python
    directly from bash.
    """
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in ("-h", "--help"):
        print(_CLI_HELP, end="")
        return 0
    if len(argv) != 3:
        print(
            "usage: bug_filer.py <bugs_dir> <project> <findings_file>",
            file=sys.stderr,
        )
        return 2

    bugs_dir, project, findings_path = argv
    results = file_or_cite_hygiene_bugs(bugs_dir, project=project, findings_path=findings_path)

    for result in results:
        print(f"class={result.cls}")
        print(f"outcome={result.outcome}")
        print(f"bug_id={result.bug_id}")
        print(f"bug_path={result.bug_path or ''}")
        print(f"count={result.count}")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
