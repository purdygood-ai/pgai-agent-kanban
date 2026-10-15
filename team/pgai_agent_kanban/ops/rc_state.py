"""
rc_state.py — the read-only consistency check of a project's RC state.

:func:`verify_rc_state` is the one implementation behind the
``verify-rc-state`` operator verb: ``team/scripts/verify-rc-state.sh``
reaches it through ``pp_run_ops pgai_agent_kanban.ops verify_rc_state`` and
``GET /verify-rc-state`` calls it in process. It returns the
:class:`~pgai_agent_kanban.ops.read.ReadOutput` (exit code, stdout, stderr)
the script prints.

Pre-flight (each refusal exits 2 with stdout empty):
  * the project name resolves against ``projects.cfg`` by exact match, then
    an unambiguous prefix (``ops.resolve.resolve_project_name``; with no
    ``projects.cfg`` the name is taken as typed);
  * ``projects/<name>/`` exists;
  * its ``project.cfg`` (or a legacy ``PROJECT.cfg``) exists;
  * a dev tree is named: ``[project] dev_tree_path``, else the
    ``PGAI_DEV_TREE_PATH`` environment value, else ``kanban.cfg``
    ``[paths] dev_tree_path``.

Checks, each under its own ``--- Check <x>: ... ---`` heading:
  a. release-state.md fields validate (``cm.read_state_field``'s schema
     validator), and a real Last Released matches the newest release tag
     (``lib.released_version.last_released_version``, local refs only).
  b. Every open task entry in the agent backlogs has a task folder, and
     every task folder a queue entry (or a DONE / WONT-DO state).
  c. Each queued task's ``## State`` matches its queue marker.
  d. Every ``CLAUDE-...`` id under a README's ``## Prerequisites`` names a
     task folder, live or archived (``archive.lookup.archived_task_ids``).
  e. The Active RC has its local ``<prefix>rc/<version>`` branch in the dev
     tree and no other RC branch is left over (git reads only).
  f. Every BUG-/PRIORITY- item a requirements bundle lists exists, and every
     ``[x]`` backlog item appears in some bundle.

Output lines are ``[OK]    <message>`` (with *verbose* only), ``[WARN]  ...``
and ``[ERROR] ...``, then ``verify-rc-state: N errors, N warnings, N ok``.
Exit 0 with no ERROR, 1 with one or more.

The requirements files of check f are taken in the collation order of the
process's ``LC_COLLATE`` (the order a shell glob lists them in); the
command line sets that locale from the environment before it runs.
"""

from __future__ import annotations

import functools
import io
import locale
import os
import pathlib
import re
import subprocess
import sys
import traceback
from typing import Callable, Iterable, Iterator, Mapping, Optional, TextIO

from pgai_agent_kanban.archive.lookup import archived_task_ids
from pgai_agent_kanban.cm.read_state_field import read_state_field, validate_release_state_fields
from pgai_agent_kanban.lib import ini, semver
from pgai_agent_kanban.lib.error_contract import format_error_contract
from pgai_agent_kanban.lib.project_cfg import project_cfg_file, read_cfg_key
from pgai_agent_kanban.lib.released_version import last_released_version
from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.ops.errors import Ambiguous, NotFound, OpsError
from pgai_agent_kanban.ops.read import ReadOutput
from pgai_agent_kanban.ops.resolve import resolve_project_name

__all__ = ["CLI_VERBS", "verify_rc_state"]

#: The script whose output this module reproduces, named in its messages.
_SCRIPT = "verify-rc-state.sh"

#: The agent backlogs checks b and c read, in this order.
_QUEUE_AGENTS = ("coder", "cm", "pm", "tester", "writer", "bug", "priority")

_LINE_PAT = re.compile(r'^\s*-\s+\[([^\]]*)\]\s+(\S+)')
_TASK_ID_PAT = re.compile(r'^[A-Z]+-[A-Z]+-\d{8}-\d+')
_PREREQ_ID_PAT = re.compile(r'\b(CLAUDE-[A-Z]+-\d{8}-\d+-\S+)')
_BUNDLE_REF_PAT = re.compile(r'\b((?:PRIORITY|BUG)-\d+[^\s\`)"]*)')
_BRANCH_PREFIX_RE = re.compile(r'^[a-zA-Z0-9_-]+$')


class _Stop(Exception):
    """Ends the run early with *exit_code* (a pre-flight refusal or a fault)."""

    def __init__(self, exit_code: int) -> None:
        super().__init__(exit_code)
        self.exit_code = exit_code


class _Report:
    """The counters and the three line writers of one run."""

    def __init__(self, out: TextIO, verbose: bool) -> None:
        self.out = out
        self.verbose = verbose
        self.ok_count = 0
        self.warn_count = 0
        self.error_count = 0

    def ok(self, message: str) -> None:
        self.ok_count += 1
        if self.verbose:
            self.out.write(f"[OK]    {message}\n")

    def warn(self, message: str) -> None:
        self.warn_count += 1
        self.out.write(f"[WARN]  {message}\n")

    def error(self, message: str) -> None:
        self.error_count += 1
        self.out.write(f"[ERROR] {message}\n")

    def heading(self, title: str) -> None:
        self.out.write(f"\n--- {title} ---\n")

    def finding(self, severity: str, message: str) -> None:
        """Record one ``OK``/``WARN``/``ERROR`` finding; any other severity is dropped."""
        {"OK": self.ok, "WARN": self.warn, "ERROR": self.error}.get(severity, lambda _m: None)(message)

    def findings(self, produce: Callable[[], Iterable[tuple[str, str]]], err: TextIO) -> None:
        """Record every finding *produce* yields. A fault inside it keeps the
        findings already yielded and writes its traceback to *err*."""
        try:
            for severity, message in produce():
                self.finding(severity, message)
        except Exception:  # noqa: BLE001 - a fault in one check ends that check only
            err.write(traceback.format_exc())


# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------


def _refuse(err: TextIO, error: str, message: str, remedy: str) -> None:
    err.write(format_error_contract(error, message, remedy) + "\n")


def _dev_tree(kanban_root: pathlib.Path, cfg: pathlib.Path, env: Mapping[str, str]) -> str:
    """The dev tree the checks read: project config, then environment, then kanban.cfg."""
    configured = read_cfg_key(cfg, "project", "dev_tree_path")
    if configured:
        return configured
    from_env = env.get("PGAI_DEV_TREE_PATH", "")
    if from_env:
        return from_env
    kanban_cfg = kanban_root / "kanban.cfg"
    if kanban_cfg.is_file():
        return ini.read_line_value(kanban_cfg, "paths", "dev_tree_path") or ""
    return ""


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def _queue_files(tasks_dir: pathlib.Path) -> list[pathlib.Path]:
    return [p for p in (tasks_dir / "queues" / f"{agent}_backlog.md" for agent in _QUEUE_AGENTS) if p.is_file()]


def _queue_entries(queue_files: Iterable[pathlib.Path]) -> Iterator[tuple[str, str, str]]:
    """``(task id, marker, queue file name)`` for every queue entry line."""
    for qpath in queue_files:
        if not qpath.is_file():
            continue
        for line in qpath.read_text().splitlines():
            m = _LINE_PAT.match(line)
            if m:
                yield m.group(2), m.group(1), qpath.name


def _state_line(status_file: pathlib.Path) -> Optional[str]:
    """The first non-blank, non-``#`` line after an exact ``## State`` line."""
    in_state = False
    for line in status_file.read_text().splitlines():
        if line.strip() == '## State':
            in_state = True
            continue
        if in_state:
            value = line.strip()
            if value and not value.startswith('#'):
                return value
    return None


def _check_a(report: _Report, kanban_root: pathlib.Path, project: str, release_state: pathlib.Path) -> None:
    report.heading("Check a: release-state.md consistency")
    if not release_state.is_file():
        report.error(f"release-state.md not found: {release_state}")
        return
    report.ok(f"release-state.md exists: {release_state}")

    try:
        results = validate_release_state_fields(str(release_state))
    except Exception:  # noqa: BLE001 - an unreadable file validates nothing
        results = []

    last_released = ""
    for result in results:
        if result.ok:
            value = result.value or ""
            if result.heading == "Active RC":
                report.ok("Active RC is 'none' (no RC in flight)" if value == "none"
                          else f"Active RC has valid format: {value}")
            elif result.heading == "RC Opened At":
                report.ok(f"RC Opened At timestamp valid: {value}")
            elif result.heading == "RC Opened By Task":
                report.ok(f"RC Opened By Task is valid: {value}")
            elif result.heading == "Last Released":
                last_released = value
                report.ok("Last Released is 'none' (no prior release)" if value == "none"
                          else f"Last Released has valid format: {value}")
        elif result.heading in ("Active RC", "RC Opened At", "RC Opened By Task", "Last Released"):
            report.error(f"release-state.md: {result.message} — remedy: {result.remedy} — file: {release_state}")

    if last_released and last_released != "none":
        latest_tag = last_released_version(kanban_root, project)
        if latest_tag == "v0.0.0":
            report.warn(
                f"release-state.md: could not determine latest git tag to validate Last Released "
                f"'{last_released}' — git may be unavailable or no tags exist on origin/main"
            )
        elif semver.eq(last_released, latest_tag):
            report.ok(f"Last Released matches most-recent git tag: {last_released}")
        else:
            report.warn(
                f"release-state.md: Last Released '{last_released}' does not match most-recent git tag "
                f"'{latest_tag}' — file: {release_state} (may be intentional if tag not yet squash-merged "
                f"to origin/main)"
            )


def _queue_folder_findings(tasks_dir: pathlib.Path, queue_files: list[pathlib.Path]) -> Iterator[tuple[str, str]]:
    all_queued = {tid: (marker, qname) for tid, marker, qname in _queue_entries(queue_files)}
    open_queued = {tid: entry for tid, entry in all_queued.items() if entry[0] != 'x'}

    # Only task ids (OWNER-ROLE-YYYYMMDD-NNN) live under tasks/; BUG- and
    # PRIORITY- ids belong to their own directories.
    ok1 = 0
    for tid, (_marker, qname) in sorted(open_queued.items()):
        if not _TASK_ID_PAT.match(tid):
            continue
        folder = tasks_dir / tid
        if folder.is_dir():
            ok1 += 1
        else:
            yield "ERROR", f"Queue entry has no folder on disk: {tid} — referenced in {qname} but {folder} does not exist"
    if ok1:
        yield "OK", f"Queue->folder: {ok1} open entries have matching folders"

    ok2 = 0
    for d in sorted(tasks_dir.iterdir()):
        if not d.is_dir() or d.name in ('queues', 'README.md') or not _TASK_ID_PAT.match(d.name):
            continue
        if d.name in all_queued:
            ok2 += 1
            continue
        status_file = d / 'status.md'
        if status_file.is_file():
            state = _state_line(status_file) or 'unknown'
            if state in ('DONE', 'WONT-DO'):
                ok2 += 1
            else:
                yield "WARN", (f"Task folder {d.name} (state: {state}) has no matching queue entry — "
                               "tasks/queues may be out of sync")
        else:
            yield "WARN", f"Task folder {d.name} has no status.md and no queue entry — orphaned task folder"
    if ok2:
        yield "OK", f"Folder->queue: {ok2} task folders accounted for"


def _check_b(report: _Report, tasks_dir: pathlib.Path, err: TextIO) -> None:
    report.heading("Check b: Queue <-> folder consistency")
    if not tasks_dir.is_dir():
        report.warn(f"Tasks directory not found: {tasks_dir} — skipping queue/folder checks")
        return
    queue_files = _queue_files(tasks_dir)
    if not queue_files:
        report.warn(f"No queue backlog files found under {tasks_dir}/queues — skipping queue/folder checks")
        return
    report.findings(lambda: _queue_folder_findings(tasks_dir, queue_files), err)


# Expected queue marker per state; BLOCKED prefers [B] and accepts the
# legacy [ ] and [A] with a warning.
_EXPECTED_MARKER = {
    'BACKLOG': ' ',
    'WAITING': 'W',
    'WORKING': 'A',
    'BLOCKED': ('B', ' ', 'A'),
    'DONE': 'x',
    'WONT-DO': 'x',
}


def _state_marker_findings(tasks_dir: pathlib.Path, queue_files: list[pathlib.Path]) -> Iterator[tuple[str, str]]:
    id_to_marker: dict[str, str] = {}
    id_to_qfile: dict[str, str] = {}
    for tid, marker, qname in _queue_entries(queue_files):
        id_to_marker[tid] = marker
        id_to_qfile[tid] = qname

    c_ok = 0
    for d in sorted(tasks_dir.iterdir()):
        if not d.is_dir() or not _TASK_ID_PAT.match(d.name) or d.name not in id_to_marker:
            continue
        status_file = d / 'status.md'
        if not status_file.is_file():
            continue
        state = _state_line(status_file) or 'none'
        marker = id_to_marker[d.name]
        qref = id_to_qfile[d.name]
        if state == 'none':
            yield "WARN", f"status/marker: {d.name} has no ## State field in {status_file}"
            continue
        if state not in _EXPECTED_MARKER:
            yield "WARN", f"status/marker: {d.name} has unknown state '{state}' in {status_file}"
            continue
        expected = _EXPECTED_MARKER[state]
        if isinstance(expected, tuple):
            if marker not in expected:
                yield "ERROR", (f"status/marker mismatch: {d.name} state={state} but queue marker=[{marker}] "
                                f"in {qref} (expected [B] or legacy [ ]/[A]) — status: {status_file}")
            elif marker in (' ', 'A'):
                yield "WARN", f"status/marker: {d.name} state=BLOCKED has marker=[{marker}] in {qref} (prefer [B])"
            else:
                c_ok += 1
        elif marker == expected:
            c_ok += 1
        else:
            exp_display = f"[{expected}]" if expected != ' ' else "[ ]"
            yield "ERROR", (f"status/marker mismatch: {d.name} state={state} but queue marker=[{marker}] "
                            f"in {qref} (expected {exp_display}) — status: {status_file}")
    if c_ok:
        yield "OK", f"State/marker: {c_ok} task(s) consistent"


def _check_c(report: _Report, tasks_dir: pathlib.Path, err: TextIO) -> None:
    report.heading("Check c: status.md state <-> queue marker consistency")
    if not tasks_dir.is_dir():
        report.warn(f"Tasks directory not found: {tasks_dir} — skipping state/marker checks")
        return
    queue_files = _queue_files(tasks_dir)
    if not queue_files:
        report.warn("No queue backlog files found — skipping state/marker checks")
        return
    report.findings(lambda: _state_marker_findings(tasks_dir, queue_files), err)


def _prerequisite_findings(tasks_dir: pathlib.Path) -> Iterator[tuple[str, str]]:
    """Check d's findings: every prerequisite a live task names must resolve.

    A prerequisite resolves to its live task folder first; one with no
    live folder resolves when the task has been archived
    (``<kanban_root>/archive/<project>/tasks/<id>/``, the archive of the
    kanban root *tasks_dir* sits in), since only finished tasks are
    archived. Only a prerequisite that is neither live nor archived is
    reported as a dangling reference.
    """
    task_folders = {d.name for d in tasks_dir.iterdir() if d.is_dir() and _TASK_ID_PAT.match(d.name)}
    archived: dict[str, bool] = {}
    d_ok = 0
    d_archived = 0
    for d in sorted(tasks_dir.iterdir()):
        if not d.is_dir() or not _TASK_ID_PAT.match(d.name):
            continue
        readme = d / 'README.md'
        if not readme.is_file():
            continue
        in_prereqs = False
        for line in readme.read_text().splitlines():
            if line.strip() == '## Prerequisites':
                in_prereqs = True
                continue
            if not in_prereqs:
                continue
            if line.startswith('## '):
                break
            for m in _PREREQ_ID_PAT.finditer(line.strip().rstrip('.,')):
                prereq_id = m.group(1).rstrip('.,')
                if prereq_id in task_folders:
                    d_ok += 1
                    continue
                if prereq_id not in archived:
                    archived[prereq_id] = bool(archived_task_ids(tasks_dir, [prereq_id]))
                if archived[prereq_id]:
                    d_archived += 1
                else:
                    yield "ERROR", (f"Prerequisites: dangling reference in {d.name} README — '{prereq_id}' "
                                    f"has no task folder at {tasks_dir / prereq_id}")
    if d_ok:
        yield "OK", f"Prerequisites: {d_ok} reference(s) resolved"
    if d_archived:
        yield "OK", f"Prerequisites: {d_archived} reference(s) resolved to archived (finished) tasks"


def _check_d(report: _Report, tasks_dir: pathlib.Path, err: TextIO) -> None:
    report.heading("Check d: Prerequisites references")
    if not tasks_dir.is_dir():
        report.warn(f"Tasks directory not found: {tasks_dir} — skipping prerequisites checks")
        return
    report.findings(lambda: _prerequisite_findings(tasks_dir), err)


def _is_git_repo(path: str, env: Mapping[str, str]) -> bool:
    """True when ``git -C <path> rev-parse --git-dir`` succeeds.

    A path with no ``.git`` at or above it is not a repository and starts no
    process; otherwise git itself answers.
    """
    directory = pathlib.Path(path)
    if not directory.is_dir():
        return False
    if not env.get("GIT_DIR"):
        resolved = directory.resolve()
        if not any((candidate / ".git").exists() for candidate in (resolved, *resolved.parents)):
            return False
    return _git_ok(path, ["rev-parse", "--git-dir"])


def _git_ok(repo: str, args: list[str]) -> bool:
    try:
        return subprocess.run(
            ["git", "-C", repo, *args], capture_output=True, stdin=subprocess.DEVNULL, check=False
        ).returncode == 0
    except OSError:
        return False


def _git_branches(repo: str, pattern: str) -> list[str]:
    """``git branch --list <pattern>`` names, with the leading marker columns removed."""
    try:
        done = subprocess.run(
            ["git", "-C", repo, "branch", "--list", pattern],
            capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False,
        )
    except OSError:
        return []
    branches = []
    for branch in done.stdout.splitlines():
        branch = branch[2:] if branch.startswith("  ") else branch
        branch = branch[1:] if branch.startswith(" ") else branch
        branch = branch[2:] if branch.startswith("* ") else branch
        if branch:
            branches.append(branch)
    return branches


def _branch_prefix(project: str, cfg: pathlib.Path, err: TextIO) -> str:
    """The project's configured branch prefix; an invalid one stops the run with exit 1."""
    raw = read_cfg_key(cfg, "project", "branch_prefix")
    raw = raw[:-1] if raw.endswith('"') else raw
    raw = raw[1:] if raw.startswith('"') else raw
    if raw and not _BRANCH_PREFIX_RE.match(raw):
        err.write(
            f"project_paths.sh: validate_branch_prefix: invalid branch_prefix '{raw}' (allowed characters: "
            "a-z A-Z 0-9 _ -; spaces, slashes, dots, and other special characters are not permitted)\n"
            f"project_paths.sh: pp_branch_prefix: invalid branch_prefix in config for project '{project}'\n"
        )
        raise _Stop(1)
    return raw


def _check_e(report: _Report, project: str, cfg: pathlib.Path, release_state: pathlib.Path, repo_root: str,
             env: Mapping[str, str], err: TextIO) -> None:
    report.heading("Check e: release-state.md <-> git branch consistency")
    if not release_state.is_file():
        report.warn("release-state.md not found — skipping git branch consistency checks")
        return
    if not _is_git_repo(repo_root, env):
        report.warn(f"Dev tree at {repo_root} is not a git repository — skipping git branch checks")
        return

    active_rc = read_state_field(str(release_state), "Active RC")
    prefix = _branch_prefix(project, cfg, err)
    rc_glob = f"{prefix}rc/*"
    rc_branches = _git_branches(repo_root, rc_glob)

    # On a prefixed project an unprefixed rc/* branch is reported too: the
    # prefixed glob never lists it.
    bare_rc_glob = "rc/*"
    if rc_glob != bare_rc_glob:
        for branch in _git_branches(repo_root, bare_rc_glob):
            report.warn(
                f"Stray unprefixed {bare_rc_glob} branch found on a project configured with branch_prefix: "
                f"{branch} — expected RC branches to use the configured prefix instead ({rc_glob}); may be "
                "orphaned pre-prefix work"
            )

    if active_rc == "none":
        if not rc_branches:
            report.ok(f"Active RC is none and no local {rc_glob} branches exist")
        for branch in rc_branches:
            report.warn(f"release-state.md shows Active RC=none but local branch exists: {branch} — may be "
                        "operator-staged or orphaned work")
        return

    rc_branch = f"{prefix}rc/{active_rc}"
    if _git_ok(repo_root, ["rev-parse", "--verify", f"refs/heads/{rc_branch}"]):
        report.ok(f"Active RC {active_rc} has matching local branch: {rc_branch}")
    else:
        report.error(f"release-state.md Active RC={active_rc} but local branch {rc_branch} does not exist in "
                     f"{repo_root}")
    for branch in rc_branches:
        if branch != rc_branch:
            report.warn(f"Extra {rc_glob} branch found: {branch} (Active RC is {active_rc}) — may be a stale or "
                        f"orphaned branch in {repo_root}")


def _bundled_ids(req_file: pathlib.Path) -> list[str]:
    """The BUG-/PRIORITY- ids under ``## Bundled Items``, first match per line, deduplicated."""
    ids: list[str] = []
    in_section = False
    for line in req_file.read_text().splitlines():
        if line.strip() == '## Bundled Items':
            in_section = True
            continue
        if not in_section:
            continue
        if line.startswith('## '):
            break
        m = _BUNDLE_REF_PAT.search(line)
        if m:
            ident = m.group(1)
            ident = ident[:-3] if ident.endswith('.md') else ident
            if ident not in ids:
                ids.append(ident)
    return ids


def _bundle_findings(priority_dir: pathlib.Path, bugs_dir: pathlib.Path, priority_backlog: pathlib.Path,
                     bug_backlog: pathlib.Path, req_files: list[pathlib.Path]) -> Iterator[tuple[str, str]]:
    f_ok = 0
    for req_file in req_files:
        for ref_id in _bundled_ids(req_file):
            if ref_id.startswith('PRIORITY-'):
                ref_dir = priority_dir
            elif ref_id.startswith('BUG-'):
                ref_dir = bugs_dir
            else:
                continue
            candidate = next(iter(ref_dir.glob(ref_id + '*.md')), None) if ref_dir.is_dir() else None
            if candidate is not None:
                yield "OK", f"Bundle ref exists: {ref_id} -> {candidate} (in {req_file.name})"
                f_ok += 1
            else:
                yield "ERROR", (f"Bundle invariant: {req_file.name} references {ref_id} but no matching file "
                                f"found in {ref_dir}")

    bundled = {"PRIORITY": set(), "BUG": set()}
    for req_file in req_files:
        for ref_id in _bundled_ids(req_file):
            for kind in bundled:
                if ref_id.startswith(kind + '-'):
                    bundled[kind].add(ref_id)

    for backlog, kind in ((priority_backlog, 'PRIORITY'), (bug_backlog, 'BUG')):
        if not backlog.is_file():
            continue
        ok_n = 0
        for line in backlog.read_text().splitlines():
            m = _LINE_PAT.match(line)
            if not (m and m.group(1) == 'x') or not m.group(2).startswith(kind + '-'):
                continue
            if m.group(2) in bundled[kind]:
                ok_n += 1
            else:
                yield "WARN", (f"{kind} backlog [x]: {m.group(2)} is marked done but not found in any active "
                               "requirements file — may have been bundled into a superseded RC or already shipped")
        if ok_n:
            yield "OK", f"{kind} backlog: {ok_n} [x] entries accounted for in active bundles"

    if f_ok:
        yield "OK", f"Bundle invariants: {f_ok} reference(s) validated"


def _check_f(report: _Report, project_root: pathlib.Path, err: TextIO) -> None:
    report.heading("Check f: Bundle invariants")
    requirements_dir = project_root / "requirements"
    if not requirements_dir.is_dir():
        report.warn(f"Requirements directory not found: {requirements_dir} — skipping bundle invariant checks")
        return
    names = sorted(
        (entry.name for entry in requirements_dir.iterdir()
         if entry.name.endswith(".md") and not entry.name.startswith(".")),
        key=functools.cmp_to_key(locale.strcoll),
    )
    req_files = [
        requirements_dir / name for name in names
        if (requirements_dir / name).is_file() and name != "README.md" and "SUPERSEDED" not in str(requirements_dir / name)
    ]
    if not req_files:
        report.ok("No active requirements files found (nothing to validate)")
        return
    queues = project_root / "tasks" / "queues"
    report.findings(
        lambda: _bundle_findings(project_root / "priority", project_root / "bugs",
                                 queues / "priority_backlog.md", queues / "bug_backlog.md", req_files),
        err,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _run(ctx: OpsContext, project_arg: str, verbose: bool, env: Mapping[str, str], out: TextIO, err: TextIO) -> int:
    kanban_root = ctx.kanban_root
    try:
        project = resolve_project_name(ctx, project_arg)
    except (NotFound, Ambiguous) as exc:
        err.write(f"{exc}\n")
        return 2
    except OpsError as exc:
        err.write(f"{exc}\n")
        return 2

    project_root = kanban_root / "projects" / project
    if not project_root.is_dir():
        # BLOCK-WORTHY (docs/blocking-site-census.md): with no projects.cfg
        # the resolver passes a name through unchecked, so this directory
        # check is the backstop that refuses a project never found rather
        # than checking a directory that does not exist.
        _refuse(err, "verify_rc_state.project_directory_missing",
                f"{_SCRIPT}: project directory not found: {project_root}.",
                f"Check the project name against projects.cfg, or create it first with "
                f"team/scripts/create-project.sh --project {project}.")
        # CENSUS-ANCHOR: verify-rc-state-project-directory-missing-01
        return 2

    cfg = project_cfg_file(project_root)
    if cfg is None:
        err.write(f"project_paths.sh: PROJECT.cfg not found at {project_root / 'PROJECT.cfg'} "
                  f"(and project.cfg not found at {project_root / 'project.cfg'})\n")
        # BLOCK-WORTHY (docs/blocking-site-census.md): every check below
        # depends on the project's config, so there is nothing left to check.
        _refuse(err, "verify_rc_state.project_config_unloadable",
                f"{_SCRIPT}: could not load project config for '{project}'.",
                f"Check that {project_root}/project.cfg exists and is valid INI.")
        # CENSUS-ANCHOR: verify-rc-state-project-config-unloadable-01
        return 2

    repo_root = _dev_tree(kanban_root, cfg, env)
    if not repo_root:
        # BLOCK-WORTHY (docs/blocking-site-census.md): every branch and tag
        # check needs a dev tree, so this refuses rather than silently
        # skipping half the report.
        _refuse(err, "verify_rc_state.dev_tree_path_unset",
                f"{_SCRIPT}: dev_tree_path is not set in project config for '{project}'.",
                f"Set [project] dev_tree_path in {project_root}/project.cfg.")
        # CENSUS-ANCHOR: verify-rc-state-dev-tree-path-unset-01
        return 2

    release_state = project_root / "release-state.md"
    tasks_dir = project_root / "tasks"
    out.write(f"{_SCRIPT} — project: {project}\n")
    out.write(f"Kanban root:  {kanban_root}\n")
    out.write(f"Project root: {project_root}\n")
    out.write(f"Dev tree:     {repo_root}\n")

    report = _Report(out, verbose)
    try:
        _check_a(report, kanban_root, project, release_state)
        _check_b(report, tasks_dir, err)
        _check_c(report, tasks_dir, err)
        _check_d(report, tasks_dir, err)
        _check_e(report, project, cfg, release_state, repo_root, env, err)
        _check_f(report, project_root, err)
    except _Stop as stop:
        return stop.exit_code

    out.write(f"\nverify-rc-state: {report.error_count} errors, {report.warn_count} warnings, {report.ok_count} ok\n")
    return 1 if report.error_count else 0


def verify_rc_state(
    ctx: OpsContext,
    project: str,
    verbose: bool = False,
    *,
    environ: Optional[Mapping[str, str]] = None,
) -> ReadOutput:
    """Return what ``verify-rc-state.sh PROJECT [--verbose]`` prints and exits with.

    Args:
        ctx:     OpsContext carrying the kanban root.
        project: Project name or unambiguous prefix, as typed.
        verbose: Also print every ``[OK]`` line.
        environ: Environment read for ``PGAI_DEV_TREE_PATH`` and ``GIT_DIR``
                 (default: this process's).

    Returns:
        The ReadOutput: exit 0 with no ERROR finding, 1 with any, 2 for a
        pre-flight refusal (stdout empty, the refusal in stderr).
    """
    out, err = io.StringIO(), io.StringIO()
    exit_code = _run(ctx, project, verbose, os.environ if environ is None else environ, out, err)
    return ReadOutput(exit_code=exit_code, stdout=out.getvalue(), stderr=err.getvalue())


#: Verbs ``python3 -m pgai_agent_kanban.ops`` routes to :func:`_cli_main`.
CLI_VERBS: tuple[str, ...] = ("verify_rc_state",)


def _cli_main(argv: Optional[list] = None) -> int:
    """``python3 -m pgai_agent_kanban.ops verify_rc_state KANBAN_ROOT PROJECT [VERBOSE]``.

    Prints :func:`verify_rc_state`'s stdout and stderr and exits with its
    code. VERBOSE ``1`` prints every ``[OK]`` line. The requirements files
    of check f are ordered by the environment's collation, as a shell glob
    orders them. A wrong argument count prints the usage and exits 2.
    """
    args = sys.argv[1:] if argv is None else argv
    rest = args[1:]
    if not 2 <= len(rest) <= 3:
        print("usage: python3 -m pgai_agent_kanban.ops verify_rc_state KANBAN_ROOT PROJECT [VERBOSE]",
              file=sys.stderr)
        return 2
    try:
        locale.setlocale(locale.LC_COLLATE, "")
    except locale.Error:
        pass
    output = verify_rc_state(OpsContext(kanban_root=pathlib.Path(rest[0])), rest[1],
                             verbose=len(rest) == 3 and rest[2] == "1")
    sys.stdout.write(output.stdout)
    sys.stdout.flush()
    sys.stderr.write(output.stderr)
    sys.stderr.flush()
    return output.exit_code
