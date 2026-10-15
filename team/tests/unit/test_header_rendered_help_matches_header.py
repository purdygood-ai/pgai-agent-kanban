r"""
test_header_rendered_help_matches_header.py
===========================================
Every script under team/scripts/ that renders ``--help`` from its own
header -- every caller of ``header_help_print`` from
team/scripts/lib/header_help.sh -- prints exactly its leading comment
block and exits 0.

The scripts are discovered by reading every ``*.sh`` under team/scripts/
for a ``header_help_print`` call on a non-comment line, never from a
hand list, so a script that adopts the helper is covered the moment it
lands. Files under any ``lib/`` directory are sourced helpers, not entry
points (lib/header_help.sh itself names the function only in comments),
and are out of scope.

The expected text is computed here, independently of the helper, from
the helper's documented contract: the lines after a leading ``#!``
shebang, up to the first line that does not start with ``#``; each loses
its ``#`` and at most one following space; ``# shellcheck ...`` directive
lines are linter instructions and are left out. Equality is exact, so a
header edit can neither cut the help short nor let it run into body
comments.

Isolation
---------
Each probe runs from a working directory outside the repository, inside
a throwaway kanban root under pytest's ``tmp_path``. All five inherited
root variables are set or cleared together (docs/test-authoring-
guidelines.md, "Live-install safety"); ``TMUX`` is unset and
``TMUX_TMPDIR`` points into the throwaway root. Probes run one at a time
through ``run_dashboard_script``, which owns the probe's process group.
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

_THIS_FILE = pathlib.Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/tests/unit/ -> team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"

# A --help arm returns in well under a second; the margin absorbs a slow,
# loaded host.
_PER_SCRIPT_TIMEOUT_S = 15

# A call on a line whose first non-blank character is not '#'.
_CALL_RE = re.compile(r"^(?![ \t]*#)[^\n]*\bheader_help_print\b", re.MULTILINE)
_SHELLCHECK_DIRECTIVE_RE = re.compile(r"^#\s*shellcheck\s")


def _discover_header_help_scripts() -> list[pathlib.Path]:
    """Every ``*.sh`` under team/scripts/, outside ``lib/`` directories,
    that calls ``header_help_print`` from code."""
    found = []
    for path in sorted(_SCRIPTS_DIR.rglob("*.sh")):
        if not path.is_file():
            continue
        if "lib" in path.relative_to(_SCRIPTS_DIR).parts[:-1]:
            continue
        if _CALL_RE.search(path.read_text(encoding="utf-8")):
            found.append(path)
    return found


_HEADER_HELP_SCRIPTS = _discover_header_help_scripts()


def _expected_help(script: pathlib.Path) -> str:
    """The script's leading comment block, rendered as --help prints it."""
    lines = script.read_text(encoding="utf-8").splitlines()
    if lines and lines[0].startswith("#!"):
        lines = lines[1:]
    block = []
    for line in lines:
        if not line.startswith("#"):
            break
        if _SHELLCHECK_DIRECTIVE_RE.match(line):
            continue
        block.append(re.sub(r"^# ?", "", line))
    return "".join(f"{line}\n" for line in block)


def _throwaway_root_env(root: pathlib.Path) -> dict[str, str]:
    """Subprocess environment confined to the throwaway kanban root *root*."""
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


def _relative(script: pathlib.Path) -> str:
    return str(script.relative_to(_SCRIPTS_DIR))


def test_header_help_discovery_is_not_vacuous() -> None:
    """The equality check below is only meaningful if discovery found the
    header-rendered scripts, in more than one directory, and left the
    helper library itself out."""
    names = {_relative(p) for p in _HEADER_HELP_SCRIPTS}
    assert len(names) >= 40, (
        f"expected at least 40 scripts under {_SCRIPTS_DIR} calling "
        f"header_help_print, found {len(names)}: {sorted(names)}. Fix: check "
        f"_CALL_RE in {_THIS_FILE.name} against the scripts' --help arms."
    )
    for expected in (
        "dashboard/create.sh",
        "dashboard/logs.sh",
        "kanban-status.sh",
        "verify-visibility-pane-widths.sh",
    ):
        assert expected in names, f"discovery missed {expected}: {sorted(names)}"
    assert not any("lib/" in name for name in names), (
        f"discovery swept in a sourced lib/ helper: {sorted(names)}"
    )


@pytest.mark.parametrize("script", _HEADER_HELP_SCRIPTS, ids=_relative)
def test_help_output_equals_leading_comment_block(
    script: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """``<script> --help`` exits 0 and its stdout is exactly the script's
    leading comment block."""
    expected = _expected_help(script)
    assert expected.strip(), f"{_relative(script)} has no leading '#' header block"

    root = tmp_path / "kanban-root"
    env = _throwaway_root_env(root)
    foreign_cwd = tmp_path / "cwd"
    foreign_cwd.mkdir()
    try:
        result = run_dashboard_script(
            ["bash", "-c", 'cd "$1" && exec bash "$2" --help', "probe",
             str(foreign_cwd), str(script)],
            env=env,
            timeout=_PER_SCRIPT_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            f"{_relative(script)} --help did not exit within "
            f"{_PER_SCRIPT_TIMEOUT_S}s -- it started its main work instead of "
            f"printing usage. Fix: answer --help before the script's own work.",
            pytrace=False,
        )

    diagnostic = f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    assert result.returncode == 0, (
        f"{_relative(script)} --help exited {result.returncode}, expected 0.\n{diagnostic}"
    )
    if result.stdout != expected:
        printed = result.stdout.splitlines()
        wanted = expected.splitlines()
        first = next(
            (i for i, (a, b) in enumerate(zip(printed, wanted)) if a != b),
            min(len(printed), len(wanted)),
        )
        pytest.fail(
            f"{_relative(script)} --help printed {len(printed)} lines; its "
            f"leading comment block has {len(wanted)}. First difference at "
            f"line {first + 1}: printed "
            f"{printed[first] if first < len(printed) else '(end)'!r}, header "
            f"{wanted[first] if first < len(wanted) else '(end)'!r}. Fix: the "
            f"--help arm should call header_help_print \"${{BASH_SOURCE[0]}}\" "
            f"and print nothing else.\n{diagnostic}",
            pytrace=False,
        )


if __name__ == "__main__":  # pragma: no cover - manual debug entry point
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", "-v", str(_THIS_FILE)]))
