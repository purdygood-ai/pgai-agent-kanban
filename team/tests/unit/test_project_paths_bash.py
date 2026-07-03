"""
test_project_paths_bash.py
==========================
Behavioral unit tests for team/scripts/lib/project_paths.sh.

Tests source the shell script and invoke pp_* functions via the bash harness,
constructing synthetic directory trees in tmp_path.  No live kanban root is
touched (all env vars point at tmp_path).

Functions under test:
  - pp_require_project_context: resolves project name from arg or env
  - pp_project_root:   $KANBAN_ROOT/projects/<name>
  - pp_tasks_dir:      <project_root>/tasks
  - pp_requirements_dir: <project_root>/requirements
  - pp_bugs_dir:       <project_root>/bugs
  - pp_priority_dir:   <project_root>/priority
  - pp_release_state:  <project_root>/release-state.md
  - pp_queue_path:     <tasks_dir>/queues/<agent>_backlog.md
  - validate_branch_prefix: allowed character-class check
  - pp_push_to_remote: reads [project] push_to_remote from project.cfg
  - pp_verbose_mode:   reads [debug] verbose_mode from project.cfg
  - pp_verbose_agents: reads [debug] verbose_agents from project.cfg
  - pp_reasoning_trace: reads [training] reasoning_trace from project.cfg
"""

from __future__ import annotations

import pathlib

import pytest

from tests.unit.shell_harness import run_bash

_LIB = "scripts/lib/project_paths.sh"


def _source(func_call: str, extra_env: dict | None = None) -> tuple[str, dict | None]:
    """Return (bash_script, extra_env) ready for run_bash."""
    return (f"source {_LIB} && {func_call}", extra_env)


def _make_project(root: pathlib.Path, project_name: str) -> pathlib.Path:
    """Create a minimal project directory structure under root/projects/<name>."""
    proj = root / "projects" / project_name
    proj.mkdir(parents=True, exist_ok=True)
    return proj


def _run(tmp_path: pathlib.Path, func_call: str, kanban_root: pathlib.Path, **extra_env_kv: str) -> object:
    """Source project_paths.sh and run func_call with KANBAN_ROOT set to kanban_root."""
    env = {"KANBAN_ROOT": str(kanban_root)}
    env.update(extra_env_kv)
    return run_bash(tmp_path, f"source {_LIB} && {func_call}", extra_env=env)


# ---------------------------------------------------------------------------
# pp_require_project_context
# ---------------------------------------------------------------------------


def test_require_project_context_uses_explicit_argument(tmp_path: pathlib.Path) -> None:
    """pp_require_project_context echoes the explicit argument when supplied."""
    result = _run(tmp_path, "pp_require_project_context my-project", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "my-project"


def test_require_project_context_uses_env_when_no_arg(tmp_path: pathlib.Path) -> None:
    """pp_require_project_context falls back to PGAI_PROJECT_NAME when no argument."""
    result = _run(
        tmp_path,
        "pp_require_project_context",
        tmp_path,
        PGAI_PROJECT_NAME="from-env",
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "from-env"


def test_require_project_context_fails_when_no_source(tmp_path: pathlib.Path) -> None:
    """pp_require_project_context exits non-zero when neither arg nor env is set."""
    result = run_bash(
        tmp_path,
        f"source {_LIB} && pp_require_project_context",
        extra_env={
            "KANBAN_ROOT": str(tmp_path),
            "PGAI_PROJECT_NAME": "",
        },
    )
    assert result.returncode != 0
    assert "PGAI_PROJECT_NAME" in result.stderr or "required" in result.stderr.lower()


# ---------------------------------------------------------------------------
# pp_project_root
# ---------------------------------------------------------------------------


def test_project_root_echoes_correct_path(tmp_path: pathlib.Path) -> None:
    """pp_project_root echoes $KANBAN_ROOT/projects/<name>."""
    result = _run(tmp_path, "pp_project_root my-project", tmp_path)
    assert result.returncode == 0
    expected = str(tmp_path / "projects" / "my-project")
    assert result.stdout.strip() == expected


def test_project_root_uses_pgai_project_name_env(tmp_path: pathlib.Path) -> None:
    """pp_project_root resolves the name from PGAI_PROJECT_NAME when no arg given."""
    result = _run(
        tmp_path,
        "pp_project_root",
        tmp_path,
        PGAI_PROJECT_NAME="env-project",
    )
    assert result.returncode == 0
    expected = str(tmp_path / "projects" / "env-project")
    assert result.stdout.strip() == expected


# ---------------------------------------------------------------------------
# pp_tasks_dir / pp_requirements_dir / pp_bugs_dir / pp_priority_dir
# ---------------------------------------------------------------------------


def test_tasks_dir_echoes_tasks_subpath(tmp_path: pathlib.Path) -> None:
    """pp_tasks_dir echoes <project_root>/tasks."""
    result = _run(tmp_path, "pp_tasks_dir alpha", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip().endswith("projects/alpha/tasks")


def test_requirements_dir_echoes_requirements_subpath(tmp_path: pathlib.Path) -> None:
    """pp_requirements_dir echoes <project_root>/requirements."""
    result = _run(tmp_path, "pp_requirements_dir alpha", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip().endswith("projects/alpha/requirements")


def test_bugs_dir_echoes_bugs_subpath(tmp_path: pathlib.Path) -> None:
    """pp_bugs_dir echoes <project_root>/bugs."""
    result = _run(tmp_path, "pp_bugs_dir alpha", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip().endswith("projects/alpha/bugs")


def test_priority_dir_echoes_priority_subpath(tmp_path: pathlib.Path) -> None:
    """pp_priority_dir echoes <project_root>/priority."""
    result = _run(tmp_path, "pp_priority_dir alpha", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip().endswith("projects/alpha/priority")


# ---------------------------------------------------------------------------
# pp_release_state
# ---------------------------------------------------------------------------


def test_release_state_echoes_release_state_md_path(tmp_path: pathlib.Path) -> None:
    """pp_release_state echoes <project_root>/release-state.md."""
    result = _run(tmp_path, "pp_release_state alpha", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip().endswith("projects/alpha/release-state.md")


# ---------------------------------------------------------------------------
# pp_queue_path
# ---------------------------------------------------------------------------


def test_queue_path_two_arg_form_echoes_backlog_file(tmp_path: pathlib.Path) -> None:
    """pp_queue_path <project> <agent> echoes the correct queue file path."""
    result = _run(tmp_path, "pp_queue_path myproject coder", tmp_path)
    assert result.returncode == 0
    expected_suffix = "projects/myproject/tasks/queues/coder_backlog.md"
    assert result.stdout.strip().endswith(expected_suffix)


def test_queue_path_one_arg_form_uses_pgai_project_name(tmp_path: pathlib.Path) -> None:
    """pp_queue_path <agent> resolves project name from PGAI_PROJECT_NAME."""
    result = _run(
        tmp_path,
        "pp_queue_path pm",
        tmp_path,
        PGAI_PROJECT_NAME="envproject",
    )
    assert result.returncode == 0
    assert result.stdout.strip().endswith("projects/envproject/tasks/queues/pm_backlog.md")


def test_queue_path_fails_when_no_agent_and_no_project_context(
    tmp_path: pathlib.Path,
) -> None:
    """pp_queue_path exits non-zero when no agent is supplied and project context is absent."""
    # Call with zero arguments and no PGAI_PROJECT_NAME: both agent and project name
    # resolution fail.
    result = run_bash(
        tmp_path,
        f"source {_LIB} && KANBAN_ROOT='{tmp_path}' PGAI_PROJECT_NAME='' pp_queue_path",
    )
    assert result.returncode != 0


# ---------------------------------------------------------------------------
# validate_branch_prefix
# ---------------------------------------------------------------------------


def test_validate_branch_prefix_accepts_empty_string(tmp_path: pathlib.Path) -> None:
    """validate_branch_prefix returns 0 for an empty string (no prefix)."""
    result = run_bash(
        tmp_path,
        f"source {_LIB} && validate_branch_prefix '' && echo ok || echo fail",
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "ok"


def test_validate_branch_prefix_accepts_alphanumeric_with_underscore(
    tmp_path: pathlib.Path,
) -> None:
    """validate_branch_prefix accepts letters, digits, underscore, and hyphen."""
    result = run_bash(
        tmp_path,
        f"source {_LIB} && validate_branch_prefix 'ai_' && echo ok || echo fail",
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "ok"


def test_validate_branch_prefix_accepts_hyphen(tmp_path: pathlib.Path) -> None:
    """validate_branch_prefix accepts a prefix containing a hyphen."""
    result = run_bash(
        tmp_path,
        f"source {_LIB} && validate_branch_prefix 'team-' && echo ok || echo fail",
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "ok"


def test_validate_branch_prefix_rejects_space(tmp_path: pathlib.Path) -> None:
    """validate_branch_prefix rejects a prefix containing a space."""
    result = run_bash(
        tmp_path,
        f"source {_LIB} && validate_branch_prefix 'my prefix' && echo ok || echo fail",
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "fail"


def test_validate_branch_prefix_rejects_slash(tmp_path: pathlib.Path) -> None:
    """validate_branch_prefix rejects a prefix containing a slash."""
    result = run_bash(
        tmp_path,
        f"source {_LIB} && validate_branch_prefix 'feat/' && echo ok || echo fail",
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "fail"


def test_validate_branch_prefix_rejects_dot(tmp_path: pathlib.Path) -> None:
    """validate_branch_prefix rejects a prefix containing a dot."""
    result = run_bash(
        tmp_path,
        f"source {_LIB} && validate_branch_prefix 'v1.0' && echo ok || echo fail",
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "fail"


# ---------------------------------------------------------------------------
# pp_push_to_remote — reads [project] push_to_remote from project.cfg
# ---------------------------------------------------------------------------


def test_push_to_remote_defaults_to_true_when_key_absent(tmp_path: pathlib.Path) -> None:
    """pp_push_to_remote echoes 'true' when project.cfg lacks push_to_remote."""
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text("[project]\nproject_name=myproj\n", encoding="utf-8")
    result = _run(tmp_path, "pp_push_to_remote myproj", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "true"


def test_push_to_remote_echoes_false_when_configured_false(tmp_path: pathlib.Path) -> None:
    """pp_push_to_remote echoes 'false' when push_to_remote = false in project.cfg."""
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text(
        "[project]\nproject_name=myproj\npush_to_remote=false\n",
        encoding="utf-8",
    )
    result = _run(tmp_path, "pp_push_to_remote myproj", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "false"


def test_push_to_remote_echoes_true_when_configured_true(tmp_path: pathlib.Path) -> None:
    """pp_push_to_remote echoes 'true' when push_to_remote = true in project.cfg."""
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text(
        "[project]\nproject_name=myproj\npush_to_remote=true\n",
        encoding="utf-8",
    )
    result = _run(tmp_path, "pp_push_to_remote myproj", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "true"


# ---------------------------------------------------------------------------
# pp_verbose_mode — reads [debug] verbose_mode from project.cfg
# ---------------------------------------------------------------------------


def test_verbose_mode_defaults_to_false_when_absent(tmp_path: pathlib.Path) -> None:
    """pp_verbose_mode echoes 'false' when [debug] verbose_mode is absent."""
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text("[project]\nproject_name=myproj\n", encoding="utf-8")
    result = _run(tmp_path, "pp_verbose_mode myproj", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "false"


def test_verbose_mode_echoes_true_when_configured(tmp_path: pathlib.Path) -> None:
    """pp_verbose_mode echoes 'true' when verbose_mode = true in project.cfg."""
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text(
        "[project]\nproject_name=myproj\n\n[debug]\nverbose_mode=true\n",
        encoding="utf-8",
    )
    result = _run(tmp_path, "pp_verbose_mode myproj", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "true"


# ---------------------------------------------------------------------------
# pp_verbose_agents — reads [debug] verbose_agents from project.cfg
# ---------------------------------------------------------------------------


def test_verbose_agents_returns_default_when_absent(tmp_path: pathlib.Path) -> None:
    """pp_verbose_agents echoes the default agent list when key is absent."""
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text("[project]\nproject_name=myproj\n", encoding="utf-8")
    result = _run(tmp_path, "pp_verbose_agents myproj", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "pm,coder,writer,tester,cm"


def test_verbose_agents_returns_configured_list(tmp_path: pathlib.Path) -> None:
    """pp_verbose_agents echoes the configured agent list from project.cfg."""
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text(
        "[project]\nproject_name=myproj\n\n[debug]\nverbose_agents=coder,writer\n",
        encoding="utf-8",
    )
    result = _run(tmp_path, "pp_verbose_agents myproj", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "coder,writer"


# ---------------------------------------------------------------------------
# pp_reasoning_trace — reads [training] reasoning_trace from project.cfg
# ---------------------------------------------------------------------------


def test_reasoning_trace_defaults_to_false_when_absent(tmp_path: pathlib.Path) -> None:
    """pp_reasoning_trace echoes 'false' when [training] reasoning_trace is absent."""
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text("[project]\nproject_name=myproj\n", encoding="utf-8")
    result = _run(tmp_path, "pp_reasoning_trace myproj", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "false"


def test_reasoning_trace_echoes_true_when_configured(tmp_path: pathlib.Path) -> None:
    """pp_reasoning_trace echoes 'true' when reasoning_trace = true in project.cfg."""
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text(
        "[project]\nproject_name=myproj\n\n[training]\nreasoning_trace=true\n",
        encoding="utf-8",
    )
    result = _run(tmp_path, "pp_reasoning_trace myproj", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "true"
