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


@pytest.mark.parametrize(
    "func, expected_suffix",
    [
        ("pp_tasks_dir", "projects/alpha/tasks"),
        ("pp_requirements_dir", "projects/alpha/requirements"),
        ("pp_bugs_dir", "projects/alpha/bugs"),
        ("pp_priority_dir", "projects/alpha/priority"),
        ("pp_release_state", "projects/alpha/release-state.md"),
    ],
)
def test_project_paths_helper_echoes_subpath(
    tmp_path: pathlib.Path, func: str, expected_suffix: str
) -> None:
    """Each pp_*_dir/pp_release_state helper echoes <project_root>/<subpath> for the given project."""
    result = _run(tmp_path, f"{func} alpha", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip().endswith(expected_suffix)


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


@pytest.mark.parametrize(
    "prefix, expected",
    [
        ("ai_", "ok"),  # letters, digits, underscore
        ("team-", "ok"),  # hyphen
        ("my prefix", "fail"),  # space
        ("feat/", "fail"),  # slash
        ("v1.0", "fail"),  # dot
    ],
)
def test_validate_branch_prefix_charset(tmp_path: pathlib.Path, prefix: str, expected: str) -> None:
    """validate_branch_prefix accepts alphanumeric/underscore/hyphen, rejects space/slash/dot."""
    result = run_bash(
        tmp_path,
        f"source {_LIB} && validate_branch_prefix '{prefix}' && echo ok || echo fail",
    )
    assert result.returncode == 0
    assert result.stdout.strip() == expected


# ---------------------------------------------------------------------------
# pp_push_to_remote — reads [project] push_to_remote from project.cfg
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "func, cfg_body, expected",
    [
        # pp_push_to_remote — [project] push_to_remote
        ("pp_push_to_remote", "[project]\nproject_name=myproj\n", "true"),  # absent -> default true
        (
            "pp_push_to_remote",
            "[project]\nproject_name=myproj\npush_to_remote=false\n",
            "false",
        ),
        (
            "pp_push_to_remote",
            "[project]\nproject_name=myproj\npush_to_remote=true\n",
            "true",
        ),
        # pp_verbose_mode — [debug] verbose_mode
        ("pp_verbose_mode", "[project]\nproject_name=myproj\n", "false"),  # absent -> default false
        (
            "pp_verbose_mode",
            "[project]\nproject_name=myproj\n\n[debug]\nverbose_mode=true\n",
            "true",
        ),
        # pp_verbose_agents — [debug] verbose_agents
        (
            "pp_verbose_agents",
            "[project]\nproject_name=myproj\n",
            "pm,coder,writer,tester,cm",
        ),  # absent -> default agent list
        (
            "pp_verbose_agents",
            "[project]\nproject_name=myproj\n\n[debug]\nverbose_agents=coder,writer\n",
            "coder,writer",
        ),
        # pp_reasoning_trace — [training] reasoning_trace
        ("pp_reasoning_trace", "[project]\nproject_name=myproj\n", "false"),  # absent -> default false
        (
            "pp_reasoning_trace",
            "[project]\nproject_name=myproj\n\n[training]\nreasoning_trace=true\n",
            "true",
        ),
    ],
)
def test_project_cfg_boolean_or_list_key(
    tmp_path: pathlib.Path, func: str, cfg_body: str, expected: str
) -> None:
    """pp_push_to_remote/pp_verbose_mode/pp_verbose_agents/pp_reasoning_trace read their
    project.cfg key with the documented default when the key/section is absent."""
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text(cfg_body, encoding="utf-8")
    result = _run(tmp_path, f"{func} myproj", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == expected


# ---------------------------------------------------------------------------
# pp_max_rc_retries — effective RC-retry ceiling: project.cfg [release]
# max_rc_retries override when present, else kanban.cfg [release]
# max_rc_retries default; fails loud (non-zero) when both are absent.
# ---------------------------------------------------------------------------


def test_max_rc_retries_present_case_returns_kanban_default(
    tmp_path: pathlib.Path,
) -> None:
    """pp_max_rc_retries returns the kanban.cfg default when no project override exists."""
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text("[project]\nproject_name=myproj\n", encoding="utf-8")
    (tmp_path / "kanban.cfg").write_text("[release]\nmax_rc_retries=3\n", encoding="utf-8")
    result = _run(tmp_path, "pp_max_rc_retries myproj", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "3"


def test_max_rc_retries_project_override_case_wins_over_kanban_default(
    tmp_path: pathlib.Path,
) -> None:
    """pp_max_rc_retries returns the project.cfg override, not the kanban.cfg default,
    when both are present — this is the self-build project's compounding-error
    containment (kanban.cfg default 3, project override 1)."""
    proj = _make_project(tmp_path, "pgai-agent-kanban")
    (proj / "project.cfg").write_text(
        "[project]\nproject_name=pgai-agent-kanban\n\n[release]\nmax_rc_retries=1\n",
        encoding="utf-8",
    )
    (tmp_path / "kanban.cfg").write_text("[release]\nmax_rc_retries=3\n", encoding="utf-8")
    result = _run(tmp_path, "pp_max_rc_retries pgai-agent-kanban", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "1"


def test_max_rc_retries_absent_case_fails_loud(tmp_path: pathlib.Path) -> None:
    """pp_max_rc_retries exits non-zero with a clear stderr message when the key is
    absent from both project.cfg and kanban.cfg — never silently assumes a default."""
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text("[project]\nproject_name=myproj\n", encoding="utf-8")
    (tmp_path / "kanban.cfg").write_text("[wake]\nmax_tasks_per_wake=5\n", encoding="utf-8")
    result = _run(tmp_path, "pp_max_rc_retries myproj", tmp_path)
    assert result.returncode != 0
    assert result.stdout.strip() == ""
    assert "max_rc_retries" in result.stderr
    assert "myproj" in result.stderr


def test_max_rc_retries_absent_case_fails_loud_when_kanban_cfg_missing(
    tmp_path: pathlib.Path,
) -> None:
    """pp_max_rc_retries fails loud (not empty-string default) when kanban.cfg
    itself does not exist and no project override is configured."""
    _make_project(tmp_path, "myproj")
    result = _run(tmp_path, "pp_max_rc_retries myproj", tmp_path)
    assert result.returncode != 0
    assert "max_rc_retries" in result.stderr


def test_max_rc_retries_rejects_non_integer_project_override(
    tmp_path: pathlib.Path,
) -> None:
    """pp_max_rc_retries fails loud on a malformed (non-integer) project.cfg override
    rather than silently coercing or falling through to the kanban.cfg default."""
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text(
        "[project]\nproject_name=myproj\n\n[release]\nmax_rc_retries=notanumber\n",
        encoding="utf-8",
    )
    (tmp_path / "kanban.cfg").write_text("[release]\nmax_rc_retries=3\n", encoding="utf-8")
    result = _run(tmp_path, "pp_max_rc_retries myproj", tmp_path)
    assert result.returncode != 0
    assert "invalid value" in result.stderr.lower()
