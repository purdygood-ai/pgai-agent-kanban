"""
test_install_crontab_keeps_foreign_lines.py
===========================================
Behavioural tests: install-crontab.sh with an explicit --wake-tier rewrites
only the kanban-managed crontab lines -- the marked pgai-agent-kanban block
for this install root and unmarked lines naming the root -- and keeps every
other line: the operator's own jobs, MAILTO/PATH settings, another
install's block.

Every test drives the real script as a subprocess against a throwaway
kanban root (tests/fixtures/upgrade_sandbox.py) with a fake crontab on PATH,
so the operator's crontab is never read or written.
"""

from __future__ import annotations

import pathlib

from tests.fixtures.upgrade_sandbox import INSTALL_CRONTAB_SCRIPT, UpgradeSandbox, diagnostic

_FOREIGN = (
    "MAILTO=ops@example.com\n"
    "15 3 * * * /usr/local/bin/nightly-backup.sh\n"
)
_BEGIN = "# BEGIN pgai-agent-kanban managed block"
_END = "# END pgai-agent-kanban managed block"


def _install(sandbox: UpgradeSandbox, tier: str):
    return sandbox.run(["bash", INSTALL_CRONTAB_SCRIPT, f"--wake-tier={tier}", "--yes"])


def _crontab_text(sandbox: UpgradeSandbox) -> str:
    data = sandbox.crontab_bytes()
    assert data is not None, "no crontab was installed"
    return data.decode("utf-8")


def _wake_lines(text: str, root: pathlib.Path) -> list[str]:
    return [
        line for line in text.splitlines()
        if not line.lstrip().startswith("#") and f"{root}/scripts/wake-batch.sh" in line
    ]


class TestExplicitTierKeepsForeignLines:
    def test_non_kanban_job_is_kept_and_kanban_lines_are_written(
        self, tmp_path: pathlib.Path
    ) -> None:
        sandbox = UpgradeSandbox(tmp_path)
        sandbox.write_crontab(_FOREIGN)

        result = _install(sandbox, "small")

        assert result.returncode == 0, diagnostic(result)
        text = _crontab_text(sandbox)
        assert text.startswith(_FOREIGN), (
            f"the operator's own lines must be kept, in order, ahead of the kanban block\n"
            f"crontab:\n{text}\n" + diagnostic(result)
        )
        assert f"{_BEGIN}: {sandbox.root}" in text and f"{_END}: {sandbox.root}" in text, text
        assert _wake_lines(text, sandbox.root), f"no kanban wake lines written\n{text}"

    def test_rerun_with_the_same_tier_is_byte_identical(self, tmp_path: pathlib.Path) -> None:
        sandbox = UpgradeSandbox(tmp_path)
        sandbox.write_crontab(_FOREIGN)
        first = _install(sandbox, "small")
        assert first.returncode == 0, diagnostic(first)
        after_first = sandbox.crontab_bytes()

        second = _install(sandbox, "small")

        assert second.returncode == 0, diagnostic(second)
        assert sandbox.crontab_bytes() == after_first, diagnostic(second)

    def test_tier_change_replaces_only_the_kanban_block(self, tmp_path: pathlib.Path) -> None:
        sandbox = UpgradeSandbox(tmp_path)
        sandbox.write_crontab(_FOREIGN)
        small = _install(sandbox, "small")
        assert small.returncode == 0, diagnostic(small)

        result = _install(sandbox, "large")

        assert result.returncode == 0, diagnostic(result)
        text = _crontab_text(sandbox)
        assert text.startswith(_FOREIGN), text
        assert text.count(_BEGIN) == 1, f"expected exactly one kanban block\n{text}"
        assert "# Tier: large" in text and "# Tier: small" not in text, text

    def test_unmarked_lines_of_an_earlier_install_are_replaced_not_duplicated(
        self, tmp_path: pathlib.Path
    ) -> None:
        sandbox = UpgradeSandbox(tmp_path)
        root = sandbox.root
        sandbox.write_crontab(
            _FOREIGN
            + "# Tier: small\n"
            + f"*/5 * * * * {root}/scripts/wake-batch.sh --agent=pm --sleep=0 >> {root}/logs/cron-pm.log 2>&1\n"
            + f"0 4 * * * {root}/scripts/cleanup.sh >> {root}/logs/cleanup.log 2>&1\n"
        )

        result = _install(sandbox, "small")

        assert result.returncode == 0, diagnostic(result)
        text = _crontab_text(sandbox)
        outside_block = text.split(_BEGIN, 1)[0]
        assert outside_block == _FOREIGN + "\n", (
            f"only the operator's lines may remain outside the kanban block\n{text}"
        )

    def test_another_install_roots_block_is_kept(self, tmp_path: pathlib.Path) -> None:
        sandbox = UpgradeSandbox(tmp_path)
        other_root = tmp_path / "other_kanban_root"
        other_block = (
            f"{_BEGIN}: {other_root}\n"
            f"*/5 * * * * {other_root}/scripts/wake-batch.sh --agent=pm\n"
            f"{_END}: {other_root}\n"
        )
        sandbox.write_crontab(_FOREIGN + other_block)

        result = _install(sandbox, "small")

        assert result.returncode == 0, diagnostic(result)
        text = _crontab_text(sandbox)
        assert text.startswith(_FOREIGN + other_block), text
        assert f"{_BEGIN}: {sandbox.root}" in text, text

    def test_unterminated_kanban_block_is_refused_and_crontab_unchanged(
        self, tmp_path: pathlib.Path
    ) -> None:
        sandbox = UpgradeSandbox(tmp_path)
        sandbox.write_crontab(
            _FOREIGN
            + f"{_BEGIN}: {sandbox.root}\n"
            + f"*/5 * * * * {sandbox.root}/scripts/wake-batch.sh --agent=pm\n"
            + "30 2 * * * /usr/local/bin/rotate-logs.sh\n"
        )
        before = sandbox.crontab_bytes()

        result = _install(sandbox, "small")

        assert result.returncode != 0, (
            "a BEGIN marker with no END cannot be merged safely; the run must refuse\n"
            + diagnostic(result)
        )
        assert sandbox.crontab_bytes() == before, diagnostic(result)
        assert f"{_END}: {sandbox.root}" in result.stderr, diagnostic(result)
