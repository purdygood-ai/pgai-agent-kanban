"""
test_temp_root_lint_callers.py
==============================
The lint scripts that write scratch files take the framework temp root from
``pgai_agent_kanban.lib.temp_root.resolve_temp_root`` and nowhere else:

  1. ``lint_icd_freshness.py`` -- ``_get_temp_root`` is
     ``<temp root>/icd_freshness``, created when missing.
  2. ``lint_changelog_freshness.py`` -- ``_get_temp_root`` is
     ``<temp root>/changelog_freshness``, created when missing.
  3. ``lint_doc_command_truth.py`` -- every ``--help`` probe runs in a fresh
     ``help-sandbox-*`` directory under ``<temp root>/lint_doc_command_truth``.
  4. ``lint_config_key_coverage.py`` -- the ``create_project`` render's
     scratch dir is made under the temp root, which is created when missing;
     when it cannot be created the render falls back to the system temp
     directory rather than failing.

Each case points ``PGAI_AGENT_KANBAN_TEMP_DIR`` (or kanban.cfg ``[paths]``,
for the configuration tier) at a sandbox under tmp_path and asserts the path
the lint produced lies under it. Every kanban root is a throwaway directory
under tmp_path, and the live-install variables are cleared or repointed for
every case.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from types import ModuleType

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_REPO_ROOT = _TEAM_DIR.parent


def _load(name: str) -> ModuleType:
    """Load ``team/scripts/<name>.py`` by file path, as the lint runs."""
    path = _SCRIPTS_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    # Registered before exec: dataclasses resolve their annotations through
    # sys.modules[cls.__module__] while the class bodies run.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


icd_lint = _load("lint_icd_freshness")
changelog_lint = _load("lint_changelog_freshness")
doc_truth_lint = _load("lint_doc_command_truth")
config_key_lint = _load("lint_config_key_coverage")


@pytest.fixture
def sandboxed_env(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Clear the temp override and the live-install variables; point the
    kanban root at an empty sandbox so no kanban.cfg outside tmp_path is read."""
    for name in ("PGAI_AGENT_KANBAN_TEMP_DIR", "PGAI_TASKS_DIR", "PGAI_PROJECT_ROOT"):
        monkeypatch.delenv(name, raising=False)
    empty_root = tmp_path / "empty-kanban-root"
    empty_root.mkdir()
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(empty_root))
    monkeypatch.setenv("KANBAN_ROOT", str(empty_root))
    return monkeypatch


def _configure_paths(tmp_path: pathlib.Path, env: pytest.MonkeyPatch) -> pathlib.Path:
    """Point the kanban root at a kanban.cfg whose ``[paths]`` names a root
    under tmp_path; return the temp root that configuration gives."""
    kanban_root = tmp_path / "configured-kanban"
    kanban_root.mkdir()
    cfg_root = tmp_path / "configured-root"
    (kanban_root / "kanban.cfg").write_text(
        f"[paths]\ntmp_root = {cfg_root}\ntmp_subdir = kanban-scratch\n", encoding="utf-8"
    )
    env.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(kanban_root))
    env.setenv("KANBAN_ROOT", str(kanban_root))
    return cfg_root / "kanban-scratch"


# ---------------------------------------------------------------------------
# 1 and 2. The freshness lints' scratch directories
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lint", "subdir"),
    [(icd_lint, "icd_freshness"), (changelog_lint, "changelog_freshness")],
    ids=["icd_freshness", "changelog_freshness"],
)
def test_freshness_lint_scratch_is_under_the_temp_dir_override(
    tmp_path: pathlib.Path, sandboxed_env: pytest.MonkeyPatch, lint: ModuleType, subdir: str
) -> None:
    sandbox = tmp_path / "override-sandbox" / "not-yet-created"
    sandboxed_env.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(sandbox))

    scratch = lint._get_temp_root()

    assert scratch == sandbox / subdir
    assert scratch.is_dir(), "the lint's scratch directory is created when missing"


@pytest.mark.parametrize(
    ("lint", "subdir"),
    [(icd_lint, "icd_freshness"), (changelog_lint, "changelog_freshness")],
    ids=["icd_freshness", "changelog_freshness"],
)
def test_freshness_lint_scratch_honours_configured_paths(
    tmp_path: pathlib.Path, sandboxed_env: pytest.MonkeyPatch, lint: ModuleType, subdir: str
) -> None:
    expected_root = _configure_paths(tmp_path, sandboxed_env)

    scratch = lint._get_temp_root()

    assert scratch == expected_root / subdir
    assert scratch.is_dir()


# ---------------------------------------------------------------------------
# 3. lint_doc_command_truth: the --help probe sandbox
# ---------------------------------------------------------------------------


def test_help_sandbox_is_made_under_the_temp_dir_override(
    tmp_path: pathlib.Path, sandboxed_env: pytest.MonkeyPatch
) -> None:
    sandbox = tmp_path / "override-sandbox"
    sandboxed_env.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(sandbox))

    made = doc_truth_lint._make_help_sandbox_root()

    assert made.parent == sandbox / "lint_doc_command_truth"
    assert made.name.startswith("help-sandbox-") and made.is_dir()


def test_help_sandbox_honours_configured_paths(
    tmp_path: pathlib.Path, sandboxed_env: pytest.MonkeyPatch
) -> None:
    expected_root = _configure_paths(tmp_path, sandboxed_env)

    made = doc_truth_lint._make_help_sandbox_root()

    assert made.parent == expected_root / "lint_doc_command_truth"


def test_help_probe_runs_inside_the_sandbox_under_the_temp_dir_override(
    tmp_path: pathlib.Path, sandboxed_env: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """End to end: a cited script's ``--help`` records the directory it ran
    in; that directory is a help sandbox under the override."""
    root = tmp_path / "fixture_repo"
    scripts_dir = root / "team" / "scripts"
    scripts_dir.mkdir(parents=True)
    (root / "docs").mkdir()
    record = tmp_path / "probe-cwd.txt"
    script = scripts_dir / "records_cwd.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        f'case "${{1:-}}" in\n  --help|-h) pwd > "{record}"; exit 0 ;;\nesac\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    doc = root / "docs" / "cites.md"
    doc.write_text("# Doc\n\n```bash\nteam/scripts/records_cwd.sh\n```\n", encoding="utf-8")
    sandbox = tmp_path / "override-sandbox"
    sandboxed_env.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(sandbox))

    code = doc_truth_lint.main(["--repo-root", str(root), "--doc", str(doc)])

    assert code == 0, capsys.readouterr().out
    probe_cwd = pathlib.Path(record.read_text(encoding="utf-8").strip())
    assert probe_cwd.parent == sandbox / "lint_doc_command_truth"
    assert probe_cwd.name.startswith("help-sandbox-")


# ---------------------------------------------------------------------------
# 4. lint_config_key_coverage: the create_project render's scratch dir
# ---------------------------------------------------------------------------


def test_render_scratch_parent_is_the_temp_dir_override_created(
    tmp_path: pathlib.Path, sandboxed_env: pytest.MonkeyPatch
) -> None:
    sandbox = tmp_path / "override-sandbox" / "not-yet-created"
    sandboxed_env.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(sandbox))

    assert config_key_lint._render_scratch_parent() == str(sandbox)
    assert sandbox.is_dir(), "the render's temp root is created when missing"


def test_render_scratch_parent_honours_configured_paths(
    tmp_path: pathlib.Path, sandboxed_env: pytest.MonkeyPatch
) -> None:
    expected_root = _configure_paths(tmp_path, sandboxed_env)

    assert config_key_lint._render_scratch_parent() == str(expected_root)
    assert expected_root.is_dir()


def test_render_scratch_parent_falls_back_to_the_system_temp_dir_when_the_root_cannot_be_made(
    tmp_path: pathlib.Path, sandboxed_env: pytest.MonkeyPatch
) -> None:
    blocker = tmp_path / "a-file-not-a-dir"
    blocker.write_text("", encoding="utf-8")
    sandboxed_env.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(blocker / "temp-root"))

    assert config_key_lint._render_scratch_parent() is None


def test_render_runs_in_a_scratch_dir_under_the_temp_dir_override(
    tmp_path: pathlib.Path, sandboxed_env: pytest.MonkeyPatch
) -> None:
    """End to end: the render of this checkout's project.cfg runs its child
    interpreter in a scratch dir under the override (its cwd, TMPDIR and
    HOME), still returns the rendered text, and removes the scratch dir."""
    sandbox = tmp_path / "override-sandbox"
    sandboxed_env.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(sandbox))
    seen: list[dict[str, str]] = []
    real_run = config_key_lint.subprocess.run

    def recording_run(*args, **kwargs):
        seen.append({"cwd": kwargs["cwd"], "TMPDIR": kwargs["env"]["TMPDIR"], "HOME": kwargs["env"]["HOME"]})
        return real_run(*args, **kwargs)

    sandboxed_env.setattr(config_key_lint.subprocess, "run", recording_run)

    text, reason = config_key_lint.render_project_cfg_text(_REPO_ROOT)

    assert text is not None, reason
    assert "[project]" in text
    assert len(seen) == 1
    scratch = pathlib.Path(seen[0]["cwd"])
    assert scratch.parent == sandbox
    assert scratch.name.startswith("config-key-render-")
    assert seen[0]["TMPDIR"] == seen[0]["HOME"] == str(scratch)
    assert not scratch.exists(), "the render's scratch dir is removed when the render ends"
