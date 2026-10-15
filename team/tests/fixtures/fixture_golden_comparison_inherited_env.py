"""fixture_golden_comparison_inherited_env.py
===============================================
DELIBERATE BAD EXAMPLE -- DO NOT COPY THIS PATTERN INTO REAL TESTS.

This file carries the exact pairing the Anti-pattern 7 check in
lint_test_anti_patterns.py exists to catch: a golden-comparison marker
(`_GOLDEN_DIR`, assigned via a pathlib `/` join under "fixtures") together
with a subprocess environment built by inheriting the ambient one wholesale
(see `_run_against_golden` below for the exact construct). Because the
subprocess environment is not rebuilt explicitly, the comparison's verdict
against `_GOLDEN_DIR` depends on whatever the invoking shell happens to
export, not on the tree alone.

This fixture is excluded from the normal CI lint scan by the fixtures/
directory guard. It is scanned DIRECTLY by
team/tests/unit/test_lint_golden_env_coupling.py to prove the check fires
on exactly this shape and names the file and line where the inherited
environment is built.

DO NOT add an opt-out marker here -- the whole point is that the check
must flag this file.
"""

from __future__ import annotations

import os
import pathlib
import subprocess

_HERE = pathlib.Path(__file__).parent
_GOLDEN_DIR = _HERE / "fixtures" / "example-golden-surface"


def _run_against_golden(command: list[str]) -> subprocess.CompletedProcess:
    """Compare a fresh capture against the committed golden surface.

    The line below is the deliberate anti-pattern: the subprocess
    environment is inherited wholesale rather than rebuilt from an
    explicit, minimal set of names, so this comparison's verdict is a
    property of the invoking shell rather than of the tree.
    """
    env = dict(os.environ)
    env["EXAMPLE_GOLDEN_COMPARE_TARGET"] = str(_GOLDEN_DIR)
    return subprocess.run(command, env=env, capture_output=True, text=True)
