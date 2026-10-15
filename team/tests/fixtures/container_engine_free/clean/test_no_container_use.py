"""
test_no_container_use.py
==========================
Fixture: a clean sweep-set module for
team/scripts/lint_container_engine_free.py's own unit tests
(team/tests/unit/test_lint_container_engine_free.py). Not a real test file
-- never collected by any gated runner or pytest run; only scanned by name
via the lint's own --sweep-dir override.

Invokes a subprocess for an unrelated binary (never docker/podman) and
otherwise contains only ordinary assertions -- proves the lint reports zero
findings against a module with no container-engine shape at all.
"""

from __future__ import annotations

import subprocess


def test_runs_an_unrelated_binary() -> None:
    result = subprocess.run(["true"], check=False)
    assert result.returncode == 0


def test_plain_arithmetic() -> None:
    assert 1 + 1 == 2
