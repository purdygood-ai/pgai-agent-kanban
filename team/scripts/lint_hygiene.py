#!/usr/bin/env python3
"""
lint_hygiene.py
================
Single hygiene-lint entry point. Owns a registry of named hygiene classes,
each of which scans the tree for one category of quality concern and
returns per-instance findings — every finding names its class, its file,
its line, and a runnable remedy. This module is the consolidation point
new hygiene classes register into; it carries four classes today.

Registered classes
-------------------
  provenance  -- flags bug, priority, task and requirement IDs and
                 versions cited as history (a version cited as history is
                 label ``version-history``: the ``since vN.N``, ``added in
                 vN``, ``(vN.N.N)`` and ``vN.N.N+`` shapes); wording is not
                 checked. This reproduces every documented exception of
                 the lint it consolidates (``lint_comment_provenance.py``),
                 with two deliberate behavior changes described below.

  version-named-test  -- a test module or test function named for the
                 release that produced it (``test_v2_16_0_fix.py``,
                 ``def test_v2_16_0_config_migration():``) rather than for
                 the behavior it exercises. Scoped to the test-tree surface
                 only. Targets the naming PATTERN, not the mere presence of
                 a version-shaped character sequence: the version token is
                 a finding only when it sits immediately after the
                 mandatory ``test_`` prefix (the classic "named after the
                 release" shape); a version-shaped token appearing later in
                 the name -- because the test's own subject is a version
                 string, e.g. ``test_compares_v2_16_0_against_v2_15_4`` --
                 is left alone. A second, independent narrowing catches the
                 case a ``test_``-prefixed version token would otherwise
                 misclassify: even a prefix match is not flagged when the
                 SAME version, in its dotted prose form, reappears in the
                 surrounding text (the function's own body for a
                 function-name match; the whole file for a module-name
                 match) -- e.g.
                 ``test_v1_0_0_entry_carries_ki_1_0_0_1_with_tmux_symptom``,
                 whose body's own regexes and assertions repeat "v1.0.0",
                 is a changelog-writer test genuinely about that version,
                 not a release-provenance label. Two labels:
                 ``version-named-test-file`` (the module's own filename)
                 and ``version-named-test-function`` (a ``def test_...``
                 line).

  comment-drift  -- a comment or docstring that states a checkable
                 argument/parameter count the adjacent ``def`` signature
                 contradicts (e.g. ``# Takes three arguments`` above
                 ``def add(a, b):``, or a same-shaped docstring as a
                 function's first statement, describing that function's
                 OWN signature). Deliberately narrow: this is the one shape
                 in the class that can be checked mechanically without
                 false-positiving on a comment that is merely general (a
                 comment that describes intent at a level the code does not
                 restate makes no checkable claim, so it is never a
                 candidate). Only single-line ``def`` signatures are
                 checked; a multi-line signature is not a candidate either
                 -- see the scan function's docstring for the full list of
                 drift shapes deliberately left out of scope.

  operator-literal  -- a literal that only makes sense on one operator's
                 machine, in a file a public release would publish. Two
                 labels: ``home-path`` (``/home/`` followed by a real
                 account name) and ``ssh-host-alias`` (an scp-style remote
                 ``git@<host>:`` whose host is a private ``~/.ssh/config``
                 alias, such as a per-account forge alias). Neutral forms
                 are never findings:
                 ``$HOME/...`` and ``~/...``; an angle-bracket or variable
                 segment (``/home/<user>/``, ``/home/$USER/``); the
                 placeholder account names already used across the tree
                 (``you``, ``user``, ``youruser``, ``USERNAME``, ``op`` --
                 compared ignoring case and ``-``/``_``, so ``your-user``
                 counts too); ``git@github.com:``; and the reserved example
                 domains (``example.com``/``.net``/``.org`` and their
                 subdomains, and any host under the ``.example``,
                 ``.test``, ``.invalid`` or ``.localhost`` names). One
                 finding per line per label. Scope is its own surface, the
                 tracked-file surface below, not the directory surfaces the
                 other classes read.

``lint_comment_provenance.py`` -- the lint this module's ``provenance``
class consolidates -- and its test module are retired; this module is now
wired into both gated runners (``run-unit-tests.sh``, ``run-integration-
tests.sh``) via ``gated_runner_merge_findings_file``, never through the
coarse ``gated_runner_run_quality_lint`` wrapper (that wrapper only records
a finding on a non-zero exit, and this module always exits 0 -- see "Exit
codes" below -- so wrapping it there would silently drop every finding).

What changed in the move
-------------------------
1. **Suppression is a sidecar, not an in-file marker.** The lint being
   consolidated reads its opt-out from a comment written into the scanned
   file itself (``# provenance-allowlist: <justification>``), which agents
   have repeatedly satisfied by pasting the marker into a docstring —
   turning a suppression into shipped prose, the exact litter this class
   exists to remove. This module never reads an allowlist directive from a
   scanned file. Suppression is by a line-level allowlist sidecar: a JSON
   array, checked into the tree outside the scanned surface (default
   ``team/scripts/hygiene-lint-allowlist.json``, override with
   ``--sidecar PATH``), one entry per suppressed line. Each entry carries:

     - ``file``     -- the finding's own ``file`` value (see "Path identity"
                       below), so entries are portable across checkouts.
     - ``class``     -- the hygiene class name the entry suppresses
                        (``provenance`` today).
     - ``content_hash`` -- a fingerprint of the flagged line's own text
                        (see "Content-hash anchoring" below), NOT its line
                        number.
     - ``line_hint`` -- the line number at authoring time, carried for a
                        human reader's convenience only; it plays no part
                        in whether the entry matches.
     - ``justification`` -- required, non-empty. An entry with a blank or
                        missing justification refuses that sidecar load;
                        the malformed entry is reported as its own finding
                        (never silently honoured or silently dropped) and
                        no suppression from the broken file is applied to
                        that run -- see "Exit codes" below, this does not
                        stop the lint.

   A whole-file exemption cannot be expressed: every entry names one
   line's worth of suppression.

   **Content-hash anchoring, and its drift behavior.** The entry key is a
   fingerprint of the flagged line's stripped text, not its line number.
   This project has observed line-number pins go stale on nearly every
   release that happens to touch the pinned file — an edit anywhere above
   the pinned line shifts it, and the pin silently stops matching (or
   worse, starts matching the wrong line). A content hash carries no
   position information, so inserting or removing lines elsewhere in the
   file never invalidates an entry. The trade-off is the opposite
   direction: editing the SUPPRESSED line's own text — even a whitespace
   reflow beyond leading/trailing trim — changes the hash, the entry stops
   matching, and the finding reappears. That reappearance is intentional:
   a changed line is a line that has not been re-reviewed, and a stale
   suppression silently surviving a content change is a worse failure mode
   than a one-time false reappearance an agent re-suppresses (with a fresh
   ``--add-suppression`` call) after confirming the new text still
   deserves it.

2. **Version-shaped matchers are anchored against mid-identifier
   embedding.** The lint being consolidated bounds each version-shaped
   pattern with a bare ``\\b`` word boundary, which treats ``.`` as a
   non-word character — so a version-shaped substring embedded right after
   a dot, e.g. a dotted config filename or package name, still satisfies
   the boundary and is misread as a citation. Every
   version-shaped pattern in this class instead uses ``_TOKEN_LEFT_BOUNDARY``
   (a negative lookbehind excluding both word characters AND ``.``
   immediately to the left), so a version-shaped token is only a candidate
   when it starts at a genuine token boundary — whitespace, start of
   string, or punctuation other than ``.``. A real, standalone citation
   (preceded by a space, a quote, an open-paren, or nothing) is unaffected.

3. **String-literal CONTENT on the test-tree surface is deliberately still
   NOT read -- a decision, not an oversight.** The test-tree surface's
   ordinary-string-literal-VALUE exemption (see "Exception 5" under the
   Test-tree surface entry below) exempts a plain string assignment,
   including one whose own text happens to read as a full comment line a
   fixture writes verbatim into a generated file (``PROBE = ("# kanban.cfg
   -- BUG-9999 fixture\\n")``) -- the same citation is reported when it sits
   in a real ``#`` comment, and silent when it sits in a string a module
   WRITES as one (`team/tests/fixtures/dashboard_render_fixture.py`,
   `git_network_stub.py`, `installed_root.py`, `log_stub.py`, and
   `two_project.py` all carry this shape). Two concrete problems were found
   by actually building and running the obvious fix -- flag a string token
   whenever one of its own physical lines, stripped of the opening
   quote/prefix, starts with ``#`` -- before deciding against shipping it:

     a. **The heuristic is unreliable on this project's own Python version.**
        CPython 3.12's PEP 701 tokenizer represents an f-string (with or
        without an interpolated ``{...}``) as ``FSTRING_START``/
        ``FSTRING_MIDDLE``/``FSTRING_END`` tokens, never a single
        ``tokenize.STRING`` token a comment-shape check can inspect. Three
        of the five files above (`git_network_stub.py`, `log_stub.py`,
        `two_project.py`'s own ``f"..."`` line) build their generated
        header as an f-string, so a STRING-token-only heuristic silently
        misses exactly the shape it was built to catch, on the interpreter
        this project actually runs -- a heuristic that only sometimes
        detects its own target shape is worse than one that documents its
        own boundary.
     b. **The false-positive volume swamps the signal.** Measured against
        the full ``team/tests/`` tree, flagging every comment-shaped
        ``tokenize.STRING`` (docstring position or not) surfaced 63 new
        findings, the overwhelming majority of them a DIFFERENT, already-
        sanctioned shape: synthetic bug/priority-report markdown content
        built by operator-command and API-contract tests
        (``test_operation_contracts.py``, ``test_operator_commands.py``,
        ``test_board.py``, ``test_release_notes_content_described.py``, and
        a dozen more), whose leading ``#`` is a Markdown H1 heading
        (``# BUG-9001\\n\\nSomething broke.\\n``), not a comment in the
        target file's own language. Nothing short of tracing which
        function a literal is eventually passed to (or what file extension
        it is written into) tells the two apart at the point a per-line
        regex runs, which is a materially larger project than this lint
        takes on.

   Given (a) a real gap in the very shape the fix targets, and (b) a
   false-positive rate this size on a class whose whole point is a low
   enough noise floor that a finding is worth reading, the lint's
   design decision is to leave string-literal CONTENT out of scope and record it
   here rather than ship a heuristic that is simultaneously unreliable and
   noisy. The five files' nine pre-existing citations are unaffected by
   this decision either way (a separate bug-backlog item already tracks
   them by name for a follow-on ticket to reword or allowlist); this
   decision only settles
   whether the LINT can see a future citation of the same shape, and for
   now the answer is written down as no rather than left for the next
   reader to rediscover by probing.

Path identity
-------------
Every finding's ``file`` is the scanned path relative to ``--repo-root``
(inferred from this script's own location when not given, matching a real
checkout), falling back to the absolute path string when the file sits
outside that root (this only happens when a caller points ``--code-dirs``
outside a checkout entirely, e.g. a test fixture that also passes its own
``--repo-root``). This is the same string sidecar entries key on, so a
checked-in sidecar's ``file`` values are ordinary repo-relative paths,
portable across worktrees and clones.

Scan-surface exclusion is relative to the directory a caller actually
named (``--code-dirs`` / ``--test-tree-dirs`` / ``--prose-dirs`` /
``--docs-dirs`` / ``--config-dirs`` / ``--text-dirs``), never to an
ancestor of it. A file's path ABOVE the point the caller told this
script to scan is never inspected for an exclusion segment. This matters
for testability: the framework's own temp-root convention nests every
pytest ``tmp_path`` fixture under a directory literally named ``tests``,
and a caller-supplied ``--code-dirs`` pointed at a `tmp_path` fixture must
not be silently excluded merely because an ancestor directory outside the
scan target happens to be named ``tests``.

Seven scan surfaces
-------------------
  Code surface (``--code-dirs``, default: ``team/scripts/``,
    ``team/scripts/lib/``, ``team/scripts/cm/``,
    ``team/scripts/dashboard/lib/``, ``team/pgai_agent_kanban/``, plus
    top-level ``*.sh`` at the repo root) — enforces the full provenance
    pattern set. ``.py``/``.sh`` files, plus any executable regular file
    with no suffix whose first line is a shebang (see "Extensionless
    shebang executables" below) — this is what makes
    ``team/scripts/wake/git-guard/git`` a candidate: it must keep its
    exact extensionless name to be found on ``PATH`` ahead of the real
    ``git`` binary, so the selector widens instead. A directory literally
    named ``tests`` or ``test``, nested anywhere beneath a code-surface
    directory, is never scanned under code-surface rules and never
    silently dropped either: it is routed whole to the test-tree surface
    below (see "Scan-surface routing"), the same as any directory named on
    ``--test-tree-dirs`` directly, so a test tree that happens to live
    under the code surface is read under test-tree rules rather than going
    unread.

  Test-tree surface (``--test-tree-dirs``, default: ``team/tests/``, plus
    every ``tests``/``test`` directory the code surface routes to it) —
    every directory this surface scans, however it got here, gets two
    additional exemptions the code surface does not: a
    bug/task/priority-shaped VALUE used as test data (an ordinary string
    literal, not a comment, a genuine docstring, an ``assert`` statement's
    own message clause, or a ``pytest.fail(...)`` call's argument) is
    exempt (Python's own tokenizer decides comment/docstring/assert-
    message/fail-argument position vs. ordinary-value position — an
    assertion failure message and a ``pytest.fail`` string are shipped
    prose a person reads at failure time, exactly like a comment, so they
    are held to the same bar even though each is an ordinary string
    literal). This class does NOT additionally read a VALUE-position
    string's own content for a comment shape (e.g. a fixture assembling a
    generated file's ``#``-prefixed header as a plain string) — see the
    module docstring's "What changed in the move" item 3 for the two
    concrete, measured reasons (an unreliable signal on this project's own
    Python tokenizer, and a false-positive volume that swamped the signal)
    a wider matcher was tried and deliberately not shipped. A
    ``version-history`` match in a comment or docstring is a test value,
    not a finding, when the same version also appears in one of the
    file's own value-position string literals -- a docstring naming the
    tag or Active RC its own fixture builds describes test data. The
    unshipped-requirements-path / bare-release-slug
    patterns are not enforced here at all (a ``vX.Y.Z-slug`` is also the
    literal shape of a requirements-discovery test's own fixture
    filename). ``.py``/``.sh`` files, plus any executable regular file
    with no suffix whose first line is a shebang, excluding ``conftest*``,
    ``__pycache__/``, and ``artifacts/`` (archived measurement snapshots,
    not live test modules) — the same exclusions apply to a routed
    directory, relative to that directory itself. ``team/tests/fixtures/``
    is scanned rather than excluded wholesale: it holds reusable,
    long-lived test-fidelity helper modules (``git_network_stub.py``,
    ``dashboard_render_fixture.py``, and the rest of
    ``team/tests/fixtures/__init__.py``'s own module list), not generated
    fixture data. The handful of entries under it that genuinely ARE
    synthetic per-lint corpora — a deliberate good/bad-example fixture
    authored for a DIFFERENT lint's own unit tests, the same role
    ``test_lint_hygiene_corpus.py`` plays for this lint — are excluded by
    their own name instead: the individual ``fixture_*`` files at
    ``team/tests/fixtures/`` top level go in ``_FILE_WHITELIST`` and the
    synthetic-corpus subdirectories (``container_engine_free/``,
    ``engine_size_dupe/``, ``import_completeness/``,
    ``hands_free_demo_green/``) go in this surface's own exclude-segment
    set, so widening the surface never pulls in a fixture whose entire
    purpose is to violate some other lint's rule on request.

  Prose surface (``--prose-dirs``, default: ``release-notes/``) —
    ``.md`` files. Release notes are a shipped surface, so the same
    pattern set applies, with one exemption: BUG-ID, PRIORITY-ID,
    bare-release-slug, and version-history references are the sanctioned,
    intended content of a release note ("Bug Fixes", "Bugs Skipped",
    "Other Changes", the version a change landed in) and are not flagged
    there. Task IDs, version-bundle references, and
    Requirement-Goal citations remain enforced in prose exactly as in code.

  Docs surface (``--docs-dirs``, default: ``docs/``) — ``.md`` files under
    the project's standalone documentation set (operator guides, the
    coding-standards reference, the blocking-site census). This is
    ordinary shipped prose a reader holds as authoritative project
    documentation, not a change record whose sanctioned content is a list
    of the tickets it closed -- so, unlike the prose surface below, the
    full provenance pattern set applies here with NO surface-specific
    exemption (the same enforcement as the config surface, for the same
    reason: a shipped comment/sentence here is read by an operator exactly
    like a shipped code comment). This is deliberately a DIFFERENT surface
    from the release-notes prose surface rather than an addition to it,
    because the two document types have opposite sanctioned content for a
    BUG-ID/PRIORITY-ID/bare-release-slug: release notes exist to record
    which tickets a release closed, so citing one there is the document
    working as designed; a citation in this surface's documentation is the
    same unwanted process-provenance litter a code comment would carry (a
    gate's own census row naming the ticket that prompted the gate, say),
    and is enforced accordingly. A genuine worked USAGE EXAMPLE inside
    this surface (an "e.g."-marked interface illustration, a documented
    format-with-example) is unaffected -- Exceptions 1-3 (``_is_exception``)
    exempt the ID- and version-shaped patterns on every surface
    identically. Every subtree of the surface is
    enforced alike; the only by-name relief is the one every surface
    shares -- a published changelog (``CHANGELOG.md``,
    ``ICD_CHANGELOG.md``) is exempt from version-history alone, since a
    version cited as history is its whole content (see
    ``_CHANGELOG_FILENAMES``).

  Config surface (``--config-dirs``, default: ``team/config/``) —
    declarative configuration a script or a census-style reconciliation
    check reads at runtime — a verb manifest, a window census, a palette —
    rather than prose a person reads top to bottom. A shipped comment in
    one of these files is read by an operator the same as a shipped code
    comment, so the full provenance pattern set applies, with no
    surface-specific exemption. ``.manifest``/``.census``/``.txt`` files:
    both declarative-config shapes this lint reads without special-casing
    either one's filename, plus the plain-text lists (allowlists, the docs
    manifest, a palette) whose comments an operator reads the same way.

  Shipped-text surface (``--text-dirs``, default: ``team/roles/``,
    ``subagents/``, ``team/templates/``, ``team/pm-agent/``, ``docker/``,
    plus -- only while the flag is left at its default -- the top-level
    ``*.md`` files at the repo root except ``CHANGELOG.md``, and every
    ``*_example`` / ``*.example`` configuration template in the git work
    tree, tracked or untracked-but-not-ignored, outside ``team/tests/`` and
    ``release-notes/``; a root that is not a git work-tree top contributes
    only its top-level templates) — directories whose
    every file is shipped text an operator or an agent reads: role files,
    the agent prompts installed as subagents, install/project/task
    templates, the PM agent's own code and docs, and the container build
    files. Every non-binary file is collected whatever its suffix (a
    ``Dockerfile``, a ``crontab.example``, a ``queue-files.list``), with
    the code surface's exclude segments (``tests``, ``test``,
    ``fixtures``, ``__pycache__``) and ``conftest*`` skipped; a file with a
    NUL byte in its first 8 KiB is binary and skipped. The full provenance
    pattern set applies, with no surface-specific exemption.

  Tracked-file surface (``git ls-files`` at ``--repo-root``; read only by
    the ``operator-literal`` class) — every file git tracks, of any type,
    except ``release-notes/``, the root ``CHANGELOG.md``, and ``team/tests/``
    other than ``team/tests/artifacts/`` (archived snapshots shipped as-is,
    so they stay in scope). Binary files (a NUL byte), symlinks, files
    missing from the work tree, and ``_FILE_WHITELIST`` names are skipped.
    The ``--*-dirs`` flags do not narrow this surface. It is empty unless
    ``--repo-root`` is itself the top of a git work tree -- an installed
    root or a bare temp directory has nothing "tracked", and an ancestor
    repository is never borrowed (``--verbose`` says so when it happens).

Extensionless shebang executables
----------------------------------
The code and test-tree surfaces collect a third file shape beyond their two
named suffixes: an executable regular file whose name carries no suffix at
all and whose first line begins with ``#!``. This is deliberately narrower
than "every file with no suffix" — a non-executable, non-shebang data file
under a scan directory is never a candidate, so an unrelated extensionless
file (a lockfile stem, a checksum, a directory marker) stays unscanned. The
same exclude-segment and ``_FILE_WHITELIST`` rules that govern the suffix
scan apply here too, relative to the same scan-directory root. The prose,
docs and config surfaces do not get this widening: an extensionless
executable under ``release-notes/`` or ``team/config/`` is not the shape
either surface exists to catch. The shipped-text surface needs no such
widening -- it already collects every non-binary file whatever its name.

Scan-surface routing
---------------------
A code-surface directory is walked top-down looking for a nested directory
named ``tests`` or ``test``; the first one found on a given path is routed
whole to the test-tree surface (added as its own test-tree scan target, so
its own nested ``conftest*``/``__pycache__/``/``artifacts/`` exclusions
apply from there rather than being walked further as code), and the routed
directory's own contents are never additionally collected under the code
surface. Walking stops descending at a directory named ``fixtures`` or
``__pycache__`` the same as the code surface's own exclusion, so synthetic
fixture data is never mistaken for a real test tree. This is general by
construction — a third test tree added anywhere under a code-surface
directory is picked up automatically, with no lint change required.

A file matching the module-level ``_FILE_WHITELIST`` (this module's own
test module and false-positive/true-positive corpus, the small set of
tools that legitimately read/route real bug IDs, and a handful of
synthetic per-lint fixture corpora) is excluded from every surface. This
module's own basename is deliberately NOT on that list -- see the
"Self-scanning" note directly below ``_FILE_WHITELIST``'s definition for
why, and how its own documented worked-example tokens are retained
without a whole-file exemption.

Suppression sidecar management
--------------------------------
  python3 team/scripts/lint_hygiene.py --add-suppression FILE LINE CLASS \\
      "<justification>"

Reads the current text of line LINE in FILE, fingerprints it, and appends a
new entry to the sidecar (creating it if absent). This is the supported way
to author a sidecar entry — the hash is derived from FILE's actual current
content, never typed by hand.

Findings output
-----------------
Every finding is printed as part of a three-part remedy block (cause,
numbered fix commands, the one-line rule) and, when ``--findings-file
PATH`` is given, written as a JSON array of finding objects to PATH (each
finding carries at minimum: ``lint``, ``class``, ``severity``, ``file``,
``line``, ``label``, ``token``, ``cause``, ``remedy`` — the shape
``team/scripts/lib/gated_runner_lint.sh``'s ``gated_runner_merge_findings_file``
already knows how to ingest).

Exit codes
----------
  0   The lint ran to completion, with or without findings. This class
      never blocks: findings are reported, never a reason to fail a build.
      A malformed or unreadable allowlist sidecar (bad JSON, wrong shape,
      a blank or missing justification) is one such finding, not an
      exception to this rule -- it is reported by name (file, offending
      entry, reason) and no suppression from the broken file is honoured
      for that run, but the run itself still completes and exits 0.
  2   A bad CLI argument (an unrecognized --classes value, or a malformed
      --add-suppression invocation) is the only thing that produces a
      non-zero exit from this script.

Usage
-----
  python3 team/scripts/lint_hygiene.py [--code-dirs PATH [PATH ...]]
                                       [--test-tree-dirs PATH [PATH ...]]
                                       [--prose-dirs PATH [PATH ...]]
                                       [--docs-dirs PATH [PATH ...]]
                                       [--config-dirs PATH [PATH ...]]
                                       [--text-dirs PATH [PATH ...]]
                                       [--classes NAME [NAME ...]]
                                       [--sidecar PATH]
                                       [--repo-root PATH]
                                       [--findings-file PATH]
                                       [--verbose]

  python3 team/scripts/lint_hygiene.py --add-suppression FILE LINE CLASS \\
      JUSTIFICATION [--sidecar PATH] [--repo-root PATH]
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import textwrap
import tokenize
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, NamedTuple, Optional

# ---------------------------------------------------------------------------
# Provenance token patterns (class "provenance")
# ---------------------------------------------------------------------------

_BUG_ID: re.Pattern[str] = re.compile(r"\bBUG-\d+\b", re.IGNORECASE)

# Matches both spellings a person actually writes: the canonical task-folder
# name ``<ROLE>-YYYYMMDD-NNN`` (SOP-core.md's "Task IDs use the format"), and
# the abbreviated ``<ROLE>-NNN`` shorthand agents write in prose when the
# folder name itself is not the point (e.g. "ticket CODER-009's field"). The
# short branch's digit run is bounded to 1-4 digits -- wide enough for every
# short-form citation observed in the tree (three-digit sequences like
# ``CODER-009``, and the occasional bare ``CODER-1``) while staying well
# short of the eight-digit date segment the long form requires, so the two
# branches can never be confused for each other. The short branch's trailing
# ``(?!-\d)`` refuses to match the FIRST segment of a longer, unrelated
# multi-segment identifier (e.g. a fixture name shaped like
# ``CODER-001-002`` in a prefix-matching test) -- without it, greedy
# backtracking would let ``\d{1,4}`` claim just the first segment and still
# satisfy the trailing word boundary at the hyphen that follows it.
_TASK_ID: re.Pattern[str] = re.compile(
    r"\b(?:CODER|WRITER|TESTER|CM|PM|PO)-(?:\d{8}-\d+|\d{1,4}(?!-\d))\b",
    re.IGNORECASE,
)

_PRIORITY_ID: re.Pattern[str] = re.compile(r"\bPRIORITY-\d+\b", re.IGNORECASE)

# Left boundary for a version-shaped token, e.g. "v2.16.0" or the
# "requirements/" that precedes one. A bare `\b` treats "." as a non-word
# character, so it is satisfied immediately after a dot -- letting a
# version-shaped substring embedded in a dotted identifier
# ("config.v2.16.0.yaml") pass as if it were a standalone citation. This
# lookbehind additionally excludes an immediately preceding "." (and, via
# each pattern's own extra exclusion where relevant, "/"), so only a
# genuine token boundary -- whitespace, start-of-string, or punctuation
# other than "." -- may precede the match. See the module docstring's
# "What changed in the move", item 2, for the proof-by-test this exists to
# satisfy.
_TOKEN_LEFT_BOUNDARY = r"(?<![\w.])"

_VERSION_BUNDLE: re.Pattern[str] = re.compile(
    _TOKEN_LEFT_BOUNDARY + r"v\d+\.\d+\.\d+-(?:priority|bug)-bundle\b",
    re.IGNORECASE,
)

_VERSION_GOAL: re.Pattern[str] = re.compile(
    _TOKEN_LEFT_BOUNDARY + r"v\d+\.\d+\.\d+\b.{0,60}?\bGoal\s+\d+\b",
    re.IGNORECASE,
)

_UNSHIPPED_REQUIREMENTS_PATH: re.Pattern[str] = re.compile(
    _TOKEN_LEFT_BOUNDARY + r"requirements/v\d+\.\d+\.\d+[\w.-]*",
    re.IGNORECASE,
)

# Bare release slug: same internal-release citation as
# _UNSHIPPED_REQUIREMENTS_PATH with the "requirements/" path left implicit.
# The left boundary additionally excludes "/" (a real, followable path
# segment is not a bare citation) alongside the "." exclusion every
# version-shaped pattern here carries. The slug body is an atomic group so
# a failed trailing lookahead cannot make the engine backtrack to a
# shorter, bogus slug match.
_BARE_RELEASE_SLUG: re.Pattern[str] = re.compile(
    r"(?<![\w./])v\d+\.\d+\.\d+-(?>[a-z][a-z0-9-]*)\b"
    r"(?!\.md)(?!\s+(?:match(?:es)?|pattern)\b)",
    re.IGNORECASE,
)

# A bare release version cited as the history of the line it sits on rather
# than as a value the line is about. Four shapes, matched case-insensitively
# with liberal whitespace:
#   - "since vN.N" (patch optional)           -- when a behaviour began
#   - "added in vN" (minor/patch optional)    -- which release introduced it
#   - "(vN.N.N)" as a whole parenthetical      -- a release tag on a sentence
#   - "vN.N.N+"                                -- "this release or later"
# Each tolerates a Markdown backtick around the version. Shapes that only
# look like these are left alone: a "--since" option and its value (the
# lookbehind refuses a "-" before "since"); a parenthesised QUOTED value or
# a call argument ("index(" + quoted version + ")" -- no word character may
# precede the "(", and quotes are not accepted inside it); and version
# arithmetic ("vN.N.N+1" -- the "+" may not be followed by a digit). A
# placeholder version ("vX.Y.Z") has no digits and never matches; an
# "e.g."-marked illustration is exempt through Exceptions 1-3 like every
# other ID- or version-shaped pattern. The trailing-plus branch carries the same mid-identifier
# anchor as every other version-shaped pattern here.
_VERSION_AS_HISTORY: re.Pattern[str] = re.compile(
    r"(?<![\w-])since\s+`?v\d+\.\d+(?:\.\d+)*\b"
    r"|\badded\s+in\s+`?v\d+(?:\.\d+)*\b"
    r"|(?<!\w)\(\s*`?v\d+\.\d+\.\d+`?\s*\)"
    r"|" + _TOKEN_LEFT_BOUNDARY + r"v\d+\.\d+\.\d+\+(?!\d)",
    re.IGNORECASE,
)

# The version number inside a version-history match, used to decide whether
# a test file also carries that version as test data (see
# ``_version_used_as_test_value``).
_VERSION_NUMBER: re.Pattern[str] = re.compile(r"v\d+(?:\.\d+)*", re.IGNORECASE)

_PROVENANCE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (_BUG_ID, "BUG-ID"),
    (_TASK_ID, "task-ID"),
    (_PRIORITY_ID, "PRIORITY-ID"),
    (_VERSION_BUNDLE, "version-bundle"),
    (_VERSION_GOAL, "requirement-Goal"),
    (_UNSHIPPED_REQUIREMENTS_PATH, "unshipped-requirements-path"),
    (_BARE_RELEASE_SLUG, "bare-release-slug"),
    (_VERSION_AS_HISTORY, "version-history"),
]

# Exception 4 / 6: BUG-ID, PRIORITY-ID, bare-release-slug and version-history
# are the sanctioned, intended content of the prose surface (release notes
# exist to record which items a release addressed, which slug shipped, and
# which version a change landed in).
_PROSE_SURFACE_EXEMPT_LABELS: frozenset[str] = frozenset(
    {"BUG-ID", "PRIORITY-ID", "bare-release-slug", "version-history"}
)

# An append-only published changelog (the rendered root CHANGELOG.md, the
# operator API's ICD_CHANGELOG.md) records which version each change landed
# in -- a version cited as history is its whole content. A file with one of
# these basenames is exempt from version-history on whichever surface
# collects it; every other provenance pattern still applies there.
_CHANGELOG_FILENAMES: frozenset[str] = frozenset({"CHANGELOG.md", "ICD_CHANGELOG.md"})
_CHANGELOG_EXEMPT_LABELS: frozenset[str] = frozenset({"version-history"})

# Exception 7: unshipped-requirements-path and bare-release-slug are never
# enforced on the sanctioned test-tree surface, regardless of comment/
# docstring position -- a "vX.Y.Z-slug" is also the literal shape of a
# requirements-discovery test's own fixture filename.
_TEST_TREE_EXEMPT_LABELS: frozenset[str] = frozenset(
    {"unshipped-requirements-path", "bare-release-slug"}
)

# ---------------------------------------------------------------------------
# Version-named-test token patterns (class "version-named-test")
# ---------------------------------------------------------------------------
#
# A version-shaped token in a snake_case identifier, e.g. "v2_16_0" (Python
# identifiers cannot carry the dotted "v2.16.0" form a version normally
# takes, so the underscore-joined shape is what a test module or function
# name actually carries). The token must stand as its own underscore-
# flanked segment -- "(?:^|_)" and "(?:_|$)" -- so it is only a candidate
# when it reads as a distinct word in the identifier, not an accidental
# substring inside a longer run of digits.
_VERSION_NAME_TOKEN: re.Pattern[str] = re.compile(r"(?:^|_)v\d+_\d+_\d+(?:_|$)")

# The provenance shape: the version token sits immediately after the
# mandatory "test_" prefix, reading as if the release itself were the
# test's name (`test_v2_16_0_fix`, `test_v2_16_0`). A version token
# appearing anywhere else in the identifier is left alone -- see the class
# docstring in the module header for the worked "subject under test"
# counter-example.
_VERSION_NAME_PREFIX: re.Pattern[str] = re.compile(r"^test_v\d+_\d+_\d+(?:_|$)")

# A `def test_...(` line, used to find function names to test against
# _VERSION_NAME_PREFIX. Deliberately does not require the signature to
# close on the same line -- the name is captured before the first "(".
_TEST_FUNC_DEF: re.Pattern[str] = re.compile(r"^\s*def\s+(test_\w+)\s*\(")

# ---------------------------------------------------------------------------
# Comment-drift patterns (class "comment-drift")
# ---------------------------------------------------------------------------
#
# The one drift shape this class checks: a comment or docstring stating a
# checkable argument/parameter COUNT that the adjacent `def` signature
# contradicts. Deliberately narrow -- see the module header's class
# description and `_scan_comment_drift_lines`'s docstring for the shapes
# left out of scope and why.

_ARG_COUNT_WORDS: "dict[str, int]" = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

_ARG_COUNT_CLAIM: re.Pattern[str] = re.compile(
    r"\b(?P<num>one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+"
    r"(?P<noun>arguments?|parameters?)\b",
    re.IGNORECASE,
)

# A single-line `def` signature: name, params entirely between one pair of
# parens, ending in ":" at end of line. A multi-line signature never
# matches this -- the check is skipped for it rather than guessed at (a
# narrower class that catches less beats a broader one that misparses a
# continuation line as the whole parameter list).
_DEF_LINE: re.Pattern[str] = re.compile(
    r"^(?P<indent>[ \t]*)(?:async\s+)?def\s+\w+\s*\((?P<params>.*)\)\s*"
    r"(?:->\s*[^:]+)?:\s*$"
)

# A single-line triple-quoted docstring, e.g. `    """Takes three args."""`.
# Matches only when the whole docstring is on one physical line -- a
# multi-line docstring is not a candidate, same rationale as _DEF_LINE.
_SINGLE_LINE_DOCSTRING: re.Pattern[str] = re.compile(
    r'^[ \t]*(?:"""(?P<body1>.*)"""|\'\'\'(?P<body2>.*)\'\'\')\s*$'
)

# ---------------------------------------------------------------------------
# Operator-literal patterns (class "operator-literal")
# ---------------------------------------------------------------------------
#
# A home-directory literal naming one person's account: "/home/" followed by
# an account-name-shaped segment. The lookbehind refuses a "/home/" that is
# itself the tail of a longer word or relative path ("example.com/home/...",
# "./home/...", "~/home/..."), none of which is an absolute home directory.
# Anything that is not an account-name character right after "/home/" -- an
# angle-bracket placeholder ("/home/<user>/"), a shell variable
# ("/home/$USER/"), a glob ("/home/*/") -- never matches at all.
_HOME_PATH: re.Pattern[str] = re.compile(
    r"(?<![\w.~-])/home/(?P<name>[A-Za-z0-9_][A-Za-z0-9._-]*)"
)

# Home-directory names already used across the tree as neutral placeholders.
# Compared after lower-casing and dropping "-"/"_", so "USERNAME",
# "your-user" and "your_user" all read as the placeholders they are. A
# realistic personal name ("alice", an operator's own login) is never here.
_PLACEHOLDER_HOME_NAMES: frozenset[str] = frozenset(
    {"you", "user", "youruser", "username", "op"}
)

# An scp-style git remote, "git@<host>:<path>". The host is captured so the
# public forge and the reserved example domains can be told apart from a
# private alias defined in one operator's ~/.ssh/config.
_SSH_GIT_REMOTE: re.Pattern[str] = re.compile(
    r"(?<![\w.-])git@(?P<host>[A-Za-z0-9][A-Za-z0-9.-]*):"
)

# Hosts that are neutral in shipped text: the public forge every example in
# the tree points at, and the reserved example domains (RFC 2606, RFC 6761).
_NEUTRAL_SSH_HOSTS: frozenset[str] = frozenset(
    {"github.com", "example.com", "example.net", "example.org"}
)
_NEUTRAL_SSH_TOP_LABELS: frozenset[str] = frozenset(
    {"example", "test", "invalid", "localhost"}
)

# ---------------------------------------------------------------------------
# Allowed-exception patterns (Exceptions 1-3: apply to every ID- and
# version-shaped pattern on every surface)
# ---------------------------------------------------------------------------

_EXAMPLE_MARKERS: re.Pattern[str] = re.compile(
    r"(?:e\.g\.|e\.g,|example:|Examples?\b|BUG-NNNN|PRIORITY-NNNN|TASK-ID)",
    re.IGNORECASE,
)

_SKIP_ANNOTATION: re.Pattern[str] = re.compile(
    r"""(?:pytest\.mark\.skip|skip\s*\(|SKIP\s*:|\bxfail\b)""",
    re.IGNORECASE,
)

_EXTERNAL_REF: re.Pattern[str] = re.compile(
    r"""(?:CVE-\d|RFC\s*\d|GH#\d|upstream|external)""",
    re.IGNORECASE,
)

_EXCEPTION_PATTERNS: list[re.Pattern[str]] = [
    _EXAMPLE_MARKERS,
    _SKIP_ANNOTATION,
    _EXTERNAL_REF,
]


def _is_exception(line: str) -> bool:
    """Return True if *line* matches any allowed-exception pattern
    (Exceptions 1-3). The caller applies the result to every provenance
    pattern."""
    return any(pat.search(line) for pat in _EXCEPTION_PATTERNS)


# ---------------------------------------------------------------------------
# File-level whitelist: files whose content is inherently about internal
# IDs, excluded from every surface.
# ---------------------------------------------------------------------------

_FILE_WHITELIST: frozenset[str] = frozenset({
    "promote_bundled_items.py",    # CM tool that reads/routes BUG-NNNN IDs
    "changelog_writer.py",         # reads bug IDs from ledger to build CHANGELOG
    # This file is deliberately NOT on this list -- see the "Self-scanning"
    # note below _FILE_WHITELIST's definition.
    "test_lint_hygiene.py",        # this module's own test module.
    "test_lint_hygiene_corpus.py",  # this module's false-positive/true-
                                    # positive corpus: synthetic fixture
                                    # strings for the provenance,
                                    # version-named-test and comment-drift
                                    # classes.
    # The entries below are team/tests/fixtures/ top-level modules whose
    # entire content is a deliberate good/bad-example corpus authored for a
    # DIFFERENT lint's own unit tests (the same role test_lint_hygiene_corpus.py
    # plays for this one) -- not shared test-fidelity helpers, so widening the
    # test-tree surface to team/tests/fixtures/ must not turn them into
    # candidates here.
    "fixture_abs_path_prefix_controlled.py",       # lint_test_anti_patterns.py corpus
    "fixture_abs_path_substring_assertion.py",     # lint_test_anti_patterns.py corpus
    "fixture_api_drift_script.sh",                 # lint_api_parity.py corpus
    "fixture_bare_mktemp_anti_pattern.py",         # lint_test_anti_patterns.py corpus
    "fixture_bare_mktemp_d_runtime_script.sh",     # lint_test_anti_patterns.py corpus
    "fixture_bare_tmp_fake_mkdir.py",              # lint_test_anti_patterns.py corpus
    "fixture_bare_tmp_redirect.sh",                # lint_test_anti_patterns.py corpus
    "fixture_direct_env_read_py.py",               # lint_test_anti_patterns.py corpus
    "fixture_golden_comparison_explicit_env.py",   # lint_test_anti_patterns.py corpus
    "fixture_golden_comparison_inherited_env.py",  # lint_test_anti_patterns.py corpus
    "fixture_hardcoded_tmp_and_mkdtemp.py",        # lint_test_anti_patterns.py corpus
    "fixture_inherited_env_without_golden_comparison.py",  # lint_test_anti_patterns.py corpus
    "fixture_missing_env_bootstrap_sh.py",         # lint_test_anti_patterns.py corpus
    "fixture_no_help_script.sh",                   # lint_help_presence.py corpus
})

# Self-scanning: this module's own basename is deliberately absent from
# _FILE_WHITELIST above, so this file is a candidate on the code surface
# exactly like any other file under team/scripts/. The class this module
# enforces is the one class that could not previously see a violation
# inside its own shipped prose -- a whole-file exemption meant a real
# ledger citation here was neither reported nor deliberately retained with
# a justification, just unseen. Its handful of documented worked-example
# tokens (the ones this module's own docstring uses to explain a pattern,
# e.g. a placeholder-shaped bug ID) are kept exactly as-is by naming them
# individually in the sidecar allowlist, the same mechanism any other file
# uses to retain a citation on purpose; a real citation anywhere else in
# this file is reported like any other finding.

# ---------------------------------------------------------------------------
# Scan-surface collection: exclusion is relative to the directory a caller
# actually named, never to an ancestor of it (see module docstring).
# ---------------------------------------------------------------------------

# Segments excluded anywhere below a CODE-surface scan directory.
_CODE_EXCLUDE_SEGMENTS: frozenset[str] = frozenset(
    {"tests", "test", "fixtures", "__pycache__"}
)

# Segments excluded anywhere below a TEST-TREE-surface scan directory.
# "tests"/"test" are deliberately absent here -- the test-tree surface IS a
# tests directory; only cache/archive noise and named synthetic-data
# subtrees are excluded. "fixtures" is deliberately ALSO absent: unlike the
# code surface, team/tests/fixtures/ holds reusable test-fidelity helper
# modules the test-tree surface must read, not generated data, so the
# directory is scanned rather than excluded wholesale. The four
# names below are the specific team/tests/fixtures/ subdirectories that
# genuinely ARE synthetic per-lint corpora -- each one entirely made of
# deliberate good/bad-example fixtures authored for a DIFFERENT lint's own
# unit tests -- excluded by their own name instead of reinstating the
# blanket "fixtures" exclusion; the sibling top-level "fixture_*" files get
# the same treatment via _FILE_WHITELIST above.
_TEST_TREE_EXCLUDE_SEGMENTS: frozenset[str] = frozenset(
    {
        "__pycache__",
        "artifacts",
        "container_engine_free",   # lint_container_engine_free.py corpus
        "engine_size_dupe",        # lint_engine_size_dupe.py corpus
        "import_completeness",     # lint_python_import_completeness.py corpus
        "hands_free_demo_green",   # hands-free demo's always-green pytest fixture
    }
)

# A directory bearing one of these names, found while walking a code-surface
# directory, is a real test tree rather than shipped code -- see module
# docstring's "Scan-surface routing".
_ROUTABLE_TEST_TREE_SEGMENTS: frozenset[str] = frozenset({"tests", "test"})

# Directories a code-surface walk never descends into while looking for a
# routable test tree: synthetic fixture data and interpreter cache, neither
# of which is a real test tree even when something inside happens to be
# named "tests".
_ROUTING_WALK_PRUNE_SEGMENTS: frozenset[str] = frozenset({"fixtures", "__pycache__"})


def _discover_routed_test_tree_dirs(code_dir: Path) -> list[Path]:
    """Find every directory literally named "tests" or "test" nested
    anywhere beneath *code_dir* (a code-surface scan directory), so the
    caller can route each one to the test-tree surface instead of the code
    surface's own exclusion rule (`_CODE_EXCLUDE_SEGMENTS`) silently
    dropping it. *code_dir* itself is never a candidate -- only a
    directory strictly beneath it. A routable directory nested inside
    another already-discovered one is not reported separately: the outer
    directory is routed as a whole, and walking does not descend into it
    looking for a second one. Walking also never descends into a
    "fixtures" or "__pycache__" directory, matching the code surface's own
    exclusion -- synthetic fixture data is not a real test tree even when
    something inside it happens to be named "tests"."""
    if not code_dir.is_dir():
        return []

    discovered: list[Path] = []
    for root, dirnames, _files in os.walk(code_dir):
        root_path = Path(root)
        if root_path != code_dir and root_path.name in _ROUTABLE_TEST_TREE_SEGMENTS:
            discovered.append(root_path)
            dirnames[:] = []  # routed as a unit; the test-tree surface scans it
            continue
        dirnames[:] = [d for d in dirnames if d not in _ROUTING_WALK_PRUNE_SEGMENTS]

    return sorted(discovered)


def _relpath_parts_excluded(parts: tuple[str, ...], exclude_segments: frozenset[str]) -> bool:
    """Return True if any directory component of *parts* (a file's path
    relative to the scan directory it was collected under, split on "/")
    is an excluded segment, or if the file's own basename starts with
    "conftest" (the fixture-harness convention every surface excludes)."""
    if not parts:
        return False
    if any(part in exclude_segments for part in parts[:-1]):
        return True
    basename = parts[-1]
    return basename.startswith("conftest")


def _to_relpath(path: Path, repo_root: Path) -> str:
    """Return *path* as a POSIX-style string relative to *repo_root*, or
    the absolute path (also POSIX-style) when *path* does not sit under
    *repo_root*. This is the canonical identity string used for both a
    finding's ``file`` field and sidecar-entry matching."""
    try:
        return str(path.resolve().relative_to(repo_root)).replace("\\", "/")
    except ValueError:
        return str(path.resolve()).replace("\\", "/")


def _has_shebang(path: Path) -> bool:
    """Return True if *path*'s first line begins with a shebang (``#!``).
    Reads defensively -- a binary file, a vanished path, or a permissions
    error reads as "no shebang" rather than raising, since this predicate
    runs mid directory-walk and must not abort the scan over one bad file."""
    try:
        with path.open("rb") as fh:
            first_line = fh.readline(256)
    except OSError:
        return False
    return first_line.startswith(b"#!")


def _is_executable_file(path: Path) -> bool:
    """Return True if *path* carries an executable bit for someone (owner,
    group, or other) -- the same test the shell's own PATH lookup applies,
    so a data file that merely starts with "#!" in its first two bytes by
    coincidence, but was never marked runnable, is not swept in."""
    try:
        return bool(path.stat().st_mode & 0o111)
    except OSError:
        return False


@dataclass(frozen=True)
class _ScanTarget:
    """One directory a scan surface collects files from."""

    directory: Path
    test_tree_surface: bool


def _is_binary_file(path: Path) -> bool:
    """Return True if *path*'s first 8 KiB carry a NUL byte -- the same
    binary test the tracked-file surface applies. An unreadable file reads
    as binary, so a directory walk skips it rather than aborting."""
    try:
        with path.open("rb") as fh:
            return b"\0" in fh.read(8192)
    except OSError:
        return True


def _collect_files(
    targets: list[_ScanTarget],
    suffixes: tuple[str, ...],
    *,
    include_extensionless_shebang: bool = False,
    any_text_file: bool = False,
) -> list[tuple[Path, bool]]:
    """Collect scannable files from *targets*, applying exclusions relative
    to each target's own directory (never to an ancestor of it).

    Beyond the named *suffixes*, when *include_extensionless_shebang* is
    True this also collects any executable regular file with NO suffix at
    all whose first line is a shebang (see module docstring's
    "Extensionless shebang executables") -- deliberately narrower than
    "every extensionless file": a non-executable, non-shebang data file
    stays unscanned. The code and test-tree surfaces opt in; the prose,
    docs and config surfaces do not.

    When *any_text_file* is True, *suffixes* is ignored and every regular
    file that is not binary (no NUL byte in its first 8 KiB) is collected,
    whatever its suffix -- the shipped-text surface's selector, for
    directories whose every file is shipped text (a Dockerfile, a crontab
    template, a role file).

    Returns a list of (absolute path, test_tree_surface) pairs, deduplicated
    by resolved path (the first target to claim a path wins, matching the
    directory-list-order precedence a caller's explicit ordering implies).
    """
    collected: list[tuple[Path, bool]] = []
    seen: set[Path] = set()

    for target in targets:
        scan_dir = target.directory
        if not scan_dir.is_dir():
            continue
        exclude_segments = (
            _TEST_TREE_EXCLUDE_SEGMENTS if target.test_tree_surface else _CODE_EXCLUDE_SEGMENTS
        )

        def _accept(p: Path) -> bool:
            resolved = p.resolve()
            if resolved in seen or not p.is_file():
                return False
            if p.name in _FILE_WHITELIST:
                return False
            relparts = tuple(resolved.relative_to(scan_dir.resolve()).parts)
            if _relpath_parts_excluded(relparts, exclude_segments):
                return False
            return True

        if any_text_file:
            for p in sorted(scan_dir.rglob("*")):
                if not _accept(p) or _is_binary_file(p):
                    continue
                resolved = p.resolve()
                seen.add(resolved)
                collected.append((resolved, target.test_tree_surface))
            continue

        for suffix in suffixes:
            for p in sorted(scan_dir.rglob(f"*{suffix}")):
                if not _accept(p):
                    continue
                resolved = p.resolve()
                seen.add(resolved)
                collected.append((resolved, target.test_tree_surface))

        if include_extensionless_shebang:
            for p in sorted(scan_dir.rglob("*")):
                if p.suffix:
                    continue
                if not _accept(p):
                    continue
                if not _is_executable_file(p) or not _has_shebang(p):
                    continue
                resolved = p.resolve()
                seen.add(resolved)
                collected.append((resolved, target.test_tree_surface))

    return collected


def _collect_repo_root_scripts(repo_root: Path) -> list[tuple[Path, bool]]:
    """Collect top-level ``*.sh`` files directly at *repo_root* (today, just
    ``install.sh``) -- non-recursive, same exclusions as the directory scan."""
    if not repo_root.is_dir():
        return []
    out: list[tuple[Path, bool]] = []
    for p in sorted(repo_root.glob("*.sh")):
        if not p.is_file() or p.name in _FILE_WHITELIST:
            continue
        out.append((p.resolve(), False))
    return out


def _collect_repo_root_text_files(repo_root: Path) -> list[tuple[Path, bool]]:
    """Collect the shipped text files sitting directly at *repo_root* --
    top-level ``*.md`` (README.md, ARCHITECTURE.md and the rest) except the
    rendered ``CHANGELOG.md``, and top-level ``*_example`` / ``*.example``
    configuration templates (kanban.cfg_example, secrets_example and the
    rest). Non-recursive, so it works on any root, git or not;
    ``_FILE_WHITELIST`` names and binary files are skipped. Example files
    below the root are ``_collect_example_files``'s."""
    if not repo_root.is_dir():
        return []
    out: list[tuple[Path, bool]] = []
    seen: set[Path] = set()
    for pattern in ("*.md", "*_example", "*.example"):
        for p in sorted(repo_root.glob(pattern)):
            if not p.is_file() or p.name in _FILE_WHITELIST or p.name == "CHANGELOG.md":
                continue
            resolved = p.resolve()
            if resolved in seen or _is_binary_file(p):
                continue
            seen.add(resolved)
            out.append((resolved, False))
    return out


# Top-level directories whose example files are not shipped text: the test
# tree (fixtures, not templates) and the change record.
_EXAMPLE_FILE_EXCLUDED_PREFIXES: tuple[str, ...] = ("team/tests/", "release-notes/")


def _is_example_file_name(name: str) -> bool:
    """True for a configuration-template name: ``*_example`` or ``*.example``."""
    return name.endswith("_example") or name.endswith(".example")


def _collect_example_files(repo_root: Path, verbose: bool = False) -> list[tuple[Path, bool]]:
    """Collect every ``*_example`` / ``*.example`` file in the git work tree
    at *repo_root*, at any depth -- team/config/project.cfg_example,
    team/scripts/pseudocron.env.example and the rest.

    The listing is ``git ls-files --cached --others --exclude-standard``:
    tracked files plus untracked ones git does not ignore, so an example
    file is checked before its first commit as well as after. Skipped:
    anything under ``team/tests/`` or ``release-notes/``, a path with one of
    the code surface's exclude segments (``tests``, ``test``, ``fixtures``,
    ``__pycache__``) or a ``conftest*`` name, ``_FILE_WHITELIST`` names,
    symlinks, files missing from the work tree, and binary files. A root
    that is not itself the top of a git work tree (an installed root, a bare
    temp directory) yields nothing here; its root-level example files are
    still read by ``_collect_repo_root_text_files``."""
    out: list[tuple[Path, bool]] = []
    root = repo_root.resolve()
    for filepath in _git_tracked_files(
        repo_root, verbose=verbose, include_untracked=True, surface="shipped-text examples"
    ):
        if not _is_example_file_name(filepath.name) or filepath.name in _FILE_WHITELIST:
            continue
        relpath = _to_relpath(filepath, root)
        if relpath.startswith(_EXAMPLE_FILE_EXCLUDED_PREFIXES):
            continue
        if _relpath_parts_excluded(tuple(relpath.split("/")), _CODE_EXCLUDE_SEGMENTS):
            continue
        if filepath.is_symlink() or not filepath.is_file() or _is_binary_file(filepath):
            continue
        out.append((filepath.resolve(), False))
    return out


# ---------------------------------------------------------------------------
# Tokenizer-based comment/docstring position test (Exception 5's surface
# distinction: an ordinary string-literal VALUE is exempt on the test-tree
# surface; only a genuine comment or docstring is a candidate there).
# ---------------------------------------------------------------------------

_SKIP_TOKEN_TYPES = (
    tokenize.NL,
    tokenize.COMMENT,
    tokenize.INDENT,
    tokenize.DEDENT,
    tokenize.NEWLINE,
)

_TO_END_OF_LINE = 1 << 30


def _classify_python_line_surfaces(
    text: str,
) -> "dict[int, list[tuple[int, int]]] | None":
    """Return {1-indexed line -> [(start_col, end_col), ...]} for every span
    in *text* the test-tree surface treats as shipped prose a human reads at
    failure/runtime rather than ordinary test data: a ``#`` COMMENT, a
    genuine DOCSTRING, an ``assert`` statement's own message clause (the
    expression after its top-level comma), and a ``pytest.fail(...)`` call's
    argument list. Returns ``None`` on a tokenize failure -- distinct from
    an empty dict (tokenizes cleanly, nothing to flag) -- so a caller can
    fall back to "no exemption" rather than silently exempting an
    unparseable file.

    The assert-message and ``pytest.fail`` spans exist because an
    assertion's own failure message -- and the string a ``pytest.fail(...)``
    call raises with -- are prose a person reads at failure time exactly
    like a log line on the code surface, not test data (a fixture filename,
    a constructed ledger row) the surrounding ordinary-string-literal
    exemption exists to protect. Only the clause AFTER the assert's
    top-level comma is a candidate; the condition expression before it
    (where a bug-shaped value legitimately appears as literal test data,
    e.g. ``assert bug_id == "BUG-0001"``) is left exempt exactly as before.

    This function deliberately does NOT add a span for an ordinary string
    literal's own content, however comment-shaped that content looks (a
    fixture building a generated file's ``#``-prefixed header line as a
    plain string) -- see the module docstring's "What changed in the move"
    item 3 for the two concrete, measured reasons this was tried and
    reverted rather than shipped.
    """
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, SyntaxError, IndentationError, ValueError):
        return None

    spans: dict[int, list[tuple[int, int]]] = {}

    def add_span(start_row: int, start_col: int, end_row: int, end_col: int) -> None:
        if start_row == end_row:
            spans.setdefault(start_row, []).append((start_col, end_col))
            return
        spans.setdefault(start_row, []).append((start_col, _TO_END_OF_LINE))
        for row in range(start_row + 1, end_row):
            spans.setdefault(row, []).append((0, _TO_END_OF_LINE))
        spans.setdefault(end_row, []).append((0, end_col))

    n = len(tokens)

    for idx, tok in enumerate(tokens):
        if tok.type == tokenize.COMMENT:
            add_span(tok.start[0], tok.start[1], tok.end[0], tok.end[1])
            continue
        if tok.type != tokenize.STRING:
            continue

        j = idx - 1
        while j >= 0 and tokens[j].type in _SKIP_TOKEN_TYPES:
            j -= 1
        prev = tokens[j] if j >= 0 else None
        is_docstring_position = prev is None or (
            prev.type == tokenize.OP and prev.string == ":"
        )

        k = idx + 1
        while k < len(tokens) and tokens[k].type in (tokenize.NL, tokenize.COMMENT):
            k += 1
        nxt = tokens[k] if k < len(tokens) else None
        is_whole_statement = nxt is None or nxt.type == tokenize.NEWLINE

        if is_docstring_position and is_whole_statement:
            add_span(tok.start[0], tok.start[1], tok.end[0], tok.end[1])

    # Assert-message spans: everything after an `assert` statement's own
    # top-level comma, up to the statement's NEWLINE. `depth` tracks
    # unmatched brackets opened AFTER `assert` so a comma nested inside the
    # condition's own parens/brackets/braces is not mistaken for the
    # condition/message separator.
    for idx, tok in enumerate(tokens):
        if not (tok.type == tokenize.NAME and tok.string == "assert"):
            continue

        j = idx - 1
        while j >= 0 and tokens[j].type in (tokenize.NL, tokenize.COMMENT):
            j -= 1
        prev = tokens[j] if j >= 0 else None
        is_stmt_start = prev is None or prev.type in (
            tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT,
        ) or (prev.type == tokenize.OP and prev.string == ";")
        if not is_stmt_start:
            continue

        depth = 0
        comma_tok = None
        k = idx + 1
        while k < n:
            t = tokens[k]
            if t.type == tokenize.OP and t.string in "([{":
                depth += 1
            elif t.type == tokenize.OP and t.string in ")]}":
                depth -= 1
            elif t.type == tokenize.OP and t.string == "," and depth == 0:
                comma_tok = t
                break
            elif t.type == tokenize.NEWLINE:
                break
            k += 1
        if comma_tok is None:
            continue

        m = k + 1
        stmt_end = None
        while m < n:
            if tokens[m].type == tokenize.NEWLINE:
                stmt_end = tokens[m]
                break
            m += 1
        if stmt_end is None:
            continue

        add_span(comma_tok.end[0], comma_tok.end[1], stmt_end.start[0], stmt_end.start[1])

    # pytest.fail(...) argument spans: the whole parenthesized argument list
    # of a `pytest.fail(` call, matched by bracket depth so a nested call or
    # literal inside the arguments does not end the span early.
    for idx, tok in enumerate(tokens):
        if not (tok.type == tokenize.NAME and tok.string == "pytest"):
            continue
        j = idx + 1
        if j >= n or not (tokens[j].type == tokenize.OP and tokens[j].string == "."):
            continue
        j += 1
        if j >= n or not (tokens[j].type == tokenize.NAME and tokens[j].string == "fail"):
            continue
        j += 1
        if j >= n or not (tokens[j].type == tokenize.OP and tokens[j].string == "("):
            continue
        open_paren = tokens[j]

        depth = 1
        close_paren = None
        k = j + 1
        while k < n:
            t = tokens[k]
            if t.type == tokenize.OP and t.string in "([{":
                depth += 1
            elif t.type == tokenize.OP and t.string in ")]}":
                depth -= 1
                if depth == 0:
                    close_paren = t
                    break
            k += 1
        if close_paren is None:
            continue

        add_span(open_paren.end[0], open_paren.end[1], close_paren.start[0], close_paren.start[1])

    return spans


def _match_overlaps_flagged_span(
    match_start: int, match_end: int, line_spans: "list[tuple[int, int]]"
) -> bool:
    return any(match_start < end and start < match_end for start, end in line_spans)


# Token types carrying string-literal text: a plain STRING, and on Python
# 3.12+ the literal middle part of an f-string (absent on older versions).
_STRING_TEXT_TOKEN_TYPES: tuple[int, ...] = tuple(
    t for t in (tokenize.STRING, getattr(tokenize, "FSTRING_MIDDLE", None)) if t is not None
)


def _python_value_string_text(
    text: str, spans: "dict[int, list[tuple[int, int]]]"
) -> str:
    """Return the joined text of every string literal in *text* that sits
    in ordinary VALUE position -- not inside one of the comment, docstring,
    assert-message or ``pytest.fail`` spans *spans* holds (the
    ``_classify_python_line_surfaces`` result for the same text). Returns
    an empty string on a tokenize failure."""
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, SyntaxError, IndentationError, ValueError):
        return ""
    parts: list[str] = []
    for tok in tokens:
        if tok.type not in _STRING_TEXT_TOKEN_TYPES:
            continue
        row, col = tok.start
        if _match_overlaps_flagged_span(col, col + 1, spans.get(row, [])):
            continue
        parts.append(tok.string)
    return "\n".join(parts)


def _version_used_as_test_value(version: str, value_text: str) -> bool:
    """Return True if *version* (e.g. the digits-and-dots number a
    version-history match carries) appears as a standalone token in
    *value_text* -- the test file's own string-literal values. A test-tree
    docstring or comment that names the version its fixture builds (a tag
    the fixture creates, the Active RC it seeds) is describing test data,
    which the test-tree surface exempts, not citing release history."""
    pattern = re.compile(_TOKEN_LEFT_BOUNDARY + re.escape(version) + r"(?![\d])", re.IGNORECASE)
    return bool(pattern.search(value_text))


# ---------------------------------------------------------------------------
# Finding + core matcher
# ---------------------------------------------------------------------------


class _Finding(NamedTuple):
    """One hygiene finding, structured for both display and remedy-command
    derivation (docs/coding-standards.md "Gates prescribe, not just
    diagnose")."""

    cls: str
    path: Path
    lineno: int
    label: str
    token: str
    line_text: str


def _scan_lines(
    cls_name: str,
    path: Path,
    lines: list[str],
    prose_surface: bool = False,
    test_tree_surface: bool = False,
    exempt_labels: frozenset[str] = frozenset(),
) -> list[_Finding]:
    """Scan *lines* for provenance violations. The single detection
    rule-source for the provenance class: every invocation mode (whole-tree
    scan today; any future diff-scope mode) calls this function with the
    same pattern list and exception filters, so a rule change is enforced
    identically everywhere. *exempt_labels* names extra pattern labels the
    caller exempts for this one file (a changelog's version-history, see
    ``_CHANGELOG_FILENAMES``). Deliberately carries NO in-file opt-out check
    -- suppression is decided by the caller against the sidecar, after
    findings are collected (see ``_apply_sidecar_suppression``)."""
    findings: list[_Finding] = []

    line_spans: "dict[int, list[tuple[int, int]]] | None" = None
    if test_tree_surface:
        line_spans = _classify_python_line_surfaces("".join(lines))
    # The test file's own string-literal values, built on first need (a
    # version-history candidate on the test-tree surface) -- see
    # ``_version_used_as_test_value``.
    value_text: Optional[str] = None

    for idx, line in enumerate(lines):
        lineno = idx + 1
        stripped = line.strip()
        if not stripped:
            continue

        for pat, label in _PROVENANCE_PATTERNS:
            match = pat.search(line)
            if not match:
                continue

            if prose_surface and label in _PROSE_SURFACE_EXEMPT_LABELS:
                continue

            if test_tree_surface and label in _TEST_TREE_EXEMPT_LABELS:
                continue

            if label in exempt_labels:
                continue

            if test_tree_surface and line_spans is not None:
                flagged_here = line_spans.get(lineno, [])
                if not _match_overlaps_flagged_span(match.start(), match.end(), flagged_here):
                    continue
                if label == "version-history":
                    if value_text is None:
                        value_text = _python_value_string_text("".join(lines), line_spans)
                    version = _VERSION_NUMBER.search(match.group(0))
                    if version and _version_used_as_test_value(version.group(0), value_text):
                        continue

            if _is_exception(line):
                continue

            findings.append(_Finding(cls_name, path, lineno, label, match.group(0), stripped))
            break  # one finding per line is sufficient

    return findings


def _scan_file(
    cls_name: str,
    filepath: Path,
    prose_surface: bool = False,
    test_tree_surface: bool = False,
    verbose: bool = False,
) -> list[_Finding]:
    if verbose:
        print(f"  scanning: {filepath}", flush=True)
    try:
        text = filepath.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [_Finding(cls_name, filepath, 0, "READ-ERROR", str(exc), "")]

    lines = text.splitlines(keepends=True)
    exempt_labels = _CHANGELOG_EXEMPT_LABELS if filepath.name in _CHANGELOG_FILENAMES else frozenset()
    return _scan_lines(
        cls_name,
        filepath,
        lines,
        prose_surface=prose_surface,
        test_tree_surface=test_tree_surface,
        exempt_labels=exempt_labels,
    )


# ---------------------------------------------------------------------------
# Registered classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ScanInputs:
    code_files: list[Path]
    test_tree_files: list[Path]
    prose_files: list[Path]
    config_files: list[Path]
    repo_root: Path
    verbose: bool = False
    docs_files: list[Path] = field(default_factory=list)
    text_files: list[Path] = field(default_factory=list)


def _scan_provenance_class(inputs: "_ScanInputs") -> list[_Finding]:
    """The "provenance" hygiene class: reproduces lint_comment_provenance.py's
    detection behavior (see module docstring for the two deliberate changes)."""
    findings: list[_Finding] = []
    for filepath in inputs.code_files:
        findings.extend(
            _scan_file("provenance", filepath, prose_surface=False, test_tree_surface=False, verbose=inputs.verbose)
        )
    for filepath in inputs.test_tree_files:
        findings.extend(
            _scan_file("provenance", filepath, prose_surface=False, test_tree_surface=True, verbose=inputs.verbose)
        )
    for filepath in inputs.prose_files:
        findings.extend(
            _scan_file("provenance", filepath, prose_surface=True, test_tree_surface=False, verbose=inputs.verbose)
        )
    for filepath in inputs.docs_files:
        # The docs surface enforces the full pattern set with no
        # surface-specific exemption -- see the module docstring's "Docs
        # surface" entry for why this is a DIFFERENT surface from the
        # release-notes prose surface rather than an extension of it (a
        # release note's sanctioned content is the ticket it closed; a
        # citation in standalone documentation is not).
        findings.extend(
            _scan_file("provenance", filepath, prose_surface=False, test_tree_surface=False, verbose=inputs.verbose)
        )
    for filepath in inputs.text_files:
        # The shipped-text surface (role files, agent prompts, templates,
        # the PM agent, the container build files, root *.md and
        # *_example) enforces the full pattern set with no surface-specific
        # exemption -- see the module docstring's "Shipped-text surface".
        findings.extend(
            _scan_file("provenance", filepath, prose_surface=False, test_tree_surface=False, verbose=inputs.verbose)
        )
    for filepath in inputs.config_files:
        # The config surface enforces the full pattern set with no
        # surface-specific exemption -- see the module docstring's "Config
        # surface" entry for why a shipped config comment is held to the
        # same bar as a shipped code comment.
        findings.extend(
            _scan_file("provenance", filepath, prose_surface=False, test_tree_surface=False, verbose=inputs.verbose)
        )
    return findings


def _version_named_finding(filepath: Path, lineno: int, label: str, name: str, line_text: str) -> _Finding:
    """Build the ``_Finding`` for a version-named-test hit. *name* is the
    identifier (module stem or function name) the pattern matched against;
    the finding's ``token`` is the version-shaped slug extracted from it
    (falling back to the whole identifier if extraction somehow fails)."""
    m = _VERSION_NAME_TOKEN.search(name)
    token = m.group(0).strip("_") if m else name
    return _Finding("version-named-test", filepath, lineno, label, token, line_text)


def _version_token_dotted_form(token: str) -> str:
    """Convert an underscore-joined version token ("v1_0_0") to the dotted
    form ("v1.0.0") a test body would actually write when the version is
    literal data -- a regex pattern, an assertion string, a docstring
    sentence -- rather than an identifier segment."""
    digits = token[1:]  # strip leading "v"
    return "v" + ".".join(digits.split("_"))


def _version_token_reappears(token: str, haystack: str) -> bool:
    """Return True if *token* (in either its identifier form "v1_0_0" or
    its dotted prose form "v1.0.0") appears again in *haystack*. This is
    the mechanical test for "subject under test": a test genuinely about a
    specific version renders, parses, or asserts on that version's own
    dotted string somewhere in its own text, not just in its name."""
    haystack_lower = haystack.lower()
    return token.lower() in haystack_lower or _version_token_dotted_form(token).lower() in haystack_lower


def _function_body_text(lines: list[str], def_lineno_idx: int) -> str:
    """Return the text of the function body starting at *def_lineno_idx*
    (a 0-indexed line into *lines* that is itself a ``def`` line), up to
    (not including) the next line at the same or shallower indentation, or
    end of file.

    The signature itself may span multiple physical lines (one parameter
    per line is a common style for a long parameter list) -- open-paren
    depth is tracked from the ``def`` line forward so the body is only
    considered to start once the signature's own parens have balanced back
    to zero. Without this, a signature-continuation line ending in a
    closing paren at the ``def`` line's own indentation (``) -> None:``)
    would be misread as the end of the function, and the body -- where a
    version token would actually reappear as literal test data -- would
    never be scanned at all.
    """
    def_line = lines[def_lineno_idx]
    def_indent = len(def_line) - len(def_line.lstrip(" \t"))

    depth = def_line.count("(") - def_line.count(")")
    sig_end_idx = def_lineno_idx
    while depth > 0 and sig_end_idx + 1 < len(lines):
        sig_end_idx += 1
        depth += lines[sig_end_idx].count("(") - lines[sig_end_idx].count(")")

    body: list[str] = []
    for line in lines[sig_end_idx + 1:]:
        if line.strip() == "":
            body.append(line)
            continue
        if (len(line) - len(line.lstrip(" \t"))) <= def_indent:
            break
        body.append(line)
    return "\n".join(body)


def _scan_version_named_test_file(filepath: Path, verbose: bool = False) -> list[_Finding]:
    """Scan one test-tree file for the "version-named-test" class: a module
    name or a ``def test_...`` function name whose ``test_`` prefix is
    immediately followed by a version-shaped token (see
    ``_VERSION_NAME_PREFIX``'s comment for why that position, specifically,
    is the provenance signal).

    A prefix match is NOT flagged when the same version, in its dotted
    prose form, reappears in the surrounding text (the whole file for a
    module-name match; the function's own body for a function-name match)
    -- that reappearance is the mechanical signal that the version is the
    actual SUBJECT under test (a changelog-writer test asserting on the
    literal "v1.0.0" entry it renders, say), not a release-provenance
    label attached to an otherwise unrelated test. This is what lets a
    name like ``test_v1_0_0_entry_carries_ki_1_0_0_1_with_tmux_symptom`` --
    whose body's own regexes and assertions repeat "v1.0.0" -- pass clean.

    The flagged line's text is always the file's OWN physical text at the
    finding's line number -- for a file-name finding that is the file's
    actual line 1, never the synthesized module stem -- so
    ``--add-suppression FILE 1 version-named-test JUSTIFICATION``
    fingerprints the same text this scan used and the suppression sidecar
    works identically to every other class.
    """
    if verbose:
        print(f"  scanning: {filepath}", flush=True)

    try:
        text = filepath.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [_Finding("version-named-test", filepath, 0, "READ-ERROR", str(exc), "")]

    lines = text.splitlines()
    findings: list[_Finding] = []

    stem = filepath.stem
    if _VERSION_NAME_PREFIX.match(stem):
        token_match = _VERSION_NAME_TOKEN.search(stem)
        token = token_match.group(0).strip("_") if token_match else stem
        if not _version_token_reappears(token, text):
            first_line = lines[0].strip() if lines else ""
            findings.append(_version_named_finding(filepath, 1, "version-named-test-file", stem, first_line))

    if filepath.suffix != ".py":
        return findings

    for idx, line in enumerate(lines):
        m = _TEST_FUNC_DEF.match(line)
        if not m:
            continue
        func_name = m.group(1)
        if not _VERSION_NAME_PREFIX.match(func_name):
            continue
        token_match = _VERSION_NAME_TOKEN.search(func_name)
        token = token_match.group(0).strip("_") if token_match else func_name
        body_text = _function_body_text(lines, idx)
        if _version_token_reappears(token, body_text):
            continue
        findings.append(
            _version_named_finding(filepath, idx + 1, "version-named-test-function", func_name, line.strip())
        )

    return findings


def _scan_version_named_test_class(inputs: "_ScanInputs") -> list[_Finding]:
    """The "version-named-test" hygiene class. Scoped to the test-tree
    surface only -- this class is about how test modules and test
    functions are named, and the test-tree surface (``team/tests/`` by
    default) is the one surface that carries test modules."""
    findings: list[_Finding] = []
    for filepath in inputs.test_tree_files:
        findings.extend(_scan_version_named_test_file(filepath, verbose=inputs.verbose))
    return findings


def _count_def_params(params_text: str) -> int:
    """Count the parameters in a ``def`` signature's parameter-list text,
    splitting on top-level commas only (commas nested inside a default
    value's own brackets/parens/braces do not split). ``self`` and ``cls``
    are excluded -- a comment claiming "three arguments" almost always
    means the caller-facing arguments, not the bound receiver."""
    depth = 0
    current: list[str] = []
    parts: list[str] = []
    for ch in params_text:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))

    count = 0
    for raw in parts:
        name = raw.strip()
        if not name:
            continue
        name = name.lstrip("*").strip()
        base = name.split(":", 1)[0].split("=", 1)[0].strip()
        if base in ("self", "cls"):
            continue
        count += 1
    return count


def _claimed_arg_count(text: str) -> Optional[int]:
    """Return the argument/parameter count *text* claims (spelled-out one
    through ten, or a bare digit run), or ``None`` if it makes no such
    claim."""
    m = _ARG_COUNT_CLAIM.search(text)
    if not m:
        return None
    num = m.group("num").lower()
    if num.isdigit():
        return int(num)
    return _ARG_COUNT_WORDS.get(num)


def _scan_comment_drift_lines(cls_name: str, path: Path, lines: list[str]) -> list[_Finding]:
    """Scan *lines* for the one comment-drift shape this class checks: a
    comment or docstring stating an argument/parameter COUNT that the
    adjacent ``def`` signature contradicts. Two positions are checked:

      A. A ``#`` comment claiming a count, immediately followed (skipping
         only blank lines) by a single-line ``def`` -- the comment
         describes the function below it.
      B. A single-line docstring that is the first statement of a function
         body, claiming a count -- checked against the ENCLOSING function's
         own signature (the ``def`` line immediately above, skipping only
         blank lines).

    Deliberately out of scope, so as not to become a false-positive
    generator (docs/coding-standards.md "narrow class that catches less"):
    multi-line ``def`` signatures, multi-line docstrings, a docstring or
    comment describing a DIFFERENT function than the adjacent one, and any
    claim that is not an argument/parameter count (a claim about a return
    value, a branch count, a list length, or free-form prose intent). A
    comment that is merely general -- describing intent at a level the code
    does not restate -- makes no claim this scan recognizes, so it is never
    a candidate; only the narrow argument-count shape is checked at all.
    """
    findings: list[_Finding] = []
    n = len(lines)

    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith("#"):
            claimed = _claimed_arg_count(stripped)
            if claimed is None:
                continue
            j = idx + 1
            while j < n and not lines[j].strip():
                j += 1
            if j >= n:
                continue
            def_match = _DEF_LINE.match(lines[j])
            if not def_match:
                continue
            actual = _count_def_params(def_match.group("params"))
            if actual != claimed:
                findings.append(
                    _Finding(
                        cls_name, path, idx + 1, "argument-count-drift",
                        f"comment claims {claimed}, def has {actual}", stripped,
                    )
                )
            continue

        doc_match = _SINGLE_LINE_DOCSTRING.match(line)
        if not doc_match:
            continue
        body = doc_match.group("body1")
        if body is None:
            body = doc_match.group("body2")
        claimed = _claimed_arg_count(body or "")
        if claimed is None:
            continue
        k = idx - 1
        while k >= 0 and not lines[k].strip():
            k -= 1
        if k < 0:
            continue
        def_match = _DEF_LINE.match(lines[k])
        if not def_match:
            continue
        actual = _count_def_params(def_match.group("params"))
        if actual != claimed:
            findings.append(
                _Finding(
                    cls_name, path, idx + 1, "argument-count-drift",
                    f"docstring claims {claimed}, enclosing def has {actual}", stripped,
                )
            )

    return findings


def _scan_comment_drift_file(filepath: Path, verbose: bool = False) -> list[_Finding]:
    if verbose:
        print(f"  scanning: {filepath}", flush=True)
    try:
        text = filepath.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [_Finding("comment-drift", filepath, 0, "READ-ERROR", str(exc), "")]
    if filepath.suffix != ".py":
        return []
    lines = text.splitlines()
    return _scan_comment_drift_lines("comment-drift", filepath, lines)


def _scan_comment_drift_class(inputs: "_ScanInputs") -> list[_Finding]:
    """The "comment-drift" hygiene class: checks the code and test-tree
    surfaces (Python ``def`` signatures only -- the prose surface carries
    no ``def`` to check a claim against)."""
    findings: list[_Finding] = []
    for filepath in inputs.code_files:
        findings.extend(_scan_comment_drift_file(filepath, verbose=inputs.verbose))
    for filepath in inputs.test_tree_files:
        findings.extend(_scan_comment_drift_file(filepath, verbose=inputs.verbose))
    return findings


def _is_placeholder_home_name(name: str) -> bool:
    """Return True if *name* (the segment after "/home/") is one of the
    neutral placeholder names, ignoring case and "-"/"_" separators."""
    normalized = name.rstrip(".").lower().replace("-", "").replace("_", "")
    return normalized in _PLACEHOLDER_HOME_NAMES


def _is_neutral_ssh_host(host: str) -> bool:
    """Return True if *host* is the public forge or a reserved example
    domain -- anything else in ``git@<host>:`` is a private alias or a
    private server name."""
    h = host.rstrip(".").lower()
    if h in _NEUTRAL_SSH_HOSTS:
        return True
    if any(h.endswith("." + d) for d in ("example.com", "example.net", "example.org")):
        return True
    return h.rsplit(".", 1)[-1] in _NEUTRAL_SSH_TOP_LABELS


def _scan_operator_literal_lines(cls_name: str, path: Path, lines: list[str]) -> list[_Finding]:
    """Scan *lines* for operator-specific literals. At most one finding per
    line per label: the first home-path match whose name is not a neutral
    placeholder, and the first ``git@<host>:`` remote whose host is not
    neutral -- so a line carrying both a placeholder and a real name is
    still reported, under the real one."""
    findings: list[_Finding] = []
    for idx, line in enumerate(lines):
        if "/home/" not in line and "git@" not in line:
            continue
        stripped = line.strip()
        for m in _HOME_PATH.finditer(line):
            name = m.group("name").rstrip(".")
            if _is_placeholder_home_name(name):
                continue
            findings.append(_Finding(cls_name, path, idx + 1, "home-path", "/home/" + name, stripped))
            break
        for m in _SSH_GIT_REMOTE.finditer(line):
            if _is_neutral_ssh_host(m.group("host")):
                continue
            findings.append(_Finding(cls_name, path, idx + 1, "ssh-host-alias", m.group(0), stripped))
            break
    return findings


def _operator_literal_in_scope(relpath: str) -> bool:
    """Return True if the repo-relative *relpath* is on the operator-literal
    surface: every tracked file except release notes, the rendered
    changelog, and the test tree -- with the test tree's archived
    ``artifacts/`` subtree kept in scope, since those snapshots are shipped
    as-is and read like documentation."""
    if relpath.startswith("release-notes/") or relpath == "CHANGELOG.md":
        return False
    if relpath.startswith("team/tests/") and not relpath.startswith("team/tests/artifacts/"):
        return False
    return Path(relpath).name not in _FILE_WHITELIST


def _git_tracked_files(
    repo_root: Path,
    verbose: bool = False,
    *,
    include_untracked: bool = False,
    surface: str = "operator-literal",
) -> list[Path]:
    """Return every file git tracks under *repo_root*, as absolute paths.

    With *include_untracked*, untracked files git does not ignore are
    listed too (``--others --exclude-standard``). Only a directory that is
    itself the top of a git work tree is listed; anything else (an installed
    root, a bare temp directory, a subdirectory of some unrelated checkout)
    yields an empty list, so the surface is never borrowed from an ancestor
    repository. ``--verbose`` says when that happens, naming *surface*."""
    root = repo_root.resolve()
    ls_args = ["ls-files", "-z"]
    if include_untracked:
        ls_args += ["--cached", "--others", "--exclude-standard"]
    try:
        top = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=False,
        )
        if top.returncode != 0 or Path(top.stdout.strip()).resolve() != root:
            if verbose:
                print(f"  {surface}: {root} is not a git work-tree top; no tracked files to scan", flush=True)
            return []
        listed = subprocess.run(
            ["git", "-C", str(root), *ls_args],
            capture_output=True, check=False,
        )
    except OSError as exc:
        if verbose:
            print(f"  {surface}: could not run git ({exc}); no tracked files to scan", flush=True)
        return []
    if listed.returncode != 0:
        return []
    names = [n for n in listed.stdout.decode("utf-8", errors="replace").split("\0") if n]
    return [root / n for n in names]


def _scan_operator_literal_class(inputs: "_ScanInputs") -> list[_Finding]:
    """The "operator-literal" hygiene class: operator-specific home-directory
    literals and private SSH host aliases in every tracked file outside
    release notes, the changelog, and the test tree (whose ``artifacts/``
    subtree stays in scope). Unlike the other classes, this one does not
    read the directory surfaces -- its surface is ``git ls-files`` at
    ``--repo-root``. Symlinks, files missing from the work tree, and binary
    files (a NUL byte in the content) are skipped."""
    findings: list[_Finding] = []
    for filepath in _git_tracked_files(inputs.repo_root, verbose=inputs.verbose):
        relpath = _to_relpath(filepath, inputs.repo_root.resolve())
        if not _operator_literal_in_scope(relpath):
            continue
        if filepath.is_symlink() or not filepath.is_file():
            continue
        if inputs.verbose:
            print(f"  scanning: {filepath}", flush=True)
        try:
            data = filepath.read_bytes()
        except OSError as exc:
            findings.append(_Finding("operator-literal", filepath, 0, "READ-ERROR", str(exc), ""))
            continue
        if b"\0" in data:
            continue
        text = data.decode("utf-8", errors="replace")
        findings.extend(_scan_operator_literal_lines("operator-literal", filepath, text.splitlines()))
    return findings


@dataclass(frozen=True)
class HygieneClass:
    """One entry in the hygiene-lint class registry.

    ``rule`` is the one-sentence standing rule the class enforces, printed
    as the report's ``Rule:`` footer whenever a finding of this class is
    reported. ``example_justification`` is the filled-in justification the
    ``--add-suppression`` remedy suggests for a finding of this class. Both
    live beside the class's scan so a report states the rule of the
    classes it actually reports, never another class's."""

    name: str
    description: str
    scan: Callable[["_ScanInputs"], list[_Finding]]
    rule: str
    example_justification: str


_REGISTRY: "dict[str, HygieneClass]" = {}


def _register(hc: HygieneClass) -> None:
    _REGISTRY[hc.name] = hc


_register(
    HygieneClass(
        name="provenance",
        description=(
            "flags bug, priority, task and requirement IDs and versions cited as "
            "history in shipped code, tests and prose; wording is not checked"
        ),
        scan=_scan_provenance_class,
        rule=(
            "comments, docstrings, help text, log lines, and output strings in "
            "production code and tests must not cite internal bug/task/priority IDs, "
            "requirement IDs or versions as history — those belong in the commit "
            "message and task state; wording is not checked."
        ),
        example_justification="retained intentionally as a deliberate internal identifier reference",
    )
)

_register(
    HygieneClass(
        name="version-named-test",
        description=(
            "a test module or test function named for the release that produced "
            "it instead of the behavior it exercises"
        ),
        scan=_scan_version_named_test_class,
        rule=(
            "test modules and test functions are named for the behavior they exercise, "
            "never for the release that produced them — the release belongs in the "
            "commit message and task state."
        ),
        example_justification=(
            "retained intentionally: the version in this name is the subject under test, "
            "not the release that produced it"
        ),
    )
)

_register(
    HygieneClass(
        name="comment-drift",
        description=(
            "a comment or docstring stating an argument/parameter count the "
            "adjacent def signature contradicts"
        ),
        scan=_scan_comment_drift_class,
        rule=(
            "a comment or docstring that states an argument or parameter count must "
            "match the signature it describes — fix whichever side is wrong in the "
            "same change."
        ),
        example_justification=(
            "retained intentionally: the stated count describes a different call shape, "
            "not this def's own signature"
        ),
    )
)

_register(
    HygieneClass(
        name="operator-literal",
        description=(
            "an operator-specific home-directory literal or private SSH host alias "
            "in a tracked file outside release-notes/, CHANGELOG.md and team/tests/ "
            "(team/tests/artifacts/ stays in scope)"
        ),
        scan=_scan_operator_literal_class,
        rule=(
            "shipped files use neutral placeholders such as $HOME/..., ~/..., /home/user/ "
            "or git@github.com:, never one operator's home directory or private SSH host "
            "alias — a public release would publish the account name, and no other "
            "machine resolves the alias."
        ),
        example_justification=(
            "retained intentionally as a deliberate literal path or host the format requires"
        ),
    )
)


# The malformed-sidecar finding carries a class of its own that no scan
# produces, so it is not a registry entry; its rule is stated here.
_SIDECAR_CLASS = "hygiene-sidecar"
_SIDECAR_RULE = (
    "every allowlist sidecar entry names a file, a class, a content hash and a "
    "non-empty justification; a sidecar that fails to load suppresses nothing."
)


def _class_entry(cls_name: str) -> HygieneClass:
    """Return the registry entry for *cls_name*, or the ``provenance`` entry
    for a name the registry does not hold -- the same default
    _finding_cause applies to a finding of an unrecognised class."""
    return _REGISTRY.get(cls_name, _REGISTRY["provenance"])


def registered_class_names() -> list[str]:
    """Return the names of every registered hygiene class, in registration
    order -- used by ``--help`` and by ``--classes`` validation."""
    return list(_REGISTRY.keys())


# ---------------------------------------------------------------------------
# Suppression sidecar
# ---------------------------------------------------------------------------


class _SidecarError(Exception):
    """Raised when the sidecar file cannot be loaded or fails validation.
    Every raise site names the offending entry or the structural problem
    concretely -- this is the message ``main()`` prints via the dual-channel
    emitter."""


@dataclass(frozen=True)
class _SidecarEntry:
    file: str
    cls: str
    content_hash: str
    justification: str
    line_hint: Optional[int] = None


def _line_fingerprint(line_text: str) -> str:
    """Return the content-hash anchor for *line_text* (a single line's text,
    already stripped of surrounding whitespace by the caller). See the
    module docstring's "Content-hash anchoring" for the drift rationale.
    Twelve hex characters (48 bits) is ample collision resistance for a
    per-repository allowlist scoped to (file, class) pairs."""
    return hashlib.sha256(line_text.encode("utf-8")).hexdigest()[:12]


def _load_sidecar(path: Path) -> list[_SidecarEntry]:
    """Load and validate the sidecar at *path*. A missing sidecar is not an
    error -- it means no suppressions are on file yet. Raises
    ``_SidecarError`` naming the exact structural problem for anything else
    that goes wrong (unreadable file, invalid JSON, wrong shape, a blank
    justification)."""
    if not path.exists():
        return []
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise _SidecarError(f"sidecar {path} could not be read: {exc}") from exc
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise _SidecarError(f"sidecar {path} is not valid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise _SidecarError(
            f"sidecar {path} must be a JSON array of entries; found a "
            f"top-level {type(data).__name__}"
        )
    return [_parse_sidecar_entry(path, idx, raw) for idx, raw in enumerate(data)]


def _parse_sidecar_entry(path: Path, idx: int, raw_entry: object) -> _SidecarEntry:
    if not isinstance(raw_entry, dict):
        raise _SidecarError(f"sidecar {path} entry #{idx} is not a JSON object")

    required = ("file", "class", "content_hash", "justification")
    missing = [key for key in required if key not in raw_entry]
    if missing:
        raise _SidecarError(
            f"sidecar {path} entry #{idx} is missing required field(s): {', '.join(missing)}"
        )

    justification = raw_entry["justification"]
    if not isinstance(justification, str) or not justification.strip():
        raise _SidecarError(
            f"sidecar {path} entry #{idx} (file={raw_entry.get('file')!r}, "
            f"class={raw_entry.get('class')!r}, "
            f"content_hash={raw_entry.get('content_hash')!r}) has a blank "
            "justification -- every suppression must carry a non-empty reason"
        )

    line_hint = raw_entry.get("line_hint")
    return _SidecarEntry(
        file=str(raw_entry["file"]),
        cls=str(raw_entry["class"]),
        content_hash=str(raw_entry["content_hash"]),
        justification=justification,
        line_hint=int(line_hint) if isinstance(line_hint, int) else None,
    )


def _suppressed_keys(entries: list[_SidecarEntry]) -> "set[tuple[str, str, str]]":
    return {(e.file, e.cls, e.content_hash) for e in entries}


def _apply_sidecar_suppression(
    findings: list[_Finding], repo_root: Path, entries: list[_SidecarEntry]
) -> list[_Finding]:
    """Drop every finding a sidecar entry covers. A finding is covered when
    its (file, class, content-hash-of-its-own-line) triple matches an
    entry's (file, class, content_hash) exactly -- no in-file marker is ever
    consulted."""
    suppressed = _suppressed_keys(entries)
    kept: list[_Finding] = []
    for f in findings:
        if f.label in ("READ-ERROR", "SIDECAR-MALFORMED"):
            # A sidecar-load finding can never be suppressed by the very
            # sidecar it is reporting as broken.
            kept.append(f)
            continue
        rel = _to_relpath(f.path, repo_root)
        key = (rel, f.cls, _line_fingerprint(f.line_text))
        if key in suppressed:
            continue
        kept.append(f)
    return kept


def _default_sidecar_path(script_dir: Path) -> Path:
    return script_dir / "hygiene-lint-allowlist.json"


def _add_suppression(
    file_arg: str, line_arg: int, cls_arg: str, justification: str, repo_root: Path, sidecar_path: Path
) -> int:
    """Handle ``--add-suppression``: read FILE's current LINE, fingerprint
    it, and append a new sidecar entry. Prints a plain diagnostic and
    returns 2 on a bad FILE/LINE -- an operator-facing CLI argument
    validation, not a censused gate (the
    operator is typing this by hand at a terminal, the same posture as any
    other interactive-utility argument check)."""
    target = Path(file_arg)
    if not target.is_absolute():
        target = repo_root / file_arg
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        print(f"lint_hygiene: --add-suppression could not read {target}: {exc}", file=sys.stderr)
        return 2
    if line_arg < 1 or line_arg > len(lines):
        print(
            f"lint_hygiene: --add-suppression line {line_arg} is out of range for "
            f"{target} ({len(lines)} line(s))",
            file=sys.stderr,
        )
        return 2

    content_hash = _line_fingerprint(lines[line_arg - 1].strip())
    rel = _to_relpath(target, repo_root)

    try:
        entries_raw: list[dict] = json.loads(sidecar_path.read_text(encoding="utf-8")) if sidecar_path.exists() else []
    except (OSError, json.JSONDecodeError) as exc:
        print(f"lint_hygiene: --add-suppression could not read sidecar {sidecar_path}: {exc}", file=sys.stderr)
        return 2

    entries_raw.append(
        {
            "file": rel,
            "class": cls_arg,
            "content_hash": content_hash,
            "line_hint": line_arg,
            "justification": justification,
        }
    )
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    sidecar_path.write_text(json.dumps(entries_raw, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"lint_hygiene: appended suppression for {rel}:{line_arg} [{cls_arg}] to {sidecar_path}")
    return 0


# ---------------------------------------------------------------------------
# Remedy block + findings-file output
# ---------------------------------------------------------------------------


def _finding_remedy(f: _Finding, repo_root: Path, lint_script: Path, sidecar_path: Path) -> str:
    """Build the runnable fix command for *f*. Every path in the returned
    command is expressed relative to *repo_root* (falling back to absolute
    only when a path does not actually sit under *repo_root* -- see
    _to_relpath), and --repo-root is spelled "." rather than interpolated
    absolute. This is deliberate: this same string is both printed to an
    operator's terminal (_format_remedy_block) and persisted verbatim into
    a filed bug report (bug_filer.py's _remedy_line) that a stranger reads
    long after the tree that generated it may be gone. An absolute path
    baked in here would be dead the moment that tree is deleted; a
    repo-relative command run from the repository root of ANY checkout
    that still carries the finding works identically. One command shape
    serves both readers -- there is no separate absolute-path variant to
    keep in sync."""
    rel = _to_relpath(f.path, repo_root)
    script_rel = _to_relpath(lint_script, repo_root)
    sidecar_rel = _to_relpath(sidecar_path, repo_root)

    if f.label == "READ-ERROR":
        return f"fix the read error at {rel} ({f.token}) and re-run the lint"

    if f.label == "SIDECAR-MALFORMED":
        return (
            f'fix the entry in "{sidecar_rel}" named above, or remove it, then re-run '
            f'from the repository root: python3 "{script_rel}"'
        )

    if f.cls == "version-named-test" and f.label == "version-named-test-file":
        base = f"rename {rel} to describe the behavior the module exercises, not the release that produced it"
    elif f.cls == "version-named-test":
        base = (
            f"rename the test function at {rel}:{f.lineno} to describe the behavior "
            "it exercises, not the release that produced it"
        )
    elif f.cls == "comment-drift":
        base = (
            f"fix the drift at {rel}:{f.lineno} -- update the comment/docstring to "
            "state the actual count, or change the def to match the count it claims"
        )
    elif f.cls == "operator-literal" and f.label == "ssh-host-alias":
        base = (
            f"replace '{f.token}' at {rel}:{f.lineno} with the public form, "
            "for example git@github.com:org/repo.git"
        )
    elif f.cls == "operator-literal":
        base = (
            f"replace '{f.token}' at {rel}:{f.lineno} with a neutral form, for example "
            "$HOME/pgai_agent_kanban, ~/develop/my-app, or /home/user/ where a literal path is required"
        )
    else:
        base = f"reword {rel}:{f.lineno} to describe behavior instead of citing the {f.label}"

    example_justification = _class_entry(f.cls).example_justification
    return (
        f"{base}, or suppress it deliberately (run from the repository root): "
        f'python3 "{script_rel}" --add-suppression "{rel}" {f.lineno} {f.cls} '
        f'"{example_justification}" --sidecar "{sidecar_rel}" --repo-root "."'
    )


def _finding_cause(f: _Finding, repo_root: Path) -> str:
    rel = _to_relpath(f.path, repo_root)
    if f.label == "READ-ERROR":
        return f"{rel}: could not be read — {f.token}"

    if f.label == "SIDECAR-MALFORMED":
        # f.token is the exact _SidecarError message -- it already names
        # the sidecar path, the offending entry, and the reason it was
        # refused. It spells the sidecar with the same absolute, resolved
        # path this Finding carries as f.path (str(f.path)) -- the
        # spelling a person wants when reading it at a terminal -- but a
        # persisted cause must survive the scan root itself being deleted.
        # Rather than author a second message from scratch (two strings
        # that could drift apart), every occurrence of that one absolute
        # path is swapped for its repo-relative form: one value, two
        # renderings.
        return f.token.replace(str(f.path), rel)

    if f.cls == "version-named-test" and f.label == "version-named-test-file":
        return (
            f"{rel}:{f.lineno}: [{f.label}] test module name encodes a release "
            f"version ('{f.token}') instead of the behavior it exercises"
        )
    if f.cls == "version-named-test":
        return (
            f"{rel}:{f.lineno}: [{f.label}] test function name encodes a release "
            f"version ('{f.token}') instead of the behavior it exercises"
        )
    if f.cls == "comment-drift":
        return f"{rel}:{f.lineno}: [{f.label}] {f.token} -- comment/docstring drifted from the code it describes"
    if f.cls == "operator-literal" and f.label == "ssh-host-alias":
        return (
            f"{rel}:{f.lineno}: [{f.label}] private SSH host alias '{f.token}' in a "
            "shipped file -- it only resolves through one operator's ~/.ssh/config"
        )
    if f.cls == "operator-literal":
        return (
            f"{rel}:{f.lineno}: [{f.label}] operator-specific home directory '{f.token}' "
            "in a shipped file -- a public release would publish one person's account name"
        )

    return (
        f"{rel}:{f.lineno}: [{f.label}] provenance reference '{f.token}' in "
        "code — comments and output must describe behavior, not internal IDs"
    )


def _finding_to_dict(f: _Finding, repo_root: Path, lint_script: Path, sidecar_path: Path) -> dict:
    return {
        "lint": "hygiene",
        "class": f.cls,
        "severity": "quality",
        "file": _to_relpath(f.path, repo_root),
        "line": f.lineno,
        "label": f.label,
        "token": f.token,
        "cause": _finding_cause(f, repo_root),
        "remedy": _finding_remedy(f, repo_root, lint_script, sidecar_path),
    }


def _format_remedy_block(
    findings: list[_Finding], repo_root: Path, lint_script: Path, sidecar_path: Path, verify_command: str
) -> str:
    lines = [
        f"lint_hygiene: {len(findings)} finding(s) (non-blocking; see docs/coding-standards.md directive 8)",
        "",
        "Cause(s):",
    ]
    for f in findings:
        lines.append(f"  - {_finding_cause(f, repo_root)}")
    lines.append("")
    lines.append("To fix (reword the line, or suppress it deliberately with a justification):")
    for i, f in enumerate(findings, start=1):
        lines.append(f"  {i}. {_finding_remedy(f, repo_root, lint_script, sidecar_path)}")
    lines.append(f"  {len(findings) + 1}. {verify_command}")
    lines.append("")
    lines.extend(_report_rule_lines(findings))
    return "\n".join(lines)


def _report_rule_lines(findings: list[_Finding]) -> list[str]:
    """Return the report's rule footer: the rule of every class present in
    *findings*, in registry order, with the malformed-sidecar rule last.

    A report holding one class prints the bare ``Rule: <sentence>`` form;
    a report holding several prints one ``Rule (<class>): <sentence>`` line
    per class, so each rule is attributed to the findings it explains."""
    present = {f.cls for f in findings}
    rules: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name, hc in _REGISTRY.items():
        if name in present:
            rules.append((name, hc.rule))
            seen.add(name)
    # A class name no registry entry holds falls back to the provenance
    # entry, matching the cause and remedy text of such a finding.
    if any(c not in seen and c != _SIDECAR_CLASS for c in present) and "provenance" not in seen:
        rules.insert(0, ("provenance", _REGISTRY["provenance"].rule))
    if _SIDECAR_CLASS in present:
        rules.append((_SIDECAR_CLASS, _SIDECAR_RULE))
    if len(rules) == 1:
        return [f"Rule: {rules[0][1]}"]
    return [f"Rule ({name}): {rule}" for name, rule in rules]


# ---------------------------------------------------------------------------
# Whole-tree run
# ---------------------------------------------------------------------------


def run_hygiene_lint(
    *,
    code_dirs: list[Path],
    test_tree_dirs: list[Path],
    prose_dirs: list[Path],
    config_dirs: list[Path],
    repo_root: Path,
    sidecar_path: Path,
    docs_dirs: "list[Path] | None" = None,
    text_dirs: "list[Path] | None" = None,
    classes: Optional[list[str]] = None,
    include_repo_root_scripts: bool = False,
    include_repo_root_text_files: bool = False,
    verbose: bool = False,
) -> list[_Finding]:
    """Run every selected registered class over the given surfaces and
    return the surviving findings (sidecar-suppressed findings removed).
    *include_repo_root_scripts* adds top-level ``*.sh`` to the code surface;
    *include_repo_root_text_files* adds top-level ``*.md`` (except
    CHANGELOG.md), and ``*_example`` / ``*.example`` files at any depth of
    the git work tree (except team/tests/ and release-notes/), to the
    shipped-text surface -- the CLI
    sets each only when the matching ``--*-dirs`` flag is left at its
    default, so an explicit directory list scans exactly what it names.
    A sidecar that fails to load (unreadable, invalid JSON, wrong shape, a
    blank/missing justification) never aborts this call: it is appended to
    the returned findings as its own ``SIDECAR-MALFORMED`` entry, and no
    suppression from that broken file is applied for this run -- see the
    module docstring's "Exit codes" for why this site must never signal
    failure through an exception or a process exit code."""
    selected = classes if classes is not None else registered_class_names()

    code_targets = [_ScanTarget(d, test_tree_surface=False) for d in code_dirs]

    # Route every "tests"/"test" directory nested under a code-surface
    # directory to the test-tree surface instead of letting the code
    # surface's own exclusion drop it -- see module docstring's
    # "Scan-surface routing".
    routed_test_tree_dirs = [
        routed for d in code_dirs for routed in _discover_routed_test_tree_dirs(d)
    ]
    test_tree_targets = [_ScanTarget(d, test_tree_surface=True) for d in test_tree_dirs] + [
        _ScanTarget(d, test_tree_surface=True) for d in routed_test_tree_dirs
    ]

    code_pairs = _collect_files(code_targets, (".py", ".sh"), include_extensionless_shebang=True)
    if include_repo_root_scripts:
        code_pairs.extend(_collect_repo_root_scripts(repo_root))
    test_tree_pairs = _collect_files(
        test_tree_targets, (".py", ".sh"), include_extensionless_shebang=True
    )
    prose_pairs = _collect_files(
        [_ScanTarget(d, test_tree_surface=False) for d in prose_dirs], (".md",)
    )
    docs_pairs = _collect_files(
        [_ScanTarget(d, test_tree_surface=False) for d in (docs_dirs or [])], (".md",)
    )
    config_pairs = _collect_files(
        [_ScanTarget(d, test_tree_surface=False) for d in config_dirs],
        (".manifest", ".census", ".txt"),
    )
    text_pairs = _collect_files(
        [_ScanTarget(d, test_tree_surface=False) for d in (text_dirs or [])],
        (),
        any_text_file=True,
    )
    if include_repo_root_text_files:
        # The directory surfaces may already reach an example file (one
        # under team/templates/, say); each file is scanned once.
        already = {p for p, _ in text_pairs}
        for pair in _collect_repo_root_text_files(repo_root) + _collect_example_files(
            repo_root, verbose=verbose
        ):
            if pair[0] not in already:
                already.add(pair[0])
                text_pairs.append(pair)

    inputs = _ScanInputs(
        code_files=[p for p, _ in code_pairs],
        test_tree_files=[p for p, _ in test_tree_pairs],
        prose_files=[p for p, _ in prose_pairs],
        config_files=[p for p, _ in config_pairs],
        repo_root=repo_root,
        verbose=verbose,
        docs_files=[p for p, _ in docs_pairs],
        text_files=[p for p, _ in text_pairs],
    )

    findings: list[_Finding] = []
    for name in selected:
        hc = _REGISTRY[name]
        findings.extend(hc.scan(inputs))

    try:
        sidecar_entries = _load_sidecar(sidecar_path)
    except _SidecarError as exc:
        # A malformed sidecar is authored content with zero runtime-failure
        # risk (docs/blocking-site-census.md, CONVERT class): report it as
        # its own finding and continue, honouring no suppression from the
        # file that failed to load, rather than aborting the caller.
        findings.append(
            _Finding(
                cls=_SIDECAR_CLASS,
                path=sidecar_path,
                lineno=0,
                label="SIDECAR-MALFORMED",
                token=str(exc),
                line_text="",
            )
        )
        sidecar_entries = []

    return _apply_sidecar_suppression(findings, repo_root, sidecar_entries)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _default_code_dirs(script_dir: Path, repo_root: Path) -> list[Path]:
    return [
        script_dir,
        script_dir / "lib",
        script_dir / "cm",
        script_dir / "dashboard" / "lib",
        repo_root / "team" / "pgai_agent_kanban",
    ]


def _default_text_dirs(repo_root: Path) -> list[Path]:
    """The shipped-text surface's default directories: role files, the
    agent prompts installed as subagents, the install/project/task
    templates, the PM agent, and the container build files."""
    return [
        repo_root / "team" / "roles",
        repo_root / "subagents",
        repo_root / "team" / "templates",
        repo_root / "team" / "pm-agent",
        repo_root / "docker",
    ]


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="lint_hygiene.py",
        description=textwrap.fill(
            "Single hygiene-lint entry point: a registry of named hygiene classes, "
            "each emitting per-instance findings (class, file, line, remedy). "
            "Findings are reported, never a reason to fail a build -- see Exit codes "
            "in the module docstring for the one exception (a malformed sidecar).",
            width=78,
        ),
        epilog="registered classes:\n" + "\n".join(
            textwrap.fill(
                f"{hc.name}: {hc.description}", width=78,
                initial_indent="  ", subsequent_indent="      ",
            )
            for hc in _REGISTRY.values()
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--code-dirs", metavar="PATH", nargs="*", default=None,
                         help="Code-surface directories to scan (.py/.sh). Default: the "
                              "production code surfaces (see module docstring).")
    parser.add_argument("--test-tree-dirs", metavar="PATH", nargs="*", default=None,
                         help="Test-tree-surface directories to scan (.py/.sh). Default: team/tests/. "
                              "Comments, docstrings and assert messages here are flagged for bug, "
                              "priority, task and requirement IDs and versions cited as history; "
                              "wording is not checked.")
    parser.add_argument("--prose-dirs", metavar="PATH", nargs="*", default=None,
                         help="Prose-surface directories to scan (.md). Default: release-notes/. "
                              "BUG-ID/PRIORITY-ID/bare-release-slug/version-history are "
                              "sanctioned content here.")
    parser.add_argument("--docs-dirs", metavar="PATH", nargs="*", default=None,
                         help="Docs-surface directories to scan (.md). Default: docs/. Unlike "
                              "--prose-dirs, the full pattern set is enforced with no "
                              "surface-specific exemption -- standalone documentation is not a "
                              "change record (see module docstring's 'Docs surface').")
    parser.add_argument("--config-dirs", metavar="PATH", nargs="*", default=None,
                         help="Config-surface directories to scan (.manifest/.census/.txt). "
                              "Default: team/config/.")
    parser.add_argument("--text-dirs", metavar="PATH", nargs="*", default=None,
                         help="Shipped-text-surface directories to scan: every non-binary "
                              "file, whatever its suffix, with the full pattern set enforced. "
                              "Default: team/roles/, subagents/, team/templates/, "
                              "team/pm-agent/ and docker/, plus the top-level *.md files "
                              "(except CHANGELOG.md) and every *_example / *.example file "
                              "in the git work tree outside team/tests/ and release-notes/; "
                              "an explicit list scans only the directories it names.")
    parser.add_argument("--classes", metavar="NAME", nargs="*", default=None,
                         help=f"Hygiene classes to run. Default: all registered classes "
                              f"({', '.join(registered_class_names())}).")
    parser.add_argument("--sidecar", metavar="PATH", default=None,
                         help="Allowlist sidecar path. Default: "
                              "team/scripts/hygiene-lint-allowlist.json.")
    parser.add_argument("--repo-root", metavar="PATH", default=None,
                         help="Root used to compute each finding's repo-relative 'file' "
                              "identity. Default: inferred from this script's location.")
    parser.add_argument("--findings-file", metavar="PATH", default=None,
                         help="Write findings as a JSON array to PATH (parent dirs created "
                              "as needed). Always printed to stdout as a remedy block regardless.")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print each file name as it is scanned.")
    parser.add_argument("--add-suppression", metavar=("FILE", "LINE", "CLASS", "JUSTIFICATION"),
                         nargs=4, default=None,
                         help="Append a sidecar entry suppressing CLASS's finding on FILE's "
                              "current LINE, with the given JUSTIFICATION. Derives the "
                              "content-hash anchor from FILE's actual text.")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    """Run the hygiene lint, or record a suppression with ``--add-suppression``.

    Exit codes: 0 the lint ran to completion, with or without findings (a
    malformed allowlist sidecar is itself a finding); 2 a bad argument, such as
    an unrecognized ``--classes`` value or a malformed ``--add-suppression``.
    With ``--add-suppression``, the suppression writer's own exit code.
    """
    args = _parse_args(argv)

    script_dir = Path(__file__).resolve().parent  # team/scripts/
    default_repo_root = script_dir.parent.parent   # repo root
    repo_root = Path(args.repo_root).resolve() if args.repo_root else default_repo_root
    sidecar_path = Path(args.sidecar).resolve() if args.sidecar else _default_sidecar_path(script_dir)

    if args.add_suppression is not None:
        file_arg, line_arg, cls_arg, justification = args.add_suppression
        try:
            line_int = int(line_arg)
        except ValueError:
            print(f"lint_hygiene: --add-suppression LINE must be an integer, got {line_arg!r}", file=sys.stderr)
            return 2
        return _add_suppression(file_arg, line_int, cls_arg, justification, repo_root, sidecar_path)

    if args.classes is not None:
        unknown = [c for c in args.classes if c not in _REGISTRY]
        if unknown:
            print(
                f"lint_hygiene: unknown --classes value(s) {unknown}; "
                f"registered classes are {registered_class_names()}",
                file=sys.stderr,
            )
            return 2

    default_code = args.code_dirs is None
    code_dirs = (
        _default_code_dirs(script_dir, repo_root)
        if default_code
        else [Path(d).resolve() for d in args.code_dirs if d]
    )
    test_tree_dirs = (
        [repo_root / "team" / "tests"]
        if args.test_tree_dirs is None
        else [Path(d).resolve() for d in args.test_tree_dirs if d]
    )
    prose_dirs = (
        [repo_root / "release-notes"]
        if args.prose_dirs is None
        else [Path(d).resolve() for d in args.prose_dirs if d]
    )
    docs_dirs = (
        [repo_root / "docs"]
        if args.docs_dirs is None
        else [Path(d).resolve() for d in args.docs_dirs if d]
    )
    config_dirs = (
        [repo_root / "team" / "config"]
        if args.config_dirs is None
        else [Path(d).resolve() for d in args.config_dirs if d]
    )
    default_text = args.text_dirs is None
    text_dirs = (
        _default_text_dirs(repo_root)
        if default_text
        else [Path(d).resolve() for d in args.text_dirs if d]
    )

    print(
        f"lint_hygiene: scanning {len(code_dirs)} code dir(s), {len(test_tree_dirs)} "
        f"test-tree dir(s), {len(prose_dirs)} prose dir(s), {len(docs_dirs)} docs "
        f"dir(s), {len(config_dirs)} config dir(s), {len(text_dirs)} shipped-text dir(s); "
        f"classes={args.classes or registered_class_names()}",
        flush=True,
    )

    findings = run_hygiene_lint(
        code_dirs=code_dirs,
        test_tree_dirs=test_tree_dirs,
        prose_dirs=prose_dirs,
        docs_dirs=docs_dirs,
        config_dirs=config_dirs,
        text_dirs=text_dirs,
        repo_root=repo_root,
        sidecar_path=sidecar_path,
        classes=args.classes,
        include_repo_root_scripts=default_code,
        include_repo_root_text_files=default_text,
        verbose=args.verbose,
    )

    lint_script = Path(__file__).resolve()
    total_files = (
        len(code_dirs) + len(test_tree_dirs) + len(prose_dirs) + len(docs_dirs)
        + len(config_dirs) + len(text_dirs)
    )

    if findings:
        verify_parts = [f'python3 "{lint_script}"']
        if not default_code:
            verify_parts.append("--code-dirs " + " ".join(f'"{d}"' for d in code_dirs))
        verify_command = " ".join(verify_parts)
        print("\n" + _format_remedy_block(findings, repo_root, lint_script, sidecar_path, verify_command), flush=True)
    else:
        print(f"lint_hygiene: OK — 0 findings across {total_files} scan director(y/ies).", flush=True)

    if args.findings_file:
        findings_path = Path(args.findings_file)
        findings_path.parent.mkdir(parents=True, exist_ok=True)
        finding_dicts = [_finding_to_dict(f, repo_root, lint_script, sidecar_path) for f in findings]
        findings_path.write_text(json.dumps(finding_dicts, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # Quality-class lint: findings -- including a malformed sidecar -- are
    # reported, never a reason to fail a build. Always exit 0 here; a bad
    # CLI argument, rejected earlier in this function before the scan
    # begins, is the only thing that produces a non-zero exit from this
    # script (see the module docstring's "Exit codes" section).
    return 0


if __name__ == "__main__":
    sys.exit(main())
