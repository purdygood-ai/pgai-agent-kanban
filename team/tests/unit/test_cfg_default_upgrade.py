"""
test_cfg_default_upgrade.py
===========================
Interface tests for pgai_agent_kanban.lib.cfg_default_upgrade: the shared
logic that moves a kanban.cfg still holding a previously shipped default onto
the new default, and reports (never writes) each project's own project.cfg
value.

Coverage:
  - Each key is classified by what the file holds: the previous default
    (rewritten), the new default (already applied), absent (built-in default
    applies) or anything else (an operator's choice, kept).
  - A rewrite changes only the matched lines; every other byte, the missing
    final newline and the permission bits survive.
  - A second run and a dry run write nothing.
  - project.cfg values are reported per project and never written.
  - A kanban.cfg that cannot be rewritten raises, is left unchanged, and the
    command line reports it dual-channel with its own exit code.
  - Every change rendered for the operator carries its one revert line.

Every test builds its files under tmp_path; nothing reads or writes a live
kanban root.
"""

from __future__ import annotations

import os
import pathlib
import stat

import pytest

from pgai_agent_kanban.lib import cfg_default_upgrade as cdu
from pgai_agent_kanban.lib.error_contract import parse_error_contract

_SHIP_POSTURE = cdu.DefaultChange("release", "ship_posture", "matrix", "always", "ships instead of halting")
_STOP_ON_BLOCKED = cdu.DefaultChange("wake", "stop_on_blocked", "true", "false", "the batch carries on")
_CHANGES = (_SHIP_POSTURE, _STOP_ON_BLOCKED)

_OLD_KANBAN_CFG = """\
[paths]
temp_dir = /srv/kanban-tmp

[wake]
# What a BLOCKED task stops.
max_tasks_per_wake = 5
stop_on_blocked = true
pause_between_tasks = 5

[release]
# --- Ship posture
ship_posture = matrix
deferred_bugs = false
"""


def _write(path: pathlib.Path, text: str) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _statuses(outcomes: list[cdu.ChangeOutcome]) -> dict[str, str]:
    return {o.change.key: o.status for o in outcomes}


def _changed_line_indexes(before: str, after: str) -> list[int]:
    old, new = before.split("\n"), after.split("\n")
    assert len(old) == len(new), f"line count changed:\nbefore:\n{before}\nafter:\n{after}"
    return [i for i, (a, b) in enumerate(zip(old, new)) if a != b]


class TestPlanChangesClassifiesEachKey:
    def test_previous_defaults_are_planned_for_rewrite(self, tmp_path: pathlib.Path) -> None:
        cfg = _write(tmp_path / "kanban.cfg", _OLD_KANBAN_CFG)
        outcomes = cdu.plan_changes(cfg, _CHANGES)
        assert _statuses(outcomes) == {"ship_posture": cdu.WOULD_CHANGE, "stop_on_blocked": cdu.WOULD_CHANGE}
        assert [o.found for o in outcomes] == ["matrix", "true"]

    def test_new_defaults_read_as_already_applied(self, tmp_path: pathlib.Path) -> None:
        cfg = _write(tmp_path / "kanban.cfg", "[wake]\nstop_on_blocked = false\n\n[release]\nship_posture = always\n")
        assert _statuses(cdu.plan_changes(cfg, _CHANGES)) == {
            "ship_posture": cdu.ALREADY,
            "stop_on_blocked": cdu.ALREADY,
        }

    def test_absent_and_blank_keys_follow_the_built_in_default(self, tmp_path: pathlib.Path) -> None:
        cfg = _write(tmp_path / "kanban.cfg", "[wake]\nstop_on_blocked =\n\n[release]\n# ship_posture = matrix\n")
        outcomes = cdu.plan_changes(cfg, _CHANGES)
        assert _statuses(outcomes) == {"ship_posture": cdu.ABSENT, "stop_on_blocked": cdu.ABSENT}

    def test_a_missing_file_is_absent_for_every_key(self, tmp_path: pathlib.Path) -> None:
        outcomes = cdu.plan_changes(tmp_path / "kanban.cfg", _CHANGES)
        assert _statuses(outcomes) == {"ship_posture": cdu.ABSENT, "stop_on_blocked": cdu.ABSENT}
        assert [o.found for o in outcomes] == [None, None]

    def test_any_other_value_is_an_operator_choice(self, tmp_path: pathlib.Path) -> None:
        cfg = _write(tmp_path / "kanban.cfg", "[wake]\nstop_on_blocked = True\n\n[release]\nship_posture = Matrix\n")
        outcomes = cdu.plan_changes(cfg, _CHANGES)
        assert _statuses(outcomes) == {"ship_posture": cdu.CUSTOM, "stop_on_blocked": cdu.CUSTOM}

    def test_a_key_in_another_section_is_not_matched(self, tmp_path: pathlib.Path) -> None:
        cfg = _write(tmp_path / "kanban.cfg", "[release]\nstop_on_blocked = true\n\n[wake]\nship_posture = matrix\n")
        assert _statuses(cdu.plan_changes(cfg, _CHANGES)) == {
            "ship_posture": cdu.ABSENT,
            "stop_on_blocked": cdu.ABSENT,
        }


class TestUpgradeRewritesOnlyThePreviousDefaults:
    def test_both_lines_move_and_every_other_line_keeps_its_bytes(self, tmp_path: pathlib.Path) -> None:
        cfg = _write(tmp_path / "kanban.cfg", _OLD_KANBAN_CFG)

        outcomes = cdu.upgrade_cfg_defaults(cfg, _CHANGES)

        assert _statuses(outcomes) == {"ship_posture": cdu.CHANGED, "stop_on_blocked": cdu.CHANGED}
        after = cfg.read_text(encoding="utf-8")
        changed = _changed_line_indexes(_OLD_KANBAN_CFG, after)
        lines = after.split("\n")
        assert [lines[i] for i in changed] == ["stop_on_blocked = false", "ship_posture = always"]

    def test_a_second_run_writes_nothing_and_reports_already_applied(self, tmp_path: pathlib.Path) -> None:
        cfg = _write(tmp_path / "kanban.cfg", _OLD_KANBAN_CFG)
        cdu.upgrade_cfg_defaults(cfg, _CHANGES)
        after_first = cfg.read_bytes()

        second = cdu.upgrade_cfg_defaults(cfg, _CHANGES)

        assert _statuses(second) == {"ship_posture": cdu.ALREADY, "stop_on_blocked": cdu.ALREADY}
        assert cfg.read_bytes() == after_first

    def test_dry_run_writes_nothing(self, tmp_path: pathlib.Path) -> None:
        cfg = _write(tmp_path / "kanban.cfg", _OLD_KANBAN_CFG)

        outcomes = cdu.upgrade_cfg_defaults(cfg, _CHANGES, dry_run=True)

        assert _statuses(outcomes) == {"ship_posture": cdu.WOULD_CHANGE, "stop_on_blocked": cdu.WOULD_CHANGE}
        assert cfg.read_text(encoding="utf-8") == _OLD_KANBAN_CFG

    def test_an_operator_value_is_left_exactly_as_set(self, tmp_path: pathlib.Path) -> None:
        text = "[wake]\nstop_on_blocked = true\n\n[release]\nship_posture = mtarix\n"
        cfg = _write(tmp_path / "kanban.cfg", text)

        outcomes = cdu.upgrade_cfg_defaults(cfg, _CHANGES)

        assert _statuses(outcomes) == {"ship_posture": cdu.CUSTOM, "stop_on_blocked": cdu.CHANGED}
        assert cfg.read_text(encoding="utf-8") == text.replace("stop_on_blocked = true", "stop_on_blocked = false")

    def test_a_file_without_a_final_newline_still_has_none(self, tmp_path: pathlib.Path) -> None:
        cfg = _write(tmp_path / "kanban.cfg", "[release]\nship_posture = matrix")
        cdu.upgrade_cfg_defaults(cfg, _CHANGES)
        assert cfg.read_text(encoding="utf-8") == "[release]\nship_posture = always"

    def test_the_file_keeps_its_permission_bits(self, tmp_path: pathlib.Path) -> None:
        cfg = _write(tmp_path / "kanban.cfg", _OLD_KANBAN_CFG)
        cfg.chmod(0o640)
        cdu.upgrade_cfg_defaults(cfg, _CHANGES)
        assert stat.S_IMODE(cfg.stat().st_mode) == 0o640

    def test_a_missing_file_is_not_created(self, tmp_path: pathlib.Path) -> None:
        cfg = tmp_path / "kanban.cfg"
        outcomes = cdu.upgrade_cfg_defaults(cfg, _CHANGES)
        assert _statuses(outcomes) == {"ship_posture": cdu.ABSENT, "stop_on_blocked": cdu.ABSENT}
        assert not cfg.exists()

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory write permission")
    def test_an_unwritable_file_raises_and_is_left_unchanged(self, tmp_path: pathlib.Path) -> None:
        cfg = _write(tmp_path / "locked" / "kanban.cfg", _OLD_KANBAN_CFG)
        cfg.parent.chmod(0o500)
        try:
            with pytest.raises(OSError):
                cdu.upgrade_cfg_defaults(cfg, _CHANGES)
            assert cfg.read_text(encoding="utf-8") == _OLD_KANBAN_CFG
        finally:
            cfg.parent.chmod(0o700)


class TestProjectValuesAreReportedNeverWritten:
    def test_each_project_reports_its_own_value_or_none(self, tmp_path: pathlib.Path) -> None:
        root = tmp_path / "root"
        texts = {
            "project-a": "[project]\nproject_name = project-a\n\n[release]\nship_posture = matrix\n",
            "project-b": "[project]\nproject_name = project-b\n\n[release]\nship_posture = always\n",
            "project-c": "[project]\nproject_name = project-c\n",
        }
        for name, text in texts.items():
            _write(root / "projects" / name / "project.cfg", text)
        (root / "projects" / "project-d").mkdir(parents=True)

        values = cdu.project_values(
            root, ["project-a", "project-b", "project-c", "project-d"], "release", "ship_posture"
        )

        assert [(v.project, v.value) for v in values] == [
            ("project-a", "matrix"),
            ("project-b", "always"),
            ("project-c", ""),
            ("project-d", ""),
        ]
        assert values[0].source == str(root / "projects" / "project-a" / "project.cfg")
        assert values[3].source == ""
        for name, text in texts.items():
            assert (root / "projects" / name / "project.cfg").read_text(encoding="utf-8") == text

    def test_a_legacy_project_file_is_read_too(self, tmp_path: pathlib.Path) -> None:
        root = tmp_path / "root"
        _write(root / "projects" / "project-a" / "PROJECT.cfg", "ship_posture = matrix\n")
        values = cdu.project_values(root, ["project-a"], "release", "ship_posture")
        assert values[0].value == "matrix"


class TestRenderedOutputStatesEachChange:
    def test_each_change_carries_the_line_that_reverts_it(self, tmp_path: pathlib.Path) -> None:
        cfg = _write(tmp_path / "kanban.cfg", _OLD_KANBAN_CFG)
        lines = cdu.render_outcomes(cfg, cdu.upgrade_cfg_defaults(cfg, _CHANGES))
        text = "\n".join(lines)
        assert "[release] ship_posture: matrix -> always" in text
        assert "[wake] stop_on_blocked: true -> false" in text
        assert f"under [release] in {cfg}: ship_posture = matrix" in text
        assert f"under [wake] in {cfg}: stop_on_blocked = true" in text
        assert lines[-1].startswith("updated: 2 line(s) changed")

    def test_a_second_run_says_already_applied(self, tmp_path: pathlib.Path) -> None:
        cfg = _write(tmp_path / "kanban.cfg", _OLD_KANBAN_CFG)
        cdu.upgrade_cfg_defaults(cfg, _CHANGES)
        lines = cdu.render_outcomes(cfg, cdu.upgrade_cfg_defaults(cfg, _CHANGES))
        assert lines[-1].startswith("already applied")
        assert not any("revert:" in line for line in lines)

    def test_a_dry_run_says_what_would_change(self, tmp_path: pathlib.Path) -> None:
        cfg = _write(tmp_path / "kanban.cfg", _OLD_KANBAN_CFG)
        lines = cdu.render_outcomes(cfg, cdu.upgrade_cfg_defaults(cfg, _CHANGES, dry_run=True), dry_run=True)
        assert any(line.startswith("  [would-change]  [release] ship_posture") for line in lines)
        assert lines[-1].startswith("dry run: 2 line(s) would change")

    def test_a_project_keeping_its_own_value_is_named(self) -> None:
        lines = cdu.render_project_values(
            [
                cdu.ProjectValue("project-a", "release", "ship_posture", "matrix", "/k/projects/project-a/project.cfg"),
                cdu.ProjectValue("project-b", "release", "ship_posture", "", ""),
            ]
        )
        assert lines[0].startswith("  [keeps]  project-a:")
        assert "ship_posture = matrix" in lines[0]
        assert lines[1].startswith("  [inherits]  project-b:")


class TestCommandLine:
    def _argv(self, root: pathlib.Path, *extra: str) -> list[str]:
        return [
            "--kanban-root",
            str(root),
            "--change",
            "release",
            "ship_posture",
            "matrix",
            "always",
            "ships instead of halting",
            "--change",
            "wake",
            "stop_on_blocked",
            "true",
            "false",
            "the batch carries on",
            "--project-key",
            "release",
            "ship_posture",
            "--project",
            "project-a",
            *extra,
        ]

    def test_main_migrates_and_reports_projects(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        root = tmp_path / "root"
        cfg = _write(root / "kanban.cfg", _OLD_KANBAN_CFG)
        _write(root / "projects" / "project-a" / "project.cfg", "[release]\nship_posture = matrix\n")

        assert cdu.main(self._argv(root)) == cdu.EXIT_OK

        out = capsys.readouterr().out
        assert "[changed]  [release] ship_posture: matrix -> always" in out
        assert "[keeps]  project-a:" in out
        assert cdu.plan_changes(cfg, _CHANGES)[0].status == cdu.ALREADY

    def test_a_missing_change_argument_is_a_usage_error(self, tmp_path: pathlib.Path) -> None:
        assert cdu.main(["--kanban-root", str(tmp_path)]) == cdu.EXIT_USAGE

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory write permission")
    def test_an_unwritable_kanban_cfg_is_refused_dual_channel(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("PGAI_PROJECT_NAME", raising=False)
        root = tmp_path / "root"
        cfg = _write(root / "kanban.cfg", _OLD_KANBAN_CFG)
        root.chmod(0o500)
        try:
            code = cdu.main(self._argv(root, "--rerun-command", "bash migrate.sh"))
        finally:
            root.chmod(0o700)

        assert code == cdu.EXIT_WRITE_FAILED
        contract = parse_error_contract(capsys.readouterr().err)
        assert contract is not None
        assert contract["error"] == "cfg_default_upgrade.write_failed"
        assert contract["remedy"].endswith("&& bash migrate.sh")
        assert cfg.read_text(encoding="utf-8") == _OLD_KANBAN_CFG
