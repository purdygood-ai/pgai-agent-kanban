#!/usr/bin/env python3
"""
lint_test_anti_patterns.py
==========================
CI lint for the pgai-agent-kanban test suite AND runtime scripts.

Scans team/tests/ for the two highest-impact anti-patterns documented in
roles/TESTER.md "Test Authoring Guidelines":

  Anti-pattern 1 — Pattern-scan universal invariants
      A test loops over all items returned by a scan function (glob, find,
      matching, finditer, findall) and asserts something about every item.
      When production code legitimately adds a new item that does not satisfy
      the assertion, the test fails spuriously.

  Anti-pattern 2 — Environment-coupled fixtures
      A test hardcodes a /tmp/<name> path, $HOME, or /home/<user>/ path for
      filesystem operations rather than using mktemp + PGAI_AGENT_KANBAN_TEMP_DIR.
      Any bare-/tmp path is flagged regardless of name prefix — the zero-exemption
      contract applies: /tmp/fake_xyz, /tmp/stub_foo, and /tmp/anything are all
      flagged exactly like /tmp/real_dir.  Shell redirects to a bare /tmp path
      (stdout redirect, stderr redirect, append, and combined forms) and Python
      open() calls that write to a bare /tmp path are also flagged.

Additionally scans runtime script directories (team/scripts/, team/scripts/lib/,
team/scripts/lib/overwatch-checks/) for Anti-pattern 2 violations including bare
mktemp and bare mktemp -d calls that bypass the framework temp-dir helpers.  This
prevents regressions of the bare-/tmp and hardcoded-temp-path anti-pattern class.

  Anti-pattern 6 — Bug-provenance test names
      A test function is named with a bug-ID suffix (e.g.
      ``test_foo_bug0248``, ``test_bar_BUG_0009``, or ``test_baz_Bug123``).
      Names must describe behavior, not the historical defect that prompted
      the test.  The anti-pattern token is ``bug`` (case-insensitive) immediately
      followed by one or more digits, appearing anywhere in the function name
      after the leading ``test_`` prefix.  Names where "bug" describes a real
      return value (e.g. ``test_returns_bug_report``) are not flagged because
      "bug" is not immediately followed by digits in those cases.

  Anti-pattern 7 — Golden comparison with an inherited subprocess environment
      A narrower sub-class of Anti-pattern 2, scoped down deliberately
      because inherited-environment subprocess construction is otherwise far
      too common (over a hundred call sites in team/tests/, most correct) to
      flag on its own.  This check fires only on the pairing that actually
      produces a host-dependent verdict: a test file that both (a) compares
      a captured surface against a committed golden -- recognised by a
      "golden"-named identifier, or a literal "golden" path segment, assigned
      as part of a pathlib join or os.path.join() -- AND (b) builds a
      subprocess environment by inheriting the ambient one (``dict(os.environ)``
      or ``os.environ.copy()``) anywhere in the same file.  Neither half alone
      is a finding: a golden comparison with an explicit, rebuilt environment
      is correct and silent; an inherited environment with no golden
      comparison is one of the many ordinary, correct call sites and is also
      silent. See tests.fixtures.scrubbed_subprocess_env.build_scrubbed_env
      for the remedy.

  Anti-pattern 8 — Uncontrolled absolute-path substring assertion
      A narrower sub-class of Anti-pattern 2: an assertion searches an
      absolute path for a hardcoded path-segment literal (one that both
      starts and ends with "/", e.g. "/projects/") using Python's ``in`` /
      ``not in`` containment operator, rather than comparing against a
      value the test itself computed. The absolute path's own prefix is
      the ambient scratch root the whole suite happens to run under
      (``PGAI_AGENT_KANBAN_TEMP_DIR`` / pytest's ``tmp_path``), which the
      test does not control — a caller whose own scratch root happens to
      contain the same substring (the multi-project layout puts scratch
      space at ``.../projects/<name>/...``) makes the assertion fail with
      no defect in the code under test. Fix: assert against the path
      RELATIVE to a root value the test built (``path.relative_to(root)``),
      or compare the full expected value the test computed instead of a
      free-floating substring. This fires only on the narrow literal shape
      above — every ordinary ``"needle" in haystack`` containment check in
      the tree (hundreds of them, checking captured stdout/stderr text
      against expected substrings) is untouched.

Scope: Anti-patterns 3, 4, 5 (order-dependent state, production-coupling, and
side-effect-leaking tests) require semantic judgment that static analysis cannot
reliably provide and are explicitly out of scope for this linter.

Exit codes
----------
  0   No findings; all checks passed.
  1   One or more findings; see stdout for per-finding messages.
  2   Usage error or internal failure.

Opt-out
-------
Any flagged line can be silenced on a per-instance basis by placing one of the
following marker comments within 5 lines BEFORE the flagged line:

  # anti-pattern-allowlist: 1 (justification: ...)
  # anti-pattern-allowlist: 2 (justification: ...)
  # anti-pattern-allowlist: 8 (justification: ...)
  # Intentional pattern-scan: <reason>
  # allowlist: <description>
  # scoped-scan: <description>
  # Path-dependent test: <reason>

The justification text is required but its content is not validated.

Reference
---------
  roles/TESTER.md "Test Authoring Guidelines" — canonical anti-pattern
  definitions and recommended alternatives.

Usage
-----
  python3 scripts/lint_test_anti_patterns.py [--tests-dir PATH]
                                              [--scripts-dirs PATH [PATH ...]]
                                              [--verbose]
  python3 scripts/lint_test_anti_patterns.py --files PATH [PATH ...] [--verbose]

  --tests-dir    PATH       Directory to scan for test anti-patterns (default:
                            team/tests/ relative to repo root inferred from
                            this script's location).
  --scripts-dirs PATH ...   Runtime script directories to scan for AP2 only
                            (default: team/scripts/, team/scripts/lib/,
                            team/scripts/lib/overwatch-checks/ relative to repo
                            root).  Pass an empty string to disable runtime
                            scanning.
  --files PATH ...          Named-file mode: scan exactly these test files
                            with every test-file check (AP1, AP2, AP3, AP6,
                            AP7, AP8) and nothing else -- no tests-directory
                            walk and no runtime-script scan.  A named file is
                            scanned even if it sits under a fixtures/
                            directory, because naming it is explicit.  Each
                            path must be an existing file; a missing path is a
                            usage error (exit 2).  Cannot be combined with
                            --tests-dir or --scripts-dirs.  Without --files
                            the whole-tree default above is unchanged.
  --verbose                 Emit each scanned file name before processing.

Examples
--------
  # From repo root:
  python3 team/scripts/lint_test_anti_patterns.py

  # With explicit paths:
  python3 team/scripts/lint_test_anti_patterns.py --tests-dir /path/to/team/tests

  # Disable runtime script scanning:
  python3 team/scripts/lint_test_anti_patterns.py --scripts-dirs

  # Scan only the named test files (e.g. for a targeted test run):
  python3 team/scripts/lint_test_anti_patterns.py \\
      --files team/tests/unit/test_semver.py team/tests/unit/test_skip_bug_citation_scan.py
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Temp-root basename — resolved from PGAI_AGENT_KANBAN_TEMP_DIR at import time.
#
# The linter self-exempts lines that define the temp-root constant itself
# (e.g. in temp.sh or the conftest resolver) from the bare-/tmp AP2 checks.
# That exemption uses a negative lookahead on the basename of the configured
# temp root so the exclusion follows an operator-configured rename automatically.
#
# Resolution order:
#   1. PGAI_AGENT_KANBAN_TEMP_DIR env var (if set and non-empty): use its basename.
#   2. Fallback: "pgai_kanban_tmp" — the default root baked into temp.sh.
# ---------------------------------------------------------------------------

_env_temp_dir = os.environ.get("PGAI_AGENT_KANBAN_TEMP_DIR", "").strip()
_TEMP_ROOT_BASENAME: str = (
    Path(_env_temp_dir).name if _env_temp_dir else "pgai_kanban_tmp"
)

# ---------------------------------------------------------------------------
# Opt-out marker patterns (searched in the N lines BEFORE a flagged line)
# ---------------------------------------------------------------------------

_OPT_OUT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"#\s*anti-pattern-allowlist\s*:\s*[12678]", re.IGNORECASE),
    re.compile(r"#\s*Intentional pattern-scan\s*:", re.IGNORECASE),
    re.compile(r"#\s*allowlist\s*:", re.IGNORECASE),
    re.compile(r"#\s*scoped-scan\s*:", re.IGNORECASE),
    re.compile(r"#\s*Path-dependent test\s*:", re.IGNORECASE),
    re.compile(r"#\s*Static guard\s*:", re.IGNORECASE),
    re.compile(r"#\s*Cleanup opt-out\s*:", re.IGNORECASE),
]

_OPT_OUT_WINDOW = 5  # lines to look back from the flagged line

# ---------------------------------------------------------------------------
# Anti-pattern 1 detection patterns
# ---------------------------------------------------------------------------

# Matches the beginning of a for-loop over a scan function result.
# Python:   for X in EXPR.glob(   for X in EXPR.find(   for X in EXPR.matching(
#           for X in re.finditer(   for X in re.findall(   for X in find_vars(
# Bash:     for X in $(find ...   for X in $(glob ...
_AP1_FOR_LINE: re.Pattern[str] = re.compile(
    r"for\s+\w+[\w,\s]*\s+in\s+"  # for VAR in
    r"(?:"
    r"[^#\n]*\.\s*(?:glob|find|matching)\s*\("  # .glob(  .find(  .matching(
    r"|[^#\n]*re\.\s*(?:finditer|findall)\s*\("  # re.finditer(  re.findall(
    r"|[^#\n]*find_\w+\s*\("                    # find_variables(  find_cmds(
    r"|\$\s*\(\s*(?:find|glob)\s"               # $(find ...  $(glob ...
    r")"
)

# Matches an assert statement in the loop body (Python or bash-style)
_ASSERT_PATTERN: re.Pattern[str] = re.compile(
    r"(?:^\s*assert\b"           # Python assert
    r"|^\s*\[\[?\s*"             # bash [ or [[
    r"|^\s*test\s+"              # bash test builtin
    r")"
)

_AP1_BODY_WINDOW = 10  # lines after the for-line to search for assert

# ---------------------------------------------------------------------------
# Anti-pattern 2 detection patterns — Python (.py files)
# ---------------------------------------------------------------------------

# Path("/tmp/WORD") or Path('/tmp/WORD') — literal /tmp path as a filesystem root
# Any bare-/tmp path is flagged regardless of name prefix.  There are no
# name-based carve-outs: "/tmp/fake_xyz", "/tmp/stub_foo", and "/tmp/anything"
# are all flagged exactly the same.  Paths must route through
# PGAI_AGENT_KANBAN_TEMP_DIR.  Use the per-instance opt-out marker if a
# specific occurrence is legitimately data-only and creates no real path.
#
# The optional `[a-zA-Z]{0,2}` before the quote covers Python string prefixes
# (f, r, b, u, rb, br, fr, rf and their case variants) so that
# Path(f"/tmp/{name}") is caught exactly like Path("/tmp/name") — an
# f-string previously slipped past this check entirely because the prefix
# letter sat between the opening paren and the quote the old pattern
# required immediately after "\(\s*".
_AP2_PY_PATH_CONSTRUCTOR: re.Pattern[str] = re.compile(
    r"""Path\s*\(\s*[a-zA-Z]{0,2}["']/tmp/"""
)

# os.makedirs("/tmp/...) or os.mkdir("/tmp/...)
# Same zero-exemption policy: any /tmp path is flagged regardless of name.
# Same f-string-prefix coverage as _AP2_PY_PATH_CONSTRUCTOR above.
_AP2_PY_MAKEDIRS: re.Pattern[str] = re.compile(
    r"""os\s*\.\s*make?dirs?\s*\(\s*[a-zA-Z]{0,2}["']/tmp/"""
)

# Direct assignment of a /tmp path to a variable (Python or embedded shell):
#   TMPDIR = "/tmp/my_specific_dir"
#   test_dir = "/tmp/some_framework_dir"
#
# The ONLY exclusion is the self-reference to the configured temp-root basename
# (resolved from PGAI_AGENT_KANBAN_TEMP_DIR, defaulting to pgai_kanban_tmp) so
# the linter does not flag the temp-root constant definition itself (e.g. in
# temp.sh or the conftest resolver).  All other bare-/tmp names — including
# "fake*", "stub*", "test*", "mock*", "placeholder*", "example*", and "dummy*"
# — are flagged without exception.  The optional `[a-zA-Z]{0,2}` prefix (see
# _AP2_PY_PATH_CONSTRUCTOR above) catches f-string/raw-string assignments too.
# The trailing character class includes `{` so an f-string whose interpolation
# starts immediately after the literal segment (e.g. f"/tmp/{name}") is still
# matched — without it, the very first character after "/tmp/" is "{", which
# neither the negative lookahead nor a bare word-class character would reach.
_AP2_PY_VAR_ASSIGN: re.Pattern[str] = re.compile(
    r"""^\s*\w+\s*=\s*[a-zA-Z]{0,2}["']/tmp/(?!"""
    + re.escape(_TEMP_ROOT_BASENAME)
    + r"""["'])[\w/.{_-]"""
)

# Shell redirect to a bare /tmp path: > /tmp/..., 2> /tmp/..., >> /tmp/...,
# &> /tmp/..., and the no-space variants (>/tmp/..., 2>/tmp/..., etc.).
#
# This is the form that let pyc_err/bn_err slip past the guard — redirects
# create a file at the bare /tmp path but do not use mkdir, Path, or a
# variable assignment, so the existing patterns did not catch them.
#
# Flagged (shell files and embedded shell strings in Python files):
#   ... 2>/tmp/pyc_err       — common py_compile / bash -n error capture
#   ... >/tmp/out.txt        — stdout redirect
#   ... >> /tmp/log          — append redirect
#   ... &>/tmp/all.log       — combined redirect
#
# NOT flagged:
#   ... 2>"${PGAI_AGENT_KANBAN_TEMP_DIR}/pyc_err"   — routed form
#   ... >/dev/null                                    — null device
#
# The pattern matches the redirect operator optionally preceded by a file
# descriptor digit (1, 2) or & for the combined form, followed by optional
# whitespace, then the literal string /tmp/.
_AP2_SH_REDIRECT: re.Pattern[str] = re.compile(
    r"""[12&]?>>?\s*/tmp/[\w._-]"""
)

# Python open() call writing to a bare /tmp path — the Python equivalent of a
# shell redirect.  Catches forms like open("/tmp/foo", "w") or
# open('/tmp/err', 'a') that create files directly in /tmp without routing
# through PGAI_AGENT_KANBAN_TEMP_DIR.
#
# The negative lookahead excludes the configured temp-root basename (resolved
# from PGAI_AGENT_KANBAN_TEMP_DIR, defaulting to pgai_kanban_tmp) for
# consistency with the other patterns.
#
# Flagged:
#   open("/tmp/pyc_err", "w")         — write to bare /tmp
#   open('/tmp/out', 'a')             — append to bare /tmp
#
# NOT flagged:
#   open(os.environ["PGAI_AGENT_KANBAN_TEMP_DIR"] + "/err", "w")  — routed
#   open("/dev/null", "w")            — null device
#
# The optional `[a-zA-Z]{0,2}` prefix (see _AP2_PY_PATH_CONSTRUCTOR above)
# catches open(f"/tmp/...", "w") the same way plain-string calls are caught.
# The trailing character class includes `{` for the same reason documented on
# _AP2_PY_VAR_ASSIGN — an f-string interpolation that starts immediately after
# the literal segment (open(f"/tmp/{name}", "w")) must still be reachable.
_AP2_PY_OPEN_TMP: re.Pattern[str] = re.compile(
    r"""open\s*\(\s*[a-zA-Z]{0,2}["']/tmp/(?!"""
    + re.escape(_TEMP_ROOT_BASENAME)
    + r"""["'])[\w/.{_-]"""
)

# Bare $(mktemp) inside an embedded shell string in a Python test file.
# Matches lines in .py files that contain $(mktemp) or $(mktemp PREFIX) where
# the call has no flags (no -p, no -d).  This catches the anti-pattern where a
# test builds a shell heredoc/snippet with a bare mktemp that bypasses the
# framework temp dir.
#
# The negative lookahead (?!\s+-) prevents flagging $(mktemp -p ...) or
# $(mktemp -d ...) — those have a flag that may route to a specific path.
# See _AP2_PY_EMBEDDED_SHELL_BARE_MKTEMP_D for the bare $(mktemp -d) variant.
_AP2_PY_EMBEDDED_SHELL_BARE_MKTEMP: re.Pattern[str] = re.compile(
    r"""\$\(mktemp\b(?!\s+-)"""
)

# Bare $(mktemp -d) inside an embedded shell string in a Python test file.
# The -d flag requests a temporary DIRECTORY.  When no path argument or -p
# flag is supplied after -d, the directory still lands directly in /tmp
# rather than under PGAI_AGENT_KANBAN_TEMP_DIR.
#
# Flagged:
#   $(mktemp -d)              — bare directory form, lands in /tmp
#
# NOT flagged (routed forms):
#   $(mktemp -d -p "${PGAI_...}")        — -p routes to a specific directory
#   $(mktemp -d "${root}/prefix.XXXXXX") — explicit path arg routes correctly
#
# The negative lookahead (?!\s+[-"'/\$\w]) prevents matching when -d is
# followed by another flag (-) or an explicit path argument.
_AP2_PY_EMBEDDED_SHELL_BARE_MKTEMP_D: re.Pattern[str] = re.compile(
    r"""\$\(mktemp\s+-d\b(?!\s+[-"'/\$\w])"""
)

# tempfile.mkdtemp() without a ``dir=`` keyword argument in Python test code.
# When called without ``dir=``, mkdtemp() lands directly in /tmp rather than
# under PGAI_AGENT_KANBAN_TEMP_DIR.
#
# Flagged:
#   tempfile.mkdtemp()                   — lands in /tmp
#   tempfile.mkdtemp(suffix="_foo")      — still no dir=, lands in /tmp
#   tempfile.mkdtemp(prefix="pgai_")     — still no dir=, lands in /tmp
#
# NOT flagged (routed forms):
#   tempfile.mkdtemp(dir=...)            — explicit dir= routes correctly
#   tempfile.mkdtemp(..., dir=...)       — same
#
# The pattern matches `tempfile.mkdtemp(` where the rest of the argument list
# (up to the end of the line) does NOT contain `dir=`.  We use a negative
# lookahead that scans to the end of the line for `dir\s*=`.
_AP2_PY_MKDTEMP_NO_DIR: re.Pattern[str] = re.compile(
    r"""tempfile\s*\.\s*mkdtemp\s*\((?![^)]*\bdir\s*=)"""
)

# Bare mkdtemp() without a ``dir=`` keyword argument, used when mkdtemp is
# imported directly via ``from tempfile import mkdtemp``.  Without ``dir=``,
# the call still lands in /tmp rather than under PGAI_AGENT_KANBAN_TEMP_DIR.
#
# Flagged:
#   mkdtemp()                  — bare import, no dir=, lands in /tmp
#   mkdtemp(suffix="_foo")     — still no dir=, lands in /tmp
#   mkdtemp(prefix="pgai_")   — still no dir=, lands in /tmp
#
# NOT flagged:
#   mkdtemp(dir=...)           — explicit dir= routes correctly
#   tempfile.mkdtemp(...)      — handled by _AP2_PY_MKDTEMP_NO_DIR above
#
# The negative lookbehind ``(?<!tempfile\s*\.\s*)`` ensures this pattern does
# not double-flag lines already covered by _AP2_PY_MKDTEMP_NO_DIR.  The word
# boundary ``\bmkdtemp`` prevents matching substrings like ``pgai_mkdtemp``.
_AP2_PY_BARE_MKDTEMP_NO_DIR: re.Pattern[str] = re.compile(
    r"""(?<!\.)\bmkdtemp\s*\((?![^)]*\bdir\s*=)"""
)

# tempfile.TemporaryDirectory() without a ``dir=`` keyword argument in Python
# test code.  Without ``dir=``, TemporaryDirectory() uses Python's default
# temp root (usually /tmp) rather than PGAI_AGENT_KANBAN_TEMP_DIR.
#
# Flagged:
#   tempfile.TemporaryDirectory()        — lands in /tmp
#   tempfile.TemporaryDirectory(prefix=) — still no dir=, lands in /tmp
#
# NOT flagged (routed forms):
#   tempfile.TemporaryDirectory(dir=...) — explicit dir= routes correctly
#
# The pattern matches `TemporaryDirectory(` where the argument list (up to
# end of line or closing paren) does NOT contain `dir=`.  Single-line calls
# are the common case; multi-line calls with dir= on a continuation line are
# out of scope for this simple scan (add an opt-out comment if needed).
_AP2_PY_TMPDIR_NO_DIR: re.Pattern[str] = re.compile(
    r"""TemporaryDirectory\s*\((?![^)]*\bdir\s*=)"""
)

# ---------------------------------------------------------------------------
# Conftest whitelist — files that define the wrapper, not callers of it
# ---------------------------------------------------------------------------

# team/tests/conftest.py is whitelisted from the un-rooted mkdtemp/
# TemporaryDirectory check because it IS the framework wrapper.  The
# ``pgai_mkdtemp()`` helper defined there calls ``tempfile.mkdtemp(dir=...)``
# with an explicit dir= argument by design (that is the point of the helper).
# Callers UNDER team/tests/ are the targets of this lint rule; the helper
# itself must not be flagged.  Any new call added to conftest.py that lacks
# ``dir=`` is a genuine bug — add a per-instance opt-out comment instead of
# loosening this whitelist.
_AP2_CONFTEST_WHITELIST: frozenset[str] = frozenset({"conftest.py"})

# ---------------------------------------------------------------------------
# Bare-/tmp subject-matter whitelist — files that behaviorally test a real
# bare-/tmp scan and cannot route the path they plant through either
# sanctioned mechanism without testing nothing
# ---------------------------------------------------------------------------
#
# team/tests/unit/test_wake_bracket_litter_check.py exercises
# wake_tmp_litter_check_and_report (scripts/lib/temp.sh), which scans REAL
# top-level bare /tmp entries and deliberately EXCLUDES both the framework
# temp root (PGAI_AGENT_KANBAN_TEMP_DIR) and pytest's own tmp_path (which
# nests under /tmp/pytest-of-*, itself on the check's own allowlist) from
# that scan.  A fixture proving the check detects real litter therefore
# cannot route through either sanctioned mechanism — landing directly under
# bare /tmp is this module's subject matter, not a caller bypassing the
# temp-path contract.  Every such fixture generates a fresh, uuid4-suffixed
# name (see the file's own bare-tmp-probe helper) so concurrent runs, and
# repeat runs against an already-dirty host, never collide on the same path.
#
# This is DELIBERATELY narrower than _AP2_CONFTEST_WHITELIST: it exempts
# ONLY the Path()/open() bare-/tmp constructor checks (_AP2_PY_PATH_CONSTRUCTOR
# and _AP2_PY_OPEN_TMP) for the listed filename.  Every other AP2 pattern —
# variable assignment, os.makedirs, bare mktemp, TemporaryDirectory without
# dir= — still applies normally to these files.  A new bare-/tmp construct
# added to a whitelisted file outside its bare-tmp-probe helper is a genuine
# finding; keep this set to the fewest files possible and re-justify here
# before adding another.
_AP2_BARE_TMP_SUBJECT_MATTER_WHITELIST: frozenset[str] = frozenset({
    "test_wake_bracket_litter_check.py",
})

# ---------------------------------------------------------------------------
# Anti-pattern 2 detection patterns — Bash (.sh files)
# ---------------------------------------------------------------------------

# mkdir -p /tmp/WORD or mkdir /tmp/WORD (real directory creation)
# Any /tmp path is flagged regardless of name prefix — there is no name-based
# carve-out.  Fixtures must route through pgai_mktemp_d (rooted under
# PGAI_AGENT_KANBAN_TEMP_DIR); naming a fixture directory "fake*" or "stub*"
# does not exempt it from the bare-/tmp contract.
_AP2_SH_MKDIR: re.Pattern[str] = re.compile(
    r"""mkdir\s+(?:-p\s+)?["']?/tmp/[\w._-]+"""
)

# Variable assignment: VAR=/tmp/WORD (without mktemp)
# Same policy: any bare-/tmp assignment is flagged regardless of name prefix.
_AP2_SH_VAR_ASSIGN: re.Pattern[str] = re.compile(
    r"""^\s*\w+=['"]?/tmp/[\w._-]+['"]?"""
)

# $HOME/WORD or /home/USERNAME/WORD used in mkdir context
_AP2_SH_HOME_MKDIR: re.Pattern[str] = re.compile(
    r"""mkdir\s+(?:-p\s+)?["']?(?:\$HOME|/home/[a-z][a-z0-9_-]*)"""
)

# Bare $(mktemp) in a shell script — no flags, no path argument.
# "Bare" means mktemp is called with neither a -p flag nor an explicit path
# argument, so the temp file lands directly in /tmp rather than under the
# framework temp root.
#
# Allowed forms (NOT flagged):
#   $(mktemp -p "${PGAI_...}")          — -p routes to a specific directory
#   $(mktemp "${root}/prefix.XXXXXX")   — explicit path argument routes correctly
#
# The negative lookahead (?!\s+[-"'/\$\w]) prevents matching when mktemp is
# followed by a flag (- ...) or a quoted/unquoted path argument.
_AP2_SH_BARE_MKTEMP: re.Pattern[str] = re.compile(
    r"""\$\(mktemp\b(?!\s+[-"'/\$\w])"""
)

# Bare $(mktemp -d) in a shell script — directory flag only, no -p flag,
# no explicit path argument.  The directory still lands in /tmp.
#
# Allowed forms (NOT flagged):
#   $(mktemp -d -p "${PGAI_...}")        — -p routes to a specific directory
#   $(mktemp -d "${root}/prefix.XXXXXX") — explicit path argument routes correctly
#
# The negative lookahead (?!\s+[-"'/\$\w]) prevents matching when -d is
# followed by another flag or an explicit path argument.
_AP2_SH_BARE_MKTEMP_D: re.Pattern[str] = re.compile(
    r"""\$\(mktemp\s+-d\b(?!\s+[-"'/\$\w])"""
)

# ---------------------------------------------------------------------------
# Anti-pattern 3 detection — hardcoded /tmp/pgai_kanban_tmp literal
# ---------------------------------------------------------------------------
#
# AP3 flags any occurrence of the literal "/tmp/pgai_kanban_tmp" in source
# files, indicating a caller that bypasses the temp.sh resolver rather than
# routing through PGAI_AGENT_KANBAN_TEMP_DIR.  It keeps every caller on the
# resolver: no literal may appear in scripts or tests outside the permitted
# locations below.
#
# The ONLY permitted locations are:
#   1. team/scripts/lib/temp.sh          — the resolver's own hard fallback
#   2. team/scripts/lint_test_anti_patterns.py  — this file's regex patterns
#
# Those two files are whitelisted by filename.  All other files that contain
# the literal (except when an opt-out marker is present) produce an AP3 finding.
#
# Flagged (any file except the two whitelisted names):
#   dir="/tmp/pgai_kanban_tmp"                  # variable assignment
#   TEMP="${PGAI_AGENT_KANBAN_TEMP_DIR:-/tmp/pgai_kanban_tmp}"  # fallback in caller
#   # fallback /tmp/pgai_kanban_tmp             # even in a comment
#
# NOT flagged:
#   "/tmp/" + "pgai_kanban_tmp"   # string concatenation — literal does not appear
#   pgai_kanban_tmp               # subdir name alone (no /tmp/ prefix)
#   /home/user/pgai_kanban_tmp    # different root (not /tmp/)
#
# Stable error code: AP3

_AP3_LITERAL_PATTERN: re.Pattern[str] = re.compile(
    r"/tmp/pgai_kanban_tmp"
)

# Files whitelisted from AP3 — they ARE the resolver, the lint rule itself,
# or the unit test that exercises the AP3 rule (which must contain the literal
# as test data / documentation; analogous to how conftest.py is whitelisted
# from AP2's un-rooted mkdtemp check).
_AP3_FILE_WHITELIST: frozenset[str] = frozenset({
    "temp.sh",
    "lint_test_anti_patterns.py",
    "test_lint_hardcoded_tmp_guard.py",
    # test_lint_tempdir_env_resolution.py exercises the temp-dir basename
    # resolution logic.  Its docstrings and test data necessarily reference the
    # default basename as documentation; it is whitelisted for the same reason
    # as test_lint_hardcoded_tmp_guard.py.
    "test_lint_tempdir_env_resolution.py",
    # conftest.py defines _PGAI_DEFAULT_TEMP_ROOT — the Python-side resolver
    # fallback that mirrors temp.sh's shell-layer hard fallback.  Both serve
    # the identical purpose (ensuring the no-env-var branch never lands in
    # bare /tmp) and are equally legitimate.
    "conftest.py",
})

# ---------------------------------------------------------------------------
# Anti-pattern 6 detection — bug-provenance test function names
# ---------------------------------------------------------------------------
#
# AP6 flags any ``def test_…`` function whose name contains the token ``bug``
# (case-insensitive) immediately followed by one or more decimal digits.  This
# pattern captures all common provenance-naming forms:
#
#   def test_read_state_field_stops_at_horizontal_rule_bug0248  — lowercase, no hyphen
#   def test_parse_BUG_0009                                     — uppercase, underscore
#   def test_parse_Bug123                                       — mixed case
#
# The regex deliberately requires that ``bug`` be followed immediately by a
# digit (``\d+``) so that behavioral names where "bug" describes a real concept
# are not flagged:
#
#   test_returns_bug_report       — "bug" followed by "_", not a digit → OK
#   test_handle_bug_tracker_link  — "bug" followed by "_", not a digit → OK
#   test_returns_bug0001          — "bug" immediately followed by "0" → FLAGGED
#
# Stable error code: AP6
#
# This check applies only to test files (Python), not to runtime scripts.

_AP6_PROV_NAME: re.Pattern[str] = re.compile(
    r"^\s*def\s+(test_\w*bug\d+\w*)\s*\(",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Anti-pattern 7 detection — golden comparison + inherited subprocess env
# ---------------------------------------------------------------------------
#
# The pairing this check targets is narrower than either half alone:
#
#   (a) golden-comparison marker -- a "golden"-named identifier, or a literal
#       "golden" path segment, assigned as part of a pathlib `/` join or an
#       os.path.join() call.  This is the naming convention the tree's own
#       golden-comparison harnesses already use (`_GOLDEN_DIR = ... /
#       "fixtures" / "engine-baseline"`, `_FIXTURE_DIR = ... / "fixtures" /
#       "golden"`), not a bare occurrence of the word "golden" anywhere in
#       the file -- the word alone is also idiomatic test-naming shorthand
#       for "the happy path" (`test_golden_path_...`), which is unrelated to
#       a committed-fixture comparison and must not trip this check.
#
#   (b) inherited-environment marker -- ``dict(os.environ)`` or
#       ``os.environ.copy()``, the same two forms a tree-wide survey of
#       team/tests/ found at well over a hundred call sites.  Most of those
#       sites are correct as written; this check only turns that signal into
#       a finding when marker (a) is ALSO present somewhere in the same file.
#
# Both markers are evaluated at file scope, not per-function: the check does
# not attempt to prove the two constructs sit in the same test or the same
# call chain.  That is a deliberate width choice, not an oversight -- a file
# that carries a golden-fixture comparison anywhere already has a verdict
# that depends on a committed artifact, so an inherited-environment
# subprocess anywhere else in that same file is worth a second look even
# when today's read of the code shows the two are unrelated.  Because this
# is a quality-class finding (warn, record, never block), the cost of that
# width is a nudge to re-check, not a build failure.  A file with the
# marker-(a) shape and no marker-(b) construct, or vice versa, is silent --
# proven by the two negative-control fixtures accompanying this check.
_AP7_GOLDEN_NAME_ASSIGN: re.Pattern[str] = re.compile(
    r"""[A-Za-z_]*golden[A-Za-z_]*\s*=\s*.*(?:/\s*["']|os\.path\.join\()""",
    re.IGNORECASE,
)

_AP7_GOLDEN_PATH_LITERAL: re.Pattern[str] = re.compile(
    r"""(?:/\s*["']golden["']|os\.path\.join\([^)]*["']golden["'])""",
    re.IGNORECASE,
)

_AP7_ENV_INHERIT: re.Pattern[str] = re.compile(
    r"""dict\(\s*os\.environ\s*\)|os\.environ\.copy\(\)"""
)

# ---------------------------------------------------------------------------
# Anti-pattern 8 detection — uncontrolled absolute-path substring assertion
# ---------------------------------------------------------------------------
#
# AP8 flags ``assert "<literal>" (not )?in <subject>`` where <literal> both
# starts AND ends with "/" -- a path-segment shape ("/projects/", "/tmp/",
# "/home/") searched as a free-floating substring of an absolute path the
# test does not fully control.  The literal's containing prefix is whatever
# the ambient scratch root happens to be (PGAI_AGENT_KANBAN_TEMP_DIR /
# pytest's tmp_path), which the multi-project layout can itself route
# through a "projects" segment -- so the assertion's verdict becomes a
# property of the invoking environment, not of what the routing code under
# test computed.
#
# The "starts and ends with a slash" requirement is the deliberate narrowing
# that keeps this check from firing on the hundreds of ordinary, correct
# containment assertions in the tree that check captured stdout/stderr text
# against an expected substring (`assert "1/3" in captured.out`, `assert
# "rc/v8.8.8" in result.stderr`) -- none of those literals both open and
# close with "/", so none of them match.
#
# Flagged:
#   assert "/projects/" not in resolved_path
#   assert "/tmp/" in some_absolute_path
#
# NOT flagged (no leading/trailing slash on the literal, or not a
# containment assertion against a bare-name subject):
#   assert "1/3" in captured.out
#   assert "/approvals" in scratch["paths"]
#   assert resolved_path.startswith(expected_prefix)
#
# Stable error code: AP8
#
# This check applies only to Python test files, not to runtime scripts.

_AP8_ABS_PATH_SUBSTRING: re.Pattern[str] = re.compile(
    r"""assert\s+[a-zA-Z]{0,2}(?P<quote>["'])(?P<literal>/[^"']*/)(?P=quote)"""
    r"""\s+(?P<neg>not\s+)?in\s+(?P<subject>\w[\w.\[\]]*)"""
)

# ---------------------------------------------------------------------------
# Shared utility
# ---------------------------------------------------------------------------

SOP_CITATION = (
    "See roles/TESTER.md 'Test Authoring Guidelines' for the preferred "
    "pattern and opt-out instructions."
)


def _has_opt_out(lines: list[str], flagged_lineno: int) -> bool:
    """Return True if any opt-out marker appears in the _OPT_OUT_WINDOW lines
    before *flagged_lineno* (1-based).
    """
    start = max(0, flagged_lineno - 1 - _OPT_OUT_WINDOW)
    end = flagged_lineno - 1  # exclusive; don't include the flagged line itself
    window = lines[start:end]
    for line in window:
        for pat in _OPT_OUT_PATTERNS:
            if pat.search(line):
                return True
    return False


def _finding(filepath: Path, lineno: int, category: str, detail: str) -> str:
    """Format a single lint finding message."""
    return (
        f"{filepath}:{lineno}: [{category}] {detail}\n"
        f"    {SOP_CITATION}"
    )


# ---------------------------------------------------------------------------
# Per-file checkers
# ---------------------------------------------------------------------------


def _is_comprehension_for(line: str) -> bool:
    """Return True if the 'for' keyword in *line* is part of a comprehension.

    A comprehension for is one that appears inside brackets/braces on the
    same line (e.g. ``{f.name for f in xs}`` or ``[x for x in xs]``), or
    one where the line contains an assignment with a comprehension on the
    right-hand side.

    We detect this by checking whether the line (stripped of leading space)
    does NOT start with 'for' — which would indicate a for-statement. If the
    'for' is embedded elsewhere in the line (inside ``{}``, ``[]``, ``()``,
    or after ``=``), it is part of a comprehension or generator expression,
    not a statement.
    """
    stripped = line.lstrip()
    # A for-statement starts with 'for' (possibly preceded by label/async)
    if stripped.startswith("for ") or stripped.startswith("async for "):
        return False
    # Otherwise the 'for' is embedded — it is part of a comprehension
    return True


def _for_statement_indent(line: str) -> int:
    """Return the indentation level (number of leading spaces) of *line*."""
    return len(line) - len(line.lstrip())


def _check_ap1(filepath: Path, lines: list[str]) -> list[str]:
    """Detect anti-pattern 1: pattern-scan assertion loops.

    A flagged line is a 'for X in SCAN()' *statement* (not a comprehension)
    where:
    - SCAN is a glob/find/matching/finditer/findall/find_* call
    - The loop body (next _AP1_BODY_WINDOW lines) contains an assert statement
      that is MORE indented than the for-line (i.e., inside the loop body)
    - No opt-out marker is present in the preceding _OPT_OUT_WINDOW lines

    Two common false-positive sources are explicitly excluded:
    1. Comprehension ``for`` clauses (e.g. ``{f.name for f in xs}``)
    2. assert statements that appear AFTER the loop ends (same or less indent
       than the for-line) are not counted as loop-body asserts.
    """
    findings: list[str] = []

    for idx, line in enumerate(lines):
        lineno = idx + 1  # 1-based

        # Skip comment-only lines
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue

        if not _AP1_FOR_LINE.search(line):
            continue

        # Exclude comprehension 'for' clauses — they are not for-statements
        if _is_comprehension_for(line):
            continue

        # Check if opt-out marker is present
        if _has_opt_out(lines, lineno):
            continue

        # The indentation of the for-line; loop body must be more indented
        for_indent = _for_statement_indent(line)

        # Look for assert IN THE LOOP BODY (indented further than the for-line)
        body_end = min(len(lines), idx + 1 + _AP1_BODY_WINDOW)
        body_lines = lines[idx + 1 : body_end]

        has_body_assert = False
        for body_line in body_lines:
            body_stripped = body_line.lstrip()
            if not body_stripped or body_stripped.startswith("#"):
                continue  # blank or comment line — skip
            body_indent = _for_statement_indent(body_line)
            if body_indent <= for_indent:
                # We've exited the loop body; stop scanning
                break
            if _ASSERT_PATTERN.search(body_line):
                has_body_assert = True
                break

        if not has_body_assert:
            continue

        detail = (
            "Pattern-scan assertion loop detected — "
            "loops over .glob()/.find()/.matching()/re.finditer()/find_*() "
            "with assert in body. "
            "Replace scan with an explicit allowlist, or add a marker comment "
            "to opt out: '# anti-pattern-allowlist: 1 (justification: ...)'"
        )
        findings.append(_finding(filepath, lineno, "AP1", detail))

    return findings


def _check_ap2_py(filepath: Path, lines: list[str]) -> list[str]:
    """Detect anti-pattern 2 in Python files: hardcoded /tmp paths.

    Files listed in _AP2_CONFTEST_WHITELIST are exempted from the
    mkdtemp/TemporaryDirectory-without-dir= checks because they ARE the
    framework wrappers that define the correct calling convention.  Their
    internal bare mkdtemp calls (which always pass dir=) must not be
    flagged; only the callers under team/tests/ are targeted by this lint.

    Files listed in _AP2_BARE_TMP_SUBJECT_MATTER_WHITELIST lose only the
    Path()/open() bare-/tmp constructor checks — they behaviorally test a
    real bare-/tmp scan that cannot be exercised through the sanctioned
    routing mechanisms without testing nothing. See that set's own comment
    for the full reasoning.
    """
    findings: list[str] = []

    # Whitelist: conftest.py defines the pgai_mkdtemp() wrapper and therefore
    # legitimately calls tempfile.mkdtemp() internally with dir= by design.
    # Flagging the helper itself would produce a false positive.  Callers that
    # add a new bare call without dir= should use a per-instance opt-out instead.
    if filepath.name in _AP2_CONFTEST_WHITELIST:
        return findings

    patterns_and_messages: list[tuple[re.Pattern[str], str]] = [
        (
            _AP2_PY_PATH_CONSTRUCTOR,
            "Hardcoded /tmp path in Path() constructor. "
            "Use pytest's tmp_path fixture or "
            "pathlib.Path(os.environ.get('PGAI_AGENT_KANBAN_TEMP_DIR') or '/tmp'). "
            "Opt out with: '# anti-pattern-allowlist: 2 (justification: ...)'",
        ),
        (
            _AP2_PY_MAKEDIRS,
            "Hardcoded /tmp path in os.makedirs/os.mkdir. "
            "Use tmp_path fixture or PGAI_AGENT_KANBAN_TEMP_DIR env var. "
            "Opt out with: '# anti-pattern-allowlist: 2 (justification: ...)'",
        ),
        (
            _AP2_PY_VAR_ASSIGN,
            "Hardcoded /tmp path assigned to variable. "
            "Use tmp_path fixture or PGAI_AGENT_KANBAN_TEMP_DIR env var. "
            "Opt out with: '# anti-pattern-allowlist: 2 (justification: ...)'",
        ),
        (
            _AP2_PY_EMBEDDED_SHELL_BARE_MKTEMP,
            "Bare $(mktemp) (no -p flag) in embedded shell string. "
            "This creates a temp file directly in /tmp rather than under "
            "PGAI_AGENT_KANBAN_TEMP_DIR. "
            "Fix: source team/scripts/lib/temp.sh and use $(pgai_mktemp PREFIX) "
            "or $(mktemp -p \"<sandboxed-dir>\"). "
            "Opt out with: '# anti-pattern-allowlist: 2 (justification: ...)'",
        ),
        (
            _AP2_PY_EMBEDDED_SHELL_BARE_MKTEMP_D,
            "Bare $(mktemp -d) (no path argument or -p flag) in embedded shell string. "
            "This creates a temp directory directly in /tmp rather than under "
            "PGAI_AGENT_KANBAN_TEMP_DIR. "
            "Fix: source team/scripts/lib/temp.sh and use $(pgai_mktemp_d PREFIX). "
            "Opt out with: '# anti-pattern-allowlist: 2 (justification: ...)'",
        ),
        (
            _AP2_PY_MKDTEMP_NO_DIR,
            "tempfile.mkdtemp() called without dir= argument. "
            "This creates a directory directly in /tmp rather than under "
            "PGAI_AGENT_KANBAN_TEMP_DIR. "
            "Fix: tempfile.mkdtemp(dir=<framework_temp_dir>) where the dir is "
            "resolved via PGAI_AGENT_KANBAN_TEMP_DIR (or use pytest's tmp_path). "
            "Opt out with: '# anti-pattern-allowlist: 2 (justification: ...)'",
        ),
        (
            _AP2_PY_TMPDIR_NO_DIR,
            "tempfile.TemporaryDirectory() called without dir= argument. "
            "This creates a directory directly in /tmp rather than under "
            "PGAI_AGENT_KANBAN_TEMP_DIR. "
            "Fix: tempfile.TemporaryDirectory(dir=<framework_temp_dir>) where "
            "the dir is resolved via PGAI_AGENT_KANBAN_TEMP_DIR "
            "(or use pytest's tmp_path fixture). "
            "Opt out with: '# anti-pattern-allowlist: 2 (justification: ...)'",
        ),
        (
            _AP2_PY_BARE_MKDTEMP_NO_DIR,
            "Bare mkdtemp() (imported via 'from tempfile import mkdtemp') "
            "called without dir= argument. "
            "This creates a directory directly in /tmp rather than under "
            "PGAI_AGENT_KANBAN_TEMP_DIR. "
            "Fix: mkdtemp(dir=<framework_temp_dir>) where the dir is resolved "
            "via PGAI_AGENT_KANBAN_TEMP_DIR (or use pytest's tmp_path fixture). "
            "Opt out with: '# anti-pattern-allowlist: 2 (justification: ...)'",
        ),
        (
            _AP2_SH_REDIRECT,
            "Shell redirect to a bare /tmp path (> /tmp/..., 2> /tmp/..., "
            ">> /tmp/..., &> /tmp/...) in embedded shell string. "
            "This writes a file directly in /tmp, bypassing "
            "PGAI_AGENT_KANBAN_TEMP_DIR. "
            "Fix: redirect to a path under $(pgai_temp_dir) or "
            "PGAI_AGENT_KANBAN_TEMP_DIR. "
            "Opt out with: '# anti-pattern-allowlist: 2 (justification: ...)'",
        ),
        (
            _AP2_PY_OPEN_TMP,
            "Python open() call writing to a bare /tmp path. "
            "This creates a file directly in /tmp rather than under "
            "PGAI_AGENT_KANBAN_TEMP_DIR. "
            "Fix: open a path under PGAI_AGENT_KANBAN_TEMP_DIR instead. "
            "Opt out with: '# anti-pattern-allowlist: 2 (justification: ...)'",
        ),
    ]

    # Narrow allowance: a file on _AP2_BARE_TMP_SUBJECT_MATTER_WHITELIST loses
    # ONLY the Path()/open() bare-/tmp constructor checks — see that set's own
    # docstring-comment for the full reasoning. Every other AP2 pattern in
    # patterns_and_messages (var assignment, makedirs, bare mktemp,
    # TemporaryDirectory-without-dir=) still applies to the file unchanged.
    if filepath.name in _AP2_BARE_TMP_SUBJECT_MATTER_WHITELIST:
        patterns_and_messages = [
            (pat, message)
            for pat, message in patterns_and_messages
            if pat not in (_AP2_PY_PATH_CONSTRUCTOR, _AP2_PY_OPEN_TMP)
        ]

    for idx, line in enumerate(lines):
        lineno = idx + 1
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue

        for pat, message in patterns_and_messages:
            if pat.search(line):
                if _has_opt_out(lines, lineno):
                    break  # opt-out applies to this line
                findings.append(_finding(filepath, lineno, "AP2", message))
                break  # one finding per line is enough

    return findings


def _check_ap2_sh(filepath: Path, lines: list[str]) -> list[str]:
    """Detect anti-pattern 2 in shell files: hardcoded /tmp or $HOME paths,
    and bare mktemp / mktemp -d calls that bypass the framework temp-dir helpers.
    """
    findings: list[str] = []

    patterns_and_messages: list[tuple[re.Pattern[str], str]] = [
        (
            _AP2_SH_MKDIR,
            "Hardcoded /tmp path in mkdir. "
            "Use mktemp -d -p \"${PGAI_AGENT_KANBAN_TEMP_DIR:-/tmp}\" test.XXXXXX "
            "plus a trap EXIT for cleanup. "
            "Opt out with: '# anti-pattern-allowlist: 2 (justification: ...)'",
        ),
        (
            _AP2_SH_VAR_ASSIGN,
            "Hardcoded /tmp path in variable assignment. "
            "Use TMPDIR=$(mktemp -d -p \"${PGAI_AGENT_KANBAN_TEMP_DIR:-/tmp}\" test.XXXXXX). "
            "Opt out with: '# anti-pattern-allowlist: 2 (justification: ...)'",
        ),
        (
            _AP2_SH_HOME_MKDIR,
            "Hardcoded $HOME or /home/user path in mkdir. "
            "Use mktemp -d or PGAI_AGENT_KANBAN_TEMP_DIR. "
            "Opt out with: '# anti-pattern-allowlist: 2 (justification: ...)'",
        ),
        (
            _AP2_SH_BARE_MKTEMP,
            "Bare $(mktemp) with no path argument creates a temp file directly in /tmp, "
            "bypassing the framework temp-dir root (PGAI_AGENT_KANBAN_TEMP_DIR). "
            "Fix: source team/scripts/lib/temp.sh and use pgai_mktemp PREFIX. "
            "Opt out with: '# anti-pattern-allowlist: 2 (justification: ...)'",
        ),
        (
            _AP2_SH_BARE_MKTEMP_D,
            "Bare $(mktemp -d) with no path argument creates a temp directory directly "
            "in /tmp, bypassing the framework temp-dir root (PGAI_AGENT_KANBAN_TEMP_DIR). "
            "Fix: source team/scripts/lib/temp.sh and use pgai_mktemp_d PREFIX. "
            "Opt out with: '# anti-pattern-allowlist: 2 (justification: ...)'",
        ),
        (
            _AP2_SH_REDIRECT,
            "Shell redirect to a bare /tmp path (> /tmp/..., 2> /tmp/..., "
            ">> /tmp/..., &> /tmp/...). "
            "This writes a file directly in /tmp, bypassing "
            "PGAI_AGENT_KANBAN_TEMP_DIR. "
            "Fix: redirect to a path under $(pgai_temp_dir) or "
            "PGAI_AGENT_KANBAN_TEMP_DIR. "
            "Opt out with: '# anti-pattern-allowlist: 2 (justification: ...)'",
        ),
    ]

    for idx, line in enumerate(lines):
        lineno = idx + 1
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue

        for pat, message in patterns_and_messages:
            if pat.search(line):
                if _has_opt_out(lines, lineno):
                    break
                findings.append(_finding(filepath, lineno, "AP2", message))
                break

    return findings


def _check_ap3(filepath: Path, lines: list[str]) -> list[str]:
    """Detect anti-pattern 3: hardcoded /tmp/pgai_kanban_tmp literal.

    Flags any line that contains the literal string ``/tmp/pgai_kanban_tmp``
    in files that are not the resolver (temp.sh) or the lint script itself
    (lint_test_anti_patterns.py).  It keeps every caller on the temp.sh
    resolver: no literal may appear anywhere outside those two files.

    Applies to both Python (.py) and shell (.sh) files.  Also flags comments
    and string data — any occurrence of the literal is a potential regression.

    Whitelisted filenames (skipped entirely):
      - temp.sh                     — the resolver's own hard fallback
      - lint_test_anti_patterns.py  — this file's regex pattern definitions

    Per-instance opt-out: place an allowed opt-out marker comment within 5
    lines BEFORE the flagged line.
    """
    # Whitelist: the resolver and this lint script are the only permitted
    # locations for the literal.
    if filepath.name in _AP3_FILE_WHITELIST:
        return []

    findings: list[str] = []

    for idx, line in enumerate(lines):
        lineno = idx + 1
        if not _AP3_LITERAL_PATTERN.search(line):
            continue
        if _has_opt_out(lines, lineno):
            continue
        detail = (
            "Hardcoded /tmp/pgai_kanban_tmp literal detected. "
            "This literal must not appear outside team/scripts/lib/temp.sh "
            "(the resolver's documented fallback). "
            "All other callers must route through the resolver: "
            "source team/scripts/lib/temp.sh and call $(pgai_temp_dir) or "
            "use PGAI_AGENT_KANBAN_TEMP_DIR. "
            "Opt out with: '# anti-pattern-allowlist: 2 (justification: ...)'"
        )
        findings.append(_finding(filepath, lineno, "AP3", detail))

    return findings


def _check_ap6(filepath: Path, lines: list[str]) -> list[str]:
    """Detect anti-pattern 6 in Python test files: bug-provenance test names.

    A test function name carries bug-provenance when the identifier contains
    ``bug`` (case-insensitive) immediately followed by one or more digits
    (e.g. ``test_foo_bug0248``, ``test_bar_BUG_0009``).  This form encodes
    the bug ID that motivated the test into the function name, which violates
    the SOP Anti-pattern 6 naming rule: test names must describe behavior,
    not history.

    Names where "bug" is followed by a non-digit character (e.g.
    ``test_returns_bug_report``) are not flagged — "bug" is a behavioral
    descriptor there, not a provenance token.

    Applies only to Python test files; runtime scripts are skipped.
    """
    findings: list[str] = []

    for idx, line in enumerate(lines):
        lineno = idx + 1

        m = _AP6_PROV_NAME.search(line)
        if not m:
            continue

        if _has_opt_out(lines, lineno):
            continue

        func_name = m.group(1)
        detail = (
            f"Test function name '{func_name}' encodes bug provenance. "
            "Rename to describe the behavior under test "
            "(e.g. 'test_read_state_field_treats_horizontal_rule_as_section_boundary'). "
            "Bug IDs belong in the commit message and git history, not in the "
            "function name. "
            "Opt out with: '# anti-pattern-allowlist: 6 (justification: ...)'"
        )
        findings.append(_finding(filepath, lineno, "AP6", detail))

    return findings


def _has_golden_comparison_marker(lines: list[str]) -> bool:
    """Return True if *lines* contains a golden-comparison marker anywhere.

    See the AP7 pattern comments above for exactly what counts: a
    "golden"-named identifier or a literal "golden" path segment, in each
    case assigned as part of a pathlib `/` join or an os.path.join() call.
    A bare occurrence of the word "golden" with no path-join context (e.g.
    the common `test_golden_path_...` happy-path naming idiom) does not
    count.
    """
    for line in lines:
        if _AP7_GOLDEN_NAME_ASSIGN.search(line) or _AP7_GOLDEN_PATH_LITERAL.search(
            line
        ):
            return True
    return False


def _check_ap7(filepath: Path, lines: list[str]) -> list[str]:
    """Detect anti-pattern 7: golden comparison + inherited subprocess env.

    Applies only to Python test files (golden-comparison harnesses in this
    tree are Python).  Scans the whole file once for the golden-comparison
    marker; if absent, the file cannot carry this pairing and the inherited-
    environment scan is skipped entirely so an ordinary, correct inherited-
    environment site never pays for this check.  If the marker is present,
    every inherited-environment construct in the file is a finding at its
    own line, subject to the normal per-instance opt-out.
    """
    if not _has_golden_comparison_marker(lines):
        return []

    findings: list[str] = []
    for idx, line in enumerate(lines):
        lineno = idx + 1
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if not _AP7_ENV_INHERIT.search(line):
            continue
        if _has_opt_out(lines, lineno):
            continue
        detail = (
            "This file compares a captured surface against a committed golden "
            "AND builds its subprocess environment by inheriting the ambient "
            "one here, which makes the comparison's verdict a property of the "
            "invoking shell rather than of the tree. "
            "Fix: build the subprocess environment from an explicit, minimal "
            "set of names instead -- see "
            "tests.fixtures.scrubbed_subprocess_env.build_scrubbed_env. "
            "Opt out with: '# anti-pattern-allowlist: 7 (justification: ...)'"
        )
        findings.append(_finding(filepath, lineno, "AP7", detail))

    return findings


def _check_ap8(filepath: Path, lines: list[str]) -> list[str]:
    """Detect anti-pattern 8: uncontrolled absolute-path substring assertion.

    Flags ``assert "<literal>" (not )?in <subject>`` where the literal both
    starts and ends with "/" -- a path-segment shape ("/projects/", "/tmp/")
    searched as a free-floating substring of an absolute path.  The literal's
    surrounding prefix is whatever the ambient scratch root happens to be,
    which the test does not control, so the assertion's verdict can flip
    based on the invoking environment rather than on what the code under
    test computed.

    Deliberately narrow: only a literal that both opens and closes with "/"
    matches, so the hundreds of ordinary containment assertions against
    captured stdout/stderr text elsewhere in the tree are untouched.
    """
    findings: list[str] = []

    for idx, line in enumerate(lines):
        lineno = idx + 1

        m = _AP8_ABS_PATH_SUBSTRING.search(line)
        if not m:
            continue

        if _has_opt_out(lines, lineno):
            continue

        literal = m.group("literal")
        subject = m.group("subject")
        detail = (
            f"Assertion searches an absolute path for the hardcoded literal "
            f"'{literal}' inside '{subject}'. The prefix surrounding that "
            "literal is the ambient scratch root the suite happens to run "
            "under, which this test does not control -- a caller whose own "
            "scratch root contains the same substring makes this assertion "
            "fail with no defect in the code under test. "
            "Fix: assert against a value the test computed instead, e.g. "
            "pathlib.Path(subject).relative_to(root).parts, or the full "
            "expected path. "
            "Opt out with: '# anti-pattern-allowlist: 8 (justification: ...)'"
        )
        findings.append(_finding(filepath, lineno, "AP8", detail))

    return findings


# ---------------------------------------------------------------------------
# File scanner
# ---------------------------------------------------------------------------


def _scan_file(
    filepath: Path,
    verbose: bool = False,
    runtime_only: bool = False,
) -> list[str]:
    """Scan a single file and return all findings.

    Parameters
    ----------
    filepath:
        Path to the file to scan.
    verbose:
        If True, print the file path before scanning.
    runtime_only:
        If True the file is a runtime script rather than a test file.
        AP1 (pattern-scan assertion loops) is skipped because it targets
        test assertions; only AP2 (environment-coupled paths / bare mktemp)
        is applied.
    """
    if verbose:
        print(f"  scanning: {filepath}", flush=True)

    try:
        text = filepath.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [f"{filepath}: ERROR reading file: {exc}"]

    lines = text.splitlines(keepends=True)

    findings: list[str] = []

    # AP1 — pattern-scan assertion loops — only applies to test files.
    if not runtime_only:
        findings.extend(_check_ap1(filepath, lines))

    # AP2 — environment-coupled paths / bare mktemp — applies to both test
    # files and runtime scripts.
    if filepath.suffix == ".py":
        findings.extend(_check_ap2_py(filepath, lines))
    elif filepath.suffix == ".sh":
        findings.extend(_check_ap2_sh(filepath, lines))

    # AP3 — hardcoded /tmp/pgai_kanban_tmp literal — applies to all file
    # types (Python and shell) except the whitelisted resolver and this script.
    findings.extend(_check_ap3(filepath, lines))

    # AP6 — bug-provenance test names — only applies to Python test files.
    if not runtime_only and filepath.suffix == ".py":
        findings.extend(_check_ap6(filepath, lines))

    # AP7 — golden comparison + inherited subprocess env — only applies to
    # Python test files; runtime scripts do not author golden comparisons.
    if not runtime_only and filepath.suffix == ".py":
        findings.extend(_check_ap7(filepath, lines))

    # AP8 — uncontrolled absolute-path substring assertion — only applies to
    # Python test files; runtime scripts do not author pytest assertions.
    if not runtime_only and filepath.suffix == ".py":
        findings.extend(_check_ap8(filepath, lines))

    return findings


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="lint_test_anti_patterns.py",
        description=(
            "Lint team/tests/ for anti-pattern 1 (pattern-scan assertion loops), "
            "anti-pattern 2 (hardcoded /tmp or $HOME paths / bare mktemp calls), "
            "anti-pattern 6 (bug-provenance test names), anti-pattern 7 "
            "(golden comparison + inherited subprocess environment), and "
            "anti-pattern 8 (uncontrolled absolute-path substring assertion). "
            "Also scans runtime script directories for AP2 violations. "
            "With --files, scans only the named test files instead. "
            "See roles/TESTER.md 'Test Authoring Guidelines' for full definitions."
        ),
    )
    parser.add_argument(
        "--tests-dir",
        metavar="PATH",
        help=(
            "Directory to scan for test anti-patterns (AP1 + AP2). "
            "Default: team/tests/ relative to this script's parent directory."
        ),
    )
    parser.add_argument(
        "--scripts-dirs",
        metavar="PATH",
        nargs="*",
        default=None,
        help=(
            "Runtime script directories to scan for AP2 violations only. "
            "Default: team/scripts/, team/scripts/lib/, and "
            "team/scripts/lib/overwatch-checks/ relative to the repo root. "
            "Pass no paths (--scripts-dirs with no arguments) to disable "
            "runtime script scanning."
        ),
    )
    parser.add_argument(
        "--files",
        metavar="PATH",
        nargs="+",
        default=None,
        help=(
            "Named-file mode: scan exactly these test files with every "
            "test-file check (AP1, AP2, AP3, AP6, AP7, AP8) and nothing else "
            "-- no tests-directory walk and no runtime-script scan. A named "
            "file is scanned even under a fixtures/ directory. Each path must "
            "be an existing file (a missing one exits 2). Cannot be combined "
            "with --tests-dir or --scripts-dirs."
        ),
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print each file name as it is scanned.",
    )
    return parser.parse_args(argv)


def _resolve_named_files(raw_paths: list[str]) -> tuple[list[Path], list[str]]:
    """Resolve the --files arguments to the exact list of files to scan.

    Returns ``(files, missing)``: *files* holds each existing regular file
    once, in the order first named (a path named twice is scanned once, so
    its findings are not double-counted); *missing* holds every argument
    that does not name an existing regular file, verbatim, so the caller
    can report all of them in one message rather than one per run.
    """
    files: list[Path] = []
    missing: list[str] = []
    seen: set[Path] = set()
    for raw in raw_paths:
        path = Path(raw).resolve()
        if not path.is_file():
            missing.append(raw)
            continue
        if path in seen:
            continue
        seen.add(path)
        files.append(path)
    return files, missing


def _report(all_findings: list[str], test_count: int, runtime_count: int) -> int:
    """Print the findings block or the OK line; return the process exit code."""
    if all_findings:
        print(
            f"\nlint_test_anti_patterns: {len(all_findings)} finding(s):\n",
            flush=True,
        )
        for finding in all_findings:
            print(finding, flush=True)
            print()  # blank line between findings
        print(
            f"lint_test_anti_patterns: FAIL — {len(all_findings)} finding(s). "
            "Fix the issues above or add per-instance opt-out marker comments.\n"
            f"{SOP_CITATION}",
            flush=True,
        )
        return 1

    print(
        f"lint_test_anti_patterns: OK — 0 findings across "
        f"{test_count + runtime_count} file(s) "
        f"({test_count} test, {runtime_count} runtime).",
        flush=True,
    )
    return 0


def _main_named_files(args: argparse.Namespace) -> int:
    """Named-file mode: scan exactly the --files paths as test files.

    No directory is walked and no runtime script is scanned, so the cost of
    a run is proportional to the files named, not to the size of the tree.
    The fixtures/ exclusion the whole-tree walk applies does not apply here:
    a caller that names a file wants that file checked.
    """
    script = str(Path(__file__).resolve())
    if args.tests_dir or args.scripts_dirs is not None:
        print(
            "ERROR: --files cannot be combined with --tests-dir or "
            "--scripts-dirs: named-file mode scans exactly the named files "
            "and never walks a directory.\n"
            f"Run with --files alone: python3 {shlex.quote(script)} --files "
            + shlex.join(args.files),
            file=sys.stderr,
        )
        return 2

    named_files, missing = _resolve_named_files(args.files)
    if missing:
        print(
            "ERROR: --files path(s) not found or not a regular file: "
            + ", ".join(missing)
            + "\nPass existing test file paths, e.g.: python3 "
            f"{shlex.quote(script)} --files "
            + shlex.quote(str(Path(script).parent.parent / "tests" / "unit" / "test_semver.py")),
            file=sys.stderr,
        )
        return 2

    print(
        f"lint_test_anti_patterns: scanning {len(named_files)} named file(s)",
        flush=True,
    )
    all_findings: list[str] = []
    for filepath in named_files:
        all_findings.extend(_scan_file(filepath, verbose=args.verbose))

    return _report(all_findings, len(named_files), 0)


def main(argv: list[str] | None = None) -> int:
    """Run the test anti-pattern lint over the test tree, or exactly ``--files``.

    Exit codes: 0 no findings; 1 one or more findings (printed per finding);
    2 usage error or internal failure, such as a missing ``--files`` path.
    """
    args = _parse_args(argv)

    if args.files is not None:
        return _main_named_files(args)

    # Infer the repo root as the grandparent of this script
    # (team/scripts/lint_test_anti_patterns.py → repo root = parent of team/).
    script_dir = Path(__file__).resolve().parent  # team/scripts/
    repo_root = script_dir.parent.parent           # repo root

    # -----------------------------------------------------------------------
    # Resolve the tests directory (AP1 + AP2 scan).
    # -----------------------------------------------------------------------
    if args.tests_dir:
        tests_dir = Path(args.tests_dir).resolve()
    else:
        # Default: team/tests/ relative to the directory containing this script
        tests_dir = (script_dir.parent / "tests").resolve()

    if not tests_dir.is_dir():
        print(
            f"ERROR: tests directory not found: {tests_dir}\n"
            "Use --tests-dir to specify an alternative path.",
            file=sys.stderr,
        )
        return 2

    # -----------------------------------------------------------------------
    # Resolve runtime script directories (AP2-only scan).
    # --scripts-dirs with no arguments → empty list → disable runtime scan.
    # --scripts-dirs absent → None → use defaults.
    # -----------------------------------------------------------------------
    if args.scripts_dirs is None:
        # Default runtime script directories to scan.
        _default_scripts_dirs = [
            script_dir,                              # team/scripts/
            script_dir / "lib",                      # team/scripts/lib/
            script_dir / "lib" / "overwatch-checks",  # team/scripts/lib/overwatch-checks/
        ]
        scripts_dirs: list[Path] = [d for d in _default_scripts_dirs if d.is_dir()]
    else:
        scripts_dirs = [Path(d).resolve() for d in args.scripts_dirs if d]

    # -----------------------------------------------------------------------
    # Scan test files (AP1 + AP2).
    # -----------------------------------------------------------------------
    print(f"lint_test_anti_patterns: scanning tests {tests_dir}", flush=True)

    # Collect all Python and shell test files.
    # Excluded directories:
    #   __pycache__  — compiled bytecode; not source
    #   fixtures/    — deliberate bad-example files used by lint unit tests;
    #                  they intentionally contain anti-patterns and are only
    #                  scanned explicitly by those unit tests, not by CI runs.
    test_files: list[Path] = []
    for suffix in (".py", ".sh"):
        test_files.extend(
            p
            for p in tests_dir.rglob(f"*{suffix}")
            if "__pycache__" not in p.parts and "fixtures" not in p.parts
        )
    test_files.sort()

    all_findings: list[str] = []
    for filepath in test_files:
        all_findings.extend(_scan_file(filepath, verbose=args.verbose))

    # -----------------------------------------------------------------------
    # Scan runtime script directories (AP2 only).
    # Each directory is scanned non-recursively (top-level files only) to
    # avoid inadvertently scanning nested dirs that have their own lint rules.
    # The test directory is excluded to prevent double-counting.
    # -----------------------------------------------------------------------
    runtime_files: list[Path] = []
    for scripts_dir in scripts_dirs:
        if not scripts_dir.is_dir():
            continue
        for suffix in (".py", ".sh"):
            runtime_files.extend(
                p
                for p in scripts_dir.glob(f"*{suffix}")
                if p.is_file()
                and "__pycache__" not in p.parts
                and "fixtures" not in p.parts
            )

    runtime_files.sort()

    # De-duplicate: skip runtime files that are already in the test scan tree.
    test_file_set = set(test_files)
    runtime_files = [f for f in runtime_files if f not in test_file_set]

    if scripts_dirs:
        dirs_str = ", ".join(str(d) for d in scripts_dirs)
        print(
            f"lint_test_anti_patterns: scanning runtime scripts in {dirs_str}",
            flush=True,
        )
    for filepath in runtime_files:
        all_findings.extend(
            _scan_file(filepath, verbose=args.verbose, runtime_only=True)
        )

    return _report(all_findings, len(test_files), len(runtime_files))


if __name__ == "__main__":
    sys.exit(main())
