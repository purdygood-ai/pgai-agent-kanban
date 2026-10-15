"""
test_claude_env_install_seeding.py
====================================
Behavioral tests for claude-env's install/upgrade seeding contract:
install.sh seeds a live claude-env from claude-env_example create-if-absent
on a fresh install, and team/scripts/upgrade.sh does the same on upgrade
while never overwriting an existing live file
(requirements/v2.14.0-provider-runtime-hardening.md).

WHAT IS TESTED
--------------
1. **Fresh install seeds claude-env from the template.** A fresh
   ``install.sh`` run against an empty root deposits both
   ``claude-env_example`` and a live ``claude-env``, and the live file's
   contents derive from (are byte-identical to) the template.

2. **Upgrade preserves a modified live file.** ``upgrade.sh`` never
   overwrites an existing ``claude-env`` — a hand-edited file is byte-for-byte
   unchanged after an upgrade run, even though the same run refreshes
   ``claude-env_example`` to the shipped version.

3. **Upgrade creates the live file when only the template is present.** An
   install that has ``claude-env_example`` but no live ``claude-env`` (the
   shape of any install that predates the file's introduction) gets the
   live file created on upgrade.

4. **Repeated upgrades are idempotent.** Running upgrade.sh twice in a row
   against a root with a modified ``claude-env`` leaves the file
   byte-identical after both runs — the create-if-absent seed step never
   fires once the file exists.

5. **The example template refreshes independently of the live file.**
   ``claude-env_example`` is refreshed to the shipped version on upgrade in
   the very same run that leaves the live ``claude-env`` untouched — proving
   the two are governed by different rules (template: always refreshed;
   live file: create-if-absent, never overwritten).

SANDBOX DESIGN
--------------
install.sh runs directly from the real dev tree (this checkout) against a
throwaway ``PGAI_AGENT_KANBAN_ROOT_PATH`` and throwaway ``HOME`` — the same
"real script, live layout" approach used by
test_subagent_deploy_dir_creation.py and test_install_version_targeting.py.
``--stamp-version`` bypasses git tag resolution so no fixture git repo is
needed.

upgrade.sh's phase-2 body is invoked directly with ``--phase2
--phase2-protocol 1 --kanban-root <path>``, bypassing the phase-1
bootstrap/re-exec dance (covered elsewhere). Phase 2 infers DEV_TREE from
its own BASH_SOURCE, so pointing at this checkout's team/scripts/upgrade.sh
exercises the real, just-edited deposit and seed logic directly — no
synthetic dev-tree stub is built.

Neither entry point touches the real kanban root at
$PGAI_AGENT_KANBAN_ROOT_PATH; every root used here lives under pytest's
tmp_path (redirected under PGAI_AGENT_KANBAN_TEMP_DIR/tests by conftest.py).

NAMING CONVENTION
-----------------
Function names describe the behavior under test, not the ticket ID.
"""

from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

# ---------------------------------------------------------------------------
# Paths to the real scripts under test
# ---------------------------------------------------------------------------

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent
_REPO_ROOT = _TEAM_DIR.parent

_REAL_INSTALL_SH = _REPO_ROOT / "install.sh"
_REAL_UPGRADE_SH = _TEAM_DIR / "scripts" / "upgrade.sh"
_REAL_CLAUDE_ENV_TEMPLATE = _REPO_ROOT / "claude-env_example"

_TIMEOUT = 120


# ---------------------------------------------------------------------------
# Shared scaffolding
# ---------------------------------------------------------------------------


def _run_install(
    *,
    home: pathlib.Path,
    kanban_root: pathlib.Path,
    stamp_version: str = "v0.0.0-sandbox-test",
) -> subprocess.CompletedProcess:
    """Run the real install.sh against a throwaway root.

    Uses --force --no-system-cron so the run is non-interactive and never
    touches the host crontab; --stamp-version avoids a git resolution call.
    Neither --add-claude-agents nor --add-codex-agents is passed, so
    nothing is written under HOME beyond install.sh's own bookkeeping.
    """
    env = dict(os.environ)
    env.update(
        {
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(kanban_root),
            "HOME": str(home),
        }
    )

    return subprocess.run(
        [
            "bash",
            str(_REAL_INSTALL_SH),
            "--force",
            "--no-system-cron",
            "--stamp-version",
            stamp_version,
        ],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
        env=env,
    )


def _run_upgrade_phase2(
    *,
    home: pathlib.Path,
    kanban_root: pathlib.Path,
    stamp_version: str = "v0.0.1-sandbox-test",
) -> subprocess.CompletedProcess:
    """Run the real upgrade.sh phase-2 body directly against kanban_root.

    Bypasses the phase-1 bootstrap/re-exec dance so the test stays focused
    on the deposit/seed logic. DEV_TREE is inferred by the script itself
    from BASH_SOURCE (this checkout's team/scripts/upgrade.sh), so this
    exercises the real, just-edited code.
    """
    kanban_root.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env.update(
        {
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(kanban_root),
            "HOME": str(home),
        }
    )

    return subprocess.run(
        [
            "bash",
            str(_REAL_UPGRADE_SH),
            "--phase2",
            "--phase2-protocol",
            "1",
            "--kanban-root",
            str(kanban_root),
            "--force",
            "--stamp-version",
            stamp_version,
        ],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
        env=env,
    )


def _combined_output(result: subprocess.CompletedProcess) -> str:
    return f"{result.stdout}\n{result.stderr}"


# ---------------------------------------------------------------------------
# install.sh — fresh-install seeding
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _REAL_INSTALL_SH.exists(),
    reason=f"Real install.sh not found at {_REAL_INSTALL_SH}; skipping.",
)
def test_fresh_install_seeds_claude_env_from_template(tmp_path: pathlib.Path) -> None:
    """A fresh install against an empty root produces both the template and
    a live claude-env whose contents derive from (are byte-identical to)
    that template."""
    home = tmp_path / "home"
    home.mkdir()
    kanban_root = tmp_path / "kanban_root"

    result = _run_install(home=home, kanban_root=kanban_root)
    output = _combined_output(result)

    assert result.returncode == 0, f"install.sh failed:\n{output}"

    template = kanban_root / "claude-env_example"
    live = kanban_root / "claude-env"

    assert template.is_file(), f"claude-env_example was not deposited:\n{output}"
    assert live.is_file(), f"claude-env was not seeded:\n{output}"
    assert "Initialized: claude-env (from claude-env_example)" in output, output
    assert live.read_bytes() == template.read_bytes(), (
        "live claude-env does not derive from claude-env_example"
    )


# ---------------------------------------------------------------------------
# upgrade.sh — preservation of a modified live file
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _REAL_UPGRADE_SH.exists(),
    reason=f"Real upgrade.sh not found at {_REAL_UPGRADE_SH}; skipping.",
)
def test_upgrade_preserves_hand_edited_claude_env_byte_for_byte(
    tmp_path: pathlib.Path,
) -> None:
    """upgrade.sh must never overwrite an existing claude-env, even though
    the same run refreshes claude-env_example to the shipped version."""
    home = tmp_path / "home"
    home.mkdir()
    kanban_root = tmp_path / "kanban_root"
    kanban_root.mkdir()

    modified_content = (
        "# operator hand-tuned this file after an afternoon of CLI audit\n"
        "export PGAI_CLAUDE_CUSTOM_TUNING=1\n"
    )
    (kanban_root / "claude-env").write_text(modified_content, encoding="utf-8")

    result = _run_upgrade_phase2(home=home, kanban_root=kanban_root)
    output = _combined_output(result)

    assert result.returncode == 0, f"upgrade.sh failed:\n{output}"
    assert (kanban_root / "claude-env").read_text(encoding="utf-8") == modified_content, (
        "upgrade overwrote a hand-edited claude-env"
    )
    # claude-env is never passed through deposit_item (it is not part of the
    # team/docs/example item loops), so the per-item "Protected (preserved):
    # <label>" narration never fires for it specifically. Its protected
    # status is instead confirmed by the closing summary, which lists it
    # among the files the run declares preserved.
    assert "Protected (preserved):" in output, output
    assert "claude-env" in output.split("Protected (preserved):", 1)[1], output


# ---------------------------------------------------------------------------
# upgrade.sh — create-if-absent when only the template is present
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _REAL_UPGRADE_SH.exists(),
    reason=f"Real upgrade.sh not found at {_REAL_UPGRADE_SH}; skipping.",
)
def test_upgrade_creates_claude_env_when_only_template_present(
    tmp_path: pathlib.Path,
) -> None:
    """An install with claude-env_example but no live claude-env (the shape
    of an install that was never seeded with one) gets the live file
    created on upgrade."""
    home = tmp_path / "home"
    home.mkdir()
    kanban_root = tmp_path / "kanban_root"
    kanban_root.mkdir()

    # Stand in for a stale, pre-ticket template already on disk. It gets
    # refreshed to the shipped version in the same run that creates the
    # live file from that refreshed template.
    (kanban_root / "claude-env_example").write_text(
        "# stale pre-ticket template\n", encoding="utf-8"
    )
    assert not (kanban_root / "claude-env").exists()

    result = _run_upgrade_phase2(home=home, kanban_root=kanban_root)
    output = _combined_output(result)

    assert result.returncode == 0, f"upgrade.sh failed:\n{output}"
    live = kanban_root / "claude-env"
    assert live.is_file(), f"claude-env was not created:\n{output}"
    assert "Initialized: claude-env (from claude-env_example)" in output, output
    # The live file derives from the freshly-refreshed template, not the
    # stale one that was on disk before the run.
    assert live.read_bytes() == _REAL_CLAUDE_ENV_TEMPLATE.read_bytes()


# ---------------------------------------------------------------------------
# upgrade.sh — idempotence across repeated runs
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _REAL_UPGRADE_SH.exists(),
    reason=f"Real upgrade.sh not found at {_REAL_UPGRADE_SH}; skipping.",
)
def test_upgrade_twice_in_a_row_leaves_modified_claude_env_byte_identical(
    tmp_path: pathlib.Path,
) -> None:
    """Running upgrade.sh twice in a row against a root with a modified
    claude-env must leave the file byte-identical after both runs."""
    home = tmp_path / "home"
    home.mkdir()
    kanban_root = tmp_path / "kanban_root"
    kanban_root.mkdir()

    modified_content = "export PGAI_CLAUDE_CUSTOM_TUNING=twice-in-a-row\n"
    (kanban_root / "claude-env").write_text(modified_content, encoding="utf-8")

    first = _run_upgrade_phase2(
        home=home, kanban_root=kanban_root, stamp_version="v0.0.1-sandbox-test"
    )
    assert first.returncode == 0, f"first upgrade.sh run failed:\n{_combined_output(first)}"
    after_first = (kanban_root / "claude-env").read_text(encoding="utf-8")
    assert after_first == modified_content

    second = _run_upgrade_phase2(
        home=home, kanban_root=kanban_root, stamp_version="v0.0.2-sandbox-test"
    )
    assert second.returncode == 0, f"second upgrade.sh run failed:\n{_combined_output(second)}"
    after_second = (kanban_root / "claude-env").read_text(encoding="utf-8")
    assert after_second == modified_content, (
        "claude-env changed between the first and second upgrade.sh run"
    )


# ---------------------------------------------------------------------------
# upgrade.sh — the template refreshes independently of the live file
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _REAL_UPGRADE_SH.exists(),
    reason=f"Real upgrade.sh not found at {_REAL_UPGRADE_SH}; skipping.",
)
def test_upgrade_refreshes_example_while_preserving_live_file(
    tmp_path: pathlib.Path,
) -> None:
    """claude-env_example is refreshed to the shipped version on upgrade in
    the same run that leaves a modified live claude-env untouched — the two
    files are governed by different rules."""
    home = tmp_path / "home"
    home.mkdir()
    kanban_root = tmp_path / "kanban_root"
    kanban_root.mkdir()

    stale_template = "# stale pre-upgrade template, must be replaced\n"
    (kanban_root / "claude-env_example").write_text(stale_template, encoding="utf-8")

    modified_live = "export PGAI_CLAUDE_CUSTOM_TUNING=template-refresh-check\n"
    (kanban_root / "claude-env").write_text(modified_live, encoding="utf-8")

    result = _run_upgrade_phase2(home=home, kanban_root=kanban_root)
    output = _combined_output(result)

    assert result.returncode == 0, f"upgrade.sh failed:\n{output}"

    template_after = (kanban_root / "claude-env_example").read_bytes()
    live_after = (kanban_root / "claude-env").read_text(encoding="utf-8")

    assert template_after == _REAL_CLAUDE_ENV_TEMPLATE.read_bytes(), (
        "claude-env_example was not refreshed to the shipped version"
    )
    assert template_after != stale_template.encode("utf-8")
    assert live_after == modified_live, (
        "live claude-env was overwritten even though only the template should refresh"
    )
