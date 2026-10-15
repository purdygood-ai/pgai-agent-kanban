"""
refusal_ledger.py — the most-recent-refusal record, per project.

A refusal that only narrates to a rotating wake log is a verdict nobody
reads. Every refusal already speaks dual-channel the moment it happens
(``pgai_emit_error_contract`` / ``emit_error_contract`` — the stable machine
key, the plain-language message, the runnable remedy), and this module is
where that same moment is also recorded to a durable, single-record file per
project: ``<kanban_root>/projects/<project>/refusal-ledger.md``. The
operator's question is never "what happened at 03:14 last Tuesday" — it is
"why is this lane not moving right now" — and the answer is almost always
the most recent refusal. One read of this file answers that question
without opening a log.

MOST-RECENT ONLY, by design: this is not an audit log. Every write
REPLACES the file's entire content with exactly one record. The wake log
already keeps history; this file exists to answer "right now," not
"ever." There is no append path here — contrast
``pgai_agent_kanban.deep_verify.ledger``, which is deliberately append-only
for a different question ("when did this project last build, and did it
work").

Field discipline (mirrors team/scripts/lib/lock_provenance.sh's four-field
rationale for the same reason): a record carries exactly project, surface,
timestamp, error, message, remedy, and context — the three dual-channel
fields plus "who raised it," "when," and "from which estate." Nothing
else. No environment dump, no command line, no token, no credential ever
belongs in a record — the three dual-channel fields are already what the
refusal prints to stderr, so recording them creates no new leak surface,
and the writer never accepts or serializes any other kind of value.

Context: a refusal fired while the test suite is running must
be distinguishable from one the install's own operation raised, and a
suite run must never be mistaken for the fleet's real most-recent
refusal. Every write stamps ``context`` to exactly ``"install"`` or
``"test"`` — never left to the caller to compute, never guessed by a
reader. Both entry points that real call sites use (``record_refusal()``
below, and this module's own CLI ``record`` subcommand, which is the one
code path both language bindings share — see "Usage (CLI)") stamp it via
``_detect_context()``, which reads ``PYTEST_CURRENT_TEST`` — the
environment variable pytest's own core (not a plugin) sets for the
duration of every test's setup/call/teardown phases. That variable is an
ordinary environment variable, so it propagates for free through every
subprocess boundary a refusal's write path crosses in this framework: a
Python test calling ``record_refusal()`` directly, a Python test shelling
out to a bash script that sources ``refusal_ledger.sh``
(``team/tests/unit/shell_harness.py``'s ``run_bash``, which inherits
``os.environ``), and that bash script's own shell-out to this module's
CLI via ``pp_run_ops`` — the same signal, unchanged, at every hop. A
record written before this field existed has no ``context`` bullet at
all; ``read_latest()`` reports ``"unknown"`` for it rather than guessing
``"install"`` (see ``read_latest()``'s docstring).

Two operations, both used the same way ``pgai_agent_kanban.deep_verify.ledger``
uses append/latest, except the write here overwrites rather than appends
(atomic: ``pgai_agent_kanban.lib.atomic_write`` — temp file in the same
directory + fsync + os.replace):

  - write_latest(ledger_path, record) — replaces the ledger's entire
    content with exactly this one record. A second call with a different
    record REPLACES the first; the file never grows.
  - read_latest(ledger_path) — the one reader every consumer should call.
    Returns the current record as a dict, or None when the project has
    never recorded a refusal. Raises RefusalLedgerError when the file has
    non-whitespace content that does not parse, or is missing a required
    field — a malformed ledger refuses rather than silently reporting
    "never recorded."

record_refusal() is the safe, best-effort entry point the shared dual-channel
emitters (team/scripts/lib/error_contract.sh's pgai_emit_error_contract and
this package's sibling team/pgai_agent_kanban/lib/error_contract.py's
emit_error_contract) call after printing. It NEVER raises and NEVER changes
what the caller already did: a refusal's exit status and stdout are never
touched by whether the ledger write succeeded (team/SOP-core.md "Gates and
Refusals" — recording is diagnostics, not a gate). Its stderr gains one
narration line in exactly one case, the unregistered project below; every
other skip is silent. These cases return False without touching the
filesystem at all, by design, not by accident:

  - project is empty/None (no explicit project and no ambient
    PGAI_PROJECT_NAME) — a refusal raised with no project in scope (a
    kanban-root-level refusal, or one that fired before a project was
    resolved) goes NOWHERE rather than guessing which project's directory
    to write into.
  - the kanban root itself cannot be resolved (PGAI_AGENT_KANBAN_ROOT_PATH
    unset, or any other resolution failure) — recording is auxiliary; a
    refusal firing outside a fully-configured kanban environment (a
    standalone script run, a unit test) must still refuse exactly as
    documented, with nothing extra required of the caller.
  - the resolved kanban root does not exist as a directory — a refusal
    whose entire subject is "this directory does not exist" (a
    kanban-root-missing guard, for example) must not fabricate the very
    directory it is refusing on. Directories BENEATH an already-existing
    root (projects/<project>/) are still created normally; only the root
    itself is guarded.
  - the root carries a projects.cfg that does not register the project
    (or that cannot be read) — the ledger never creates
    projects/<project>/ for a name the registry does not know, so a typo, a
    test-fixture name, or a process that resolved the operator's live root
    by mistake leaves no folder behind. This one case narrates a single
    ``refusal_ledger: ...`` line on stderr naming the project and the
    registry, so the skip is visible rather than silent. A root with no
    projects.cfg at all (a single-project or not-yet-registered estate)
    has no registry to consult and records as before.

Usage (CLI, mirrors pgai_agent_kanban.deep_verify.ledger's CLI shape — the
bash adapter team/scripts/lib/refusal_ledger.sh invokes this so both
languages write through the exact same code path and therefore the exact
same record shape):

    python3 refusal_ledger.py record [--kanban-root <root>] <ledger_path> <project> <error> <message> <remedy> <surface>
    python3 refusal_ledger.py latest <ledger_path>

    The 'record' subcommand always stamps context itself (see "Context"
    above) — there is no CLI flag for it, so a caller (bash or Python)
    never needs to compute or pass it.

    With --kanban-root, 'record' first applies the same registration check
    record_refusal() applies: when <root>/projects.cfg exists and does not
    register <project>, it writes nothing, prints one ``refusal_ledger:``
    narration line on stderr and exits 3 (EXIT_UNREGISTERED_PROJECT), so a
    caller can tell this skip from a write failure (exit 1). Without the
    flag no registration check runs — the raw path-addressed write.

Usage (import):

    from pgai_agent_kanban.lib.refusal_ledger import (
        RefusalRecord, write_latest, read_latest, record_refusal,
    )
    record_refusal(
        "gate.rc-branch-mismatch",
        "the release stopped because ...",
        "git checkout rc/v1.2.3 && re-run the release script",
        project="pgai-agent-kanban",
        surface="release.sh:git_step",
    )
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import sys
from dataclasses import dataclass, fields

# Run by file path (``python3 .../refusal_ledger.py``) or loaded from a file
# outside its package, this module has no parent package and the interpreter
# puts only its own directory on sys.path, so the package import below would
# not resolve. Put this file's own package root -- the directory holding the
# pgai_agent_kanban/ this file sits in -- at the front of sys.path so the
# import resolves from the same tree, never another. Imported as a package
# member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.lib.atomic_write import atomic_write  # noqa: E402

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class RefusalLedgerError(Exception):
    """Raised for structural problems with the ledger file: content that
    does not parse into a record at all, or a record missing a required
    field. Never raised by record_refusal() — that entry point swallows
    every error (see module docstring)."""


# ---------------------------------------------------------------------------
# Record model
# ---------------------------------------------------------------------------

_RECORD_HEADER_RE = re.compile(
    r"^##\s+Refusal:\s*(?P<timestamp>\S+)\s+—\s+(?P<project>\S+)\s+—\s+"
    r"(?P<surface>.+?)\s*$"
)
_CONTEXT_RE = re.compile(r"^-\s*Context:\s*(?P<value>.*)$")
_ERROR_RE = re.compile(r"^-\s*Error:\s*(?P<value>.*)$")
_MESSAGE_RE = re.compile(r"^-\s*Message:\s*(?P<value>.*)$")
_REMEDY_RE = re.compile(r"^-\s*Remedy:\s*(?P<value>.*)$")

# The six fields every record has always carried. Deliberately excludes
# "context" -- read_latest()'s "is this record malformed" check is scoped
# to this tuple alone, so a record written before the context field
# existed is never mistaken for a malformed one (see read_latest()).
_REQUIRED_FIELDS = ("timestamp", "project", "surface", "error", "message", "remedy")

# Every field a freshly WRITTEN record must carry, context included --
# write_latest()/_validate_record() refuses a blank/missing one of these.
# A pre-existing record on disk (parsed by read_latest()) is held to
# _REQUIRED_FIELDS only; this longer tuple never governs reading.
_WRITE_REQUIRED_FIELDS = _REQUIRED_FIELDS + ("context",)

# The only two values a freshly written record's context may hold (see
# _detect_context()). "unknown" is a read-side fallback for a pre-existing
# record with no context bullet at all -- never a value the writer stamps.
_VALID_CONTEXTS = frozenset({"install", "test"})
_UNKNOWN_CONTEXT = "unknown"


@dataclass
class RefusalRecord:
    """The single most-recent refusal for one project.

    All seven fields are required — a record missing the remedy is worse
    than no record at all: it tells the operator something is wrong and
    not what to do. ``context`` is the one exception on the READ side
    only: a record written before this field existed parses with
    ``context`` absent, and read_latest() reports that as ``"unknown"``
    rather than raising or guessing ``"install"``.
    """

    timestamp: str
    project: str
    surface: str
    error: str
    message: str
    remedy: str
    context: str

    @classmethod
    def from_dict(cls, data: dict) -> "RefusalRecord":
        """Build a record from a plain dict (parsed JSON, say), ignoring unknown keys."""
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def to_dict(self) -> dict:
        """Return the record as a dict keyed by field name: timestamp, project, surface, error, message, remedy, context."""
        return {f.name: getattr(self, f.name) for f in fields(self)}


def _single_line(value: str) -> str:
    """Collapse any stray newline -- this ledger's field-parse format is one
    value per line; a multi-line value would corrupt the record shape."""
    return " ".join(str(value).split("\n"))


def _validate_record(record: RefusalRecord) -> None:
    blank = [
        name for name in _WRITE_REQUIRED_FIELDS if not str(getattr(record, name) or "").strip()
    ]
    if blank:
        raise RefusalLedgerError(
            "refusal_ledger: refusing to write a record with blank/missing "
            f"field(s): {', '.join(blank)} — all seven fields (timestamp, project, "
            "surface, error, message, remedy, context) are required"
        )
    if record.context not in _VALID_CONTEXTS:
        raise RefusalLedgerError(
            f"refusal_ledger: refusing to write a record with context={record.context!r} "
            f"— context must be one of {sorted(_VALID_CONTEXTS)}"
        )


def format_record(record: RefusalRecord) -> str:
    """Render *record* as a self-contained markdown block (no leading/
    trailing blank lines -- the caller controls spacing)."""
    return "\n".join(
        [
            f"## Refusal: {record.timestamp} — {record.project} — "
            f"{_single_line(record.surface)}",
            f"- Context: {_single_line(record.context)}",
            f"- Error: {_single_line(record.error)}",
            f"- Message: {_single_line(record.message)}",
            f"- Remedy: {_single_line(record.remedy)}",
        ]
    )


def _ledger_header(ledger_path: str) -> str:
    """Standard file header. Project name is derived from the ledger path's
    parent directory basename (the standard projects/<name>/
    refusal-ledger.md convention); falls back to a generic title
    otherwise."""
    project_name = os.path.basename(os.path.dirname(os.path.abspath(ledger_path)))
    title = f"# Refusal Ledger — {project_name}" if project_name else "# Refusal Ledger"
    return (
        f"{title}\n\n"
        "Most-recent-only. Every write REPLACES this file's entire content "
        "with exactly one record — the most recent refusal a dual-channel "
        "emitter recorded for this project. This is not an audit log; the "
        "wake log already keeps history (see team/SOP-core.md 'Gates and "
        "Refusals'). Read the current record with "
        "pgai_agent_kanban.lib.refusal_ledger.read_latest() rather than "
        "re-deriving it from this file's raw text.\n"
    )


def _write_ledger_file(target: str, content: str) -> None:
    """Atomically replace *target* with *content* via the shared writer,
    creating its directory first. A write failure is a
    RefusalLedgerError.
    """
    os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
    try:
        atomic_write(target, content, prefix=".refusal_ledger_tmp_")
    except OSError as exc:
        raise RefusalLedgerError(f"refusal_ledger: failed writing {target}: {exc}") from exc


# ---------------------------------------------------------------------------
# write_latest / read_latest — the raw, raising API
# ---------------------------------------------------------------------------


def write_latest(ledger_path: str, record: RefusalRecord) -> None:
    """Replace the ledger at *ledger_path* with exactly *record*, creating
    the file (with its standard header) if it does not yet exist. The write
    is atomic: a temp file is written in the same directory and renamed
    over the target, so a concurrent reader never observes a half-written
    file — and never overwrites a prior record with a merge; the whole
    file's content is always exactly the header plus this one record.

    Raises RefusalLedgerError when *record* has any blank/missing field.
    """
    _validate_record(record)
    new_content = _ledger_header(ledger_path) + "\n" + format_record(record) + "\n"
    _write_ledger_file(ledger_path, new_content)


def _parse_block(text: str) -> dict:
    lines = text.splitlines()
    # Find the header line (the block may be preceded by the file's own
    # title/prose header when read_latest reads the whole file).
    header_idx = None
    header_match = None
    for idx, line in enumerate(lines):
        m = _RECORD_HEADER_RE.match(line)
        if m:
            header_idx = idx
            header_match = m
            break
    if header_match is None:
        return {}

    result = {
        "timestamp": header_match.group("timestamp"),
        "project": header_match.group("project"),
        "surface": header_match.group("surface"),
        "error": None,
        "message": None,
        "remedy": None,
        # Absent on a record written before this field existed -- left as
        # None here (never guessed), normalised to "unknown" by
        # read_latest() after its malformed-record check runs.
        "context": None,
    }
    for line in lines[header_idx + 1 :]:
        context_match = _CONTEXT_RE.match(line)
        error_match = _ERROR_RE.match(line)
        message_match = _MESSAGE_RE.match(line)
        remedy_match = _REMEDY_RE.match(line)
        if context_match:
            result["context"] = context_match.group("value").strip()
        elif error_match:
            result["error"] = error_match.group("value").strip()
        elif message_match:
            result["message"] = message_match.group("value").strip()
        elif remedy_match:
            result["remedy"] = remedy_match.group("value").strip()
    return result


def read_latest(ledger_path: str) -> dict | None:
    """Return the current record at *ledger_path* as a dict, or None when
    the project has never recorded a refusal (no file, or an empty one).

    Raises RefusalLedgerError when:
      - the file has non-whitespace content but no recognizable
        '## Refusal: ...' header (malformed), or
      - the record is missing any of its six original required fields (a
        hand-edited or corrupted ledger) -- a record missing the remedy is
        worse than no record (see module docstring), so a malformed
        current record refuses rather than silently reporting "never
        recorded" or handing back a fabricated field.

    Never raises over a missing ``context`` alone: a record written
    before that field existed has no ``- Context:`` bullet at all, which
    is expected of an old record rather than evidence of corruption. The
    returned dict's ``"context"`` key is ``"unknown"`` in that case --
    never guessed as ``"install"`` (see the module docstring's "Context" section).
    """
    if not os.path.isfile(ledger_path):
        return None
    with open(ledger_path, "r", encoding="utf-8") as handle:
        text = handle.read()
    if not text.strip():
        return None

    record = _parse_block(text)
    if not record:
        raise RefusalLedgerError(
            f"refusal_ledger: {ledger_path} has content but no recognizable "
            "'## Refusal: ...' record header -- the file is malformed"
        )

    missing = [name for name in _REQUIRED_FIELDS if not record.get(name)]
    if missing:
        raise RefusalLedgerError(
            f"refusal_ledger: record in {ledger_path} is missing required "
            f"field(s): {', '.join(missing)} -- ledger is malformed"
        )
    record["context"] = record.get("context") or _UNKNOWN_CONTEXT
    return record


# ---------------------------------------------------------------------------
# Path convention helper
# ---------------------------------------------------------------------------


def ledger_path_for_project(kanban_root: str, project_name: str) -> str:
    """Return the standard ledger path:
    <kanban_root>/projects/<project_name>/refusal-ledger.md."""
    return os.path.join(kanban_root, "projects", project_name, "refusal-ledger.md")


# ---------------------------------------------------------------------------
# Registration check — the ledger never creates a folder for a project the
# root's registry does not name.
# ---------------------------------------------------------------------------

_COLON_LEGACY_ENTRY_RE = re.compile(r"^\s*(?P<name>[^\s:#\[][^\s:]*)\s*:")


def _colon_legacy_names(registry: str) -> set[str] | None:
    """Project names in a deprecated colon-format projects.cfg
    (``name:priority[:color]`` per line), or None when the file is not in
    that format either (its first non-blank, non-comment line opens an INI
    section) or cannot be read. Liberal on whitespace around the name, as
    the bash registry helpers are."""
    try:
        with open(registry, "r", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    names: set[str] = set()
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("["):
            return None
        match = _COLON_LEGACY_ENTRY_RE.match(line)
        if match:
            names.add(match.group("name"))
    return names


#: Exit status of the 'record' CLI when --kanban-root names a root whose
#: projects.cfg does not register the project: nothing was written. Distinct
#: from 1 (a write failure) so the bash adapter can forward the narration
#: for this case alone and stay silent for every other failure.
EXIT_UNREGISTERED_PROJECT = 3


def unregistered_project_narration(kanban_root: str, project: str) -> str | None:
    """Return why a ledger write for *project* under *kanban_root* is refused,
    or None when the write may proceed.

    The write may proceed when ``<kanban_root>/projects.cfg`` registers
    *project* (an ``[project:<name>]`` section), and when there is no
    projects.cfg at all -- a root with no registry (a single-project estate,
    a bare test sandbox) has nothing to check against and records as it
    always has.

    The write is refused when projects.cfg exists and does not register
    *project* (read as INI, or in the deprecated colon format the bash
    registry helpers still accept), or exists but cannot be read in either
    format: in both cases the
    registry cannot vouch for the name, and writing would create
    ``projects/<project>/`` for a folder no registered project owns -- the
    exact litter a typo or a test resolving the operator's live root by
    mistake would otherwise leave behind.

    The returned text is one line, ready to print on stderr. It names the
    project, the registry file, and what was (not) done.
    """
    # CENSUS CLASS CONVERT (docs/blocking-site-census.md): this refusal only
    # skips a diagnostic write and narrates; the caller's own refusal, exit
    # status and stdout are untouched, so nothing here can stop the loop.
    from pgai_agent_kanban.lib.ini import IniError, projects_registry

    registry = os.path.join(kanban_root, "projects.cfg")
    if not os.path.isfile(registry):
        return None
    try:
        names = {entry.name for entry in projects_registry(kanban_root)}
    except (IniError, OSError, UnicodeDecodeError) as exc:
        names = _colon_legacy_names(registry)
        if names is None:
            return (
                f"refusal_ledger: not recording this refusal for project '{project}': "
                f"{registry} could not be read ({_single_line(exc)}), so it cannot "
                "confirm the project is registered; nothing was written and no folder "
                "was created. Check the registry with: scripts/migrate/projects-cfg.sh"
            )
    if project in names:
        return None
    registered = ", ".join(sorted(names)) or "none"
    # CENSUS-ANCHOR: refusal-ledger-unregistered-project-01
    return (
        f"refusal_ledger: not recording this refusal for project '{project}': "
        f"it is not registered in {registry} (registered: {registered}), so "
        "nothing was written and no folder was created. If it is a real project, "
        f"register it with: scripts/add-project.sh --project {project}"
    )


# ---------------------------------------------------------------------------
# record_refusal — the safe, best-effort entry point the shared dual-channel
# emitters call. See the module docstring's list of cases that return False
# for the no-project / no-kanban-root / unregistered-project rules.
# ---------------------------------------------------------------------------


def record_refusal(
    error: str,
    message: str,
    remedy: str,
    *,
    project: str | None = None,
    surface: str | None = None,
    kanban_root: str | None = None,
    timestamp: str | None = None,
    context: str | None = None,
) -> bool:
    """Best-effort refusal recording. Never raises.

    Resolves *project* from the argument, then the ambient
    PGAI_PROJECT_NAME environment variable (the same resolution order
    every other project-scoped helper in this framework uses — see
    team/scripts/lib/project_paths.sh's pp_require_project_context). When
    neither is set, returns False immediately without touching the
    filesystem or attempting to resolve a kanban root at all — a
    project-less refusal must not guess.

    Resolves *kanban_root* from the argument, then
    pgai_agent_kanban.env.resolve_kanban_root() (reads
    PGAI_AGENT_KANBAN_ROOT_PATH only -- deliberately NOT the KANBAN_ROOT
    legacy bash alias, which is ambient in a real agent's shell and would
    otherwise let an unrelated subprocess -- a test run, a standalone
    script -- silently resolve to the live install). Any failure to
    resolve a kanban root, create the project directory, or write the file
    is caught here and reported as False; the caller's own refusal is
    never affected.

    Never creates the kanban root itself: when the resolved *kanban_root*
    does not already exist as a directory, this returns False without
    touching the filesystem at all -- a refusal recorded from a guard that
    fires precisely because the root is absent must not fabricate the very
    precondition the guard is refusing on. Directories beneath an
    already-existing root (projects/<project>/) are still created
    normally.

    Never creates a folder for an unregistered project: when the root's
    projects.cfg exists and does not register *project*, this prints the
    one-line ``unregistered_project_narration()`` to stderr and returns
    False without touching the filesystem. That line is the only output
    this function ever produces; it reaches stderr after the caller's own
    refusal block and never alters it.

    Resolves *context* from the argument, then ``_detect_context()`` (see
    "Context" in the module docstring) — a caller never needs
    to compute this itself; the keyword exists mainly for tests that want
    to pin a value explicitly rather than rely on the ambient
    PYTEST_CURRENT_TEST signal.

    Returns:
        True if a record was written, False otherwise (no project in
        scope, kanban root unresolvable, project not registered, or a
        write failure).
    """
    resolved_project = (project or os.environ.get("PGAI_PROJECT_NAME") or "").strip()
    if not resolved_project:
        return False

    try:
        if kanban_root is None:
            from pgai_agent_kanban.env import resolve_kanban_root

            kanban_root = str(resolve_kanban_root())

        if not os.path.isdir(kanban_root):
            return False

        narration = unregistered_project_narration(kanban_root, resolved_project)
        if narration is not None:
            print(narration, file=sys.stderr)
            return False

        ledger_path = ledger_path_for_project(kanban_root, resolved_project)

        if timestamp is None:
            timestamp = _now_iso8601()

        record = RefusalRecord(
            timestamp=timestamp,
            project=resolved_project,
            surface=surface or "unknown",
            error=error,
            message=message,
            remedy=remedy,
            context=context or _detect_context(),
        )
        write_latest(ledger_path, record)
        return True
    except Exception:  # noqa: BLE001 - recording is diagnostics, never a gate
        return False


def _now_iso8601() -> str:
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _detect_context() -> str:
    """Return ``"test"`` when this process is running inside a pytest test,
    ``"install"`` otherwise.

    Reads ``PYTEST_CURRENT_TEST`` — the environment variable pytest's own
    core sets in ``os.environ`` for the duration of every test's
    setup/call/teardown phases (not a plugin, not a project convention; a
    documented pytest built-in). An ordinary environment variable
    propagates through every subprocess boundary this module's callers
    cross for free, so checking it here — rather than, say, detecting
    whether the ``pytest`` package is importable — is what makes the bash
    binding's writes agree with the Python binding's without either one
    computing the value itself: see "Context" in the module
    docstring for the full propagation path.
    """
    return "test" if os.environ.get("PYTEST_CURRENT_TEST") else "install"


# ---------------------------------------------------------------------------
# CLI — invoked by team/scripts/lib/refusal_ledger.sh via pp_run_ops, so
# both the bash and Python emitters write through this exact same code
# path (and therefore produce the exact same record shape).
# ---------------------------------------------------------------------------


def main() -> int:
    """Run the refusal ledger CLI.

    ``record [--kanban-root ROOT] LEDGER PROJECT ERROR MESSAGE REMEDY SURFACE``
    replaces the ledger with one new record and prints ``{"written": true}``;
    ``latest LEDGER`` prints the current record as JSON (``{"found": false}``
    when there is none). Exit codes: 0 done; 1 a ledger error (named on
    stderr); 2 usage error (argparse); 3 (``EXIT_UNREGISTERED_PROJECT``)
    ``--kanban-root`` exists and does not register PROJECT, so nothing was
    written.
    """
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Read or replace the most-recent-refusal record for one project. "
            "'record' REPLACES the ledger's entire content with the given "
            "record (overwrite, not append). 'latest' prints the current "
            "record as JSON, or {\"found\": false} when the project has "
            "never recorded a refusal."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_record = subparsers.add_parser("record", help="Replace the ledger with a new record.")
    p_record.add_argument(
        "--kanban-root",
        default=None,
        help=(
            "Kanban root whose projects.cfg must register <project> before "
            "anything is written. When it exists and does not, nothing is "
            "written, one narration line goes to stderr, and the exit status "
            f"is {EXIT_UNREGISTERED_PROJECT}. Omit for a raw path-addressed write."
        ),
    )
    p_record.add_argument(
        "ledger_path",
        help="path to the project's most-recent-refusal ledger file (its content is replaced)",
    )
    p_record.add_argument("project", help="the project the refusal belongs to")
    p_record.add_argument("error", help="the refusal's stable machine error key")
    p_record.add_argument("message", help="the refusal's plain-language message")
    p_record.add_argument("remedy", help="the refusal's runnable remedy")
    p_record.add_argument("surface", help="where the refusal was raised, e.g. release.sh:git_step")

    p_latest = subparsers.add_parser("latest", help="Print the current record as JSON.")
    p_latest.add_argument(
        "ledger_path",
        help="path to the project's most-recent-refusal ledger file",
    )

    args = parser.parse_args()

    try:
        if args.command == "record":
            if args.kanban_root is not None:
                narration = unregistered_project_narration(args.kanban_root, args.project)
                if narration is not None:
                    print(narration, file=sys.stderr)
                    return EXIT_UNREGISTERED_PROJECT
            record = RefusalRecord(
                timestamp=_now_iso8601(),
                project=args.project,
                surface=args.surface,
                error=args.error,
                message=args.message,
                remedy=args.remedy,
                context=_detect_context(),
            )
            write_latest(args.ledger_path, record)
            sys.stdout.write(json.dumps({"written": True}) + "\n")
        elif args.command == "latest":
            record = read_latest(args.ledger_path)
            if record is None:
                sys.stdout.write(json.dumps({"found": False}) + "\n")
            else:
                payload = dict(record)
                payload["found"] = True
                sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")
    except RefusalLedgerError as exc:
        print(f"refusal_ledger: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
