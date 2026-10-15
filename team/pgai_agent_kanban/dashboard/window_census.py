"""
window_census.py — the one Python reader for the dashboard window census.

team/config/dashboard_windows.census is the machine-readable single source
of truth for the tmux dashboard's window and pane layout (see that file's
own header for the full format and parse rule). This module is the ONLY
place in the Python stack that parses it — every other Python consumer
(the generator, the pytest suite) imports :func:`load_census` instead of
re-reading the file itself, so there is never a second hand-transcribed
copy of the window list to drift out of sync with the first.

The sibling bash reader is team/scripts/lib/dashboard_window_census.sh —
same file, same parse rule, independent implementation because bash and
Python cannot share a parser, but both readers agree on the on-disk format
documented in the census file's header.

Public API
----------
    load_census(path=None) -> list[Window]
        Parse the census file into an ordered list of Window records, each
        carrying its Pane records in tmux pane-index order.

    default_census_path() -> pathlib.Path
        The census file's default location
        (team/config/dashboard_windows.census, resolved relative to this
        module's own location — "each tree finds itself").

    load_pane_cadence(path=None) -> dict[tuple[int, str, int], PaneCadence]
        Parse the census's C (cadence) records, keyed by
        (tmux_index, variant, pane_seq).

    pane_hidden_declaration_findings(path=None) -> list[str]
        One message per pane whose C record is missing, duplicated,
        malformed, or declares something other than "pause" or the
        window-0 "always-on" status surface; empty when every pane is
        declared correctly.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Pane:
    """One pane record (a single P line) from the census."""

    tmux_index: int
    variant: str
    pane_seq: int
    position: str
    script: str
    standalone_cmd: str


@dataclass(frozen=True)
class Window:
    """One window record (a single W line) from the census, with its panes."""

    tmux_index: int
    name: str
    variant: str
    pane_count: int
    panes: tuple[Pane, ...] = field(default_factory=tuple)


#: The two accepted when_hidden declarations (see the census header's C
#: record section).
WHEN_HIDDEN_PAUSE = "pause"
WHEN_HIDDEN_ALWAYS_ON = "always-on"
#: The only window whose status surface may stay on while hidden.
ALWAYS_ON_WINDOW_INDEX = 0
#: Symbolic cadence values besides a whole number of seconds.
SYMBOLIC_CADENCES = frozenset({"refresh", "stream", "-"})


@dataclass(frozen=True)
class PaneCadence:
    """One cadence record (a single C line) from the census."""

    tmux_index: int
    variant: str
    pane_seq: int
    cadence: str
    when_hidden: str


class CensusFormatError(ValueError):
    """Raised when the census file does not parse to a coherent window list.

    Never raised for cosmetic issues (comment wording, column alignment) —
    only for structural problems a reader cannot safely paper over: a P
    record with no preceding W record, a pane count that does not match the
    number of P records actually present, or a P record whose tmux_index /
    variant disagrees with the W record it follows.
    """


def default_census_path() -> pathlib.Path:
    """Return the census file's default path.

    Resolved from this module's own location (team/pgai_agent_kanban/
    dashboard/window_census.py -> team/config/dashboard_windows.census) so
    the module finds its own tree's census regardless of the process's
    working directory.
    """
    this_file = pathlib.Path(__file__).resolve()
    team_dir = this_file.parent.parent.parent  # dashboard/ -> pgai_agent_kanban/ -> team/
    return team_dir / "config" / "dashboard_windows.census"


def load_census(path: pathlib.Path | None = None) -> list[Window]:
    """Parse the census file into an ordered list of Window records.

    Args:
        path: Census file to read. Defaults to :func:`default_census_path`.

    Returns:
        Windows in the order their W records appear in the file. A window
        with more than one variant (currently only drill-N: "release" and
        "document") appears as two separate Window entries sharing the same
        tmux_index.

    Raises:
        FileNotFoundError: the census file does not exist at *path*.
        CensusFormatError: the file exists but does not parse to a coherent
            window list (see CensusFormatError's docstring for the specific
            conditions checked).
    """
    census_path = path or default_census_path()
    if not census_path.exists():
        raise FileNotFoundError(f"census file not found: {census_path}")

    windows: list[Window] = []
    current_window: dict | None = None
    current_panes: list[Pane] = []

    def _flush() -> None:
        nonlocal current_window, current_panes
        if current_window is None:
            return
        expected = current_window["pane_count"]
        actual = len(current_panes)
        if actual != expected:
            raise CensusFormatError(
                f"window '{current_window['name']}' (variant="
                f"{current_window['variant']!r}, tmux_index="
                f"{current_window['tmux_index']}) declares pane_count="
                f"{expected} but {actual} P record(s) followed it"
            )
        windows.append(
            Window(
                tmux_index=current_window["tmux_index"],
                name=current_window["name"],
                variant=current_window["variant"],
                pane_count=expected,
                panes=tuple(current_panes),
            )
        )
        current_window = None
        current_panes = []

    for lineno, raw_line in enumerate(
        census_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        fields = raw_line.split("\t")
        kind = fields[0]

        if kind == "W":
            _flush()
            if len(fields) != 5:
                raise CensusFormatError(
                    f"{census_path}:{lineno}: W record has {len(fields)} "
                    f"fields, expected 5: {raw_line!r}"
                )
            _, idx_str, name, variant, count_str = fields
            current_window = {
                "tmux_index": _parse_int(idx_str, census_path, lineno, "tmux_index"),
                "name": name,
                "variant": variant,
                "pane_count": _parse_int(count_str, census_path, lineno, "pane_count"),
            }
            current_panes = []
            continue

        if kind == "P":
            if len(fields) != 7:
                raise CensusFormatError(
                    f"{census_path}:{lineno}: P record has {len(fields)} "
                    f"fields, expected 7: {raw_line!r}"
                )
            if current_window is None:
                raise CensusFormatError(
                    f"{census_path}:{lineno}: P record with no preceding W "
                    f"record: {raw_line!r}"
                )
            _, idx_str, variant, seq_str, position, script, cmd = fields
            tmux_index = _parse_int(idx_str, census_path, lineno, "tmux_index")
            if tmux_index != current_window["tmux_index"] or variant != current_window["variant"]:
                raise CensusFormatError(
                    f"{census_path}:{lineno}: P record (tmux_index="
                    f"{tmux_index}, variant={variant!r}) does not match its "
                    f"preceding W record (tmux_index="
                    f"{current_window['tmux_index']}, variant="
                    f"{current_window['variant']!r})"
                )
            current_panes.append(
                Pane(
                    tmux_index=tmux_index,
                    variant=variant,
                    pane_seq=_parse_int(seq_str, census_path, lineno, "pane_seq"),
                    position=position,
                    script=script,
                    standalone_cmd=cmd,
                )
            )
            continue

        # Any other first field (blank line, comment line, stray text) is
        # not a data line -- ignored per the census file's own parse rule.

    _flush()
    return windows


def _parse_int(value: str, path: pathlib.Path, lineno: int, field_name: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise CensusFormatError(
            f"{path}:{lineno}: {field_name} value {value!r} is not an integer"
        ) from exc


def load_pane_cadence(path: pathlib.Path | None = None) -> dict[tuple[int, str, int], PaneCadence]:
    """Parse the census's C records, keyed by (tmux_index, variant, pane_seq).

    A record with the wrong field count or a non-integer index raises
    CensusFormatError; a key that appears twice keeps its first record
    (pane_hidden_declaration_findings reports the duplicate).
    """
    census_path = path or default_census_path()
    if not census_path.exists():
        raise FileNotFoundError(f"census file not found: {census_path}")
    records: dict[tuple[int, str, int], PaneCadence] = {}
    for lineno, raw_line in enumerate(census_path.read_text(encoding="utf-8").splitlines(), start=1):
        fields = raw_line.split("\t")
        if fields[0] != "C":
            continue
        if len(fields) != 6:
            raise CensusFormatError(
                f"{census_path}:{lineno}: C record has {len(fields)} fields, expected 6: {raw_line!r}"
            )
        _, idx_str, variant, seq_str, cadence, when_hidden = fields
        record = PaneCadence(
            tmux_index=_parse_int(idx_str, census_path, lineno, "tmux_index"),
            variant=variant,
            pane_seq=_parse_int(seq_str, census_path, lineno, "pane_seq"),
            cadence=cadence.strip(),
            when_hidden=when_hidden.strip(),
        )
        records.setdefault((record.tmux_index, record.variant, record.pane_seq), record)
    return records


def pane_hidden_declaration_findings(path: pathlib.Path | None = None) -> list[str]:
    """Check that every census pane declares what it does while hidden.

    Every P record needs exactly one C record whose when_hidden is "pause",
    or "always-on" for a pane in window 0 (the one always-on status
    surface, at most one of them). The cadence must be a positive whole
    number of seconds or one of "refresh", "stream", "-". A C record that
    names no P record is reported too. Returns one plain-language message
    per finding; an empty list means the census is fully declared.
    """
    census_path = path or default_census_path()
    windows = load_census(census_path)
    cadences = load_pane_cadence(census_path)
    findings: list[str] = []

    seen_keys: dict[tuple[int, str, int], int] = {}
    for raw_line in census_path.read_text(encoding="utf-8").splitlines():
        fields = raw_line.split("\t")
        if fields[0] == "C" and len(fields) == 6:
            try:
                key = (int(fields[1]), fields[2], int(fields[3]))
            except ValueError:
                continue
            seen_keys[key] = seen_keys.get(key, 0) + 1
    for key, count in seen_keys.items():
        if count > 1:
            findings.append(f"pane {key} has {count} C records; declare it exactly once")

    pane_keys = set()
    always_on: list[tuple[int, str, int]] = []
    for window in windows:
        for pane in window.panes:
            key = (pane.tmux_index, pane.variant, pane.pane_seq)
            pane_keys.add(key)
            label = f"window {window.name} (index {pane.tmux_index}, variant {pane.variant}) pane {pane.pane_seq}"
            record = cadences.get(key)
            if record is None:
                findings.append(
                    f"{label} has no C record; add 'C\t{pane.tmux_index}\t{pane.variant}\t"
                    f"{pane.pane_seq}\t<cadence>\tpause' directly after its P record"
                )
                continue
            if record.cadence not in SYMBOLIC_CADENCES and not (
                record.cadence.isdigit() and int(record.cadence) > 0
            ):
                findings.append(
                    f"{label} declares cadence {record.cadence!r}; expected whole seconds, "
                    f"'refresh', 'stream' or '-'"
                )
            if record.when_hidden == WHEN_HIDDEN_PAUSE:
                continue
            if record.when_hidden == WHEN_HIDDEN_ALWAYS_ON:
                if pane.tmux_index != ALWAYS_ON_WINDOW_INDEX:
                    findings.append(
                        f"{label} declares always-on, which only the window-{ALWAYS_ON_WINDOW_INDEX} "
                        f"status surface may; declare it 'pause'"
                    )
                else:
                    always_on.append(key)
                continue
            findings.append(
                f"{label} declares when_hidden {record.when_hidden!r}; expected 'pause' "
                f"(or 'always-on' for the window-{ALWAYS_ON_WINDOW_INDEX} status surface)"
            )
    if len(always_on) > 1:
        findings.append(
            f"{len(always_on)} panes declare always-on {sorted(always_on)}; "
            f"only one status surface may stay on while hidden"
        )
    for key in sorted(set(cadences) - pane_keys):
        findings.append(f"C record for pane {key} names no P record; remove it or fix its key")
    return findings
