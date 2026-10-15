"""
Command line for the dashboard's board column panes.

    python3 -m pgai_agent_kanban.dashboard.column_render input        [options]
    python3 -m pgai_agent_kanban.dashboard.column_render queue        [options]
    python3 -m pgai_agent_kanban.dashboard.column_render multi        [options]
    python3 -m pgai_agent_kanban.dashboard.column_render board-render [options]
    python3 -m pgai_agent_kanban.dashboard.column_render board-build  [options]

``input`` and ``queue`` render one project's column; ``multi`` renders the
mixed-project column from the per-project lists the bash gather stage
exports (see ``pgai_agent_kanban.dashboard.column_render.multi``).  Every
mode prints the pane's lines to stdout and exits 0; a usage error exits 2.

``board-render`` and ``board-build`` serve the mixed-project column from
the shared board snapshot (``board_snapshot``).  ``board-render`` prints the
column from a fresh snapshot and exits 0, or prints nothing and exits 3
when the snapshot is missing or unusable and 4 when it is stale.
``board-build`` gathers every board column, writes the snapshot, counts the
gather, writes the pre-rendered columns, and prints the requested column
(exit 0); a snapshot, stats or pre-render file it cannot write is narrated
on stderr and the column is still printed.  With ``--gather=env`` (the
default) the per-project lists come from the environment the bash gather
stage exported; with ``--gather=tree`` they are gathered in this process
from ``--kanban-root`` (``board_gather``), and a tree that gather does not
cover -- or any error while gathering -- prints nothing, narrates on stderr
and exits 5 so the caller gathers through bash instead.  ``--prerender``
takes column-render.sh's ``--board-widths`` value; the requested column is
always pre-rendered at its own width as well.

The colour options take the ANSI escapes the caller resolved (empty when
colour is off), so the caller's NO_COLOR / TERM=dumb decision is the only
one.  Bash callers reach this through ``pp_run_ops``.

``input``'s ``--max-major`` / ``--max-minor`` / ``--max-patch`` take the
ceiling in effect as the ``pp_max_*`` accessors print it: a whole number,
or empty for no ceiling (``none``).  A ceiling option left out is not
checked.  ``--ceiling-unreadable=true`` says an accessor refused a malformed
value; a non-numeric ceiling value is read the same way, never as "no
ceiling".  An open requirement the ceilings stop carries a " [ceiling]" or
" [ceiling?]" suffix (``cells.CeilingCards``).
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys

# Run by file path (``python3 .../__main__.py``) or loaded from a file
# outside its package, this module has no parent package and the interpreter
# puts only its own directory on sys.path, so the package imports below would
# not resolve. Put this file's own package root -- the directory holding the
# pgai_agent_kanban/ this file sits in -- at the front of sys.path so the
# imports resolve from the same tree, never another. Imported as a package
# member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[3])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.dashboard.column_render import board_snapshot  # noqa: E402
from pgai_agent_kanban.dashboard.column_render.cells import CeilingCards, DeferCards, Palette  # noqa: E402
from pgai_agent_kanban.dashboard.column_render.multi import (  # noqa: E402
    DEFAULT_STATUS_GLYPH,
    Layout,
    render_multi_project_column,
    sources_from_env,
)
from pgai_agent_kanban.dashboard.column_render.single import (  # noqa: E402
    parse_max_rows,
    render_input_column,
    render_queue_column,
)

_PROG = "python3 -m pgai_agent_kanban.dashboard.column_render"

#: board-render exit codes for a snapshot it cannot serve the column from.
EXIT_SNAPSHOT_MISSING = 3
EXIT_SNAPSHOT_STALE = 4

#: board-build --gather=tree exit code when the in-process gather cannot serve the tree.
EXIT_GATHER_UNAVAILABLE = 5


def _bool(value: str) -> bool:
    return value.strip().lower() == "true"


def _ceiling_cards(args: argparse.Namespace) -> CeilingCards:
    """The input column's ceilings from its --max-* / --ceiling-unreadable options."""
    limits: dict = {}
    unreadable = args.ceiling_unreadable
    for key in ("max_major", "max_minor", "max_patch"):
        raw = getattr(args, key)
        if raw is None:
            continue
        raw = raw.strip()
        if not raw:
            limits[key] = None
        elif raw.isdigit():
            limits[key] = int(raw)
        else:
            unreadable = True
    return CeilingCards(limits=limits, unreadable=unreadable)


def _add_palette_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--use-color", type=_bool, default=False, metavar="true|false",
                        help="colour rows (default false)")
    for name in ("red", "yellow", "green", "white", "dim", "reset"):
        parser.add_argument(f"--{name}", default="", metavar="ESC",
                            help=f"ANSI escape for {name} (empty when colour is off)")


def _palette(args: argparse.Namespace) -> Palette:
    return Palette(args.use_color, args.red, args.yellow, args.green, args.white, args.dim, args.reset)


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for the pane modes."""
    parser = argparse.ArgumentParser(prog=_PROG, description="Render a dashboard board column pane.")
    modes = parser.add_subparsers(dest="mode", required=True, metavar="{input,queue,multi,board-render,board-build}")

    p_input = modes.add_parser("input", help="one project's bugs/, priority/ or requirements/ column")
    p_input.add_argument("--input-dir", required=True, help="the intake directory to list")
    p_input.add_argument("--rows", type=int, required=True, help="pane height in rows")
    p_input.add_argument("--width", type=int, required=True, help="pane width in visible characters")
    p_input.add_argument("--requirements", type=_bool, default=False, metavar="true|false",
                         help="classify as a requirements column (default false)")
    p_input.add_argument("--active-rc", default="", help="the project's active RC, vX.Y.Z, or empty")
    p_input.add_argument("--last-released", default="", help="the project's last release, vX.Y.Z, or empty")
    p_input.add_argument("--max-rows", default="20",
                         help="per-project row cap; a non-positive or non-numeric value reads as 20")
    p_input.add_argument("--defer-mode", default="off", help="deferral posture: off, on or malformed")
    p_input.add_argument("--defer-ids", default="", help="newline-separated IDs of deferred bugs")
    p_input.add_argument("--glyph-deferred-bug", default="", help="glyph for a deferred bug card")
    p_input.add_argument("--glyph-defer-warning", default="", help="glyph for an open bug under a malformed posture")
    for key in ("major", "minor", "patch"):
        p_input.add_argument(f"--max-{key}", dest=f"max_{key}", default=None, metavar="N",
                             help=f"the max_{key} ceiling in effect; empty for no ceiling; omit to not check it")
    p_input.add_argument("--ceiling-unreadable", type=_bool, default=False, metavar="true|false",
                         help="a version ceiling is malformed: mark every open requirement (default false)")
    _add_palette_args(p_input)

    p_queue = modes.add_parser("queue", help="one agent backlog column")
    p_queue.add_argument("--backlog-file", required=True, help="the <agent>_backlog.md to list")
    p_queue.add_argument("--rows", type=int, required=True, help="pane height in rows")
    p_queue.add_argument("--width", type=int, required=True, help="pane width in visible characters")
    p_queue.add_argument("--tasks-dir", default="",
                         help="directory holding <TASK-ID>/status.md (empty: relative to the working directory)")
    p_queue.add_argument("--max-rows", default="20",
                         help="per-project row cap; a non-positive or non-numeric value reads as 20")
    _add_palette_args(p_queue)

    p_multi = modes.add_parser("multi", help="the mixed-project column; per-project lists come from the environment")
    _add_multi_args(p_multi)

    p_board_render = modes.add_parser(
        "board-render", help="the mixed-project column from a fresh shared board snapshot (exit 3 missing, 4 stale)",
    )
    p_board_render.add_argument("--snapshot", required=True, help="the board snapshot file")
    p_board_render.add_argument("--ttl", type=float, required=True,
                                help="seconds a snapshot stays fresh; one this old or older is stale")
    _add_multi_args(p_board_render)

    p_board_build = modes.add_parser(
        "board-build",
        help="gather every board column into the shared snapshot, then print one column; "
             "per-column lists come from the environment",
    )
    p_board_build.add_argument("--snapshot", required=True, help="the board snapshot file to write")
    p_board_build.add_argument("--gather", choices=("env", "tree"), default="env",
                               help="where the per-project lists come from: the environment the bash "
                                    "gather exported (env, default) or this process (tree)")
    p_board_build.add_argument("--kanban-root", default="", help="the kanban root (required with --gather=tree)")
    p_board_build.add_argument("--dashboard-max-rows", default="",
                               help="the caller's DASHBOARD_MAX_ROWS (--gather=tree; empty reads as 20)")
    p_board_build.add_argument("--prerender", default="", metavar="SPEC",
                               help="column widths to pre-render, as column-render.sh --board-widths takes them")
    _add_multi_args(p_board_build)
    return parser


def _add_multi_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--subcommand", required=True, choices=("input", "queue"), help="column kind")
    parser.add_argument("--column-type", default="",
                        help="bugs, priorities, requirements, pm, coder, writer, tester or cm")
    parser.add_argument("--width", type=int, required=True, help="pane width in visible characters")
    parser.add_argument("--status-glyph", default=DEFAULT_STATUS_GLYPH,
                        help=f"project-tag glyph (default {DEFAULT_STATUS_GLYPH})")
    defaults = Layout()
    for field in ("min_rows_per_column", "max_rows_per_column", "min_rows_per_project", "max_rows_per_project"):
        parser.add_argument(f"--{field.replace('_', '-')}", type=int, default=getattr(defaults, field),
                            dest=field, help=f"[dashboard] {field} (default {getattr(defaults, field)})")
    _add_palette_args(parser)


def _layout(args: argparse.Namespace) -> Layout:
    return Layout(args.min_rows_per_column, args.max_rows_per_column,
                  args.min_rows_per_project, args.max_rows_per_project)


def _render_from_snapshot(snapshot: dict, args: argparse.Namespace, palette: Palette):
    return board_snapshot.render_column(
        snapshot, subcommand=args.subcommand, column_type=args.column_type, width=args.width,
        palette=palette, layout=_layout(args), status_glyph=args.status_glyph,
    )


def _board_render(args: argparse.Namespace, palette: Palette) -> "tuple[int, list]":
    snapshot, reason = board_snapshot.read_snapshot(
        pathlib.Path(args.snapshot), ttl_seconds=args.ttl, use_color=palette.use_color,
    )
    if snapshot is None:
        return (EXIT_SNAPSHOT_STALE if reason == board_snapshot.MISS_STALE else EXIT_SNAPSHOT_MISSING), []
    lines = _render_from_snapshot(snapshot, args, palette)
    if lines is None:
        return EXIT_SNAPSHOT_MISSING, []
    return 0, lines


def _gather_tree_snapshot(args: argparse.Namespace, palette: Palette) -> "dict | None":
    """The snapshot gathered in process from --kanban-root, or None (narrated) when it cannot be."""
    from pgai_agent_kanban.dashboard.column_render import board_gather

    try:
        sources = board_gather.gather_board_sources(
            args.kanban_root, dashboard_max_rows=args.dashboard_max_rows, environ=os.environ,
        )
        return board_snapshot.build_snapshot(sources, use_color=palette.use_color)
    except Exception as exc:  # noqa: BLE001 -- any failure hands the build back to the bash gather
        print(f"[dashboard-snapshot] in-process board gather unavailable for {args.kanban_root or '(no root)'} "
              f"({type(exc).__name__}: {exc}); gathering through bash", file=sys.stderr)
        return None


def _write_prerenders(path: pathlib.Path, snapshot: dict, args: argparse.Namespace, palette: Palette) -> None:
    """Pre-render the requested column and every --prerender column beside the snapshot."""
    wanted = set(board_snapshot.parse_board_widths(args.prerender).items())
    wanted.add((args.column_type, args.width))
    layout = _layout(args)
    for column_type, width in sorted(wanted):
        subcommand = board_snapshot.column_subcommand(column_type)
        lines = board_snapshot.render_column(
            snapshot, subcommand=subcommand, column_type=column_type, width=width,
            palette=palette, layout=layout, status_glyph=args.status_glyph,
        ) if subcommand else None
        if lines is None:
            continue
        target = board_snapshot.prerender_path(path, column_type, width)
        try:
            board_snapshot.write_prerender(target, lines)
        except OSError as exc:
            print(f"[dashboard-snapshot] pre-rendered column {target} could not be written ({exc}); "
                  "that column will render from the snapshot", file=sys.stderr)


def _board_build(args: argparse.Namespace, palette: Palette) -> "list | None":
    path = pathlib.Path(args.snapshot)
    if args.gather == "tree":
        snapshot = _gather_tree_snapshot(args, palette)
        if snapshot is None:
            return None
    else:
        snapshot = board_snapshot.build_snapshot_from_env(os.environ, use_color=palette.use_color)
    try:
        board_snapshot.write_snapshot(path, snapshot)
        board_snapshot.record_gather(path)
    except OSError as exc:
        print(f"[dashboard-snapshot] board snapshot {path} could not be written ({exc}); "
              "the other columns will gather for themselves", file=sys.stderr)
    else:
        _write_prerenders(path, snapshot, args, palette)
    lines = _render_from_snapshot(snapshot, args, palette)
    if lines is None:
        lines = render_multi_project_column(
            sources_from_env(os.environ, board_snapshot.column_env_suffix(args.column_type)),
            subcommand=args.subcommand, column_type=args.column_type, width=args.width,
            palette=palette, layout=_layout(args), status_glyph=args.status_glyph,
        )
    return lines


def main(argv: "list[str] | None" = None) -> int:
    """Render the requested pane to stdout; returns the exit status."""
    args = build_parser().parse_args(argv)
    palette = _palette(args)
    if args.mode == "input":
        lines = render_input_column(
            pathlib.Path(args.input_dir),
            rows=args.rows,
            width=args.width,
            is_requirements=args.requirements,
            active_rc=args.active_rc.strip(),
            last_released=args.last_released.strip(),
            max_rows=parse_max_rows(args.max_rows),
            palette=palette,
            defer=DeferCards(
                mode=args.defer_mode.strip(),
                ids=frozenset(ln.strip() for ln in args.defer_ids.splitlines() if ln.strip()),
                glyph_deferred_bug=args.glyph_deferred_bug,
                glyph_defer_warning=args.glyph_defer_warning,
            ),
            ceiling=_ceiling_cards(args),
        )
    elif args.mode == "queue":
        lines = render_queue_column(
            pathlib.Path(args.backlog_file),
            rows=args.rows,
            width=args.width,
            tasks_dir=pathlib.Path(args.tasks_dir),
            max_rows=parse_max_rows(args.max_rows),
            palette=palette,
        )
    elif args.mode == "board-render":
        status, lines = _board_render(args, palette)
        if status != 0:
            return status
    elif args.mode == "board-build":
        lines = _board_build(args, palette)
        if lines is None:
            return EXIT_GATHER_UNAVAILABLE
    else:
        lines = render_multi_project_column(
            sources_from_env(os.environ),
            subcommand=args.subcommand,
            column_type=args.column_type,
            width=args.width,
            palette=palette,
            layout=_layout(args),
            status_glyph=args.status_glyph,
        )
    sys.stdout.write("".join(f"{line}\n" for line in lines))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI wiring
    sys.exit(main())
