"""
test_ops_read_state_twins.py
=============================
Behavioral unit tests for the two ops-layer read facades
``pgai_agent_kanban.ops.read.get_release_state`` and
``pgai_agent_kanban.ops.read.get_deep_verify_status`` -- the exact functions
both the CLI twins (``team/scripts/release-state.sh`` /
``team/scripts/deep-verify-status.sh``) and the REST twins
(``GET /release-state`` / ``GET /deep-verify-status``,
``team/pgai_agent_kanban/api/reads.py``) call, so a regression here is a
regression on every surface at once.

Coverage:
  - get_release_state: a well-formed release-state.md loads cleanly; a
    project with no directory raises NotFound; a malformed field (routed
    through the shared validating loader) raises Refused.
  - get_deep_verify_status: a project that has never been deep-verified
    reports mode + empty entries + latest=None; a populated ledger reports
    every entry and the correct latest; the legacy true/false boolean
    aliases resolve to weekly/off (mirroring
    team/scripts/lib/project_paths.sh's pp_deep_verify_mode); the mode
    resolves project.cfg > kanban.cfg > weekly, the same chain as
    pp_deep_verify_mode; an unrecognized deep_verify config value raises
    Refused naming the file rather than guessing a cadence; a project with
    no directory raises NotFound.

All tests use tmp_path for scratch -- no bare /tmp paths. Each test is
self-contained; no shared state between tests.
"""

from __future__ import annotations

import pathlib

import pytest

from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.ops.errors import NotFound, Refused
from pgai_agent_kanban.ops.read import get_deep_verify_status, get_release_state


def _make_project(kanban_root: pathlib.Path, name: str = "demo") -> pathlib.Path:
    project_root = kanban_root / "projects" / name
    project_root.mkdir(parents=True, exist_ok=True)
    return project_root


class TestGetReleaseState:
    def test_well_formed_release_state_loads_every_field(self, tmp_path: pathlib.Path) -> None:
        project_root = _make_project(tmp_path)
        (project_root / "release-state.md").write_text(
            "## Active RC\nv1.2.3\n\n"
            "## RC Opened At\n2026-01-01T00:00:00+00:00\n\n"
            "## RC Opened By Task\nCM-20260101-001-open\n\n"
            "## Last Released\nv1.2.2\n",
            encoding="utf-8",
        )
        ctx = OpsContext(kanban_root=tmp_path)

        fields = get_release_state(ctx, "demo")

        assert fields["Active RC"] == "v1.2.3"
        assert fields["RC Opened At"] == "2026-01-01T00:00:00+00:00"
        assert fields["RC Opened By Task"] == "CM-20260101-001-open"
        assert fields["Last Released"] == "v1.2.2"

    def test_a_fresh_projects_all_none_shape_is_a_validated_value_not_an_absence(
        self, tmp_path: pathlib.Path
    ) -> None:
        project_root = _make_project(tmp_path)
        (project_root / "release-state.md").write_text(
            "## Active RC\nnone\n\n## RC Opened At\nnone\n\n"
            "## RC Opened By Task\nnone\n\n## Last Released\nnone\n",
            encoding="utf-8",
        )
        ctx = OpsContext(kanban_root=tmp_path)

        fields = get_release_state(ctx, "demo")

        assert fields == {
            "Active RC": "none",
            "RC Opened At": "none",
            "RC Opened By Task": "none",
            "Last Released": "none",
        }

    def test_project_with_no_directory_raises_not_found(self, tmp_path: pathlib.Path) -> None:
        ctx = OpsContext(kanban_root=tmp_path)

        with pytest.raises(NotFound):
            get_release_state(ctx, "never-registered")

    def test_malformed_field_raises_refused_naming_the_bad_value(
        self, tmp_path: pathlib.Path
    ) -> None:
        project_root = _make_project(tmp_path)
        (project_root / "release-state.md").write_text(
            "## Active RC\nNOTAVERSION\n\n## RC Opened At\nnone\n\n"
            "## RC Opened By Task\nnone\n\n## Last Released\nnone\n",
            encoding="utf-8",
        )
        ctx = OpsContext(kanban_root=tmp_path)

        with pytest.raises(Refused, match="NOTAVERSION"):
            get_release_state(ctx, "demo")


class TestGetDeepVerifyStatus:
    def test_never_run_project_reports_empty_ledger_and_null_latest(
        self, tmp_path: pathlib.Path
    ) -> None:
        _make_project(tmp_path)
        ctx = OpsContext(kanban_root=tmp_path)

        status = get_deep_verify_status(ctx, "demo")

        assert status == {"mode": "weekly", "entries": [], "latest": None}

    def test_populated_ledger_reports_every_entry_and_the_correct_latest(
        self, tmp_path: pathlib.Path
    ) -> None:
        from pgai_agent_kanban.deep_verify.ledger import DeepVerifyRecord, append_record

        project_root = _make_project(tmp_path)
        ledger_path = project_root / "deep-verify-ledger.md"
        append_record(
            str(ledger_path),
            DeepVerifyRecord(
                timestamp="2026-01-01T00:00:00+00:00",
                project="demo",
                version="v1.0.0",
                commit="aaaaaaa",
                verdict="built",
                detail="all pairs built",
            ),
        )
        append_record(
            str(ledger_path),
            DeepVerifyRecord(
                timestamp="2026-01-08T00:00:00+00:00",
                project="demo",
                version="v1.1.0",
                commit="bbbbbbb",
                verdict="failed-to-build",
                detail="docker/x: failed at step: boom",
            ),
        )
        ctx = OpsContext(kanban_root=tmp_path)

        status = get_deep_verify_status(ctx, "demo")

        assert len(status["entries"]) == 2
        assert status["entries"][0]["verdict"] == "built"
        assert status["entries"][1]["verdict"] == "failed-to-build"
        assert status["latest"]["verdict"] == "failed-to-build"
        assert status["latest"]["commit"] == "bbbbbbb"

    @pytest.mark.parametrize(
        "raw_value,expected_mode",
        [
            ("weekly", "weekly"),
            ("off", "off"),
            ("true", "weekly"),
            ("false", "off"),
            ("", "weekly"),
        ],
    )
    def test_deep_verify_mode_resolves_the_same_vocabulary_pp_deep_verify_mode_accepts(
        self, tmp_path: pathlib.Path, raw_value: str, expected_mode: str
    ) -> None:
        project_root = _make_project(tmp_path)
        if raw_value:
            (project_root / "project.cfg").write_text(
                f"[release]\ndeep_verify = {raw_value}\n", encoding="utf-8"
            )
        ctx = OpsContext(kanban_root=tmp_path)

        status = get_deep_verify_status(ctx, "demo")

        assert status["mode"] == expected_mode

    def test_projects_resolve_independently_each_own_value_over_kanban_cfg(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Two projects with different project.cfg [release] deep_verify
        values resolve independently; a project's own value wins over the
        kanban-wide one."""
        weekly_root = _make_project(tmp_path, "weekly-project")
        off_root = _make_project(tmp_path, "off-project")
        (weekly_root / "project.cfg").write_text(
            "[release]\ndeep_verify = weekly\n", encoding="utf-8"
        )
        (off_root / "project.cfg").write_text(
            "[release]\ndeep_verify = off\n", encoding="utf-8"
        )
        (tmp_path / "kanban.cfg").write_text("[release]\ndeep_verify = off\n", encoding="utf-8")
        ctx = OpsContext(kanban_root=tmp_path)

        assert get_deep_verify_status(ctx, "weekly-project")["mode"] == "weekly"
        assert get_deep_verify_status(ctx, "off-project")["mode"] == "off"

    @pytest.mark.parametrize(
        "kanban_value,expected_mode",
        [
            ("off", "off"),
            ("weekly", "weekly"),
            ("true", "weekly"),
            ("false", "off"),
        ],
    )
    def test_kanban_cfg_value_applies_when_project_cfg_is_silent(
        self, tmp_path: pathlib.Path, kanban_value: str, expected_mode: str
    ) -> None:
        """The kanban-wide [release] deep_verify is the second tier, the same
        chain pp_deep_verify_mode reads."""
        project_root = _make_project(tmp_path)
        (project_root / "project.cfg").write_text(
            "[project]\nproject_name = demo\n", encoding="utf-8"
        )
        (tmp_path / "kanban.cfg").write_text(
            f"[release]\ndeep_verify = {kanban_value}\n", encoding="utf-8"
        )
        ctx = OpsContext(kanban_root=tmp_path)

        assert get_deep_verify_status(ctx, "demo")["mode"] == expected_mode

    def test_kanban_cfg_value_applies_when_the_project_has_no_config_file(
        self, tmp_path: pathlib.Path
    ) -> None:
        _make_project(tmp_path)
        (tmp_path / "kanban.cfg").write_text("[release]\ndeep_verify = off\n", encoding="utf-8")
        ctx = OpsContext(kanban_root=tmp_path)

        assert get_deep_verify_status(ctx, "demo")["mode"] == "off"

    def test_project_cfg_weekly_wins_over_kanban_cfg_off(self, tmp_path: pathlib.Path) -> None:
        project_root = _make_project(tmp_path)
        (project_root / "project.cfg").write_text(
            "[release]\ndeep_verify = weekly\n", encoding="utf-8"
        )
        (tmp_path / "kanban.cfg").write_text("[release]\ndeep_verify = off\n", encoding="utf-8")
        ctx = OpsContext(kanban_root=tmp_path)

        assert get_deep_verify_status(ctx, "demo")["mode"] == "weekly"

    def test_unrecognized_kanban_cfg_value_raises_refused_naming_kanban_cfg(
        self, tmp_path: pathlib.Path
    ) -> None:
        _make_project(tmp_path)
        kanban_cfg = tmp_path / "kanban.cfg"
        kanban_cfg.write_text("[release]\ndeep_verify = bogus\n", encoding="utf-8")
        ctx = OpsContext(kanban_root=tmp_path)

        with pytest.raises(Refused) as excinfo:
            get_deep_verify_status(ctx, "demo")

        message = str(excinfo.value)
        assert "bogus" in message
        assert str(kanban_cfg) in message
        assert "deep_verify" in message

    def test_unrecognized_deep_verify_value_raises_refused_rather_than_guessing(
        self, tmp_path: pathlib.Path
    ) -> None:
        project_root = _make_project(tmp_path)
        (project_root / "project.cfg").write_text(
            "[release]\ndeep_verify = weekly-ish\n", encoding="utf-8"
        )
        ctx = OpsContext(kanban_root=tmp_path)

        with pytest.raises(Refused, match="weekly-ish"):
            get_deep_verify_status(ctx, "demo")

    def test_malformed_ledger_raises_refused_rather_than_reporting_never_run(
        self, tmp_path: pathlib.Path
    ) -> None:
        project_root = _make_project(tmp_path)
        (project_root / "deep-verify-ledger.md").write_text(
            "this is not a recognizable ledger record at all\n", encoding="utf-8"
        )
        ctx = OpsContext(kanban_root=tmp_path)

        with pytest.raises(Refused):
            get_deep_verify_status(ctx, "demo")

    def test_project_with_no_directory_raises_not_found(self, tmp_path: pathlib.Path) -> None:
        ctx = OpsContext(kanban_root=tmp_path)

        with pytest.raises(NotFound):
            get_deep_verify_status(ctx, "never-registered")
