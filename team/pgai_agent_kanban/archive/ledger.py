r"""
ledger.py — ``ARCHIVE-LEDGER.tsv``, the record of every archive move and restore.

Each project's archive tree holds one ledger at
``<kanban_root>/archive/<project>/ARCHIVE-LEDGER.tsv``. It is append-only:
one row per item moved, written right after the move, never rewritten.
There is no header row. Each row is six tab-separated fields::

    <utc>\t<release>\t<category>\t<id>\t<from>\t<to>

``utc``       When the move finished, ``YYYY-MM-DDThh:mm:ss+00:00``
              (``lib.timeutil.utc_now_iso``).
``release``   The project's last released version when the move ran
              (``lib.released_version.last_released_version``: ``vX.Y.Z``,
              ``v0.0.0`` before the first release).
``category``  ``bugs``, ``priority``, ``requirements`` or ``tasks`` for an
              item moved into the archive; :data:`CATEGORY_RESTORE`
              (``restore``) for an item moved back into the live project.
``id``        The item's canonical ID: ``BUG-NNNN``, ``PRIORITY-NNNN``, the
              requirements file name ``vX.Y.Z-slug.md``, or the task ID. A
              restore row's category says only that it is a restore; the
              ID's shape says what kind of item came back.
``from``      Where the item was, relative to the kanban root
              (``projects/<project>/bugs/BUG-NNNN-<slug>.md``).
``to``        Where it went, relative to the kanban root
              (``archive/<project>/bugs/BUG-NNNN-<slug>.md``). A restore row
              has the two the other way round.

Rows are per item. The queue lines and training logs that travel with an
item get no row of their own: the queue lines are kept, markers
unchanged, in ``archive/<project>/tasks/queues/<same file name>``, and
the training logs at ``archive/<project>/logs/training/<role>/``, beside
the item's row.

A field never holds a tab or a line break: either is written as one
space. Reading is liberal: blank lines and lines without six fields are
skipped, and trailing whitespace is ignored.

Public API
----------
``LedgerRow``                   One row, with ``to_line()`` and ``parse()``.
``append_row(ledger, row)``     Append one row and fsync it.
``read_rows(ledger)``           Every well-formed row, in file order.
``CATEGORY_RESTORE``            The category of a restore row.
"""

from __future__ import annotations

import dataclasses
import os
import pathlib
import re
from typing import Optional, Union

__all__ = ["CATEGORY_RESTORE", "LEDGER_FIELDS", "LedgerRow", "append_row", "read_rows"]

PathLike = Union[str, "os.PathLike[str]"]

#: The category written on a row that records an item moved back live.
CATEGORY_RESTORE = "restore"

#: Column names, in order.
LEDGER_FIELDS = ("utc", "release", "category", "id", "from", "to")

_FIELD_BREAK_RE = re.compile(r"[\t\r\n]+")


def _clean(value: str) -> str:
    """*value* with every tab or line break replaced by one space."""
    return _FIELD_BREAK_RE.sub(" ", str(value)).strip()


@dataclasses.dataclass(frozen=True)
class LedgerRow:
    """One ledger row. See the module docstring for each field."""

    utc: str
    release: str
    category: str
    item_id: str
    source: str
    destination: str

    def to_line(self) -> str:
        """The row as one tab-separated line, without a line ending."""
        return "\t".join(
            _clean(value)
            for value in (self.utc, self.release, self.category, self.item_id, self.source, self.destination)
        )

    @classmethod
    def parse(cls, line: str) -> Optional["LedgerRow"]:
        """The row *line* holds, or ``None`` when it is not six tab-separated fields."""
        fields = line.rstrip().split("\t")
        if len(fields) != len(LEDGER_FIELDS) or not all(field.strip() for field in fields):
            return None
        return cls(*(field.strip() for field in fields))


def append_row(ledger: PathLike, row: LedgerRow) -> None:
    """Append *row* to *ledger* and fsync it.

    The ledger and its directory are created when missing. The row is one
    ``O_APPEND`` write, so rows from two writers never interleave.

    Raises:
        OSError: The ledger cannot be created or written.
    """
    path = pathlib.Path(ledger)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, (row.to_line() + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def read_rows(ledger: PathLike) -> tuple[LedgerRow, ...]:
    """Every well-formed row of *ledger*, in file order; ``()`` when it is absent.

    Raises:
        OSError: The ledger exists but cannot be read.
    """
    path = pathlib.Path(ledger)
    if not path.exists():
        return ()
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        row = LedgerRow.parse(line)
        if row is not None:
            rows.append(row)
    return tuple(rows)
