"""fixture_inherited_env_without_golden_comparison.py
=======================================================
NEGATIVE CONTROL for the Anti-pattern 7 check in
lint_test_anti_patterns.py: this file builds a subprocess environment by
inheriting the ambient one (`dict(os.environ)`) -- one of the ~157 ordinary,
correct call sites the check is deliberately narrow enough to leave alone --
but performs no golden comparison anywhere in the file. The check must stay
silent on this file: inherited environment alone, with no comparison
against a committed fixture, is not the pairing it targets.

This fixture is excluded from the normal CI lint scan by the fixtures/
directory guard. It is scanned DIRECTLY by
team/tests/unit/test_lint_golden_env_coupling.py to prove the check does
NOT fire here.
"""

from __future__ import annotations

import os
import subprocess


def _run_with_inherited_environment(command: list[str]) -> subprocess.CompletedProcess:
    """Run a subprocess that legitimately needs the ambient environment.

    No golden comparison happens anywhere in this module -- there is
    nothing here for Anti-pattern 7 to pair this inherited environment
    against.
    """
    env = dict(os.environ)
    env["EXAMPLE_EXTRA_FLAG"] = "1"
    return subprocess.run(command, env=env, capture_output=True, text=True)
