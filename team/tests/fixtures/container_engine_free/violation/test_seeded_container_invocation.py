"""
test_seeded_container_invocation.py
=====================================
Fixture: a deliberate container-engine-invocation violation for
team/scripts/lint_container_engine_free.py's own unit tests
(team/tests/unit/test_lint_container_engine_free.py). Not a real test file
-- never collected by any gated runner or pytest run; only imported/scanned
by name via the lint's own --sweep-dir override.

Invokes the real "docker" binary directly, with no PATH redirection, the
exact shape the lint must report.
"""

from __future__ import annotations

import subprocess


def test_builds_an_image_directly() -> None:
    subprocess.run(["docker", "build", "-t", "example:test", "."], check=True)
