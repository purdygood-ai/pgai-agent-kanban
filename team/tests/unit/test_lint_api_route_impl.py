"""
test_lint_api_route_impl.py — Unit tests for team/scripts/lint_api_route_impl.py.

Most tests build a fixture tree under pytest's ``tmp_path``: a
``team/config/operator_verbs.manifest`` and
``team/config/api-route-impl-baseline.txt``, a fixture router file under
``team/pgai_agent_kanban/api/routers/``, fixture CLI scripts under
``team/scripts/``, and copies of the two library modules the lint imports
(the manifest reader and the error-contract emitter). The lint is pointed at
it with ``--root``, so those tests read nothing from this dev tree's own
manifest, routers or scripts. The last group runs the lint against the real
tree and the real runner wiring.

Coverage:

  1. A module row whose route calls the function (directly, through a
     same-file helper, or through a package re-export) and whose CLI script
     reaches it through pp_run_ops (call form or pp_pythonpath import form)
     is clean.
  2. A module row whose route calls shell_out or subprocess is reported; one
     whose route does not call the function is reported; one whose route
     does not exist is reported.
  3. A module row whose CLI script does not reach the function through
     pp_run_ops is reported; a pp_run_ops call that appears only in a
     comment does not count.
  4. The shell-row ratchet: more shell rows than the baseline is reported;
     fewer asks to tighten; a missing baseline is reported; a malformed impl
     is reported and not counted; --emit-baseline writes a baseline the lint
     then accepts.
  5. The lint exits 0 whatever it finds and always writes its findings file.
  6. Against the real tree the lint reports nothing, its baseline equals
     the measured impl-shell count, no shell row is left to port, and
     wake-now and deep-verify are printed as launchers
     with their reasons; the wiring block both gated runners carry records a
     seeded finding and returns 0.
  7. Documented launchers: a launcher with a reason is printed with it, is
     not reported, and leaves the shell rows to port; a launcher with no
     reason is reported and stays a row to port; a launcher naming an
     unknown verb, a module row, or listed twice is reported; a missing
     launchers file declares none; declaring a launcher never lowers the
     ratchet.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_THIS_FILE = Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent
_LINT_SCRIPT = _TEAM_DIR / "scripts" / "lint_api_route_impl.py"
_GATED_RUNNER_LIB = _TEAM_DIR / "scripts" / "lib" / "gated_runner_lint.sh"
_RUNNERS = (
    _TEAM_DIR / "scripts" / "run-unit-tests.sh",
    _TEAM_DIR / "scripts" / "run-integration-tests.sh",
)
_LIB_COPIES = ("operator_verb_manifest.py", "error_contract.py")
_ROUTER_REL = "team/pgai_agent_kanban/api/routers/operations.py"

_HALT = "module:pgai_agent_kanban.ops.write.halt"
_GET_QUEUES = "module:pgai_agent_kanban.ops.read.get_queues"

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

ROUTER = textwrap.dedent(
    '''\
    import subprocess

    from fastapi import APIRouter

    from ..adapter import shell_out
    from ...ops import halt as reexported_halt
    from ...ops import read as ops_read
    from ...ops.write import halt

    router = APIRouter(prefix="/operations")


    def _delegate(project):
        halt(None, project)


    @router.post("/direct")
    def post_direct(body):
        halt(None, body.project)
        return {}


    @router.post("/via-helper")
    def post_via_helper(body):
        _delegate(body.project)
        return {}


    @router.post("/via-reexport")
    def post_via_reexport(body):
        reexported_halt(None, body.project)
        return {}


    @router.post("/reader")
    def post_reader(body):
        return ops_read.get_queues(None, body.project)


    @router.post("/shells")
    def post_shells(body):
        halt(None, body.project)
        return shell_out(["scripts/halt.sh"])


    @router.post("/subprocess")
    def post_subprocess(body):
        halt(None, body.project)
        subprocess.run(["true"], check=False)
        return {}


    @router.post("/no-call")
    def post_no_call(body):
        return {}
    '''
)

SCRIPT_CALL_FORM = textwrap.dedent(
    """\
    #!/usr/bin/env bash
    source "$(dirname "${BASH_SOURCE[0]}")/lib/pp_run_ops.sh"
    pp_run_ops pgai_agent_kanban.ops halt "${_project_root}"
    """
)

SCRIPT_IMPORT_FORM = textwrap.dedent(
    """\
    #!/usr/bin/env bash
    source "$(dirname "${BASH_SOURCE[0]}")/lib/pp_run_ops.sh"
    PYTHONPATH="$(pp_pythonpath)" python3 - "$1" <<'PY'
    from pgai_agent_kanban.ops.read import (
        get_queues,
    )
    PY
    """
)

SCRIPT_NOT_REACHING = textwrap.dedent(
    """\
    #!/usr/bin/env bash
    # pp_run_ops pgai_agent_kanban.ops halt "${_project_root}"
    python3 -m pgai_agent_kanban.ops halt "${_project_root}"
    """
)


def _row(name: str, route: str, script: str, impl: str) -> str:
    return "\t".join(
        ["V", name, "active", script, "project|help", "yes", "idempotent", "POST", route,
         "NONE", "action", "DualChannelShellEnvelope", impl]
    )


def _make_tree(tmp_path: Path, rows: list[str], baseline: int | None, launchers: str | None = None) -> Path:
    root = tmp_path / "repo"
    team = root / "team"
    (team / "config").mkdir(parents=True)
    (team / "config" / "operator_verbs.manifest").write_text(
        "# fixture manifest\n" + "\n".join(rows) + "\n", encoding="utf-8"
    )
    if baseline is not None:
        (team / "config" / "api-route-impl-baseline.txt").write_text(
            f"# fixture baseline\nshell_rows\t{baseline}\n", encoding="utf-8"
        )
    if launchers is not None:
        (team / "config" / "api-route-impl-launchers.txt").write_text(launchers, encoding="utf-8")
    pkg = team / "pgai_agent_kanban"
    (pkg / "lib").mkdir(parents=True)
    (pkg / "api" / "routers").mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "lib" / "__init__.py").write_text("", encoding="utf-8")
    for name in _LIB_COPIES:
        shutil.copy2(_TEAM_DIR / "pgai_agent_kanban" / "lib" / name, pkg / "lib" / name)
    (root / _ROUTER_REL).write_text(ROUTER, encoding="utf-8")
    scripts = team / "scripts"
    scripts.mkdir()
    (scripts / "call-form.sh").write_text(SCRIPT_CALL_FORM, encoding="utf-8")
    (scripts / "import-form.sh").write_text(SCRIPT_IMPORT_FORM, encoding="utf-8")
    (scripts / "not-reaching.sh").write_text(SCRIPT_NOT_REACHING, encoding="utf-8")
    return root


def _run(root: Path, tmp_path: Path, *extra: str) -> tuple[int, str, list[dict]]:
    """Run the lint against *root*; return (exit code, stdout, findings)."""
    findings_file = tmp_path / "findings.json"
    proc = subprocess.run(
        [sys.executable, str(_LINT_SCRIPT), "--root", str(root), "--findings-file", str(findings_file), *extra],
        capture_output=True,
        text=True,
        timeout=60,
    )
    findings = json.loads(findings_file.read_text(encoding="utf-8"))
    return proc.returncode, proc.stdout, findings


def _kinds(findings: list[dict]) -> list[tuple[str, str]]:
    return sorted((f["kind"], f["verb"]) for f in findings)


# ---------------------------------------------------------------------------
# 1. Clean module rows
# ---------------------------------------------------------------------------


def test_module_rows_sharing_the_function_are_clean(tmp_path):
    rows = [
        _row("direct", "/operations/direct", "scripts/call-form.sh", _HALT),
        _row("via-helper", "/operations/via-helper", "scripts/call-form.sh", _HALT),
        _row("via-reexport", "/operations/via-reexport", "scripts/call-form.sh", _HALT),
        _row("reader", "/operations/reader", "scripts/import-form.sh", _GET_QUEUES),
        _row("shelled", "/operations/shells", "scripts/not-reaching.sh", "shell"),
    ]
    code, out, findings = _run(_make_tree(tmp_path, rows, baseline=1), tmp_path)
    assert code == 0
    assert findings == [], out
    assert "5 verb(s), 4 module row(s), 1 shell row(s), 0 launcher(s) (1 impl shell, baseline 1); 0 finding(s)" in out


# ---------------------------------------------------------------------------
# 2. Route checks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", ["/operations/shells", "/operations/subprocess"])
def test_module_route_starting_a_process_is_reported(tmp_path, route):
    rows = [_row("verb", route, "scripts/call-form.sh", _HALT)]
    code, _out, findings = _run(_make_tree(tmp_path, rows, baseline=0), tmp_path)
    assert code == 0
    assert _kinds(findings) == [("route-shells-out", "verb")]
    assert findings[0]["file"] == _ROUTER_REL
    assert findings[0]["line"] > 0


def test_module_route_not_calling_the_function_is_reported(tmp_path):
    rows = [_row("verb", "/operations/no-call", "scripts/call-form.sh", _HALT)]
    code, out, findings = _run(_make_tree(tmp_path, rows, baseline=0), tmp_path)
    assert code == 0
    assert _kinds(findings) == [("route-not-calling", "verb")]
    assert "does not call pgai_agent_kanban.ops.write.halt" in out


def test_module_route_calling_a_different_function_is_reported(tmp_path):
    rows = [_row("verb", "/operations/reader", "scripts/import-form.sh", _HALT)]
    code, _out, findings = _run(_make_tree(tmp_path, rows, baseline=0), tmp_path)
    assert code == 0
    assert ("route-not-calling", "verb") in _kinds(findings)


def test_module_row_without_a_route_is_reported(tmp_path):
    rows = [_row("verb", "/operations/absent", "scripts/call-form.sh", _HALT)]
    code, _out, findings = _run(_make_tree(tmp_path, rows, baseline=0), tmp_path)
    assert code == 0
    assert _kinds(findings) == [("route-missing", "verb")]


def test_shell_rows_are_not_checked_against_their_route(tmp_path):
    rows = [_row("verb", "/operations/shells", "scripts/not-reaching.sh", "shell")]
    code, _out, findings = _run(_make_tree(tmp_path, rows, baseline=1), tmp_path)
    assert code == 0
    assert findings == []


# ---------------------------------------------------------------------------
# 3. CLI checks
# ---------------------------------------------------------------------------


def test_module_row_whose_script_does_not_reach_the_function_is_reported(tmp_path):
    rows = [_row("verb", "/operations/direct", "scripts/not-reaching.sh", _HALT)]
    code, out, findings = _run(_make_tree(tmp_path, rows, baseline=0), tmp_path)
    assert code == 0
    assert _kinds(findings) == [("cli-not-reaching", "verb")]
    assert findings[0]["file"] == "team/scripts/not-reaching.sh"
    assert "does not reach pgai_agent_kanban.ops.write.halt through pp_run_ops" in out


def test_script_reaching_a_different_function_is_reported(tmp_path):
    rows = [_row("verb", "/operations/reader", "scripts/call-form.sh", _GET_QUEUES)]
    code, _out, findings = _run(_make_tree(tmp_path, rows, baseline=0), tmp_path)
    assert code == 0
    assert _kinds(findings) == [("cli-not-reaching", "verb")]


def test_module_row_whose_script_is_missing_is_reported(tmp_path):
    rows = [_row("verb", "/operations/direct", "scripts/absent.sh", _HALT)]
    code, _out, findings = _run(_make_tree(tmp_path, rows, baseline=0), tmp_path)
    assert code == 0
    assert _kinds(findings) == [("cli-script-missing", "verb")]


# ---------------------------------------------------------------------------
# 4. The shell-row ratchet
# ---------------------------------------------------------------------------


def _shell_rows(count: int) -> list[str]:
    return [_row(f"shell-{i}", "/operations/shells", "scripts/not-reaching.sh", "shell") for i in range(count)]


def test_more_shell_rows_than_the_baseline_is_reported(tmp_path):
    code, out, findings = _run(_make_tree(tmp_path, _shell_rows(3), baseline=2), tmp_path)
    assert code == 0
    assert _kinds(findings) == [("shell-rows-above-baseline", "")]
    assert "3 manifest row(s) are impl shell (3 to port, 0 launcher(s)), above the baseline of 2" in out


def test_fewer_shell_rows_than_the_baseline_asks_to_tighten(tmp_path):
    code, out, findings = _run(_make_tree(tmp_path, _shell_rows(1), baseline=2), tmp_path)
    assert code == 0
    assert _kinds(findings) == [("baseline-tighten", "")]
    assert "lower the baseline to 1" in out


def test_missing_baseline_is_reported(tmp_path):
    code, _out, findings = _run(_make_tree(tmp_path, _shell_rows(1), baseline=None), tmp_path)
    assert code == 0
    assert _kinds(findings) == [("baseline-missing", "")]


def test_malformed_impl_is_reported_and_not_counted(tmp_path):
    rows = _shell_rows(1) + [_row("odd", "/operations/direct", "scripts/call-form.sh", "bash")]
    code, _out, findings = _run(_make_tree(tmp_path, rows, baseline=1), tmp_path)
    assert code == 0
    assert _kinds(findings) == [("impl-malformed", "odd")]


def test_emit_baseline_writes_a_baseline_the_lint_accepts(tmp_path):
    root = _make_tree(tmp_path, _shell_rows(4), baseline=None)
    proc = subprocess.run(
        [sys.executable, str(_LINT_SCRIPT), "--root", str(root), "--emit-baseline"],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0
    assert re.search(r"^shell_rows\t4$", proc.stdout, re.M)
    (root / "team" / "config" / "api-route-impl-baseline.txt").write_text(proc.stdout, encoding="utf-8")
    code, _out, findings = _run(root, tmp_path)
    assert code == 0
    assert findings == []


# ---------------------------------------------------------------------------
# 5. Exit code and findings file
# ---------------------------------------------------------------------------


def test_findings_carry_the_runner_shape_and_the_lint_exits_zero(tmp_path):
    rows = [_row("verb", "/operations/shells", "scripts/not-reaching.sh", _HALT)]
    code, _out, findings = _run(_make_tree(tmp_path, rows, baseline=0), tmp_path)
    assert code == 0
    assert _kinds(findings) == [("cli-not-reaching", "verb"), ("route-shells-out", "verb")]
    for finding in findings:
        assert finding["lint"] == "api_route_impl"
        assert finding["severity"] == "quality"
        assert set(finding) == {"lint", "severity", "kind", "verb", "file", "line", "summary", "remedy"}
        assert finding["remedy"] and "<" not in finding["remedy"]


# ---------------------------------------------------------------------------
# 6. The real tree and the runner wiring
# ---------------------------------------------------------------------------


def test_real_tree_is_clean_and_baseline_matches_the_measured_count(tmp_path):
    findings_file = tmp_path / "findings.json"
    proc = subprocess.run(
        [sys.executable, str(_LINT_SCRIPT), "--findings-file", str(findings_file)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0
    assert json.loads(findings_file.read_text(encoding="utf-8")) == [], proc.stdout
    match = re.search(
        r"(\d+) shell row\(s\), (\d+) launcher\(s\) \((\d+) impl shell, baseline (\d+)\); 0 finding\(s\)",
        proc.stdout,
    )
    assert match, proc.stdout
    to_port, launchers, impl_shell, baseline = (int(g) for g in match.groups())
    assert to_port + launchers == impl_shell == baseline
    assert re.search(r"^lint_api_route_impl: launcher: wake-now -- \S", proc.stdout, re.M), proc.stdout
    # Every impl shell row left is a documented launcher: none remains to port.
    assert to_port == 0, proc.stdout
    assert re.search(r"^lint_api_route_impl: launcher: deep-verify -- \S", proc.stdout, re.M), proc.stdout


_BLOCK_START = 'echo "Running API route implementation lint..."'
_BLOCK_END_RE = re.compile(r"^unset _R[UI]_API_ROUTE_IMPL_FINDINGS\s*$", re.M)


def _runner_block(runner: Path) -> str:
    text = runner.read_text(encoding="utf-8")
    start = text.find(_BLOCK_START)
    assert start != -1, f"{runner.name} does not run the API route implementation lint"
    end = _BLOCK_END_RE.search(text, start)
    assert end is not None, f"{runner.name}: API route implementation lint block has no closing unset"
    return text[start : end.end()]


@pytest.mark.parametrize("runner", _RUNNERS, ids=lambda p: p.name)
def test_runner_wiring_records_a_seeded_finding_and_returns_zero(tmp_path, runner):
    """Run the runner's own wiring block against a seeded fixture tree.

    The fixture's ``team/scripts`` carries a copy of the lint (it self-locates
    its root from its own path) and a manifest whose one module row shells
    out; the block runs from the fixture's ``team`` directory, as the runner
    runs from its own.
    """
    rows = [_row("verb", "/operations/shells", "scripts/call-form.sh", _HALT)]
    root = _make_tree(tmp_path, rows, baseline=0)
    shutil.copy2(_LINT_SCRIPT, root / "team" / "scripts" / "lint_api_route_impl.py")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    out_file = tmp_path / "findings-out.json"
    driver = tmp_path / "driver.sh"
    driver.write_text(
        "set -euo pipefail\n"
        f'source "{_GATED_RUNNER_LIB}"\n'
        + _runner_block(runner)
        + "\n"
        + f'gated_runner_write_findings_file "{out_file}" "{runner.name}"\n'
        + 'echo "block-exit=$?"\n',
        encoding="utf-8",
    )
    env = build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_env={"PGAI_AGENT_KANBAN_TEMP_DIR": str(scratch)},
    )
    proc = subprocess.run(
        ["bash", str(driver)],
        cwd=root / "team",
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "block-exit=0" in proc.stdout
    data = json.loads(out_file.read_text(encoding="utf-8"))
    ours = [f for f in data["findings"] if f.get("lint") == "api_route_impl"]
    assert [(f["kind"], f["verb"]) for f in ours] == [("route-shells-out", "verb")]


# ---------------------------------------------------------------------------
# 7. Documented launchers
# ---------------------------------------------------------------------------


def test_launcher_with_a_reason_is_printed_and_leaves_the_rows_to_port(tmp_path):
    launchers = "# fixture launchers\nshell-0\tStarts a lane and returns.\n"
    code, out, findings = _run(_make_tree(tmp_path, _shell_rows(2), baseline=2, launchers=launchers), tmp_path)
    assert code == 0
    assert findings == [], out
    assert "lint_api_route_impl: launcher: shell-0 -- Starts a lane and returns." in out
    assert "2 verb(s), 0 module row(s), 1 shell row(s), 1 launcher(s) (2 impl shell, baseline 2); 0 finding(s)" in out


def test_launcher_line_separated_by_spaces_is_read(tmp_path):
    launchers = "   shell-0    Starts a lane and returns.   \n"
    code, out, findings = _run(_make_tree(tmp_path, _shell_rows(1), baseline=1, launchers=launchers), tmp_path)
    assert code == 0
    assert findings == [], out
    assert "launcher: shell-0 -- Starts a lane and returns.\n" in out


def test_launcher_without_a_reason_is_reported_and_stays_a_row_to_port(tmp_path):
    launchers = "# fixture launchers\nshell-0\nshell-1\t   \n"
    code, out, findings = _run(_make_tree(tmp_path, _shell_rows(2), baseline=2, launchers=launchers), tmp_path)
    assert code == 0
    assert _kinds(findings) == [("launcher-without-reason", "shell-0"), ("launcher-without-reason", "shell-1")]
    assert [f["line"] for f in findings] == [2, 3]
    assert all(f["file"] == "team/config/api-route-impl-launchers.txt" for f in findings)
    assert "launcher: shell-" not in out
    assert "2 shell row(s), 0 launcher(s) (2 impl shell, baseline 2)" in out


def test_launcher_naming_an_unknown_verb_is_reported(tmp_path):
    launchers = "wake-later\tNo such verb.\n"
    code, _out, findings = _run(_make_tree(tmp_path, _shell_rows(1), baseline=1, launchers=launchers), tmp_path)
    assert code == 0
    assert _kinds(findings) == [("launcher-unknown-verb", "wake-later")]


def test_launcher_naming_a_module_row_is_reported_and_the_row_is_still_checked(tmp_path):
    rows = [_row("direct", "/operations/direct", "scripts/not-reaching.sh", _HALT)]
    launchers = "direct\tClaims to only launch.\n"
    code, _out, findings = _run(_make_tree(tmp_path, rows, baseline=0, launchers=launchers), tmp_path)
    assert code == 0
    assert _kinds(findings) == [("cli-not-reaching", "direct"), ("launcher-not-shell", "direct")]


def test_launcher_listed_twice_is_reported_and_counted_once(tmp_path):
    launchers = "shell-0\tFirst reason.\nshell-0\tSecond reason.\n"
    code, out, findings = _run(_make_tree(tmp_path, _shell_rows(2), baseline=2, launchers=launchers), tmp_path)
    assert code == 0
    assert _kinds(findings) == [("launcher-duplicate", "shell-0")]
    assert "launcher: shell-0 -- First reason." in out
    assert "1 shell row(s), 1 launcher(s) (2 impl shell, baseline 2)" in out


def test_missing_launchers_file_declares_no_launchers(tmp_path):
    code, out, findings = _run(_make_tree(tmp_path, _shell_rows(2), baseline=2, launchers=None), tmp_path)
    assert code == 0
    assert findings == []
    assert "2 shell row(s), 0 launcher(s) (2 impl shell, baseline 2)" in out


def test_declaring_a_launcher_never_lowers_the_ratchet(tmp_path):
    launchers = "shell-0\tStarts a lane and returns.\n"
    code, out, findings = _run(_make_tree(tmp_path, _shell_rows(3), baseline=2, launchers=launchers), tmp_path)
    assert code == 0
    assert _kinds(findings) == [("shell-rows-above-baseline", "")]
    assert "3 manifest row(s) are impl shell (2 to port, 1 launcher(s)), above the baseline of 2" in out


def test_emit_baseline_counts_launchers(tmp_path):
    launchers = "shell-0\tStarts a lane and returns.\n"
    root = _make_tree(tmp_path, _shell_rows(3), baseline=None, launchers=launchers)
    proc = subprocess.run(
        [sys.executable, str(_LINT_SCRIPT), "--root", str(root), "--emit-baseline"],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0
    assert re.search(r"^shell_rows\t3$", proc.stdout, re.M)
