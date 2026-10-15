"""
test_crontab_backup_rotation.py
===============================
Behavioural tests for the crontab backups the crontab step writes to
``$HOME/.crontab.before-install-YYYYMMDD-HHMMSS.bak``
(safe_overwrite_crontab in team/scripts/lib/safe_overwrite.sh):

* a crontab byte-identical to the newest backup writes no new backup;
* after the backup step only the newest ``[install] crontab_backups_keep``
  backups (default 10) remain, and a hand-named backup is never removed;
* a count that is not a whole number of 1 or more is refused dual-channel,
  before anything is written or deleted, with a remedy that fixes it;
* the empty-crontab restore hint still lists the surviving backups.

Every test drives the real install-crontab.sh as a subprocess against a
throwaway kanban root (tests/fixtures/upgrade_sandbox.py): HOME is inside
the sandbox and the crontab is a file behind a fake crontab binary, so the
operator's crontab and home directory are never read or written.
"""

from __future__ import annotations

import glob
import pathlib
import re
import subprocess

from tests.fixtures.upgrade_sandbox import (
    INSTALL_CRONTAB_SCRIPT,
    TEAM_DIR,
    UpgradeSandbox,
    diagnostic,
)

_SAFE_OVERWRITE = TEAM_DIR / "scripts" / "lib" / "safe_overwrite.sh"
_FOREIGN = "MAILTO=ops@example.com\n15 3 * * * /usr/local/bin/nightly-backup.sh\n"
_FIXTURE_COUNT = 15
_DEFAULT_KEEP = 10


def _install(sandbox: UpgradeSandbox) -> subprocess.CompletedProcess:
    return sandbox.run(["bash", INSTALL_CRONTAB_SCRIPT, "--wake-tier=small", "--yes"])


def _backups(sandbox: UpgradeSandbox) -> list[pathlib.Path]:
    return sorted(sandbox.home.glob(".crontab.before-install-*.bak"))


def _settled_sandbox(tmp_path: pathlib.Path) -> UpgradeSandbox:
    """A sandbox whose crontab is already in its installed shape, so a
    further install leaves it byte-identical, and whose HOME holds the
    fifteen fixture backups (January 2026, one a day, each with distinct
    content) and nothing else."""
    sandbox = UpgradeSandbox(tmp_path)
    sandbox.write_crontab(_FOREIGN)
    first = _install(sandbox)
    assert first.returncode == 0, diagnostic(first)
    for path in _backups(sandbox):
        path.unlink()
    for day in range(1, _FIXTURE_COUNT + 1):
        path = sandbox.home / f".crontab.before-install-202601{day:02d}-030000.bak"
        path.write_text(f"# fixture backup {day}\n", encoding="utf-8")
    return sandbox


def _set_keep(sandbox: UpgradeSandbox, value: str) -> pathlib.Path:
    cfg = sandbox.root / "kanban.cfg"
    cfg.write_text(
        cfg.read_text(encoding="utf-8") + f"\n[install]\ncrontab_backups_keep = {value}\n",
        encoding="utf-8",
    )
    return cfg


def test_backup_step_keeps_the_ten_newest_and_writes_nothing_when_unchanged(
    tmp_path: pathlib.Path,
) -> None:
    sandbox = _settled_sandbox(tmp_path)
    fixtures = _backups(sandbox)
    assert len(fixtures) == _FIXTURE_COUNT

    result = _install(sandbox)

    assert result.returncode == 0, diagnostic(result)
    after_first = _backups(sandbox)
    new = [p for p in after_first if p not in fixtures]
    assert len(new) == 1, f"expected one new backup, found {new}\n" + diagnostic(result)
    assert new[0].read_bytes() == sandbox.crontab_bytes(), (
        "the new backup must hold the crontab as it was before the step"
    )
    expected = fixtures[-(_DEFAULT_KEEP - 1):] + new
    assert after_first == sorted(expected), (
        f"expected the {_DEFAULT_KEEP} newest backups, found "
        f"{[p.name for p in after_first]}\n" + diagnostic(result)
    )

    second = _install(sandbox)

    assert second.returncode == 0, diagnostic(second)
    assert _backups(sandbox) == after_first, (
        "an unchanged crontab must not write another backup\n" + diagnostic(second)
    )
    assert "no new backup written" in second.stdout, diagnostic(second)


def test_configured_count_is_honoured_and_hand_named_backups_are_kept(
    tmp_path: pathlib.Path,
) -> None:
    sandbox = _settled_sandbox(tmp_path)
    _set_keep(sandbox, "3")
    hand_named = sandbox.home / ".crontab.before-install-keep-me.bak"
    hand_named.write_text("# my own copy\n", encoding="utf-8")
    fixtures = [p for p in _backups(sandbox) if p != hand_named]

    result = _install(sandbox)

    assert result.returncode == 0, diagnostic(result)
    remaining = [p for p in _backups(sandbox) if p != hand_named]
    assert len(remaining) == 3, [p.name for p in remaining]
    assert remaining[:2] == fixtures[-2:], [p.name for p in remaining]
    assert hand_named.exists(), "a hand-named backup must never be pruned"


def test_invalid_count_is_refused_before_anything_changes(tmp_path: pathlib.Path) -> None:
    for value in ("0", "abc"):
        sandbox = _settled_sandbox(tmp_path / f"keep-{value}")
        cfg = _set_keep(sandbox, value)
        sandbox.write_crontab(_FOREIGN)
        crontab_before = sandbox.crontab_bytes()
        backups_before = _backups(sandbox)

        result = _install(sandbox)

        assert result.returncode != 0, diagnostic(result)
        assert re.search(
            r"^error:\s+safe_overwrite\.invalid_crontab_backups_keep\s*$",
            result.stderr,
            re.MULTILINE,
        ), diagnostic(result)
        assert f"crontab_backups_keep='{value}'" in result.stderr, diagnostic(result)
        assert "whole number of 1 or more" in result.stderr, diagnostic(result)
        assert sandbox.crontab_bytes() == crontab_before, diagnostic(result)
        assert _backups(sandbox) == backups_before, diagnostic(result)

        remedy = re.search(r"^remedy:\s+(.+)$", result.stderr, re.MULTILINE)
        assert remedy, diagnostic(result)
        fix = subprocess.run(
            ["bash", "-c", remedy.group(1)], capture_output=True, text=True, timeout=30
        )
        assert fix.returncode == 0, diagnostic(fix)
        assert "crontab_backups_keep = 10" in cfg.read_text(encoding="utf-8")
        rerun = _install(sandbox)
        assert rerun.returncode == 0, "the printed remedy must clear the refusal\n" + diagnostic(rerun)


def test_empty_crontab_restore_hint_lists_the_surviving_backups(
    tmp_path: pathlib.Path,
) -> None:
    sandbox = _settled_sandbox(tmp_path)
    pruned = _install(sandbox)
    assert pruned.returncode == 0, diagnostic(pruned)
    survivors = _backups(sandbox)
    sandbox.write_crontab("")

    result = sandbox.run(
        ["bash", "-c", f'source "{_SAFE_OVERWRITE}"; warn_if_empty_crontab']
    )

    assert result.returncode == 1, diagnostic(result)
    listing = [
        line.strip()[len("ls "):]
        for line in result.stdout.splitlines()
        if line.strip().startswith("ls ")
    ]
    assert len(listing) == 1, diagnostic(result)
    assert sorted(pathlib.Path(p) for p in glob.glob(listing[0])) == survivors, (
        f"the restore hint's listing must name exactly the surviving backups "
        f"{[p.name for p in survivors]}\n" + diagnostic(result)
    )
