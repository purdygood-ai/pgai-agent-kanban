"""
test_dashboard_pane_refresh_cadence.py
========================================
Tests for team/scripts/dashboard/lint_pane_refresh_cadence.py — the census
check that fails when a dashboard pane's first command (the ones
team/scripts/dashboard/create.sh dispatches through
`dashboard_send_armed_keys`) is neither loop-wrapped (routed through
cadence-loop.sh) nor explicitly registered as one-shot-static/
continuously-self-refreshing in create.sh's CENSUS-EXEMPT-PANES data
block.

Background: window 4 (the "schedule" window)'s five per-agent columns
once carried the trap/armed-marker preamble dashboard_send_armed_keys
installs (so the readiness handshake never noticed anything wrong), but
their render was never wrapped in the refresh loop every other renderer
column in this file uses — create.sh typed `cron-tail.sh <agent>` once at
pane creation, and once cron-tail.sh's own one-shot "no log yet" banner or
a single tail snapshot returned, the pane's shell fell through to an
interactive prompt underneath. The fix (routing every schedule pane
through cadence-loop.sh) survives a later redesign that replaced those
five raw per-agent columns with three purpose-built bands (roster,
status, event — see team/config/dashboard_windows.census); this module
locks in that every one of the three current band panes is still
loop-wrapped, and the regression check itself: a seeded pane whose
command reverts to a bare renderer invocation must fail the lint, and the
shipped tree must pass it.

Test coverage:
  1. The real, checked-in create.sh produces zero violations.
  2. A seeded bare-renderer pane (schedule.0 reverted to an unwrapped
     form) is caught by name, and reverting the seed clears it again.
  3. Every one of window 4's two band panes is loop-wrapped in the
     shipped tree (the direct, non-lint-mediated form of the acceptance
     criterion).
  4. A pane target that legitimately skips the wrap (a continuously
     self-refreshing `exec tail -F` script, or a genuinely static/
     interactive pane) is accepted when registered in the
     CENSUS-EXEMPT-PANES block, and rejected when that registration is
     removed.
  5. A create.sh with no CENSUS-EXEMPT-PANES markers at all is a parse
     failure (exit 2), distinct from zero violations (exit 0).
  6. main()'s exit codes and --help integration.

Test naming (roles/TESTER.md anti-pattern 6): names describe behavior,
never bug IDs, scaffolding labels, or version strings.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Load lint_pane_refresh_cadence.py as a module (mirrors sibling
# test_lint_dashboard_hardcode_colors.py's import-by-path pattern).
# Path: team/tests/unit/ -> team/ (two levels up)
# ---------------------------------------------------------------------------
_THIS_FILE = Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/
_LINT_SCRIPT = _TEAM_DIR / "scripts" / "dashboard" / "lint_pane_refresh_cadence.py"
_REAL_CREATE_SH = _TEAM_DIR / "scripts" / "dashboard" / "create.sh"


def _import_lint_module():
    spec = importlib.util.spec_from_file_location(
        "lint_pane_refresh_cadence", _LINT_SCRIPT
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load spec from {_LINT_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_lint = _import_lint_module()


# ---------------------------------------------------------------------------
# 1 -- the checked-in create.sh is clean
# ---------------------------------------------------------------------------


def test_checked_in_create_sh_has_no_pane_refresh_violations() -> None:
    text = _REAL_CREATE_SH.read_text(encoding="utf-8")
    violations = _lint.find_violations(text)
    assert violations == [], (
        f"the shipped create.sh has {len(violations)} pane(s) that are "
        f"neither cadence-loop.sh-wrapped nor registered one-shot-static: "
        f"{violations}"
    )


def test_check_mode_passes_against_checked_in_tree() -> None:
    result = subprocess.run(
        [sys.executable, str(_LINT_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"lint_pane_refresh_cadence.py failed against the checked-in tree:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "OK" in result.stdout


# ---------------------------------------------------------------------------
# 2 -- a seeded bare-command pane is caught, and clears on revert
# ---------------------------------------------------------------------------

_WRAPPED_STATUS_ASSIGNMENT = (
    'SCHED_STATUS_CMD="${SCRIPT_DIR}/cadence-loop.sh --interval '
    '${REFRESH_INTERVAL} -- ${SCRIPT_DIR}/status-band.sh --kanban-root '
    '${KANBAN_ROOT}"'
)
_BARE_STATUS_ASSIGNMENT = (
    'SCHED_STATUS_CMD="${SCRIPT_DIR}/status-band.sh --kanban-root ${KANBAN_ROOT}"'
)


def test_seeded_bare_renderer_pane_is_caught_and_clears_on_revert(
    tmp_path: Path,
) -> None:
    original_text = _REAL_CREATE_SH.read_text(encoding="utf-8")
    assert _WRAPPED_STATUS_ASSIGNMENT in original_text, (
        "fixture literal not found in the shipped create.sh -- update this "
        "test's literal to match the current SCHED_STATUS_CMD assignment"
    )

    # Clean copy: zero violations.
    clean_copy = tmp_path / "create.sh"
    clean_copy.write_text(original_text, encoding="utf-8")
    assert _lint.find_violations(clean_copy.read_text(encoding="utf-8")) == []

    # Seed the defect shape this lint guards: schedule.0's command reverts to
    # a bare status-band.sh invocation, outside the cadence-loop.sh wrapper,
    # so the window-4 agent pane renders once and never refreshes.
    bad_text = original_text.replace(_WRAPPED_STATUS_ASSIGNMENT, _BARE_STATUS_ASSIGNMENT)
    assert bad_text != original_text
    bad_copy = tmp_path / "create-bad.sh"
    bad_copy.write_text(bad_text, encoding="utf-8")

    violations = _lint.find_violations(bad_copy.read_text(encoding="utf-8"))
    assert violations == [
        ("schedule.0", "${SCRIPT_DIR}/status-band.sh --kanban-root ${KANBAN_ROOT}")
    ], f"expected exactly the seeded schedule.0 violation, got: {violations}"

    # main() surfaces the same finding as a non-zero exit with the pane
    # named on stderr, and the dual-channel contract present.
    result = subprocess.run(
        [sys.executable, str(_LINT_SCRIPT), "--create-sh", str(bad_copy)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 1
    assert "schedule.0" in result.stderr
    assert "error:" in result.stderr
    assert "message:" in result.stderr
    assert "remedy:" in result.stderr

    # Revert: clean again.
    reverted_violations = _lint.find_violations(original_text)
    assert reverted_violations == []


# ---------------------------------------------------------------------------
# 3 -- every window-4 band pane is loop-wrapped in the shipped tree
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pane_target,cmd_var",
    [
        ("schedule.0", "SCHED_STATUS_CMD"),
        ("schedule.1", "SCHED_EVENT_CMD"),
    ],
)
def test_every_schedule_band_pane_is_loop_wrapped(pane_target: str, cmd_var: str) -> None:
    text = _REAL_CREATE_SH.read_text(encoding="utf-8")
    send_calls = dict(_lint._SEND_KEYS_RE.findall(text))
    assert pane_target in send_calls, (
        f"no dashboard_send_armed_keys call found for {pane_target!r}"
    )
    assert send_calls[pane_target] == f"${{{cmd_var}}}", (
        f"{pane_target} is no longer dispatched via ${{{cmd_var}}} -- update "
        f"this test's literal if the variable was intentionally renamed"
    )
    assignments = dict(_lint._ASSIGNMENT_RE.findall(text))
    assert cmd_var in assignments, f"no assignment found for {cmd_var}"
    assert "cadence-loop.sh" in assignments[cmd_var], (
        f"{cmd_var} does not route through cadence-loop.sh: {assignments[cmd_var]!r} "
        f"-- window 4's band panes must refresh on the same cadence every "
        f"other renderer column in create.sh uses"
    )


def test_schedule_band_panes_keep_their_own_renderer() -> None:
    """The refresh wrap must not change WHAT a schedule pane renders -- each
    band's own renderer script remains the command inside the
    cadence-loop.sh wrap."""
    text = _REAL_CREATE_SH.read_text(encoding="utf-8")
    assignments = dict(_lint._ASSIGNMENT_RE.findall(text))
    for cmd_var, renderer in (
        ("SCHED_STATUS_CMD", "status-band.sh"),
        ("SCHED_EVENT_CMD", "event-band.sh"),
    ):
        assert renderer in assignments[cmd_var], (
            f"{cmd_var} no longer invokes {renderer}: {assignments[cmd_var]!r}"
        )


# ---------------------------------------------------------------------------
# 4 -- CENSUS-EXEMPT-PANES registration accepts and un-registration rejects
# ---------------------------------------------------------------------------


def test_registered_interactive_shell_pane_is_accepted() -> None:
    text = _REAL_CREATE_SH.read_text(encoding="utf-8")
    allowlist = _lint._parse_allowlist(text)
    # terminal.1 is an interactive login shell -- never cadence-loop.sh
    # wrapped, by design, and must be present in the registry for the real
    # tree to pass at all (test 1 above already proves the tree passes;
    # this pins down WHY terminal.1 specifically is exempt).
    assert "terminal.1" in allowlist
    violations = _lint.find_violations(text)
    assert not any(pane == "terminal.1" for pane, _ in violations)


def test_log_stream_panes_run_under_cadence_loop_stream_not_the_registry() -> None:
    """The panes that follow their logs (logs.sh, debug-logs.sh) run under
    cadence-loop.sh --stream, so they pause while hidden like every other
    rendering pane, and are not registered as exempt."""
    text = _REAL_CREATE_SH.read_text(encoding="utf-8")
    allowlist = _lint._parse_allowlist(text)
    assignments = dict(_lint._ASSIGNMENT_RE.findall(text))
    for cmd_var in ("LOGS_PANE_CMD", "LOGS_WIN_CMD", "_DRILL_LOGS_CMD"):
        assert "cadence-loop.sh --stream" in assignments[cmd_var], assignments[cmd_var]
    assert "debug-logs.sh" in text and "cadence-loop.sh --stream --interval ${REFRESH_INTERVAL} -- env PGAI_VERBOSE_MODE" in text
    for pane in ("main.1", "logs", "debug-logs.0", "terminal.0", "${_DRILL_WIN}.1"):
        assert pane not in allowlist, f"{pane} is a stream pane and must not be registered exempt"


def test_removing_registration_makes_the_pane_a_violation(tmp_path: Path) -> None:
    original_text = _REAL_CREATE_SH.read_text(encoding="utf-8")
    # Strip exactly the "terminal.1" comment line out of the
    # CENSUS-EXEMPT-PANES block, leaving every other exemption (and the
    # marker pair itself) intact.
    lines = original_text.splitlines(keepends=True)
    filtered = [
        line for line in lines if line.strip() not in ("#   terminal.1",)
    ]
    assert len(filtered) == len(lines) - 1, (
        "expected to remove exactly one line ('#   terminal.1') -- update this "
        "test's literal if the registry's formatting changed"
    )
    mutated_text = "".join(filtered)

    violations = _lint.find_violations(mutated_text)
    assert any(pane == "terminal.1" for pane, _ in violations), (
        "removing terminal.1's CENSUS-EXEMPT-PANES registration should make it "
        "a violation (it is not cadence-loop.sh-wrapped)"
    )


# ---------------------------------------------------------------------------
# 5 -- missing marker pair is a parse failure, not zero violations
# ---------------------------------------------------------------------------


def test_missing_allowlist_markers_raises_parse_error() -> None:
    text = _REAL_CREATE_SH.read_text(encoding="utf-8")
    stripped = text.replace(_lint._ALLOWLIST_BEGIN, "").replace(_lint._ALLOWLIST_END, "")
    assert stripped != text
    with pytest.raises(_lint.PaneRefreshLintError):
        _lint.find_violations(stripped)


def test_missing_allowlist_markers_exits_2_via_main(tmp_path: Path) -> None:
    text = _REAL_CREATE_SH.read_text(encoding="utf-8")
    stripped = text.replace(_lint._ALLOWLIST_BEGIN, "").replace(_lint._ALLOWLIST_END, "")
    broken = tmp_path / "create-no-registry.sh"
    broken.write_text(stripped, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(_LINT_SCRIPT), "--create-sh", str(broken)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 2, (
        f"expected a parse-failure exit (2), got {result.returncode}:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_no_send_keys_call_sites_raises_parse_error() -> None:
    with pytest.raises(_lint.PaneRefreshLintError):
        _lint.find_violations("# not a real create.sh\n")


# ---------------------------------------------------------------------------
# 6 -- main() exit codes and --help
# ---------------------------------------------------------------------------


def test_main_exits_2_when_create_sh_path_does_not_exist(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(_LINT_SCRIPT), "--create-sh", str(tmp_path / "nope.sh")],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 2
    assert "cannot read" in result.stderr


def test_help_exits_zero() -> None:
    result = subprocess.run(
        [sys.executable, str(_LINT_SCRIPT), "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert "create-sh" in result.stdout


# ---------------------------------------------------------------------------
# Census declarations: every pane pauses when hidden, or is the one
# always-on status surface in window 0
# ---------------------------------------------------------------------------

_REAL_CENSUS = _TEAM_DIR / "config" / "dashboard_windows.census"


def _census_variant(tmp_path: Path, edit) -> Path:
    lines = _REAL_CENSUS.read_text(encoding="utf-8").splitlines()
    path = tmp_path / "dashboard_windows.census"
    path.write_text("\n".join(edit(lines)) + "\n", encoding="utf-8")
    return path


def _run_with_census(census: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_LINT_SCRIPT), "--census", str(census)],
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_checked_in_census_declares_every_pane_paused_when_hidden() -> None:
    from pgai_agent_kanban.dashboard.window_census import load_census, load_pane_cadence

    cadences = load_pane_cadence(_REAL_CENSUS)
    panes = [(p.tmux_index, p.variant, p.pane_seq) for w in load_census(_REAL_CENSUS) for p in w.panes]
    assert sorted(cadences) == sorted(panes)
    assert {c.when_hidden for c in cadences.values()} == {"pause"}


def test_pane_without_a_declaration_is_reported(tmp_path: Path) -> None:
    def drop_first_c(lines: list[str]) -> list[str]:
        index = next(i for i, line in enumerate(lines) if line.startswith("C\t"))
        return lines[:index] + lines[index + 1:]

    result = _run_with_census(_census_variant(tmp_path, drop_first_c))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "window main (index 0, variant -) pane 0 has no C record" in result.stderr
    assert "error:   dashboard.pane-refresh-cadence-violation" in result.stderr


def test_always_on_outside_window_zero_is_reported(tmp_path: Path) -> None:
    def always_on_attention(lines: list[str]) -> list[str]:
        return [line.replace("C\t3\t-\t0\trefresh\tpause", "C\t3\t-\t0\trefresh\talways-on") for line in lines]

    result = _run_with_census(_census_variant(tmp_path, always_on_attention))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "window attention (index 3, variant -) pane 0 declares always-on" in result.stderr


def test_one_always_on_status_surface_in_window_zero_is_accepted(tmp_path: Path) -> None:
    def always_on_header(lines: list[str]) -> list[str]:
        return [line.replace("C\t0\t-\t0\trefresh\tpause", "C\t0\t-\t0\trefresh\talways-on") for line in lines]

    result = _run_with_census(_census_variant(tmp_path, always_on_header))
    assert result.returncode == 0, result.stdout + result.stderr


def test_unknown_declaration_and_cadence_are_reported(tmp_path: Path) -> None:
    def bad_values(lines: list[str]) -> list[str]:
        return [
            line.replace("C\t5\t-\t0\t10\tpause", "C\t5\t-\t0\tsometimes\tslow")
            for line in lines
        ]

    result = _run_with_census(_census_variant(tmp_path, bad_values))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "declares cadence 'sometimes'" in result.stderr
    assert "declares when_hidden 'slow'" in result.stderr
