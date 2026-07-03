#!/usr/bin/env bash
# fixture_bare_tmp_redirect.sh
# ==============================
# Deliberate bad-example fixture: a shell script that redirects stderr to a
# bare /tmp path.  This is the form that previously leaked pyc_err/bn_err
# past the lint guard (PRIORITY-0099): shell redirects like 2>/tmp/pyc_err
# were not caught by the existing mkdir/Path/makedirs/VAR= patterns.
#
# This fixture is excluded from the normal CI lint scan by the fixtures/
# directory guard.  It is scanned DIRECTLY by the unit tests in
# test_lint_bare_tmp_redirect.py to prove the linter now flags it.
#
# DO NOT add an opt-out marker here — the whole point is that the linter
# must flag this file.

set -euo pipefail

# Previously this redirect was not caught by any lint pattern.
# Under the zero-exemption contract it must be flagged.
python3 -m py_compile some_file.py 2>/tmp/foo
