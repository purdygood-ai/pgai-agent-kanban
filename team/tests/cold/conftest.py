"""
tests/cold/conftest.py
======================
The in-file container-tier gate for every team/tests/cold module.

The cold modules build real images and start real containers, so they are
the container tier: operator-invoked only (team/scripts/lib/container_tier.sh).
run-cold-build-tests.sh gates its own launch, but a module collected any
other way -- `run-unit-tests.sh --files tests/cold/<module>.py`, a bare
`python3 -m pytest tests/cold/` -- must refuse on its own as well.

Every fixture or test that can reach a container engine requests
``operator_container_tier``. Without the operator's authorization
(PGAI_CONTAINER_TIER=1, outside an agent wake) it narrate-skips through
tests.fixtures.binary_guard.require_container_tier before any engine
command, `docker info` included, so the requesting test skips with a reason
naming container_tier.operator_only. With authorization it returns and the
module's own daemon and engine checks follow as before. Tests that only
read files or drive a stub engine do not request it and run either way.
"""

from __future__ import annotations

import pathlib
import shlex

import pytest

from tests.fixtures.binary_guard import require_container_tier

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_OPERATOR_COMMAND = (
    f"PGAI_CONTAINER_TIER=1 bash {shlex.quote(str(_TEAM_DIR / 'scripts' / 'run-cold-build-tests.sh'))} --image all"
)


@pytest.fixture(scope="session")
def operator_container_tier() -> None:
    """Skip the requesting test unless the operator authorized the container tier.

    Session-scoped so the check and its narration happen once per run;
    pytest re-raises the cached skip for every later test that requests it.
    """
    require_container_tier(label="cold-lane", operator_command=_OPERATOR_COMMAND)
