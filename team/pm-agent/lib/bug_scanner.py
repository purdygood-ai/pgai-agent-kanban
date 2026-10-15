"""lib/bug_scanner.py — Scan bugs/ directory and sync bug_backlog.md cache.

The bugs/ directory is the source of truth for bug reports. bug_backlog.md is
a derived cache that PM updates to reflect directory contents. This module
provides the scanning and sync utilities.

Public API
----------
    scan_bugs_directory(bugs_dir)           -> list[dict]
    get_bundled_bug_ids(bug_backlog_path)   -> set[str]
    get_open_bug_ids(bug_backlog_path)      -> set[str]
    update_bug_backlog_cache(bugs_dir, bug_backlog_path) -> None
    get_unbundled_bugs(bugs_dir, bug_backlog_path)       -> list[dict]
    claim_next_bug_id(bugs_dir, slug, claimed_by=None) -> tuple[str, Path, Path]
    release_bug_id_claim(lock_path)         -> None
    detect_duplicate_bug_ids(bugs_dir)      -> list[int]
"""

import os
import re
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Matches BUG-NNNN-slug filenames. Excludes BUG-TEMPLATE and README.
# This is intentionally the loose "looks like a bug file" pattern used to
# enumerate candidates for the backlog cache — it is NOT the filing-time
# gate. See _BUG_GATE_RE below for the strict pattern used by the
# high-water scan.
_BUG_FILE_RE = re.compile(r'^(BUG-\d{4,}-.+)\.md$', re.IGNORECASE)

# Matches only the numeric prefix of a BUG-NNNN-* file (4 or more digits).
_BUG_NUM_RE = re.compile(r'^BUG-(\d{4,})-', re.IGNORECASE)

# Lock file pattern written during atomic BUG-ID claims.
_CLAIM_LOCK_RE = re.compile(r'^\.claim-(\d{4,})\.lock$')

# ---------------------------------------------------------------------------
# Gate-valid BUG filename pattern, sourced from the single-source
# ledger_filename_contract module shared with discovery.sh's scan-and-select
# layer.  Used ONLY by _scan_highest_bug_num to decide which filenames may
# participate in the high-water scan — a malformed name (date-first,
# under/over-padded, or any other shape) must never poison the next-number
# arithmetic.  _BUG_FILE_RE above stays loose on purpose: it drives backlog
# cache enumeration, a separate concern from filing-time numbering safety.
# ---------------------------------------------------------------------------


def _resolve_ledger_filename_re():
    """Return ``ledger_filename_re`` from the shared contract module, or ``None``.

    Tries the normal import first (works when the package is already on
    ``sys.path``, e.g. via an editable install).  Falls back to
    self-locating ``team/`` relative to this file's own path — "each tree
    finds itself" (never assume an external caller pre-populated sys.path).
    """
    try:
        from pgai_agent_kanban.lib.ledger_filename_contract import ledger_filename_re
        return ledger_filename_re
    except ImportError:
        pass
    try:
        _team_root = str(Path(__file__).resolve().parents[2])  # team/pm-agent/lib -> team/
        if _team_root not in sys.path:
            sys.path.insert(0, _team_root)
        from pgai_agent_kanban.lib.ledger_filename_contract import ledger_filename_re
        return ledger_filename_re
    except ImportError:
        return None


_ledger_filename_re = _resolve_ledger_filename_re()
if _ledger_filename_re is not None:
    _BUG_GATE_RE = _ledger_filename_re("BUG")
else:
    # Fallback definition when the package is not importable (e.g. a test
    # environment without a full install).  Kept byte-identical to
    # ledger_filename_contract.py's canonical BUG pattern.
    _BUG_GATE_RE = re.compile(
        r'^BUG-[0-9]{4}(?:-[0-9]{8})?-[a-z0-9][a-z0-9-]*\.md$', re.IGNORECASE
    )

# Which 'running' items a shipped bundle carried (see
# pgai_agent_kanban.lib.bundle_items). _resolve_ledger_filename_re above has
# already put this tree's package root on sys.path when it was needed.
try:
    from pgai_agent_kanban.lib.bundle_items import carried_item_names as _carried_item_names
except ImportError:
    _carried_item_names = None

# The closed states every reader shares (done, wont-do; the retired
# "superseded" reads as wont-do). The package root is on sys.path by now
# (see _resolve_ledger_filename_re above).
from pgai_agent_kanban.lib.terminal_states import TERMINAL_STATES, canonical_state  # noqa: E402


def _carried_bug_files(bugs_dir: str) -> frozenset:
    """File names of the bugs a shipped bundle left at 'running' (carried).

    Read from the ``requirements`` directory beside *bugs_dir*. Empty when
    the shared module is not importable.
    """
    if _carried_item_names is None:
        return frozenset()
    return _carried_item_names(Path(bugs_dir).parent / "requirements")


# Excluded filenames (compared case-insensitively)
_EXCLUDED_NAMES = {"BUG-TEMPLATE.md", "README.md"}

# Maximum retry attempts for claim_next_bug_id before giving up.
_CLAIM_MAX_RETRIES = 256

# Durable per-bugs-dir provenance ledger claim_next_bug_id appends to on every
# successful claim -- see _record_bug_claim below. A dotfile sitting beside
# the bug reports themselves, never a bug report's own content, so it needs
# no ledger-filename-gate handling and is invisible to the BUG-NNNN scans
# above.
_CLAIM_LOG_FILENAME = ".bug-claims.log"

# Env var an agent's own wake dispatch exports naming the task currently
# running in that session (team/scripts/lib/wake_driver_invoke.sh). Read as
# the default claimant identity when a caller does not name itself
# explicitly via claim_next_bug_id's own claimed_by argument.
_TASK_ID_ENV_VAR = "PGAI_TASK_ID"

# Extracts severity from **Severity:** field — liberal whitespace handling.
_SEVERITY_RE = re.compile(
    r'\*\*Severity:\*\*\s*(.+)',
    re.IGNORECASE,
)

# Extracts ## Status field value.  Matches the first non-whitespace token on
# the line immediately following the "## Status" heading.
_STATUS_RE = re.compile(
    r'^##\s+Status\s*\n\s*(\S+)',
    re.MULTILINE | re.IGNORECASE,
)

# Matches a backlog entry line: "- [x] BUG-ID" or "- [ ] BUG-ID"
# Group 1: checkbox content (x or space), Group 2: bug ID
_BACKLOG_ENTRY_RE = re.compile(
    r'^\s*-\s+\[([x ])\]\s+(BUG-\S+)',
    re.IGNORECASE,
)

# Extracts content under ## Resolved By heading up to the next ## heading or
# end of file.  Captures everything between the heading line and the next
# top-level heading (##) so we can check whether the field has substantive text.
_RESOLVED_BY_RE = re.compile(
    r'^##\s+Resolved\s+By\s*\n(.*?)(?=^##|\Z)',
    re.MULTILINE | re.IGNORECASE | re.DOTALL,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_summary(text: str) -> str:
    """Return the first non-empty content line under the ## Symptom heading.

    Scans for a line matching '## Symptom' (case-insensitive), then returns
    the first subsequent non-empty, non-heading line. Returns empty string if
    not found.
    """
    lines = text.splitlines()
    in_symptom = False
    for line in lines:
        if re.match(r'^\s*##\s+Symptom\s*$', line, re.IGNORECASE):
            in_symptom = True
            continue
        if in_symptom:
            stripped = line.strip()
            if stripped and not stripped.startswith('#'):
                return stripped
    return ""


def _extract_severity(text: str) -> str:
    """Return the severity value from **Severity:** field, or empty string."""
    m = _SEVERITY_RE.search(text)
    if m:
        return m.group(1).strip()
    return ""


def _is_excluded(filename: str) -> bool:
    """Return True if the filename should be excluded from bug scanning."""
    return filename in _EXCLUDED_NAMES


def _extract_status(text: str) -> str:
    """Return the normalized ## Status value from bug file text.

    Returns the lowercase status token ('open', 'running', 'done', etc.).
    Returns 'open' if the ## Status header is absent.
    """
    m = _STATUS_RE.search(text)
    if m:
        return m.group(1).strip().lower()
    return "open"


def _extract_resolved_by(text: str) -> str:
    """Return the content of the ## Resolved By section, stripped of whitespace.

    Captures everything between the '## Resolved By' heading and the next
    '##' heading (or end of file).  Returns an empty string if the section is
    absent or contains only whitespace / placeholder-style lines.

    A non-empty return value means the field has been filled in with at least
    one substantive (non-blank, non-placeholder) token.  Callers should check
    ``bool(_extract_resolved_by(text))`` to decide whether the field is
    populated.
    """
    m = _RESOLVED_BY_RE.search(text)
    if not m:
        return ""
    return m.group(1).strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def resolve_bugs_dir(bugs_dir: str = None) -> str:
    """Resolve the bugs directory path using PGAI_PROJECT_ROOT with fallback.

    Parameters
    ----------
    bugs_dir:
        Explicit bugs directory path. If provided, returned as-is.
        If None, resolved from PGAI_PROJECT_ROOT or PGAI_AGENT_KANBAN_ROOT_PATH
        (canonical) env var, falling back to ~/pgai_agent_kanban/bugs.

    Returns
    -------
    str
        Absolute path to the bugs directory.
    """
    if bugs_dir:
        return bugs_dir
    # Resolve canonical var first, new-path as default.
    project_root = os.environ.get("PGAI_PROJECT_ROOT") or (
        os.environ.get("PGAI_AGENT_KANBAN_ROOT_PATH")
        or str(Path.home() / "pgai_agent_kanban")
    )
    return str(Path(project_root) / "bugs")


def scan_bugs_directory(bugs_dir: str) -> list:
    """Scan bugs/ directory for BUG-*.md files.

    Reads each BUG-NNNN-*.md file (excluding BUG-TEMPLATE.md and README.md)
    and extracts metadata from its contents.

    Parameters
    ----------
    bugs_dir:
        Path to the bugs/ directory to scan.

    Returns
    -------
    list[dict]
        Sorted (by bug ID) list of dicts with keys:
          - id       : bug ID slug, e.g. "BUG-0078-pm-bug-scanning"
          - file     : absolute path to the bug file (str)
          - summary  : first non-empty line from ## Symptom section
          - severity : value from **Severity:** field, or "" if absent
    """
    bugs_path = Path(bugs_dir)
    results = []

    for entry in bugs_path.iterdir():
        if not entry.is_file():
            continue
        filename = entry.name
        if _is_excluded(filename):
            continue
        m = _BUG_FILE_RE.match(filename)
        if not m:
            continue
        bug_id = m.group(1)
        text = entry.read_text(encoding="utf-8")
        results.append({
            "id": bug_id,
            "file": str(entry.resolve()),
            "summary": _extract_summary(text),
            "severity": _extract_severity(text),
        })

    results.sort(key=lambda d: d["id"])
    return results


def get_bundled_bug_ids(bug_backlog_path: str) -> set:
    """Read bug_backlog.md and return the set of bug IDs marked [x].

    Parameters
    ----------
    bug_backlog_path:
        Path to the bug_backlog.md file.

    Returns
    -------
    set[str]
        Bug IDs (e.g. "BUG-0078-pm-bug-scanning") whose checkbox is [x].
        Returns empty set if the file does not exist.
    """
    path = Path(bug_backlog_path)
    if not path.exists():
        return set()
    bundled = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _BACKLOG_ENTRY_RE.match(line)
        if m and m.group(1).lower() == 'x':
            bundled.add(m.group(2))
    return bundled


def get_open_bug_ids(bug_backlog_path: str) -> set:
    """Read bug_backlog.md and return the set of bug IDs marked [ ] (open).

    Parameters
    ----------
    bug_backlog_path:
        Path to the bug_backlog.md file.

    Returns
    -------
    set[str]
        Bug IDs whose checkbox is [ ] (space). Returns empty set if the file
        does not exist.
    """
    path = Path(bug_backlog_path)
    if not path.exists():
        return set()
    open_ids = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _BACKLOG_ENTRY_RE.match(line)
        if m and m.group(1) == ' ':
            open_ids.add(m.group(2))
    return open_ids


def update_bug_backlog_cache(bugs_dir: str, bug_backlog_path: str) -> None:
    """Sync bug_backlog.md to reflect the current bugs/ directory contents.

    The checkbox state is derived exclusively from each bug file's
    ## Status field:

    - ## Status: running  →  [x]  (bundled into an RC, in flight)
    - ## Status: running  →  [ ]  when carried: every bundle in the
                                   requirements/ directory beside bugs_dir
                                   that lists it is done or wont-do, so a
                                   release shipped it unfixed and it is a
                                   candidate for the next bundle
    - ## Status: done     →  [x]  (already shipped; only when ## Resolved By
                                   is populated — see below)
    - ## Status: wont-do  →  [x]  (closed without a fix; any closed state
                                   other than done, from
                                   lib.terminal_states, with the retired
                                   "superseded" read as wont-do)
    - ## Status: open     →  [ ]  (needs bundling)
    - ## Status absent    →  [ ]  (treat missing as open)

    Resolution gate for ``done`` bugs
    ----------------------------------
    A bug with ``## Status: done`` MUST also have a populated ``## Resolved By``
    section to be treated as done-eligible.  If the section is absent or empty,
    the bug is excluded from the done path: a ``UserWarning`` is emitted (so the
    discovery pipeline can surface it as a log warning) and the bug is written as
    ``[ ]`` (open) so that the next cycle still sees it as unresolved.  The
    scanner does **not** raise an exception — ``done`` bugs without this field
    are written as ``[ ]`` (open) so the next cycle still sees them as unresolved.

    This overrides any prior cache marker state.  A file whose cache entry
    was [x] but whose Status has been reset to 'open' will be written back
    as [ ] so that discovery picks it up on the next run.

    - Bugs no longer present in bugs/ directory are removed from the cache.

    The file is created (with a header) if it does not already exist.

    Parameters
    ----------
    bugs_dir:
        Path to the bugs/ directory (source of truth).
    bug_backlog_path:
        Path to the bug_backlog.md cache file to update.
    """
    backlog_path = Path(bug_backlog_path)

    # Collect current bug IDs from directory (source of truth).
    # The file path is needed so we can read Status from the file content.
    dir_bugs = scan_bugs_directory(bugs_dir)
    bugs_path = Path(bugs_dir)
    carried = _carried_bug_files(bugs_dir)

    # Build new entry list in sorted order, only for bugs in directory.
    # Derive checkbox from file Status — cache prior state is NOT consulted.
    lines = ["# Bug Backlog\n", "\n", "## Queue\n", "\n"]
    for bug in dir_bugs:
        bid = bug["id"]
        file_path = Path(bug["file"])
        try:
            text = file_path.read_text(encoding="utf-8")
        except OSError:
            # File vanished between scan and read — skip safely
            continue
        status = _extract_status(text)
        if status == "done":
            # Enforce resolution attribution: done bugs must have ## Resolved By
            # populated.  Emit a warning and skip to open treatment when absent.
            resolved_by = _extract_resolved_by(text)
            if not resolved_by:
                warnings.warn(
                    f"Bug {bid!r} has ## Status: done but ## Resolved By is "
                    f"absent or empty in {str(file_path)!r}. "
                    f"Populate ## Resolved By with the closing CODER task ID "
                    f"(e.g. CODER-YYYYMMDD-NNN-fix-bug-XXXX) or a manual "
                    f"hotfix note before treating this bug as closed. "
                    f"Skipping from done-eligible path.",
                    UserWarning,
                    stacklevel=2,
                )
                checkbox = " "
            else:
                checkbox = "x"
        elif status == "running":
            checkbox = " " if file_path.name in carried else "x"
        elif canonical_state(status) in TERMINAL_STATES:
            # Closed without a fix (wont-do, or the retired "superseded"):
            # never offered for bundling again.
            checkbox = "x"
        else:
            # 'open' or any unrecognised value → treat as open
            checkbox = " "
        lines.append(f"- [{checkbox}] {bid}\n")

    backlog_path.parent.mkdir(parents=True, exist_ok=True)
    backlog_path.write_text("".join(lines), encoding="utf-8")

    # Retain reference to silence F841 linters on older toolchains
    _ = bugs_path


def get_unbundled_bugs(bugs_dir: str, bug_backlog_path: str) -> list:
    """Return bugs from the directory whose ## Status is 'open' (or absent),
    plus every 'running' bug a shipped bundle carried (see
    update_bug_backlog_cache).

    Calls update_bug_backlog_cache as a side effect so the cache is
    regenerated from file Status fields before being read.  A bug whose cache
    entry was previously [x] but whose file Status has since been reset to
    'open' will appear in the returned list after this call.

    Parameters
    ----------
    bugs_dir:
        Path to the bugs/ directory (source of truth).
    bug_backlog_path:
        Path to the bug_backlog.md cache file.

    Returns
    -------
    list[dict]
        Bugs from bugs_dir whose ## Status is 'open' (or absent), or
        'running' and carried. Each dict
        has keys: id, file, summary, severity.
    """
    update_bug_backlog_cache(bugs_dir, bug_backlog_path)
    bundled = get_bundled_bug_ids(bug_backlog_path)
    all_bugs = scan_bugs_directory(bugs_dir)
    return [b for b in all_bugs if b["id"] not in bundled]


# ---------------------------------------------------------------------------
# Atomic BUG-ID claim
# ---------------------------------------------------------------------------

def _scan_highest_bug_num(bugs_dir: Path) -> int:
    """Scan bugs_dir for the highest numeric prefix across GATE-VALID
    BUG-NNNN-*.md files and any active .claim-NNNN.lock files.  Returns 0 if
    the directory is empty or contains no BUG-* files.

    Both committed bug files and in-flight claim lock files are considered so
    that a concurrent claimer who has locked but not yet written the final file
    is still visible.

    Only filenames matching the strict ledger gate (_BUG_GATE_RE — exactly 4
    digits, optional date segment immediately AFTER the number) participate
    in the .md-file half of this scan.  A malformed name such as a
    date-first ``BUG-20260803-001-*.md`` (e.g.) must never poison the
    high-water mark by contributing its 8-digit "number": a looser
    ``\\d{4,}`` prefix match would let ``highest`` jump from a legitimate
    4-digit value to an 8-digit one and permanently break subsequent
    numbering.  The 4-digit field is always parsed with ``int()`` (never
    compared as a string).
    """
    highest = 0
    for entry in bugs_dir.iterdir():
        # Check BUG-NNNN-*.md files — gate-valid names only.
        if _BUG_GATE_RE.match(entry.name):
            m = _BUG_NUM_RE.match(entry.name)
            if m:
                n = int(m.group(1))
                if n > highest:
                    highest = n
            continue
        # Check .claim-NNNN.lock files (in-flight claims from concurrent callers)
        m2 = _CLAIM_LOCK_RE.match(entry.name)
        if m2:
            n = int(m2.group(1))
            if n > highest:
                highest = n
    return highest


def _record_bug_claim(bugs_path: Path, bug_id: str, claimed_by: str) -> None:
    """Append one durable provenance line to ``<bugs_dir>/.bug-claims.log`` for
    a successful ``claim_next_bug_id`` call: a UTC timestamp, the claimed
    ``bug_id``, and the claiming session's identity.

    This is the record a bug file's own ``Source Task`` header cannot be
    trusted to be on its own -- a header is whatever the filer chose to
    write into the content, while this line is written by the claiming
    function itself at the moment of creation, from the caller's own
    identity rather than from anything the filer's content composed later.
    A mismatch between the two is now detectable by comparing this ledger
    against a bug file's own header, rather than inferred from file mtimes
    and wake-log timing the way an earlier investigation had to.

    Best-effort: a write failure here (read-only filesystem, permissions)
    must never turn a successful bug-ID claim into a failed one, so any
    ``OSError`` is swallowed silently. The claim itself already succeeded
    by the time this is called; the provenance line is additive evidence,
    not a precondition for filing.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    line = f"{timestamp}\t{bug_id}\t{claimed_by}\n"
    try:
        with open(bugs_path / _CLAIM_LOG_FILENAME, "a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError:
        pass


def claim_next_bug_id(bugs_dir: str, slug: str, claimed_by: str = None) -> tuple:
    """Atomically claim the next available BUG-NNNN slot and return the claimed
    bug file path along with a lock path that the caller must release.

    This function uses POSIX ``O_CREAT | O_EXCL`` semantics to create a
    ``.claim-NNNN.lock`` sentinel file, which is atomic on any POSIX filesystem.
    If two callers race, exactly one wins each numeric slot and the other
    automatically increments to the next slot.

    Workflow for the caller
    -----------------------
    1. Call ``claim_next_bug_id(bugs_dir, slug)`` → ``(bug_id, bug_path, lock_path)``
    2. Write bug file content to ``bug_path``.
    3. Call ``release_bug_id_claim(lock_path)`` to remove the sentinel.

    The caller **must** call ``release_bug_id_claim`` after writing the bug file
    (or on error).  Stale lock files left by crashed callers do not break future
    claims because they merely cause the claimer to skip that slot; however they
    do permanently consume a BUG number.  Always use a try/finally guard::

        bug_id, bug_path, lock_path = claim_next_bug_id(bugs_dir, "my-slug")
        try:
            bug_path.write_text(content, encoding="utf-8")
        finally:
            release_bug_id_claim(lock_path)

    Parameters
    ----------
    bugs_dir:
        Path to the bugs/ directory.  Must exist.
    slug:
        Kebab-case slug for the bug file name, e.g. ``"my-new-bug"``.
        Do not include the ``BUG-NNNN-`` prefix or the ``.md`` extension.
    claimed_by:
        Identity of the claiming session, recorded verbatim into the
        durable provenance ledger (see ``_record_bug_claim``). When omitted
        (the default, preserving every existing two-argument call site),
        falls back to the ``PGAI_TASK_ID`` environment variable an agent's
        own wake dispatch exports for the duration of its session, and
        finally to the literal string ``"unknown"`` when neither is
        available -- an automated filer invoked outside any task session
        (a gated-runner hook, a cron-triggered sweep) should pass its own
        fixed identity here explicitly rather than relying on the fallback.

    Returns
    -------
    tuple[str, Path, Path]
        ``(bug_id, bug_path, lock_path)`` where:
        - ``bug_id``   — the claimed identifier, e.g. ``"BUG-0044"``
        - ``bug_path`` — the path where the caller should write the bug file,
                         e.g. ``bugs_dir/BUG-0044-my-new-bug.md``
        - ``lock_path`` — the lock sentinel path; caller passes to
                          ``release_bug_id_claim`` when done.

    Raises
    ------
    FileNotFoundError
        If ``bugs_dir`` does not exist.
    RuntimeError
        If no free slot is found within ``_CLAIM_MAX_RETRIES`` attempts
        (indicates a serious filesystem or concurrency anomaly).
    """
    bugs_path = Path(bugs_dir)
    if not bugs_path.is_dir():
        raise FileNotFoundError(
            f"claim_next_bug_id: bugs directory does not exist: {bugs_dir!r}"
        )

    for _attempt in range(_CLAIM_MAX_RETRIES):
        # Compute the next candidate number from the current highest.
        # We re-scan on every attempt so that we see files written by racing
        # callers since the previous attempt.
        candidate_num = _scan_highest_bug_num(bugs_path) + 1
        bug_id = f"BUG-{candidate_num:04d}"
        lock_path = bugs_path / f".claim-{candidate_num:04d}.lock"
        bug_path = bugs_path / f"{bug_id}-{slug}.md"

        # Atomically create the lock file.  O_CREAT|O_EXCL fails with
        # FileExistsError if the file already exists — this is the collision
        # guard.  os.open is used directly because pathlib.Path.open does not
        # expose O_EXCL.
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            os.close(fd)
        except FileExistsError:
            # Another caller beat us to this slot.  Re-scan and try again.
            continue

        # Claim is ours. Record who claimed it before returning, so the
        # claim is durably attributable even if the caller never gets as
        # far as writing bug_path's content (see _record_bug_claim).
        _claimant = claimed_by or os.environ.get(_TASK_ID_ENV_VAR) or "unknown"
        _record_bug_claim(bugs_path, bug_id, _claimant)

        # Return immediately — do not write the bug file here.  The caller
        # writes the content and then calls release_bug_id_claim().
        return (bug_id, bug_path, lock_path)

    raise RuntimeError(
        f"claim_next_bug_id: could not claim a free BUG-NNNN slot after "
        f"{_CLAIM_MAX_RETRIES} attempts in {bugs_dir!r}. "
        f"Inspect the directory for stale .claim-*.lock files or excessive "
        f"concurrent writers."
    )


def release_bug_id_claim(lock_path) -> None:
    """Remove the .claim-NNNN.lock sentinel created by ``claim_next_bug_id``.

    Safe to call even if the lock file has already been removed (e.g. after a
    crash recovery).  The caller is responsible for calling this after writing
    the bug file content (or in a finally block on error).

    Parameters
    ----------
    lock_path:
        ``pathlib.Path`` or str — the lock path returned by
        ``claim_next_bug_id``.
    """
    try:
        Path(lock_path).unlink()
    except FileNotFoundError:
        pass  # Already gone — that's fine.


# ---------------------------------------------------------------------------
# Collision detection
# ---------------------------------------------------------------------------

def detect_duplicate_bug_ids(bugs_dir: str) -> list:
    """Scan bugs_dir for files that share the same numeric BUG-NNNN prefix.

    A collision occurs when two or more ``BUG-NNNN-*.md`` files in the same
    directory share the same 4-digit (or longer) numeric prefix.  This should
    not happen if all bug files are created via ``claim_next_bug_id``, but may
    occur when files were created out-of-band or when a claim lock was not
    properly cleaned up.

    Emits a ``warnings.warn`` (category ``UserWarning``) for each colliding
    number found so that callers such as the discovery pipeline can surface
    these as log warnings without raising an exception.

    Parameters
    ----------
    bugs_dir:
        Path to the bugs/ directory to inspect.

    Returns
    -------
    list[int]
        Sorted list of numeric BUG IDs for which duplicates were detected.
        Empty list if no collisions exist.
    """
    bugs_path = Path(bugs_dir)
    if not bugs_path.is_dir():
        return []

    # Map numeric prefix -> list of filenames that claim it
    num_to_files: dict = {}
    for entry in bugs_path.iterdir():
        if not entry.is_file():
            continue
        m = _BUG_NUM_RE.match(entry.name)
        if not m:
            continue
        n = int(m.group(1))
        num_to_files.setdefault(n, []).append(entry.name)

    collisions = sorted(n for n, files in num_to_files.items() if len(files) > 1)

    for n in collisions:
        files_str = ", ".join(sorted(num_to_files[n]))
        warnings.warn(
            f"BUG-ID collision detected: numeric prefix {n:04d} is claimed by "
            f"multiple files in {bugs_dir!r}: {files_str}. "
            f"Use claim_next_bug_id() for all new bug file creation to prevent "
            f"future collisions. Existing collisions should be resolved manually.",
            UserWarning,
            stacklevel=2,
        )

    return collisions
