"""
test_lint_bash_shared_blocks.py
===============================
Ten bash blocks once pasted across scripts now live once each in
team/scripts/lib/, and team/scripts/lint_bash_shared_blocks.py reports a
copy that reappears. This file pins both halves:

  * the lint: the real tree carries no copy (exit 0), each block's
    signature is caught in a script that pastes it (exit 1, one finding of
    that kind in the findings file), a status-line logger is caught in
    any line layout and in the shipped repo-root scripts (install.sh,
    docker/entrypoint.sh), the block's home file and comment lines are
    never reported, and the help prelude is only checked in the dashboard
    scripts it serves;
  * the helpers the scripts now call, run as the scripts run them:
    header_help_exit_if_requested, overwatch_resolve_check_project,
    gated_runner_reject_unknown_arg /
    gated_runner_report_pytest_missing, gated_runner_parse_args,
    verify_harness_init with pass / fail / warn, resolve_script_repo_root,
    pp_load_project_repo_root, the wake entry body a provider shim
    sources (lib/wake_entry.sh), and the status-line logger
    (script_log_init with info / ok / warn / err / die, log_line and
    script_colour_table in lib/script_common.sh).

Every fixture lives under pytest's tmp_path; kanban roots are throwaway
directories with every root variable pointed at them, and no live kanban
root is read or written.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

from tests.unit.shell_harness import run_bash

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_SCRIPTS = _TEAM_DIR / "scripts"
_LIB = _SCRIPTS / "lib"
_LINT = _SCRIPTS / "lint_bash_shared_blocks.py"

# One pasted copy of each block, as the scripts carried it, and the path
# (under a scratch scripts/ directory) a copy would sit at.
_COPIES = {
    "help-prelude": (
        "dashboard/pane.sh",
        'for __help_arg in "$@"; do\n' '  case "$__help_arg" in\n' "    -h|--help) exit 0 ;;\n" "  esac\n" "done\n",
    ),
    "overwatch-project": (
        "lib/overwatch-checks/check-x.sh",
        '        echo "check-x: ERROR: OVERWATCH_PROJECT not set and multiple projects registered." >&2\n',
    ),
    "runner-unknown-arg": (
        "run-x-tests.sh",
        "      echo \"FAIL: unrecognized argument '$1'.\" >&2\n",
    ),
    "runner-pytest-missing": (
        "run-y-tests.sh",
        '  echo "FAIL: pytest is not installed in this Python environment." >&2\n',
    ),
    "verify-harness": (
        "verify-x.sh",
        'pass() { echo "${C_GREEN}PASS${C_RESET}: $*"; (( PASS_COUNT++ )) || true; }\n',
    ),
    "repo-root-walk": (
        "dashboard/pane2.sh",
        'while [[ "$REPO_ROOT" != "/" ]] && [[ ! -d "$REPO_ROOT/.git" ]]; do\n'
        '  REPO_ROOT="$(dirname "$REPO_ROOT")"\n'
        "done\n",
    ),
    "project-repo-root": (
        "cm/x.sh",
        "    echo \"ERROR: could not load project.cfg for project '$PROJECT_NAME'\" >&2\n",
    ),
    "runner-workers-refusal": (
        "run-z-tests.sh",
        '          "--workers needs a non-negative whole number in decimal digits after it (got \'${2:-}\')." \\\n',
    ),
    "wake-argument-loop": (
        "wake/gemini.sh",
        '  echo "WARNING: positional agent argument is deprecated." >&2\n',
    ),
    "status-logger": (
        "install-x.sh",
        'warn()  { echo "${C_YELLOW}[install-x]${C_RESET} WARNING: $*"; }\n',
    ),
}


def _lint(scripts_dir: pathlib.Path, findings: pathlib.Path | None = None) -> subprocess.CompletedProcess:
    argv = [sys.executable, str(_LINT), "--scripts-dir", str(scripts_dir)]
    if findings is not None:
        argv += ["--findings-file", str(findings)]
    return subprocess.run(argv, capture_output=True, text=True, timeout=120)


def _write(root: pathlib.Path, rel: str, body: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")


# --- the lint --------------------------------------------------------------------


def test_the_tree_carries_no_copied_block() -> None:
    result = _lint(_SCRIPTS)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "; 0 copy(ies)" in result.stdout


def test_every_block_has_a_copy_case() -> None:
    sys.path.insert(0, str(_SCRIPTS))
    try:
        import lint_bash_shared_blocks as lint  # noqa: PLC0415 - loaded from the scripts dir under test
    finally:
        sys.path.remove(str(_SCRIPTS))
    assert {block.name for block in lint.BLOCKS} == set(_COPIES)
    for block in lint.BLOCKS:
        assert (_SCRIPTS / block.home).is_file(), f"{block.name}'s home {block.home} is missing"


@pytest.mark.parametrize("kind", sorted(_COPIES))
def test_a_pasted_copy_is_reported(tmp_path: pathlib.Path, kind: str) -> None:
    rel, body = _COPIES[kind]
    scripts = tmp_path / "scripts"
    _write(scripts, rel, body)
    findings_file = tmp_path / "findings.json"
    result = _lint(scripts, findings_file)
    assert result.returncode == 1, result.stdout + result.stderr
    findings = json.loads(findings_file.read_text(encoding="utf-8"))
    assert [f["kind"] for f in findings] == [kind], findings
    assert findings[0]["file"] == f"team/scripts/{rel}"
    assert findings[0]["line"] == 2
    assert f"team/scripts/{rel}:2: {kind} copied" in result.stdout


@pytest.mark.parametrize("kind", sorted(_COPIES))
def test_the_block_home_is_not_reported(tmp_path: pathlib.Path, kind: str) -> None:
    sys.path.insert(0, str(_SCRIPTS))
    try:
        import lint_bash_shared_blocks as lint  # noqa: PLC0415 - loaded from the scripts dir under test
    finally:
        sys.path.remove(str(_SCRIPTS))
    home = next(block.home for block in lint.BLOCKS if block.name == kind)
    scripts = tmp_path / "scripts"
    _write(scripts, home, _COPIES[kind][1])
    result = _lint(scripts)
    assert result.returncode == 0, result.stdout


def test_a_commented_signature_is_not_reported(tmp_path: pathlib.Path) -> None:
    scripts = tmp_path / "scripts"
    _write(scripts, "run-x-tests.sh", "# echo \"FAIL: unrecognized argument '$1'.\" >&2\n")
    result = _lint(scripts)
    assert result.returncode == 0, result.stdout


def test_the_help_prelude_is_checked_in_dashboard_scripts_only(tmp_path: pathlib.Path) -> None:
    scripts = tmp_path / "scripts"
    _write(scripts, "kanban-status.sh", _COPIES["help-prelude"][1])
    _write(scripts, "dashboard/lib/helper.sh", _COPIES["help-prelude"][1])
    result = _lint(scripts)
    assert result.returncode == 0, result.stdout


def test_a_help_arm_printing_the_header_itself_is_reported(tmp_path: pathlib.Path) -> None:
    scripts = tmp_path / "scripts"
    _write(
        scripts,
        "dashboard/pane.sh",
        'while [[ $# -gt 0 ]]; do\n  case "$1" in\n'
        "    -h|--help)\n"
        "      # shellcheck source=../lib/header_help.sh\n"
        '      source "$(dirname "${BASH_SOURCE[0]}")/../lib/header_help.sh"\n'
        '      header_help_print "${BASH_SOURCE[0]}"\n'
        "      exit 0\n"
        "      ;;\n  esac\ndone\n",
    )
    result = _lint(scripts)
    assert result.returncode == 1
    assert "dashboard/pane.sh:4: help-prelude copied" in result.stdout


@pytest.mark.parametrize(
    "rel, body",
    [
        ("install.sh", 'info() {\n  echo -e "${C_BLUE}[install]${C_RESET} $*"\n}\n'),
        ("docker/entrypoint.sh", 'err() { echo "${C_RED}[entrypoint]${C_RESET} ERROR: $*" >&2; }\n'),
        ("team/scripts/x.sh", 'warn()\n{\n  local msg="$*"\n  printf "%s\\n" "${C_YELLOW}[x]${C_RESET} $msg"\n}\n'),
    ],
    ids=["root-install-multi-line", "root-entrypoint-one-line", "scripts-brace-on-next-line"],
)
def test_a_pasted_logger_in_any_shipped_script_is_reported(tmp_path: pathlib.Path, rel: str, body: str) -> None:
    repo = tmp_path / "repo"
    (repo / "team" / "scripts").mkdir(parents=True)
    _write(repo, rel, body)
    findings_file = tmp_path / "findings.json"
    result = _lint(repo / "team" / "scripts", findings_file)
    assert result.returncode == 1, result.stdout + result.stderr
    findings = json.loads(findings_file.read_text(encoding="utf-8"))
    assert [(f["kind"], f["file"], f["line"]) for f in findings] == [("status-logger", rel, 2)], findings
    assert f"{rel}:2: status-logger copied" in result.stdout


def test_a_root_script_logger_that_echoes_no_colour_itself_is_not_reported(tmp_path: pathlib.Path) -> None:
    repo = tmp_path / "repo"
    (repo / "team" / "scripts").mkdir(parents=True)
    _write(repo, "install.sh", '# info() {\n#   echo "${C_BLUE}[install]${C_RESET} $*"\n# }\n')
    _write(repo, "docker/entrypoint.sh", 'warn() {\n  log_line "$C_YELLOW" "[x]" " $*"\n}\necho "${C_RESET}"\n')
    result = _lint(repo / "team" / "scripts")
    assert result.returncode == 0, result.stdout


def test_root_scripts_are_scanned_only_beside_a_team_scripts_dir(tmp_path: pathlib.Path) -> None:
    (tmp_path / "scripts").mkdir()
    _write(tmp_path, "install.sh", _COPIES["status-logger"][1])
    result = _lint(tmp_path / "scripts")
    assert result.returncode == 0, result.stdout


def test_a_missing_scripts_dir_is_refused(tmp_path: pathlib.Path) -> None:
    result = _lint(tmp_path / "absent")
    assert result.returncode == 2
    assert "is not a directory" in result.stderr


# --- the helpers -----------------------------------------------------------------


def test_help_prelude_prints_the_header_and_exits_on_either_flag(tmp_path: pathlib.Path) -> None:
    script = tmp_path / "pane.sh"
    script.write_text(
        "#!/usr/bin/env bash\n# pane.sh -- draws a pane\n#\n# Usage: pane.sh [--session NAME]\n\n"
        f"source '{_LIB / 'header_help.sh'}'\n"
        'header_help_exit_if_requested "${BASH_SOURCE[0]}" "$@"\n'
        "echo body-ran\n",
        encoding="utf-8",
    )
    for argv in ("--help", "-h", "--session --help"):
        result = subprocess.run(["bash", str(script), *argv.split()], capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        assert result.stdout == "pane.sh -- draws a pane\n\nUsage: pane.sh [--session NAME]\n"
    result = subprocess.run(["bash", str(script), "--session", "x"], capture_output=True, text=True, timeout=30)
    assert result.stdout == "body-ran\n"


def test_help_prelude_without_a_path_returns_2(tmp_path: pathlib.Path) -> None:
    result = run_bash(tmp_path, f"source '{_LIB / 'header_help.sh'}'\nheader_help_exit_if_requested; echo \"rc=$?\"\n")
    assert "rc=2" in result.stdout
    assert "a script path is required" in result.stderr


def _ow_env(root: pathlib.Path) -> dict[str, str]:
    return {
        "KANBAN_ROOT": str(root),
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
    }


@pytest.mark.parametrize(
    "registry, rc, project, message",
    [
        ("[project:alpha]\npriority=1\n", 0, "alpha", ""),
        (
            "[project:alpha]\npriority=1\n\n[project:beta]\npriority=2\n",
            1,
            "",
            "check-z: ERROR: OVERWATCH_PROJECT is not set and multiple projects are registered.",
        ),
        ("", 1, "", "check-z: ERROR: no project specified and none resolvable from projects.cfg"),
    ],
    ids=["one-project", "several-projects", "none"],
)
def test_overwatch_resolve_check_project(
    tmp_path: pathlib.Path, registry: str, rc: int, project: str, message: str
) -> None:
    root = tmp_path / "kanban"
    (root / "tmp").mkdir(parents=True)
    (root / "projects.cfg").write_text(registry, encoding="utf-8")
    result = run_bash(
        tmp_path,
        "unset OVERWATCH_PROJECT PGAI_PROJECT_NAME\n"
        f"source '{_LIB / 'overwatch_config.sh'}'\n"
        'overwatch_resolve_check_project check-z; echo "rc=$? project=${OVERWATCH_PROJECT:-}"\n',
        extra_env=_ow_env(root),
        timeout=60,
    )
    assert f"rc={rc} project={project}\n" in result.stdout, result.stderr
    if message:
        assert message in result.stderr


def test_overwatch_resolve_check_project_refuses_a_missing_root(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "absent"
    result = run_bash(
        tmp_path,
        f"source '{_LIB / 'overwatch_config.sh'}'\noverwatch_resolve_check_project check-z; echo \"rc=$?\"\n",
        extra_env={**_ow_env(root), "PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path)},
    )
    assert "rc=1" in result.stdout
    assert f"check-z: KANBAN_ROOT does not exist: {root}" in result.stderr


def test_runner_refusals_print_the_shared_text(tmp_path: pathlib.Path) -> None:
    result = run_bash(
        tmp_path,
        f"source '{_LIB / 'gated_runner_lint.sh'}'\n"
        "gated_runner_reject_unknown_arg run-x.sh --bogus 'bash run-x.sh --verbose'\n"
        "gated_runner_report_pytest_missing 'bash run-x.sh' 'the x lane'\n",
    )
    assert "FAIL: unrecognized argument '--bogus'." in result.stderr
    assert '  1. bash "run-x.sh" --help' in result.stderr
    assert "  2. Re-run with the corrected flag, e.g.: bash run-x.sh --verbose" in result.stderr
    assert "FAIL: pytest is not installed in this Python environment." in result.stderr
    assert "  2. bash run-x.sh\n" in result.stderr
    assert "the x lane without it." in result.stderr
    assert result.stdout == ""


def test_runner_flag_parser_sets_the_flags_and_refuses_a_bad_worker_count(tmp_path: pathlib.Path) -> None:
    result = run_bash(
        tmp_path,
        f"source '{_LIB / 'error_contract.sh'}'\nsource '{_LIB / 'gated_runner_lint.sh'}'\n"
        f"source '{_LIB / 'gated_runner_args.sh'}'\n"
        "_help() { echo 'help text'; }\n"
        "gated_runner_parse_args run-x-tests run-x.sh _help -v --workers 2 --files a.py b.py --base main"
        " --quarantined --no-scratch-sweep\n"
        'echo "v=$VERBOSE w=$REQUESTED_WORKERS files=${TARGETED_FILES[*]} base=$PRE_MERGE_BASE'
        ' q=$QUARANTINED s=$NO_SCRATCH_SWEEP c=$COVERAGE r=$REUSE_IF_GREEN"\n'
        "(gated_runner_parse_args run-x-tests run-x.sh _help --coverage --help); echo \"help_rc=$?\"\n"
        "(gated_runner_parse_args run-x-tests run-x.sh _help --workers two); echo \"workers_rc=$?\"\n",
    )
    assert "v=true w=2 files=a.py b.py base=main q=true s=true c=false r=false\n" in result.stdout, result.stderr
    assert "help text\nhelp_rc=0\n" in result.stdout
    assert "workers_rc=1" in result.stdout
    assert "run-x-tests.workers-flag-invalid" in result.stderr
    assert "(got 'two')" in result.stderr
    assert 'bash "run-x.sh" --workers 1' in result.stderr


def _wake_shim(tmp_path: pathlib.Path, provider_line: str) -> pathlib.Path:
    shim = tmp_path / "wake" / "fake.sh"
    shim.parent.mkdir(parents=True, exist_ok=True)
    shim.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        f"{provider_line}"
        'WAKE_ENTRY_PROVIDER_LIB="provider_fake.sh"\nWAKE_ENTRY_API_KEY_VAR="FAKE_API_KEY"\n'
        f"source '{_LIB / 'wake_entry.sh'}'\n",
        encoding="utf-8",
    )
    return shim


def test_wake_entry_body_parses_the_shim_argv(tmp_path: pathlib.Path) -> None:
    shim = _wake_shim(tmp_path, 'WAKE_ENTRY_PROVIDER="fake"\n')
    result = subprocess.run(["bash", str(shim), "--help"], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stderr.startswith("Usage: fake.sh --agent=AGENT"), result.stderr
    result = subprocess.run(["bash", str(shim), "coder", "--bogus"], capture_output=True, text=True, timeout=30)
    assert result.returncode == 1
    assert "WARNING: positional agent argument is deprecated.\n  Use: fake.sh --agent=coder" in result.stderr
    assert "ERROR: unknown option: --bogus" in result.stderr


def test_wake_entry_body_refuses_a_shim_that_names_no_provider(tmp_path: pathlib.Path) -> None:
    shim = _wake_shim(tmp_path, "")
    result = subprocess.run(["bash", str(shim), "--help"], capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    assert "must set WAKE_ENTRY_PROVIDER" in result.stderr


def test_runner_pytest_probe_answers_from_the_environment(tmp_path: pathlib.Path) -> None:
    result = run_bash(
        tmp_path,
        f"source '{_LIB / 'gated_runner_lint.sh'}'\n"
        'gated_runner_pytest_available; echo "here=$?"\n'
        'PATH=/nonexistent gated_runner_pytest_available; echo "gone=$?"\n',
    )
    assert "here=0" in result.stdout
    assert "gone=0" not in result.stdout


def test_verify_harness_counts_and_stays_plain_off_a_terminal(tmp_path: pathlib.Path) -> None:
    result = run_bash(
        tmp_path,
        f"set -euo pipefail\nsource '{_LIB / 'verify_harness.sh'}'\nverify_harness_init\n"
        'pass "one"; pass "two"; fail "three"; warn "four"\n'
        'echo "counts=${PASS_COUNT}/${FAIL_COUNT}"\n',
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "PASS: one\nPASS: two\nFAIL: three\nWARN: four\ncounts=2/1\n"


@pytest.mark.parametrize(
    "init, stdout, stderr",
    [
        (
            "script_log_init demo",
            "[demo] one  two\n[demo] done\n[demo] careful\n",
            "[demo] boom\n[demo] fatal\n",
        ),
        (
            "script_log_init demo --labelled",
            "[demo] one  two\n[demo] done\n[demo] WARNING: careful\n",
            "[demo] ERROR: boom\n[demo] ERROR: fatal\n",
        ),
        ('script_log_init ""', "INFO: one  two\nOK: done\nWARN: careful\n", "ERROR: boom\nERROR: fatal\n"),
    ],
    ids=["tagged", "labelled", "bare"],
)
def test_status_logger_prints_plain_lines_off_a_terminal(
    tmp_path: pathlib.Path, init: str, stdout: str, stderr: str
) -> None:
    result = run_bash(
        tmp_path,
        f"set -euo pipefail\nsource '{_LIB / 'script_common.sh'}'\n{init}\n"
        'info "one  two"; ok "done"; warn "careful"; err "boom"\n'
        '(die "fatal") || echo "die_rc=$?"\n',
    )
    assert result.stdout == stdout + "die_rc=1\n", result.stderr
    assert result.stderr == stderr


def test_status_logger_colours_the_head_when_the_table_is_on(tmp_path: pathlib.Path) -> None:
    result = run_bash(
        tmp_path,
        f"source '{_LIB / 'script_common.sh'}'\nUSE_COLOR=caller\n"
        "script_log_init demo --labelled\nscript_colour_table on\n"
        'info "i"; ok "o"; warn "w"; err "e" 2>&1; log_line "$C_GREEN" "[demo] PASS" " checked"\n'
        'echo "use_color=$USE_COLOR"\nscript_colour_table off; info "plain"\n',
    )
    assert result.stdout == (
        "\x1b[0;34m[demo]\x1b[0m i\n"
        "\x1b[0;32m[demo]\x1b[0m o\n"
        "\x1b[0;33m[demo]\x1b[0m WARNING: w\n"
        "\x1b[0;31m[demo]\x1b[0m ERROR: e\n"
        "\x1b[0;32m[demo] PASS\x1b[0m checked\n"
        "use_color=caller\n"
        "[demo] plain\n"
    ), result.stderr


def test_resolve_script_repo_root_walks_to_the_nearest_git_dir(tmp_path: pathlib.Path) -> None:
    repo = tmp_path / "repo"
    deep = repo / "team" / "scripts" / "dashboard"
    deep.mkdir(parents=True)
    (repo / ".git").mkdir()
    bare = tmp_path / "export" / "team" / "scripts" / "dashboard"
    bare.mkdir(parents=True)
    result = run_bash(
        tmp_path,
        f"source '{_LIB / 'dev_tree.sh'}'\n"
        f"resolve_script_repo_root '{deep}' '{deep}/../../..'\n"
        f"resolve_script_repo_root '{bare}' '{bare}/../../..'\n",
    )
    assert result.stdout == f"{repo}\n{tmp_path / 'export'}\n", result.stderr


def _project_root(tmp_path: pathlib.Path, cfg: str | None) -> pathlib.Path:
    root = tmp_path / "kanban"
    project = root / "projects" / "alpha"
    project.mkdir(parents=True)
    (root / "tmp").mkdir()
    (root / "projects.cfg").write_text("[project:alpha]\npriority = 1\n", encoding="utf-8")
    if cfg is not None:
        (project / "project.cfg").write_text(cfg, encoding="utf-8")
    return root


@pytest.mark.parametrize(
    "cfg, stdout, message",
    [
        (
            "[project]\nproject_name = alpha\ndev_tree_path = /srv/dev/alpha\n",
            "rc=0 [/srv/dev/alpha] [/srv/dev/alpha]\n",
            "",
        ),
        (
            "[project]\nproject_name = alpha\n",
            "rc=1 [] []\n",
            "ERROR: dev_tree_path is not set in project.cfg for project 'alpha'",
        ),
        (None, "rc=1 [] []\n", "ERROR: could not load project.cfg for project 'alpha'"),
    ],
    ids=["set", "unset", "no-project-cfg"],
)
def test_pp_load_project_repo_root(tmp_path: pathlib.Path, cfg, stdout: str, message: str) -> None:
    root = _project_root(tmp_path, cfg)
    result = run_bash(
        tmp_path,
        "unset PGAI_PROJECT_NAME PGAI_TASKS_DIR PGAI_PROJECT_ROOT PGAI_DEV_TREE_PATH PP_dev_tree_path\n"
        f"source '{_LIB / 'ini_parser.sh'}'\nsource '{_LIB / 'project_paths.sh'}'\n"
        "pp_load_project_repo_root alpha; rc=$?\n"
        'echo "rc=${rc} [${PP_REPO_ROOT}] [${PP_dev_tree_path:-}]"\n',
        extra_env=_ow_env(root),
        timeout=60,
    )
    assert result.stdout.endswith(stdout), result.stdout + result.stderr
    if message:
        assert message in result.stderr
