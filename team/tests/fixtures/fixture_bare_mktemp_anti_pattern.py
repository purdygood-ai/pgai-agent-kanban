"""fixture_bare_mktemp_anti_pattern.py
======================================
DELIBERATE BAD EXAMPLE -- DO NOT COPY THIS PATTERN INTO REAL TESTS.

This file is a lint fixture for BUG-0168.  It contains a bare mktemp call
(no -p flag) inside an embedded shell string, which is exactly the
anti-pattern that lint_test_anti_patterns.py must flag.

Purpose: when test_lint_bare_mktemp_detection.py runs _scan_file() directly
against this file, it proves that the _AP2_PY_EMBEDDED_SHELL_BARE_MKTEMP
detection rule fires on real source content.

This file is intentionally excluded from the normal lint scan
(team/tests/fixtures/ is skipped by lint_test_anti_patterns.py) so that
CI does not fail on this deliberate bad example.  Only the unit test
exercises it via _scan_file() directly.
"""

import textwrap


def _bad_mock_wake_fragment(kanban_root_str: str) -> str:
    """Build a bash fragment that reproduces the BUG-0168 anti-pattern.

    The embedded shell string below uses a bare mktemp with no -p flag.
    This is the anti-pattern: the temp file lands in /tmp instead of under
    PGAI_AGENT_KANBAN_TEMP_DIR.  The lint must flag this line.
    """
    # The line below is the deliberate anti-pattern. Do not copy this.
    fragment = textwrap.dedent(f"""\
        set -euo pipefail
        KANBAN_ROOT="{kanban_root_str}"
        LOG_FILE="$(mktemp)"
        log() {{ echo "[mock-wake]: $*" | tee -a "${{LOG_FILE}}"; }}
    """)
    return fragment
