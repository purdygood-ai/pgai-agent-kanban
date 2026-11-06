"""
provider_signatures.py — the reader of the provider-error signature table.

``config/provider-error-signatures.txt`` (``team/config/`` in a checkout) is
the one list of provider error signatures. Each line names a class
(``transient``, ``refusal`` or ``billing``), a label and an extended regular
expression; the table's own header documents the line format, what each
class means and the action it takes. This module is the only code that parses
it. Python callers import it; bash callers go through
``scripts/lib/provider_signatures.sh``, which runs the ``patterns`` subcommand
below once and matches with ``grep -E``.

Classifying a text
------------------
:func:`classify` returns the class of the highest-precedence signature found
in the text, ``billing`` over ``refusal`` over ``transient``
(:data:`CLASS_PRECEDENCE`), or ``None`` when nothing matches. Every pattern
is matched case-insensitively, with ``^`` and ``$`` at line boundaries, the
way ``grep -iE`` reads it one line at a time.

Self-location
-------------
The table is found beside this module's own install: ``<root>/config/``
next to ``<root>/pgai_agent_kanban/``, the same layout in the live install
and in a checkout's ``team/`` directory, so each tree reads its own copy. A
missing or malformed table raises :class:`SignatureTableError`, whose message
is the dual-channel error contract naming the path.

Usage (CLI; scripts/lib/provider_signatures.sh calls ``patterns``)::

    python3 -m pgai_agent_kanban.lib.provider_signatures patterns [--table PATH]
    python3 -m pgai_agent_kanban.lib.provider_signatures classify [--table PATH] [FILE]

``patterns`` prints one ``<class>\\t<pattern>`` line per class, in precedence
order, the pattern being every signature of that class joined into one
alternation. ``classify`` prints the class of FILE (standard input when
omitted) and exits 0, or prints nothing and exits 1 when no signature
matches. Either exits 2 when the table (or FILE) cannot be read.
"""

from __future__ import annotations

import argparse
import dataclasses
import functools
import pathlib
import re
import sys
from collections.abc import Iterable

# Run by file path, this module has no parent package; put its own package
# root first on sys.path so the imports below resolve from the same tree.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.lib.error_contract import format_error_contract  # noqa: E402

__all__ = [
    "CLASS_PRECEDENCE",
    "DEFAULT_TABLE_PATH",
    "Signature",
    "SignatureTableError",
    "load_table",
    "first_match",
    "classify",
    "class_pattern",
    "main",
]

#: The signature classes, highest precedence first: a text carrying more than
#: one class is classed by the first of them it carries.
CLASS_PRECEDENCE: tuple[str, ...] = ("billing", "refusal", "transient")

#: The table this tree reads: config/ beside this package.
DEFAULT_TABLE_PATH: pathlib.Path = (
    pathlib.Path(__file__).resolve().parents[2] / "config" / "provider-error-signatures.txt"
)

EXIT_MATCH = 0
EXIT_NO_MATCH = 1
EXIT_TABLE_ERROR = 2

_LINE_RE = re.compile(r"^\s*(\S+)\s+(\S+)\s+(\S.*?)\s*$")
_LABEL_RE = re.compile(r"^[a-z0-9_]+$")
# Constructs grep -E and Python's re read differently (or one of them not at
# all): escape classes, (?...) groups and lazy quantifiers.
_NON_PORTABLE_RE = re.compile(r"\\[dDsSwW]|\(\?|[*+?}]\?")


class SignatureTableError(RuntimeError):
    """The signature table is missing, unreadable or malformed.

    The message is the three-line error contract (error, message, remedy)
    and always names the table's path.
    """


@dataclasses.dataclass(frozen=True)
class Signature:
    """One table line: its class, label, pattern and line number."""

    klass: str
    label: str
    pattern: str
    line: int
    regex: re.Pattern[str] = dataclasses.field(compare=False, repr=False)


def _table_error(path: pathlib.Path, problem: str) -> SignatureTableError:
    # BLOCK-WORTHY (docs/blocking-site-census.md): verification unable to
    # run -- without the table no reader can tell a transient provider error
    # from a refusal, so every retry decision would be a guess.
    # CENSUS-ANCHOR: provider-signatures-table-unreadable-01
    return SignatureTableError(
        format_error_contract(
            "provider_signatures.table_unreadable",
            f"The provider-error signature table {path} {problem}. The wake's "
            "post-exit check, the OVERWATCH transient check and the block-evidence "
            "classifier all read their signatures from it.",
            f"restore {path} from the release that installed this tree "
            "(or from version control in a checkout), then run "
            "python3 -m pgai_agent_kanban.lib.provider_signatures patterns",
        )
    )


def _parse_line(path: pathlib.Path, number: int, raw: str) -> Signature:
    match = _LINE_RE.match(raw)
    if not match:
        raise _table_error(path, f"line {number} is not '<class> <label> <pattern>': {raw.strip()!r}")
    klass, label, pattern = match.groups()
    if klass not in CLASS_PRECEDENCE:
        raise _table_error(path, f"line {number} has class {klass!r}; accepted: {', '.join(CLASS_PRECEDENCE)}")
    if not _LABEL_RE.match(label):
        raise _table_error(path, f"line {number} has label {label!r}; a label is [a-z0-9_]+")
    if _NON_PORTABLE_RE.search(pattern):
        raise _table_error(
            path,
            f"line {number} pattern {pattern!r} uses a construct grep -E does not read "
            "as Python does (\\d, \\s, \\w, (?...) or a lazy quantifier)",
        )
    try:
        regex = re.compile(pattern, re.IGNORECASE | re.MULTILINE)
    except re.error as exc:
        raise _table_error(path, f"line {number} pattern {pattern!r} does not compile: {exc}") from None
    return Signature(klass=klass, label=label, pattern=pattern, line=number, regex=regex)


@functools.lru_cache(maxsize=8)
def _load(path_text: str) -> tuple[Signature, ...]:
    path = pathlib.Path(path_text)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise _table_error(path, "does not exist") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise _table_error(path, f"cannot be read ({exc})") from None
    rows = []
    for number, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        rows.append(_parse_line(path, number, raw))
    if not rows:
        raise _table_error(path, "lists no signature")
    return tuple(rows)


def load_table(path: pathlib.Path | str | None = None) -> tuple[Signature, ...]:
    """Return every signature in the table, in file order.

    *path* defaults to :data:`DEFAULT_TABLE_PATH`. Raises
    :class:`SignatureTableError` when the table is missing, unreadable,
    empty, or has a line that is not a valid signature.
    """
    return _load(str(path if path is not None else DEFAULT_TABLE_PATH))


def first_match(
    text: str,
    classes: Iterable[str] = CLASS_PRECEDENCE,
    table: tuple[Signature, ...] | None = None,
) -> Signature | None:
    """The first signature, in table order, of one of *classes* found in *text*."""
    wanted = frozenset(classes)
    for signature in table if table is not None else load_table():
        if signature.klass in wanted and signature.regex.search(text):
            return signature
    return None


def classify(text: str, table: tuple[Signature, ...] | None = None) -> str | None:
    """The class of *text*: the highest-precedence class it carries, or ``None``."""
    rows = table if table is not None else load_table()
    for klass in CLASS_PRECEDENCE:
        if first_match(text, (klass,), rows) is not None:
            return klass
    return None


def class_pattern(klass: str, table: tuple[Signature, ...] | None = None) -> str:
    """Every pattern of *klass* joined into one extended-regex alternation.

    Each pattern keeps its own group, ``(p1)|(p2)|...``, so an alternation
    inside one pattern stays inside it. Raises ``ValueError`` for a class the
    table does not define.
    """
    if klass not in CLASS_PRECEDENCE:
        raise ValueError(f"unknown signature class {klass!r}; accepted: {', '.join(CLASS_PRECEDENCE)}")
    rows = table if table is not None else load_table()
    return "|".join(f"({row.pattern})" for row in rows if row.klass == klass)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="provider_signatures",
        description=(
            "Read the provider-error signature table (config/provider-error-signatures.txt "
            "beside this package): print each class's combined pattern, or classify a text."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("patterns", "print '<class>\\t<pattern>' per class, highest precedence first"),
        ("classify", "print the class of FILE (stdin when omitted); exit 1 when nothing matches"),
    ):
        command = sub.add_parser(name, help=help_text, description=help_text)
        command.add_argument(
            "--table",
            default=None,
            help=f"read this table instead of the installed one (default {DEFAULT_TABLE_PATH})",
        )
        if name == "classify":
            command.add_argument("file", nargs="?", default=None, help="text to classify (default: stdin)")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI. Exit 0 on success (a match, for ``classify``), 1 when
    ``classify`` finds no signature, 2 when the table or FILE cannot be read."""
    args = _parser().parse_args(argv)
    try:
        table = load_table(args.table)
    except SignatureTableError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_TABLE_ERROR

    if args.command == "patterns":
        for klass in CLASS_PRECEDENCE:
            print(f"{klass}\t{class_pattern(klass, table)}")
        return EXIT_MATCH

    if args.file is None:
        text = sys.stdin.read()
    else:
        try:
            text = pathlib.Path(args.file).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            print(f"provider_signatures: cannot read {args.file}: {exc}", file=sys.stderr)
            return EXIT_TABLE_ERROR
    klass = classify(text, table)
    if klass is None:
        return EXIT_NO_MATCH
    print(klass)
    return EXIT_MATCH


if __name__ == "__main__":
    raise SystemExit(main())
