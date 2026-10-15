#!/usr/bin/env python3
"""
lint_doc_command_truth.py
==========================
Doc-truth lint, existence half: every command a document cites must exist in
this tree, and must answer ``--help`` without erroring.

A document that names a command which no longer exists is worse than a
document that says nothing: it costs the reader the time to discover the
lie, and it teaches them to distrust the rest of the page. The existing
fixture (``team/scripts/harness/doc-lint.sh``, ``cmd`` suite) executes
fenced commands verbatim, but a citation only reaches it when it sits inside
a fenced block on the standing five-file audited surface and survives its
skip heuristics — a verb named in running prose, or a script mentioned on a
page outside that surface, is unchecked. This lint asks a narrower,
answerable question for a much wider citation surface: does the thing this
document names exist in this tree, and does it answer ``--help`` cleanly.

CENSUS CLASS: CONVERT (team/SOP-core.md, "Gates and Refusals"). A false or
stale doc citation carries zero runtime-failure risk — nothing crashes, no
data is corrupted, the loop is not moved backwards. It is a documentation-
accuracy finding, not a build break, so it warns, reports, and continues
rather than blocking a calling runner.

Scan set (the boundary this lint checks, and why)
---------------------------------------------------
The default scan set has two sources, unioned.

The first is the 14 documents named in ``team/config/docs-manifest.txt`` —
"the docs an install actually receives" (see that file's own header) —
minus three that are excluded by name (``_SCAN_SET_EXCLUSIONS`` below):
``blocking-site-census.md``, ``read-order-manifests.md``, and
``inherited-environment-exposure-survey.md``. This is deliberately narrower
than "the whole of docs/": the manifest boundary already gets to the pages
an operator or contributor actually reads for command guidance, including
``docs/operator-commands.md`` — a page whose commands nothing else checks
against the verb set — but the three excluded documents are, by their own
stated purpose, inventory snapshots (a gate census, a read-order map, a
security survey) that cite internal implementation file paths —
library helpers under ``team/scripts/lib/``, refusal-site source files — for
record-keeping, never as something a reader is meant to invoke. Checking
those citations for existence-and-``--help`` is a different, much fuzzier
problem (whole-tree architecture-reference accuracy) than the doc-truth-for-
commands problem this lint targets, and scanning them produces citation
noise with no command-truth signal — every one of those three documents'
script mentions is a description of "where this code lives," not "run this." ``docs-manifest.txt``'s own header
already requires whoever adds a new ``docs/`` citation to a role file to add
its path to that manifest in the same change, so this half of the scan set
grows with no second registration step (short of the three standing
exclusions).

The second source is ``team/config/doc-truth-extra-scan-docs.txt``, read via
``--extra-scan-manifest`` (default that path). It names the documents a
stranger reads first — ``README.md``, ``HOW_TO.md`` and ``ARCHITECTURE.md``
at the repository root, plus ``docs/docker.md`` — and it is a genuinely
separate file rather than an addition to ``docs-manifest.txt``, on purpose:
``docs-manifest.txt``'s own header states its one job as "what
install.sh and upgrade.sh copy into an installed root," and none of these
four documents clear that bar. The three root documents live above ``docs/``
entirely, so they were never candidates for that manifest's own closure rule
to begin with; ``docs/docker.md`` IS inside ``docs/`` but is
``docs-manifest.txt``'s own recorded, deliberate exclusion (see that file's
"Recorded exclusions" note) because every step it documents happens before
an install exists. Folding this second list into ``docs-manifest.txt`` would
either break its documented closure rule (root documents are not under
``docs/``) or contradict its own recorded exclusion (``docker.md``) — so
this lint keeps its own, separate scan-set source instead of bending that
file's stated purpose. Together the two sources scan 15 documents by
default: the eleven manifest survivors plus these four.

Override the first source with ``--docs-manifest``/``--docs-root``, the
second with ``--extra-scan-manifest``, or bypass BOTH sources at once with
``--doc`` (one or more explicit paths, which bypasses the manifest, the
exclusion list, and the extra-scan-manifest entirely) — including in tests.

Citation extraction
--------------------
Fenced blocks are recognised the CommonMark way: a line of three or more
backticks or tildes opens a fenced block whatever info string follows it
(```` ```text ````, ```` ```ini ````, ```` ~~~python ````, a bare fence),
and the next line made only of the same character, at least as long,
closes it. Every fenced block is paired with its own closer this way, so
the prose between two fences is always scanned for inline spans. Only a
block whose info string's first word is ``bash`` or ``sh`` is scanned for
commands; every other block's lines (bare, ``text``, ``ini``, ``python``,
...) are skipped.

Two sources, over every scanned document:

1. **Fenced bash/sh blocks** — the same fence and skip vocabulary
   ``team/scripts/harness/doc-lint.sh`` already defines (block-level
   ``<!-- doc-lint: skip -->``, block-level ``<!-- doc-lint: docker -->``,
   inline ``# doc-lint: skip``, and the ``_is_placeholder_cmd`` heuristic —
   angle-bracket tokens, ``.../`` paths, a bare ``${VAR}`` command, an
   ``$EDITOR`` invocation, a ``/path/to/`` fragment). A citation the
   existing harness legitimately treats as non-executable is a placeholder
   for this lint too — this module does not invent a second vocabulary.
   Within a non-skipped command, the first token (after stripping a leading
   ``bash``/``sh``/``python3``/``python`` interpreter word) is the
   candidate citation.
2. **Inline code spans** — a single-backtick ```` `...` ```` span outside
   any fenced block, on a scanned document's prose lines. The span's first
   token is the candidate citation, subject to the same placeholder check.
   This is how a bare script basename or an operator verb named in running
   text (for example, "```` `wake-now.sh` ```` fires...", "the
   ```` `halt` ```` verb...") reaches the lint without being fenced.

A candidate is classified as one of:

  - **script** — ends in ``.sh`` or ``.py`` (a path or a bare basename).
  - **verb** — exactly matches an ``active`` verb name in
    ``team/config/operator_verbs.manifest``, read through the one Python
    reader of that file (``team/pgai_agent_kanban/lib/
    operator_verb_manifest.py``) — this module never hand-parses the
    manifest a second time.

Anything else (shell builtins, generic tools like ``git``/``curl``/``cat``,
words that are neither a script-shaped path nor a declared verb) is not a
citation this lint tracks — per the brief, this lint resists being clever
about argument semantics, so it only ever asks "does the named thing exist
and answer --help," never "is this invocation well-formed." Two narrow
exclusions keep the classifier from tripping on shapes that were never
citations to begin with: a candidate containing ``*`` (a glob pattern in
running prose, e.g. ``test_*.py``) names no single file, and a bare
basename starting with ``test_`` (a pytest test-module reference, the
universal way this codebase's docs point a reader at test coverage) is not
an operator- or contributor-invocable command.

Two checks, per unique resolved target
-----------------------------------------
1. **Existence.** The candidate must resolve to a real file in this tree.
   A leading live-install-root variable (``$KANBAN_ROOT/``,
   ``${KANBAN_ROOT}/``, ``$PGAI_AGENT_KANBAN_ROOT_PATH/`` or its braced
   form) is stripped first — those variables name the same directory
   ``team/`` occupies in a dev-tree checkout (every governance file in this
   tree resolves them that way), so a citation written from an installed
   root's perspective resolves the same as one written from the checkout's.
   What remains is tried, in order: the literal path under the repo root,
   the literal path under ``team/``, and the literal path under
   ``team/scripts/`` (documents written from an installed root's
   perspective cite ``scripts/foo.sh``, or a stage script under
   ``cm/``/``dashboard/``/``lib/``, all of which live under
   ``team/scripts/`` in a dev-tree checkout). A bare basename with no path
   separator additionally resolves by an exact-name search anywhere under
   ``team/scripts/`` (recursive) — this is a real-file existence check
   only, never a fuzzy or prefix match, so a citation naming a renamed or
   never-existing script (``cm-release.sh`` when the real file is
   ``cm/release.sh``) still reports missing. A verb citation resolves
   through the manifest's own ``script`` column instead of any of the
   above.
2. **--help parseability.** For every citation that DID resolve, this lint
   invokes ``<resolved> --help`` exactly once per unique resolved path
   (never once per citation — many citations share one script) and records
   whether it exited 0 within a bounded timeout. This is the ONLY
   invocation this lint ever makes; it never executes a cited command
   with its documented arguments, because several of the 27 verbs mutate
   kanban state and one touches origin, and running them from a lint is a
   destructive-failure path a lint must never take.
   Every ``--help`` invocation runs with ``PGAI_AGENT_KANBAN_ROOT_PATH`` /
   ``KANBAN_ROOT`` redirected at a fresh, empty sandbox directory
   (``_make_help_sandbox_root``) rather than whatever root the calling
   shell has configured, AND with that same directory passed as the
   subprocess's explicit ``cwd`` — never the calling process's own working
   directory, whatever it happens to be at the time: a probe that inherits
   its caller's cwd makes its verdict a property of what ran earlier in
   the same process rather than of the tree it checks. This
   lint's own development run against the real tree found a script that
   does not check ``--help`` before resolving its kanban root and starting
   real work — a well-behaved script's ``--help`` branch never touches
   that variable, so the redirect is invisible to it; a script that is not
   well-behaved is contained to the empty sandbox instead of a live
   install. The timeout bounding each invocation is
   ``DEFAULT_HELP_TIMEOUT_SECONDS`` (overridable via ``--help-timeout``) —
   stated once, at module scope, rather than as a bare literal repeated
   wherever a default is needed.
   Each probe starts in its own session (its own process group), and that
   whole group is killed once the probe has returned or timed out
   (``_run_probe``), so nothing it started outlives the lint: a script that
   falls through ``--help`` into a follow-mode pipeline such as
   ``tail -F <log> | awk ...`` loses both halves of the pipeline, not only
   the shell that started them. The sandbox directory itself is removed when
   the run ends.

Process-internal citations (HELP FAILS exempt list)
------------------------------------------------------
Not every cited path is an operator command line. A sourced prelude
(``team/scripts/lib/wake_bootstrap.sh``) or a container entrypoint
(``docker/entrypoint.sh``) is cited so a reader can find the file, never so
a reader can run it — bolting a decorative ``--help`` onto either is the
wrong fix. ``team/config/doc-command-truth-help-exempt.txt``
(overridable via ``--help-exempt-list``) names, one repo-root-relative path
per line with a mandatory comment stating why, every citation this lint
accepts as process-internal. The file is read through
``_load_help_exempt_list`` in exactly the comment/blank-line shape
``team/scripts/help_presence_exempt.txt`` already uses for the same
distinction on the help-presence lint.

The exemption is narrow by construction: it only ever suppresses a HELP
FAILS finding for a citation whose candidate **resolved** to a real file on
the list. It never touches the existence check above — a listed path that
stops existing in the tree still resolves to nothing and still produces a
MISSING COMMAND finding, because the exempt-list lookup runs strictly after
resolution succeeds. A missing exempt list degrades to "nothing exempted"
with a stderr warning (the same discipline this module already applies to
a missing operator-verbs-manifest or Key Registry) rather than aborting the
lint.

Config-key parity (second reconciliation)
------------------------------------------
A document can also lie about *configuration*: naming a key the wake chain
does not accept, misstating an accepted key's default, or going on
describing a key the loader dropped. The accepted-key source is TWO
registries, not one, because ``kanban.cfg`` and ``project.cfg`` are
different files with different key sets:

  - ``team/scripts/lib/config_loader.sh`` carries a "Key Registry" comment
    block that is the single source of truth for every ``kanban.cfg`` key
    the wake chain accepts — not only the keys ``load_config()`` itself
    reads, but every ``kanban.cfg`` key any file reads via ``read_ini`` or
    ``config_get`` (dashboard/lib/snapshot.sh, docker_engine.sh,
    provider_claude.sh, lint_governance_byte_budgets.sh, cleanup.sh,
    the API config loader, and more) — see that block's own header note.
  - ``team/scripts/lib/project_paths.sh`` carries its own "Key Registry"
    comment block for ``project.cfg``'s distinct key set (``[project]``,
    ``[versioning]``, ``[hooks]``, ``[debug]``, ``[training]``) — the file
    is the single source of truth for every ``pp_*`` accessor's project.cfg
    read. ``[release]`` is the one section genuinely shared by both files
    (a project.cfg override of a kanban.cfg-wide default), so it is
    declared once, in config_loader.sh's registry, per that registry's own
    header note.

Both blocks share one shape — one ``# [section]`` header per INI section
followed by one ``#   key   STATUS   default`` line per key (the three
``[models.<provider>]`` sections in config_loader.sh's registry instead
list their shared key names against one section-level default, since all
three providers share the same per-role vocabulary; a key-group line that
names ``shipped value "X"`` records X for that group instead, the way the
template's ``Shipped value:`` convention states a value it ships) — and one
parser,
``_parse_config_loader_registry`` below, which is a structural reader of
the block, never a hand-copied key list, so this lint drifts with either
registry rather than beside it. The two parsed maps are merged into one
lookup table before reconciliation; they do not collide by section name.
If a future edit ever makes either block genuinely unparseable (not "this
parser needs a fix" but "the format stopped being regular"), the fix
belongs in that registry's own shape, called out in the task that hits it,
not a hardcoded key list here.

A registry default line may spell the literal token ``N/A`` instead of a
value. That marks a key with genuinely no single canonical default to
check documents against — a per-installation identity or example field
(project.cfg's ``project_name``, ``dev_tree_path``, ``git_repo_url``,
``branch_prefix``) where every real project.cfg legitimately carries a
different value and the ``*_example`` template's active line is
illustrative, not a claim about what the loader falls back to when the key
is absent. An ``N/A`` key still participates fully in the unknown-key and
undocumented-key directions below; only the default-value comparison is
skipped for it (``RegistryKey.default_checked``) — skipping it entirely
would trade the UNKNOWN CONFIG KEY false positive this lint exists to
remove for a CONFIG DEFAULT MISMATCH false positive on the same line.

The reconciliation runs in both directions:

  - **A document names a key/section pair the registry has no entry for**
    — invented, renamed, or removed. Higher-severity: a reader who trusts
    the document acts on a key that does nothing.
  - **A document names a real key but states the wrong default** — a
    quieter but still real inaccuracy, skipped for a key whose registry
    entry is ``N/A`` (see above).
  - **The registry declares a key no scanned document mentions** — an
    omission, not a lie. This direction is deliberately never suppressed:
    it is the worklist the docs-rewrite tickets consume.

Scan set: the same manifest-derived document set as the command-existence
check above, plus ``kanban.cfg_example`` and ``project.cfg_example`` at the
repo root — the two templates whose own inline ``key = value`` lines are
themselves a doc claim about the accepted key set. A key citation is only
recognized under a *recognized* ``[section]`` context (a bracketed header
appearing at the start of a line, tolerating a leading ``#``/``*``/backtick
decoration so both raw INI headers and a markdown bold-line section
reference like `` **`[dashboard]` — layout knobs:** `` are read the same
way); markdown heading boundaries (``#`` .. ``######``) reset that context
so an unrelated table two sections later is never attributed to a stale
section. Within a recognized section, a citation is either an *active*
(uncommented) ``key = value`` line inside a raw template or a fenced
` ```ini ` block, or a markdown reference-table row whose first column is
a backtick-quoted key name. A key mentioned only in a commented-out
illustration (` # coder = claude-sonnet-4-6 `) is not a citation — it is
an example, not a claim about the accepted default. In a raw template that
commented line still documents the key's existence: a registered key
whose only mention is ``# key = value`` there is not reported as
undocumented, and nothing else is checked on that line. In a raw template
only a real, uncommented ``[section]`` line sets the section; a comment
that opens with a bracket is prose about a key, not a header.

Dashboard window/pane parity (third reconciliation)
------------------------------------------------------
The third claim class: dashboard geometry. ``team/config/
dashboard_windows.census`` is the machine-readable single source of truth
for the tmux dashboard's window/pane layout, and ``generate_window_docs.py
--check`` already keeps ONE derived surface — ``docs/DASHBOARD-PANES.md`` —
honest against it. Every OTHER window/pane claim anywhere else in the doc
set (a "Window N: Name" tour heading, a "Name (Window N)" cross-reference,
a hand-maintained Pane/Script table) is unchecked prose today. This lint
does not re-implement or weaken ``generate_window_docs.py --check`` — it
never scans ``docs/DASHBOARD-PANES.md`` (that document is not in the
command-existence check's manifest-derived scan set to begin with) and it
defers entirely to that script for the ONE surface it already generates.

Citations are read through ``team/pgai_agent_kanban/dashboard/
window_census.py``'s ``load_census()`` — the one Python reader for the
census file — never a second hand-parse. A "window" citation is a `` Window
N: Name ``, `` Window N (name) ``, `` Name (Window N) ``, or a bare `` window
N `` mention; a "pane" citation is a markdown ``| Pane | ... Script ... |``
table located shortly after a window citation, one row per claimed
pane. Reconciliation, again bidirectional:

  - **A document names a window index the census has no row for**, or
    names a real index under the wrong window name.
  - **A document's pane table claims a script the census does not list**
    for that window.
  - **A census window no scanned document describes at all** — coverage
    gap, not a lie, reported the same non-suppressed way as the config
    registry's undocumented-key direction above.

Undocumented installed scripts (fourth check, the reverse direction)
----------------------------------------------------------------------
The three checks above ask whether what a document names is true. This one
asks the reverse: is every script the install ships named somewhere an
operator looks. ``install.sh`` copies all of ``team/scripts/`` into
``$KANBAN_ROOT/scripts/``, so a script that no operator doc mentions is a
tool nobody can find, or an internal check nobody can tell apart from one.

Every ``*.sh`` / ``*.py`` file at the top level of ``team/scripts/`` and in
its ``cm/``, ``cleanup/``, ``migrate/`` and ``migrations/`` folders
(``__init__.py`` excepted) must be named, by its file name as a whole token
(bare, in a path, in a code span or in plain prose), either anywhere in one
of the three operator docs -- ``HOW_TO.md``, ``docs/OPERATIONS.md``,
``docs/operator-commands.md`` -- or in an "Internal checks" section (a
heading of that title, up to the next heading of the same or a higher
level) in ``docs/operator-commands.md`` or ``team/TESTING.md``. Each script
in neither place is reported by name as UNDOCUMENTED SCRIPT, with the line
to open and add it at. Override the doc lists with ``--operator-doc`` /
``--internal-checks-doc`` and the scripts tree with ``--scripts-dir``. A
tree that has none of these documents (a fixture tree built for the other
checks) is not checked, with a stderr note.

Exit codes
----------
  0   No findings from any of the four checks.
  1   One or more findings (missing/broken command citation, config-key
      drift, dashboard window/pane drift, or an undocumented installed
      script). A calling runner should
      treat this as a CONVERT-class quality finding — see
      ``team/scripts/lib/gated_runner_lint.sh``,
      ``gated_runner_run_quality_lint`` — not a hard abort.
  2   Usage error or internal failure (docs-manifest missing, no citations
      scanned, etc). A config_loader.sh or census file that cannot be
      found/parsed does not raise this — it degrades the matching check to
      0 registry keys / 0 census windows with a stderr warning, the same
      discipline the existence check already applies to a missing
      operator-verbs-manifest, so a tree without one of these files still
      gets a clean run of the other checks.

Usage
-----
  python3 team/scripts/lint_doc_command_truth.py [--repo-root PATH]
      [--docs-manifest PATH] [--docs-root PATH] [--doc PATH ...]
      [--operator-verbs-manifest PATH] [--config-loader PATH]
      [--project-key-registry PATH]
      [--census PATH] [--key-doc PATH ...] [--pane-doc PATH ...]
      [--operator-doc PATH ...] [--internal-checks-doc PATH ...]
      [--scripts-dir PATH]
      [--help-timeout SECONDS] [--help-exempt-list PATH] [--verbose]
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# The pgai_agent_kanban package sits beside scripts/ in both the dev tree
# (team/) and the live install (the install root), so it is located from
# this file's own path -- one level above scripts/ -- and never from the
# environment.
_OWN_ROOT = str(Path(__file__).resolve().parent.parent)
if _OWN_ROOT not in sys.path:
    sys.path.insert(0, _OWN_ROOT)

from pgai_agent_kanban.lib.markdown_fence import (  # noqa: E402
    SHELL_FENCE_LANGS,
    find_fence_close,
    match_fence_opener,
)

# ---------------------------------------------------------------------------
# --help probe timeout — the single place this value is stated.
# The argparse default below and this module's docstring both point at this
# name rather than repeating the literal, so there is one number to change
# rather than several that can drift apart. 30s rather than a tighter bound
# because the probe runs a real subprocess per cited script inside a full
# suite pass sharing CPU with hundreds of other tests; a bound comfortable
# on an idle machine reads as a false HELP FAILS under that load.
# ---------------------------------------------------------------------------

DEFAULT_HELP_TIMEOUT_SECONDS = 30.0

# ---------------------------------------------------------------------------
# Skip vocabulary — ported from team/scripts/harness/doc-lint.sh so both
# lints agree on what counts as a placeholder / explicitly non-checkable
# citation. Kept as a literal port (not a shared import) because doc-lint.sh
# is bash and this module is Python; the two implementations are read
# side-by-side in code review to keep them from drifting.
# ---------------------------------------------------------------------------

_BLOCK_SKIP_RE = re.compile(r"<!--\s*doc-lint:\s*skip", re.IGNORECASE)
_DOCKER_GATE_RE = re.compile(r"<!--\s*doc-lint:\s*docker\s*-->", re.IGNORECASE)
_INLINE_SKIP_RE = re.compile(r"#\s*doc-lint:\s*skip")

# Fence recognition follows CommonMark rather than the skip vocabulary above:
# ANY run of three or more backticks or tildes opens a fenced block, whatever
# info string follows it (```text, ```ini, ~~~python, ```bash title=x), and
# the block closes at the next line made only of the same character, at least
# as long. Recognising only bare and bash/sh openers would read a tagged
# opener as prose and take its closer for an opener, hiding every prose line
# up to the next bare fence from the inline-span scan. Which blocks are
# SCANNED for commands is a separate question, answered by the info string's
# first word (see _SCANNED_FENCE_LANGS). The recognition rule itself lives in
# pgai_agent_kanban/lib/markdown_fence.py, shared with
# team/scripts/harness/lib/doc_step_extract.py so the two readers cannot
# drift apart.
_SCANNED_FENCE_LANGS = SHELL_FENCE_LANGS
_match_fence_opener = match_fence_opener

_ANGLE_BRACKET_RE = re.compile(r"<[^\s>]+>")
_ELLIPSIS_PATH_RE = re.compile(r"(\.\.\./)")
_BARE_VAR_RE = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*(:-[^}]*)?\}$")

_INTERPRETERS = frozenset({"bash", "sh", "python3", "python"})
_SCRIPT_SUFFIXES = (".sh", ".py")


def _is_placeholder_citation(candidate: str) -> bool:
    """True when *candidate* is a doc-lint-recognized placeholder shape.

    Applied to the extracted candidate token only (not the whole command
    line) — an example command's argument being a placeholder (``--project
    <name>``) does not make the script name at argv[0] unresolvable, so this
    lint checks the candidate in isolation rather than reusing doc-lint's
    whole-line heuristic verbatim.
    """
    if _ANGLE_BRACKET_RE.search(candidate):
        return True
    if _ELLIPSIS_PATH_RE.search(candidate) or candidate == "...":
        return True
    if _BARE_VAR_RE.match(candidate):
        return True
    if candidate == "$EDITOR" or candidate.startswith("${EDITOR"):
        return True
    if "/path/to/" in candidate:
        return True
    return False


# ---------------------------------------------------------------------------
# Citation record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Citation:
    """One script or verb a document cites: where it appears, the raw text, and whether it is a fenced or inline citation."""
    doc_display: str  # document path as shown in findings (relative when possible)
    doc_path: Path  # absolute path to the document, for the grep remedy line
    line: int  # 1-based line number of the citation's first physical line
    candidate: str  # the raw citation text (script path/basename or verb name)
    kind: str  # "script" | "verb"
    source: str  # "fenced" | "inline"


def _extract_citations(doc_path: Path, doc_display: str) -> list[Citation]:
    """Return every script/verb citation found in *doc_path*.

    Single pass, tracking fence state so the inline-code-span scan never
    re-examines a fenced block's own lines.
    """
    try:
        lines = doc_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        print(f"lint_doc_command_truth: WARNING: cannot read {doc_path}: {exc}", file=sys.stderr)
        return []

    citations: list[Citation] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]

        # --- Detect a block-level annotation on the line before a fence ---
        block_skip = bool(_BLOCK_SKIP_RE.match(line.strip()))
        docker_gate = bool(_DOCKER_GATE_RE.match(line.strip()))

        fence_line_idx = None
        fence_open = None
        if block_skip or docker_gate:
            j = i + 1
            while j < n and lines[j].strip() == "":
                j += 1
            if j < n:
                fence_open = _match_fence_opener(lines[j].strip())
                if fence_open is not None:
                    fence_line_idx = j
        else:
            fence_open = _match_fence_opener(line.strip())
            if fence_open is not None:
                fence_line_idx = i

        if fence_open is not None:
            # A fenced block of any info string is paired with its closer
            # here, so the inline-span scan resumes on the line after it.
            # Its body is skipped wholesale for citation purposes when
            # block-skipped or docker-gated (matching doc-lint.sh's own
            # skip semantics), or when its info string is anything but
            # bash/sh; bash/sh blocks are scanned for commands.
            fence_token, lang = fence_open

            body_start = fence_line_idx + 1
            close_idx = find_fence_close(lines, body_start, fence_token)  # n if unterminated

            scan_block = (not block_skip) and (not docker_gate) and lang in _SCANNED_FENCE_LANGS
            if scan_block:
                citations.extend(
                    _scan_fenced_block(lines, body_start, close_idx, doc_path, doc_display)
                )

            i = close_idx + 1
            continue

        # --- Not a fence line: scan for inline single-backtick spans ---
        citations.extend(_scan_inline_line(line, i + 1, doc_path, doc_display))
        i += 1

    return citations


def _join_continuations(raw_lines: list[str], start_line_no: int) -> list[tuple[str, int]]:
    """Join backslash-continuation lines; return (logical_command, first_line_no)."""
    joined: list[tuple[str, int]] = []
    buf = ""
    buf_start = start_line_no
    for offset, raw in enumerate(raw_lines):
        line_no = start_line_no + offset
        if buf == "":
            buf_start = line_no
        if raw.endswith("\\"):
            buf += raw[:-1] + " "
        else:
            buf += raw
            stripped = buf.strip()
            if stripped:
                joined.append((stripped, buf_start))
            buf = ""
    if buf.strip():
        joined.append((buf.strip(), buf_start))
    return joined


def _scan_fenced_block(
    lines: list[str], body_start: int, close_idx: int, doc_path: Path, doc_display: str
) -> list[Citation]:
    raw_lines = lines[body_start:close_idx]
    out: list[Citation] = []
    for cmd_text, line_no in _join_continuations(raw_lines, body_start + 1):
        stripped = cmd_text.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if _INLINE_SKIP_RE.search(stripped):
            continue  # explicit doc-lint: skip — never a citation
        candidate, kind = _classify_command(stripped)
        if candidate is None:
            continue
        if _is_placeholder_citation(candidate):
            continue
        out.append(
            Citation(
                doc_display=doc_display,
                doc_path=doc_path,
                line=line_no,
                candidate=candidate,
                kind=kind,
                source="fenced",
            )
        )
    return out


_INLINE_CODE_SPAN_RE = re.compile(r"`([^`\n]+)`")


def _scan_inline_line(line: str, line_no: int, doc_path: Path, doc_display: str) -> list[Citation]:
    out: list[Citation] = []
    for span in _INLINE_CODE_SPAN_RE.findall(line):
        span = span.strip()
        if not span:
            continue
        candidate, kind = _classify_command(span)
        if candidate is None:
            continue
        if _is_placeholder_citation(candidate):
            continue
        out.append(
            Citation(
                doc_display=doc_display,
                doc_path=doc_path,
                line=line_no,
                candidate=candidate,
                kind=kind,
                source="inline",
            )
        )
    return out


def _classify_command(text: str) -> tuple[Optional[str], Optional[str]]:
    """Return (candidate, kind) for *text*, or (None, None) if it names
    nothing this lint tracks."""
    try:
        tokens = shlex.split(text)
    except ValueError:
        tokens = text.split()
    if not tokens:
        return None, None

    first = tokens[0]
    if first in _INTERPRETERS and len(tokens) > 1:
        candidate = tokens[1]
    else:
        candidate = first

    if candidate.startswith("-"):
        return None, None

    if "*" in candidate:
        return None, None  # a glob pattern in prose names no single file

    if candidate.endswith(_SCRIPT_SUFFIXES):
        basename = candidate.rsplit("/", 1)[-1]
        if basename.startswith("test_"):
            return None, None  # a pytest test-module reference, not a command
        return candidate, "script"

    # Bare word with no path separator and no extension: only a citation
    # this lint tracks when it is exactly a declared verb name — checked by
    # the caller against the loaded manifest, so at classification time we
    # provisionally mark it "verb" and let resolution decide it does not
    # exist as a verb (never re-classified as "script").
    if "/" not in candidate and re.match(r"^[A-Za-z][A-Za-z0-9_-]*$", candidate):
        return candidate, "verb"

    return None, None


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Resolution:
    """Where a cited script resolved to (None when it did not), and every path or verb tried."""
    resolved_path: Optional[Path]  # None when the candidate does not resolve
    tried: tuple[str, ...]  # human-readable list of paths/verbs tried


# Live-install-root variable prefixes that name the same directory as
# team/ in a dev-tree checkout (see team/SOP-core.md "Shared Team Root" and
# every governance file's own resolution of these variables).
_ROOT_VAR_PREFIXES = (
    "${KANBAN_ROOT}/",
    "$KANBAN_ROOT/",
    "${PGAI_AGENT_KANBAN_ROOT_PATH}/",
    "$PGAI_AGENT_KANBAN_ROOT_PATH/",
)


def _strip_root_var_prefix(candidate: str) -> str:
    for prefix in _ROOT_VAR_PREFIXES:
        if candidate.startswith(prefix):
            return candidate[len(prefix) :]
    return candidate


def _find_under_scripts_by_basename(basename: str, scripts_dir: Path) -> Optional[Path]:
    """Exact-basename search anywhere under *scripts_dir* (recursive).

    A real-file existence check, not a fuzzy match: only an exact filename
    equality counts. Deterministic on multiple matches (sorted, first wins)
    — ambiguity here would be a real, separate finding this lint does not
    attempt to adjudicate.
    """
    if not scripts_dir.is_dir():
        return None
    matches = sorted(p for p in scripts_dir.rglob(basename) if p.is_file())
    return matches[0] if matches else None


def _resolve_script(candidate: str, repo_root: Path, team_dir: Path) -> Resolution:
    normalized = _strip_root_var_prefix(candidate)
    candidates: list[Path] = [
        repo_root / normalized,
        team_dir / normalized,
        team_dir / "scripts" / normalized,
    ]
    tried = list(str(p) for p in candidates)
    for p in candidates:
        if p.is_file():
            return Resolution(resolved_path=p, tried=tuple(tried))

    if "/" not in normalized:
        found = _find_under_scripts_by_basename(normalized, team_dir / "scripts")
        tried.append(str(team_dir / "scripts" / "**" / normalized))
        if found is not None:
            return Resolution(resolved_path=found, tried=tuple(tried))

    return Resolution(resolved_path=None, tried=tuple(tried))


def _resolve_verb(candidate: str, verbs: dict, team_dir: Path) -> Optional[Resolution]:
    """Return a Resolution for *candidate* as a verb, or None if *candidate*
    is not a declared verb at all (caller then reports it as unclassified,
    not as a missing citation — a bare word that is not a verb was never a
    citation to begin with)."""
    verb = verbs.get(candidate)
    if verb is None:
        return None
    if verb.status != "active":
        # A "planned" verb has no script yet by design (see the manifest's
        # own header) — citing it is forward-looking documentation, not a
        # lie. Report as resolved-with-nothing-to-check by returning a
        # Resolution whose path is a sentinel the caller recognizes: since
        # planned verbs carry no script, there is nothing to check, so we
        # signal "not a finding" by resolving to None with an empty tried
        # list rather than a real miss.
        return Resolution(resolved_path=None, tried=())
    script_path = team_dir / verb.script
    tried = (str(script_path),)
    if script_path.is_file():
        return Resolution(resolved_path=script_path, tried=tried)
    return Resolution(resolved_path=None, tried=tried)


# ---------------------------------------------------------------------------
# --help invocation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HelpResult:
    """Whether a resolved script's ``--help`` ran cleanly, with a human-readable detail."""
    ok: bool
    detail: str  # human-readable explanation (exit code, or timeout)


def _make_help_sandbox_root() -> Path:
    """Create and return an isolated, empty directory for --help invocations
    to resolve as their kanban root AND to run in as their working
    directory.

    A well-behaved operator script never needs this — its --help/-h branch
    is checked before anything environment-dependent runs. This lint found,
    while checking the real tree, at least one script that is NOT
    well-behaved: it treats --help as an unrecognized flag and falls
    straight into its normal startup path, which resolves
    PGAI_AGENT_KANBAN_ROOT_PATH (or its own default of ``~/pgai_agent_kanban``
    when unset) and begins doing real work — reading the live install's
    config and firing real wake cycles. Per docs/coding-standards.md and
    team/roles/CODER.md ("Live-Install Safety"), no invocation this lint
    makes may be able to touch a live kanban root, so every --help
    subprocess runs with PGAI_AGENT_KANBAN_ROOT_PATH and KANBAN_ROOT
    redirected at a throwaway, empty directory: a script that behaves
    (checks --help first) is unaffected, and a script that does not is
    contained to an empty sandbox instead of the operator's real install.

    The same directory doubles as the child's explicit ``cwd`` (see
    ``_check_help``): a probe that lets a --help subprocess inherit
    whatever directory the calling process happens to be sitting in makes
    the probe's verdict a property of what ran earlier in the same process
    rather than of the tree it is checking.
    """
    # anti-pattern-allowlist: 2 (justification: mirrors the documented
    # PGAI_AGENT_KANBAN_TEMP_DIR-with-hard-fallback convention in
    # team/roles/CODER.md "Temporary File Hygiene" for non-bash callers; the
    # same fallback literal team/tests/conftest.py's own resolver carries)
    temp_base = Path(os.environ.get("PGAI_AGENT_KANBAN_TEMP_DIR") or "/tmp/pgai_kanban_tmp") / "lint_doc_command_truth"
    temp_base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="help-sandbox-", dir=str(temp_base)))


def _format_captured_output(stdout: Optional[str], stderr: Optional[str], limit: int = 2000) -> str:
    """Render a bounded excerpt of a failed child's stdout/stderr for a
    finding's ``Cause:`` line, so a failure names what the command actually
    said instead of only its exit status. Bounded so one runaway child's
    output cannot blow up the report; ``repr()`` so embedded newlines and
    control characters stay on one printed line."""
    def _trim(text: Optional[str]) -> str:
        text = (text or "").strip()
        if len(text) > limit:
            text = text[:limit] + "...[truncated]"
        return text

    return f"stdout={_trim(stdout)!r} stderr={_trim(stderr)!r}"


def _kill_process_group(pgid: int) -> None:
    """SIGKILL every process still in process group *pgid*; a group that is
    already empty (or never existed) is not an error."""
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


@dataclass(frozen=True)
class _ProbeOutcome:
    returncode: Optional[int]  # None when the probe itself never exited in time
    stdout: str
    stderr: str


def _run_probe(argv: list[str], *, env: dict, cwd: Path, timeout: float) -> _ProbeOutcome:
    """Run one --help probe and leave nothing it started alive.

    The child starts in a new session, so it and everything it spawns
    (a pipeline, a backgrounded job) share one process group whose id is
    the child's pid. That group is killed on every path out of here: after
    a timeout, and also after a normal exit, because a child can exit while
    a job it backgrounded is still running. Stdin is /dev/null, so a probe
    that waits for input gets EOF instead of hanging until the timeout.

    A probe whose own process exited before the deadline is judged by its
    exit code even when something it left behind kept the output pipes
    open past the deadline; only a probe that is itself still running at
    the deadline has no return code.
    """
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        cwd=str(cwd),
        start_new_session=True,
    )
    try:
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            return _ProbeOutcome(proc.returncode, stdout or "", stderr or "")
        except subprocess.TimeoutExpired:
            exited_in_time = proc.poll()
            _kill_process_group(proc.pid)
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                # Something outside the group still holds the pipes; give up
                # on the output rather than wait on it.
                proc.kill()
                proc.wait()
                stdout, stderr = "", ""
            return _ProbeOutcome(exited_in_time, stdout or "", stderr or "")
    finally:
        _kill_process_group(proc.pid)


def _check_help(resolved: Path, timeout: float, sandbox_root: Path) -> HelpResult:
    if resolved.suffix == ".py":
        argv = ["python3", str(resolved), "--help"]
    else:
        argv = ["bash", str(resolved), "--help"]

    env = dict(os.environ)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(sandbox_root)
    env["KANBAN_ROOT"] = str(sandbox_root)
    env["PGAI_AGENT_KANBAN_TEMP_DIR"] = str(sandbox_root)

    try:
        proc = _run_probe(
            argv,
            env=env,
            # Explicit cwd: without this the child inherits
            # whatever directory the calling process is sitting in, which
            # under a full suite run is whatever the previous test left
            # behind rather than anything this lint controls. sandbox_root
            # is the same fresh, empty directory already used to isolate
            # the child's kanban-root env vars, so both halves of "explicit
            # working directory and environment" resolve to one place.
            cwd=sandbox_root,
            timeout=timeout,
        )
    except OSError as exc:
        return HelpResult(ok=False, detail=f"'{' '.join(argv)}' could not be started: {exc}")

    if proc.returncode is None:
        return HelpResult(
            ok=False,
            detail=(
                f"'{' '.join(argv)}' did not return within {timeout}s "
                f"({_format_captured_output(proc.stdout, proc.stderr)})"
            ),
        )
    if proc.returncode == 0:
        return HelpResult(ok=True, detail="exit 0")
    return HelpResult(
        ok=False,
        detail=(
            f"'{' '.join(argv)}' exited {proc.returncode} "
            f"({_format_captured_output(proc.stdout, proc.stderr)})"
        ),
    )


# ---------------------------------------------------------------------------
# HELP FAILS exempt list — process-internal citations
# (see module docstring, "Process-internal citations (HELP FAILS exempt
# list)"). This is the same seam ``lint_help_presence.py`` /
# ``help_presence_exempt.txt`` already has, narrowed to this lint's own
# citation-vs-file distinction.
# ---------------------------------------------------------------------------


def _load_help_exempt_list(exempt_list_path: Path, verbose: bool) -> frozenset[str]:
    """Load the process-internal HELP FAILS exempt list and return a
    frozenset of repo-root-relative path strings.

    File format: one repo-root-relative path per line, ``#`` comments and
    blank lines ignored — the same shape
    ``team/scripts/help_presence_exempt.txt`` uses for its own
    ``process-internal`` exemption category. Each real entry is expected to
    carry its reason as a comment on the line(s) immediately above it; this
    loader does not enforce that shape (it only strips ``#...`` trailing
    comments and blank lines), so an unreasoned entry is a code-review
    concern, not a parse failure.

    A missing exempt list degrades to an empty set with a stderr warning
    (when *verbose*) — the same discipline this module already applies to a
    missing operator-verbs-manifest or Key Registry — so a tree or test
    fixture without this file still gets a clean, fully-enforced run of the
    --help check rather than an aborted lint.
    """
    if not exempt_list_path.is_file():
        if verbose:
            print(
                f"lint_doc_command_truth: WARNING: help-exempt list not found: "
                f"{exempt_list_path}; no HELP FAILS citation will be exempted.",
                file=sys.stderr,
            )
        return frozenset()

    entries: set[str] = set()
    for raw_line in exempt_list_path.read_text(encoding="utf-8").splitlines():
        entry = raw_line.split("#", 1)[0].strip()
        if entry:
            entries.add(entry)
    return frozenset(entries)


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


def _format_missing_finding(citation: Citation, tried: tuple[str, ...], lint_invocation: str) -> str:
    tried_list = ", ".join(tried) if tried else "(no candidate path — verb not declared)"
    return "\n".join(
        [
            f"MISSING COMMAND: {citation.candidate}",
            f"  in: {citation.doc_display}:{citation.line}",
            f"  kind: {citation.kind}",
            f"  Cause: {citation.doc_display} cites '{citation.candidate}', which does not "
            f"resolve to any file in this tree (tried: {tried_list}).",
            "  To fix:",
            f"    1. grep -n -F '{citation.candidate}' \"{citation.doc_path}\"",
            "    2. Edit the line found above so it cites a script or verb that exists, or "
            f"restore/rename the file '{citation.candidate}' is supposed to name.",
            f"    3. {lint_invocation}",
            "  Rule: every command a document cites must exist in this tree.",
        ]
    )


def _format_help_finding(citation: Citation, resolved: Path, detail: str, lint_invocation: str) -> str:
    return "\n".join(
        [
            f"HELP FAILS: {citation.candidate}",
            f"  in: {citation.doc_display}:{citation.line}",
            f"  resolved: {resolved}",
            f"  Cause: {detail}.",
            "  Next diagnostic step: re-run it directly to see the full output:",
            f"    bash \"{resolved}\" --help" if resolved.suffix != ".py" else f"    python3 \"{resolved}\" --help",
            f"  Then re-run this lint: {lint_invocation}",
            "  Rule: every command a document cites must answer --help without erroring.",
        ]
    )


# ---------------------------------------------------------------------------
# Manifest loading
# ---------------------------------------------------------------------------


def _load_verb_manifest(team_dir: Path, manifest_path: Optional[Path], verbose: bool) -> dict:
    team_str = str(team_dir)
    if team_str not in sys.path:
        sys.path.insert(0, team_str)
    try:
        from pgai_agent_kanban.lib import operator_verb_manifest as ovm
    except ImportError as exc:
        if verbose:
            print(
                f"lint_doc_command_truth: WARNING: cannot import operator_verb_manifest ({exc}); "
                "verb citations will not be checked.",
                file=sys.stderr,
            )
        return {}

    try:
        return ovm.load_manifest(manifest_path)
    except (FileNotFoundError, ovm.ManifestFormatError) as exc:
        if verbose:
            print(
                f"lint_doc_command_truth: WARNING: cannot load operator verb manifest ({exc}); "
                "verb citations will not be checked.",
                file=sys.stderr,
            )
        return {}


# ---------------------------------------------------------------------------
# Scan-set resolution
# ---------------------------------------------------------------------------


# Standing exclusions from the docs-manifest.txt scan set — see the module
# docstring's "Scan set" section for the rationale. Entries are
# manifest-relative paths, matched verbatim against each manifest line.
# Applies whenever the scan set is read from a manifest file (default or
# --docs-manifest override); --doc bypasses manifest reading entirely and
# is never filtered.
_SCAN_SET_EXCLUSIONS = frozenset(
    {
        "blocking-site-census.md",
        "read-order-manifests.md",
        "inherited-environment-exposure-survey.md",
    }
)


def _load_extra_scan_docs(
    repo_root: Path, extra_scan_manifest: Path, verbose: bool
) -> list[tuple[Path, str]]:
    """Return a list of (absolute_path, display_name) for the second scan-set
    source — ``team/config/doc-truth-extra-scan-docs.txt`` by default (see
    the module docstring's "Scan set" section for why this is a separate
    file from ``docs-manifest.txt`` rather than an addition to it). Entries
    are repo-root-relative paths, one per line, same comment/blank-line
    syntax as ``docs-manifest.txt``.

    A missing extra-scan-manifest degrades to an empty list with a stderr
    warning — the same discipline ``_load_scan_set`` already applies to a
    missing individual entry — so a tree or test fixture without this file
    still gets a clean run of the manifest-derived half of the scan set.
    """
    if not extra_scan_manifest.is_file():
        if verbose:
            print(
                f"lint_doc_command_truth: WARNING: extra scan-set manifest not found: "
                f"{extra_scan_manifest}; the four repo-root/docker additions will not be scanned.",
                file=sys.stderr,
            )
        return []

    out = []
    for raw_line in extra_scan_manifest.read_text(encoding="utf-8").splitlines():
        entry = raw_line.split("#", 1)[0].strip()
        if not entry:
            continue
        p = repo_root / entry
        if not p.is_file():
            if verbose:
                print(f"lint_doc_command_truth: WARNING: extra scan-set entry not found: {p}", file=sys.stderr)
            continue
        try:
            display = str(p.relative_to(repo_root))
        except ValueError:
            display = str(p)
        out.append((p, display))
    return out


def _load_scan_set(
    repo_root: Path,
    docs_root: Path,
    docs_manifest: Path,
    explicit_docs: list[str],
    verbose: bool,
    extra_scan_manifest: Optional[Path] = None,
) -> list[tuple[Path, str]]:
    """Return a list of (absolute_path, display_name) for every document to
    scan. *explicit_docs*, when non-empty, overrides BOTH scan-set sources
    entirely — used by callers (including tests) that want a specific
    document set. *extra_scan_manifest*, when given and *explicit_docs* is
    empty, is read via ``_load_extra_scan_docs`` and unioned onto the
    manifest-derived set (see the module docstring's "Scan set" section);
    omitted (None) means no second source is consulted at all — the caller
    (``main``) always passes one, but a direct test caller that wants only
    the manifest half is free to omit it."""
    if explicit_docs:
        out = []
        for raw in explicit_docs:
            p = Path(raw).resolve()
            try:
                display = str(p.relative_to(repo_root))
            except ValueError:
                display = str(p)
            out.append((p, display))
        return out

    if not docs_manifest.is_file():
        print(
            f"lint_doc_command_truth: ERROR: docs manifest not found: {docs_manifest}",
            file=sys.stderr,
        )
        return []

    out = []
    for raw_line in docs_manifest.read_text(encoding="utf-8").splitlines():
        entry = raw_line.split("#", 1)[0].strip()
        if not entry:
            continue
        if entry in _SCAN_SET_EXCLUSIONS:
            if verbose:
                print(f"lint_doc_command_truth: excluded from scan (inventory/audit doc): {entry}", flush=True)
            continue
        p = docs_root / entry
        if not p.is_file():
            if verbose:
                print(f"lint_doc_command_truth: WARNING: scan-set entry not found: {p}", file=sys.stderr)
            continue
        try:
            display = str(p.relative_to(repo_root))
        except ValueError:
            display = str(p)
        out.append((p, display))

    if extra_scan_manifest is not None:
        seen_paths = {p for p, _ in out}
        for p, display in _load_extra_scan_docs(repo_root, extra_scan_manifest, verbose):
            if p in seen_paths:
                continue  # already present via the manifest-derived half
            seen_paths.add(p)
            out.append((p, display))

    return out


# ---------------------------------------------------------------------------
# Config-key parity — reconciles every configuration key a document names
# against team/scripts/lib/config_loader.sh's own Key Registry.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RegistryKey:
    """One key a Key Registry comment block declares."""

    section: str  # e.g. "dashboard", "release", "models.claude"
    key: str
    status: str  # "REQUIRED" | "OPTIONAL"
    default: str  # normalized default text ("" for an empty/absent default)
    default_checked: bool = True  # False for an "N/A" registry default — see below
    source_display: str = ""  # the registry file this entry was parsed from,
    # e.g. "team/scripts/lib/config_loader.sh" — carried on the entry so a
    # finding can name the registry the key actually came from instead of
    # assuming a single fixed source file.


# The literal default token a registry line spells to declare that the key
# genuinely has no single canonical value to check documents against — a
# per-installation identity/example field (project.cfg's project_name,
# dev_tree_path, git_repo_url, branch_prefix) where every real project.cfg
# legitimately carries a different value and the *_example template's active
# line is illustrative, not a claim about what the loader falls back to when
# the key is absent. Registering these with a real default (typically "")
# would make every legitimate example value a false CONFIG DEFAULT MISMATCH —
# trading the UNKNOWN CONFIG KEY false positive this lint exists to remove
# for a different false positive on the same line. A key marked N/A here
# still participates fully in the unknown-key and undocumented-key
# directions; only the default-value comparison is skipped for it.
_REGISTRY_DEFAULT_NOT_APPLICABLE = "N/A"


class RegistryFormatError(ValueError):
    """The Key Registry comment block did not parse to a coherent key set.

    Raised only when the block cannot be found at all (no "# Key Registry"
    marker) — never for an individual line the parser does not recognize,
    since prose continuation lines inside the block are expected and
    silently skipped.
    """


_REGISTRY_START_MARKER = "# Key Registry"
_REGISTRY_SECTION_RE = re.compile(r"^\[([a-z][a-z0-9_.]*)\](.*)$")
# A key line is a lower-case key token, any run of whitespace (one space
# or column padding), then its status word. lint_config_key_coverage.py's
# _REGISTRY_KEY_RE reads the same key token and separator, so the two lints
# agree on which registry lines declare a key; prose lines ("Status:
# REQUIRED = ...") never match because their first token is not a key.
_REGISTRY_KEY_LINE_RE = re.compile(r"^([a-z_][a-z0-9_]*)\s+(REQUIRED|OPTIONAL)\b\s*(.*)$")
_REGISTRY_MODEL_KEY_LINE_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_, ]*?)\s{2,}[—-]\s+(.*)$")
_REGISTRY_MODEL_DEFAULT_RE = re.compile(r'default\s+"([^"]*)"')
# A [models.<provider>] key-group line may name its own value with
# 'shipped value "X"', overriding the section-level default for that group
# -- the same "Shipped value:" convention the template uses for a value it
# ships that the code does not fall back to on its own.
_REGISTRY_MODEL_SHIPPED_RE = re.compile(r'shipped\s+value\s+"([^"]*)"', re.IGNORECASE)


def _normalize_default_text(value: Optional[str]) -> str:
    """Normalize a default/value string for cross-source comparison.

    Strips one layer of surrounding quote/backtick characters and cuts a
    trailing explanatory parenthetical (`` "13 (per-project cap)" `` ->
    ``"13"``) the same way for both a registry-declared default and a
    document's claimed default, so the two are compared on the literal
    value alone, not the prose wrapped around it.
    """
    if value is None:
        return ""
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'", "`"):
        v = v[1:-1].strip()
    paren_idx = v.find(" (")
    if paren_idx != -1:
        v = v[:paren_idx].strip()
    return v


def _parse_config_loader_registry(
    config_loader_path: Path, source_display: str = ""
) -> dict[tuple[str, str], RegistryKey]:
    """Parse a Key Registry comment block into a
    ``{(section, key): RegistryKey}`` map.

    Reads whichever registry file *config_loader_path* names — this parser
    is shared between config_loader.sh's kanban.cfg registry and
    project_paths.sh's project.cfg registry, since both blocks share one
    shape. *source_display* (the display path/label the caller wants
    attributed to every entry this parse produces, e.g.
    "team/scripts/lib/config_loader.sh") is stamped onto each ``RegistryKey``
    so a downstream finding can name the registry a key actually came from.

    A structural reader of the block's own documented shape (one
    ``# [section]`` header per INI section, one ``#   key STATUS default``
    line per key, or — for the three ``[models.<provider>]`` sections — a
    section-level default plus one ``#   key[, key...]  — description``
    line per key group) — never a hand-transcribed second copy of the key
    list. See the module docstring's "Config-key parity" section for why
    this reads the registry rather than a copy of it.

    Raises:
        RegistryFormatError: the file has no "# Key Registry" marker at all.
    """
    text = config_loader_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    start = None
    for i, line in enumerate(lines):
        if line.strip() == _REGISTRY_START_MARKER:
            start = i
            break
    if start is None:
        raise RegistryFormatError(
            f"{config_loader_path}: no '{_REGISTRY_START_MARKER}' marker found — "
            "cannot locate the Key Registry block."
        )

    registry: dict[tuple[str, str], RegistryKey] = {}
    current_section: Optional[str] = None
    current_section_is_models = False
    model_default = ""

    for line in lines[start + 1 :]:
        if not line.startswith("#"):
            break  # end of the comment block
        content = line[1:].strip()

        sec_match = _REGISTRY_SECTION_RE.match(content)
        if sec_match:
            current_section = sec_match.group(1)
            current_section_is_models = current_section.startswith("models.")
            model_default = ""
            if current_section_is_models:
                dm = _REGISTRY_MODEL_DEFAULT_RE.search(sec_match.group(2))
                if dm:
                    model_default = dm.group(1)
            continue

        if current_section is None:
            continue  # prose before the first [section] header

        if current_section_is_models:
            mm = _REGISTRY_MODEL_KEY_LINE_RE.match(content)
            if mm:
                shipped = _REGISTRY_MODEL_SHIPPED_RE.search(mm.group(2))
                group_default = shipped.group(1) if shipped else model_default
                for key in (k.strip() for k in mm.group(1).split(",")):
                    if key:
                        registry[(current_section, key)] = RegistryKey(
                            current_section,
                            key,
                            "OPTIONAL",
                            group_default,
                            source_display=source_display,
                        )
            continue

        km = _REGISTRY_KEY_LINE_RE.match(content)
        if km:
            key, status, remainder = km.group(1), km.group(2), km.group(3).strip()
            default = remainder.split(" (", 1)[0].strip()
            default_checked = True
            if default.upper() == _REGISTRY_DEFAULT_NOT_APPLICABLE:
                default_checked = False
                default = ""
            elif default in ('""', "''"):
                default = ""
            registry[(current_section, key)] = RegistryKey(
                current_section,
                key,
                status,
                default,
                default_checked,
                source_display=source_display,
            )
        # else: a wrapped-comment continuation line — no key/status token, skip.

    return registry


@dataclass(frozen=True)
class KeyCitation:
    """One configuration-key claim a document makes: the section, the key, and the default it states.

    *commented* is True for a raw template's commented ``# key = value``
    line: it documents that the key exists (the undocumented-key direction
    counts it) but makes no claim about the accepted key set or its default,
    so the unknown-key and default comparisons skip it.
    """
    doc_display: str
    doc_path: Path
    line: int
    section: str
    key: str
    claimed_default: str
    commented: bool = False


_KEY_SECTION_BRACKET_RE = re.compile(r"^\[([a-z][a-z0-9_.]*)\]")
_KEY_HEADING_RE = re.compile(r"^#{1,6}\s")
_KEY_INI_KEYVAL_RE = re.compile(r"^([a-z_][a-z0-9_]*)\s*=\s*(.*)$")
# A raw template's real section header: an uncommented "[section]" line,
# optionally followed by an inline comment.
_KEY_RAW_INI_HEADER_RE = re.compile(r"^\[([a-z][a-z0-9_.]*)\]\s*(?:[#;].*)?$")
# A raw template's commented key line ("# key = value", "# key ="): the
# shape lint_config_key_coverage.py's _COMMENTED_KEY_RE reads, a single-token
# value or none, so prose that happens to contain "word = words" never matches.
_KEY_RAW_INI_COMMENTED_RE = re.compile(r"^[#;]\s?([a-z_][a-z0-9_]*)\s*=\s*(\S*)\s*$")
_KEY_TABLE_ROW_RE = re.compile(r"^\|\s*`([a-z_][a-z0-9_]*)`\s*\|\s*`?([^|`]*)`?\s*\|")
_KEY_FENCE_OPEN_RE = re.compile(r"^(`{3,}|~{3,})\s*([A-Za-z0-9_+-]*)\s*$")


def _section_header_from_line(line: str) -> Optional[str]:
    """Return the section name when *line* is a section-header line for the
    key-parity scanner, or None otherwise.

    A section header is recognized when a ``[section]`` bracket sits at the
    very start of the line once common decoration (a leading ``#`` comment
    marker, markdown ``*`` emphasis, or a backtick) is stripped — this reads
    both a raw INI ``[dashboard]`` header and a markdown cross-reference
    line like `` **`[dashboard]` — layout knobs:** `` the same way, while
    leaving a mid-sentence bracket mention (`` See project.cfg_example
    [project] temp_dir. ``) alone because the bracket there is not at the
    start of the line. A bracket immediately followed by ``(`` is a
    markdown link (`` [text](url) ``), never a section header.
    """
    s = line.strip().lstrip("#*` \t")
    m = _KEY_SECTION_BRACKET_RE.match(s)
    if not m:
        return None
    if s[m.end() :].startswith("("):
        return None
    return m.group(1)


def _extract_key_citations(
    doc_path: Path, doc_display: str, is_raw_ini: bool
) -> list[KeyCitation]:
    """Return every configuration-key citation found in *doc_path*.

    *is_raw_ini* is True for a real ``.cfg_example`` template (the whole
    file is INI-shaped, no markdown fencing) and False for a markdown
    document, where key=value scanning is restricted to fenced ```` ```ini
    ```` blocks and key/default claims also come from markdown reference
    tables. See the module docstring's "Config-key parity" section for the
    full citation-recognition rule.
    """
    try:
        lines = doc_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        print(f"lint_doc_command_truth: WARNING: cannot read {doc_path}: {exc}", file=sys.stderr)
        return []

    citations: list[KeyCitation] = []
    current_section: Optional[str] = None
    fence_active = False
    fence_is_ini = False
    fence_char = ""
    fence_len = 0

    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()

        if not is_raw_ini:
            if not fence_active:
                m = _KEY_FENCE_OPEN_RE.match(stripped)
                if m:
                    fence_active = True
                    fence_char = m.group(1)[0]
                    fence_len = len(m.group(1))
                    fence_is_ini = m.group(2).strip().lower() == "ini"
                    continue
            elif re.match(rf"^[{re.escape(fence_char)}]{{{fence_len},}}\s*$", stripped):
                fence_active = False
                fence_is_ini = False
                continue

        if is_raw_ini:
            # Only a real header switches a raw template's section: a prose
            # comment that happens to open with a bracket ("# [project]
            # dev_tree_path is empty ...") describes another file's key and
            # must not re-home the keys that follow it.
            hm = _KEY_RAW_INI_HEADER_RE.match(stripped)
            sec = hm.group(1) if hm else None
        else:
            sec = _section_header_from_line(line)
        if sec is not None:
            current_section = sec
        elif not is_raw_ini and not fence_active and _KEY_HEADING_RE.match(line):
            current_section = None

        scan_kv = is_raw_ini or (fence_active and fence_is_ini)
        if scan_kv and current_section:
            km = _KEY_INI_KEYVAL_RE.match(stripped)
            if km:
                citations.append(
                    KeyCitation(doc_display, doc_path, lineno, current_section, km.group(1), km.group(2).strip())
                )
            elif is_raw_ini:
                cm = _KEY_RAW_INI_COMMENTED_RE.match(stripped)
                if cm:
                    citations.append(
                        KeyCitation(
                            doc_display, doc_path, lineno, current_section, cm.group(1), cm.group(2).strip(),
                            commented=True,
                        )
                    )
            continue

        if not is_raw_ini and not fence_active and current_section:
            tm = _KEY_TABLE_ROW_RE.match(line)
            if tm:
                citations.append(
                    KeyCitation(doc_display, doc_path, lineno, current_section, tm.group(1), tm.group(2).strip())
                )

    return citations


def _format_unknown_key_finding(
    citation: KeyCitation,
    lint_invocation: str,
    *,
    config_loader_display: str,
    project_key_registry_display: str,
) -> str:
    return "\n".join(
        [
            f"UNKNOWN CONFIG KEY: [{citation.section}] {citation.key}",
            f"  in: {citation.doc_display}:{citation.line}",
            f"  Cause: {citation.doc_display} documents key '{citation.key}' under "
            f"[{citation.section}], which neither {config_loader_display}'s nor "
            f"{project_key_registry_display}'s Key Registry declares.",
            "  To fix:",
            f"    1. grep -n -F '{citation.key}' \"{citation.doc_path}\"",
            "    2. Correct the section/key name to one a registry declares, or remove "
            "the stale entry if the key was removed from the loader.",
            f"    3. {lint_invocation}",
            "  Rule: every configuration key a document names under a recognized section "
            "must be one a registry accepts.",
        ]
    )


def _format_key_default_mismatch_finding(
    citation: KeyCitation, entry: RegistryKey, lint_invocation: str
) -> str:
    return "\n".join(
        [
            f"CONFIG DEFAULT MISMATCH: [{citation.section}] {citation.key}",
            f"  in: {citation.doc_display}:{citation.line}",
            f"  documented default: {citation.claimed_default!r}",
            f"  loader default:     {entry.default!r}",
            "  To fix:",
            f"    1. grep -n -F '{citation.key}' \"{citation.doc_path}\"",
            f"    2. Update the documented default to match {entry.source_display}'s Key "
            "Registry, or update the registry if the shipped default actually changed.",
            f"    3. {lint_invocation}",
            "  Rule: a documented key's default must match the loader's Key Registry default.",
        ]
    )


def _format_undocumented_key_finding(entry: RegistryKey, lint_invocation: str) -> str:
    return "\n".join(
        [
            f"UNDOCUMENTED CONFIG KEY: [{entry.section}] {entry.key}",
            f"  Cause: {entry.source_display}'s Key Registry declares [{entry.section}] "
            f"{entry.key} (status={entry.status}, default={entry.default!r}), but no scanned "
            "document mentions it.",
            "  To fix:",
            "    1. Add coverage for this key to the appropriate reference document "
            "(kanban.cfg_example, project.cfg_example, or a docs/ configuration reference).",
            f"    2. {lint_invocation}",
            "  Rule: every loader-accepted key should be documented somewhere in the scanned "
            "reference set. This direction is a worklist entry, not a correctness bug — it is "
            "never suppressed.",
        ]
    )


def run_key_parity_check(
    scan_set: list[tuple[Path, str]],
    registry: dict[tuple[str, str], RegistryKey],
    lint_invocation: str,
    *,
    config_loader_display: str = "team/scripts/lib/config_loader.sh",
    project_key_registry_display: str = "team/scripts/lib/project_paths.sh",
) -> tuple[list[str], int, list[KeyCitation]]:
    """Reconcile every configuration key cited across *scan_set* against
    *registry*.

    *config_loader_display* and *project_key_registry_display* are the
    display paths named in an ``UNKNOWN CONFIG KEY`` finding, since a key
    absent from *registry* is absent from both source registries and the
    finding must say so; a key present in *registry* instead names its own
    ``RegistryKey.source_display`` (set at parse time by whichever registry
    produced the entry), so a ``CONFIG DEFAULT MISMATCH`` or
    ``UNDOCUMENTED CONFIG KEY`` finding always points at the file that
    actually declares the key.

    Returns (findings, citation_count, matched_citations) where
    *matched_citations* is the subset of citations that resolved to a real
    registry entry — used only for the docstring's discrimination tests;
    callers otherwise just want *findings*.
    """
    all_citations: list[KeyCitation] = []
    for doc_path, doc_display in scan_set:
        is_raw_ini = doc_path.name.endswith(".cfg_example")
        all_citations.extend(_extract_key_citations(doc_path, doc_display, is_raw_ini))

    findings: list[str] = []
    matched: set[tuple[str, str]] = set()
    matched_citations: list[KeyCitation] = []

    # A commented template line documents a registered key, nothing more.
    for c in all_citations:
        if c.commented and (c.section, c.key) in registry:
            matched.add((c.section, c.key))
    citations = [c for c in all_citations if not c.commented]

    for c in citations:
        entry = registry.get((c.section, c.key))
        if entry is None:
            findings.append(
                _format_unknown_key_finding(
                    c,
                    lint_invocation,
                    config_loader_display=config_loader_display,
                    project_key_registry_display=project_key_registry_display,
                )
            )
            continue
        matched.add((c.section, c.key))
        matched_citations.append(c)
        if entry.default_checked and _normalize_default_text(c.claimed_default) != _normalize_default_text(
            entry.default
        ):
            findings.append(_format_key_default_mismatch_finding(c, entry, lint_invocation))

    for section_key in sorted(set(registry.keys()) - matched):
        findings.append(_format_undocumented_key_finding(registry[section_key], lint_invocation))

    return findings, len(citations), matched_citations


# ---------------------------------------------------------------------------
# Dashboard window/pane parity — reconciles every window/pane claim a
# document makes against team/config/dashboard_windows.census, read through
# window_census.load_census() (the one Python reader for that file).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WindowCitation:
    """One dashboard window a document names: its tmux index and the name it claims (None when unnamed)."""
    doc_display: str
    doc_path: Path
    line: int
    tmux_index: int
    claimed_name: Optional[str]


@dataclass(frozen=True)
class PaneCitation:
    """One pane-table row a document carries for a window: the pane's position label and the script it claims."""
    doc_display: str
    doc_path: Path
    line: int
    tmux_index: int
    position_label: str
    claimed_script: str


_PANE_WINDOW_NAME_THEN_IDX_RE = re.compile(r"\b([A-Z][A-Za-z_-]{1,40})\s*\(\s*[Ww]indow\s+(\d+)\s*\)")
_PANE_WINDOW_IDX_THEN_NAME_RE = re.compile(r"\b[Ww]indow\s+(\d+)\s*[:(]\s*`?([A-Za-z][A-Za-z0-9_-]*)`?\)?")
_PANE_WINDOW_BARE_IDX_RE = re.compile(r"\b[Ww]indow\s+(\d+)\b")
_PANE_TABLE_HEADER_RE = re.compile(r"^\|\s*Pane\s*\|.*Script.*\|", re.IGNORECASE)
_PANE_TABLE_SEPARATOR_RE = re.compile(r"^\|[\s:|-]+\|$")
_PANE_TABLE_ROW_RE = re.compile(r"^\|\s*([^|`]+?)\s*\|\s*`([^`|]+)`\s*\|")
_PANE_HEADING_RE = re.compile(r"^#{1,6}\s")

_PANE_TABLE_LOOKAHEAD_LINES = 20


def _line_window_citations(line: str) -> list[tuple[int, Optional[str]]]:
    """Return (tmux_index, claimed_name_or_None) for every window citation
    on *line*, checked in priority order (name-then-index, index-then-name,
    bare index) so an already-matched span is never double-counted by a
    lower-priority pattern."""
    spans: list[tuple[int, int]] = []
    results: list[tuple[int, Optional[str]]] = []

    for m in _PANE_WINDOW_NAME_THEN_IDX_RE.finditer(line):
        results.append((int(m.group(2)), m.group(1)))
        spans.append(m.span())

    for m in _PANE_WINDOW_IDX_THEN_NAME_RE.finditer(line):
        if any(a <= m.start() < b for a, b in spans):
            continue
        results.append((int(m.group(1)), m.group(2)))
        spans.append(m.span())

    for m in _PANE_WINDOW_BARE_IDX_RE.finditer(line):
        if any(a <= m.start() < b for a, b in spans):
            continue
        results.append((int(m.group(1)), None))
        spans.append(m.span())

    return results


def _extract_pane_table_rows(
    lines: list[str], header_line: int, tmux_index: int, doc_path: Path, doc_display: str
) -> list[PaneCitation]:
    """Return one PaneCitation per data row of the ``| Pane | ... Script
    ... |`` table whose header sits at *header_line* (0-based), associated
    with *tmux_index*."""
    citations: list[PaneCitation] = []
    n = len(lines)
    k = header_line + 1
    if k < n and _PANE_TABLE_SEPARATOR_RE.match(lines[k].strip()):
        k += 1
    while k < n and lines[k].strip().startswith("|"):
        m = _PANE_TABLE_ROW_RE.match(lines[k].strip())
        if m:
            citations.append(
                PaneCitation(doc_display, doc_path, k + 1, tmux_index, m.group(1).strip(), m.group(2).strip())
            )
        k += 1
    return citations


def _extract_window_and_pane_citations(
    doc_path: Path, doc_display: str
) -> tuple[list[WindowCitation], list[PaneCitation]]:
    """Return every window and pane citation in *doc_path*.

    A pane table is attached only to a single, unambiguous preceding
    window citation — the most recent line naming EXACTLY ONE window
    index, within ``_PANE_TABLE_LOOKAHEAD_LINES`` lines and with no
    markdown heading in between. A line naming more than one window index
    (e.g. two windows compared in the same sentence) intentionally leaves
    the pending index ambiguous rather than guessing which one owns a
    table that follows — attaching the same table to both would produce a
    false pane-mismatch finding against whichever window did not actually
    own it.
    """
    try:
        lines = doc_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        print(f"lint_doc_command_truth: WARNING: cannot read {doc_path}: {exc}", file=sys.stderr)
        return [], []

    window_citations: list[WindowCitation] = []
    pane_citations: list[PaneCitation] = []

    pending_index: Optional[int] = None
    pending_line: Optional[int] = None

    for i, line in enumerate(lines):
        if _PANE_HEADING_RE.match(line):
            pending_index = None
            pending_line = None

        line_hits = _line_window_citations(line)
        for tmux_index, name in line_hits:
            window_citations.append(WindowCitation(doc_display, doc_path, i + 1, tmux_index, name))

        distinct_indices = {idx for idx, _name in line_hits}
        if len(distinct_indices) == 1:
            pending_index = next(iter(distinct_indices))
            pending_line = i
        elif len(distinct_indices) > 1:
            pending_index = None
            pending_line = None

        if (
            pending_index is not None
            and pending_line is not None
            and i > pending_line
            and (i - pending_line) <= _PANE_TABLE_LOOKAHEAD_LINES
            and _PANE_TABLE_HEADER_RE.match(line.strip())
        ):
            pane_citations.extend(_extract_pane_table_rows(lines, i, pending_index, doc_path, doc_display))
            pending_index = None
            pending_line = None

    return window_citations, pane_citations


def _format_unknown_window_finding(citation: WindowCitation, lint_invocation: str) -> str:
    return "\n".join(
        [
            f"UNKNOWN DASHBOARD WINDOW: window {citation.tmux_index}",
            f"  in: {citation.doc_display}:{citation.line}",
            f"  Cause: {citation.doc_display} describes tmux window {citation.tmux_index}, "
            "which team/config/dashboard_windows.census does not declare.",
            "  To fix:",
            f"    1. grep -n -F 'window {citation.tmux_index}' \"{citation.doc_path}\"",
            "    2. Correct the window index to one the census declares, or update the "
            "census if a new window was actually added.",
            f"    3. {lint_invocation}",
            "  Rule: every dashboard window a document cites must be one the census declares.",
        ]
    )


def _format_window_name_mismatch_finding(
    citation: WindowCitation, actual_name: str, lint_invocation: str
) -> str:
    return "\n".join(
        [
            f"DASHBOARD WINDOW NAME MISMATCH: window {citation.tmux_index}",
            f"  in: {citation.doc_display}:{citation.line}",
            f"  documented name: {citation.claimed_name!r}",
            f"  census name:     {actual_name!r}",
            "  To fix:",
            f"    1. grep -n -F 'window {citation.tmux_index}' \"{citation.doc_path}\"",
            "    2. Update the documented name to match the census, or update "
            "team/config/dashboard_windows.census if the window was actually renamed.",
            f"    3. {lint_invocation}",
            "  Rule: a documented window's name must match the census.",
        ]
    )


def _format_unknown_pane_finding(citation: PaneCitation, lint_invocation: str) -> str:
    return "\n".join(
        [
            f"UNKNOWN DASHBOARD PANE: window {citation.tmux_index}, position "
            f"{citation.position_label!r}",
            f"  in: {citation.doc_display}:{citation.line}",
            f"  Cause: {citation.doc_display} claims window {citation.tmux_index}'s "
            f"{citation.position_label!r} pane runs '{citation.claimed_script}', which "
            "team/config/dashboard_windows.census does not declare for that window.",
            "  To fix:",
            f"    1. grep -n -F '{citation.claimed_script}' \"{citation.doc_path}\"",
            "    2. Correct the pane's script/position to one the census declares for this "
            "window, or update the census if the pane actually changed.",
            f"    3. {lint_invocation}",
            "  Rule: every dashboard pane a document cites must be one the census declares.",
        ]
    )


def _format_undocumented_window_finding(window: "Window", lint_invocation: str) -> str:
    return "\n".join(
        [
            f"UNDOCUMENTED DASHBOARD WINDOW: window {window.tmux_index} ({window.name})",
            f"  Cause: team/config/dashboard_windows.census declares window "
            f"{window.tmux_index} ({window.name!r}), but no scanned document describes it "
            "(outside the generated docs/DASHBOARD-PANES.md, which generate_window_docs.py "
            "--check already keeps honest).",
            "  To fix:",
            "    1. Add a mention of this window to the appropriate operator-facing document.",
            f"    2. {lint_invocation}",
            "  Rule: every census window should be described somewhere in the scanned "
            "reference set. This direction is a worklist entry, not a correctness bug — it "
            "is never suppressed.",
        ]
    )


def run_pane_parity_check(
    scan_set: list[tuple[Path, str]],
    windows: list["Window"],
    lint_invocation: str,
) -> tuple[list[str], int]:
    """Reconcile every window/pane claim cited across *scan_set* against
    *windows* (as returned by window_census.load_census()).

    Returns (findings, citation_count).
    """
    window_citations: list[WindowCitation] = []
    pane_citations: list[PaneCitation] = []
    for doc_path, doc_display in scan_set:
        wc, pc = _extract_window_and_pane_citations(doc_path, doc_display)
        window_citations.extend(wc)
        pane_citations.extend(pc)

    valid_indices = {w.tmux_index for w in windows}
    name_by_index: dict[int, str] = {}
    panes_by_index: dict[int, list] = {}
    for w in windows:
        name_by_index.setdefault(w.tmux_index, w.name)
        panes_by_index.setdefault(w.tmux_index, []).extend(w.panes)

    findings: list[str] = []
    referenced_indices: set[int] = set()

    for wcit in window_citations:
        referenced_indices.add(wcit.tmux_index)
        if wcit.tmux_index not in valid_indices:
            findings.append(_format_unknown_window_finding(wcit, lint_invocation))
            continue
        if wcit.claimed_name is not None:
            actual_name = name_by_index[wcit.tmux_index]
            if wcit.claimed_name.strip().lower() != actual_name.strip().lower():
                findings.append(_format_window_name_mismatch_finding(wcit, actual_name, lint_invocation))

    for pcit in pane_citations:
        panes = panes_by_index.get(pcit.tmux_index, [])
        claimed_basename = pcit.claimed_script.strip().rsplit("/", 1)[-1]
        match = any(claimed_basename == p.script.rsplit("/", 1)[-1] for p in panes)
        if not match:
            findings.append(_format_unknown_pane_finding(pcit, lint_invocation))

    for w in windows:
        if w.tmux_index not in referenced_indices:
            findings.append(_format_undocumented_window_finding(w, lint_invocation))
            referenced_indices.add(w.tmux_index)  # one finding per index, not per variant

    total_citations = len(window_citations) + len(pane_citations)
    return findings, total_citations


def _load_dashboard_windows(team_dir: Path, census_path: Optional[Path]) -> list:
    """Load the dashboard census through window_census.load_census() —
    the one Python reader for team/config/dashboard_windows.census.

    Loaded by file path under *team_dir* (``importlib.util.spec_from_file_
    location``, re-executed on every call) rather than a package import
    off ``sys.path`` — a bare ``import pgai_agent_kanban.dashboard.
    window_census`` would resolve through ``sys.modules``'s cache the
    first time any caller (this lint, a test in the same pytest session)
    imports that dotted name, so a later call passing a DIFFERENT
    *team_dir* (a test's isolated fixture tree) would silently keep
    reading the first tree's census instead of failing loudly or reading
    its own. Loading by explicit file path is immune to that cross-call
    cache bleed: this always reads the census reader that actually lives
    under *team_dir*, or raises ImportError if none does.

    Raises whatever load_census() itself raises (FileNotFoundError,
    CensusFormatError) plus ImportError if the module cannot be found —
    the caller degrades to an empty window list with a stderr warning
    rather than aborting, matching this file's existing degrade-and-warn
    discipline for a missing/malformed operator-verbs-manifest.
    """
    module_path = team_dir / "pgai_agent_kanban" / "dashboard" / "window_census.py"
    if not module_path.is_file():
        raise ImportError(f"window_census.py not found at {module_path}")

    spec = importlib.util.spec_from_file_location("_pgai_window_census_reader", module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load spec from {module_path}")
    mod = importlib.util.module_from_spec(spec)
    # Register before exec: window_census.py's frozen dataclasses combined
    # with `from __future__ import annotations` resolve annotation strings
    # via sys.modules[cls.__module__] during class-body execution — see the
    # identical note on the sibling test module's own loader.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]

    return mod.load_census(census_path)


def _resolve_key_parity_scan_set(
    args: argparse.Namespace,
    repo_root: Path,
    docs_root: Path,
    docs_manifest: Path,
    base_scan_set: list[tuple[Path, str]],
) -> list[tuple[Path, str]]:
    """Scan set for the key-parity check: the shared manifest-derived set
    plus the two root templates, unless overridden by ``--key-doc`` (test
    isolation) or the caller already narrowed the shared set with
    ``--doc`` (in which case no extra templates are added — an explicit
    ``--doc`` means "scan exactly this," matching ``_load_scan_set``'s own
    contract)."""
    if args.key_doc:
        return _load_scan_set(repo_root, docs_root, docs_manifest, args.key_doc, args.verbose)
    if args.doc:
        return base_scan_set
    result = list(base_scan_set)
    for rel in ("kanban.cfg_example", "project.cfg_example"):
        p = repo_root / rel
        if p.is_file():
            try:
                display = str(p.relative_to(repo_root))
            except ValueError:
                display = str(p)
            result.append((p, display))
    return result


def _resolve_pane_parity_scan_set(
    args: argparse.Namespace,
    repo_root: Path,
    docs_root: Path,
    docs_manifest: Path,
    base_scan_set: list[tuple[Path, str]],
) -> list[tuple[Path, str]]:
    """Scan set for the pane-parity check: the shared manifest-derived set
    (docs/DASHBOARD-PANES.md is never in that set to begin with — see the
    module docstring), unless overridden by ``--pane-doc`` for test
    isolation."""
    if args.pane_doc:
        return _load_scan_set(repo_root, docs_root, docs_manifest, args.pane_doc, args.verbose)
    return base_scan_set


# ---------------------------------------------------------------------------
# Undocumented installed scripts (fourth check, the reverse direction) --
# every script install.sh copies into $KANBAN_ROOT/scripts/ must be named by
# an operator doc or listed in the "Internal checks" section.
# ---------------------------------------------------------------------------

# The three operator docs, repo-root-relative. A script named anywhere in one
# of them is documented.
_OPERATOR_DOCS: tuple[str, ...] = ("HOW_TO.md", "docs/OPERATIONS.md", "docs/operator-commands.md")

# Docs that may carry the "Internal checks" section, repo-root-relative. Only
# the text under that heading counts from these (the operator docs among them
# count whole anyway).
_INTERNAL_CHECKS_DOCS: tuple[str, ...] = ("docs/operator-commands.md", "team/TESTING.md")

# The script folders checked, relative to team/scripts/: the top level ("")
# plus the four subfolders whose scripts operators or the release machinery
# run by path.
_CHECKED_SCRIPT_DIRS: tuple[str, ...] = ("", "cm", "cleanup", "migrate", "migrations")

_INTERNAL_CHECKS_HEADING_RE = re.compile(r"^(#{1,6})\s+Internal\s+checks\s*$", re.IGNORECASE)
_ANY_HEADING_RE = re.compile(r"^(#{1,6})\s")


@dataclass(frozen=True)
class UndocumentedScript:
    """One installed script no operator doc names and the Internal checks section does not list."""

    rel: str  # path relative to team/scripts/, e.g. "cm/finalize-release.sh"


def _installed_scripts(scripts_dir: Path) -> list[str]:
    """Return every checked script, as a path relative to *scripts_dir*, sorted.

    The top level of *scripts_dir* and its ``cm/``, ``cleanup/``,
    ``migrate/`` and ``migrations/`` folders; ``*.sh`` and ``*.py`` files
    only, package markers (``__init__.py``) excluded.
    """
    found: list[str] = []
    for sub in _CHECKED_SCRIPT_DIRS:
        folder = scripts_dir / sub if sub else scripts_dir
        if not folder.is_dir():
            continue
        for path in folder.iterdir():
            if path.is_file() and path.suffix in _SCRIPT_SUFFIXES and path.name != "__init__.py":
                found.append(path.relative_to(scripts_dir).as_posix())
    return sorted(found)


def _internal_checks_section(lines: list[str]) -> tuple[str, int]:
    """Return (text, 1-based heading line) of the "Internal checks" section in *lines*.

    The section runs from its heading to the next heading of the same or a
    higher level. ``("", 0)`` when the document has no such heading.
    """
    for index, line in enumerate(lines):
        match = _INTERNAL_CHECKS_HEADING_RE.match(line.strip())
        if not match:
            continue
        level = len(match.group(1))
        end = len(lines)
        for later in range(index + 1, len(lines)):
            other = _ANY_HEADING_RE.match(lines[later])
            if other and len(other.group(1)) <= level:
                end = later
                break
        return "\n".join(lines[index:end]), index + 1
    return "", 0


def _script_named_in(text: str, rel: str) -> bool:
    """True when *text* names the script *rel* by its file name, bare or inside a path.

    A name counts as a whole token: ``foo.sh`` matches ``foo.sh``,
    ``scripts/foo.sh`` and ``$KANBAN_ROOT/scripts/cm/foo.sh`` but not
    ``other-foo.sh`` or ``foo.shx``. Code spans, plain prose and table cells
    all count -- the check asks whether a reader can find the script, not
    how it is formatted.
    """
    basename = rel.rsplit("/", 1)[-1]
    pattern = re.compile(r"(?<![\w.-])" + re.escape(basename) + r"(?![\w-])")
    return bool(pattern.search(text))


def run_undocumented_script_check(
    scripts_dir: Path,
    operator_docs: list[tuple[Path, str]],
    internal_checks_docs: list[tuple[Path, str]],
) -> tuple[list[UndocumentedScript], int, Optional[tuple[str, int]]]:
    """Report every installed script no operator doc names and no Internal checks section lists.

    Returns ``(undocumented, scripts_checked, where_to_list)``. *where_to_list*
    is the ``(display path, heading line)`` of the first "Internal checks"
    section found, the place a finding's remedy opens -- ``None`` when no
    scanned document has that section. Documents that do not exist are
    skipped; the caller decides whether a tree with none of them is checked
    at all.
    """
    corpus: list[str] = []
    for path, _display in operator_docs:
        try:
            corpus.append(path.read_text(encoding="utf-8"))
        except OSError:
            continue
    where_to_list: Optional[tuple[str, int]] = None
    for path, display in internal_checks_docs:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        section, heading_line = _internal_checks_section(lines)
        if heading_line:
            corpus.append(section)
            if where_to_list is None:
                where_to_list = (display, heading_line)
    text = "\n".join(corpus)
    scripts = _installed_scripts(scripts_dir)
    undocumented = [UndocumentedScript(rel) for rel in scripts if not _script_named_in(text, rel)]
    return undocumented, len(scripts), where_to_list


def _format_undocumented_script_finding(
    entry: UndocumentedScript,
    operator_doc_displays: list[str],
    where_to_list: Optional[tuple[str, int]],
    lint_invocation: str,
) -> str:
    docs_named = ", ".join(operator_doc_displays) or "(none found)"
    if where_to_list is not None:
        display, line = where_to_list
        open_step = f"${{EDITOR:-vi}} +{line} {display}"
        where = f"the Internal checks section of {display} (line {line})"
    else:
        open_step = "${EDITOR:-vi} docs/operator-commands.md"
        where = "a new '## Internal checks' section in docs/operator-commands.md"
    return "\n".join(
        [
            f"UNDOCUMENTED SCRIPT: scripts/{entry.rel}",
            f"  Cause: install.sh ships scripts/{entry.rel}, but none of the operator docs "
            f"({docs_named}) names it and no Internal checks section lists it, so an operator "
            "cannot tell what it is or whether to run it.",
            "  To fix:",
            f"    1. {open_step}",
            f"    2. Add a one-line entry for {entry.rel} to {where} (what it checks and who "
            "runs it), or document it as an operator tool in one of the operator docs.",
            f"    3. {lint_invocation}",
            "  Rule: every script the install ships is named in an operator doc or listed in "
            "the Internal checks section.",
        ]
    )


def _resolve_doc_list(repo_root: Path, explicit: list[str], defaults: tuple[str, ...]) -> list[tuple[Path, str]]:
    """Return ``(path, display)`` for each explicit path, or each default that exists under *repo_root*."""
    out: list[tuple[Path, str]] = []
    if explicit:
        for raw in explicit:
            path = Path(raw).resolve()
            try:
                display = str(path.relative_to(repo_root))
            except ValueError:
                display = str(path)
            out.append((path, display))
        return out
    for rel in defaults:
        path = repo_root / rel
        if path.is_file():
            out.append((path, rel))
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="lint_doc_command_truth.py",
        description=(
            "Doc-truth lint (existence half): every command a document cites "
            "must exist in this tree and must answer --help without erroring."
        ),
    )
    parser.add_argument("--repo-root", metavar="PATH", help="Repo root (default: inferred from this script's location).")
    parser.add_argument(
        "--docs-manifest",
        metavar="PATH",
        help="Scan-set manifest (default: <repo-root>/team/config/docs-manifest.txt).",
    )
    parser.add_argument("--docs-root", metavar="PATH", help="Root the manifest entries resolve against (default: <repo-root>/docs).")
    parser.add_argument(
        "--extra-scan-manifest",
        metavar="PATH",
        help=(
            "Second scan-set source, unioned onto --docs-manifest's set (default: "
            "<repo-root>/team/config/doc-truth-extra-scan-docs.txt). Entries are "
            "repo-root-relative paths, one per line. Kept separate from "
            "docs-manifest.txt because that file's stated purpose is narrower — see "
            "the module docstring's 'Scan set' section."
        ),
    )
    parser.add_argument(
        "--doc",
        metavar="PATH",
        action="append",
        default=[],
        help="Scan exactly this document (repeatable). Overrides --docs-manifest and --extra-scan-manifest entirely.",
    )
    parser.add_argument(
        "--operator-verbs-manifest",
        metavar="PATH",
        help="Operator verb manifest (default: <repo-root>/team/config/operator_verbs.manifest).",
    )
    parser.add_argument(
        "--config-loader",
        metavar="PATH",
        help="config_loader.sh path for the key-parity check (default: <repo-root>/team/scripts/lib/config_loader.sh) — the kanban.cfg Key Registry.",
    )
    parser.add_argument(
        "--project-key-registry",
        metavar="PATH",
        help="project_paths.sh path for the key-parity check (default: <repo-root>/team/scripts/lib/project_paths.sh) — the project.cfg Key Registry, a distinct key set from kanban.cfg's.",
    )
    parser.add_argument(
        "--census",
        metavar="PATH",
        help="Dashboard window census path for the pane-parity check (default: window_census.default_census_path()).",
    )
    parser.add_argument(
        "--key-doc",
        metavar="PATH",
        action="append",
        default=[],
        help="Scan exactly this document for the key-parity check (repeatable). Overrides the shared scan set for that check only.",
    )
    parser.add_argument(
        "--pane-doc",
        metavar="PATH",
        action="append",
        default=[],
        help="Scan exactly this document for the pane-parity check (repeatable). Overrides the shared scan set for that check only.",
    )
    parser.add_argument(
        "--help-timeout",
        type=float,
        default=DEFAULT_HELP_TIMEOUT_SECONDS,
        metavar="SECONDS",
        help=f"Timeout for each --help invocation (default: {DEFAULT_HELP_TIMEOUT_SECONDS:g}).",
    )
    parser.add_argument(
        "--help-exempt-list",
        metavar="PATH",
        help=(
            "Exempt-list data file naming citations that are process-internal "
            "(a sourced prelude, a container entrypoint — never an operator "
            "command line), so a broken/absent --help on the resolved file is "
            "not reported as a HELP FAILS finding. Never exempts the existence "
            "half of the check. Default: <repo-root>/team/config/"
            "doc-command-truth-help-exempt.txt."
        ),
    )
    parser.add_argument(
        "--operator-doc",
        metavar="PATH",
        action="append",
        default=[],
        help=(
            "Operator doc for the undocumented-script check (repeatable); a script named "
            "anywhere in one counts as documented. Default: HOW_TO.md, docs/OPERATIONS.md "
            "and docs/operator-commands.md under --repo-root."
        ),
    )
    parser.add_argument(
        "--internal-checks-doc",
        metavar="PATH",
        action="append",
        default=[],
        help=(
            "Document whose 'Internal checks' section lists internal scripts, for the "
            "undocumented-script check (repeatable). Default: docs/operator-commands.md and "
            "team/TESTING.md under --repo-root."
        ),
    )
    parser.add_argument(
        "--scripts-dir",
        metavar="PATH",
        help=(
            "Scripts tree the undocumented-script check walks (its top level plus cm/, "
            "cleanup/, migrate/ and migrations/). Default: <repo-root>/team/scripts."
        ),
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Print each citation as it is resolved.")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    """Run the doc command-truth lint: command citations, config-key parity, dashboard parity, undocumented scripts.

    Exit codes: 0 no findings from any of the four checks; 1 one or more
    findings (quality class; printed); 2 the scan set is empty, or a usage
    error.
    """
    args = _parse_args(argv)

    if args.repo_root:
        repo_root = Path(args.repo_root).resolve()
    else:
        script_dir = Path(__file__).resolve().parent  # team/scripts/
        repo_root = script_dir.parent.parent  # repo root

    team_dir = repo_root / "team"
    docs_root = Path(args.docs_root).resolve() if args.docs_root else repo_root / "docs"
    docs_manifest = (
        Path(args.docs_manifest).resolve() if args.docs_manifest else team_dir / "config" / "docs-manifest.txt"
    )
    extra_scan_manifest = (
        Path(args.extra_scan_manifest).resolve()
        if args.extra_scan_manifest
        else team_dir / "config" / "doc-truth-extra-scan-docs.txt"
    )
    operator_verbs_manifest = (
        Path(args.operator_verbs_manifest).resolve()
        if args.operator_verbs_manifest
        else None  # let the reader fall back to its own default
    )
    help_exempt_list_path = (
        Path(args.help_exempt_list).resolve()
        if args.help_exempt_list
        else team_dir / "config" / "doc-command-truth-help-exempt.txt"
    )
    help_exempt_paths = _load_help_exempt_list(help_exempt_list_path, args.verbose)

    scan_set = _load_scan_set(repo_root, docs_root, docs_manifest, args.doc, args.verbose, extra_scan_manifest)
    if not scan_set:
        print(
            "lint_doc_command_truth: ERROR: scan set is empty — nothing to check. "
            "Pass --doc explicitly or verify --docs-manifest/--docs-root.",
            file=sys.stderr,
        )
        return 2

    verbs = _load_verb_manifest(team_dir, operator_verbs_manifest, args.verbose)

    all_citations: list[Citation] = []
    for doc_path, doc_display in scan_set:
        all_citations.extend(_extract_citations(doc_path, doc_display))

    if args.verbose:
        for c in all_citations:
            print(f"  citation: {c.doc_display}:{c.line} [{c.kind}/{c.source}] {c.candidate}", flush=True)

    # Dedup identical (doc, line, candidate, kind) tuples — the same span
    # matched twice is one citation, not two.
    seen: set[tuple[str, int, str, str]] = set()
    unique_citations: list[Citation] = []
    for c in all_citations:
        key = (c.doc_display, c.line, c.candidate, c.kind)
        if key in seen:
            continue
        seen.add(key)
        unique_citations.append(c)

    lint_invocation = "python3 team/scripts/lint_doc_command_truth.py"

    findings: list[str] = []
    help_cache: dict[str, HelpResult] = {}
    citations_scanned = 0
    help_exempt_count = 0
    help_sandbox_root = _make_help_sandbox_root()

    for c in unique_citations:
        if c.kind == "verb":
            resolution = _resolve_verb(c.candidate, verbs, team_dir)
            if resolution is None:
                # Not a declared verb at all — never a citation this lint
                # tracks (a bare English word that happens to match the
                # classifier's shape but names nothing real or planned).
                continue
        else:
            resolution = _resolve_script(c.candidate, repo_root, team_dir)

        if resolution.resolved_path is None and not resolution.tried:
            # Planned verb: nothing to check, not a finding.
            continue

        citations_scanned += 1

        if resolution.resolved_path is None:
            findings.append(_format_missing_finding(c, resolution.tried, lint_invocation))
            continue

        resolved_key = str(resolution.resolved_path)
        if resolved_key not in help_cache:
            help_cache[resolved_key] = _check_help(
                resolution.resolved_path, args.help_timeout, help_sandbox_root
            )
        help_result = help_cache[resolved_key]
        if not help_result.ok:
            # The exempt-list lookup runs strictly after resolution succeeded
            # above — a listed path that no longer exists never reaches this
            # branch at all, it already produced a MISSING COMMAND finding.
            # The exemption therefore never blunts the existence half of the
            # check, only this --help half, and only for a citation whose
            # resolved file is on the list. See module docstring,
            # "Process-internal citations (HELP FAILS exempt list)".
            try:
                resolved_rel = str(resolution.resolved_path.relative_to(repo_root))
            except ValueError:
                resolved_rel = resolved_key
            if resolved_rel in help_exempt_paths:
                help_exempt_count += 1
                if args.verbose:
                    print(
                        f"  help-exempt: {c.doc_display}:{c.line} -> {resolved_rel}",
                        flush=True,
                    )
                continue
            findings.append(
                _format_help_finding(c, resolution.resolved_path, help_result.detail, lint_invocation)
            )

    # Every probe's process group is already gone (_run_probe); the sandbox
    # directory, and anything a probe wrote into it, goes with the run.
    shutil.rmtree(help_sandbox_root, ignore_errors=True)

    print(
        f"lint_doc_command_truth: scanned {citations_scanned} citation(s) across "
        f"{len(scan_set)} document(s); {len(findings)} finding(s) "
        f"({help_exempt_count} process-internal citation(s) exempted from the --help check).",
        flush=True,
    )
    for f in findings:
        print("", flush=True)
        print(f, flush=True)

    # ------------------------------------------------------------------
    # Config-key parity — reconcile every cited configuration key against
    # the accepted-key source for the file it is cited under. That source
    # is TWO registries, not one: config_loader.sh's Key Registry speaks
    # for kanban.cfg (a single framework-wide config file), and
    # project_paths.sh's own Key Registry speaks for project.cfg (a
    # distinct, per-project key set — see project_paths.sh's own registry
    # header for why the two are not the same file's business). Both are
    # parsed with the same structural reader and merged into one lookup
    # table before reconciliation, since the two key sets do not collide
    # by section name (the one section genuinely shared by both files,
    # [release], is declared once in config_loader.sh's registry — see
    # that registry's own header note).
    # ------------------------------------------------------------------
    config_loader_path = (
        Path(args.config_loader).resolve()
        if args.config_loader
        else team_dir / "scripts" / "lib" / "config_loader.sh"
    )
    project_key_registry_path = (
        Path(args.project_key_registry).resolve()
        if args.project_key_registry
        else team_dir / "scripts" / "lib" / "project_paths.sh"
    )

    def _registry_display(p: Path) -> str:
        try:
            return str(p.relative_to(repo_root))
        except ValueError:
            return str(p)

    config_loader_display = _registry_display(config_loader_path)
    project_key_registry_display = _registry_display(project_key_registry_path)

    registry: dict[tuple[str, str], RegistryKey] = {}
    for registry_path, registry_label, registry_display in (
        (config_loader_path, "config_loader.sh", config_loader_display),
        (project_key_registry_path, "project_paths.sh", project_key_registry_display),
    ):
        try:
            registry.update(_parse_config_loader_registry(registry_path, registry_display))
        except (OSError, RegistryFormatError) as exc:
            # Missing or unparseable Key Registry degrades the same way a
            # missing/malformed operator-verbs-manifest does above
            # (_load_verb_manifest): warn and continue with whatever the
            # OTHER registry contributed, rather than aborting the whole
            # lint. A repo/fixture tree that legitimately has no
            # config_loader.sh or project_paths.sh at this path (an
            # existence-check-only test fixture, a non-kanban-self
            # project) still gets a clean run of the other two checks.
            print(
                f"lint_doc_command_truth: WARNING: cannot parse {registry_label} Key Registry "
                f"({registry_path}): {exc}; key-parity check will find 0 keys from this source.",
                file=sys.stderr,
            )

    key_scan_set = _resolve_key_parity_scan_set(args, repo_root, docs_root, docs_manifest, scan_set)
    key_findings, key_citations_scanned, _matched_key_citations = run_key_parity_check(
        key_scan_set,
        registry,
        lint_invocation,
        config_loader_display=config_loader_display,
        project_key_registry_display=project_key_registry_display,
    )
    undocumented_key_count = sum(1 for f in key_findings if f.startswith("UNDOCUMENTED CONFIG KEY"))
    print(
        f"lint_doc_command_truth: key-parity: scanned {key_citations_scanned} key citation(s) "
        f"across {len(key_scan_set)} document(s) against {len(registry)} registry key(s); "
        f"{len(key_findings)} finding(s) ({undocumented_key_count} loader key(s) no scanned "
        "document mentions).",
        flush=True,
    )
    for f in key_findings:
        print("", flush=True)
        print(f, flush=True)

    # ------------------------------------------------------------------
    # Dashboard window/pane parity — reconcile every cited window/pane
    # claim against team/config/dashboard_windows.census, in both
    # directions.
    # ------------------------------------------------------------------
    census_path = Path(args.census).resolve() if args.census else None
    try:
        windows = _load_dashboard_windows(team_dir, census_path)
    except (ImportError, FileNotFoundError, ValueError) as exc:
        # Same degrade-and-warn discipline as the registry load above: a
        # tree with no team/config/dashboard_windows.census (or no
        # window_census.py to read it) at this path gets a clean run of
        # the other two checks rather than an aborted lint.
        print(
            f"lint_doc_command_truth: WARNING: cannot load dashboard window census: {exc}; "
            "pane-parity check will find 0 census windows.",
            file=sys.stderr,
        )
        windows = []

    pane_scan_set = _resolve_pane_parity_scan_set(args, repo_root, docs_root, docs_manifest, scan_set)
    pane_findings, pane_citations_scanned = run_pane_parity_check(pane_scan_set, windows, lint_invocation)
    undocumented_window_count = sum(
        1 for f in pane_findings if f.startswith("UNDOCUMENTED DASHBOARD WINDOW")
    )
    unique_window_ids = len({w.tmux_index for w in windows})
    print(
        f"lint_doc_command_truth: pane-parity: scanned {pane_citations_scanned} window/pane "
        f"citation(s) across {len(pane_scan_set)} document(s) against {unique_window_ids} "
        f"census window id(s) ({len(windows)} row(s)); {len(pane_findings)} finding(s) "
        f"({undocumented_window_count} census window(s) no scanned document describes).",
        flush=True,
    )
    for f in pane_findings:
        print("", flush=True)
        print(f, flush=True)

    # ------------------------------------------------------------------
    # Undocumented installed scripts — the reverse direction: every script
    # install.sh ships must be named by an operator doc or listed in the
    # Internal checks section. A tree with none of the operator docs (a
    # fixture tree built for the other three checks) is not checked, with a
    # stderr note, the same degrade-and-warn discipline as the registry and
    # census loads above.
    # ------------------------------------------------------------------
    scripts_dir = Path(args.scripts_dir).resolve() if args.scripts_dir else team_dir / "scripts"
    operator_docs = _resolve_doc_list(repo_root, args.operator_doc, _OPERATOR_DOCS)
    internal_checks_docs = _resolve_doc_list(repo_root, args.internal_checks_doc, _INTERNAL_CHECKS_DOCS)
    script_findings: list[str] = []
    if not operator_docs and not internal_checks_docs:
        print(
            f"lint_doc_command_truth: WARNING: none of {', '.join(_OPERATOR_DOCS)} exists under "
            f"{repo_root}; undocumented-script check skipped.",
            file=sys.stderr,
        )
    else:
        undocumented, scripts_checked, where_to_list = run_undocumented_script_check(
            scripts_dir, operator_docs, internal_checks_docs
        )
        operator_doc_displays = [display for _path, display in operator_docs]
        script_findings = [
            _format_undocumented_script_finding(entry, operator_doc_displays, where_to_list, lint_invocation)
            for entry in undocumented
        ]
        print(
            f"lint_doc_command_truth: undocumented-scripts: checked {scripts_checked} installed "
            f"script(s) against {len(operator_docs)} operator doc(s) and the Internal checks "
            f"section; {len(script_findings)} finding(s).",
            flush=True,
        )
        for f in script_findings:
            print("", flush=True)
            print(f, flush=True)

    if not findings and not key_findings and not pane_findings and not script_findings:
        print("lint_doc_command_truth: OK — 0 findings.", flush=True)
        return 0

    if findings:
        error = "doc_command_truth.findings"
        message = (
            f"{len(findings)} document-cited command(s) either do not exist in this tree or "
            "fail their --help invocation; see the findings printed above"
        )
        remedy = f"fix each cited document or script named above, then re-run: {lint_invocation}"
        # CONVERT: a stale or false doc-command citation carries zero
        # runtime-failure risk — see the module docstring's "CENSUS CLASS"
        # note. Warn, report, continue; never abort a calling runner.
        # CENSUS-ANCHOR: doc-command-truth-01
        try:
            from pgai_agent_kanban.lib.error_contract import emit_error_contract

            emit_error_contract(error, message, remedy)
        except ImportError:
            print(f"error:   {error}\nmessage: {message}\nremedy:  {remedy}", file=sys.stderr)

    if key_findings:
        error = "doc_command_truth.key_parity_findings"
        message = (
            f"{len(key_findings)} document-cited configuration key(s) disagree with "
            f"{config_loader_display}'s or {project_key_registry_display}'s Key Registry "
            "(unknown key, default mismatch, or a registry key no document mentions); see "
            "the findings printed above"
        )
        remedy = f"fix each cited document or the Key Registry, then re-run: {lint_invocation}"
        # CONVERT: a stale or false config-key claim carries zero runtime-
        # failure risk — see the module docstring's "Config-key parity"
        # section. Warn, report, continue; never abort a calling runner.
        # CENSUS-ANCHOR: doc-key-truth-01
        try:
            from pgai_agent_kanban.lib.error_contract import emit_error_contract

            emit_error_contract(error, message, remedy)
        except ImportError:
            print(f"error:   {error}\nmessage: {message}\nremedy:  {remedy}", file=sys.stderr)

    if pane_findings:
        error = "doc_command_truth.pane_parity_findings"
        message = (
            f"{len(pane_findings)} document-cited dashboard window/pane claim(s) disagree "
            "with team/config/dashboard_windows.census (unknown window, name mismatch, "
            "unknown pane, or a census window no document describes); see the findings "
            "printed above"
        )
        remedy = f"fix each cited document or the census, then re-run: {lint_invocation}"
        # CONVERT: a stale or false dashboard window/pane claim carries
        # zero runtime-failure risk — see the module docstring's "Dashboard
        # window/pane parity" section. Warn, report, continue; never abort
        # a calling runner.
        # CENSUS-ANCHOR: doc-pane-truth-01
        try:
            from pgai_agent_kanban.lib.error_contract import emit_error_contract

            emit_error_contract(error, message, remedy)
        except ImportError:
            print(f"error:   {error}\nmessage: {message}\nremedy:  {remedy}", file=sys.stderr)

    if script_findings:
        error = "doc_command_truth.undocumented_scripts"
        message = (
            f"{len(script_findings)} script(s) the install ships are named in no operator doc and "
            "listed in no Internal checks section; see the findings printed above"
        )
        remedy = f"add each named script to docs/operator-commands.md's Internal checks section, then re-run: {lint_invocation}"
        # CONVERT: an undocumented script carries zero runtime-failure risk
        # — see the module docstring's "Undocumented installed scripts"
        # section. Warn, report, continue; never abort a calling runner.
        try:
            from pgai_agent_kanban.lib.error_contract import emit_error_contract

            emit_error_contract(error, message, remedy)
        except ImportError:
            print(f"error:   {error}\nmessage: {message}\nremedy:  {remedy}", file=sys.stderr)

    return 1


if __name__ == "__main__":
    sys.exit(main())
