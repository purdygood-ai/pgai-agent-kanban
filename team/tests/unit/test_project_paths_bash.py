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
  - pp_push_to_remote: covered by test_release_push_readers_project_gate.py
    (it decides through the shared push-decision resolver)
  - pp_verbose_mode:   reads [debug] verbose_mode from project.cfg
  - pp_verbose_agents: reads [debug] verbose_agents from project.cfg
  - pp_reasoning_trace: reads [training] reasoning_trace from project.cfg
  - pp_deep_verify_mode / pp_deep_verify_offhours_window: project.cfg >
    kanban.cfg > default, aliases, refusals and the misplaced-key warning
  - pp_resolve_project_name: exact-then-prefix resolution against
    projects.cfg, including both branches of the not_found remedy
  - pp_load_config: a failed load leaves no earlier load's known PP_* fields set
  - pp_load_config (legacy PROJECT.cfg): a legacy load after an INI load keeps
    none of the INI project's values; a second legacy load replaces the first
    one's values; a fresh legacy load exports exactly the file's known fields
    plus PP_TEMP_DIR (with and without temp_dir), and no PP_before_vars or
    other pp_load_config local; under a set -euo pipefail caller (|| and if
    forms) a file with a failing command, an unset-variable reference or a
    failing command substitution still exports its fields, and one with an
    exit line makes the load return 1 with every known field unset
"""

from __future__ import annotations

import os
import pathlib
import shlex

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
def test_project_paths_helper_echoes_subpath(tmp_path: pathlib.Path, func: str, expected_suffix: str) -> None:
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
# project.cfg boolean/list keys with documented defaults
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "func, cfg_body, expected",
    [
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
def test_project_cfg_boolean_or_list_key(tmp_path: pathlib.Path, func: str, cfg_body: str, expected: str) -> None:
    """pp_verbose_mode/pp_verbose_agents/pp_reasoning_trace read their
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


# ---------------------------------------------------------------------------
# pp_deep_verify_mode / pp_deep_verify_offhours_window — [release]-scoped
# readers, and the misplaced-key warning: a recognised deep-verify
# key sitting outside [release] must not resolve silently. The warning names
# both the section the key was found in and [release]; the documented
# fallback (weekly / 03:00-03:59) still applies unchanged, and the resolver
# still exits 0 — this is an advisory warning, never a refusal.
# ---------------------------------------------------------------------------


def test_deep_verify_mode_present_in_release_no_warning(tmp_path: pathlib.Path) -> None:
    """deep_verify under [release] resolves to its own value with no stderr warning."""
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text(
        "[project]\nproject_name=myproj\n\n[release]\ndeep_verify=off\n",
        encoding="utf-8",
    )
    result = _run(tmp_path, "pp_deep_verify_mode myproj", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "off"
    assert result.stderr.strip() == ""


def test_deep_verify_mode_absent_everywhere_defaults_weekly_no_warning(
    tmp_path: pathlib.Path,
) -> None:
    """deep_verify absent from every section resolves to the documented default
    (weekly) with no warning — there is nothing misplaced to report."""
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text("[project]\nproject_name=myproj\n", encoding="utf-8")
    result = _run(tmp_path, "pp_deep_verify_mode myproj", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "weekly"
    assert result.stderr.strip() == ""


def test_deep_verify_mode_misplaced_section_warns_and_defaults_weekly(
    tmp_path: pathlib.Path,
) -> None:
    """deep_verify present under a non-[release] section (and absent from
    [release]) still resolves to the documented default (weekly) and exits 0,
    but now emits a stderr warning naming both the section it was found in
    and [release]. This is the acceptance-criterion test; it fails if the
    misplaced-key warning is reverted."""
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text(
        "[project]\nproject_name=myproj\n\n[training]\ndeep_verify=weekly\n",
        encoding="utf-8",
    )
    result = _run(tmp_path, "pp_deep_verify_mode myproj", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "weekly"
    assert "[training]" in result.stderr
    assert "[release]" in result.stderr
    assert "deep_verify" in result.stderr


def test_deep_verify_mode_release_value_wins_no_warning_even_if_duplicated(
    tmp_path: pathlib.Path,
) -> None:
    """When [release] itself carries deep_verify, no warning fires — whatever
    else the file contains. A key genuinely present where it belongs must
    never trigger the misplaced-key warning."""
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text(
        "[project]\nproject_name=myproj\n\n[release]\ndeep_verify=off\n\n" "[training]\ndeep_verify=weekly\n",
        encoding="utf-8",
    )
    result = _run(tmp_path, "pp_deep_verify_mode myproj", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "off"
    assert result.stderr.strip() == ""


@pytest.mark.parametrize(
    "raw_value, expected_mode",
    [
        pytest.param("weekly", "weekly", id="weekly_word_spelling"),
        pytest.param("off", "off", id="off_word_spelling"),
        pytest.param("true", "weekly", id="true_alias_resolves_to_weekly"),
        pytest.param("false", "off", id="false_alias_resolves_to_off"),
    ],
)
def test_deep_verify_mode_accepts_every_documented_spelling(
    tmp_path: pathlib.Path, raw_value: str, expected_mode: str
) -> None:
    """Every spelling the shipped project.cfg/kanban.cfg comments advertise as
    accepted ('weekly'/'off', plus the legacy boolean aliases 'true'/'false')
    resolves to exactly one of the two canonical mode words on stdout, with
    the alias spelling never echoed back."""
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text(
        f"[project]\nproject_name=myproj\n\n[release]\ndeep_verify={raw_value}\n",
        encoding="utf-8",
    )
    result = _run(tmp_path, "pp_deep_verify_mode myproj", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == expected_mode
    assert result.stderr.strip() == ""


def test_deep_verify_mode_still_refuses_a_value_outside_the_widened_set(
    tmp_path: pathlib.Path,
) -> None:
    """A value that is not one of the four accepted spellings still fails
    loud with nothing on stdout and a stderr diagnostic naming the accepted
    set -- widening to the alias vocabulary must not turn into case-blind
    or prefix matching that would also accept something like 'weeklyish'."""
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text(
        "[project]\nproject_name=myproj\n\n[release]\ndeep_verify=weeklyish\n",
        encoding="utf-8",
    )
    result = _run(tmp_path, "pp_deep_verify_mode myproj", tmp_path)
    assert result.returncode != 0
    assert result.stdout.strip() == ""
    assert "weeklyish" in result.stderr
    assert "weekly" in result.stderr and "off" in result.stderr


def test_deep_verify_offhours_window_present_in_release_no_warning(
    tmp_path: pathlib.Path,
) -> None:
    """deep_verify_offhours_window under [release] resolves to its own value
    with no stderr warning."""
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text(
        "[project]\nproject_name=myproj\n\n[release]\ndeep_verify_offhours_window=02:00-02:59\n",
        encoding="utf-8",
    )
    result = _run(tmp_path, "pp_deep_verify_offhours_window myproj", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "02:00-02:59"
    assert result.stderr.strip() == ""


def test_deep_verify_offhours_window_absent_everywhere_defaults_no_warning(
    tmp_path: pathlib.Path,
) -> None:
    """deep_verify_offhours_window absent from every section resolves to the
    documented default (03:00-03:59) with no warning."""
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text("[project]\nproject_name=myproj\n", encoding="utf-8")
    result = _run(tmp_path, "pp_deep_verify_offhours_window myproj", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "03:00-03:59"
    assert result.stderr.strip() == ""


def test_deep_verify_offhours_window_misplaced_section_warns_and_defaults(
    tmp_path: pathlib.Path,
) -> None:
    """deep_verify_offhours_window present under a non-[release] section (and
    absent from [release]) still resolves to the documented default
    (03:00-03:59) and exits 0, but now emits a stderr warning naming both the
    section it was found in and [release]. This is the acceptance-criterion
    test for the second key; it fails if the misplaced-key warning is
    reverted."""
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text(
        "[project]\nproject_name=myproj\n\n[debug]\ndeep_verify_offhours_window=01:00-01:59\n",
        encoding="utf-8",
    )
    result = _run(tmp_path, "pp_deep_verify_offhours_window myproj", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "03:00-03:59"
    assert "[debug]" in result.stderr
    assert "[release]" in result.stderr
    assert "deep_verify_offhours_window" in result.stderr


# ---------------------------------------------------------------------------
# pp_deep_verify_mode / pp_deep_verify_offhours_window — the three-tier chain:
# project.cfg [release] > kanban.cfg [release] > built-in default. The
# kanban.cfg value is what lets an operator set the cadence and window
# fleet-wide; a project's own value always wins over it.
# ---------------------------------------------------------------------------


def _write_kanban_cfg(root: pathlib.Path, body: str) -> pathlib.Path:
    cfg = root / "kanban.cfg"
    cfg.write_text(body, encoding="utf-8")
    return cfg


def test_deep_verify_mode_kanban_cfg_off_applies_when_project_cfg_is_silent(
    tmp_path: pathlib.Path,
) -> None:
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text("[project]\nproject_name=myproj\n", encoding="utf-8")
    _write_kanban_cfg(tmp_path, "[release]\ndeep_verify = off\n")
    result = _run(tmp_path, "pp_deep_verify_mode myproj", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "off"
    assert result.stderr.strip() == ""


def test_deep_verify_mode_kanban_cfg_applies_when_the_project_has_no_config_file(
    tmp_path: pathlib.Path,
) -> None:
    _make_project(tmp_path, "myproj")
    _write_kanban_cfg(tmp_path, "[release]\ndeep_verify = off\n")
    result = _run(tmp_path, "pp_deep_verify_mode myproj", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "off"


def test_deep_verify_mode_project_cfg_value_wins_over_kanban_cfg(tmp_path: pathlib.Path) -> None:
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text("[release]\ndeep_verify = weekly\n", encoding="utf-8")
    _write_kanban_cfg(tmp_path, "[release]\ndeep_verify = off\n")
    result = _run(tmp_path, "pp_deep_verify_mode myproj", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "weekly"


def test_deep_verify_mode_blank_project_value_falls_through_to_kanban_cfg(
    tmp_path: pathlib.Path,
) -> None:
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text("[release]\ndeep_verify =\n", encoding="utf-8")
    _write_kanban_cfg(tmp_path, "[release]\ndeep_verify = off\n")
    result = _run(tmp_path, "pp_deep_verify_mode myproj", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "off"


@pytest.mark.parametrize(
    "raw_value, expected_mode",
    [
        pytest.param("weekly", "weekly", id="weekly_word_spelling"),
        pytest.param("off", "off", id="off_word_spelling"),
        pytest.param("true", "weekly", id="true_alias_resolves_to_weekly"),
        pytest.param("false", "off", id="false_alias_resolves_to_off"),
    ],
)
def test_deep_verify_mode_kanban_cfg_accepts_every_documented_spelling(
    tmp_path: pathlib.Path, raw_value: str, expected_mode: str
) -> None:
    _make_project(tmp_path, "myproj")
    _write_kanban_cfg(tmp_path, f"[release]\ndeep_verify = {raw_value}\n")
    result = _run(tmp_path, "pp_deep_verify_mode myproj", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == expected_mode
    assert result.stderr.strip() == ""


def test_deep_verify_mode_unrecognised_kanban_cfg_value_refuses_naming_kanban_cfg(
    tmp_path: pathlib.Path,
) -> None:
    _make_project(tmp_path, "myproj")
    kanban_cfg = _write_kanban_cfg(tmp_path, "[release]\ndeep_verify = bogus\n")
    result = _run(tmp_path, "pp_deep_verify_mode myproj", tmp_path)
    assert result.returncode != 0
    assert result.stdout.strip() == ""
    assert str(kanban_cfg) in result.stderr
    assert "deep_verify" in result.stderr
    assert "bogus" in result.stderr


def test_deep_verify_mode_unrecognised_project_value_refuses_without_falling_through(
    tmp_path: pathlib.Path,
) -> None:
    """An invalid value at the project tier is refused, never skipped in
    favour of a valid kanban.cfg value: a typo must not silently pick up
    the fleet-wide cadence."""
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text("[release]\ndeep_verify = weeklyish\n", encoding="utf-8")
    _write_kanban_cfg(tmp_path, "[release]\ndeep_verify = off\n")
    result = _run(tmp_path, "pp_deep_verify_mode myproj", tmp_path)
    assert result.returncode != 0
    assert result.stdout.strip() == ""
    assert str(proj / "project.cfg") in result.stderr


def test_deep_verify_mode_misplaced_in_kanban_cfg_warns_and_defaults_weekly(
    tmp_path: pathlib.Path,
) -> None:
    _make_project(tmp_path, "myproj")
    kanban_cfg = _write_kanban_cfg(tmp_path, "[wake]\ndeep_verify = off\n")
    result = _run(tmp_path, "pp_deep_verify_mode myproj", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "weekly"
    assert "[wake]" in result.stderr
    assert "[release]" in result.stderr
    assert str(kanban_cfg) in result.stderr


def test_deep_verify_mode_misplaced_in_project_cfg_warns_then_uses_kanban_cfg(
    tmp_path: pathlib.Path,
) -> None:
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text("[training]\ndeep_verify = weekly\n", encoding="utf-8")
    _write_kanban_cfg(tmp_path, "[release]\ndeep_verify = off\n")
    result = _run(tmp_path, "pp_deep_verify_mode myproj", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "off"
    assert "[training]" in result.stderr
    assert len(result.stderr.strip().splitlines()) == 1


def test_deep_verify_offhours_window_kanban_cfg_value_applies_when_project_cfg_is_silent(
    tmp_path: pathlib.Path,
) -> None:
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text("[project]\nproject_name=myproj\n", encoding="utf-8")
    _write_kanban_cfg(tmp_path, "[release]\ndeep_verify_offhours_window = 01:00-01:59\n")
    result = _run(tmp_path, "pp_deep_verify_offhours_window myproj", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "01:00-01:59"
    assert result.stderr.strip() == ""


def test_deep_verify_offhours_window_project_cfg_value_wins_over_kanban_cfg(
    tmp_path: pathlib.Path,
) -> None:
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text("[release]\ndeep_verify_offhours_window = 02:00-02:59\n", encoding="utf-8")
    _write_kanban_cfg(tmp_path, "[release]\ndeep_verify_offhours_window = 01:00-01:59\n")
    result = _run(tmp_path, "pp_deep_verify_offhours_window myproj", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "02:00-02:59"


def test_deep_verify_offhours_window_malformed_project_value_falls_to_kanban_cfg_with_one_warning(
    tmp_path: pathlib.Path,
) -> None:
    """A malformed value counts as absent at its tier: the kanban.cfg value
    applies and exactly one warning names the project.cfg value."""
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text("[release]\ndeep_verify_offhours_window = 3am-4am\n", encoding="utf-8")
    _write_kanban_cfg(tmp_path, "[release]\ndeep_verify_offhours_window = 01:00-01:59\n")
    result = _run(tmp_path, "pp_deep_verify_offhours_window myproj", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "01:00-01:59"
    warnings = result.stderr.strip().splitlines()
    assert len(warnings) == 1, f"expected exactly one warning line: {result.stderr!r}"
    assert "3am-4am" in warnings[0]
    assert str(proj / "project.cfg") in warnings[0]


def test_deep_verify_offhours_window_malformed_kanban_value_falls_to_default_with_one_warning(
    tmp_path: pathlib.Path,
) -> None:
    _make_project(tmp_path, "myproj")
    kanban_cfg = _write_kanban_cfg(tmp_path, "[release]\ndeep_verify_offhours_window = 24:00-24:59\n")
    result = _run(tmp_path, "pp_deep_verify_offhours_window myproj", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "03:00-03:59"
    warnings = result.stderr.strip().splitlines()
    assert len(warnings) == 1, f"expected exactly one warning line: {result.stderr!r}"
    assert str(kanban_cfg) in warnings[0]


def test_deep_verify_offhours_window_absent_from_both_files_defaults_silently(
    tmp_path: pathlib.Path,
) -> None:
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text("[project]\nproject_name=myproj\n", encoding="utf-8")
    _write_kanban_cfg(tmp_path, "[release]\ndeep_verify = weekly\n")
    result = _run(tmp_path, "pp_deep_verify_offhours_window myproj", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "03:00-03:59"
    assert result.stderr.strip() == ""


# ---------------------------------------------------------------------------
# pp_resolve_project_name — project_resolve.not_found remedy branches
# ---------------------------------------------------------------------------


def test_resolve_project_name_not_found_with_registered_projects_names_them(
    tmp_path: pathlib.Path,
) -> None:
    """When projects.cfg lists at least one project and the candidate matches
    none of them, the not_found remedy still names the registered set — the
    branch for a non-empty registry, which this case still exercises."""
    (tmp_path / "projects.cfg").write_text(
        "[project:alpha]\npriority = 1\n\n[project:beta]\npriority = 2\n",
        encoding="utf-8",
    )
    result = _run(tmp_path, "pp_resolve_project_name nonexistent-project", tmp_path)
    assert result.returncode != 0
    assert result.stdout.strip() == ""
    assert "error:" in result.stderr
    assert "project_resolve.not_found" in result.stderr
    assert "remedy:" in result.stderr
    assert "(registered: " in result.stderr
    assert "alpha" in result.stderr
    assert "beta" in result.stderr
    assert "(registered: )" not in result.stderr


def test_resolve_project_name_not_found_with_empty_registry_names_add_project(
    tmp_path: pathlib.Path,
) -> None:
    """When projects.cfg exists but registers nothing, the not_found remedy
    must not degenerate to a bare '(registered: )' — it names the verb that
    registers a project instead, so the refusal always hands back something
    runnable."""
    (tmp_path / "projects.cfg").write_text("", encoding="utf-8")
    result = _run(tmp_path, "pp_resolve_project_name nonexistent-project", tmp_path)
    assert result.returncode != 0
    assert result.stdout.strip() == ""
    assert "error:" in result.stderr
    assert "project_resolve.not_found" in result.stderr
    assert "remedy:" in result.stderr
    assert "(registered: )" not in result.stderr
    # The registering verb is the add-project.sh installed under the kanban
    # root, shell-quoted, so the command runs as printed from any directory.
    remedy = next(line for line in result.stderr.splitlines() if line.startswith("remedy:"))
    command = remedy.split("Register one first: ", 1)[1].rstrip(".")
    assert shlex.split(command) == [
        f"{tmp_path}/scripts/add-project.sh",
        "--project",
        "nonexistent-project",
    ], remedy


# ---------------------------------------------------------------------------
# pp_overwatch_resolve — whether OVERWATCH may act autonomously on a
# resolvable CM block for a project: project.cfg [release]
# overwatch_resolve override when present, else kanban.cfg [release]
# overwatch_resolve default. Absent/blank/unreadable resolves to 'false'
# (never an error, never 'true' -- the opposite fail-safe direction from
# pp_max_rc_retries). An unrecognized value refuses dual-channel.
# ---------------------------------------------------------------------------


def test_overwatch_resolve_absent_from_both_defaults_false(
    tmp_path: pathlib.Path,
) -> None:
    """Absent from both project.cfg and kanban.cfg resolves to 'false' with
    exit 0 and no stderr diagnostic -- this is the shipped, expected state
    for every project until an operator opts in."""
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text("[project]\nproject_name=myproj\n", encoding="utf-8")
    result = _run(tmp_path, "pp_overwatch_resolve myproj", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "false"
    assert result.stderr.strip() == ""


def test_overwatch_resolve_absent_kanban_cfg_missing_defaults_false(
    tmp_path: pathlib.Path,
) -> None:
    """Absent project.cfg [release] section and no kanban.cfg at all still
    resolves to 'false' rather than failing loud -- unlike pp_max_rc_retries,
    an unreadable/missing kanban.cfg is not an error for this key."""
    _make_project(tmp_path, "myproj")
    result = _run(tmp_path, "pp_overwatch_resolve myproj", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "false"


def test_overwatch_resolve_kanban_default_true_used_when_project_silent(
    tmp_path: pathlib.Path,
) -> None:
    """A project with no override inherits the kanban.cfg [release]
    overwatch_resolve default."""
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text("[project]\nproject_name=myproj\n", encoding="utf-8")
    (tmp_path / "kanban.cfg").write_text("[release]\noverwatch_resolve=true\n", encoding="utf-8")
    result = _run(tmp_path, "pp_overwatch_resolve myproj", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "true"


def test_overwatch_resolve_project_true_wins_over_kanban_false(
    tmp_path: pathlib.Path,
) -> None:
    """A project.cfg override of 'true' wins over a kanban.cfg default of
    'false', proving the precedence direction."""
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text(
        "[project]\nproject_name=myproj\n\n[release]\noverwatch_resolve=true\n",
        encoding="utf-8",
    )
    (tmp_path / "kanban.cfg").write_text("[release]\noverwatch_resolve=false\n", encoding="utf-8")
    result = _run(tmp_path, "pp_overwatch_resolve myproj", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "true"


def test_overwatch_resolve_project_false_wins_over_kanban_true(
    tmp_path: pathlib.Path,
) -> None:
    """A project.cfg override of 'false' wins over a kanban.cfg default of
    'true', proving the precedence direction in the other boolean sense."""
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text(
        "[project]\nproject_name=myproj\n\n[release]\noverwatch_resolve=false\n",
        encoding="utf-8",
    )
    (tmp_path / "kanban.cfg").write_text("[release]\noverwatch_resolve=true\n", encoding="utf-8")
    result = _run(tmp_path, "pp_overwatch_resolve myproj", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "false"


def test_overwatch_resolve_blank_project_value_falls_through_to_kanban(
    tmp_path: pathlib.Path,
) -> None:
    """A blank project.cfg overwatch_resolve value is treated as absent and
    falls through to the kanban.cfg default, matching pp_max_rc_retries'
    own blank-falls-through behavior."""
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text(
        "[project]\nproject_name=myproj\n\n[release]\noverwatch_resolve=\n",
        encoding="utf-8",
    )
    (tmp_path / "kanban.cfg").write_text("[release]\noverwatch_resolve=true\n", encoding="utf-8")
    result = _run(tmp_path, "pp_overwatch_resolve myproj", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "true"


def test_overwatch_resolve_unrecognized_value_refuses_dual_channel(
    tmp_path: pathlib.Path,
) -> None:
    """An unrecognized value (e.g. 'yes') produces a non-zero, dual-channel
    refusal naming the key, the offending value, and a runnable remedy --
    and never resolves to either boolean on stdout."""
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text(
        "[project]\nproject_name=myproj\n\n[release]\noverwatch_resolve=yes\n",
        encoding="utf-8",
    )
    result = _run(tmp_path, "pp_overwatch_resolve myproj", tmp_path)
    assert result.returncode != 0
    assert result.stdout.strip() == ""
    assert "error:" in result.stderr
    assert "overwatch_resolve.invalid_value" in result.stderr
    assert "message:" in result.stderr
    assert "yes" in result.stderr
    assert "overwatch_resolve" in result.stderr
    assert "remedy:" in result.stderr


def test_overwatch_resolve_unrecognized_kanban_default_refuses_dual_channel(
    tmp_path: pathlib.Path,
) -> None:
    """An unrecognized kanban.cfg default (no project override present)
    refuses the same way -- the refusal fires regardless of which tier
    supplied the bad value."""
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text("[project]\nproject_name=myproj\n", encoding="utf-8")
    (tmp_path / "kanban.cfg").write_text("[release]\noverwatch_resolve=maybe\n", encoding="utf-8")
    result = _run(tmp_path, "pp_overwatch_resolve myproj", tmp_path)
    assert result.returncode != 0
    assert result.stdout.strip() == ""
    assert "maybe" in result.stderr
    assert "overwatch_resolve.invalid_value" in result.stderr


# ---------------------------------------------------------------------------
# pp_block_triage — whether OVERWATCH's block-triage sweep may act on a
# Block Record for a project: project.cfg [overwatch] block_triage override
# when present, else kanban.cfg [overwatch] block_triage default. Same
# resolver semantics as pp_overwatch_resolve, but absent/blank/unreadable
# in both files resolves to 'true' (the shipped default). An unrecognized
# value refuses dual-channel.
# ---------------------------------------------------------------------------


def test_block_triage_absent_from_both_defaults_true(tmp_path: pathlib.Path) -> None:
    """Absent from both project.cfg and kanban.cfg resolves to 'true' with
    exit 0 and no stderr diagnostic -- an upgraded install whose kanban.cfg
    predates the key behaves as the example file ships it."""
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text("[project]\nproject_name=myproj\n", encoding="utf-8")
    (tmp_path / "kanban.cfg").write_text("[wake]\nzero_progress_cap = 3\n", encoding="utf-8")
    result = _run(tmp_path, "pp_block_triage myproj", tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "true"
    assert result.stderr.strip() == ""


def test_block_triage_missing_kanban_cfg_defaults_true(tmp_path: pathlib.Path) -> None:
    """No project.cfg and no kanban.cfg at all still resolves to the
    default rather than failing loud."""
    _make_project(tmp_path, "myproj")
    result = _run(tmp_path, "pp_block_triage myproj", tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "true"


def test_block_triage_kanban_false_used_when_project_silent(tmp_path: pathlib.Path) -> None:
    """A project with no override inherits the kanban.cfg [overwatch]
    block_triage value."""
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text("[project]\nproject_name=myproj\n", encoding="utf-8")
    (tmp_path / "kanban.cfg").write_text("[overwatch]\nblock_triage = false\n", encoding="utf-8")
    result = _run(tmp_path, "pp_block_triage myproj", tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "false"


def test_block_triage_project_false_wins_over_kanban_true(tmp_path: pathlib.Path) -> None:
    """project.cfg false overrides kanban.cfg true."""
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text(
        "[project]\nproject_name=myproj\n\n[overwatch]\nblock_triage = false\n",
        encoding="utf-8",
    )
    (tmp_path / "kanban.cfg").write_text("[overwatch]\nblock_triage = true\n", encoding="utf-8")
    result = _run(tmp_path, "pp_block_triage myproj", tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "false"


def test_block_triage_project_true_wins_over_kanban_false(tmp_path: pathlib.Path) -> None:
    """project.cfg true overrides kanban.cfg false -- the precedence holds
    in both boolean directions."""
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text(
        "[project]\nproject_name=myproj\n\n[overwatch]\nblock_triage = true\n",
        encoding="utf-8",
    )
    (tmp_path / "kanban.cfg").write_text("[overwatch]\nblock_triage = false\n", encoding="utf-8")
    result = _run(tmp_path, "pp_block_triage myproj", tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "true"


def test_block_triage_blank_project_value_falls_through_to_kanban(tmp_path: pathlib.Path) -> None:
    """A blank project.cfg value reads as absent and falls through to the
    kanban.cfg value."""
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text(
        "[project]\nproject_name=myproj\n\n[overwatch]\nblock_triage =\n",
        encoding="utf-8",
    )
    (tmp_path / "kanban.cfg").write_text("[overwatch]\nblock_triage = false\n", encoding="utf-8")
    result = _run(tmp_path, "pp_block_triage myproj", tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "false"


def test_block_triage_key_in_another_section_is_not_read(tmp_path: pathlib.Path) -> None:
    """Only the [overwatch]-scoped key counts: a block_triage line under
    [release] neither overrides nor refuses."""
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text(
        "[project]\nproject_name=myproj\n\n[release]\nblock_triage = maybe\n",
        encoding="utf-8",
    )
    (tmp_path / "kanban.cfg").write_text("[overwatch]\nblock_triage = false\n", encoding="utf-8")
    result = _run(tmp_path, "pp_block_triage myproj", tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "false"


@pytest.mark.parametrize("value", ["yes", "on", "True", "1"])
def test_block_triage_non_boolean_project_value_refuses_dual_channel(tmp_path: pathlib.Path, value: str) -> None:
    """Only the exact spellings true/false are accepted. Anything else is
    refused non-zero with the stable error key, a message naming the key,
    the value and the accepted set, and a remedy naming the file -- and
    nothing is echoed on stdout for a caller to mistake for an answer."""
    proj = _make_project(tmp_path, "myproj")
    cfg = proj / "project.cfg"
    cfg.write_text(
        f"[project]\nproject_name=myproj\n\n[overwatch]\nblock_triage = {value}\n",
        encoding="utf-8",
    )
    (tmp_path / "kanban.cfg").write_text("[overwatch]\nblock_triage = true\n", encoding="utf-8")
    result = _run(tmp_path, "pp_block_triage myproj", tmp_path)
    assert result.returncode != 0
    assert result.stdout.strip() == ""
    assert "error:   block_triage.invalid_value" in result.stderr, result.stderr
    assert f"'{value}'" in result.stderr
    assert "true|false" in result.stderr
    remedy = [line for line in result.stderr.splitlines() if line.startswith("remedy:")]
    assert remedy and str(cfg) in remedy[0], result.stderr
    assert "<" not in remedy[0], "remedy must be runnable as printed"


def test_block_triage_remedy_is_runnable_and_fixes_the_file(tmp_path: pathlib.Path) -> None:
    """Pasting the printed remedy into a shell repairs the value so the
    same resolver call succeeds."""
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text(
        "[project]\nproject_name=myproj\n\n[overwatch]\n  block_triage  =  yes\n",
        encoding="utf-8",
    )
    refused = _run(tmp_path, "pp_block_triage myproj", tmp_path)
    remedy = [line for line in refused.stderr.splitlines() if line.startswith("remedy:")]
    assert remedy, refused.stderr
    fixed = run_bash(tmp_path, remedy[0].split(":", 1)[1].strip())
    assert fixed.returncode == 0, fixed.stderr
    result = _run(tmp_path, "pp_block_triage myproj", tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "true"


def test_block_triage_non_boolean_kanban_value_refuses_dual_channel(tmp_path: pathlib.Path) -> None:
    """A bad kanban.cfg value with no project override refuses the same
    way, naming kanban.cfg as the source."""
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text("[project]\nproject_name=myproj\n", encoding="utf-8")
    (tmp_path / "kanban.cfg").write_text("[overwatch]\nblock_triage = off\n", encoding="utf-8")
    result = _run(tmp_path, "pp_block_triage myproj", tmp_path)
    assert result.returncode != 0
    assert result.stdout.strip() == ""
    assert "block_triage.invalid_value" in result.stderr
    assert "'off'" in result.stderr
    assert str(tmp_path / "kanban.cfg") in result.stderr


# ---------------------------------------------------------------------------
# pp_stale_lock_reap_after_wake_periods — the stale-lock reap threshold, in
# wake periods: project.cfg [locks] stale_lock_reap_after_wake_periods
# override when present, else kanban.cfg [locks]
# stale_lock_reap_after_wake_periods default. Unlike pp_max_rc_retries,
# absence from BOTH is never an error -- it resolves to the shipped
# default (3).
# ---------------------------------------------------------------------------


def test_stale_lock_reap_after_wake_periods_absent_everywhere_defaults_to_three(
    tmp_path: pathlib.Path,
) -> None:
    """Absence from both project.cfg and kanban.cfg resolves to the shipped
    default (3) rather than failing loud -- this knob has a safe default,
    unlike max_rc_retries."""
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text("[project]\nproject_name=myproj\n", encoding="utf-8")
    result = _run(tmp_path, "pp_stale_lock_reap_after_wake_periods myproj", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "3"
    assert result.stderr.strip() == ""


def test_stale_lock_reap_after_wake_periods_returns_kanban_default(
    tmp_path: pathlib.Path,
) -> None:
    """Returns the kanban.cfg [locks] default when no project override exists."""
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text("[project]\nproject_name=myproj\n", encoding="utf-8")
    (tmp_path / "kanban.cfg").write_text("[locks]\nstale_lock_reap_after_wake_periods=5\n", encoding="utf-8")
    result = _run(tmp_path, "pp_stale_lock_reap_after_wake_periods myproj", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "5"


def test_stale_lock_reap_after_wake_periods_project_override_wins(
    tmp_path: pathlib.Path,
) -> None:
    """The project.cfg [locks] override wins over the kanban.cfg default
    when both are present."""
    proj = _make_project(tmp_path, "patient-project")
    (proj / "project.cfg").write_text(
        "[project]\nproject_name=patient-project\n\n[locks]\nstale_lock_reap_after_wake_periods=10\n",
        encoding="utf-8",
    )
    (tmp_path / "kanban.cfg").write_text("[locks]\nstale_lock_reap_after_wake_periods=3\n", encoding="utf-8")
    result = _run(tmp_path, "pp_stale_lock_reap_after_wake_periods patient-project", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "10"


def test_stale_lock_reap_after_wake_periods_rejects_non_integer_project_override(
    tmp_path: pathlib.Path,
) -> None:
    """A malformed (non-integer) project.cfg override fails loud rather than
    silently coercing or falling through to the kanban.cfg default."""
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text(
        "[project]\nproject_name=myproj\n\n[locks]\nstale_lock_reap_after_wake_periods=notanumber\n",
        encoding="utf-8",
    )
    result = _run(tmp_path, "pp_stale_lock_reap_after_wake_periods myproj", tmp_path)
    assert result.returncode != 0
    assert "invalid value" in result.stderr.lower()


def test_stale_lock_reap_after_wake_periods_rejects_zero(tmp_path: pathlib.Path) -> None:
    """Zero is rejected rather than silently disabling the reaper."""
    proj = _make_project(tmp_path, "myproj")
    (proj / "project.cfg").write_text("[project]\nproject_name=myproj\n", encoding="utf-8")
    (tmp_path / "kanban.cfg").write_text("[locks]\nstale_lock_reap_after_wake_periods=0\n", encoding="utf-8")
    result = _run(tmp_path, "pp_stale_lock_reap_after_wake_periods myproj", tmp_path)
    assert result.returncode != 0
    assert "invalid value" in result.stderr.lower()


# ---------------------------------------------------------------------------
# pp_dev_tree_path
# ---------------------------------------------------------------------------


def test_dev_tree_path_reads_the_project_cfg_key(tmp_path: pathlib.Path) -> None:
    project_dir = _make_project(tmp_path, "my-app")
    (project_dir / "project.cfg").write_text(
        "[project]\nproject_name = my-app\ndev_tree_path = /srv/develop/my-app\n", encoding="utf-8"
    )
    result = _run(tmp_path, "pp_dev_tree_path my-app", tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "/srv/develop/my-app"


def test_dev_tree_path_is_empty_without_a_config_or_key(tmp_path: pathlib.Path) -> None:
    _make_project(tmp_path, "bare")
    result = _run(tmp_path, "pp_dev_tree_path bare", tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


def test_dev_tree_path_needs_a_project_name(tmp_path: pathlib.Path) -> None:
    result = _run(tmp_path, "pp_dev_tree_path", tmp_path, PGAI_PROJECT_NAME="")
    assert result.returncode == 1


# ---------------------------------------------------------------------------
# pp_load_config
# ---------------------------------------------------------------------------

_PP_KNOWN_FIELDS = (
    "PP_project_name",
    "PP_dev_tree_path",
    "PP_git_repo_url",
    "PP_git_remote_name",
    "PP_workflow_type",
    "PP_branch_prefix",
    "PP_max_patch",
    "PP_max_minor",
    "PP_max_major",
    "PP_max_priorities_per_run",
    "PP_cm_release_pre_squash_hook",
    "PP_cm_release_pre_tag_hook",
    "PP_cm_release_post_tag_hook",
    "PP_TEMP_DIR",
)


def test_load_config_failure_leaves_no_earlier_project_values(tmp_path: pathlib.Path) -> None:
    """A failed load in the same shell clears what an earlier project's load exported."""
    kanban = tmp_path / "kanban"
    (_make_project(kanban, "alpha") / "project.cfg").write_text(
        "[project]\nproject_name = alpha\ndev_tree_path = /srv/alpha-repo\ntemp_dir = alpha-td\n"
        "branch_prefix = ai_\n[versioning]\nmax_patch = 9\n"
        "[hooks]\ncm_release_pre_tag_hook = /srv/alpha-hook\n",
        encoding="utf-8",
    )
    _make_project(kanban, "beta")
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()
    report = " ".join(f'"{name}=${{{name}-<unset>}}"' for name in _PP_KNOWN_FIELDS)
    script = (
        "pp_load_config alpha; echo \"alpha_rc=$?\"; "
        "printf '%s\\n' \"alpha_dev=$PP_dev_tree_path\" \"alpha_temp=$PP_TEMP_DIR\"; "
        "pp_load_config beta; echo \"beta_rc=$?\"; "
        f"printf 'after_beta %s\\n' {report}"
    )
    result = _run(
        tmp_path,
        script,
        kanban,
        PGAI_AGENT_KANBAN_ROOT_PATH=str(kanban),
        PGAI_AGENT_KANBAN_TEMP_DIR=str(temp_root),
    )
    lines = result.stdout.splitlines()
    assert "alpha_rc=0" in lines, result.stderr
    assert "alpha_dev=/srv/alpha-repo" in lines
    assert f"alpha_temp={temp_root}/alpha-td" in lines
    assert "beta_rc=1" in lines
    assert "not found" in result.stderr
    after_beta = [line.removeprefix("after_beta ") for line in lines if line.startswith("after_beta ")]
    assert len(after_beta) == len(_PP_KNOWN_FIELDS)
    for entry in after_beta:
        name, _, value = entry.partition("=")
        assert value in ("", "<unset>"), f"{name} kept alpha's value {value!r} after beta's load failed"


def _legacy_load_env(kanban: pathlib.Path, temp_root: pathlib.Path, **extra: str) -> dict[str, str]:
    """Inherited environment without any PP_* name, pointed at a throwaway kanban root."""
    stripped = ("PGAI_PROJECT_NAME", "PGAI_TASKS_DIR", "PGAI_PROJECT_ROOT")
    env = {k: v for k, v in os.environ.items() if not k.startswith("PP_") and k not in stripped}
    env.update(
        KANBAN_ROOT=str(kanban),
        PGAI_AGENT_KANBAN_ROOT_PATH=str(kanban),
        PGAI_AGENT_KANBAN_TEMP_DIR=str(temp_root),
    )
    env.update(extra)
    return env


def _report_fields(label: str) -> str:
    """Bash that prints '<label> NAME=value' (or '=<unset>') for every known field."""
    report = " ".join(f'"{name}=${{{name}-<unset>}}"' for name in _PP_KNOWN_FIELDS)
    return f"printf '{label} %s\\n' {report}"


def _fields(lines: list[str], label: str) -> dict[str, str]:
    """Parse the lines _report_fields(label) printed into {name: value}."""
    prefix = f"{label} "
    return dict(line.removeprefix(prefix).partition("=")[::2] for line in lines if line.startswith(prefix))


def test_load_config_legacy_after_ini_keeps_none_of_the_ini_values(tmp_path: pathlib.Path) -> None:
    """A legacy PROJECT.cfg load after an INI load leaves no known field at the INI project's value."""
    kanban = tmp_path / "kanban"
    (_make_project(kanban, "alpha") / "project.cfg").write_text(
        "[project]\nproject_name = alpha\ndev_tree_path = /srv/alpha-repo\n"
        "git_repo_url = https://example.invalid/a.git\n[versioning]\nmax_patch = 9\n"
        "[hooks]\ncm_release_pre_tag_hook = /srv/h2\n",
        encoding="utf-8",
    )
    (_make_project(kanban, "gamma") / "PROJECT.cfg").write_text(
        "project_name=gamma\ndev_tree_path=/srv/gamma-repo\ntemp_dir=gamma-td\nbranch_prefix=g_\n",
        encoding="utf-8",
    )
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()
    script = (
        f"pp_load_config alpha; echo \"alpha_rc=$?\"; {_report_fields('alpha')}; "
        f"pp_load_config gamma; echo \"gamma_rc=$?\"; {_report_fields('gamma')}"
    )
    result = run_bash(tmp_path, f"source {_LIB} && {script}", base_env=_legacy_load_env(kanban, temp_root))
    lines = result.stdout.splitlines()
    assert "alpha_rc=0" in lines, result.stderr
    assert "gamma_rc=0" in lines, result.stderr
    alpha, gamma = _fields(lines, "alpha"), _fields(lines, "gamma")
    assert alpha["PP_git_repo_url"] == "https://example.invalid/a.git"
    assert alpha["PP_cm_release_pre_tag_hook"] == "/srv/h2"
    assert gamma["PP_project_name"] == "gamma"
    assert gamma["PP_dev_tree_path"] == "/srv/gamma-repo"
    assert gamma["PP_git_repo_url"] in ("", "<unset>")
    assert gamma["PP_cm_release_pre_tag_hook"] in ("", "<unset>")
    assert len(gamma) == len(_PP_KNOWN_FIELDS)
    for name, value in gamma.items():
        if alpha[name] not in ("", "<unset>"):
            assert value != alpha[name], f"{name} kept alpha's value {value!r} after gamma's legacy load"


def test_load_config_second_legacy_load_replaces_the_first(tmp_path: pathlib.Path) -> None:
    """A second legacy load in the same shell exports its own values, and no inherited one survives."""
    kanban = tmp_path / "kanban"
    (_make_project(kanban, "gamma") / "PROJECT.cfg").write_text(
        "project_name=gamma\ndev_tree_path=/srv/gamma-repo\ntemp_dir=gamma-td\nbranch_prefix=g_\n",
        encoding="utf-8",
    )
    (_make_project(kanban, "delta") / "PROJECT.cfg").write_text(
        "project_name=delta\ndev_tree_path=/srv/delta-repo\ntemp_dir=delta-td\nbranch_prefix=d_\n",
        encoding="utf-8",
    )
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()
    env = _legacy_load_env(kanban, temp_root, PP_cm_release_pre_tag_hook="/srv/inherited-hook")
    script = (
        "pp_load_config gamma; echo \"gamma_rc=$?\"; "
        f"pp_load_config delta; echo \"delta_rc=$?\"; {_report_fields('delta')}"
    )
    result = run_bash(tmp_path, f"source {_LIB} && {script}", base_env=env)
    lines = result.stdout.splitlines()
    assert "gamma_rc=0" in lines, result.stderr
    assert "delta_rc=0" in lines, result.stderr
    delta = _fields(lines, "delta")
    assert delta["PP_project_name"] == "delta"
    assert delta["PP_dev_tree_path"] == "/srv/delta-repo"
    assert delta["PP_branch_prefix"] == "d_"
    assert delta["PP_TEMP_DIR"] == f"{temp_root}/delta-td"
    assert delta["PP_cm_release_pre_tag_hook"] in ("", "<unset>"), "the inherited hook survived a legacy load"


@pytest.mark.parametrize(
    ("cfg_body", "expected"),
    [
        (
            "project_name=gamma\ndev_tree_path=/srv/gamma-repo\n",
            {"PP_project_name": "gamma", "PP_dev_tree_path": "/srv/gamma-repo", "PP_TEMP_DIR": "projects/gamma"},
        ),
        (
            "project_name=gamma\ndev_tree_path=/srv/gamma-repo\ntemp_dir=gamma-td\nbranch_prefix=g_\n",
            {
                "PP_project_name": "gamma",
                "PP_dev_tree_path": "/srv/gamma-repo",
                "PP_branch_prefix": "g_",
                "PP_temp_dir": "gamma-td",
                "PP_TEMP_DIR": "gamma-td",
            },
        ),
    ],
    ids=["without-temp-dir", "with-temp-dir"],
)
def test_load_config_fresh_legacy_load_exports_only_the_file_fields(
    tmp_path: pathlib.Path, cfg_body: str, expected: dict[str, str]
) -> None:
    """A fresh legacy load exports the file's fields and PP_TEMP_DIR, never PP_before_vars or another local."""
    kanban = tmp_path / "kanban"
    (_make_project(kanban, "gamma") / "PROJECT.cfg").write_text(cfg_body, encoding="utf-8")
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()
    expected = {**expected, "PP_TEMP_DIR": f"{temp_root}/{expected['PP_TEMP_DIR']}"}
    child = (
        "printenv PP_before_vars; echo \"printenv_rc=$?\"; "
        "for n in $(compgen -e); do [[ $n == PP_* ]] && printf \"child %s=%s\\n\" \"$n\" \"${!n}\"; done; true"
    )
    script = f"pp_load_config gamma; echo \"rc=$?\"; bash -c {shlex.quote(child)}"
    result = run_bash(tmp_path, f"source {_LIB} && {script}", base_env=_legacy_load_env(kanban, temp_root))
    lines = result.stdout.splitlines()
    assert "rc=0" in lines, result.stderr
    assert lines[1] == "printenv_rc=1", f"PP_before_vars reached a child process, starting {lines[1:4]!r}"
    exported = _fields(lines, "child")
    assert exported == expected


_STRICT_CALLER_FORMS = {
    "or-form": 'rc=0; pp_load_config idiom2 || rc=$?; echo "rc=$rc"',
    "if-form": 'if pp_load_config idiom2; then echo "rc=0"; else echo "rc=$?"; fi',
}


@pytest.mark.parametrize("caller_form", sorted(_STRICT_CALLER_FORMS))
@pytest.mark.parametrize(
    ("cfg_body", "expected_rc", "expected"),
    [
        (
            "project_name=idiom2\ndev_tree_path=/srv/idiom2\n"
            "git_repo_url=$(git -C /nonexistent remote get-url origin 2>/dev/null)\n",
            0,
            {"PP_project_name": "idiom2", "PP_dev_tree_path": "/srv/idiom2", "PP_git_repo_url": ""},
        ),
        (
            "project_name=idiom2\nfalse\ndev_tree_path=/srv/idiom2\n",
            0,
            {"PP_project_name": "idiom2", "PP_dev_tree_path": "/srv/idiom2"},
        ),
        (
            "project_name=idiom2\ndev_tree_path=$UNDEFINED_THING/x\n",
            0,
            {"PP_project_name": "idiom2", "PP_dev_tree_path": "/x"},
        ),
        (
            "project_name=idiom2\ndev_tree_path=/srv/idiom2\nexit 0\n",
            1,
            {name: "<unset>" for name in _PP_KNOWN_FIELDS},
        ),
    ],
    ids=["failing-command-substitution", "failing-command", "unset-variable", "exit-line"],
)
def test_load_config_legacy_failing_line_under_strict_caller(
    tmp_path: pathlib.Path, cfg_body: str, expected_rc: int, expected: dict[str, str], caller_form: str
) -> None:
    """Under set -euo pipefail a legacy load exports the file's fields or fails; never 0 with them unset."""
    kanban = tmp_path / "kanban"
    (_make_project(kanban, "idiom2") / "PROJECT.cfg").write_text(cfg_body, encoding="utf-8")
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()
    env = _legacy_load_env(kanban, temp_root, PP_dev_tree_path="/srv/inherited", PP_TEMP_DIR="/srv/inherited-temp")
    script = f"set -euo pipefail; {_STRICT_CALLER_FORMS[caller_form]}; {_report_fields('after')}"
    result = run_bash(tmp_path, f"source {_LIB} && {script}", base_env=env)
    lines = result.stdout.splitlines()
    assert result.returncode == 0, f"the strict caller aborted: {result.stderr}"
    assert f"rc={expected_rc}" in lines, result.stderr
    after = _fields(lines, "after")
    assert not ("rc=0" in lines and after["PP_project_name"] == "<unset>"), "returned 0 with the file's fields unset"
    for name, value in expected.items():
        assert after[name] == value, f"{name}={after[name]!r}, expected {value!r}"
    if expected_rc == 0:
        assert after["PP_TEMP_DIR"] == f"{temp_root}/projects/idiom2"
    else:
        assert "stopped before it finished loading" in result.stderr
