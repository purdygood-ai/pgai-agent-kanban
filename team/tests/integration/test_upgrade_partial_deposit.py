"""
test_upgrade_partial_deposit.py
===============================
Integration tests for how team/scripts/upgrade.sh's phase-2 body ends when
the deposit did not put every changed item in place.

An upgrade run with no TTY and no --force skips every managed item that
differs from the source tree. Stamping VERSION over such a root would
record a version whose files are not all there, and the migrations up to
that version would fall out of every later run's (installed, target]
range. These tests pin the behaviour that prevents it:

  - a skipped item stops the run before the VERSION stamp and the
    migrations, and exits non-zero naming the --force re-run;
  - the --force re-run then stamps the new VERSION and applies every
    migration in the range from the old version;
  - a run where every managed item is already identical in place is a
    full deposit: it stamps VERSION and runs migrations as before;
  - the migrations come from the source tree's own runner, never from the
    copy already installed under the root;
  - a staged shipped skill edited in the skill staging directory is such an
    item: without --force it is left as edited and the refusal names it and
    the --force re-run, with --force it is overwritten -- while an
    operator-added skill beside it is byte-identical after both runs, never
    moved to retired/, and named as operator-owned.

Every test drives the real upgrade.sh as a subprocess against a throwaway
kanban root (tests/fixtures/upgrade_sandbox.py): HOME inside the sandbox, a
fake crontab on PATH, no controlling terminal.
"""

from __future__ import annotations

import pathlib
import re

from pgai_agent_kanban.lib import semver
from tests.fixtures.upgrade_sandbox import REPO_ROOT, UpgradeSandbox, diagnostic

_OLD_VERSION = "v2.22.5"
_MIGRATIONS_DIR = REPO_ROOT / "team" / "scripts" / "migrations"
_DESTINATION = re.compile(r"_to_(v\d+\.\d+\.\d+)\.sh$")
_SEEDED_KEY = re.compile(r"^\s*#?\s*overwatch_resolve\s*=", re.MULTILINE)


def _tree_version() -> str:
    return (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()


def _migrations_in_range() -> list[str]:
    """Every migration shipped next to this tree's runner whose destination
    falls in (_OLD_VERSION, this tree's VERSION] -- the set an upgrade from
    _OLD_VERSION must apply (team/scripts/migrations/README.md, "Selection
    rule"). A migration whose destination is above the tree's VERSION is
    not selected until VERSION reaches it."""
    target = _tree_version()
    names = []
    for path in _MIGRATIONS_DIR.glob("migrate_v*_to_v*.sh"):
        match = _DESTINATION.search(path.name)
        if match and semver.gt(match.group(1), _OLD_VERSION) and semver.le(match.group(1), target):
            names.append(path.name)
    return sorted(names)


def _migrations_reported(stdout: str) -> list[str]:
    return sorted(re.findall(r"\[run\]\s+(migrate_\S+\.sh)", stdout))


def _deposited_sandbox(tmp_path: pathlib.Path) -> UpgradeSandbox:
    """A root fully deposited from this tree, then wound back to look like
    an install at _OLD_VERSION that no migration has touched yet."""
    sandbox = UpgradeSandbox(tmp_path)
    original_cfg = sandbox.project_cfg.read_bytes()
    sandbox.version_file.write_text(f"{_OLD_VERSION}\n", encoding="utf-8")
    first = sandbox.run_upgrade_phase2()
    assert first.returncode == 0, "fixture deposit failed\n" + diagnostic(first)

    sandbox.version_file.write_text(f"{_OLD_VERSION}\n", encoding="utf-8")
    sandbox.project_cfg.write_bytes(original_cfg)
    return sandbox


def _overwatch_backlog(sandbox: UpgradeSandbox) -> pathlib.Path:
    return sandbox.root / "projects" / sandbox.project_name / "tasks" / "queues" / "overwatch_backlog.md"


def _assert_no_migration_ran(sandbox: UpgradeSandbox, cfg_before: bytes, result) -> None:
    assert (
        sandbox.project_cfg.read_bytes() == cfg_before
    ), "project.cfg changed although the run stopped before the migrations\n" + diagnostic(result)
    assert _migrations_reported(result.stdout) == [], diagnostic(result)


def _assert_every_migration_ran(sandbox: UpgradeSandbox, result) -> None:
    expected = _migrations_in_range()
    assert (
        _migrations_reported(result.stdout) == expected
    ), f"expected every migration in ({_OLD_VERSION}, {_tree_version()}] to run: {expected}\n" + diagnostic(result)
    assert _SEEDED_KEY.search(sandbox.project_cfg.read_text(encoding="utf-8")), diagnostic(result)
    # OVERWATCH keeps no queue file: its migration step is a narrated no-op.
    assert not _overwatch_backlog(sandbox).exists(), diagnostic(result)


class TestSkippedItemStopsBeforeStampAndMigrations:
    def test_differing_managed_directory_without_force_keeps_old_version(self, tmp_path: pathlib.Path) -> None:
        sandbox = _deposited_sandbox(tmp_path)
        role_file = sandbox.root / "roles" / "CODER.md"
        role_file.write_text(role_file.read_text(encoding="utf-8") + "\nlocal edit\n", encoding="utf-8")
        cfg_before = sandbox.project_cfg.read_bytes()

        result = sandbox.run_upgrade_phase2()
        output = result.stdout + result.stderr

        assert (
            result.returncode != 0
        ), "an upgrade that left a changed item undeposited must exit non-zero\n" + diagnostic(result)
        assert (
            sandbox.version_file.read_text(encoding="utf-8").strip() == _OLD_VERSION
        ), "VERSION must keep the old version when an item was skipped\n" + diagnostic(result)
        _assert_no_migration_ran(sandbox, cfg_before, result)
        assert "upgrade.partial-deposit" in output, diagnostic(result)
        assert "team/roles" in output, "the refusal must name the skipped item\n" + diagnostic(result)
        assert "--force" in output, "the refusal must name the --force re-run\n" + diagnostic(result)

    def test_force_rerun_stamps_new_version_and_applies_every_migration(self, tmp_path: pathlib.Path) -> None:
        sandbox = _deposited_sandbox(tmp_path)
        role_file = sandbox.root / "roles" / "CODER.md"
        role_file.write_text(role_file.read_text(encoding="utf-8") + "\nlocal edit\n", encoding="utf-8")

        refused = sandbox.run_upgrade_phase2()
        assert refused.returncode != 0, diagnostic(refused)

        result = sandbox.run_upgrade_phase2("--force")
        assert result.returncode == 0, diagnostic(result)
        assert sandbox.version_file.read_text(encoding="utf-8").strip() == _tree_version(), diagnostic(result)
        _assert_every_migration_ran(sandbox, result)
        assert (
            role_file.read_bytes() == (REPO_ROOT / "team" / "roles" / "CODER.md").read_bytes()
        ), "--force must deposit the skipped item\n" + diagnostic(result)


class TestFullDepositStampsAndMigrates:
    def test_identical_tree_without_force_stamps_version_and_runs_migrations(self, tmp_path: pathlib.Path) -> None:
        sandbox = _deposited_sandbox(tmp_path)

        result = sandbox.run_upgrade_phase2()

        assert result.returncode == 0, (
            "a root whose managed items are all identical in place is fully "
            "deposited and must not be refused\n" + diagnostic(result)
        )
        assert "upgrade.partial-deposit" not in result.stdout + result.stderr, diagnostic(result)
        assert sandbox.version_file.read_text(encoding="utf-8").strip() == _tree_version(), diagnostic(result)
        _assert_every_migration_ran(sandbox, result)

    def test_migrations_come_from_the_source_tree_runner(self, tmp_path: pathlib.Path) -> None:
        sandbox = _deposited_sandbox(tmp_path)
        marker = sandbox.root / "installed-runner-migration-ran"
        # A migration only the installed copy of scripts/migrations/ carries.
        # The installed runner would select it; the source tree's must not.
        installed_only = sandbox.root / "scripts" / "migrations" / "migrate_v2.22.5_to_v2.22.6.sh"
        installed_only.write_text(
            "#!/usr/bin/env bash\n" f"touch '{marker}'\n",
            encoding="utf-8",
        )
        installed_only.chmod(0o755)

        result = sandbox.run_upgrade_phase2()

        assert result.returncode == 0, diagnostic(result)
        assert not marker.exists(), (
            "a migration present only under the installed root ran: the "
            "upgrade used the installed runner, not the source tree's\n" + diagnostic(result)
        )
        _assert_every_migration_ran(sandbox, result)


class TestStagedSkillDeposit:
    """The skill staging directory of a root installed by this tree's install.sh."""

    _SHIPPED = "skills/claude/pgai-agent-kanban/SKILL.md"
    _OPERATOR_SKILL = "skills/claude/my-skill"

    @staticmethod
    def _tree(directory: pathlib.Path) -> dict[str, bytes]:
        return {str(p.relative_to(directory)): p.read_bytes() for p in sorted(directory.rglob("*")) if p.is_file()}

    def test_edited_shipped_skill_left_and_named_without_force_overwritten_with_force(
        self, tmp_path: pathlib.Path
    ) -> None:
        sandbox = UpgradeSandbox(tmp_path)
        installed = sandbox.run(
            [
                "bash",
                REPO_ROOT / "install.sh",
                "--force",
                "--no-system-cron",
                "--wake-tier",
                "none",
                "--stamp-version",
                "v0.0.0-sandbox-test",
            ]
        )
        assert installed.returncode == 0, "fixture install failed\n" + diagnostic(installed)
        staged = sandbox.root / self._SHIPPED
        assert staged.read_bytes() == (REPO_ROOT / self._SHIPPED).read_bytes(), diagnostic(installed)

        staged.write_text(staged.read_text(encoding="utf-8") + "\nlocal edit\n", encoding="utf-8")
        edited = staged.read_bytes()
        operator_skill = sandbox.root / self._OPERATOR_SKILL
        (operator_skill / "scripts").mkdir(parents=True)
        (operator_skill / "SKILL.md").write_text(
            "---\nname: my-skill\ndescription: An operator's own skill.\n---\nBody.\n", encoding="utf-8"
        )
        (operator_skill / "scripts" / "run.sh").write_text("echo mine\n", encoding="utf-8")
        operator_before = self._tree(operator_skill)

        refused = sandbox.run_upgrade_phase2("--stamp-version", "v0.0.1-sandbox-test")
        output = refused.stdout + refused.stderr

        assert refused.returncode != 0, diagnostic(refused)
        assert staged.read_bytes() == edited, "the edited staged skill was overwritten without --force"
        assert "upgrade.partial-deposit" in output, diagnostic(refused)
        assert "skills/claude/pgai-agent-kanban" in output, "the refusal must name the edited skill\n" + diagnostic(
            refused
        )
        assert "--force" in output, "the refusal must name the --force re-run\n" + diagnostic(refused)
        assert "Operator-owned skill (left alone): skills/claude/my-skill" in output, diagnostic(refused)
        assert self._tree(operator_skill) == operator_before, diagnostic(refused)

        result = sandbox.run_upgrade_phase2("--stamp-version", "v0.0.1-sandbox-test", "--force")
        output = result.stdout + result.stderr

        assert result.returncode == 0, diagnostic(result)
        assert (
            staged.read_bytes() == (REPO_ROOT / self._SHIPPED).read_bytes()
        ), "--force must overwrite the edited staged skill\n" + diagnostic(result)
        assert self._tree(operator_skill) == operator_before, diagnostic(result)
        summary = [ln for ln in output.splitlines() if ln.strip().startswith("Operator skills:")]
        assert len(summary) == 1 and "claude/my-skill" in summary[0], diagnostic(result)
        retired = sandbox.root / "retired"
        assert not retired.exists() or not list(retired.rglob("my-skill")), diagnostic(result)
