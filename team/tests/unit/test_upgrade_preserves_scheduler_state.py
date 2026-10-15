"""
test_upgrade_preserves_scheduler_state.py
=========================================
Behavioural tests: a plain upgrade leaves the operator's scheduler state --
the per-user crontab and pseudocron.cfg -- exactly as it found it.

  - upgrade.sh without --wake-tier never writes the crontab, whatever it
    holds; the tier it detects is reported only.
  - Tier detection does not invent a tier: a crontab with a "# Tier:"
    header and cleanup lines but no wake job line is detected as none.
  - An upgrade on a host with an empty crontab (a pseudocron host) reports
    no pseudocron failure and leaves a hand-tuned pseudocron.cfg alone.
  - install-pseudocron.sh --keep-existing, the upgrade hook's mode, leaves
    an existing pseudocron.cfg byte-identical.

Every test drives the real script as a subprocess against a throwaway
kanban root (tests/fixtures/upgrade_sandbox.py): HOME inside the sandbox
and a fake crontab on PATH, so the operator's crontab is never read or
written.
"""

from __future__ import annotations

import pathlib

from tests.fixtures.upgrade_sandbox import (
    INSTALL_PSEUDOCRON_SCRIPT,
    PSEUDOCRON_TEMPLATES_DIR,
    UpgradeSandbox,
    diagnostic,
)


def _kanban_and_foreign_crontab(root: pathlib.Path) -> str:
    return (
        "MAILTO=ops@example.com\n"
        "15 3 * * * /usr/local/bin/nightly-backup.sh\n"
        "\n"
        "# Tier: small\n"
        f"*/5 * * * * {root}/scripts/wake-batch.sh --agent=pm --sleep=0 >> {root}/logs/cron-pm.log 2>&1\n"
        f"3-59/5 * * * * {root}/scripts/wake-batch.sh --agent=coder --sleep=0 >> {root}/logs/cron-coder.log 2>&1\n"
        f"0 4 * * * {root}/scripts/cleanup.sh >> {root}/logs/cleanup.log 2>&1\n"
    )


def _hand_tuned_pseudocron_cfg() -> str:
    template = (PSEUDOCRON_TEMPLATES_DIR / "pseudocron-small.cfg.example").read_text(encoding="utf-8")
    return template + "\n# operator: CODER every 2 minutes on this host\n"


class TestPlainUpgradeLeavesCrontabAlone:
    def test_crontab_with_foreign_and_kanban_lines_is_byte_identical(
        self, tmp_path: pathlib.Path
    ) -> None:
        sandbox = UpgradeSandbox(tmp_path)
        sandbox.write_crontab(_kanban_and_foreign_crontab(sandbox.root))
        before = sandbox.crontab_bytes()

        result = sandbox.run_upgrade_phase2()

        assert result.returncode == 0, diagnostic(result)
        assert sandbox.crontab_bytes() == before, (
            "an upgrade without --wake-tier must not write the crontab\n" + diagnostic(result)
        )
        assert "Detected installed tier: small" in result.stdout, diagnostic(result)
        assert "Crontab: left untouched" in result.stdout, diagnostic(result)

    def test_tier_header_without_a_wake_job_is_detected_as_none(
        self, tmp_path: pathlib.Path
    ) -> None:
        sandbox = UpgradeSandbox(tmp_path)
        root = sandbox.root
        sandbox.write_crontab(
            "# Tier: small\n"
            f"0 4 * * * {root}/scripts/cleanup.sh >> {root}/logs/cleanup.log 2>&1\n"
            f"30 4 * * 0 {root}/scripts/cleanup.sh --deep >> {root}/logs/cleanup.log 2>&1\n"
        )
        before = sandbox.crontab_bytes()

        result = sandbox.run_upgrade_phase2()

        assert result.returncode == 0, diagnostic(result)
        assert "Detected installed tier: none" in result.stdout, (
            "a crontab with no wake job line has no wake tier installed\n" + diagnostic(result)
        )
        assert sandbox.crontab_bytes() == before, diagnostic(result)


class TestPseudocronStateSurvivesUpgrade:
    def test_empty_crontab_upgrade_reports_no_pseudocron_failure_and_keeps_cfg(
        self, tmp_path: pathlib.Path
    ) -> None:
        sandbox = UpgradeSandbox(tmp_path)
        sandbox.write_crontab("")
        sandbox.pseudocron_cfg.write_text(_hand_tuned_pseudocron_cfg(), encoding="utf-8")
        before = sandbox.pseudocron_cfg.read_bytes()

        result = sandbox.run_upgrade_phase2()
        output = result.stdout + result.stderr

        assert result.returncode == 0, diagnostic(result)
        assert "Pseudocron: installation failed" not in output, diagnostic(result)
        assert sandbox.pseudocron_cfg.read_bytes() == before, (
            "an upgrade must leave a hand-tuned pseudocron.cfg byte-identical\n" + diagnostic(result)
        )
        assert sandbox.crontab_bytes() == b"", diagnostic(result)

    def test_pseudocron_cfg_without_a_tier_header_skips_the_hook(
        self, tmp_path: pathlib.Path
    ) -> None:
        sandbox = UpgradeSandbox(tmp_path)
        sandbox.pseudocron_cfg.write_text(
            "# operator-written schedule\n*/10 * * * * scripts/wake-batch.sh --agent=pm\n",
            encoding="utf-8",
        )
        before = sandbox.pseudocron_cfg.read_bytes()

        result = sandbox.run_upgrade_phase2()
        output = result.stdout + result.stderr

        assert result.returncode == 0, diagnostic(result)
        assert "Pseudocron: skipped (no tier" in output, diagnostic(result)
        assert "Pseudocron: installation failed" not in output, diagnostic(result)
        assert sandbox.pseudocron_cfg.read_bytes() == before, diagnostic(result)

    def test_keep_existing_leaves_a_hand_tuned_cfg_byte_identical(
        self, tmp_path: pathlib.Path
    ) -> None:
        sandbox = UpgradeSandbox(tmp_path)
        sandbox.pseudocron_cfg.write_text(_hand_tuned_pseudocron_cfg(), encoding="utf-8")
        before = sandbox.pseudocron_cfg.read_bytes()

        result = sandbox.run(
            ["bash", INSTALL_PSEUDOCRON_SCRIPT, "--wake-tier=small", "--keep-existing", "--yes"]
        )

        assert result.returncode == 0, diagnostic(result)
        assert sandbox.pseudocron_cfg.read_bytes() == before, (
            "--keep-existing must never overwrite an existing pseudocron.cfg\n" + diagnostic(result)
        )
        assert "kept as-is" in result.stdout + result.stderr, diagnostic(result)
        assert (sandbox.root / "pseudocron.env").is_file(), (
            "--keep-existing still seeds a missing pseudocron.env\n" + diagnostic(result)
        )
