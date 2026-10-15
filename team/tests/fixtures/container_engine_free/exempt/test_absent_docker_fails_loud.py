"""
test_absent_docker_fails_loud.py
==================================
Fixture: mirrors the real, legitimate pattern in
team/tests/unit/test_binary_guard_contract.py -- a test proving a
fail-loud contract when docker is absent, structurally unable to reach a
real container engine because PATH is redirected to a location the test
itself controls before the call. Used by
team/scripts/lint_container_engine_free.py's own unit tests
(team/tests/unit/test_lint_container_engine_free.py) to prove the
PATH-redirect exemption does not report this shape as a violation. Not a
real test file -- never collected by any gated runner or pytest run.
"""

from __future__ import annotations

import subprocess

import pytest


def test_calling_docker_with_no_guard_fails_loud_when_docker_is_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(FileNotFoundError):
        subprocess.run(["docker", "info"], capture_output=True)
