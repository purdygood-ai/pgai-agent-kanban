r"""
test_dashboard_scripts_help.py
==============================
Every top-level operator-facing script in team/scripts/dashboard/ answers
``-h`` and ``--help`` by printing its usage and exiting 0 -- without
resolving a kanban root, starting a follow-mode log pipeline, querying a
terminal or tmux, or reading the flag as a positional value (a session
name, a kanban root).

The scripts are discovered from the directory itself, never from a
hardcoded list, so a new script dropped into team/scripts/dashboard/ is
covered the moment it lands and fails here if it ignores ``--help``.
Scripts under team/scripts/dashboard/lib/ are sourced helpers, not
operator entry points, and are out of scope.

What "answers --help" means here
--------------------------------
For each script and each of the two flag spellings:

  * the process exits 0 within a short per-script timeout -- a script
    that starts its main loop instead (a ``tail -F`` stream, a refresh
    loop) is killed at the timeout and reported;
  * stdout is non-empty; and
  * stdout names the script's own file -- every dashboard script's usage
    text is its header comment, which names the script. A pane renderer
    that ignored the flag and drew its pane instead (exit 0, non-empty
    output) does not name itself, so this is what tells usage text apart
    from a normal render.

What "only its usage header" means here
---------------------------------------
Most dashboard scripts render ``--help`` from their own ``#`` comment
lines, keeping the first N of them. N must end at the last line of the
usage header; a count that runs past it prints the implementation
comments that follow the header in the script body. In every such script
the first comment below the header is a ``# shellcheck source=...``
directive, so ``--help`` output that carries a line beginning
``shellcheck `` is the signature of a slice widened past its header.

The opposite direction is pinned too: a count smaller than the header
cuts ``--help`` off partway through, often mid-sentence. For every script
whose help arm renders that slice from ``grep '^#' "$0"``, ``--help`` must
print at least as many lines as the leading ``#`` header block (shebang
excluded) and must reach the header's last line. Together the two checks
hold the slice to exactly the header.

Isolation
---------
Each probe runs inside a throwaway kanban root under pytest's ``tmp_path``
(which the suite conftest anchors under the framework temp root). All five
inherited root variables are set or cleared together, as
docs/test-authoring-guidelines.md ("Live-install safety") requires:
``PGAI_AGENT_KANBAN_ROOT_PATH``, ``KANBAN_ROOT`` and
``PGAI_AGENT_KANBAN_TEMP_DIR`` point into the throwaway root;
``PGAI_TASKS_DIR`` and ``PGAI_PROJECT_ROOT`` are removed, along with the
ambient project-identity variables the shared dashboard fixture clears.
``TMUX`` is unset and ``TMUX_TMPDIR`` points at an empty directory inside
the throwaway root, so a script that wrongly reached for tmux would find
no server rather than the operator's dashboard.

Probes run one after another (no parallel fan-out) through the shared
``run_dashboard_script`` helper, which starts each probe in its own
process group and kills the whole group on timeout, so a follow-mode
pipeline can never outlive the test.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

import pytest

from tests.fixtures.dashboard_render_fixture import (
    clean_subprocess_env,
    run_dashboard_script,
)

# ---------------------------------------------------------------------------
# Path resolution -- anchored to this file's location, matching the other
# dashboard --help test modules.
# ---------------------------------------------------------------------------
_THIS_FILE = pathlib.Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/tests/unit/ -> team/
_DASHBOARD_DIR = _TEAM_DIR / "scripts" / "dashboard"

# Seconds each probe may run. A --help branch returns in well under a
# second; the margin only absorbs a slow, loaded host. A script that does
# not return inside it is treated as having started its main work.
_PER_SCRIPT_TIMEOUT_S = 15

_HELP_FLAGS = ("--help", "-h")


def _discover_top_level_dashboard_scripts() -> list[pathlib.Path]:
    """Every *.sh directly inside team/scripts/dashboard/ (lib/ excluded:
    ``glob`` does not descend into subdirectories)."""
    return sorted(p for p in _DASHBOARD_DIR.glob("*.sh") if p.is_file())


_DASHBOARD_SCRIPTS = _discover_top_level_dashboard_scripts()

# A help arm that renders a fixed-count slice of the script's own '#'
# lines: ``mapfile -t __help_lines < <(grep '^#' "$0" ...)`` followed by
# ``printf '%s\n' "${__help_lines[@]:0:N}"``. Scripts with any other
# renderer (create.sh's awk leading-block printer, for one) end at their
# header by construction and are not selected. Whitespace is matched
# loosely so a reformatted arm is still recognised.
_GREP_RENDERED_HELP_RE = re.compile(
    r"mapfile\s+-t\s+__help_lines\s*<\s*<\(\s*grep\s+'\^#'\s+\"\$0\""
)
_HELP_SLICE_RE = re.compile(r"__help_lines\[@\]:0:\d+")


def _discover_slice_rendered_help_scripts() -> list[pathlib.Path]:
    """Dashboard scripts whose ``--help`` prints a fixed-count slice of the
    ``#`` lines that ``grep '^#' "$0"`` collects."""
    selected = []
    for script in _DASHBOARD_SCRIPTS:
        source = script.read_text(encoding="utf-8")
        if _GREP_RENDERED_HELP_RE.search(source) and _HELP_SLICE_RE.search(source):
            selected.append(script)
    return selected


_SLICE_RENDERED_HELP_SCRIPTS = _discover_slice_rendered_help_scripts()


def _leading_header_lines(script: pathlib.Path) -> list[str]:
    """The script's leading ``#`` comment block as ``--help`` renders it.

    Skips a first-line shebang, then takes consecutive lines starting with
    ``#`` up to the first line that does not, stripping ``#`` and at most
    one following space -- the same transform as the help arm's
    ``sed 's/^# \\{0,1\\}//'``.
    """
    lines = script.read_text(encoding="utf-8").splitlines()
    if lines and lines[0].startswith("#!"):
        lines = lines[1:]
    header = []
    for line in lines:
        if not line.startswith("#"):
            break
        header.append(re.sub(r"^# ?", "", line))
    return header


def _throwaway_root_env(root: pathlib.Path) -> dict[str, str]:
    """Subprocess environment confined to the throwaway kanban root *root*.

    Starts from the shared dashboard fixture's ambient-leak scrub (which
    also sets PGAI_AGENT_KANBAN_ROOT_PATH, NO_COLOR and TERM=dumb), then
    applies the rest of the five-variable sandbox rule and the tmux
    isolation described in the module docstring.
    """
    tmp_dir = root / "tmp"
    tmux_dir = root / "tmux"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmux_dir.mkdir(parents=True, exist_ok=True)

    env = clean_subprocess_env(kanban_root=root)
    env["KANBAN_ROOT"] = str(root)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(root)
    env["PGAI_AGENT_KANBAN_TEMP_DIR"] = str(tmp_dir)
    env.pop("PGAI_TASKS_DIR", None)
    env.pop("PGAI_PROJECT_ROOT", None)
    env.pop("TMUX", None)
    env.pop("TMUX_PANE", None)
    env["TMUX_TMPDIR"] = str(tmux_dir)
    return env


def test_discovery_finds_the_dashboard_entry_points() -> None:
    """The parametrized check below is only meaningful if discovery found
    the scripts. Guards against a path change that leaves it vacuously
    green, and confirms lib/ helpers are not swept in."""
    names = {p.name for p in _DASHBOARD_SCRIPTS}
    assert len(_DASHBOARD_SCRIPTS) >= 40, (
        f"expected at least 40 top-level scripts in {_DASHBOARD_DIR}, found "
        f"{len(_DASHBOARD_SCRIPTS)}: {sorted(names)}"
    )
    for expected in ("create.sh", "logs.sh", "debug-logs.sh", "verify-window0-geometry.sh"):
        assert expected in names, f"discovery missed {expected} in {_DASHBOARD_DIR}"
    assert all(p.parent == _DASHBOARD_DIR for p in _DASHBOARD_SCRIPTS), (
        "discovery must cover only top-level dashboard scripts, not lib/ helpers"
    )


@pytest.mark.parametrize("flag", _HELP_FLAGS)
@pytest.mark.parametrize("script", _DASHBOARD_SCRIPTS, ids=lambda p: p.name)
def test_dashboard_script_prints_usage_and_exits_zero_on_help_flag(
    script: pathlib.Path, flag: str, tmp_path: pathlib.Path
) -> None:
    """``<script> <flag>`` exits 0 promptly and prints usage text naming the
    script, inside a throwaway root with no reachable tmux server."""
    env = _throwaway_root_env(tmp_path / "kanban-root")
    remedy = (
        f"give {script.name} an `-h|--help)` arm that prints its header usage "
        f"and exits 0 before it sources env_bootstrap.sh or reads any argument "
        f"(see logs.sh for the pattern)"
    )

    try:
        result = run_dashboard_script(
            ["bash", str(script), flag], env=env, timeout=_PER_SCRIPT_TIMEOUT_S
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            f"{script.name} {flag} did not exit within {_PER_SCRIPT_TIMEOUT_S}s -- "
            f"it started its main work instead of printing usage. Fix: {remedy}.",
            pytrace=False,
        )

    diagnostic = f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    assert result.returncode == 0, (
        f"{script.name} {flag} exited {result.returncode}, expected 0. "
        f"Fix: {remedy}.\n{diagnostic}"
    )
    assert result.stdout.strip(), (
        f"{script.name} {flag} exited 0 but printed nothing on stdout. "
        f"Fix: {remedy}.\n{diagnostic}"
    )
    assert script.name in result.stdout, (
        f"{script.name} {flag} printed output that does not name the script, "
        f"so it is not its usage text (a pane render, or another script's "
        f"help). Fix: {remedy}.\n{diagnostic}"
    )


@pytest.mark.parametrize("script", _DASHBOARD_SCRIPTS, ids=lambda p: p.name)
def test_dashboard_script_help_stops_at_its_usage_header(
    script: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """``<script> --help`` prints no ``shellcheck `` directive line -- the
    first comment below every self-rendered usage header -- so its help
    text ends at the header rather than spilling into body comments."""
    env = _throwaway_root_env(tmp_path / "kanban-root")
    try:
        result = run_dashboard_script(
            ["bash", str(script), "--help"], env=env, timeout=_PER_SCRIPT_TIMEOUT_S
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            f"{script.name} --help did not exit within {_PER_SCRIPT_TIMEOUT_S}s; "
            f"see test_dashboard_script_prints_usage_and_exits_zero_on_help_flag.",
            pytrace=False,
        )

    leaked = [line for line in result.stdout.splitlines() if line.startswith("shellcheck ")]
    assert not leaked, (
        f"{script.name} --help prints implementation comments from below its "
        f"usage header: {leaked}. Fix: in the `-h|--help)` arm, set the slice "
        f"count in `${{__help_lines[@]:0:N}}` to the number of '#' lines in the "
        f"script's leading header block (shebang excluded), so the slice ends "
        f"at the header's last line.\n--- stdout ---\n{result.stdout}"
    )


def test_slice_rendered_help_discovery_is_not_vacuous() -> None:
    """The header-length check below is only meaningful if the selection
    found the slice-rendered scripts. Guards against a regex drift that
    leaves it vacuously green, and confirms create.sh (its own awk
    renderer) is not swept in."""
    names = {p.name for p in _SLICE_RENDERED_HELP_SCRIPTS}
    assert len(names) >= 30, (
        f"expected at least 30 dashboard scripts rendering --help as a "
        f"`${{__help_lines[@]:0:N}}` slice of `grep '^#' \"$0\"`, found "
        f"{len(names)}: {sorted(names)}. Fix: update _GREP_RENDERED_HELP_RE "
        f"in {_THIS_FILE.name} to match the help arms' current mapfile line."
    )
    assert "logs.sh" in names, f"selection missed logs.sh: {sorted(names)}"
    assert "create.sh" not in names, (
        "create.sh renders --help with its own awk leading-block printer and "
        "must not be held to the slice-count check"
    )


@pytest.mark.parametrize("script", _SLICE_RENDERED_HELP_SCRIPTS, ids=lambda p: p.name)
def test_dashboard_script_help_reaches_the_end_of_its_usage_header(
    script: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """``<script> --help`` prints at least as many lines as the script's
    leading ``#`` header block and reaches its last line, so a slice count
    smaller than the header cannot cut the usage text off partway."""
    header = _leading_header_lines(script)
    assert header, f"{script.name} has no leading '#' header block to render"

    env = _throwaway_root_env(tmp_path / "kanban-root")
    try:
        result = run_dashboard_script(
            ["bash", str(script), "--help"], env=env, timeout=_PER_SCRIPT_TIMEOUT_S
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            f"{script.name} --help did not exit within {_PER_SCRIPT_TIMEOUT_S}s; "
            f"see test_dashboard_script_prints_usage_and_exits_zero_on_help_flag.",
            pytrace=False,
        )

    printed = result.stdout.splitlines()
    fix = (
        f"Fix: in {script.name}'s `-h|--help)` arm, set the slice count in "
        f"`${{__help_lines[@]:0:N}}` to {len(header)}, the number of '#' lines "
        f"in its leading header block (shebang excluded)."
    )
    assert len(printed) >= len(header), (
        f"{script.name} --help printed {len(printed)} lines but its usage "
        f"header has {len(header)}; the help stops at "
        f"{printed[-1] if printed else '(nothing)'!r} and drops "
        f"{header[len(printed):]!r}. {fix}\n--- stdout ---\n{result.stdout}"
    )
    assert printed[len(header) - 1] == header[-1], (
        f"{script.name} --help line {len(header)} is "
        f"{printed[len(header) - 1]!r}, expected the header's last line "
        f"{header[-1]!r}. {fix}\n--- stdout ---\n{result.stdout}"
    )


if __name__ == "__main__":  # pragma: no cover - manual debug entry point
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", "-v", str(_THIS_FILE)]))
