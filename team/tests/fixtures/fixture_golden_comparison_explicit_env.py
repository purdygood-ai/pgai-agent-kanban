"""fixture_golden_comparison_explicit_env.py
==============================================
NEGATIVE CONTROL for the Anti-pattern 7 check in
lint_test_anti_patterns.py: this file carries the same golden-comparison
marker as fixture_golden_comparison_inherited_env.py (`_GOLDEN_DIR`,
assigned via a pathlib `/` join under "fixtures"), but the subprocess
environment is rebuilt explicitly from a fixed, minimal set of names rather
than inherited from the ambient one. The check must stay silent on this
file: a golden comparison is only a finding when it is PAIRED with
environment inheritance, and this file inherits nothing from the ambient
environment anywhere -- every name the subprocess sees is listed
explicitly in `_run_against_golden` below.

This fixture is excluded from the normal CI lint scan by the fixtures/
directory guard. It is scanned DIRECTLY by
team/tests/unit/test_lint_golden_env_coupling.py to prove the check does
NOT fire here.
"""

from __future__ import annotations

import pathlib
import subprocess

_HERE = pathlib.Path(__file__).parent
_GOLDEN_DIR = _HERE / "fixtures" / "example-golden-surface"


def _run_against_golden(command: list[str]) -> subprocess.CompletedProcess:
    """Compare a fresh capture against the committed golden surface.

    The environment below is built from an explicit, minimal set of names
    -- never inherited wholesale -- so the comparison's verdict is a
    property of the tree, not of whatever the invoking shell exports.
    """
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(_HERE / "example-home"),
        "EXAMPLE_GOLDEN_COMPARE_TARGET": str(_GOLDEN_DIR),
    }
    return subprocess.run(command, env=env, capture_output=True, text=True)
