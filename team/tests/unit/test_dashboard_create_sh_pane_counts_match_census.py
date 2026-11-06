"""
test_dashboard_create_sh_pane_counts_match_census.py
=====================================================
Cross-checks the pane count create.sh actually BUILDS for every tmux window
against the pane count team/config/dashboard_windows.census DECLARES for
that same window.

Why this test exists (distinct from the checks already in
test_dashboard_window_census.py): that module verifies the census is
internally consistent (each W row's declared pane_count equals the number
of P rows that follow it) and that the generated documentation surfaces
agree with the census. Neither of those catches a builder-side defect
where create.sh emits more (or fewer) `tmux split-window` calls for a
window than the census says that window should have -- the exact shape of
the bug this test is written against (an extra idle pane silently seeded
into a window's build sequence, costing its sibling regions rows, while
every doc-generation surface remains untouched and green). This test
parses create.sh itself and counts what it actually builds.

Static-analysis approach:
  1. Every window's build sequence starts with exactly one `tmux
     new-session` or `tmux new-window` call naming it (`-n "<name>"`), and
     is followed, before the next such call, by zero or more `tmux
     split-window` calls that each add exactly one pane, and by zero or
     more calls to the shared board builder `dashboard_build_board_window`
     (team/scripts/dashboard/lib/board_window.sh), which adds the panes of
     the `tmux split-window` calls in its own body. Three kinds of those
     splits are conditional: the shell-row split (the one sized
     `-l "$shell_h"`) runs unless the call's third argument, the shell
     percentage, is the literal 0 or absent; the workbench-column split
     (the one sized by `workbench_pct`) runs unless the call's fifth
     argument, the workbench percentage, is the literal 0 or absent; and
     the splits inside the builder's `if [[ "$column_set" == "split" ]]`
     block (the five agent-queue columns) run only when the call's
     seventh argument, the column set, is "split" or absent. The census
     declares the wide-window layout, so a non-zero workbench argument
     counts the workbench split even though the builder's narrow-window
     guard can skip it at runtime. So a window's actual built pane count
     is `1 + (split-window calls before the next new-session/new-window
     call) + (splits each builder call in that span adds)`.
  2. Two census windows have more than one row sharing a name, and each
     is resolved to its variants differently:
     - operator, whose builder call passes its column set as a variable
       (create.sh's OPERATOR_BOARD_AGENTS, from [dashboard]
       operator_board_agents). Its census variants are named for the
       column sets, so each variant's pane count is checked against the
       one build sequence counted with that column set.
     - drill-N, whose layout is chosen at runtime by
       `[project] workflow_type` (see create.sh's `_drill_wftype` branch),
       needs its two `tmux new-window -n "${_DRILL_WIN}"` call sites
       resolved to the matching census variant. This is done by locating
       the `if [[ "$_drill_wftype" == "document" ]] ... else ... fi`
       construct and checking which branch's text span contains each call
       site, rather than by pane-count or script-name matching (both of
       which are what this test is trying to verify in the first place,
       and would make the check circular).

A mismatch names the offending window (and variant, if it has one) so the
fix is immediately actionable: either create.sh is building the wrong
pane count and must be corrected to match the census, or the census
itself is wrong and must be corrected to match the intended layout --
never both, and never by hand-editing the census to paper over a wrong
builder (team/config/dashboard_windows.census's own header: "single
source of truth for layout").
"""

from __future__ import annotations

import pathlib
import re
import shlex
import sys

_THIS_FILE = pathlib.Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/tests/unit/ -> team/
_CREATE_SH = _TEAM_DIR / "scripts" / "dashboard" / "create.sh"
_BOARD_WINDOW_SH = _TEAM_DIR / "scripts" / "dashboard" / "lib" / "board_window.sh"

if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from pgai_agent_kanban.dashboard.window_census import (  # noqa: E402
    Window,
    load_census,
)

# ---------------------------------------------------------------------------
# create.sh parsing helpers
# ---------------------------------------------------------------------------

# Matches both `tmux new-session ... -n "main" ...` and
# `tmux new-window ... -n "operator" ...` -- every window's build sequence
# opens with one or the other, always carrying a `-n "<name>"` window-name
# argument.
_WINDOW_OPEN_RE = re.compile(r'tmux new-(?:session|window)\b[^\n]*-n\s+"([^"]+)"')

# The drill-N window's `-n` argument is the shell variable `${_DRILL_WIN}`
# rather than a literal name (it is renamed to "drill-N: <project>" only
# after all panes are built) -- resolved to the census's literal
# placeholder name "drill-N" so it matches load_census()'s window records.
_DRILL_WIN_LITERAL = "${_DRILL_WIN}"
_DRILL_CENSUS_NAME = "drill-N"

# The runtime variant switch drill-N's build sequence branches on. Locating
# this construct's branch spans is how the two `-n "${_DRILL_WIN}"` call
# sites are told apart -- see module docstring for why pane-count or
# script-name matching would be circular here.
_DRILL_VARIANT_IF_RE = re.compile(r'if\s*\[\[\s*"\$_drill_wftype"\s*==\s*"document"\s*\]\]')
_ELSE_LINE_RE = re.compile(r"\n[ \t]*else[ \t]*\n")

# A call to the shared board builder, capturing its argument text up to the
# end of the command, line continuations included:
# `dashboard_build_board_window "<session>" "<window>" <shell_pct>
# [<shell_min_rows>] [<workbench_pct>] [<workbench_min_cols>] [<column_set>]`.
_BOARD_BUILDER_CALL_RE = re.compile(
    r"^[ \t]*dashboard_build_board_window[ \t]+((?:[^\n\\]|\\[ \t]*\n)*)",
    re.MULTILINE,
)

# Positions, in the builder's argument list, of the arguments that switch a
# conditional split on.
_SHELL_PCT_ARG = 2
_WORKBENCH_PCT_ARG = 4
_COLUMN_SET_ARG = 6

# The column sets the builder builds, and the one it builds when the
# column-set argument is absent.
_COLUMN_SETS = ("merged", "split")
_DEFAULT_COLUMN_SET = "split"

# The builder's block of splits that only the "split" column set runs.
_SPLIT_ONLY_BLOCK_RE = re.compile(
    r'^[ \t]*if \[\[ "\$column_set" == "split" \]\]; then\n(.*?)^[ \t]*fi\b',
    re.MULTILINE | re.DOTALL,
)


def _board_builder_split_counts() -> tuple[int, int, int, int]:
    """Return (unconditional splits, shell-row splits, workbench splits,
    split-column-set-only splits) for the shared board builder, counted from
    the `tmux split-window` lines in its body. The shell-row split is the
    one sized by `-l "$shell_h"`; the workbench split is the one sized by
    `workbench_pct`; the split-only splits are those inside its
    `if [[ "$column_set" == "split" ]]` block. Each runs only when the
    builder is asked for that pane or column set."""
    lib_text = _BOARD_WINDOW_SH.read_text(encoding="utf-8")
    splits = re.findall(r"^\s*tmux split-window\b(.*)$", lib_text, re.MULTILINE)
    shell_splits = [s for s in splits if '"$shell_h"' in s]
    workbench_splits = [s for s in splits if "workbench_pct" in s]
    assert len(shell_splits) == 1, (
        f'expected exactly one shell-row split (-l "$shell_h") in ' f"{_BOARD_WINDOW_SH}; found {shell_splits}"
    )
    assert len(workbench_splits) == 1, (
        f"expected exactly one workbench-column split (sized by workbench_pct) "
        f"in {_BOARD_WINDOW_SH}; found {workbench_splits}"
    )
    split_only = sum(
        len(re.findall(r"^\s*tmux split-window\b", block, re.MULTILINE))
        for block in _SPLIT_ONLY_BLOCK_RE.findall(lib_text)
    )
    assert split_only > 0, (
        f'expected the split-window calls only the "split" column set runs '
        f'inside an `if [[ "$column_set" == "split" ]]` block in '
        f"{_BOARD_WINDOW_SH}; found none"
    )
    unconditional = len(splits) - len(shell_splits) - len(workbench_splits) - split_only
    return unconditional, len(shell_splits), len(workbench_splits), split_only


def _arg_enables_split(args: list[str], position: int) -> bool:
    """True unless the builder argument at *position* is absent or the literal 0."""
    return len(args) > position and args[position] != "0"


def _builder_call_args(section: str) -> list[list[str]]:
    """The argument list of every shared-board-builder call in *section*."""
    return [
        shlex.split(re.sub(r"\\[ \t]*\n", " ", arg_text), comments=True)
        for arg_text in _BOARD_BUILDER_CALL_RE.findall(section)
    ]


def _column_set_is_variable(section: str) -> bool:
    """True when a builder call in *section* passes its column set as a
    shell variable, so the column set is chosen at runtime."""
    return any(
        len(args) > _COLUMN_SET_ARG and args[_COLUMN_SET_ARG].startswith("$") for args in _builder_call_args(section)
    )


def _board_builder_panes_added(section: str, column_set: str | None = None) -> int:
    """Panes added by every shared-board-builder call in *section*. A call
    whose column set is a shell variable is counted with *column_set*."""
    unconditional, shell, workbench, split_only = _board_builder_split_counts()
    added = 0
    for args in _builder_call_args(section):
        added += unconditional
        if _arg_enables_split(args, _SHELL_PCT_ARG):
            added += shell
        if _arg_enables_split(args, _WORKBENCH_PCT_ARG):
            added += workbench
        call_set = args[_COLUMN_SET_ARG] if len(args) > _COLUMN_SET_ARG else _DEFAULT_COLUMN_SET
        if call_set.startswith("$"):
            assert column_set is not None, (
                f"a dashboard_build_board_window call passes its column set as "
                f"{call_set}; count it with an explicit column set"
            )
            call_set = column_set
        if call_set == "split":
            added += split_only
    return added


_FI_LINE_RE = re.compile(r"\n[ \t]*fi[ \t]*\n")


def _window_open_positions(content: str) -> list[tuple[int, str]]:
    """Return [(char_offset, census_name), ...] for every window-opening
    tmux call in create.sh, in file order.
    """
    positions = []
    for match in _WINDOW_OPEN_RE.finditer(content):
        raw_name = match.group(1)
        name = _DRILL_CENSUS_NAME if raw_name == _DRILL_WIN_LITERAL else raw_name
        positions.append((match.start(), name))
    return positions


def _window_sections(content: str) -> list[tuple[int, str, str]]:
    """Return [(char_offset, census_name, section_text), ...] -- one entry
    per window-opening call, in file order; a section runs to the next
    window-opening call (or end of file, for the last window)."""
    opens = _window_open_positions(content)
    results = []
    for i, (start, name) in enumerate(opens):
        end = opens[i + 1][0] if i + 1 < len(opens) else len(content)
        results.append((start, name, content[start:end]))
    return results


def _section_pane_count(section: str, column_set: str | None = None) -> int:
    """1 (the pane the opening call itself creates) plus the number of
    `tmux split-window` calls in *section*, plus the panes any shared
    board-builder call in it adds (a runtime column set counted as
    *column_set*)."""
    split_count = len(re.findall(r"tmux split-window\b", section))
    split_count += _board_builder_panes_added(section, column_set)
    return 1 + split_count


def _drill_variant_branch_spans(content: str) -> dict[str, tuple[int, int]]:
    """Return {"document": (start, end), "release": (start, end)} char-offset
    spans for the two branches of create.sh's `_drill_wftype` if/else.

    Raises AssertionError (loud, not a silent skip) if the construct is not
    found in the expected if/else/fi shape -- a passing run of this test
    depends on being able to tell the two drill-N call sites apart.
    """
    if_match = _DRILL_VARIANT_IF_RE.search(content)
    assert if_match is not None, (
        "create.sh's drill-N workflow_type branch "
        '(if [[ "$_drill_wftype" == "document" ]]) was not found; '
        "this test cannot resolve which of the two drill-N "
        "`tmux new-window` call sites belongs to which census variant"
    )
    else_match = _ELSE_LINE_RE.search(content, if_match.end())
    assert else_match is not None, "no matching 'else' found after create.sh's drill-N " "workflow_type if-branch"
    fi_match = _FI_LINE_RE.search(content, else_match.end())
    assert fi_match is not None, "no matching 'fi' found after create.sh's drill-N " "workflow_type else-branch"
    return {
        "document": (if_match.start(), else_match.start()),
        "release": (else_match.end(), fi_match.start()),
    }


def _resolve_window(
    offset: int,
    candidates: list[Window],
    drill_spans: dict[str, tuple[int, int]],
) -> Window:
    """Pick the one census Window record that the window-opening call at
    *offset* belongs to, out of *candidates* (all census rows sharing this
    window's name). A name with exactly one census row needs no
    resolution; a name with more than one whose layout is not chosen by a
    runtime column set (drill-N's two workflow_type variants, currently the
    only such case) is resolved by which branch span of the census's
    is_wftype-else-fi construct the call falls in.
    """
    if len(candidates) == 1:
        return candidates[0]

    matches = [
        w
        for w in candidates
        if drill_spans.get(w.variant, (None, None))[0] is not None
        and drill_spans[w.variant][0] <= offset < drill_spans[w.variant][1]
    ]
    assert len(matches) == 1, (
        f"could not uniquely resolve which census variant the window-opening "
        f"call at character offset {offset} belongs to; candidates are "
        f"{[w.variant for w in candidates]}, branch spans are {drill_spans}. "
        f"This test only knows how to disambiguate multiple variants for "
        f"the drill-N window's workflow_type branch -- a new multi-variant "
        f"census window needs this resolver extended."
    )
    return matches[0]


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------


def test_create_sh_builds_the_pane_count_the_census_declares_for_every_window() -> None:
    """create.sh's actual `tmux split-window` call count, per window,
    agrees with that window's census-declared pane_count -- for every
    window create.sh builds, not just the operator window.

    A window with an extra pane seeded into its build sequence (this
    test's motivating regression: a duplicate interactive shell pane in
    the operator window that outnumbered the census's declared count by
    one) fails this assertion, naming the offending window and its
    variant. A census window create.sh never builds at all also fails,
    naming the window -- closing the opposite gap, where a window is
    declared but silently never created.
    """
    content = _CREATE_SH.read_text(encoding="utf-8")
    windows = load_census()

    windows_by_name: dict[str, list[Window]] = {}
    for window in windows:
        windows_by_name.setdefault(window.name, []).append(window)
    remaining = {name: list(rows) for name, rows in windows_by_name.items()}

    drill_spans = _drill_variant_branch_spans(content)

    mismatches: list[str] = []
    unresolved: list[str] = []
    for offset, name, section in _window_sections(content):
        candidates = remaining.get(name)
        if not candidates:
            unresolved.append(
                f"create.sh builds a window named {name!r} at character "
                f"offset {offset} that team/config/dashboard_windows.census "
                f"does not declare (or has already matched to an earlier "
                f"build of the same name)"
            )
            continue
        if _column_set_is_variable(section):
            # One build sequence, one census variant per column set.
            for window in list(candidates):
                candidates.remove(window)
                if window.variant not in _COLUMN_SETS:
                    mismatches.append(
                        f"{name!r} (variant={window.variant!r}): create.sh chooses "
                        f"this window's column set at runtime, so its census "
                        f"variants must be named for the column sets {_COLUMN_SETS}"
                    )
                    continue
                actual_count = _section_pane_count(section, window.variant)
                if actual_count != window.pane_count:
                    mismatches.append(
                        f"{name!r} (variant={window.variant!r}): create.sh builds "
                        f"{actual_count} pane(s) but the census declares "
                        f"{window.pane_count}"
                    )
            continue
        window = _resolve_window(offset, candidates, drill_spans)
        candidates.remove(window)
        actual_count = _section_pane_count(section)
        if actual_count != window.pane_count:
            variant_note = f" (variant={window.variant!r})" if window.variant != "-" else ""
            mismatches.append(
                f"{name!r}{variant_note}: create.sh builds {actual_count} "
                f"pane(s) but the census declares {window.pane_count}"
            )

    still_undeclared = [
        f"{name!r} (variant={window.variant!r})"
        for name, rows in windows_by_name.items()
        for window in remaining.get(name, [])
    ]

    assert not unresolved, (
        "create.sh builds one or more windows the census does not "
        "declare:\n  " + "\n  ".join(unresolved) + "\n"
        "Add the window to team/config/dashboard_windows.census, or "
        "remove the unexpected build from create.sh."
    )
    assert not still_undeclared, (
        "team/config/dashboard_windows.census declares one or more "
        "windows create.sh never builds:\n  "
        + "\n  ".join(still_undeclared)
        + "\nAdd the window's build to create.sh, or remove the stale row "
        "from the census."
    )
    assert not mismatches, (
        "create.sh's actual built pane count disagrees with "
        "team/config/dashboard_windows.census (the single source of "
        "truth for layout) for:\n  "
        + "\n  ".join(mismatches)
        + "\nFix create.sh to build the census's declared pane count for "
        "each named window -- never hand-edit the census to match a wrong "
        "builder."
    )


def test_create_sh_bash_syntax_is_valid() -> None:
    """bash -n create.sh passes -- a cheap guard so a syntax error in the
    file this test statically parses is reported as a syntax failure, not
    a confusing regex-matching failure in the test above.
    """
    import subprocess

    result = subprocess.run(
        ["bash", "-n", str(_CREATE_SH)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"bash -n {_CREATE_SH} failed:\n{result.stderr}"
