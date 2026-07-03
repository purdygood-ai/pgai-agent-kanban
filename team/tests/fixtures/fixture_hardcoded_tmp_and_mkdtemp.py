"""fixture_hardcoded_tmp_and_mkdtemp.py
=======================================
DELIBERATE BAD EXAMPLE GENERATOR -- DO NOT COPY THESE PATTERNS INTO REAL CODE.

This file is a lint fixture for the AP3 and AP2 regression guards added in
v0.55.0 (task CODER-20260608-013-lint-guard-and-regression-test).

It provides functions that GENERATE source code containing the two synthetic
anti-patterns that the extended lint_test_anti_patterns.py must flag:

  1. A hardcoded hardcoded-temp-root literal (AP3 regression guard).
     After the v0.55.0 consolidation, no caller outside temp.sh should
     inline this literal.  The AP3 rule detects it as a sign that a new
     site has bypassed the resolver.

  2. An un-rooted tempfile.mkdtemp() call without a dir= argument (AP2).
     Without dir=, mkdtemp() lands directly in /tmp rather than under
     PGAI_AGENT_KANBAN_TEMP_DIR.

Purpose: test_lint_hardcoded_tmp_guard.py calls the generator functions
below to produce synthetic source code, writes it to temp files, and then
runs _scan_file() against those temp files to prove that both detection
rules fire on representative source content.

The generator functions use string concatenation to produce the bad patterns
so that this fixture file itself does not contain raw anti-pattern literals
that would be caught by the lint or by the acceptance-criterion grep check.

This file is intentionally placed under team/tests/fixtures/ which is
excluded from the normal CI lint scan (the 'fixtures' guard in
lint_test_anti_patterns.py skips that directory).  Only the regression-
guard unit test exercises these generators directly.
"""


def make_hardcoded_literal_src() -> str:
    """Return a Python source snippet containing a hardcoded temp-root literal.

    The returned snippet is the AP3 anti-pattern: a caller that inlines the
    resolver's last-resort fallback path as a literal instead of routing
    through PGAI_AGENT_KANBAN_TEMP_DIR.

    The literal is assembled via concatenation here so that this source file
    itself does not contain the raw literal (which would fail the AP3 lint
    and the acceptance-criterion grep check).  The returned string contains
    the literal as a continuous sequence and IS the subject the lint must flag.
    """
    # Assemble the hardcoded path literal via concatenation so this source
    # file does not itself contain the raw literal.
    _literal = "/tmp/" + "pgai_kanban_tmp"
    return (
        '#!/usr/bin/env python3\n'
        '"""Synthetic bad-example: hardcoded temp-root literal (AP3)."""\n'
        '\n'
        '\n'
        'def bad_temp_dir() -> str:\n'
        '    """Return a hardcoded temp path — deliberate AP3 violation."""\n'
        '    # The line below is the AP3 anti-pattern.\n'
        f'    return "{_literal}"\n'
    )


def make_unrooted_mkdtemp_src() -> str:
    """Return a Python source snippet containing an un-rooted mkdtemp call.

    The returned snippet is the AP2 anti-pattern: calling tempfile.mkdtemp()
    without a dir= argument, which lands in /tmp rather than under
    PGAI_AGENT_KANBAN_TEMP_DIR.
    """
    return (
        '#!/usr/bin/env python3\n'
        '"""Synthetic bad-example: un-rooted mkdtemp call (AP2)."""\n'
        '\n'
        'import tempfile\n'
        '\n'
        '\n'
        'def bad_mkdtemp() -> str:\n'
        '    """Create a temp dir without routing through the framework."""\n'
        '    # The line below is the AP2 anti-pattern: no dir= argument.\n'
        '    return tempfile.mkdtemp()\n'
    )
