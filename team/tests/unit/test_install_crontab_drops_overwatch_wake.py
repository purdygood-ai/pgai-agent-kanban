"""
test_install_crontab_drops_overwatch_wake.py
============================================
Behavioural tests: install-crontab.sh with an explicit --wake-tier, run over
a crontab an earlier install wrote, leaves no daily OVERWATCH model wake
(``wake-batch.sh --agent=overwatch``) in the managed block and still
schedules the hourly ``overwatch-sweep.sh`` -- OVERWATCH's only scheduled
firing.

Every test drives the real script as a subprocess against a throwaway
kanban root with a sandboxed HOME and a fake crontab on PATH
(tests/fixtures/upgrade_sandbox.py), so the operator's crontab is never read
or written.
"""

from __future__ import annotations

import pathlib

import pytest

from tests.fixtures.upgrade_sandbox import INSTALL_CRONTAB_SCRIPT, UpgradeSandbox, diagnostic

_BEGIN = "# BEGIN pgai-agent-kanban managed block"
_END = "# END pgai-agent-kanban managed block"
_FOREIGN = "MAILTO=ops@example.com\n15 3 * * * /usr/local/bin/nightly-backup.sh\n"


def _old_overwatch_wake(root: pathlib.Path) -> str:
    """The daily 03:30 OVERWATCH wake line earlier templates installed."""
    return (
        f"30 3 * * * {root}/scripts/wake-batch.sh --agent=overwatch --sleep=0 --max-tasks=1"
        f" >> {root}/logs/cron-overwatch.log 2>&1\n"
    )


def _old_sweep(root: pathlib.Path) -> str:
    return f"30 * * * * {root}/scripts/overwatch-sweep.sh >> {root}/logs/cron-overwatch-sweep.log 2>&1\n"


def _install(sandbox: UpgradeSandbox, tier: str):
    return sandbox.run(["bash", INSTALL_CRONTAB_SCRIPT, f"--wake-tier={tier}", "--yes"])


def _managed_block(text: str, root: pathlib.Path) -> list[str]:
    begin = f"{_BEGIN}: {root}"
    end = f"{_END}: {root}"
    assert text.count(begin) == 1 and text.count(end) == 1, f"expected one managed block\n{text}"
    return text.split(begin, 1)[1].split(end, 1)[0].splitlines()


def _jobs(lines: list[str]) -> list[str]:
    return [line for line in lines if line.strip() and not line.lstrip().startswith("#")]


@pytest.mark.parametrize("tier", ["small", "medium", "large"])
def test_marked_block_with_the_old_overwatch_wake_is_rewritten_without_it(tmp_path: pathlib.Path, tier: str) -> None:
    sandbox = UpgradeSandbox(tmp_path)
    root = sandbox.root
    sandbox.write_crontab(
        _FOREIGN
        + f"{_BEGIN}: {root}\n"
        + f"*/5 * * * * {root}/scripts/wake-batch.sh --agent=pm --sleep=0 >> {root}/logs/cron-pm.log 2>&1\n"
        + _old_sweep(root)
        + _old_overwatch_wake(root)
        + f"{_END}: {root}\n"
    )

    result = _install(sandbox, tier)

    assert result.returncode == 0, diagnostic(result)
    data = sandbox.crontab_bytes()
    assert data is not None, "no crontab was installed\n" + diagnostic(result)
    text = data.decode("utf-8")
    jobs = _jobs(_managed_block(text, root))
    assert not [
        line for line in jobs if "--agent=overwatch" in line
    ], f"the managed block still schedules the daily OVERWATCH wake\n{text}"
    assert [
        line for line in jobs if f"{root}/scripts/overwatch-sweep.sh" in line
    ], f"the managed block lost the hourly OVERWATCH sweep\n{text}"
    assert text.startswith(_FOREIGN), f"the operator's own lines must be kept\n{text}"


def test_unmarked_old_overwatch_wake_naming_the_root_is_dropped(tmp_path: pathlib.Path) -> None:
    sandbox = UpgradeSandbox(tmp_path)
    root = sandbox.root
    sandbox.write_crontab(_FOREIGN + _old_sweep(root) + _old_overwatch_wake(root))

    result = _install(sandbox, "medium")

    assert result.returncode == 0, diagnostic(result)
    data = sandbox.crontab_bytes()
    assert data is not None, "no crontab was installed\n" + diagnostic(result)
    text = data.decode("utf-8")
    assert "--agent=overwatch" not in text, f"the old OVERWATCH wake survived the rewrite\n{text}"
    assert _jobs(_managed_block(text, root)), text
    assert (
        sum(1 for line in _jobs(text.splitlines()) if "overwatch-sweep.sh" in line) == 1
    ), f"expected exactly one hourly OVERWATCH sweep line\n{text}"
