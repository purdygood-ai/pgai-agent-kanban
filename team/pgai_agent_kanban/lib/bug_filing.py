"""
bug_filing.py -- shared primitives for autonomous bug filers.

Factors the parts of an autonomous bug filer that do not vary by caller --
claiming an atomic BUG-NNNN slot (team/pm-agent's own claim_next_bug_id /
release_bug_id_claim, self-located since pm-agent/ is a sibling package,
"each tree finds itself" -- team/roles/CODER.md Principle 6), and scanning
an existing bugs/ directory for open/running bug files that are still live
dedup candidates -- out of any one filer into a single implementation.
Both pgai_agent_kanban.deep_verify.bug_filer (the DEEP-VERIFY tier's
cold-build filer) and pgai_agent_kanban.lint.bug_filer (the hygiene-lint
aggregate filer) import from here rather than each re-implementing the
same claim/scan logic, per docs/coding-standards.md directive 1 ("do not
duplicate an operation that already exists").

What is NOT shared: report rendering and the dedup MATCH key itself. Each
filer's filed-bug content differs (a captured-output engine/flavor summary
vs. a grouped per-instance findings list), and each keys dedup on a
different recorded section (deep-verify's own "## Deep-Verify Pairs" set
vs. the hygiene filer's single "## Hygiene Lint Class" value) -- a shared
renderer or a shared match predicate would need as many special cases as
the two callers have differences, which is worse than each filer reading
its own small section directly. What IS shared is the walk over bugs_dir
that both need identically: list "BUG-NNNN-*.md" files, read each one,
keep only the ones whose "## Status" still makes them live.
"""

from __future__ import annotations

import importlib.util
import os
import re
from pathlib import Path
from typing import Iterator

from pgai_agent_kanban.task_status import section_value

_BUG_FILENAME_RE = re.compile(r"^(BUG-\d{4,})-.+\.md$", re.IGNORECASE)

# Statuses the discovery pipeline (and TESTER's own "cites an OPEN bug"
# rule, team/roles/TESTER.md Step 6) still treat as live -- a bug bundled
# into an RC (running) or not yet picked up (open) both cover the defect;
# only done/wont-do bugs are no longer live and must not suppress a fresh
# filing for the same defect.
ELIGIBLE_DEDUP_STATUSES = frozenset({"open", "running"})


def read_status(text: str) -> str:
    """Return the lowercased '## Status' value of a bug file's text, or
    "open" when no Status value is present -- a malformed bug file is
    treated as live (not silently excluded from dedup), never as a signal
    that its defect is resolved. The value is read by
    task_status.section_value (first token, lowercased)."""
    tokens = section_value(text, "Status").split()
    return tokens[0].lower() if tokens else "open"


def iter_open_or_running_bugs(bugs_dir: str) -> Iterator[tuple]:
    """Yield (bug_id, text) for every 'BUG-NNNN-*.md' file directly inside
    *bugs_dir* whose '## Status' is still live (open or running), in
    filename-sorted order for deterministic dedup scanning.

    A missing *bugs_dir* yields nothing -- a fresh project's first filing
    has no bugs/ directory yet, which is not an error.
    """
    if not os.path.isdir(bugs_dir):
        return
    for name in sorted(os.listdir(bugs_dir)):
        match = _BUG_FILENAME_RE.match(name)
        if not match:
            continue
        path = os.path.join(bugs_dir, name)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError:
            continue
        if read_status(text) not in ELIGIBLE_DEDUP_STATUSES:
            continue
        yield match.group(1), text


# Loaded bug_scanner modules, keyed by the resolved path of the
# bug_scanner.py file they were executed from. Private to this module:
# nothing is registered in sys.modules, so no other import can shadow or be
# shadowed by these copies.
_BUG_SCANNER_CACHE: dict = {}


def own_pm_agent_dir() -> Path:
    """Return the pm-agent/ directory beside this module's own package root.

    pgai_agent_kanban/lib/bug_filing.py -> pgai_agent_kanban/ -> <root>/,
    and pm-agent/ is a sibling of pgai_agent_kanban/ in both layouts: the
    dev tree (team/pm-agent/) and the live install (<root>/pm-agent/).
    Each tree finds itself; no repository checkout is ever consulted.
    """
    return Path(__file__).resolve().parents[2] / "pm-agent"


def load_bug_scanner(pm_agent_dir=None):
    """Load ``<pm_agent_dir>/lib/bug_scanner.py`` by file path and return
    the module.

    *pm_agent_dir* defaults to own_pm_agent_dir(). The file is executed
    through importlib's file-location loader rather than imported by the
    dotted name ``lib.bug_scanner``: the top-level name ``lib`` is
    ambiguous (scripts/lib/ is a namespace directory of the same name),
    so a name-based import resolves to whichever ``lib`` happens to be
    first on sys.path or already cached in sys.modules. Loading by path
    depends on neither, and leaves sys.path and sys.modules untouched.

    The loaded module is cached per resolved file path, so repeated calls
    return the same module object.

    Raises ImportError, naming the path it looked at, when the file is
    missing or cannot be loaded.
    """
    base = Path(pm_agent_dir) if pm_agent_dir is not None else own_pm_agent_dir()
    path = (base / "lib" / "bug_scanner.py").resolve()
    cached = _BUG_SCANNER_CACHE.get(path)
    if cached is not None:
        return cached
    if not path.is_file():
        raise ImportError(
            f"bug_scanner not found at {path}: expected pm-agent/lib/bug_scanner.py "
            f"beside the pgai_agent_kanban package. Check that "
            f"{path.parent.parent} is a complete pm-agent directory "
            f"(reinstall with install.sh if it is missing).",
            path=str(path),
        )
    spec = importlib.util.spec_from_file_location("pm_agent_bug_scanner", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot build an import spec for {path}", path=str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _BUG_SCANNER_CACHE[path] = module
    return module


def import_bug_scanner():
    """Return the atomic BUG-NNNN claim helpers from this tree's own
    pm-agent/lib/bug_scanner.py. Never re-implement the claim logic in a
    filer -- see module docstring.

    The helpers come from load_bug_scanner(), which self-locates pm-agent/
    beside this package (dev tree or live install) and loads bug_scanner.py
    by file path. The result is therefore the same whatever top-level
    ``lib`` package is on sys.path or cached in sys.modules, and calling
    this changes neither.

    Returns (claim_next_bug_id, release_bug_id_claim).
    """
    scanner = load_bug_scanner()
    return scanner.claim_next_bug_id, scanner.release_bug_id_claim
