#!/usr/bin/env python3
"""
lint_tmux_format_jobs.py
========================
Quality check: no tmux option the dashboard sets may run a ``#(...)`` job.

tmux runs a ``#(command)`` in a status format, a window-status format, a
pane-border format or a title string as a shell job, and re-runs it whenever
it redraws that text and the last run is more than a second old -- with many
panes refreshing, on almost every redraw, once per attached client. A
dashboard renderer behind ``#()`` therefore costs a process start per redraw
per client. The dashboard renders such text from a loop into a tmux user
option instead (create.sh's ``status-format[2]`` reads
``#{@pgai_status_bottom}``, written by status-bottom-loop.sh), which tmux
expands with no fork.

What is checked: every shell script under ``team/scripts/dashboard/``, read
as TEXT (never sourced or run). Backslash-continued lines are joined, and a
line whose first non-blank character is ``#`` is a comment and skipped. A
remaining line that runs ``tmux set-option`` / ``set`` / ``set-window-option``
/ ``setw`` (any option: status-format[N], status-left, status-right,
window-status formats, pane-border-format, set-titles-string, ...) or
``tmux select-pane ... -T`` (a pane title) and contains ``#(`` is a finding.

Usage:
    python3 lint_tmux_format_jobs.py [--path PATH ...]

    --path PATH   a shell script, or a directory scanned for ``*.sh``
                  recursively; repeatable. Default: team/scripts/dashboard/.
    -h, --help    print this help and exit 0

Exit codes:
    0   no tmux option set by a scanned script runs a #() job.
    1   one or more findings (CONVERT: quality class -- the gated runners
        record the finding and carry on; it never blocks).
    2   a path to scan does not exist or cannot be read.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

# ---------------------------------------------------------------------------
# Each tree finds itself: this file lives at
# team/scripts/dashboard/lint_tmux_format_jobs.py.
# ---------------------------------------------------------------------------
_THIS_FILE = pathlib.Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent
_REPO_ROOT = _TEAM_DIR.parent

_team_str = str(_TEAM_DIR)
if _team_str not in sys.path:
    sys.path.insert(0, _team_str)

from pgai_agent_kanban.lib.error_contract import emit_error_contract  # noqa: E402

_DEFAULT_SCAN_DIR = _TEAM_DIR / "scripts" / "dashboard"

# A tmux invocation that sets an option, or a pane title. The subcommand
# must be followed by whitespace so set-hook / set-environment / set-buffer
# are not mistaken for set.
_OPTION_SETTER_RE = re.compile(
    r"\btmux\b.*?\b(?:set-option|set-window-option|setw|set)(?=\s)", re.DOTALL
)
_PANE_TITLE_RE = re.compile(r"\btmux\b.*?\bselect-pane\b.*?\s-T\b", re.DOTALL)
_FORMAT_JOB = "#("


def _logical_lines(text: str) -> list[tuple[int, str]]:
    """Return ``(first_line_number, text)`` per logical line.

    A physical line ending in a backslash continues onto the next; the
    joined line keeps the number of its first physical line.
    """
    logical: list[tuple[int, str]] = []
    pending: list[str] = []
    start = 0
    for number, line in enumerate(text.splitlines(), start=1):
        if not pending:
            start = number
        stripped = line.rstrip()
        if stripped.endswith("\\"):
            pending.append(stripped[:-1])
            continue
        pending.append(line)
        logical.append((start, " ".join(pending)))
        pending = []
    if pending:
        logical.append((start, " ".join(pending)))
    return logical


def find_format_jobs(text: str) -> list[tuple[int, str]]:
    """Return ``(line_number, line)`` for every tmux option setter in *text*
    whose value carries a ``#(`` job."""
    findings: list[tuple[int, str]] = []
    for number, line in _logical_lines(text):
        if line.lstrip().startswith("#"):
            continue
        if _FORMAT_JOB not in line:
            continue
        if _OPTION_SETTER_RE.search(line) or _PANE_TITLE_RE.search(line):
            findings.append((number, " ".join(line.split())))
    return findings


def _scripts_under(paths: list[pathlib.Path]) -> list[pathlib.Path]:
    scripts: list[pathlib.Path] = []
    for path in paths:
        if path.is_dir():
            scripts.extend(sorted(p for p in path.rglob("*.sh") if p.is_file()))
        else:
            scripts.append(path)
    return scripts


def _display(path: pathlib.Path) -> str:
    try:
        return path.resolve().relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def main(argv: "list[str] | None" = None) -> int:
    """Run the tmux format-job lint over the dashboard scripts.

    Exit codes: 0 no tmux option set by a scanned script runs a ``#()`` job;
    1 one or more findings (printed; quality class); 2 a path to scan does not
    exist or cannot be read.
    """
    parser = argparse.ArgumentParser(
        prog="lint_tmux_format_jobs.py",
        description=(
            "Report any tmux option a dashboard script sets whose value runs a "
            "#() job (status formats, status-left/right, window-status formats, "
            "pane-border-format, titles). Quality class: findings exit 1 and "
            "never block a runner."
        ),
    )
    parser.add_argument(
        "--path", action="append", default=[], metavar="PATH",
        help="a shell script, or a directory scanned for *.sh recursively; "
             "repeatable (default: team/scripts/dashboard/)",
    )
    args = parser.parse_args(argv)

    paths = [pathlib.Path(p) for p in args.path] or [_DEFAULT_SCAN_DIR]
    missing = [p for p in paths if not p.exists()]
    if missing:
        for path in missing:
            print(f"lint_tmux_format_jobs: no such file or directory: {path}", file=sys.stderr)
        return 2

    findings: list[tuple[pathlib.Path, int, str]] = []
    for script in _scripts_under(paths):
        try:
            text = script.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            print(f"lint_tmux_format_jobs: cannot read {script}: {exc}", file=sys.stderr)
            return 2
        findings.extend((script, number, line) for number, line in find_format_jobs(text))

    if not findings:
        print("lint_tmux_format_jobs: OK -- no tmux option set by a dashboard script runs a #() job.")
        return 0

    for script, number, line in findings:
        print(f"lint_tmux_format_jobs: {_display(script)}:{number}: #() job in a tmux option: {line}",
              file=sys.stderr)

    first_script, first_line, _ = findings[0]
    # CONVERT (team/SOP-core.md "Blocking Severity"): a #() job in a tmux
    # option costs CPU on every status redraw but breaks nothing -- the
    # dashboard still renders correctly. The lint narrates and its finding
    # is filed by the gated runner; it never blocks a runner or a release.
    emit_error_contract(
        "dashboard.tmux-format-job",
        (
            f"{len(findings)} tmux option(s) set by a dashboard script run a #() job, "
            "which tmux re-runs on almost every redraw, once per attached client: "
            + "; ".join(f"{_display(s)}:{n}" for s, n, _ in findings)
            + ". Render that text from a loop into a tmux user option and read "
            "the option with #{@name} instead, as create.sh does for "
            "status-format[2] (#{@pgai_status_bottom}, written by "
            "status-bottom-loop.sh)."
        ),
        (
            f"sed -n '{first_line}p' {_display(first_script)} && "
            "grep -n '@pgai_status_bottom' team/scripts/dashboard/create.sh "
            "team/scripts/dashboard/status-bottom-loop.sh && "
            "python3 team/scripts/dashboard/lint_tmux_format_jobs.py"
        ),
    )
    # CENSUS-ANCHOR: lint-tmux-format-jobs-01
    return 1


if __name__ == "__main__":
    sys.exit(main())
