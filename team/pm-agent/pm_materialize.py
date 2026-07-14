#!/usr/bin/env python3
"""
pm_materialize.py — Take a task plan JSON from the pm subagent
and create the actual task folders, README.md files, status.md files,
and backlog queue entries.

Usage:
    python3 pm_materialize.py task_plan.json [--team-root /path/to/kanban] [--dry-run]

This is invoked by scripts/pm-agent.sh after the pm subagent
produces the plan JSON. You can also run it directly if you have an
existing plan file you want to re-materialize.

Plan JSON schema (per task):
    sequence            int
    slug                str (kebab-case, < 30 chars)
    title               str
    role                "CODER" or "WRITER"
    assigned_agent      str (optional) — e.g. "coder", "writer". Overrides role-based routing.
    working_directory   str — absolute path, "local-development-only", or "none"
    git_repo            str — repo URL or "none"
    source_branch       str — typically "main" or "rc/<version>", or "none" if no git
    goal                str
    inputs              list[str]
    context_paths       list[str]
    required_output     str
    constraints         list[str]
    acceptance_criteria list[str]
    depends_on          list[int] — sequence numbers of prerequisite tasks
    notes               str

Top-level plan fields:
    workflow_type           str — plugin name resolved as workflows/<type>/pipeline.yaml.
                                  "release" is the default when absent or empty.
                                  Any registered plugin directory is a valid value; the set
                                  is not closed.  A plugin without pipeline.yaml decomposes
                                  via the simple wf_agents path (no pipeline steps).
    source_branch           str — shared branch name for the shared-branch decomposition mode
                                  (workflow_type="feature"; falls back to active RC if unset)
    test_required           str — "true" (default) or "false"; controls TESTER task injection
    parent_branch           str — branch the shared feature branch is created from (default: "main")
    target_version          str — semver version, or "none"
    human_approval_required str — "required" or "auto" (default)
    requirements_path       str — absolute path of the requirements doc (optional)
    sections                list[str] — for workflow_type=document: explicit section names used to
                                        generate one section-draft ticket per section.
                                        When absent or empty, a single placeholder section-draft ticket
                                        is emitted.
    artifact_name           str — (document only) output artifact name slug (e.g. "whitepaper").
                                  When absent, derived from the requirements filename descriptor.
                                  Surfaced to WRITER tasks as ## Artifact Name in their README.
    source_documents        list[str] — (document only) list of artifact slugs to resolve
                                        from <team_root>/artifacts/ and provide to WRITER as input.
                                        Each slug must match a file in artifacts/ (glob <slug>.*).
                                        Missing slugs cause a hard error (exit 1).
                                        Absent = start-fresh path (WRITER works from brief only).

The materializer:
- Assigns final task IDs (AGENT-YYYYMMDD-SEQ-slug)
- Resolves depends_on sequence numbers to full task IDs
- Generates feature_branch as "feature/<task-id>"
- Writes the README and status files
- Appends task IDs to per-agent queue files based on assigned_agent or role:
    CODER -> tasks/queues/coder_backlog.md
    WRITER -> tasks/queues/writer_backlog.md
    HUMAN -> tasks/queues/human_backlog.md (gate tasks visible in dashboard)

workflow_type controls the decomposition mode — how PM assembles the task pipeline for a
plan.  Each mode is resolved as a plugin directory (workflows/<type>/).  A plugin may carry
a pipeline.yaml for rich multi-step decomposition; a plugin without pipeline.yaml uses the
simple wf_agents path (single-track decomposition, no pipeline steps).

Note on terminology: "work tasks" below means the operator-authored tasks in the submitted
plan JSON (the CODER/WRITER tasks PM decomposes).  These are distinct from the
"shared-branch decomposition mode" (workflow_type="feature"), which is an assembly-mode
concept, not a description of what tasks contain.

  workflow_type = 'document':
    Unified document pipeline with CM open-doc / finalize bookends. Dispatches to
    inject_document_workflow_tasks() using the workflow definition loaded from
    team/workflows/document/pipeline.yaml (resolved via the type string). Operator
    specifies sections in the plan-level 'sections' field (list of section names);
    one section-draft WRITER ticket is emitted per section.

    Section strategy: operator-defined sections (fallback from runtime-foreach).
    The plan JSON must include a 'sections' list at the top level. If absent or
    empty, a single placeholder section-draft ticket is emitted. Runtime-foreach
    (where section names are derived from a previous step's deliverable at
    materialize time of subsequent tasks) is deferred to a later iteration.

    Pipeline order:
        CM-open-doc        — first task, role CM, cm_operation=open_doc, no prereqs
        WRITER-outline     — depends on CM-open-doc
        WRITER-section-draft (one per section) — each depends on WRITER-outline
        WRITER-integrate   — depends on all section-draft tickets
        WRITER-polish      — depends on WRITER-integrate
        TESTER-review      — depends on WRITER-polish
        CM-finalize        — last task, role CM, cm_operation=finalize, depends on TESTER-review

  workflow_type = 'release' (default, or any type whose workflow.cfg declares
                              git_mode=rw and finalize=tag/publish):
    CM bookend injection is conditional on the plugin's declared capabilities.
    When the plugin manifest declares git_mode=rw AND finalize in {tag, publish}
    (the release-lifecycle shape), CM-open-rc is prepended at position 1 and
    CM-release is appended at the final position.  When target_version is 'none'
    or absent, the version label 'unversioned' is used for the bookend task IDs
    and a WARNING is emitted.  A HUMAN-APPROVE gate is injected only when the
    plan's human_approval_required field is 'required'.  For workflow types whose
    manifest declares git_mode != rw or finalize not in {tag, publish} (e.g.
    testing-only with finalize=report), no CM bookends are injected.

        human_approval_required = 'required':
            CM-open       — first task, role CM, no prereqs
            (work tasks, each depends_on CM-open)
            TESTER        — verify task, role TESTER, prereqs = all work task IDs
            HUMAN-APPROVE — gate task, role HUMAN, prereqs = all work task IDs + TESTER ID
            CM-release    — last task, role CM, prereqs = all work IDs + TESTER + HUMAN-APPROVE

        human_approval_required = 'auto' (default / missing):
            CM-open       — first task, role CM, no prereqs
            (work tasks, each depends_on CM-open)
            TESTER        — verify task, role TESTER, prereqs = all work task IDs
            CM-release    — last task, role CM, prereqs = all work task IDs + TESTER ID

    Invalid values for human_approval_required log a WARNING to stderr and default
    to 'auto'.

  workflow_type = 'feature' [shared-branch decomposition mode]:
    NOTE: "feature" here is a DECOMPOSITION MODE name, not a description of task content.
    It means: a shared development branch is created first and all work tasks branch from
    it.  Do not confuse with the term "work tasks" (the operator-submitted tasks in any
    workflow type).

    No CM bookends. Instead a CODER create-shared-branch ticket is prepended as
    ticket 1. A TESTER task is appended when test_required=True.

        test_required = 'true' (default):
            CODER(create-shared-branch) — first task
            (work tasks, each depends_on create-shared-branch)
            TESTER — verify task, prereqs = all work task IDs

        test_required = 'false':
            CODER(create-shared-branch) — first task
            (work tasks, each depends_on create-shared-branch)

    If source_branch is not set and no Active RC is found in release-state.md,
    the materializer exits with an error (exit code 1).
"""

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from datetime import datetime, timezone

# lib.config provides get_config() for config-driven directory defaults.
# Insert the pm-agent directory onto sys.path so the import works regardless
# of the caller's working directory.
sys.path.insert(0, str(Path(__file__).parent))
from lib.config import get_config
from lib.workflow_loader import load_workflow, WorkflowError

# pgai_agent_kanban package lives one level up from pm-agent/ (i.e. under team/).
# Insert team/ onto sys.path so the import works from any caller working directory.
sys.path.insert(0, str(Path(__file__).parent.parent))
from pgai_agent_kanban.lib.terminal_states import is_terminal as _bundle_is_terminal


# ---------------------------------------------------------------------------
# Dev tree path resolution
# ---------------------------------------------------------------------------


def _resolve_dev_tree_path(team_root: str) -> str:
    """Resolve the dev tree path using env-then-config-then-fail precedence.

    Resolution order (first non-empty value wins):

      1. ``PGAI_DEV_TREE_PATH`` environment variable — the canonical runtime override.
      2. ``dev_tree_path`` key in ``project.cfg`` (or ``PROJECT.cfg``) found
         directly under *team_root*.
      3. ``dev_tree_path`` key under the ``[paths]`` section of ``kanban.cfg`` found
         directly under *team_root* (single-project layout) or under the directory
         named by the ``PGAI_AGENT_KANBAN_ROOT_PATH`` environment variable (multi-project
         layout where *team_root* is a per-project subdirectory).
      4. **Fail loudly** with a clear error message and ``sys.exit(1)``.  No silent
         hardcoded path guess is used.

    Parameters
    ----------
    team_root:
        Absolute path to the project root (i.e. the directory that contains
        ``requirements/``, ``tasks/``, and ``project.cfg``).

    Returns
    -------
    str
        The resolved dev tree path (guaranteed non-empty when returned; the function
        never returns an empty string).

    Raises
    ------
    SystemExit(1)
        When the path cannot be resolved from any source.
    """

    # --- 1. Environment variable (highest precedence) -------------------------
    env_val = (os.environ.get("PGAI_DEV_TREE_PATH") or "").strip()
    if env_val:
        return env_val

    # --- 2. project.cfg / PROJECT.cfg -----------------------------------------
    root = Path(team_root)
    _new_cfg = root / "project.cfg"
    _old_cfg = root / "PROJECT.cfg"
    proj_cfg = _new_cfg if _new_cfg.is_file() else (_old_cfg if _old_cfg.is_file() else None)
    if proj_cfg is not None:
        for line in proj_cfg.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" in stripped and stripped.split("=", 1)[0].strip() == "dev_tree_path":
                val = stripped.split("=", 1)[1].strip().strip("'\"").strip()
                if val:
                    return val

    # --- 3. kanban.cfg [paths] dev_tree_path ----------------------------------
    # Try team_root/kanban.cfg first (single-project layout), then the
    # PGAI_AGENT_KANBAN_ROOT_PATH location (multi-project layout).
    _kanban_cfg_candidates = [root / "kanban.cfg"]
    _kanban_root_env = (os.environ.get("PGAI_AGENT_KANBAN_ROOT_PATH") or "").strip()
    if _kanban_root_env:
        _kanban_cfg_candidates.append(Path(_kanban_root_env) / "kanban.cfg")

    for kanban_cfg_path in _kanban_cfg_candidates:
        if not kanban_cfg_path.is_file():
            continue
        in_paths_section = False
        for line in kanban_cfg_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("["):
                in_paths_section = stripped.lower() == "[paths]"
                continue
            if in_paths_section and "=" in stripped:
                key, _, raw_val = stripped.partition("=")
                if key.strip() == "dev_tree_path":
                    val = raw_val.strip().strip("'\"").strip()
                    if val:
                        return val

    # --- 4. Fail loudly -------------------------------------------------------
    print(
        "ERROR: dev tree path could not be resolved — set PGAI_DEV_TREE_PATH "
        "or configure dev_tree_path in project.cfg ([project] section) or "
        "kanban.cfg ([paths] section).",
        file=sys.stderr,
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# Auto-version sentinel constants and helpers
# ---------------------------------------------------------------------------

# Target Version field values that mean "compute the next available patch slot
# at runtime."  Comparison is done case-insensitively after stripping whitespace.
_AUTO_VERSION_SENTINELS = frozenset({"auto", "next-patch", ""})


def _is_auto_sentinel(declared: str) -> bool:
    """Return True if *declared* is an auto-sentinel version value.

    Sentinels: 'auto', 'next-patch', empty string, or missing (None).

    Parameters
    ----------
    declared:
        The raw value of the ``## Target Version`` field, or empty/None when
        the field is absent from the requirements document.
    """
    if declared is None:
        return True
    return declared.strip().lower() in _AUTO_VERSION_SENTINELS


def _compute_next_patch_py(team_root: str, requirements_path_str: str = "none") -> str:
    """Compute the next available patch version slot.

    Python equivalent of the ``discovery_compute_next_patch`` shell helper in
    ``team/scripts/lib/discovery.sh``.  The same three collision sources are
    checked in the same order:

      1. A ``vX.Y.Z*.md`` file already exists in ``requirements/``
      2. A ``.materialized.*vX.Y.Z*`` marker exists under ``tasks/queues/plans/``
      3. A ``vX.Y.Z`` git tag exists in the dev tree (local or origin)

    The dev tree path is resolved via :func:`_resolve_dev_tree_path` using the
    env-then-config-then-fail precedence (PGAI_DEV_TREE_PATH > project.cfg >
    kanban.cfg > fail loudly).  No hardcoded path fallback is used.

    Parameters
    ----------
    team_root:
        Absolute path to the project root (i.e. the directory that contains
        ``requirements/``, ``tasks/``, and ``project.cfg``).
    requirements_path_str:
        Absolute path of the *current* requirements file being materialized,
        as a string.  When provided (not ``'none'``), the file's own path is
        excluded from collision check #1 so a file can materialise itself
        without falsely colliding with its own presence on disk.

    Returns
    -------
    str
        The chosen version string, e.g. ``"v0.21.12"``.
    """
    import glob
    import subprocess

    root = Path(team_root)
    requirements_dir = root / "requirements"
    plans_dir = root / "tasks" / "queues" / "plans"

    # --- Resolve the dev tree path -------------------------------------------
    dev_tree = _resolve_dev_tree_path(team_root)

    # --- Determine last_released via git tags on origin/main -----------------
    last_released = "v0.0.0"
    dev_tree_path = Path(dev_tree)
    if dev_tree_path.is_dir() and (dev_tree_path / ".git").exists():
        try:
            result = subprocess.run(
                ["git", "-C", dev_tree, "tag", "--merged", "origin/main"],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0:
                tags = [
                    t for t in result.stdout.splitlines()
                    if re.match(r'^v\d+\.\d+\.\d+$', t.strip())
                ]
                if tags:
                    tags.sort(key=lambda v: tuple(int(x) for x in v.lstrip("v").split(".")))
                    last_released = tags[-1]
        except Exception:
            pass  # Fall back to v0.0.0 on any error

    # Parse X.Y.Z from last_released
    clean = last_released.lstrip("v")
    parts = clean.split(".")
    while len(parts) < 3:
        parts.append("0")
    try:
        x, y, z = int(parts[0]), int(parts[1]), int(parts[2])
    except (ValueError, IndexError):
        x, y, z = 0, 0, 0

    # Exclude the current requirements file from collision check #1
    exclude_path = ""
    if requirements_path_str and requirements_path_str.lower() != "none":
        exclude_path = str(Path(requirements_path_str).resolve())

    # Bump-around loop — mirrors the shell helper's three-source collision check
    while True:
        z += 1
        candidate = f"v{x}.{y}.{z}"

        # Source 1: drafted file in requirements/
        if requirements_dir.is_dir():
            matches = glob.glob(str(requirements_dir / f"{candidate}*.md"))
            if any(str(Path(m).resolve()) != exclude_path for m in matches):
                continue

        # Source 2: materialized marker in tasks/queues/plans/
        if plans_dir.is_dir():
            if glob.glob(str(plans_dir / f".materialized.*{candidate}*")):
                continue

        # Source 3: git tag in the dev tree
        if dev_tree_path.is_dir() and (dev_tree_path / ".git").exists():
            try:
                # Check local tags
                result = subprocess.run(
                    ["git", "-C", dev_tree, "tag", "-l", candidate],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0 and result.stdout.strip():
                    continue
                # Check origin tags
                result = subprocess.run(
                    ["git", "-C", dev_tree, "ls-remote", "--tags", "origin",
                     f"refs/tags/{candidate}"],
                    capture_output=True, text=True, timeout=10,
                )
                if result.returncode == 0 and f"refs/tags/{candidate}" in result.stdout:
                    continue
            except Exception:
                pass  # If git check fails, assume slot is free

        return candidate


def _derive_artifact_name(requirements_path_str: str) -> str:
    """Derive the artifact name from the requirements filename when none is declared.

    Strips the leading version prefix (``vX.Y.Z-``) and the trailing ``.md``
    extension from the filename, leaving the descriptor slug.

    For example, ``v0.44.0-unified-document-workflow-20260602.md`` produces
    ``unified-document-workflow-20260602``.

    Parameters
    ----------
    requirements_path_str:
        Absolute path (or basename) of the requirements file.  When ``"none"``
        or empty, the fallback ``"document"`` is returned.

    Returns
    -------
    str
        A non-empty slug usable as the artifact name.
    """
    if not requirements_path_str or requirements_path_str.lower() == "none":
        return "document"
    stem = Path(requirements_path_str).stem  # filename without .md
    # Strip leading vX.Y.Z- prefix if present.
    stripped = re.sub(r"^v\d+\.\d+\.\d+-?", "", stem)
    return stripped if stripped else stem


def _resolve_source_documents(source_slugs: list, artifacts_dir: "Path | None",
                               project_name: str = "") -> tuple:
    """Resolve a list of artifact slugs to absolute file paths.

    Each slug is matched against files in *artifacts_dir* using a glob
    ``<slug>.*``.  If exactly one match is found, that path is used.  If
    multiple files match (e.g. ``foo.md`` and ``foo.txt``), the first
    alphabetically is used and a warning is emitted.  If no file matches,
    the slug is added to the unresolved list.

    Parameters
    ----------
    source_slugs:
        List of artifact slug strings (e.g. ``["v0.1.3-slide-deck"]``).
    artifacts_dir:
        Absolute ``Path`` to the ``projects/<proj>/artifacts/`` directory.
        When ``None`` or non-existent, every slug is treated as unresolved.
    project_name:
        Optional project name for error message context.

    Returns
    -------
    tuple[list[str], list[str]]
        ``(resolved_paths, unresolved_slugs)`` — the caller handles
        unresolved slugs (typically by aborting with a clear error message).
    """
    resolved: list = []
    unresolved: list = []

    if not source_slugs:
        return resolved, unresolved

    for slug in source_slugs:
        slug = slug.strip()
        if not slug:
            continue

        if artifacts_dir is None or not artifacts_dir.is_dir():
            unresolved.append(slug)
            continue

        matches = sorted(artifacts_dir.glob(f"{slug}.*"))
        # Also check for slug-only (no extension) for extensionless artifacts.
        exact_match = artifacts_dir / slug
        if exact_match.is_file() and exact_match not in matches:
            matches = [exact_match] + matches

        if not matches:
            unresolved.append(slug)
        else:
            if len(matches) > 1:
                print(
                    f"[pm-materialize] WARNING: source slug '{slug}' matched "
                    f"{len(matches)} files in artifacts/; using first: {matches[0].name}",
                    file=sys.stderr,
                )
            resolved.append(str(matches[0]))

    return resolved, unresolved


def resolve_target_version(declared: str, team_root: str,
                            requirements_path_str: str = "none") -> tuple:
    """Resolve the effective target version for a requirements file.

    When *declared* is an auto-sentinel (``'auto'``, ``'next-patch'``, empty,
    or ``None``), the next available patch slot is computed at runtime via
    ``_compute_next_patch_py`` and ``(resolved, True)`` is returned so the
    caller knows to emit a log line.

    When *declared* is an explicit ``vX.Y.Z`` value, it is returned verbatim
    and ``(declared, False)`` is returned.

    Parameters
    ----------
    declared:
        Raw ``## Target Version`` value from the requirements doc (or ``None``
        / empty if the field is absent).
    team_root:
        Absolute path to the project root.
    requirements_path_str:
        Absolute path of the requirements file (passed through to
        ``_compute_next_patch_py`` to exclude it from collision check #1).

    Returns
    -------
    tuple[str, bool]
        ``(version_str, was_auto_resolved)``
    """
    if _is_auto_sentinel(declared):
        computed = _compute_next_patch_py(team_root, requirements_path_str)
        return computed, True

    # Explicit version — return as-is; bump-around is handled by CM/discovery
    return declared.strip(), False


# ---------------------------------------------------------------------------
# Shared Path C language constants for generated TESTER task README fields
# ---------------------------------------------------------------------------
# These constants ensure that every generated TESTER/verify README uses the
# same Path C model language as roles/TESTER.md — gaps found do NOT trigger
# BLOCKED; they trigger Path C filings + SHIP-WITH-CONCERNS/SERIOUS-CONCERNS.
# BLOCKED is reserved for genuine infrastructure failure only.
#
# DO NOT change the wording here without also updating roles/TESTER.md.
# These are the single source of truth for generated TESTER task text.

_TESTER_PATH_C_REQUIRED_OUTPUT = (
    "artifacts/report.md with verification findings. "
    "Task DONE on clean pass. If gaps or bugs are found, file them via "
    "Path C (BUG or PRIORITY under projects/<project>/bugs/ or priority/) and "
    "recommend SHIP-WITH-CONCERNS or SHIP-WITH-SERIOUS-CONCERNS in "
    "artifacts/report.md. BLOCKED is for infrastructure failure only "
    "(e.g. test runner unavailable, dev tree unreachable)."
)

_TESTER_PATH_C_ACCEPTANCE_CRITERIA = [
    "Verification report exists at artifacts/report.md.",
    "Task DONE on clean pass or after filing gaps via Path C. "
    "BLOCKED is reserved for infrastructure failure only, not for found gaps.",
]

_TESTER_PATH_C_NOTES_SUFFIX = (
    "Path C model: gaps/bugs found → file via Path C (BUG/PRIORITY) and "
    "recommend SHIP-WITH-CONCERNS or SHIP-WITH-SERIOUS-CONCERNS. "
    "BLOCKED is for infrastructure failure only, never for found gaps."
)

# Document-workflow TESTER review variant (Path C framing)
_TESTER_DOC_REVIEW_PATH_C_ACCEPTANCE_CRITERIA = [
    "review-report.md exists.",
    "Task DONE when review is complete. If gaps are found, note them in "
    "review-report.md and recommend revisions. "
    "BLOCKED is for infrastructure failure only, not for found gaps.",
]


TASK_README_TEMPLATE = """# Task: {title}

## Task ID
{task_id}

## Owner
{owner}

## Role
{role}

## Assigned Agent
{assigned_agent}

## Working Directory
{working_directory}

## Git Repo
{git_repo}

## Source Branch
{source_branch}

## Feature Branch
{feature_branch}

## Workflow Type
{workflow_type}
{model_override_section}
## Goal
{goal}

## Inputs
{inputs}

## Context Paths
{context_paths}

## Required Output
{required_output}

## Constraints
{constraints}

## Acceptance Criteria
{acceptance_criteria}

## Prerequisites
{prerequisites}

## Release Version
{release_version}

## Notes
{notes}
{artifact_name_section}{source_documents_section}"""

STATUS_TEMPLATE = """# Status

## Task
{task_id}

## Participant
{participant}

## Role
{role}

## State
{initial_state}

## Summary
Task created by PM Agent. Waiting for {participant} to pull from backlog and begin work.

## Artifacts
none

## Blockers
none

## Needs Human
no

## Next Recommended Step
{participant} should read task README.md and begin work. Move to WORKING when starting.

## Instruction Conflicts
none
"""

HUMAN_APPROVE_README_TEMPLATE = """# Task: Human Approval Gate — {version}

## Task ID
{task_id}

## Owner
Human

## Role
HUMAN

## Assigned Agent
HUMAN

## Working Directory
none

## Git Repo
none

## Source Branch
none

## Feature Branch
none

## Goal
Review all feature tasks for release {version} and approve or reject proceeding to the CM-release step.

This is a human gate. No automated agent will process this task.

## Inputs
{feature_task_list}

## Context Paths
none

## Required Output
A decision: approve (set status.md State to DONE) or reject (set status.md State to BLOCKED or leave as BACKLOG).

## Constraints
- Only a human should update this task.
- Automated agents must not mark this task DONE.
- If you reject, set State to BLOCKED and add a note in ## Blockers explaining why.

## Acceptance Criteria
- [ ] All feature tasks listed under ## Inputs have been reviewed.
- [ ] Human has set status.md ## State to DONE to approve, or BLOCKED to reject.

## Prerequisites
{prerequisites}

## Release Version
{version}

## Notes
### How to approve
Edit this file's companion status.md and change:
    ## State
    BACKLOG
to:
    ## State
    DONE

This will unblock the CM-release task ({cm_release_id}) which is waiting on this gate.

### How to reject
Edit status.md and set:
    ## State
    BLOCKED

Add a note in ## Blockers explaining the reason. The CM-release task will not proceed.

### What this gate controls
The CM-release task ({cm_release_id}) has this task as a prerequisite.
The wake script will not dispatch CM-release until this task reaches DONE state.
"""


def format_list(items, prefix="- "):
    """Format a list as markdown bullet points."""
    if not items:
        return "none"
    return "\n".join(f"{prefix}{item}" for item in items)


def format_checklist(items):
    """Format a list as markdown checkboxes."""
    if not items:
        return "- [ ] Task completes successfully"
    return "\n".join(f"- [ ] {item}" for item in items)


def get_next_sequence(tasks_root, owner, date_str):
    """Find the next available sequence number for a given date.

    Scans all task folder names matching the new format
    (ROLE-DATE-NNN-slug) and returns max_found + 1.

    The ``owner`` parameter is accepted but no longer used — the new format
    has no owner prefix.
    """
    tasks_path = Path(tasks_root)
    if not tasks_path.is_dir():
        return 1

    existing = []

    for d in tasks_path.iterdir():
        if not d.is_dir():
            continue
        name = d.name
        parts = name.split("-")
        if len(parts) < 4:
            continue

        # New format: ROLE-DATE-NNN-slug (parts[1] is 8-digit date)
        date_candidate = parts[1]
        seq_candidate = parts[2]

        if date_candidate != date_str:
            continue
        if not (len(seq_candidate) >= 1 and seq_candidate.isdigit()):
            continue
        try:
            existing.append(int(seq_candidate))
        except ValueError:
            pass

    return max(existing, default=0) + 1


def assign_task_ids(tasks, owner, tasks_root):
    """Generate task_id and feature_branch for each task in the plan.

    Task ID format: {ROLE}-{date_str}-{seq:03d}-{slug}
    e.g. CODER-20260518-001-my-task

    The ``owner`` parameter is passed by ``main()`` and used in status template
    participant resolution; it is not embedded in the task ID.

    Skips tasks that already have a task_id set (synthetic CM/HUMAN tasks
    injected by inject_cm_bookends).

    Returns the (date_str, start_seq) tuple used so callers can avoid
    sequence number conflicts when generating additional synthetic IDs.
    """
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    start_seq = get_next_sequence(tasks_root, owner, date_str)

    seq_offset = 0
    for task in tasks:
        # Skip synthetic tasks that already have a task_id
        if task.get("task_id"):
            continue

        seq_num = start_seq + seq_offset
        seq_offset += 1
        slug = task.get("slug", f"task-{seq_num}")
        role = (task.get("role") or "CODER").upper()
        task_id = f"{role}-{date_str}-{seq_num:03d}-{slug}"
        task["task_id"] = task_id
        task["owner"] = owner

        # Generate feature branch from task ID, but only if git repo is set
        git_repo = (task.get("git_repo") or "none").strip()
        if git_repo and git_repo.lower() != "none":
            task["feature_branch"] = f"feature/{task_id}"
        else:
            task["feature_branch"] = "none"

    return date_str, start_seq


def resolve_prerequisites(tasks):
    """Convert each task's depends_on (sequence numbers) to a list of full task IDs.

    Merges newly resolved IDs with any existing entries already in
    prerequisite_ids (e.g. CM-open injected by inject_cm_bookends), so that
    bookend dependencies are preserved after resolution.
    """
    seq_to_id = {t.get("sequence"): t.get("task_id") for t in tasks}

    for task in tasks:
        deps = task.get("depends_on", []) or []
        resolved = []
        for dep_seq in deps:
            tid = seq_to_id.get(dep_seq)
            if tid:
                resolved.append(tid)

        # Merge: keep existing prereqs already set (e.g. CM-open from inject),
        # then append any newly resolved ones not already present.
        existing = task.get("prerequisite_ids", []) or []
        merged = list(existing)
        for tid in resolved:
            if tid not in merged:
                merged.append(tid)
        task["prerequisite_ids"] = merged


def normalize_workspace_value(value):
    """Normalize a working_directory or git_repo field to a clean string."""
    if value is None:
        return "none"
    s = str(value).strip()
    if not s:
        return "none"
    return s


_RECOGNIZED_MODEL_ALIASES = {"opus", "sonnet", "haiku"}
_RECOGNIZED_MODEL_PREFIXES = ("claude-opus-", "claude-sonnet-", "claude-haiku-")


def validate_model_override(value):
    """Validate a model_override value and warn if unrecognized.

    Recognized values:
      - Aliases: opus, sonnet, haiku
      - Patterns: claude-opus-*, claude-sonnet-*, claude-haiku-*
      - Empty string, None, or 'none' — treated as no override (valid).

    Unrecognized values produce a WARNING on stderr but are still accepted
    (warn-don't-block policy — the claude CLI will report the error at runtime).

    Returns the normalized value as a string (stripped). Returns 'none' when
    value is None, empty, or the literal string 'none'.
    """
    if not value:
        return "none"
    s = str(value).strip()
    if not s or s.lower() == "none":
        return "none"
    lower = s.lower()
    if lower in _RECOGNIZED_MODEL_ALIASES:
        return s
    if any(lower.startswith(prefix) for prefix in _RECOGNIZED_MODEL_PREFIXES):
        return s
    print(
        f"[pm-materialize] WARNING: unrecognized model_override value '{s}'. "
        f"Writing to README anyway. The claude CLI will report the error at runtime.",
        file=sys.stderr,
    )
    return s


def get_active_rc(team_root):
    """Return the active RC version (e.g. 'v0.4.0') or None.

    Reads team/release-state.md. Tolerant of missing file or missing field.
    """
    rs = Path(team_root) / "release-state.md"
    if not rs.is_file():
        return None
    text = rs.read_text()
    # Find the line "## Active RC" then read the next non-blank non-comment line
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == "## Active RC":
            for follow in lines[i + 1:]:
                v = follow.strip()
                if v and not v.startswith("#"):
                    return None if v == "none" else v
            break
    return None


def _resolve_branch_prefix(team_root):
    """Return the configured branch_prefix for the project, or '' when absent.

    Reads ``[project] branch_prefix`` from ``<team_root>/project.cfg``.
    Strips surrounding quotes so that ``branch_prefix = ""`` is treated as
    empty, not as the 2-char string ``""``.
    Validates the value against ``[A-Za-z0-9_-]*``; returns ``''`` silently
    if the value fails validation so that misconfigured installs degrade to
    no-prefix behavior rather than breaking materialisation.

    Empty return value means "no prefix" — callers produce ``rc/<version>``
    which is today's behavior for self-build and other pure-AI projects.
    """
    import re as _re
    cfg_path = Path(team_root) / "project.cfg"
    raw = _read_cfg_field(cfg_path, "branch_prefix")
    # _read_cfg_field already strips surrounding whitespace and quotes, but
    # double-quote stripping is belt-and-suspenders here.
    raw = raw.strip().strip('"').strip("'")
    if not raw:
        return ""
    # Validate: only letters, digits, underscore, and hyphen are allowed.
    if not _re.match(r"^[A-Za-z0-9_-]+$", raw):
        import sys as _sys
        print(
            f"pm_materialize: _resolve_branch_prefix: invalid branch_prefix "
            f"{raw!r} in project.cfg — characters outside [A-Za-z0-9_-] are "
            f"not permitted; falling back to no prefix.",
            file=_sys.stderr,
        )
        return ""
    return raw


def determine_source_branch(task, team_root, workflow_type=None, target_version=None):
    """Determine source branch with this precedence.

    For release workflows:
      1. <branch_prefix>rc/<target_version> from the plan — ALWAYS overrides
         per-task source_branch for tasks that should target the RC (CODER,
         WRITER, TESTER, CM-release).  ``branch_prefix`` is read from
         ``project.cfg [project] branch_prefix`` (default empty).
         The CM-open bookend is a special case: it branches from main because
         the RC branch is what it CREATES.
      2. Default to "main" if no target_version (defensive fallback)

    For non-release workflows (feature, etc.):
      1. Explicit task["source_branch"] from PM (highest priority)
      2. Active RC from release-state.md — returned as
         ``<branch_prefix>rc/<active>`` so the README matches the real branch
         name created by the prefix-aware shell layer.
      3. Default to "main"

    For release workflows, the RC branch ALWAYS wins for
    work tasks. PM populates per-task source_branch as its
    standard schema for git-enabled tasks, but during a release the work must
    land on the RC branch. Honoring per-task source_branch in release mode
    caused CODER tasks to merge into the wrong branch instead of rc/<target_version>,
    leaving the RC empty at ship time.

    The CM-open task is identified by cm_operation == "open-rc" and is excluded
    from the RC override — it branches from main because that is the single base
    branch from which all RCs are created.  branch_prefix does NOT affect the
    CM-open source; the parent_branch name for CM-open is determined elsewhere.
    """
    # Resolve branch_prefix once; empty string means "no prefix" (default).
    prefix = _resolve_branch_prefix(team_root)

    # For release workflow: <prefix>rc/<target_version> wins for work tasks.
    if (workflow_type or "").strip().lower() == "release":
        # CM-open is the bookend that CREATES the RC branch — it must source from main.
        cm_op = (task.get("cm_operation") or "").strip().lower()
        is_cm_open = cm_op == "open-rc"

        if not is_cm_open:
            tv = (target_version or "").strip()
            if tv and tv.lower() != "none":
                # target_version is canonical "v0.17.10"; tolerate missing leading "v"
                base = f"rc/{tv}" if tv.startswith("v") else f"rc/v{tv}"
                return f"{prefix}{base}"

        # CM-open task or no target_version: source from main.
        return "main"

    # Non-release workflows: honor explicit per-task source_branch first.
    sb = (task.get("source_branch") or "").strip()
    if sb and sb.lower() != "none":
        return sb
    active = get_active_rc(team_root)
    if active:
        return f"{prefix}rc/{active}"
    return "main"


# Roles that require an isolated git worktree at dispatch time.
# The wake script's TESTER path (claude.sh) unconditionally checks for
# ## Source Branch when the workflow's git_mode is not "none".  Any role
# listed here triggers the fail-closed source-ref guard at materialize time.
_WORKTREE_REQUIRING_ROLES = frozenset({"TESTER"})


def _read_source_branch_from_requirements(requirements_path: str) -> str:
    """Parse the ``## Source Branch`` field from a requirements document.

    Returns the stripped value when found, or an empty string when the
    file is absent, unreadable, or lacks the field.

    Parameters
    ----------
    requirements_path:
        Absolute path to the requirements Markdown file, or ``"none"``/empty
        to skip (returns ``""`` immediately).
    """
    if not requirements_path or requirements_path.strip().lower() == "none":
        return ""
    try:
        text = Path(requirements_path).read_text(encoding="utf-8")
    except OSError:
        return ""
    # Match "## Source Branch" as a section header (possibly followed by a
    # blank line) and capture the next non-blank, non-header line.
    for i, line in enumerate(text.splitlines()):
        if line.strip().lower() == "## source branch":
            # Scan ahead for the first non-empty, non-header line.
            for rest in text.splitlines()[i + 1:]:
                stripped = rest.strip()
                if stripped and not stripped.startswith("#"):
                    return stripped
    return ""


def _resolve_plan_source_branch(plan: dict, requirements_path: str) -> str:
    """Return the effective plan-level source branch for default-fill propagation.

    Resolution order (first non-empty, non-"none" value wins):
      1. ``plan["source_branch"]`` — explicitly set by PM in the plan JSON.
      2. ``## Source Branch`` field parsed from *requirements_path* — the
         requirements document the PM read when generating the plan.

    Returns an empty string when neither source provides a usable value, so
    callers can distinguish "nothing to propagate" from "propagate this value".

    Parameters
    ----------
    plan:
        The top-level plan dict (deserialized plan JSON).
    requirements_path:
        Absolute path to the requirements doc, or ``"none"``/empty to skip.
    """
    # 1. Plan JSON field.
    val = (plan.get("source_branch") or "").strip()
    if val and val.lower() != "none":
        return val
    # 2. Requirements doc.
    val = _read_source_branch_from_requirements(requirements_path)
    if val and val.lower() != "none":
        return val
    return ""


def _propagate_plan_source_branch(tasks: list, plan_source_branch: str) -> int:
    """Default-fill *plan_source_branch* onto every task that lacks an explicit value.

    "Lacks an explicit value" means the task's ``source_branch`` key is absent,
    empty, or the string ``"none"`` (case-insensitive).  Tasks that already
    carry a real source-branch value are **not** touched — this is strictly a
    default-fill, not an overwrite.

    Returns the number of tasks that were updated (for logging).

    Parameters
    ----------
    tasks:
        The fully assembled list of task dicts (work tasks + synthetics).
        Mutated in-place.
    plan_source_branch:
        The plan-level source ref to fill in.  When empty, the function is a
        no-op (returns 0) — callers may pass the result of
        ``_resolve_plan_source_branch()`` directly.
    """
    if not plan_source_branch:
        return 0
    updated = 0
    for task in tasks:
        existing = (task.get("source_branch") or "").strip()
        if not existing or existing.lower() == "none":
            task["source_branch"] = plan_source_branch
            updated += 1
    return updated


def _validate_source_ref_for_worktree_roles(
    tasks: list, wf_caps: dict, workflow_type: str
) -> None:
    """Fail-closed guard: refuse to continue when a worktree-requiring task lacks a source ref.

    For every task whose role is in ``_WORKTREE_REQUIRING_ROLES`` (currently
    ``TESTER``) and whose ``git_repo`` is not ``"none"``, the task MUST carry
    a non-empty, non-``"none"`` ``source_branch``.  If it does not, the
    materializer prints a diagnostic to stderr naming the offending task ID
    and the missing field, then calls ``sys.exit(1)``.

    This guard fires **before** any task folders are created, so a failure
    leaves no half-materialized output — consistent with the BUG-0055
    receipt-rule inheritance named in BUG-0059.

    The guard is bypassed for release workflows: ``determine_source_branch``
    in ``create_task_folder`` always resolves ``rc/<target_version>`` for
    release work tasks, so per-task source_branch is irrelevant there.

    Parameters
    ----------
    tasks:
        The fully assembled, propagated list of task dicts.
    wf_caps:
        The workflow capabilities dict from ``load_workflow_capabilities()``.
        Used to check whether the workflow's ``git_mode`` requires a worktree.
    workflow_type:
        The normalised workflow-type string (e.g. ``"testing-only"``).
        Release workflows skip this guard entirely.
    """
    # Release workflow: determine_source_branch always supplies rc/<version>.
    if (workflow_type or "").strip().lower() == "release":
        return

    # Only enforce when the workflow plugin's git_mode requires a worktree.
    # A workflow with git_mode="none" (or unknown/missing caps) does not need
    # Source Branch for TESTER dispatch.
    wf_git_mode = (wf_caps.get("git_mode") or "").strip().lower()
    if wf_git_mode == "none":
        return
    # When caps are absent (empty dict), we cannot determine git_mode — skip
    # the guard rather than false-positive on custom plugins that predate the
    # manifest contract.
    if not wf_caps:
        return

    errors = []
    for task in tasks:
        role = (task.get("role") or "").upper()
        if role not in _WORKTREE_REQUIRING_ROLES:
            continue
        git_repo = (task.get("git_repo") or "").strip()
        if not git_repo or git_repo.lower() == "none":
            # No git_repo → no worktree required → guard does not apply.
            continue
        sb = (task.get("source_branch") or "").strip()
        if not sb or sb.lower() == "none":
            task_id = task.get("task_id") or task.get("slug") or "<unknown>"
            errors.append(
                f"  task {task_id!r} (role={role!r}): "
                f"'## Source Branch' is absent or 'none'"
            )

    if errors:
        print(
            "[pm-materialize] ERROR: fail-closed source-ref guard triggered.\n"
            "The following task(s) require a source branch for worktree dispatch "
            "but none is resolvable (neither the plan nor the ticket carries one):\n"
            + "\n".join(errors) + "\n"
            "Ensure the plan JSON or the requirements document carries a "
            "'source_branch' / '## Source Branch' value.  "
            "No task folders have been written.",
            file=sys.stderr,
        )
        sys.exit(1)


def is_human_task(task):
    """Return True if a task is a HUMAN-role task (gate task, operator-facing)."""
    role = (task.get("role") or "").upper()
    assigned = (task.get("assigned_agent") or "").upper()
    return role == "HUMAN" or assigned == "HUMAN"


def get_queue_path(task, team_root):
    """Return the queue file path for a task based on assigned_agent or role.

    Routing rule (flat queue layout):
        agent_lower = task.get('assigned_agent', task.get('role', 'CODER')).lower()
        queue_path  = f'{team_root}/tasks/queues/{agent_lower}_backlog.md'

    Tasks without an assigned_agent field fall back to the role field
    (CODER->coder, WRITER->writer, HUMAN->human).

    HUMAN-role tasks route to tasks/queues/human_backlog.md so the dashboard's
    queue-marker check can track their state without a 'no queue entry' warning.
    """
    agent_lower = task.get("assigned_agent", task.get("role", "CODER")).lower()
    return Path(team_root) / "tasks" / "queues" / f"{agent_lower}_backlog.md"


def compute_plan_hash(plan_content: str) -> str:
    """Return the SHA-256 hex digest of the plan file content.

    The hash is computed over the raw plan file bytes (as a string here, encoded
    to UTF-8). This gives a stable fingerprint for a given plan JSON regardless
    of which sequence numbers end up being assigned to tasks.

    Marker file naming convention:
        <plan-dir>/.materialized.<sha256-hash>

    The marker file contains:
        - timestamp: ISO-8601 UTC timestamp of when materialization completed
        - task_ids: newline-separated list of task IDs that were created

    Example marker file contents:
        timestamp: 2026-04-28T12:34:56Z
        task_ids:
          CODER-20260428-001-my-task
          CODER-20260428-002-other-task
    """
    return hashlib.sha256(plan_content.encode("utf-8")).hexdigest()


def find_existing_marker(plan_dir: Path, plan_hash: str):
    """Check if a materialization marker file already exists for this plan hash.

    Approach A: Plan-content hash idempotency guard.

    Returns the marker file Path if it exists, or None if this plan has not
    been materialized before (or the marker was deleted).

    Marker file path: <plan_dir>/.materialized.<sha256-hash>
    """
    marker_path = plan_dir / f".materialized.{plan_hash}"
    if marker_path.is_file():
        return marker_path
    return None


def write_marker_file(plan_dir: Path, plan_hash: str, task_ids: list) -> Path:
    """Write a marker file recording successful materialization of a plan.

    Approach A: Plan-content hash idempotency guard.

    Marker file format:
        timestamp: <ISO-8601 UTC>
        task_ids:
          <task-id-1>
          <task-id-2>
          ...

    Returns the Path of the written marker file.
    """
    marker_path = plan_dir / f".materialized.{plan_hash}"
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [f"timestamp: {timestamp}", "task_ids:"]
    for tid in task_ids:
        lines.append(f"  {tid}")
    marker_path.write_text("\n".join(lines) + "\n")
    return marker_path


def collect_existing_role_slugs(tasks_root: Path) -> set:
    """Scan existing task folders and return a set of (role, slug) pairs.

    Approach B: Role+slug duplicate detection regardless of sequence numbers.
    Accepts the current format (ROLE-YYYYMMDD-NNN-slug).

    Examples:
        CODER-20260518-001-my-task     -> ("CODER", "my-task")
        WRITER-20260518-002-doc-update -> ("WRITER", "doc-update")
        CM-20260518-003-open-rc-0-26-0 -> ("CM", "open-rc-0-26-0")

    Returns a set of (role_upper, slug) tuples for all parseable task folders.
    Only folders matching the expected format are included; malformed names are
    silently skipped.
    """
    existing = set()
    if not tasks_root.is_dir():
        return existing

    for d in tasks_root.iterdir():
        if not d.is_dir():
            continue
        parts = d.name.split("-")

        # Current format: ROLE-YYYYMMDD-NNN-slug (minimum 4 parts)
        if len(parts) < 4:
            continue
        role = parts[0].upper()
        date_candidate = parts[1]
        seq_candidate = parts[2]
        slug = "-".join(parts[3:])

        # date part must be 8 digits
        if not (len(date_candidate) == 8 and date_candidate.isdigit()):
            continue
        # seq part must be numeric (1-6 digits)
        if not (1 <= len(seq_candidate) <= 6 and seq_candidate.isdigit()):
            continue

        existing.add((role, slug))

    return existing


def check_role_slug_duplicate(role: str, slug: str, existing_role_slugs: set) -> bool:
    """Return True if (role, slug) already exists among existing tasks.

    Approach B: Role+slug duplicate detection.

    Used before creating each task to detect if a task with the same logical
    identity (role + slug) already exists, even if the sequence number differs.
    """
    return (role.upper(), slug) in existing_role_slugs


# ---------------------------------------------------------------------------
# Collision recycling helpers
# ---------------------------------------------------------------------------

def find_collision_folder(tasks_root: Path, role: str, slug: str):
    """Return the Path of an existing task folder matching (role, slug), or None.

    Scans *tasks_root* for any task folder whose parsed (role, slug) matches
    the given pair.  Returns the first match found, or None if no match.

    Accepts the current format (ROLE-YYYYMMDD-NNN-slug).
    """
    role_upper = role.upper()
    if not tasks_root.is_dir():
        return None
    for d in tasks_root.iterdir():
        if not d.is_dir():
            continue
        parts = d.name.split("-")
        if len(parts) < 4:
            continue
        d_role = parts[0].upper()
        date_candidate = parts[1]
        seq_candidate = parts[2]
        d_slug = "-".join(parts[3:])
        if not (len(date_candidate) == 8 and date_candidate.isdigit()):
            continue
        if not (1 <= len(seq_candidate) <= 6 and seq_candidate.isdigit()):
            continue
        if d_role == role_upper and d_slug == slug:
            return d
    return None


def get_task_state(task_dir: Path) -> str:
    """Return the ## State value from a task folder's status.md, or '' if unreadable.

    Parses the first non-blank, non-comment line after a '## State' heading.
    Returns the stripped state string (e.g. 'WONT-DO', 'DONE', 'BACKLOG'),
    or an empty string when the file is absent or the section is missing.
    """
    status_path = task_dir / "status.md"
    if not status_path.is_file():
        return ""
    try:
        lines = status_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    in_state = False
    for line in lines:
        stripped = line.strip()
        if stripped == "## State":
            in_state = True
            continue
        if in_state:
            if stripped.startswith("##"):
                # Hit next section without finding a value
                break
            if stripped and not stripped.startswith("#"):
                return stripped
    return ""


def get_task_readme_release_version(task_dir: Path) -> str:
    """Return the ## Release Version value from a task folder's README.md, or '' if unreadable.

    Parses the first non-blank, non-comment line after a '## Release Version' heading.
    Returns the stripped version string (e.g. 'v0.1.0', 'v0.42.7'),
    or an empty string when the file is absent, the section is missing, or the
    value is 'none'.

    Determines whether a colliding DONE/WONT-DO folder belongs to a strictly
    older release than the plan being materialized.
    """
    readme_path = task_dir / "README.md"
    if not readme_path.is_file():
        return ""
    try:
        lines = readme_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    in_section = False
    for line in lines:
        stripped = line.strip()
        if stripped == "## Release Version":
            in_section = True
            continue
        if in_section:
            if stripped.startswith("##"):
                break
            if stripped and not stripped.startswith("#"):
                return "" if stripped.lower() == "none" else stripped
    return ""


def _parse_semver_tuple(version: str):
    """Parse a semver string like 'v0.1.0' or '0.42.8' into a comparable tuple.

    Returns a tuple of integers (major, minor, patch) on success, or None if
    the string cannot be parsed as a three-part semver version.

    Compares the colliding folder's release version against the current
    plan's target_version to determine staleness.
    """
    v = version.strip().lstrip("v")
    parts = v.split(".")
    if len(parts) != 3:
        return None
    try:
        return (int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return None


def _is_strictly_older_version(candidate: str, current: str) -> bool:
    """Return True if *candidate* semver is strictly older than *current*.

    Both strings are expected to be in 'vX.Y.Z' or 'X.Y.Z' format.
    Returns False when either string cannot be parsed (safe default: do not
    treat unparseable versions as older, so the caller falls through to the
    normal blocking path).
    """
    c_tuple = _parse_semver_tuple(candidate)
    cur_tuple = _parse_semver_tuple(current)
    if c_tuple is None or cur_tuple is None:
        return False
    return c_tuple < cur_tuple


def _apply_rename_map_to_prerequisites(text: str, rename_map: dict,
                                       known_old_ids: set = None) -> str:
    """Rewrite task-ID references in prerequisite-style sections of *text*.

    Scans each line.  When inside a ## Prerequisites, ## Blocked By Task, or
    ## Depends On section, each line that looks like a task-ID reference is
    rewritten according to *rename_map* ({old_id: new_id}).

    Rename behaviour:
      - Lines whose candidate ID is in *rename_map* keys are replaced with the
        mapped new ID.
      - Lines whose candidate ID is already a *rename_map* value are kept as-is
        (idempotent re-application).

    Dropped-task behaviour (only when *known_old_ids* is provided):
      - A candidate ID that is in *known_old_ids* but NOT in *rename_map* keys
        is considered a "dropped" task — removed from the section with a log
        line.  This handles the case where a prior-plan task was not included
        in the new plan (and therefore has no rename entry).
      - When *known_old_ids* is None (default), no lines are removed; all IDs
        not in the rename map are passed through verbatim.

    Dedup: when two old IDs map to the same new ID, only the first occurrence
    of the new ID is kept; subsequent duplicates are silently dropped.

    Sections NOT in the target list are passed through verbatim.

    Parameters
    ----------
    text:
        Full text of the file (README.md or status.md) to rewrite.
    rename_map:
        Mapping of old task IDs to new task IDs.
    known_old_ids:
        Optional set of task IDs that were part of the cancelled plan.  When
        an ID in this set is found in a prerequisite section and is NOT in
        *rename_map*, it is treated as dropped and removed.
    """
    _PREREQ_HEADERS = {"## Prerequisites", "## Blocked By Task", "## Depends On"}
    lines = text.splitlines(keepends=True)
    out = []
    in_prereq_section = False
    seen_ids: set = set()

    for line in lines:
        stripped = line.strip()

        # Section header detection
        if stripped.startswith("##"):
            in_prereq_section = stripped in _PREREQ_HEADERS
            seen_ids = set()
            out.append(line)
            continue

        if not in_prereq_section:
            out.append(line)
            continue

        # Inside a prerequisite section: handle task-ID lines.
        # A task-ID line looks like "- ROLE-DATE-NNN-slug" or the literal "none".
        candidate = stripped.lstrip("- ").strip()
        if not candidate or candidate == "none":
            out.append(line)
            continue

        # Check if the candidate is an old ID that needs renaming
        if candidate in rename_map:
            new_id = rename_map[candidate]
            if new_id in seen_ids:
                # Dedup: already emitted this new ID, drop this line
                print(
                    f"[pm-materialize] INFO: deduplicated prerequisite: {candidate} -> {new_id} "
                    f"(already present after rename)",
                    file=sys.stderr,
                )
                continue
            seen_ids.add(new_id)
            # Preserve the leading "- " prefix
            prefix = "- " if stripped.startswith("-") else ""
            out.append(f"{prefix}{new_id}\n")
        elif candidate in rename_map.values():
            # Already a new ID (possibly from a different rename); keep it
            if candidate in seen_ids:
                print(
                    f"[pm-materialize] INFO: deduplicated prerequisite: {candidate} (already present)",
                    file=sys.stderr,
                )
                continue
            seen_ids.add(candidate)
            out.append(line)
        elif known_old_ids is not None and candidate in known_old_ids:
            # ID is from the cancelled plan but NOT being renamed → dropped task
            print(
                f"[pm-materialize] INFO: removed dropped prerequisite reference: {candidate} "
                f"(was in cancelled plan but not in new plan's rename map)",
                file=sys.stderr,
            )
            # do not append — line is removed
        else:
            # Not in rename map and not a known-dropped task — pass through verbatim
            out.append(line)

    return "".join(out)


def recycle_cancelled_folder(
    old_dir: Path,
    new_task_id: str,
    old_task_id: str,
    readme_content: str,
    status_content: str,
) -> Path:
    """Rename a cancelled (WONT-DO) task folder and write fresh contract files.

    Recycling preserves history (logs/ is retained in place; it moves with the
    folder rename) but replaces the contract files with fresh-rendered content
    from the current task dict.  A recycled folder's README.md and status.md
    are byte-equivalent to what a freshly-created folder would produce for the
    same task dict.

    Steps:
    1. Rename the folder from OLD path to NEW path (sibling of old_dir).
    2. Write readme_content to README.md — fully rendered from the current task
       dict via TASK_README_TEMPLATE; not a surgical edit of the prior corpse.
    3. Write status_content to status.md — fresh-task shape (State=BACKLOG)
       rendered via STATUS_TEMPLATE; not a surgical edit of the prior corpse.
    4. Remove stale artifact files from artifacts/ (leave the directory).
    5. Leave logs/ untouched — prior-life log evidence is preserved in place.

    Args:
        old_dir:        Path to the existing WONT-DO task folder.
        new_task_id:    New task ID (the folder will be renamed to this).
        old_task_id:    Old task ID (used only for the log message).
        readme_content: Fully-rendered README.md text from the current task dict.
        status_content: Fresh-task status.md text from the current task dict.

    Returns the new folder Path.

    Raises OSError if the rename fails.
    """
    new_dir = old_dir.parent / new_task_id
    old_dir.rename(new_dir)
    print(
        f"[pm-materialize] INFO: recycled cancelled folder {old_dir.name} -> {new_task_id}",
        file=sys.stderr,
    )

    # --- Write fresh contract files from the current task dict ---
    # Overwrite (or create) README.md and status.md with current-generation
    # content.  The prior corpse bytes are discarded; what lands on disk is
    # identical to what a fresh folder creation would produce for the same
    # task dict.
    (new_dir / "README.md").write_text(readme_content, encoding="utf-8")
    (new_dir / "status.md").write_text(status_content, encoding="utf-8")

    # --- Clear stale artifacts ---
    # artifacts/ is cleared so the incoming agent starts from a clean slate.
    # logs/ is left untouched — the prior-life log evidence moves with the
    # folder rename and is preserved for audit purposes.
    artifacts_dir = new_dir / "artifacts"
    if artifacts_dir.is_dir():
        for artifact in artifacts_dir.iterdir():
            if artifact.is_file():
                artifact.unlink()
            elif artifact.is_dir():
                import shutil
                shutil.rmtree(artifact)

    return new_dir


def apply_rename_map_globally(tasks_root: Path, rename_map: dict,
                              known_old_ids: set = None) -> None:
    """Apply a full rename map to prerequisite references across ALL task folders.

    For each task folder in *tasks_root*, rewrites ## Prerequisites, ## Blocked By
    Task, and ## Depends On sections in both README.md and status.md:

    - OLD task IDs (keys in rename_map) are replaced with NEW task IDs (values).
    - Duplicate entries that result from two old IDs mapping to the same new ID
      are collapsed to a single entry.

    When *known_old_ids* is provided, any ID in that set that is NOT in
    rename_map.keys() is treated as a dropped task reference and removed from
    prerequisite sections (with a log line).

    This function is called once after all individual folder operations complete,
    so it sees the full rename map and can apply it atomically across all folders.

    Parameters
    ----------
    tasks_root:
        Directory containing all task folders.
    rename_map:
        Mapping of old task IDs to new task IDs ({old: new}).
    known_old_ids:
        Optional set of all task IDs from the cancelled plan (including dropped
        ones).  When provided, IDs in this set that have no rename entry are
        removed from prerequisite sections.
    """
    if not rename_map:
        return
    if not tasks_root.is_dir():
        return

    for task_dir in tasks_root.iterdir():
        if not task_dir.is_dir():
            continue
        for fname in ("README.md", "status.md"):
            fpath = task_dir / fname
            if not fpath.is_file():
                continue
            try:
                original = fpath.read_text(encoding="utf-8")
                rewritten = _apply_rename_map_to_prerequisites(
                    original, rename_map, known_old_ids=known_old_ids
                )
                if rewritten != original:
                    fpath.write_text(rewritten, encoding="utf-8")
                    print(
                        f"[pm-materialize] INFO: updated cross-task references in "
                        f"{task_dir.name}/{fname}",
                        file=sys.stderr,
                    )
            except OSError as exc:
                print(
                    f"[pm-materialize] WARNING: could not rewrite {fpath}: {exc}",
                    file=sys.stderr,
                )


def validate_team_root(team_root_path):
    """Validate that team_root is not inside a task directory.

    Checks path segments for task folder name patterns that indicate the path
    points inside an existing task folder.  Matches the current format:

    - ROLE-YYYYMMDD-NNN-slug  (e.g. CODER-20260518-001-x)

    Returns True if valid, False if it appears to be inside a task directory.
    """
    parts = Path(team_root_path).resolve().parts
    # Current format: ROLE-YYYYMMDD-NNN-
    _task_dir_pattern = re.compile(r'^[A-Z]+-\d{8}-\d{3}-')
    for part in parts:
        if _task_dir_pattern.match(part):
            return False
    return True


def create_task_folder(task, team_root, dry_run=False, release_version="none", requirements_path="none",
                       existing_role_slugs=None, workflow_type="release", tasks_root=None,
                       recycle_map=None):
    """Create a single task folder with README.md and status.md.

    requirements_path: absolute path of the plan's requirements doc. When set
    (not 'none'), it is prepended to the task's ## Inputs section so every
    materialized task can trace context back to the source requirements.

    workflow_type: the decomposition mode / plugin name (e.g. 'release', 'feature',
    'document', or any registered plugin) to include in the generated README.md
    ## Workflow Type field.  Passed through verbatim; not validated here.

    tasks_root: explicit Path (or str) for the tasks directory. Task folders are
    created at tasks_root/task_id. Must be provided — passing None raises ValueError.
    No environment variable fallback is used; pass tasks_root explicitly.

    recycle_map: optional mutable dict {old_task_id: new_task_id} to accumulate
    rename decisions for cross-task reference rewriting.  When provided,
    any Option A recycle decision is recorded here.  Callers should pass
    apply_rename_map_globally(tasks_root, recycle_map) after all create_task_folder
    calls to propagate renames across prerequisite references.
    """
    # tasks_root is required — no environment variable fallback is used.
    if tasks_root is None:
        raise ValueError(
            "tasks_root must be explicitly provided (no env var fallback). "
            "Pass tasks_root=<path> explicitly."
        )
    task_id = task["task_id"]
    owner = task.get("owner", "CLAUDE")
    role = task.get("role", "CODER")
    assigned_agent = task.get("assigned_agent", "")
    title = task.get("title", "Untitled Task")
    goal = task.get("goal", "No goal specified.")
    inputs = list(task.get("inputs", []) or [])
    context_paths = task.get("context_paths", [])
    required_output = task.get("required_output", "See acceptance criteria.")
    constraints = task.get("constraints", [])
    acceptance_criteria = task.get("acceptance_criteria", [])
    notes = task.get("notes", "none")

    # Prepend requirements_path to inputs when provided, so every task knows
    # which requirements doc it derives from.
    if requirements_path and requirements_path.lower() != "none":
        if requirements_path not in inputs:
            inputs = [requirements_path] + inputs

    working_directory = normalize_workspace_value(task.get("working_directory"))
    git_repo = normalize_workspace_value(task.get("git_repo"))
    feature_branch = normalize_workspace_value(task.get("feature_branch"))

    # Defense in depth: if a worker task's working_directory resolves to the
    # live install (KANBAN_ROOT), this is a self-build with no dev tree
    # specified. Workers will edit the live install instead of git-tracked
    # source. Override to the dev tree path and warn.
    #
    # Skip this check for non-worker roles (PM, TESTER) and for tasks with no
    # git_repo (which are file-workflow tasks that legitimately operate in the
    # task artifacts directory).
    if (
        role.upper() in ("CODER", "WRITER")
        and git_repo.lower() != "none"
        and working_directory.lower() not in ("none", "local-development-only", "")
    ):
        try:
            wd_resolved = str(Path(working_directory).resolve())
            kanban_resolved = str(Path(team_root).resolve())
            if wd_resolved == kanban_resolved:
                # Worker task pointing at the live install — corrupt by design.
                # Override to dev tree.
                dev_tree = _resolve_dev_tree_path(team_root)
                print(
                    f"[pm-materialize] DUPLICATE-TASK GUARD: task {task_id} "
                    f"working_directory '{working_directory}' resolves to the live "
                    f"install ({kanban_resolved}). Overriding to dev tree '{dev_tree}' "
                    f"to prevent agents editing the deployed copy.",
                    file=sys.stderr,
                )
                working_directory = dev_tree
        except (OSError, RuntimeError):
            # Path resolution failed — leave working_directory alone and let
            # the normal flow handle whatever the path is.
            pass

    model_override = validate_model_override(task.get("model_override"))
    # Only emit the Model Override section when there is a real, non-empty value.
    # Do not inject an empty/none Model Override field at all.
    if model_override and model_override.lower() != "none":
        model_override_section = f"## Model Override\n{model_override}\n"
    else:
        model_override_section = ""

    # Optional document-workflow fields: Artifact Name and Source Documents.
    # These are set by inject_document_workflow_tasks() on WRITER tasks in a
    # document pipeline when the plan declares them.  Non-document tasks
    # leave these fields absent (empty string → no section rendered).
    _artifact_name = (task.get("artifact_name") or "").strip()
    if _artifact_name:
        artifact_name_section = f"\n## Artifact Name\n{_artifact_name}\n"
    else:
        artifact_name_section = ""

    _source_doc_paths = task.get("source_document_paths") or []
    if _source_doc_paths:
        _sdp_formatted = format_list(list(_source_doc_paths))
        source_documents_section = f"\n## Source Documents\n{_sdp_formatted}\n"
    else:
        source_documents_section = ""

    # Determine source branch using RC-aware three-tier precedence:
    # (1) explicit task source_branch, (2) active RC from release-state.md, (3) main
    # For release workflows, rc/<target_version> always wins for work tasks.
    # The CM-open task is excluded from the override — it sources main because it creates the RC.
    #
    # Always exercise determine_source_branch for release workflows, regardless
    # of git_repo.  Non-release workflows fall back to normalize_workspace_value
    # when git_repo is "none".
    _wt_lower = (workflow_type or "").strip().lower()
    if _wt_lower == "release" or git_repo.lower() != "none":
        source_branch = determine_source_branch(
            task, team_root,
            workflow_type=workflow_type,
            target_version=release_version,
        )
    else:
        source_branch = normalize_workspace_value(task.get("source_branch"))

    prereq_ids = task.get("prerequisite_ids", [])
    if prereq_ids:
        prereq_text = format_list(prereq_ids)
    else:
        prereq_text = "none"

    participant_map = {"CLAUDE": "Claude", "HUMAN": "Human"}
    participant = participant_map.get(owner, owner)

    readme_content = TASK_README_TEMPLATE.format(
        title=title,
        task_id=task_id,
        owner=participant,
        role=role,
        assigned_agent=assigned_agent if assigned_agent else "none",
        working_directory=working_directory,
        git_repo=git_repo,
        source_branch=source_branch,
        feature_branch=feature_branch,
        workflow_type=workflow_type if workflow_type else "release",
        model_override_section=model_override_section,
        goal=goal,
        inputs=format_list(inputs) if inputs else "See context paths.",
        context_paths=format_list(context_paths) if context_paths else "none",
        required_output=required_output,
        constraints=format_list(constraints) if constraints else "none",
        acceptance_criteria=format_checklist(acceptance_criteria),
        prerequisites=prereq_text,
        release_version=release_version if release_version else "none",
        notes=notes if notes else "none",
        artifact_name_section=artifact_name_section,
        source_documents_section=source_documents_section,
    )

    # Set initial state to WAITING when the task has prerequisites,
    # BACKLOG when it has none. This lets the wake script dispatch correctly
    # without requiring a separate normalization pass.
    initial_state = "WAITING" if prereq_ids else "BACKLOG"

    status_content = STATUS_TEMPLATE.format(
        task_id=task_id,
        participant=participant,
        role=role,
        initial_state=initial_state,
    )

    # tasks_root is required (ValueError raised above if None).
    _effective_tasks_root = Path(tasks_root)
    task_dir = _effective_tasks_root / task_id
    readme_path = task_dir / "README.md"
    status_path = task_dir / "status.md"
    artifacts_dir = task_dir / "artifacts"
    logs_dir = task_dir / "logs"

    if dry_run:
        if task_dir.is_dir():
            print(f"[dry-run] Skipping existing task folder: {task_dir}/")
        else:
            # (dry-run): check role+slug duplicate with collision state awareness
            task_slug_dr = task.get("slug", "")
            if existing_role_slugs and check_role_slug_duplicate(role, task_slug_dr, existing_role_slugs):
                _dtr = Path(tasks_root)
                _cf = find_collision_folder(_dtr, role, task_slug_dr)
                _cs = get_task_state(_cf) if _cf else ""
                if _cf and _cs == "WONT-DO":
                    print(
                        f"[dry-run] Would recycle (rename) cancelled folder {_cf.name} -> {task_id} "
                        f"(Option A: state WONT-DO → BACKLOG)"
                    )
                elif _cf and _cs == "DONE":
                    # Terminal-state exemption: DONE prior task never blocks.
                    _cv = get_task_readme_release_version(_cf)
                    print(
                        f"[dry-run] Would create fresh folder {task_id} "
                        f"(Option C: terminal-state exemption — DONE collision {_cf.name} "
                        f"at version {_cv or 'unknown'}; prior folder left intact)"
                    )
                else:
                    # Option B: NON-terminal prior task blocks materialization.
                    print(
                        f"[dry-run] COLLISION BLOCK: ({role}, {task_slug_dr}) exists at "
                        f"{_cf.name if _cf else '(unknown)'} (state: {_cs or 'unknown'}). "
                        f"Option B: would block materialization. "
                        f"Resolution: wait for {_cf.name if _cf else '(unknown)'} to reach "
                        f"a terminal state (DONE or WONT-DO) before re-materializing."
                    )
            else:
                print(f"[dry-run] Would create: {task_dir}/")
                print(f"[dry-run]   README.md ({len(readme_content)} chars)")
                print(f"[dry-run]   status.md ({len(status_content)} chars) [initial_state={initial_state}]")
                print(f"[dry-run]   artifacts/")
                print(f"[dry-run]   logs/")
        return task_id

    # Idempotency guard: skip if the task folder already exists.
    if task_dir.is_dir():
        print(f"[pm-materialize] Skipping existing task: {task_id}", file=sys.stderr)
        return task_id

    # Role+slug duplicate detection.
    # When a (role, slug) collision is detected, determine which path to take:
    #
    # Terminal-state exemption: prior tasks in a terminal state (DONE or WONT-DO)
    # never block new materialization.  Re-verification of the same target on a
    # label/testing-only workflow is a normal lifecycle — the same slug legitimately
    # appears across sequential runs.  Blocking on a completed prior task would make
    # label-semantics re-runs impossible without manual intervention.
    #
    # Option A (recycle): colliding folder is WONT-DO → rename + reset to BACKLOG.
    # Option C (skip):    colliding folder is in terminal state DONE → do NOT touch
    #                     the prior folder; create the new task in a fresh folder.
    # Option B (block):   colliding folder is NON-terminal (WORKING/WAITING/BACKLOG
    #                     or state unknown) → return None so the caller skips both
    #                     folder creation AND queue entry.
    #
    # Never silently skip: every collision must produce either consistent state
    # (Option A / Option C) or a clean caller-visible error (Option B / None return).
    task_slug = task.get("slug", "")
    if existing_role_slugs and check_role_slug_duplicate(role, task_slug, existing_role_slugs):
        _effective_tasks_root_for_collision = Path(tasks_root)
        collision_folder = find_collision_folder(_effective_tasks_root_for_collision, role, task_slug)
        collision_state = get_task_state(collision_folder) if collision_folder else ""
        old_collision_id = collision_folder.name if collision_folder else "(unknown)"

        if collision_folder and collision_state == "WONT-DO":
            # Option A: recycle the cancelled folder.
            print(
                f"[pm-materialize] INFO: (role, slug) collision detected: "
                f"({role}, {task_slug}) — colliding folder {old_collision_id} is WONT-DO. "
                f"Option A: renaming to {task_id} and resetting state to BACKLOG.",
                file=sys.stderr,
            )
            try:
                recycle_cancelled_folder(
                    collision_folder,
                    task_id,
                    old_collision_id,
                    readme_content,
                    status_content,
                )
            except OSError as exc:
                print(
                    f"[pm-materialize] ERROR: Option A recycle failed for {old_collision_id} -> {task_id}: {exc}. "
                    f"Falling back to Option B (no writes).",
                    file=sys.stderr,
                )
                return None
            # Record the rename for cross-reference rewriting
            if recycle_map is not None:
                recycle_map[old_collision_id] = task_id
            # Update existing_role_slugs so subsequent tasks in the same run
            # don't collide with the newly-renamed folder.
            if existing_role_slugs is not None:
                existing_role_slugs.discard((role.upper(), task_slug))
                existing_role_slugs.add((role.upper(), task_slug))
            return task_id
        elif collision_folder and collision_state == "DONE":
            # Option C: terminal-state exemption — prior DONE task never blocks new work.
            # A completed task is finished; the same slug on a new run is a fresh unit of
            # work (different task ID, different date/seq).  This is normal on label/
            # testing-only workflows where the same verification target is re-run across
            # releases.  The prior DONE folder is left completely untouched.
            collision_version = get_task_readme_release_version(collision_folder)
            plan_version = (release_version or "").strip()
            print(
                f"[pm-materialize] INFO: (role, slug) collision detected: "
                f"({role}, {task_slug}) — colliding folder {old_collision_id} is DONE "
                f"(version {collision_version or 'unknown'}). "
                f"Terminal-state exemption: prior DONE folder left intact; "
                f"creating fresh folder for {task_id}.",
                file=sys.stderr,
            )
            # Remove the stale (role, slug) entry so the fresh folder can be
            # registered after creation below.
            if existing_role_slugs is not None:
                existing_role_slugs.discard((role.upper(), task_slug))
            # Fall through to the normal folder-creation path below.
        else:
            # Option B: colliding folder is NON-terminal (active or state unknown).
            # A task in WORKING, WAITING, or BACKLOG state is still in flight — creating
            # a duplicate would produce two concurrent tasks with the same identity.
            # Emit a structured error naming the blocking task and return None so the
            # caller skips both folder creation and queue entry.
            print(
                f"[pm-materialize] ERROR: (role, slug) collision — BLOCKING MATERIALIZATION: "
                f"({role}, {task_slug}) already exists at {old_collision_id} "
                f"(state: {collision_state or 'unknown'}). "
                f"New task ID would have been: {task_id}. "
                f"Resolution: wait for {old_collision_id} to reach a terminal state "
                f"(DONE or WONT-DO), or cancel it manually, before re-materializing.",
                file=sys.stderr,
            )
            return None

    task_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir.mkdir(exist_ok=True)
    logs_dir.mkdir(exist_ok=True)
    readme_path.write_text(readme_content)
    status_path.write_text(status_content)

    # Register newly created task in existing_role_slugs so subsequent tasks
    # in the same materialization run don't create the same (role, slug).
    if existing_role_slugs is not None:
        existing_role_slugs.add((role.upper(), task_slug))

    print(f"[pm-materialize] Created task: {task_id}", file=sys.stderr)
    return task_id


def validate_and_normalize_queue(queue_path):
    """Re-read a queue file and rewrite any non-canonical marker lines in place.

    Canonical format is exactly:
        - [marker] TASK_ID

    where marker is one of: space, x, X, or any single character (the wake script
    uses '[ ]' for pending and '[x]' for done), and TASK_ID is the first token on
    the line (no trailing description, no missing leading dash).

    Non-canonical lines are those that:
      - Are missing the leading '- ' prefix (e.g. '[ ] TASK-123')
      - Have trailing text after the TASK_ID (e.g. '- [ ] TASK-123 — some desc')

    Any rewritten line produces a WARNING on stderr.  Lines that are blank or
    do not look like marker lines at all are passed through unchanged.

    Args:
        queue_path: pathlib.Path pointing to the queue file.

    Returns:
        int: number of lines that were rewritten.
    """
    if not queue_path.is_file():
        return 0

    # Pattern that captures the checkbox marker and the task ID from a line
    # that may or may not have a leading '- '.
    # Matches both canonical ('- [ ] TASK-ID') and non-canonical ('[ ] TASK-ID')
    # forms, with an optional trailing description after whitespace.
    _MARKER_RE = re.compile(
        r"^(?P<dash>-\s+)?"         # optional leading dash + whitespace
        r"\[(?P<marker>[^\]]*)\]"   # [marker] — captures anything inside brackets
        r"\s+"                      # whitespace after marker
        r"(?P<task_id>\S+)"         # task ID — first non-whitespace token
        r"(?P<trail>.*)?$"          # optional trailing text
    )

    lines = queue_path.read_text().splitlines()
    rewritten = 0
    normalized = []

    for line in lines:
        m = _MARKER_RE.match(line)
        if m is None:
            # Not a marker line — pass through unchanged
            normalized.append(line)
            continue

        marker = m.group("marker")
        task_id = m.group("task_id")

        # [B] is not a valid queue marker. BLOCKED is a runtime task state
        # recorded in status.md, not a queue state. If a [B] marker appears
        # (from operator typo, doc confusion, or upstream bug),
        # normalize it to [ ] so the wake script's pickup regex matches.
        if marker.strip() == "B":
            print(
                f"[pm-materialize] WARNING: queue marker '[B]' is not valid; "
                f"normalizing to '[ ]' for {task_id} in {queue_path.name}",
                file=sys.stderr,
            )
            marker = " "

        canonical = f"- [{marker}] {task_id}"

        if line != canonical:
            # Line deviates from canonical — rewrite and warn
            print(
                f"[pm-materialize] WARNING: non-canonical queue line rewritten in {queue_path.name}: "
                f"{line!r} -> {canonical!r}",
                file=sys.stderr,
            )
            normalized.append(canonical)
            rewritten += 1
        else:
            normalized.append(line)

    if rewritten:
        queue_path.write_text("\n".join(normalized) + "\n")

    return rewritten


def queue_marker_for_task(task):
    """Return the correct queue marker character for a task.

    Tasks with non-empty prerequisite_ids are WAITING when first created;
    their queue marker must be 'W' so the wake script re-check pass can find
    and promote them once prereqs are satisfied.
    Tasks with no prerequisites start as BACKLOG and get the ' ' (space) marker.

    The materializer must NEVER write [B] markers. Only [W] and [ ] are valid.
    The BLOCKED state is a runtime state managed by the wake script, not an initial
    state assigned during materialization. Writing [B] here would cause the wake
    script's promotion pass to ignore those tasks, leaving them permanently stuck.

    Returns:
        'W'  — task has one or more prerequisites (initial state: WAITING)
        ' '  — task has no prerequisites (initial state: BACKLOG)
    """
    prereq_ids = task.get("prerequisite_ids") or []
    marker = "W" if prereq_ids else " "
    # Assertion guard: 'B' must never be returned from this function.
    assert marker != "B", (
        f"MARKER GUARD: queue_marker_for_task returned 'B' for task {task.get('task_id')!r} — "
        "materializer must only produce 'W' or ' ' markers"
    )
    return marker


def update_backlog(tasks, team_root, dry_run=False):
    """Append new tasks to per-agent queue files.

    Each task is routed to the queue file determined by get_queue_path():
        assigned_agent (if set) -> tasks/queues/<assigned_agent>_backlog.md
        role (fallback)         -> tasks/queues/<role_lower>_backlog.md

    Queue marker selection:
        Tasks with non-empty prerequisite_ids -> [W] (WAITING, needs re-check)
        Tasks with empty prerequisite_ids     -> [ ] (BACKLOG, ready to process)

    Tasks are grouped by their target queue so each file is written once.
    HUMAN-role gate tasks route to tasks/queues/human_backlog.md.
    """
    # Group (task_id, marker) pairs by their target queue path
    queue_map: dict = {}
    for task in tasks:
        queue_path = get_queue_path(task, team_root)
        marker = queue_marker_for_task(task)
        # Defensive guard — refuse to write [B] markers under any circumstance.
        # queue_marker_for_task() should never return 'B', but if something upstream
        # mutates the task dict between assignment and this point, catch it here.
        if marker == "B":
            print(
                f"[pm-materialize] BUG GUARD: refusing [B] marker for {task['task_id']}, using [W] instead",
                file=sys.stderr,
            )
            marker = "W"
        queue_map.setdefault(queue_path, []).append((task["task_id"], marker))

    for queue_path, task_entries in queue_map.items():
        if dry_run:
            # In dry-run mode, show which entries would be added (skipping already-present ones)
            existing_content = queue_path.read_text() if queue_path.is_file() else ""
            new_entries_list = [
                (tid, m) for tid, m in task_entries if tid not in existing_content
            ]
            skipped = len(task_entries) - len(new_entries_list)
            if new_entries_list:
                new_entries = "\n".join(f"- [{m}] {tid}" for tid, m in new_entries_list)
                print(f"\n[dry-run] Would append to {queue_path}:")
                print(new_entries)
            if skipped:
                print(f"[dry-run] Skipping {skipped} already-present task ID(s) in {queue_path.name}")
            continue

        queue_path.parent.mkdir(parents=True, exist_ok=True)

        existing = ""
        if queue_path.is_file():
            existing = queue_path.read_text()

        # Idempotency guard: only append task IDs not already present in the queue.
        new_entries_list = [(tid, m) for tid, m in task_entries if tid not in existing]
        skipped = len(task_entries) - len(new_entries_list)
        if skipped:
            print(
                f"[pm-materialize] Skipping {skipped} already-present task ID(s) in {queue_path.name}",
                file=sys.stderr,
            )

        if not new_entries_list:
            continue

        new_entries = "\n".join(f"- [{m}] {tid}" for tid, m in new_entries_list)
        separator = "\n" if existing and not existing.endswith("\n") else ""
        queue_path.write_text(existing + separator + new_entries + "\n")

        print(f"[pm-materialize] Added {len(new_entries_list)} tasks to backlog: {queue_path}", file=sys.stderr)

        # Post-write validation pass — normalize any non-canonical marker lines.
        rewritten = validate_and_normalize_queue(queue_path)
        if rewritten:
            print(
                f"[pm-materialize] Normalized {rewritten} non-canonical line(s) in {queue_path}",
                file=sys.stderr,
            )


def topological_order(tasks):
    """Order tasks so dependencies are respected."""
    ordered = []
    remaining = list(tasks)
    resolved = set()

    max_iterations = len(remaining) * 2
    iteration = 0
    while remaining and iteration < max_iterations:
        iteration += 1
        for task in list(remaining):
            deps = set(task.get("depends_on", []) or [])
            if deps.issubset(resolved):
                ordered.append(task)
                seq = task.get("sequence")
                if seq is not None:
                    resolved.add(seq)
                remaining.remove(task)

    if remaining:
        print(f"[pm-materialize] WARNING: {len(remaining)} tasks have unresolvable dependencies:", file=sys.stderr)
        for t in remaining:
            print(f"  - seq={t.get('sequence')} slug={t.get('slug', '?')} depends_on={t.get('depends_on', [])}", file=sys.stderr)
        ordered.extend(remaining)

    return ordered


def build_cm_task_ids(version, start_seq, date_str):
    """Build CM/HUMAN/TESTER task IDs for a given version and starting sequence.

    Returns a dict with keys: cm_open_id, tester_id, human_approve_id, cm_release_id.

    ID formats:
        CM-open:       CM-<date>-<seq>-open-rc-<version>
        TESTER:        TESTER-<date>-<seq+1>-verify-<version>
        HUMAN-APPROVE: HUMAN-APPROVE-<version>-<seq+2>   (HUMAN is not an agent)
        CM-release:    CM-<date>-<seq+3>-release-<version>
    """
    # Strip leading 'v' and replace dots with hyphens for kebab-case slugs.
    # e.g. v0.4.0 -> 0-4-0 so IDs match ^[A-Z]+-[0-9]{8}-[0-9]{3}-[a-z0-9-]+$
    ver_clean = version.lstrip("v").replace(".", "-")
    cm_open_id = f"CM-{date_str}-{start_seq:03d}-open-rc-{ver_clean}"
    tester_id = f"TESTER-{date_str}-{start_seq + 1:03d}-verify-{ver_clean}"
    human_approve_id = f"HUMAN-APPROVE-{version}-{start_seq + 2:03d}"
    cm_release_id = f"CM-{date_str}-{start_seq + 3:03d}-release-{ver_clean}"
    return {
        "cm_open_id": cm_open_id,
        "tester_id": tester_id,
        "human_approve_id": human_approve_id,
        "cm_release_id": cm_release_id,
    }


def inject_feature_workflow_tasks(tasks, source_branch, test_required, parent_branch,
                                   date_str, base_seq, owner, git_repo="none"):
    """Inject a CODER create-shared-branch ticket as ticket 1 for the shared-branch
    decomposition mode (workflow_type="feature").

    "feature" here is the NAME of the decomposition mode — it means a shared development
    branch is created first and all work tasks branch from it.  The word "feature" in this
    function name refers to that mode, not to "feature tasks" as a generic PM term for
    operator-submitted work tasks.

    Must be called AFTER assign_task_ids() so that work tasks already have their task_ids
    set.

    For the shared-branch decomposition mode there are no CM bookends and no release
    version.  The assembly is:
        test_required=True:  CODER(create-shared-branch) -> work tasks -> TESTER
        test_required=False: CODER(create-shared-branch) -> work tasks

    Mutates `tasks` in-place:
    - Prepends a CODER create-shared-branch task (no prereqs).
    - Adds the create-shared-branch task ID as a dependency for all work tasks.
    - Appends a TESTER task (prereqs = all work task IDs) when test_required=True.

    Returns the list of synthetic tasks added.
    """
    # Build the create-shared-branch task ID — new format: ROLE-DATE-NNN-slug (no owner prefix)
    create_branch_id = f"CODER-{date_str}-{base_seq:03d}-create-shared-branch"

    feature_task_ids = [t["task_id"] for t in tasks]

    # Strip leading 'v' is not relevant here, but keep source_branch as-is
    create_branch_task = {
        "task_id": create_branch_id,
        "owner": owner,
        "title": f"CODER: Create Shared Feature Branch — {source_branch}",
        "role": "CODER",
        "assigned_agent": "CODER",
        "working_directory": "none",
        "git_repo": git_repo if git_repo else "none",
        "source_branch": parent_branch if parent_branch else "main",
        "feature_branch": "none",
        "goal": (
            f"Create the shared feature branch '{source_branch}' from '{parent_branch}' "
            f"and push it to origin. All feature tasks in this workflow will branch from "
            f"and merge back into '{source_branch}'."
        ),
        "inputs": [],
        "context_paths": [],
        "required_output": (
            f"Branch '{source_branch}' exists on origin and is up to date with "
            f"'{parent_branch}'."
        ),
        "constraints": [
            f"Branch from '{parent_branch}', not from main.",
            f"Push '{source_branch}' to origin after creating it.",
            "Do not create any other branches. Do not modify any files.",
        ],
        "acceptance_criteria": [
            f"git ls-remote origin {source_branch} returns a commit SHA.",
            f"Branch '{source_branch}' is at the same commit as '{parent_branch}'.",
        ],
        "depends_on": [],
        "prerequisite_ids": [],
        "notes": (
            f"Automated feature-workflow setup task. "
            f"workflow_type=feature source_branch={source_branch} parent_branch={parent_branch}"
        ),
        "_synthetic": True,
        "_create_shared_branch": True,
    }

    # Build TESTER synthetic task for the shared-branch decomposition mode (if test_required)
    synthetic_added = [create_branch_task]
    tester_task = None
    if test_required:
        # New format: TESTER-DATE-NNN-slug (no owner prefix)
        tester_id = f"TESTER-{date_str}-{base_seq + 1:03d}-verify-feature"
        tester_task = {
            "task_id": tester_id,
            "owner": owner,
            "title": "Verify Feature Tasks",
            "role": "TESTER",
            "assigned_agent": "TESTER",
            "working_directory": "none",
            "git_repo": git_repo if git_repo else "none",
            "source_branch": "none",
            "feature_branch": "none",
            "goal": (
                "Verify the feature tasks for this workflow against their requirements document. "
                "Produce a verification report."
            ),
            "inputs": list(feature_task_ids),
            "context_paths": [],
            "required_output": _TESTER_PATH_C_REQUIRED_OUTPUT,
            "constraints": [
                "tester_operation: verify-feature",
            ],
            "acceptance_criteria": list(_TESTER_PATH_C_ACCEPTANCE_CRITERIA),
            "depends_on": [],
            "prerequisite_ids": list(feature_task_ids),
            "notes": (
                "Automated TESTER task for feature workflow. "
                + _TESTER_PATH_C_NOTES_SUFFIX
            ),
            "_synthetic": True,
            "_tester": True,
        }
        synthetic_added.append(tester_task)

    # Add create-shared-branch task ID as dependency for all existing work tasks
    for task in tasks:
        prereq_ids = task.setdefault("prerequisite_ids", [])
        if create_branch_id not in prereq_ids:
            prereq_ids.insert(0, create_branch_id)

    # Prepend create-shared-branch, append TESTER if test_required
    tasks.insert(0, create_branch_task)
    if tester_task is not None:
        tasks.append(tester_task)

    return synthetic_added


def inject_cm_bookends(tasks, target_version, date_str, base_seq, owner, git_repo="none",
                       human_approval_required="auto", test_required=True):
    """Inject CM-open, optional TESTER, optional HUMAN-APPROVE, and CM-release tasks.

    Must be called AFTER assign_task_ids() so that work tasks already have
    their task_ids set. Pass date_str and base_seq from the assign_task_ids
    return value to avoid sequence number conflicts.

    base_seq should be start_seq + len(work_tasks) so CM seq numbers do not
    collide with the work task seq numbers assigned by assign_task_ids().

    human_approval_required controls whether a HUMAN-APPROVE gate is injected:
        'required' — inject HUMAN-APPROVE between TESTER and CM-release.
        'auto'     — skip HUMAN-APPROVE; CM-release depends only on TESTER.
        missing    — treated as 'auto'.
        any other  — WARNING logged to stderr, treated as 'auto'.

    test_required controls whether a TESTER task is injected:
        True  (default) — inject TESTER between work tasks and CM-release.
        False           — skip TESTER; CM-release depends only on work tasks.

    Mutates `tasks` in-place:
    - Prepends a CM-open synthetic task (no prereqs).
    - Appends a TESTER synthetic task when test_required=True
      (prereqs = all work task IDs).
    - Appends a HUMAN-APPROVE synthetic task when human_approval_required='required'
      (prereqs = all work task IDs + TESTER ID if test_required).
    - Appends a CM-release synthetic task
      (prereqs = all work IDs [+ TESTER ID when test_required]
       [+ HUMAN-APPROVE ID when required]).
    - Adds CM-open task ID as a dependency for all original work tasks.

    Returns the list of synthetic tasks added (for informational use), or an
    empty list if target_version is falsy or 'none'.

    Dependency graph (required, test_required=True):
        CM-open (no prereqs)
          -> work tasks (depend on CM-open)
          -> TESTER (depends on all work tasks)
          -> HUMAN-APPROVE (depends on TESTER + all work tasks)
          -> CM-release (depends on TESTER + HUMAN-APPROVE)

    Dependency graph (auto, test_required=True):
        CM-open (no prereqs)
          -> work tasks (depend on CM-open)
          -> TESTER (depends on all work tasks)
          -> CM-release (depends on TESTER + all work tasks)

    Dependency graph (auto, test_required=False):
        CM-open (no prereqs)
          -> work tasks (depend on CM-open)
          -> CM-release (depends on all work tasks)
    """
    # The caller (main()) is responsible for converting target_version="none"
    # to a fallback label (e.g. "unversioned") before calling this function for
    # release workflows. If this guard fires it means the invariant enforcement in
    # main() has a bug — emit an error rather than silently returning empty.
    if not target_version or target_version.lower() == "none":
        print(
            "[pm-materialize] ERROR: inject_cm_bookends called with target_version='none'. "
            "The caller must supply a non-'none' version (or 'unversioned' as fallback). "
            "This is a materializer bug — bookend injection was not guarded correctly.",
            file=sys.stderr,
        )
        return []

    # Normalise and validate human_approval_required
    _valid_approval_values = {"required", "auto"}
    har = (human_approval_required or "auto").strip().lower()
    if har not in _valid_approval_values:
        print(
            f"[pm-materialize] WARNING: invalid human_approval_required value "
            f"'{human_approval_required}'; defaulting to 'auto'",
            file=sys.stderr,
        )
        har = "auto"
    inject_human_approve = har == "required"
    inject_tester = bool(test_required)

    ids = build_cm_task_ids(target_version, base_seq, date_str)
    cm_open_id = ids["cm_open_id"]
    tester_id = ids["tester_id"]
    human_approve_id = ids["human_approve_id"]
    cm_release_id = ids["cm_release_id"]

    # Collect all work task IDs (the operator-submitted tasks already in the list).
    # These are available because inject_cm_bookends is called AFTER assign_task_ids().
    feature_task_ids = [t["task_id"] for t in tasks]

    # Strip leading 'v' for use in the TESTER task goal/notes
    ver_clean = target_version.lstrip("v")

    # Slug values for synthetic tasks — must be distinct so role+slug dedup
    # (Approach B) does not collapse CM-open and CM-release into one task.
    cm_open_slug = f"open-rc-{ver_clean}"
    tester_slug = f"verify-{ver_clean}"
    cm_release_slug = f"release-{ver_clean}"

    # Build CM-open synthetic task dict
    cm_open = {
        "task_id": cm_open_id,
        "slug": cm_open_slug,
        "owner": owner,
        "title": f"CM Open RC — {target_version}",
        "role": "CM",
        "assigned_agent": "CM",
        "working_directory": "none",
        "git_repo": git_repo if git_repo else "none",
        "source_branch": "none",
        "feature_branch": "none",
        "goal": f"Open the release candidate branch for {target_version}.",
        "inputs": [],
        "context_paths": [],
        "required_output": f"RC branch open for {target_version}.",
        "constraints": ["cm_operation: open-rc"],
        "acceptance_criteria": [f"RC branch for {target_version} is open and ready for feature merges."],
        "depends_on": [],
        "prerequisite_ids": [],
        "notes": f"Automated CM task. cm_operation=open-rc target_version={target_version}",
        "cm_operation": "open-rc",
        "target_version": target_version,
        "_synthetic": True,
    }

    # Build TESTER synthetic task dict (sits between features and HUMAN-APPROVE)
    # Only built when inject_tester=True (test_required=True).
    if inject_tester:
        tester_task = {
            "task_id": tester_id,
            "slug": tester_slug,
            "owner": owner,
            "title": f"Verify RC — {target_version}",
            "role": "TESTER",
            "assigned_agent": "TESTER",
            "working_directory": "none",
            "git_repo": git_repo if git_repo else "none",
            "source_branch": "none",
            "feature_branch": "none",
            "goal": f"Verify the release candidate {target_version} against its requirements document. "
                    f"Produce a verification report and either pass the RC or flag gaps for human review.",
            "inputs": list(feature_task_ids),
            "context_paths": [],
            "required_output": (
                f"artifacts/report.md with verification findings for {target_version}. "
                "Task DONE on clean pass. If gaps or bugs are found, file them via "
                "Path C (BUG or PRIORITY under projects/<project>/bugs/ or priority/) and "
                "recommend SHIP-WITH-CONCERNS or SHIP-WITH-SERIOUS-CONCERNS in "
                "artifacts/report.md. BLOCKED is for infrastructure failure only "
                "(e.g. test runner unavailable, dev tree unreachable)."
            ),
            "constraints": [
                f"tester_operation: verify-rc",
                f"target_version: {target_version}",
            ],
            "acceptance_criteria": list(_TESTER_PATH_C_ACCEPTANCE_CRITERIA),
            "depends_on": [],
            "prerequisite_ids": list(feature_task_ids),
            "notes": (
                f"Automated TESTER task. Verifies RC {target_version} against its requirements doc. "
                + _TESTER_PATH_C_NOTES_SUFFIX
            ),
            "target_version": target_version,
            "_synthetic": True,
            "_tester": True,
        }
    else:
        tester_task = None

    # Build HUMAN-APPROVE task dict
    # Human gate prereqs: all work task IDs + TESTER (if injected)
    human_approve = {
        "task_id": human_approve_id,
        "owner": "HUMAN",
        "title": f"Human Approval Gate — {target_version}",
        "role": "HUMAN",
        "assigned_agent": "HUMAN",
        "working_directory": "none",
        "git_repo": "none",
        "source_branch": "none",
        "feature_branch": "none",
        "goal": f"Review all feature tasks and TESTER verification for release {target_version} and approve or reject proceeding to the CM-release step.",
        "inputs": feature_task_ids,
        "context_paths": [],
        "required_output": "Human sets status.md State to DONE to approve.",
        "constraints": ["Only a human should update this task."],
        "acceptance_criteria": [
            "Human has reviewed all feature tasks and the TESTER verification report.",
            "Human has set status.md State to DONE to approve release.",
        ],
        "depends_on": [],
        "prerequisite_ids": list(feature_task_ids) + ([tester_id] if inject_tester else []),
        "notes": f"Human gate for release {target_version}. Set State=DONE to approve, BLOCKED to reject.",
        "target_version": target_version,
        "_synthetic": True,
        "_human_approve": True,
        "_cm_release_id": cm_release_id,
    }

    # Build CM-release task dict.
    # CM-release prereqs depend on whether TESTER and HUMAN-APPROVE are injected:
    #   test_required=True, required:  all work tasks + TESTER + HUMAN-APPROVE
    #   test_required=True, auto:      all work tasks + TESTER
    #   test_required=False, auto:     all work tasks
    cm_release_prereqs = list(feature_task_ids)
    if inject_tester:
        cm_release_prereqs.append(tester_id)
    if inject_human_approve:
        cm_release_prereqs.append(human_approve_id)

    cm_release = {
        "task_id": cm_release_id,
        "slug": cm_release_slug,
        "owner": owner,
        "title": f"CM Release — {target_version}",
        "role": "CM",
        "assigned_agent": "CM",
        "working_directory": "none",
        "git_repo": git_repo if git_repo else "none",
        "source_branch": "none",
        "feature_branch": "none",
        "goal": f"Finalize and release {target_version}.",
        "inputs": [],
        "context_paths": [],
        "required_output": f"Release {target_version} published.",
        "constraints": ["cm_operation: release"],
        "acceptance_criteria": [f"Release {target_version} has been tagged and published."],
        "depends_on": [],
        "prerequisite_ids": cm_release_prereqs,
        "notes": f"Automated CM task. cm_operation=release target_version={target_version}",
        "cm_operation": "release",
        "target_version": target_version,
        "_synthetic": True,
    }

    # Add CM-open as a dependency for all existing work tasks
    for task in tasks:
        prereq_ids = task.setdefault("prerequisite_ids", [])
        if cm_open_id not in prereq_ids:
            prereq_ids.insert(0, cm_open_id)

    # Prepend CM-open, conditionally append TESTER, conditionally append HUMAN-APPROVE,
    # append CM-release
    tasks.insert(0, cm_open)
    synthetic_added = [cm_open]
    if inject_tester and tester_task is not None:
        tasks.append(tester_task)
        synthetic_added.append(tester_task)
    if inject_human_approve:
        tasks.append(human_approve)
        synthetic_added.append(human_approve)
    tasks.append(cm_release)
    synthetic_added.append(cm_release)

    return synthetic_added


def inject_document_workflow_tasks(tasks, sections, date_str, base_seq, owner,
                                    git_repo="none", target_version="none"):
    """Inject the document pipeline tasks around any PM-supplied tasks.

    Section strategy (operator-defined sections fallback):
    -------------------------------------------------------
    The plan JSON provides a 'sections' list at the top level. Each entry
    becomes one WRITER section-draft ticket, emitted at materialize time.
    Runtime-foreach (where section names are derived from the outline
    deliverable at run time) is deferred to a future iteration; this approach
    was chosen because it is self-contained and requires no wake-script changes.

    Pipeline order emitted:
        CM-open-doc         — cm_operation=open_doc, no prereqs
        WRITER-outline      — depends on CM-open-doc
        WRITER-section-draft-<N> (one per section) — each depends on WRITER-outline
        WRITER-integrate    — depends on all section-draft tickets
        WRITER-polish       — depends on WRITER-integrate
        TESTER-review       — depends on WRITER-polish
        CM-finalize         — cm_operation=finalize, depends on TESTER-review

    Any tasks supplied in `tasks` (PM-authored tasks) are appended after the
    outline step and before integrate, as additional WRITER steps; they are not
    normally present for the document workflow but are accepted for forward
    compatibility.

    Mutates `tasks` in-place (replaces contents with the full assembled pipeline).
    Returns the list of all synthetic tasks added.

    Parameters
    ----------
    tasks:
        The original PM-supplied tasks list. For a pure document workflow this
        is typically empty; the pipeline is fully synthetic.
    sections:
        List of section name strings from the plan-level 'sections' field.
        When empty, a single placeholder section 'section-1' is used.
    date_str:
        Date string in YYYYMMDD format from assign_task_ids().
    base_seq:
        Starting sequence number for synthetic task IDs (must not collide with
        work task IDs already assigned by assign_task_ids()).
    owner:
        Task owner prefix string, e.g. 'CLAUDE'.
    git_repo:
        Git repo URL or 'none'. Passed through but not used for branching —
        document workflows are file-based (no git branch lifecycle). Synthetic
        tasks in this function always set git_repo='none' so that no git-branch
        fields are emitted in their READMEs.
    target_version:
        Version label (e.g. 'v0.22.0') or 'none'. Included in task notes.
    """
    # Normalize sections: use a placeholder when empty.
    if not sections:
        sections = ["section-1"]

    # Document workflow is file-based (no git branch lifecycle). Override any
    # caller-supplied git_repo to "none" for all synthetic tasks so that no
    # git-branch fields are emitted and no branching is attempted.
    _ = git_repo  # consumed above in caller for context; unused in synthetics
    _task_git_repo = "none"

    seq = base_seq

    # --- CM open-doc --- (new format: ROLE-DATE-NNN-slug, no owner prefix)
    cm_open_doc_id = f"CM-{date_str}-{seq:03d}-open-doc"
    seq += 1
    cm_open_doc = {
        "task_id": cm_open_doc_id,
        "slug": "open-doc",
        "owner": owner,
        "title": "CM Open Doc",
        "role": "CM",
        "assigned_agent": "CM",
        "working_directory": "none",
        "git_repo": _task_git_repo,
        "source_branch": "none",
        "feature_branch": "none",
        "goal": "Open the document workspace and prepare it for WRITER tasks.",
        "inputs": [],
        "context_paths": [],
        "required_output": "Document workspace open and ready for WRITER tasks.",
        "constraints": ["cm_operation: open_doc"],
        "acceptance_criteria": ["Document workspace is initialized."],
        "depends_on": [],
        "prerequisite_ids": [],
        "notes": f"Automated CM task. cm_operation=open_doc target_version={target_version}",
        "cm_operation": "open_doc",
        "target_version": target_version,
        "_synthetic": True,
        "_doc_open": True,
    }

    # --- WRITER outline --- (new format: no owner prefix)
    outline_id = f"WRITER-{date_str}-{seq:03d}-outline"
    seq += 1
    outline_task = {
        "task_id": outline_id,
        "slug": "outline",
        "owner": owner,
        "title": "WRITER: Outline",
        "role": "WRITER",
        "assigned_agent": "WRITER",
        "working_directory": "none",
        "git_repo": _task_git_repo,
        "source_branch": "none",
        "feature_branch": "none",
        "goal": "Produce an outline (outline.md) for the document.",
        "inputs": [],
        "context_paths": [],
        "required_output": "outline.md",
        "constraints": [],
        "acceptance_criteria": ["outline.md exists with document structure."],
        "depends_on": [],
        "prerequisite_ids": [cm_open_doc_id],
        "notes": f"Automated WRITER outline task. target_version={target_version}",
        "target_version": target_version,
        "_synthetic": True,
        "_doc_outline": True,
    }

    # --- WRITER section-draft tasks (one per section) ---
    # Section strategy: operator-defined sections (plan-level 'sections' list).
    # Each section name from the plan becomes one WRITER ticket. This is the
    # operator-defined-sections fallback; runtime-foreach (where sections are
    # derived from outline.md at materialize time) is deferred to a later
    # iteration because it requires wake-script changes outside this ticket's
    # scope.
    section_draft_ids = []
    section_draft_tasks = []
    for section_name in sections:
        slug_section = re.sub(r'[^a-z0-9]+', '-', section_name.lower()).strip('-')
        slug_section = slug_section[:25]  # keep slug short
        # New format: WRITER-DATE-NNN-slug (no owner prefix)
        section_id = f"WRITER-{date_str}-{seq:03d}-section-draft-{slug_section}"
        seq += 1
        section_task = {
            "task_id": section_id,
            "slug": f"section-draft-{slug_section}",
            "owner": owner,
            "title": f"WRITER: Section Draft — {section_name}",
            "role": "WRITER",
            "assigned_agent": "WRITER",
            "working_directory": "none",
            "git_repo": _task_git_repo,
            "source_branch": "none",
            "feature_branch": "none",
            "goal": f"Write a draft for the '{section_name}' section of the document.",
            "inputs": ["outline.md"],
            "context_paths": [],
            "required_output": f"section-{slug_section}.md",
            "constraints": [],
            "acceptance_criteria": [f"section-{slug_section}.md exists with written draft."],
            "depends_on": [],
            "prerequisite_ids": [outline_id],
            "notes": (
                f"Automated WRITER section-draft task for section '{section_name}'. "
                f"target_version={target_version}"
            ),
            "target_version": target_version,
            "_synthetic": True,
            "_doc_section_draft": True,
        }
        section_draft_ids.append(section_id)
        section_draft_tasks.append(section_task)

    # --- WRITER integrate --- (new format: no owner prefix)
    integrate_id = f"WRITER-{date_str}-{seq:03d}-integrate"
    seq += 1
    # Inputs: all section deliverables
    section_deliverables = [f"section-{re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')[:25]}.md"
                            for s in sections]
    integrate_task = {
        "task_id": integrate_id,
        "slug": "integrate",
        "owner": owner,
        "title": "WRITER: Integrate Sections",
        "role": "WRITER",
        "assigned_agent": "WRITER",
        "working_directory": "none",
        "git_repo": _task_git_repo,
        "source_branch": "none",
        "feature_branch": "none",
        "goal": "Integrate all section drafts and the outline into a single coherent document (integrated.md).",
        "inputs": ["outline.md"] + section_deliverables,
        "context_paths": [],
        "required_output": "integrated.md",
        "constraints": [],
        "acceptance_criteria": ["integrated.md exists combining all sections."],
        "depends_on": [],
        "prerequisite_ids": list(section_draft_ids),
        "notes": f"Automated WRITER integrate task. target_version={target_version}",
        "target_version": target_version,
        "_synthetic": True,
        "_doc_integrate": True,
    }

    # --- WRITER polish --- (new format: no owner prefix)
    polish_id = f"WRITER-{date_str}-{seq:03d}-polish"
    seq += 1
    polish_task = {
        "task_id": polish_id,
        "slug": "polish",
        "owner": owner,
        "title": "WRITER: Polish",
        "role": "WRITER",
        "assigned_agent": "WRITER",
        "working_directory": "none",
        "git_repo": _task_git_repo,
        "source_branch": "none",
        "feature_branch": "none",
        "goal": "Polish the integrated document into final form (polished.md).",
        "inputs": ["integrated.md"],
        "context_paths": [],
        "required_output": "polished.md",
        "constraints": [],
        "acceptance_criteria": ["polished.md exists with final polished content."],
        "depends_on": [],
        "prerequisite_ids": [integrate_id],
        "notes": f"Automated WRITER polish task. target_version={target_version}",
        "target_version": target_version,
        "_synthetic": True,
        "_doc_polish": True,
    }

    # --- TESTER review --- (new format: no owner prefix)
    review_id = f"TESTER-{date_str}-{seq:03d}-review"
    seq += 1
    review_task = {
        "task_id": review_id,
        "slug": "review",
        "owner": owner,
        "title": "TESTER: Review",
        "role": "TESTER",
        "assigned_agent": "TESTER",
        "working_directory": "none",
        "git_repo": _task_git_repo,
        "source_branch": "none",
        "feature_branch": "none",
        "goal": "Review polished.md against the original brief and produce a review report.",
        "inputs": ["polished.md"],
        "context_paths": [],
        "required_output": "review-report.md",
        "constraints": ["autonomous_criterion: required"],
        "acceptance_criteria": list(_TESTER_DOC_REVIEW_PATH_C_ACCEPTANCE_CRITERIA),
        "depends_on": [],
        "prerequisite_ids": [polish_id],
        "notes": (
            f"Automated TESTER review task for document workflow. "
            f"target_version={target_version}"
        ),
        "target_version": target_version,
        "_synthetic": True,
        "_doc_review": True,
        "_tester": True,
    }

    # --- CM finalize --- (new format: no owner prefix)
    cm_finalize_id = f"CM-{date_str}-{seq:03d}-finalize"
    cm_finalize = {
        "task_id": cm_finalize_id,
        "slug": "finalize",
        "owner": owner,
        "title": "CM Finalize",
        "role": "CM",
        "assigned_agent": "CM",
        "working_directory": "none",
        "git_repo": _task_git_repo,
        "source_branch": "none",
        "feature_branch": "none",
        "goal": "Finalize the document and close the document workspace.",
        "inputs": ["polished.md", "review-report.md"],
        "context_paths": [],
        "required_output": "Document finalized and workspace closed.",
        "constraints": ["cm_operation: finalize"],
        "acceptance_criteria": ["Document workspace is finalized and closed."],
        "depends_on": [],
        "prerequisite_ids": [review_id],
        "notes": f"Automated CM task. cm_operation=finalize target_version={target_version}",
        "cm_operation": "finalize",
        "target_version": target_version,
        "_synthetic": True,
        "_doc_finalize": True,
    }

    # Add CM-open-doc as a dependency for all PM-supplied tasks (if any), and
    # add them before the integrate step.
    for task in tasks:
        prereq_ids = task.setdefault("prerequisite_ids", [])
        if cm_open_doc_id not in prereq_ids:
            prereq_ids.insert(0, cm_open_doc_id)
        # Also depend on outline so PM tasks don't run before the structure is set.
        if outline_id not in prereq_ids:
            prereq_ids.append(outline_id)
        # Their deliverables feed into integrate.
        if task["task_id"] not in integrate_task["prerequisite_ids"]:
            integrate_task["prerequisite_ids"].append(task["task_id"])

    # Assemble all synthetic tasks in pipeline order.
    all_synthetic = (
        [cm_open_doc, outline_task]
        + section_draft_tasks
        + list(tasks)           # PM-supplied tasks (typically empty)
        + [integrate_task, polish_task, review_task, cm_finalize]
    )

    # Replace tasks list content with the fully assembled pipeline so the
    # caller's ordered_tasks assembly has the right sequence.
    tasks.clear()
    tasks.extend(all_synthetic)

    return all_synthetic


def _read_cfg_field(cfg_path, field_name):
    """Read a single key = value field from a project.cfg (or PROJECT.cfg) file.

    Reads the file line-by-line and returns the first value found for
    ``field_name``.  Handles both INI format (``key = value``) and
    bare-assignment format (``key=value``).  Strips surrounding whitespace
    and optional quotes.  Returns an empty string when the field is absent
    or the file does not exist.

    Parameters
    ----------
    cfg_path:
        Path-like or string pointing to the project.cfg file.  May be None
        or point to a non-existent file — both return empty string silently.
    field_name:
        The bare key name to look for (e.g. ``'review_agent'``).

    Returns
    -------
    str
        The stripped value, or ``''`` when absent/unreadable.
    """
    if not cfg_path:
        return ""
    cfg_path = Path(cfg_path)
    if not cfg_path.is_file():
        return ""
    try:
        for line in cfg_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if "=" not in stripped or stripped.startswith("#"):
                continue
            key, _, val = stripped.partition("=")
            if key.strip() == field_name:
                return val.strip().strip("'\"")
    except OSError:
        pass
    return ""


def load_workflow_capabilities(workflow_type: str, kanban_root=None) -> dict:
    """Read the [capabilities] section of a workflow plugin's workflow.cfg manifest.

    Search order for the ``workflow.cfg`` file (first match wins — mirrors the
    search order used by ``workflow_loader._find_workflow_file`` for pipeline.yaml):

    1. ``$KANBAN_ROOT/workflows/<type>/workflow.cfg``
       Project-local flat override: the live install layout copies the plugin
       directories to ``$KANBAN_ROOT/workflows/`` (no ``team/`` prefix).
    2. ``$KANBAN_ROOT/team/workflows/<type>/workflow.cfg``
       Canonical plugin-directory path for development checkouts and CI.

    Returns a dict with the following keys (all lowercase strings):

        git_mode          — "rw", "ro", or "none" (empty string when absent)
        finalize          — "tag", "publish", "report", or "" (empty when absent)
        agents            — comma-separated agent list string, e.g. "pm,tester"
        version_semantics — "semver", "label", or "" (empty when absent)

    When the plugin directory or manifest file is not found in either location,
    returns an empty dict so callers can distinguish "plugin present, field
    absent" from "plugin not found."  Callers that need to fail-closed on a
    missing manifest should check ``bool(result)`` and handle the empty-dict
    case explicitly.

    Parameters
    ----------
    workflow_type:
        The workflow type string, e.g. ``'release'``, ``'testing-only'``.
    kanban_root:
        Override for the kanban root directory.  Defaults to
        ``PGAI_AGENT_KANBAN_ROOT_PATH`` env var or ``~/pgai_agent_kanban``.

    Returns
    -------
    dict
        Capabilities dict (keys: git_mode, finalize, agents, version_semantics).
        Empty dict when the manifest cannot be read.
    """
    import configparser as _configparser

    if kanban_root is None:
        kanban_root = (
            os.environ.get("PGAI_AGENT_KANBAN_ROOT_PATH")
            or str(Path.home() / "pgai_agent_kanban")
        )
    root = Path(kanban_root)
    # Search order mirrors workflow_loader._find_workflow_file:
    #   1. flat install layout:  $KANBAN_ROOT/workflows/<type>/workflow.cfg
    #   2. canonical dev layout: $KANBAN_ROOT/team/workflows/<type>/workflow.cfg
    _candidates = [
        root / "workflows" / workflow_type / "workflow.cfg",
        root / "team" / "workflows" / workflow_type / "workflow.cfg",
    ]
    cfg_path = next((p for p in _candidates if p.is_file()), None)
    if cfg_path is None:
        return {}

    parser = _configparser.ConfigParser()
    try:
        parser.read(str(cfg_path), encoding="utf-8")
    except _configparser.Error:
        return {}

    caps = {}
    _SECTION = "capabilities"
    if parser.has_section(_SECTION):
        for key in ("git_mode", "finalize", "agents", "version_semantics"):
            try:
                caps[key] = parser.get(_SECTION, key).strip()
            except _configparser.NoOptionError:
                caps[key] = ""
    else:
        # Section missing — return empty dict (manifest is malformed or pre-schema)
        return {}
    return caps


def _workflow_requires_cm_bookends(caps: dict) -> bool:
    """Return True when a workflow's capabilities call for CM bookend injection.

    The predicate: the plugin declares ``git_mode = rw`` AND ``finalize``
    is one of the release-lifecycle values (``tag`` or ``publish``).
    Both conditions must hold; a read-only or no-git plugin never needs
    CM-open-rc / CM-release tasks.

    When *caps* is empty (manifest not found), the function defaults to
    True — the conservative fallback ensures backward-compatibility with
    unknown workflow types that predate the plugin manifest contract.

    Parameters
    ----------
    caps:
        Capabilities dict from :func:`load_workflow_capabilities`.
        Empty dict means "manifest not found" — handled as conservative
        fallback (emit bookends, same as before the fix).

    Returns
    -------
    bool
        True  — the workflow requires CM bookend tasks (open-rc + release).
        False — the workflow does not require CM bookend tasks.
    """
    if not caps:
        # No manifest found — fall back to the old behavior (emit bookends).
        return True
    git_mode = caps.get("git_mode", "").strip().lower()
    finalize = caps.get("finalize", "").strip().lower()
    return git_mode == "rw" and finalize in {"tag", "publish"}


def _parse_manifest_roster(caps: dict) -> list:
    """Parse the agents roster from workflow capabilities into an uppercase list.

    The ``agents`` field in ``workflow.cfg [capabilities]`` is a
    comma-separated string of agent names (lowercase), e.g. ``pm,tester``.
    This function normalises each entry to uppercase and returns a list.

    When the ``agents`` field is absent or empty, returns an empty list.
    Callers that need to enforce a non-empty roster should handle the empty
    list explicitly.

    Parameters
    ----------
    caps:
        Capabilities dict from :func:`load_workflow_capabilities`.

    Returns
    -------
    list[str]
        Roster of agent names in uppercase, e.g. ``['PM', 'TESTER']``.
    """
    raw = caps.get("agents", "").strip()
    if not raw:
        return []
    return [a.strip().upper() for a in raw.split(",") if a.strip()]


def _check_plan_roster_against_manifest(tasks: list, manifest_roster: list,
                                         workflow_type: str) -> None:
    """Fail loudly when a plan task requests an agent outside the plugin roster.

    Iterates over the plan tasks and checks each task's role against the
    ``manifest_roster`` list.  When a role is not in the roster, prints an
    actionable error to stderr and calls ``sys.exit(1)`` with a message that
    names both the offending agent and the full roster.

    Synthetic tasks (``_synthetic=True``) are skipped — they are materializer-
    injected and not subject to PM's roster.

    When ``manifest_roster`` is empty (manifest not found or agents field
    absent), the check is skipped silently with a warning so that unknown
    plugin types do not false-positive.  This is the conservative open-plugin
    fallback: unknown plugins pass the guard.

    Parameters
    ----------
    tasks:
        The list of plan task dicts (before ID assignment and bookend injection).
    manifest_roster:
        The uppercase agent roster from the plugin manifest, e.g.
        ``['PM', 'TESTER']``.  Empty list disables the check.
    workflow_type:
        The workflow type string, used in the error message for context.
    """
    if not manifest_roster:
        print(
            f"[pm-materialize] ROSTER GUARD: manifest roster for workflow_type "
            f"'{workflow_type}' is empty or unreadable — skipping roster check.",
            file=sys.stderr,
        )
        return

    roster_display = ", ".join(manifest_roster)
    for task in tasks:
        # Skip materializer-injected synthetic tasks.
        if task.get("_synthetic"):
            continue
        role = (task.get("role") or "").strip().upper()
        if role and role not in manifest_roster:
            print(
                f"[pm-materialize] ERROR: ROSTER GUARD — plan requests role '{role}' "
                f"but workflow_type '{workflow_type}' plugin roster is: {roster_display}. "
                f"Remove the out-of-roster task (slug={task.get('slug', '?')!r}) or "
                f"switch to a workflow type whose roster includes '{role}'.",
                file=sys.stderr,
            )
            sys.exit(1)


def inject_simple_tester_task(tasks: list, date_str: str, base_seq: int,
                               owner: str, git_repo: str = "none",
                               target_version: str = "none",
                               finalize_mode: str = "report") -> list:
    """Inject a standalone TESTER task for workflows without CM bookends.

    Used by non-CM workflow types (e.g. testing-only) where the workflow
    plugin declares ``finalize=report``.  The injected TESTER task carries
    the finalize responsibility: its goal and notes make explicit that the
    TESTER must write the report artifact at the finalize location.

    Must be called AFTER ``assign_task_ids()`` so that work tasks already
    have their ``task_id`` set.

    Mutates *tasks* in-place: appends the TESTER task at the end.

    Parameters
    ----------
    tasks:
        The list of plan task dicts (with task_ids already assigned).
    date_str:
        Date string in YYYYMMDD format (from ``assign_task_ids`` return).
    base_seq:
        Starting sequence number for the TESTER task ID.
    owner:
        Task owner string, e.g. ``'CLAUDE'``.
    git_repo:
        Git repo URL or ``'none'``.  Passed through to the task dict.
    target_version:
        Version label or semver string from the plan.  Used in task text.
    finalize_mode:
        Declared ``finalize`` capability of the plugin.  Controls the
        goal/notes language.  Defaults to ``'report'``.

    Returns
    -------
    list
        The list containing only the injected TESTER task (for logging).
    """
    work_task_ids = [t["task_id"] for t in tasks]
    tester_id = f"TESTER-{date_str}-{base_seq:03d}-verify-and-report"

    version_label = target_version if target_version and target_version.lower() != "none" else ""
    version_ctx = f" for {version_label}" if version_label else ""

    tester_task = {
        "task_id": tester_id,
        "slug": "verify-and-report",
        "owner": owner,
        "title": f"Verify and Report{' — ' + version_label if version_label else ''}",
        "role": "TESTER",
        "assigned_agent": "TESTER",
        "working_directory": "none",
        "git_repo": git_repo,
        "source_branch": "none",
        "feature_branch": "none",
        "goal": (
            f"Verify the work tasks{version_ctx} and produce the finalize artifact "
            f"(finalize={finalize_mode}). "
            "Write the test report to the finalize location declared by the workflow plugin. "
            "No git release lifecycle applies — this workflow finalizes by report, not by tag."
        ),
        "inputs": list(work_task_ids),
        "context_paths": [],
        "required_output": _TESTER_PATH_C_REQUIRED_OUTPUT,
        "constraints": [
            f"tester_operation: verify-and-report",
            f"finalize_mode: {finalize_mode}",
        ],
        "acceptance_criteria": list(_TESTER_PATH_C_ACCEPTANCE_CRITERIA),
        "depends_on": [],
        "prerequisite_ids": list(work_task_ids),
        "notes": (
            f"Standalone TESTER task for a non-CM workflow (finalize={finalize_mode}). "
            "Write the report artifact to the finalize location (projects/<project>/artifacts/). "
            "No CM release task follows — this task IS the final step in the pipeline. "
            + _TESTER_PATH_C_NOTES_SUFFIX
        ),
        "_synthetic": True,
        "_tester": True,
        "_finalize_report": True,
    }

    tasks.append(tester_task)
    return [tester_task]


def load_project_git_repo_url(team_root):
    """Return the git_repo_url from project.cfg [project] section, or '' if absent.

    Reads ``<team_root>/project.cfg`` using ``configparser``.  Returns the
    stripped ``git_repo_url`` value when found, or an empty string when:

    - the file does not exist
    - the ``[project]`` section is missing
    - the ``git_repo_url`` key is missing or empty

    No exception is raised for any of the above conditions.  Callers should
    treat an empty return value as "no override" and leave PM-emitted values
    unchanged.

    Parameters
    ----------
    team_root:
        Absolute path to the project root directory (the directory that
        contains ``project.cfg`` alongside ``requirements/``, ``tasks/``, etc.).

    Returns
    -------
    str
        The ``git_repo_url`` value, stripped; empty string when absent.
    """
    import configparser as _configparser
    cfg_path = Path(team_root) / "project.cfg"
    if not cfg_path.is_file():
        return ""
    parser = _configparser.ConfigParser()
    try:
        parser.read(str(cfg_path), encoding="utf-8")
    except _configparser.Error:
        return ""
    try:
        value = parser.get("project", "git_repo_url").strip()
    except (_configparser.NoSectionError, _configparser.NoOptionError):
        return ""
    return value


def apply_git_repo_override(tasks, canonical_url, dry_run=False):
    """Override git_repo for every PM-emitted task in *tasks* with *canonical_url*.

    PM occasionally hallucinates or omits the git_repo_url when building the
    plan JSON.  This function replaces every task's ``git_repo`` field with the
    value read from ``project.cfg [project] git_repo_url`` so that rendered
    README ## Git Repo lines are always correct, regardless of what PM emitted.

    Rules:
    - Only fires when ``canonical_url`` is non-empty.
    - Processes only tasks that are NOT already marked as synthetic bookends
      or injected TESTER/HUMAN tasks (``_synthetic``, ``_tester``,
      ``_human_approve``, ``_create_shared_branch``).  In practice this
      function is called BEFORE bookend injection so all tasks in *tasks* at
      that point are PM-emitted work tasks.
    - Emits one stderr warning per overridden task when the original value
      differs from ``canonical_url``.
    - Does NOT override tasks whose ``git_repo`` already matches the
      canonical URL (no spurious warnings on clean plans).

    Parameters
    ----------
    tasks:
        The list of task dicts to process in-place.
    canonical_url:
        The authoritative git repo URL from ``project.cfg``.  Callers must
        pass a non-empty string; empty strings are a no-op by convention.
    dry_run:
        When True, emit the same warnings but do not modify task dicts.

    Returns
    -------
    int
        Number of tasks whose ``git_repo`` field was overridden (or would be
        overridden in dry-run mode).
    """
    if not canonical_url:
        return 0

    overridden = 0
    _SYNTHETIC_MARKERS = ("_synthetic", "_tester", "_human_approve",
                          "_create_shared_branch", "_doc_open", "_doc_finalize",
                          "_prose_open_doc", "_prose_finalize", "_doc_review")
    for task in tasks:
        # Skip tasks that have synthetic-injection markers
        if any(task.get(m) for m in _SYNTHETIC_MARKERS):
            continue

        original = normalize_workspace_value(task.get("git_repo"))
        if original == canonical_url:
            continue  # already correct, no warning

        task_id = task.get("task_id") or task.get("slug") or "(unknown)"
        print(
            f"[pm-materialize] GIT-REPO-URL GUARD: task {task_id} "
            f"git_repo overridden: '{original}' -> '{canonical_url}' "
            f"(canonical value from project.cfg [project] git_repo_url)",
            file=sys.stderr,
        )
        if not dry_run:
            task["git_repo"] = canonical_url
            # Re-generate feature_branch when git_repo was previously 'none'
            # (PM declined to set a repo) but canonical is now real.
            if original.lower() == "none" and canonical_url.lower() != "none":
                tid = task.get("task_id")
                if tid:
                    task["feature_branch"] = f"feature/{tid}"
        overridden += 1

    return overridden


def evaluate_when_predicate(predicate, foreach_produced_items, workflow_agents,
                            project_cfg_path=None):
    """Evaluate a when: predicate against runtime pipeline state.

    Parameters
    ----------
    predicate:
        The predicate name string from the step's ``when:`` field, e.g.
        ``'foreach_was_used'`` or ``'review_agent_configured'``.  When
        ``None`` or empty the function returns True (no gate — always run).
    foreach_produced_items:
        Bool — True when at least one foreach step earlier in the same
        pipeline generated one or more items (section-draft tickets).
        Used to evaluate ``foreach_was_used``.
    workflow_agents:
        Dict mapping agent purpose labels to role strings from the
        workflow YAML ``agents:`` block (e.g. ``{'primary': 'WRITER',
        'manage': 'CM'}``).  No longer consulted for
        ``review_agent_configured``; kept for forward-compat callers.
    project_cfg_path:
        Optional path to the project's ``project.cfg`` file.  When
        provided, the ``review_agent_configured`` predicate reads the
        ``review_agent`` field from this file.  When absent (``None``),
        the predicate returns False (opt-in is off by default).

    Returns
    -------
    bool
        True  — the step should be materialized.
        False — the step should be skipped.

    Raises
    ------
    ValueError
        If *predicate* is a non-empty string that is not a recognised
        predicate name.  Callers should guard against this with
        ``VALID_WHEN_PREDICATES`` from workflow_loader before calling this
        function (the workflow loader validates at load time, so this should
        never fire in normal operation).
    """
    if not predicate:
        return True  # no when: gate — always run

    if predicate == "foreach_was_used":
        return bool(foreach_produced_items)

    if predicate == "review_agent_configured":
        # True when the project's project.cfg declares a non-empty 'review_agent'
        # field.  The workflow YAML agents block is not the authority for this
        # predicate — project.cfg is the single source of truth.
        return bool(_read_cfg_field(project_cfg_path, "review_agent").strip())

    raise ValueError(
        f"Unknown when predicate '{predicate}'. "
        f"Valid predicates: foreach_was_used, review_agent_configured"
    )


# ---------------------------------------------------------------------------
# D2 helpers: accumulated-input computation for document pipeline steps
# ---------------------------------------------------------------------------

# D2 chain: for each step, the list of prior deliverable step names whose
# outputs must be included in that step's ## Inputs so WRITER always has the
# full context (requirement + source + all work so far).
#
# Reading order (requirement + source injected by create_task_folder prepend):
#   outline      ← (no prior deliverables; requirement + source are injected by
#                   the create_task_folder prepend and source-paths mechanism)
#   draft        ← outline.md
#   section-draft ← outline.md
#   integrate    ← outline.md + all section-draft deliverables
#   polish       ← outline.md + draft.md  (short-form)
#                  outline.md + integrated.md  (long-form)
#
# The mapping below lists which step names' deliverables precede each step.
# Steps not in the map get no accumulated-deliverable injection (e.g. CM steps).
_D2_PRIOR_STEPS: dict = {
    "draft": ["outline"],
    "section-draft": ["outline"],
    "integrate": ["outline", "section-draft"],  # all section-draft files
    "polish": ["outline", "draft", "integrate"],  # draft XOR integrate depending on path
}


def _d2_accumulated_inputs(step_name: str, step_deliverables: dict) -> list:
    """Return the list of accumulated prior deliverable filenames for a step.

    Implements the D2 chain: each WRITER step receives the output files of all
    prior content steps so it has the full context of what has been produced.

    Parameters
    ----------
    step_name:
        Name of the current step (e.g. 'draft', 'polish', 'integrate').
    step_deliverables:
        Dict mapping step_name -> list[filename] collected as steps are
        processed.  Only steps processed BEFORE the current step will be
        present (the caller updates this dict AFTER calling this function).

    Returns
    -------
    list[str]
        Filenames to prepend to the step's base inputs, in dependency order.
        Empty list when no prior deliverables apply.
    """
    prior_step_names = _D2_PRIOR_STEPS.get(step_name, [])
    accumulated: list = []
    for prior in prior_step_names:
        files = step_deliverables.get(prior, [])
        for f in files:
            if f not in accumulated:
                accumulated.append(f)
    return accumulated


def _merge_inputs(new_items: list, existing: list) -> list:
    """Prepend new_items to existing, skipping items already present.

    Preserves order: new_items first (in their given order), then existing
    items that are not already in new_items.

    Parameters
    ----------
    new_items:
        Items to prepend (earlier in the inputs list = higher reading priority).
    existing:
        The step's existing input list (from YAML or default).

    Returns
    -------
    list[str]
        Merged list with no duplicates.
    """
    if not new_items:
        return list(existing)
    result = list(new_items)
    for item in existing:
        if item not in result:
            result.append(item)
    return result


def _resolve_step_input_templates(inputs: list, step_deliverables: dict) -> list:
    """Resolve YAML template strings in step inputs to actual filenames.

    The workflow YAML may contain template strings like
    ``"section-{section_name}.md"`` in a step's inputs list.  These cannot be
    rendered literally — instead, replace any input that contains
    ``{section_name}`` with the full set of section-draft deliverables already
    collected in *step_deliverables*.

    Any input that does not contain ``{section_name}`` is passed through unchanged.

    Parameters
    ----------
    inputs:
        Raw inputs list from the workflow YAML (``step.inputs``).
    step_deliverables:
        Dict mapping step_name -> list[filename] collected so far.

    Returns
    -------
    list[str]
        Resolved inputs with template strings replaced by actual filenames.
    """
    resolved: list = []
    for item in inputs:
        if "{section_name}" in item:
            # Replace with the actual section-draft deliverable filenames.
            section_files = step_deliverables.get("section-draft", [])
            for sf in section_files:
                if sf not in resolved:
                    resolved.append(sf)
        else:
            if item not in resolved:
                resolved.append(item)
    return resolved


def inject_document_workflow_tasks(tasks, workflow_def, sections, date_str, base_seq, owner,
                                 git_repo="none", target_version="none",
                                 project_cfg_path=None,
                                 artifact_name="",
                                 resolved_source_paths=None):
    """Inject the document pipeline tasks, evaluating when: predicates.

    This function drives the ``document`` workflow YAML pipeline through the
    materializer, emitting one ticket per step (or one per section for foreach
    steps), while skipping steps whose ``when:`` predicate evaluates to False.

    The two supported predicates are:
      - ``foreach_was_used``       — True when the plan's *sections* list is
                                     non-empty (i.e. the outline foreach step
                                     produced at least one item).
      - ``review_agent_configured`` — True when the project's ``project.cfg``
                                      declares a non-empty ``review_agent``
                                      field (read via *project_cfg_path*).

    Pipeline rules:
      - Steps without ``when:`` always run.
      - Steps with ``foreach: outline.sections`` that ARE NOT skipped generate
        one ticket per section name in *sections*.  When *sections* is empty
        they generate zero tickets, so ``foreach_was_used`` evaluates to False.
      - Steps with ``foreach: outline.sections`` that ARE skipped (because
        ``when: foreach_was_used`` is False and sections is empty) are omitted.
      - The ``draft`` step (short-form only, no foreach, no when) is skipped
        when ``foreach_was_used`` is True so the two paths are mutually
        exclusive.

    Short-form vs long-form:
      - Short-form (sections is empty/absent):  draft is included, section-draft
        and integrate are skipped, review is evaluated against project.cfg.
      - Long-form (sections is non-empty):       section-draft and integrate are
        included, draft is skipped, review is evaluated against project.cfg.

    Mutates *tasks* in-place (replaces contents with the fully assembled
    pipeline).  Returns the list of all synthetic tasks added.

    Parameters
    ----------
    tasks:
        PM-supplied tasks (typically empty for document workflows; accepted for
        forward compatibility).
    workflow_def:
        A ``WorkflowDefinition`` object loaded from the document plugin's
        ``pipeline.yaml`` (``team/workflows/document/pipeline.yaml``).
    sections:
        List of section name strings from the plan-level ``sections`` field.
        When empty or absent the short-form path is used.
    date_str:
        Date string in ``YYYYMMDD`` format from ``assign_task_ids()``.
    base_seq:
        Starting sequence number for synthetic task IDs.
    owner:
        Task owner prefix string, e.g. ``'CLAUDE'``.
    git_repo:
        Git repo URL or ``'none'``.  Document workflows are file-based; synthetic
        tasks always set ``git_repo='none'``.
    target_version:
        Version label (e.g. ``'v0.24.0'``) or ``'none'``.  Propagated to
        task notes.
    project_cfg_path:
        Optional path to the project's ``project.cfg`` file.  Passed through
        to ``evaluate_when_predicate`` for the ``review_agent_configured``
        predicate.  When ``None`` the review step is skipped (opt-in is off
        by default — a project must declare ``review_agent`` in project.cfg
        to enable the review step).
    artifact_name:
        Optional output artifact name slug (e.g. ``"whitepaper"``).  When
        non-empty it is stored on WRITER tasks so ``create_task_folder`` can
        render a ``## Artifact Name`` section in the task README.  Empty
        string is the default (no section rendered).
    resolved_source_paths:
        Optional list of absolute file path strings for pre-resolved source
        document artifacts.  When non-empty, the paths are added to the
        ``inputs`` of WRITER content-creation steps (outline, draft,
        section-draft) and stored on the task dict so ``create_task_folder``
        can render a ``## Source Documents`` section in the task README.
        ``None`` or empty list means no source documents (start-fresh path).
    """
    # Normalise sections: empty list → short-form path.
    if not sections:
        sections = []

    # Normalise optional document-workflow params.
    artifact_name = (artifact_name or "").strip()
    resolved_source_paths = list(resolved_source_paths or [])

    # Content-creation steps that receive source document paths as inputs.
    # These are the steps that WRITER reads to understand what to transform.
    _SOURCE_STEPS = {"outline", "draft", "section-draft", "integrate", "polish"}

    # foreach_was_used is True when sections is non-empty — i.e. the outline
    # step's foreach reference would produce at least one item.
    foreach_produced = len(sections) > 0

    # Document workflows are file-based (no git branch lifecycle).
    _task_git_repo = "none"

    seq = base_seq

    # Collect IDs of tasks from previous pipeline steps so we can wire prereqs.
    # Maps step name → list of task_ids emitted by that step (list because
    # foreach steps can emit multiple).
    step_ids: dict = {}  # step_name -> list[task_id]

    # Track the task_id of the most recently materialised non-bookend step
    # so each step can declare the previous step as its prerequisite.
    prev_task_ids: list = []  # IDs of the last step (or last foreach batch)

    # D2: track output file names for each step so later steps can include
    # accumulated prior deliverables in their ## Inputs (D2 chain).
    # Maps step_name → list of deliverable filenames produced by that step.
    step_deliverables: dict = {}  # step_name -> list[str]

    all_synthetic = []

    for step in workflow_def.pipeline:
        step_name = step.name

        # --- Evaluate when: predicate ---
        when_result = evaluate_when_predicate(
            step.when,
            foreach_produced_items=foreach_produced,
            workflow_agents=workflow_def.agents,
            project_cfg_path=project_cfg_path,
        )
        if not when_result:
            # Predicate is False — skip this step entirely.
            continue

        # --- Special mutual-exclusion rule for 'draft' vs long-form path ---
        # The 'draft' step is the short-form single-document draft.  It must
        # be skipped when section-draft / integrate are in play (i.e. when
        # foreach produced items), even though it has no explicit when: gate.
        if step_name == "draft" and foreach_produced:
            continue

        # --- foreach step → one ticket per section ---
        if step.foreach:
            if not sections:
                # foreach with empty sections — zero tickets; foreach_was_used
                # stays False (already computed before the loop).
                step_ids[step_name] = []
                prev_task_ids = []
                continue

            batch_ids = []
            batch_deliverables = []
            for section_name in sections:
                slug_section = re.sub(r'[^a-z0-9]+', '-', section_name.lower()).strip('-')
                slug_section = slug_section[:25]
                section_deliverable = f"section-{slug_section}.md"
                # New format: WRITER-DATE-NNN-slug (no owner prefix)
                task_id = f"WRITER-{date_str}-{seq:03d}-{step_name}-{slug_section}"
                seq += 1
                # Build inputs (D2 chain): section-draft receives the outline
                # as accumulated prior deliverable plus source docs.
                # Base: step.inputs from YAML, or ["outline.md"] as fallback so
                # WRITER always has the outline available.
                _base_inputs = list(step.inputs) if step.inputs else ["outline.md"]
                # D2: prepend accumulated prior deliverables (outline.md).
                _accumulated = _d2_accumulated_inputs(step_name, step_deliverables)
                _base_inputs = _merge_inputs(_accumulated, _base_inputs)
                if step_name in _SOURCE_STEPS and resolved_source_paths:
                    _section_inputs = resolved_source_paths + _base_inputs
                else:
                    _section_inputs = _base_inputs
                section_task = {
                    "task_id": task_id,
                    "slug": f"{step_name}-{slug_section}",
                    "owner": owner,
                    "title": f"WRITER: {step_name.replace('-', ' ').title()} — {section_name}",
                    "role": step.role,
                    "assigned_agent": step.role,
                    "working_directory": "none",
                    "git_repo": _task_git_repo,
                    "source_branch": "none",
                    "feature_branch": "none",
                    "goal": f"Write a draft for the '{section_name}' section.",
                    "inputs": _section_inputs,
                    "context_paths": [],
                    "required_output": section_deliverable,
                    "constraints": [],
                    "acceptance_criteria": [f"{section_deliverable} exists with written draft."],
                    "depends_on": [],
                    "prerequisite_ids": list(prev_task_ids),
                    "notes": (
                        f"Document pipeline step '{step_name}' (foreach) for section "
                        f"'{section_name}'. target_version={target_version}"
                    ),
                    "target_version": target_version,
                    "_synthetic": True,
                    "_prose_step": step_name,
                    "_prose_section": section_name,
                }
                # Inject document-workflow fields when present.
                if artifact_name:
                    section_task["artifact_name"] = artifact_name
                if step_name in _SOURCE_STEPS and resolved_source_paths:
                    section_task["source_document_paths"] = resolved_source_paths
                all_synthetic.append(section_task)
                batch_ids.append(task_id)
                batch_deliverables.append(section_deliverable)

            step_ids[step_name] = batch_ids
            # D2: record all section-draft deliverables so integrate can list them.
            step_deliverables[step_name] = batch_deliverables
            prev_task_ids = list(batch_ids)
            continue

        # --- Normal (non-foreach) step ---
        # Determine role from workflow step definition.
        role = step.role
        assigned = step.role  # use role as the assigned_agent for document steps

        # Determine autonomous_criterion constraint if set.
        extra_constraints = []
        if step.autonomous_criterion:
            extra_constraints.append(f"autonomous_criterion: {step.autonomous_criterion}")

        # Handle special operations (open_doc, finalize).
        cm_operation = None
        step_marker = {}
        if step.operation == "open_doc":
            cm_operation = "open_doc"
            step_marker["_prose_open_doc"] = True
            step_marker["_doc_open"] = True  # alias used by doc-workflow tests
        elif step.operation == "finalize":
            cm_operation = "finalize"
            step_marker["_prose_finalize"] = True
            step_marker["_doc_finalize"] = True  # alias used by doc-workflow tests

        # CM bookend steps: add cm_operation to constraints so the rendered README
        # ## Constraints field carries a machine-readable operation marker.  This
        # matches the convention used by inject_document_workflow_tasks() and
        # inject_cm_bookends(), and lets the CM agent identify the task type
        # without relying on literal step names in ## Notes.
        if cm_operation:
            extra_constraints.append(f"cm_operation: {cm_operation}")

        # New format: ROLE-DATE-NNN-slug (no owner prefix)
        task_id = f"{role}-{date_str}-{seq:03d}-{step_name}"
        seq += 1

        # Build prereq list: all IDs from the previous step.
        prereq_ids = list(prev_task_ids)

        # Build inputs (D2 chain): prepend accumulated prior deliverables, then
        # resolve any template strings from the workflow YAML, then prepend
        # source document paths for WRITER content steps.
        _base_step_inputs = list(step.inputs) if step.inputs else []
        # D2: resolve template strings from YAML (e.g. "section-{section_name}.md")
        # into the actual section-draft deliverable filenames collected earlier.
        _base_step_inputs = _resolve_step_input_templates(
            _base_step_inputs, step_deliverables
        )
        # D2: prepend accumulated prior deliverables for WRITER content steps.
        _accumulated = _d2_accumulated_inputs(step_name, step_deliverables)
        _base_step_inputs = _merge_inputs(_accumulated, _base_step_inputs)
        if step_name in _SOURCE_STEPS and resolved_source_paths:
            _step_inputs = resolved_source_paths + _base_step_inputs
        else:
            _step_inputs = _base_step_inputs

        # D2: record this step's deliverable so later steps can include it
        # in their accumulated prior inputs.
        _step_deliverable = step.deliverable if step.deliverable else f"{step_name}.md"
        if step_name not in ("open-doc", "finalize") and not cm_operation:
            step_deliverables[step_name] = [_step_deliverable]

        task_dict = {
            "task_id": task_id,
            "slug": step_name,
            "owner": owner,
            "title": f"{role}: {step_name.replace('-', ' ').title()}",
            "role": role,
            "assigned_agent": assigned,
            "working_directory": "none",
            "git_repo": _task_git_repo,
            "source_branch": "none",
            "feature_branch": "none",
            "goal": f"Execute the '{step_name}' step of the document pipeline.",
            "inputs": _step_inputs,
            "context_paths": [],
            "required_output": step.deliverable if step.deliverable else f"{step_name}.md",
            "constraints": extra_constraints,
            "acceptance_criteria": [
                f"{step.deliverable if step.deliverable else step_name + '.md'} exists."
            ],
            "depends_on": [],
            "prerequisite_ids": prereq_ids,
            "notes": (
                f"Document pipeline step '{step_name}'. target_version={target_version}"
            ),
            "target_version": target_version,
            "_synthetic": True,
            "_prose_step": step_name,
        }
        if cm_operation:
            task_dict["cm_operation"] = cm_operation
        task_dict.update(step_marker)
        # Inject document-workflow fields when present (non-CM steps only).
        if artifact_name and not cm_operation:
            task_dict["artifact_name"] = artifact_name
        if step_name in _SOURCE_STEPS and resolved_source_paths:
            task_dict["source_document_paths"] = resolved_source_paths

        all_synthetic.append(task_dict)
        step_ids[step_name] = [task_id]
        prev_task_ids = [task_id]

    # Wire together integrate's prerequisites: all section-draft IDs.
    # The integrate step's prereq_ids were set to prev_task_ids at the time
    # integrate was added (which is the last batch of section-draft IDs).
    # That is already correct because we set prev_task_ids = batch_ids after
    # each foreach step. No additional wiring needed.

    # Add any PM-supplied tasks (typically none for document workflows) before integrate.
    # For now, the document pipeline is fully synthetic — PM tasks are not expected.
    _ = tasks  # accepted but not used; log if any arrive
    if tasks:
        print(
            f"[pm-materialize] WARNING: {len(tasks)} PM-supplied task(s) passed to "
            f"inject_document_workflow_tasks — these are not inserted into the pipeline "
            f"(document pipeline is fully synthetic).",
            file=sys.stderr,
        )

    # Replace tasks list content with the fully assembled pipeline.
    tasks.clear()
    tasks.extend(all_synthetic)

    return all_synthetic


def create_human_approve_folder(task, team_root, dry_run=False, tasks_root=None):
    """Create the task folder for the HUMAN-APPROVE gate task with a custom README.

    tasks_root: explicit Path (or str) for the tasks directory. Task folders are
    created at tasks_root/task_id. Must be provided — passing None raises ValueError.
    No environment variable fallback is used; pass tasks_root explicitly.
    """
    # tasks_root is required — no environment variable fallback is used.
    if tasks_root is None:
        raise ValueError(
            "tasks_root must be explicitly provided (no env var fallback). "
            "Pass tasks_root=<path> explicitly."
        )
    task_id = task["task_id"]
    target_version = task.get("target_version", "unknown")
    cm_release_id = task.get("_cm_release_id", "unknown")
    feature_task_ids = task.get("inputs", [])
    prereq_ids = task.get("prerequisite_ids", [])

    feature_task_list = format_list(feature_task_ids)
    prerequisites = format_list(prereq_ids) if prereq_ids else "none"

    readme_content = HUMAN_APPROVE_README_TEMPLATE.format(
        task_id=task_id,
        version=target_version,
        feature_task_list=feature_task_list,
        prerequisites=prerequisites,
        cm_release_id=cm_release_id,
    )

    # HUMAN-APPROVE tasks have prerequisites (work tasks + TESTER); use WAITING.
    human_initial_state = "WAITING" if prereq_ids else "BACKLOG"
    status_content = STATUS_TEMPLATE.format(
        task_id=task_id,
        participant="Human",
        role="HUMAN",
        initial_state=human_initial_state,
    )

    # tasks_root is required (ValueError raised above if None).
    _effective_tasks_root = Path(tasks_root)
    task_dir = _effective_tasks_root / task_id
    artifacts_dir = task_dir / "artifacts"
    logs_dir = task_dir / "logs"

    if dry_run:
        if task_dir.is_dir():
            print(f"[dry-run] Skipping existing task folder: {task_dir}/")
        else:
            print(f"[dry-run] Would create: {task_dir}/")
            print(f"[dry-run]   README.md ({len(readme_content)} chars) [HUMAN-APPROVE gate]")
            print(f"[dry-run]   status.md ({len(status_content)} chars)")
            print(f"[dry-run]   artifacts/")
            print(f"[dry-run]   logs/")
        return task_id

    # Idempotency guard: skip if task folder already exists.
    if task_dir.is_dir():
        print(f"[pm-materialize] Skipping existing HUMAN-APPROVE task: {task_id}", file=sys.stderr)
        return task_id

    task_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir.mkdir(exist_ok=True)
    logs_dir.mkdir(exist_ok=True)
    (task_dir / "README.md").write_text(readme_content)
    (task_dir / "status.md").write_text(status_content)

    print(f"[pm-materialize] Created HUMAN-APPROVE task: {task_id}", file=sys.stderr)
    return task_id


def ensure_default_workspace(team_root, project_name, tasks):
    """If no tasks specify a working directory, create a default workspace and assign it.

    For self-build (the kanban building itself), the default workspace must be
    the dev tree, NOT a subdirectory of the live install. Detection: if
    PGAI_DEV_TREE_PATH is set and exists as a git checkout, treat this as a
    self-build and use the dev tree directly. Otherwise, fall back to the
    default of team_root/workspaces/projects/<project_name> for non-self-build
    projects.
    """
    needs_default = False
    for task in tasks:
        wd = normalize_workspace_value(task.get("working_directory"))
        if wd.lower() in ("none", ""):
            needs_default = True
            break

    if not needs_default:
        return

    # Prefer dev tree as default workspace for self-build scenarios.
    dev_tree = _resolve_dev_tree_path(team_root)
    dev_tree_path = Path(dev_tree)
    use_dev_tree = (
        dev_tree_path.is_dir()
        and (dev_tree_path / ".git").exists()
    )

    if use_dev_tree:
        default_workspace = dev_tree_path
        print(
            f"[pm-materialize] Default workspace (self-build): {default_workspace}",
            file=sys.stderr,
        )
    else:
        # Historical default for non-self-build projects.
        default_workspace = Path(team_root) / "workspaces" / "projects" / (project_name or "unnamed")
        default_workspace.mkdir(parents=True, exist_ok=True)
        print(f"[pm-materialize] Default workspace: {default_workspace}", file=sys.stderr)

    for task in tasks:
        wd = normalize_workspace_value(task.get("working_directory"))
        if wd.lower() in ("none", ""):
            task["working_directory"] = str(default_workspace)


def main():
    parser = argparse.ArgumentParser(description="Materialize a task plan into kanban folders")
    parser.add_argument("plan_file", help="Path to plan JSON from pm subagent")
    parser.add_argument("--team-root", default=None, help="Override kanban root path")
    parser.add_argument("--owner", default="CLAUDE", help="Task owner (default: CLAUDE)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument(
        "--requirements-path",
        default=None,
        metavar="PATH",
        help=(
            "Absolute path of the requirements doc being decomposed. "
            "Takes precedence over the plan JSON requirements_path field so the "
            "path cannot be lost when PM omits it from the plan. "
            "When absent the plan field is used; when neither is set, 'none' is used."
        ),
    )
    args = parser.parse_args()

    plan_path = Path(args.plan_file)
    if not plan_path.is_file():
        print(f"ERROR: Plan file not found: {args.plan_file}", file=sys.stderr)
        sys.exit(1)

    plan_content = plan_path.read_text()
    plan = json.loads(plan_content)

    # Resolve kanban root: CLI arg wins, then fall back to config-driven default.
    cli_root = args.team_root or None
    cfg = get_config(kanban_root=cli_root)
    team_root = cfg["KANBAN_ROOT"]

    # PGAI_PROJECT_ROOT takes priority over PGAI_AGENT_KANBAN_ROOT_PATH
    # for per-project path resolution (tasks, queues, release-state, bugs).
    _pgai_project_root = os.environ.get("PGAI_PROJECT_ROOT")
    if _pgai_project_root and not cli_root:
        team_root = _pgai_project_root

    # Always resolve team_root to an absolute canonical path so relative
    # paths from the CLI do not produce incorrect sub-directory computations.
    team_root = str(Path(team_root).resolve())

    # Validate that team_root is not inside a task directory. This catches
    # the case where --team-root was passed pointing to a PM task's artifacts dir.
    if not validate_team_root(team_root):
        print(
            f"[pm-materialize] WARNING: --team-root '{team_root}' appears to be inside a task directory. "
            f"Falling back to config-derived KANBAN_ROOT to prevent nesting tasks inside task folders.",
            file=sys.stderr,
        )
        # Re-derive from config WITHOUT the CLI override
        cfg_fallback = get_config(kanban_root=None)
        team_root = str(Path(cfg_fallback["KANBAN_ROOT"]).resolve())
        tasks_root = Path(cfg_fallback["PGAI_TASKS_DIR"]).resolve()
    else:
        tasks_root = Path(cfg["PGAI_TASKS_DIR"]).resolve()

    tasks = plan.get("tasks", [])

    # Peek at workflow_type to decide whether an empty tasks list is valid.
    # For document workflows, the pipeline is fully synthetic — PM produces
    # no tasks of its own, so an empty tasks list is expected and must not exit early.
    _peek_workflow_type = (plan.get("workflow_type") or "release").strip().lower()
    _fully_synthetic_workflows = {"document"}
    if not tasks and _peek_workflow_type not in _fully_synthetic_workflows:
        print("[pm-materialize] No tasks in plan. Nothing to do.", file=sys.stderr)
        sys.exit(0)
    elif not tasks and _peek_workflow_type in _fully_synthetic_workflows:
        print(
            f"[pm-materialize] No PM tasks in plan (expected for {_peek_workflow_type} workflow — "
            "pipeline is fully synthetic). Proceeding.",
            file=sys.stderr,
        )

    project_name = plan.get("project_name", "unnamed-project")

    # Cross-check plan.project_name against the resolved team_root.
    #
    # When pm-agent.sh incorrectly places the PM task under the default project
    # (pgai-agent-kanban) instead of the target project, the wake script's
    # auto-materialization call receives the wrong --team-root. This guard detects
    # that mismatch and overrides team_root / tasks_root to the correct project path
    # so that task folders and queue markers land under the right project directory.
    #
    # Correction logic:
    #   - Only fires when plan.project_name is a non-empty, non-sentinel value.
    #   - Only fires when the resolved team_root does NOT already contain the
    #     expected "projects/<project_name>" path segment (i.e., there is a mismatch).
    #   - Correction is only attempted in multi-project layout: team_root.parent
    #     must be a directory named "projects". In single-project layout
    #     (team_root IS the kanban root with no projects/ subdirectory) the check
    #     is skipped — no auto-correction is applied and a warning is emitted.
    #   - Self-build (kanban-self, project_name="pgai-agent-kanban") passes
    #     this check naturally because team_root is already
    #     <kanban_root>/projects/pgai-agent-kanban which contains
    #     "projects/pgai-agent-kanban".
    _plan_project_name = (project_name or "").strip()
    if _plan_project_name and _plan_project_name != "unnamed-project":
        _expected_segment = f"projects/{_plan_project_name}"
        _team_root_resolved = Path(team_root).resolve()
        if _expected_segment not in str(_team_root_resolved):
            # Mismatch detected. Check if we are in multi-project layout.
            _parent_dir = _team_root_resolved.parent
            if _parent_dir.name == "projects" and _parent_dir.is_dir():
                # Multi-project layout: team_root is <kanban_root>/projects/<wrong_name>.
                # Correct by replacing the trailing project name with the plan's project.
                _corrected_root = str(_parent_dir / _plan_project_name)
                print(
                    f"[pm-materialize] WARNING: team_root '{team_root}' does not match "
                    f"plan project_name '{_plan_project_name}' "
                    f"(expected path segment '{_expected_segment}' not found). "
                    f"Overriding team_root to '{_corrected_root}' to prevent task folders "
                    f"and queue markers from landing in the wrong project. "
                    f"Root cause: pm-agent.sh may have created the PM task under the "
                    f"default project instead of '{_plan_project_name}'.",
                    file=sys.stderr,
                )
                team_root = _corrected_root
                tasks_root = Path(_corrected_root) / "tasks"
            else:
                # Cannot auto-correct (no projects/ parent directory found).
                # Warn loudly so the operator can investigate.
                print(
                    f"[pm-materialize] WARNING: team_root '{team_root}' does not match "
                    f"plan project_name '{_plan_project_name}' "
                    f"(expected path segment '{_expected_segment}' not found). "
                    f"Cannot auto-correct: parent directory '{_parent_dir}' is not a "
                    f"'projects' directory (legacy layout or unexpected structure). "
                    f"Proceeding with original team_root — verify invocation is correct.",
                    file=sys.stderr,
                )

    # Approach A: Plan-content hash idempotency guard.
    #
    # Compute SHA-256 of the plan file content. If a marker file
    # <plan-dir>/.materialized.<sha256-hash> already exists, this plan has
    # been materialized before — log a warning and exit cleanly.
    #
    # The marker file is written after successful materialization and records
    # the timestamp and the list of task IDs that were created.
    plan_hash = compute_plan_hash(plan_content)
    plan_dir = plan_path.parent
    print(f"[pm-materialize] Plan hash: {plan_hash[:16]}...", file=sys.stderr)

    if not args.dry_run:
        existing_marker = find_existing_marker(plan_dir, plan_hash)
        if existing_marker is not None:
            print(
                f"[pm-materialize] Plan already materialized (idempotent no-op). "
                f"Marker file exists: {existing_marker}. "
                f"Skipping materialization to prevent duplicate ticket sets.",
                file=sys.stderr,
            )
            print(f"[pm-materialize] Remove {existing_marker} to force re-materialization.", file=sys.stderr)
            # Exit 2 signals the wake script that this is an idempotent no-op
            # (hash-marker already present), not a genuine failure.  The wake
            # script maps exit 2 to INFO rather than WARNING.  Exit 1 is reserved
            # for genuine materialization failures.
            sys.exit(2)
    else:
        existing_marker = find_existing_marker(plan_dir, plan_hash)
        if existing_marker is not None:
            print(f"[dry-run] Plan already materialized (marker: {existing_marker}). Would skip.")
        else:
            print(f"[dry-run] No existing marker — would proceed with materialization.")

    # Read requirements_path using three-tier precedence:
    #   1. --requirements-path CLI value (authoritative when provided), else
    #   2. the plan JSON's requirements_path field, else
    #   3. "none".
    # The CLI value makes the path available independent of the plan JSON so it
    # cannot be silently lost when PM omits the field (document workflows need
    # this to avoid WRITER running without context).
    # Read this BEFORE resolving target_version so the path can be passed to
    # _compute_next_patch_py to exclude the requirements file itself from the
    # collision check (a file cannot collide with its own version slot).
    _cli_req_path = (getattr(args, "requirements_path", None) or "").strip()
    _plan_req_path = (plan.get("requirements_path") or "").strip()
    if _cli_req_path:
        requirements_path = _cli_req_path
        print(
            f"[pm-materialize] Requirements path (from --requirements-path CLI): {requirements_path}",
            file=sys.stderr,
        )
    elif _plan_req_path and _plan_req_path.lower() != "none":
        requirements_path = _plan_req_path
        print(
            f"[pm-materialize] Requirements path (from plan JSON): {requirements_path}",
            file=sys.stderr,
        )
    else:
        requirements_path = "none"
        print("[pm-materialize] Requirements path: none (not provided via CLI or plan JSON)", file=sys.stderr)

    # Terminal-state guard: refuse to (re-)materialize an intake item whose
    # ## Status is a terminal state (done or wont-do).  A terminal item is
    # definitively resolved; mutating its state would clobber the operator's
    # explicit close decision and re-arm the bug this guard protects against.
    #
    # Fail-loud: emit the skip line and exit non-zero.  Never silently proceed.
    if requirements_path and requirements_path.lower() != "none":
        _req_path = Path(requirements_path)
        if _req_path.is_file():
            _req_text = _req_path.read_text(encoding="utf-8", errors="replace")
            _status_re = re.compile(
                r'^##\s+Status\s*\n\s*(\S+)', re.M | re.IGNORECASE
            )
            _status_m = _status_re.search(_req_text)
            if _status_m:
                _req_status_raw = _status_m.group(1).strip()
                if _bundle_is_terminal(_req_status_raw):
                    print(
                        f"[pm-materialize] skipping {_req_path.name}: "
                        f"terminal state '{_req_status_raw.lower()}'",
                        file=sys.stderr,
                    )
                    sys.exit(0)

    # Read target_version from the plan (propagated from ## Target Version in the
    # requirements doc).  Falls back to "none" if missing or blank.
    #
    # Auto-sentinel handling: if the requirements doc declared Target Version as
    # 'auto', 'next-patch', empty string, or omitted the field entirely, the PM
    # subagent propagates that sentinel value into the plan JSON.  The materializer
    # resolves it to the next available patch slot at materialization time using
    # _compute_next_patch_py (the Python equivalent of discovery_compute_next_patch).
    # A log line is emitted recording the computed version and the source file path.
    _raw_target_version = (plan.get("target_version") or "").strip()
    if _raw_target_version.lower() == "none":
        # PM explicitly set "none" — treat as a missing/auto sentinel for
        # framework-authored plans, but preserve "none" semantics for the
        # existing release-workflow "unversioned" fallback path below.
        # We leave it as "none" here; inject_cm_bookends will warn and use
        # "unversioned".
        target_version = "none"
        print(f"[pm-materialize] Target version: {target_version}", file=sys.stderr)
    elif _is_auto_sentinel(_raw_target_version):
        # Auto-sentinel: resolve to the next available patch slot.
        target_version, _was_auto = resolve_target_version(
            _raw_target_version, team_root, requirements_path
        )
        print(
            f"[pm-materialize] Target version: {target_version} "
            f"(auto-resolved from sentinel '{_raw_target_version}'; "
            f"source: {requirements_path})",
            file=sys.stderr,
        )
    else:
        # Explicit vX.Y.Z — honour verbatim; bump-around handles collisions via CM.
        target_version = _raw_target_version
        print(f"[pm-materialize] Target version: {target_version}", file=sys.stderr)

    # Read human_approval_required from the plan.
    # Valid values: 'required' (inject HUMAN-APPROVE gate) or 'auto' (skip it).
    # Missing field defaults to 'auto'.
    # Invalid values are warned and default to 'auto' (handled inside inject_cm_bookends).
    _raw_har = (plan.get("human_approval_required") or "").strip() or "auto"
    human_approval_required = _raw_har
    print(f"[pm-materialize] human_approval_required: {human_approval_required}", file=sys.stderr)

    # Read workflow_type from the plan. Controls which workflow pipeline is executed.
    # Default: 'release' when absent or empty.
    workflow_type = (plan.get("workflow_type") or "release").strip().lower()
    print(f"[pm-materialize] Workflow type: {workflow_type}", file=sys.stderr)
    # Load the pipeline definition from workflows/<type>/pipeline.yaml, constructed
    # from the type string.  The path is resolved generically by load_workflow —
    # no per-type path constants, no enumeration of type names here.
    #
    # Two outcomes:
    #   pipeline found  → _workflow_def is a WorkflowDefinition; pipeline-driven
    #                     dispatch (e.g. inject_document_workflow_tasks) applies.
    #   not found       → _workflow_def is None; simple wf_agents path applies.
    #                     This is the documented default for types without a
    #                     pipeline.yaml (e.g. testing-only).
    #
    # A file that IS found but is malformed or invalid still exits with an error
    # so the operator can fix it.
    #
    # Do NOT pass team_root as kanban_root. The workflows/ directory is shared
    # infrastructure that lives at the kanban root, not under any project subdir.
    # team_root may equal the project subdir (when invoked with
    # --team-root $PGAI_PROJECT_ROOT for multi-project layout), in which
    # case passing it would make load_workflow look in projects/<name>/workflows/
    # and fail. Letting load_workflow default to PGAI_AGENT_KANBAN_ROOT_PATH
    # is correct: workflows live at kanban root regardless of which project this
    # materializer call belongs to.
    try:
        _workflow_def = load_workflow(workflow_type)
        print(
            f"[pm-materialize] Loaded workflow definition: {_workflow_def.name} "
            f"({len(_workflow_def.pipeline)} pipeline steps)",
            file=sys.stderr,
        )
    except WorkflowError as _wf_err:
        _wf_err_str = str(_wf_err)
        if "not found" in _wf_err_str:
            # No pipeline.yaml for this type — use the simple wf_agents path.
            _workflow_def = None
            print(
                f"[pm-materialize] No pipeline.yaml for workflow_type '{workflow_type}'; "
                "using simple wf_agents decomposition path.",
                file=sys.stderr,
            )
        else:
            # Pipeline file exists but is malformed or invalid — hard error.
            print(
                f"[pm-materialize] ERROR: invalid pipeline for workflow_type "
                f"'{workflow_type}': {_wf_err}",
                file=sys.stderr,
            )
            sys.exit(1)

    # Read the workflow plugin manifest (workflow.cfg [capabilities]) so the graph
    # assembly below can derive bookend emission and roster validation from the
    # plugin's declared capabilities — not from a hardcoded type switch.
    #
    # This implements the engine-queries-capabilities invariant (B39→B42→B45→B47→B51):
    # the materializer must ask the plugin whether it needs CM bookends before
    # injecting them, just as discovery asks the plugin about version_semantics
    # and the wake script asks about git_mode before setting up worktrees.
    #
    # Note: do NOT pass team_root here — workflows/ lives at kanban root, not
    # at the project subdirectory.  load_workflow_capabilities defaults to
    # PGAI_AGENT_KANBAN_ROOT_PATH, which is correct for all project layouts.
    _wf_caps = load_workflow_capabilities(workflow_type)
    if _wf_caps:
        print(
            f"[pm-materialize] Workflow capabilities for '{workflow_type}': "
            f"git_mode={_wf_caps.get('git_mode', '?')!r} "
            f"finalize={_wf_caps.get('finalize', '?')!r} "
            f"agents={_wf_caps.get('agents', '?')!r}",
            file=sys.stderr,
        )
    else:
        print(
            f"[pm-materialize] WARNING: workflow.cfg manifest not found for "
            f"workflow_type '{workflow_type}'; defaulting to release-shaped behavior.",
            file=sys.stderr,
        )

    # Read source_branch (required for feature workflows).
    # Falls back to active RC from release-state.md when not set in the plan.
    _plan_source_branch = (plan.get("source_branch") or "").strip()
    if workflow_type == "feature" and (not _plan_source_branch or _plan_source_branch.lower() == "none"):
        # Try active RC as fallback
        _active_rc = get_active_rc(team_root)
        if _active_rc:
            _plan_source_branch = f"rc/{_active_rc}"
            print(
                f"[pm-materialize] source_branch not set; using active RC as fallback: {_plan_source_branch}",
                file=sys.stderr,
            )
        else:
            print(
                "[pm-materialize] ERROR: workflow_type=feature but source_branch is not set "
                "and no Active RC found in release-state.md. Cannot determine shared branch.",
                file=sys.stderr,
            )
            sys.exit(1)
    feature_source_branch = _plan_source_branch

    # Read test_required from the plan. Controls whether a TESTER task is appended.
    # Default: True.
    _raw_tr = (plan.get("test_required") or "true").strip().lower()
    test_required = _raw_tr not in ("false", "0", "no")
    print(f"[pm-materialize] test_required: {test_required}", file=sys.stderr)

    # Read parent_branch (used by feature workflow create-shared-branch ticket).
    # Default: 'main'.
    parent_branch = (plan.get("parent_branch") or "main").strip()
    if not parent_branch or parent_branch.lower() == "none":
        parent_branch = "main"
    print(f"[pm-materialize] parent_branch: {parent_branch}", file=sys.stderr)

    # Step 1a: roster guard — verify every plan task's role is in the plugin's
    # declared agents roster.  This is the fail-closed mirror to capability-gated
    # bookend emission: if PM emitted an agent outside the roster, refuse loudly
    # now rather than silently creating a task that the wake script would never
    # dispatch (no queue, wrong role).  The guard fires BEFORE ID assignment so
    # the error names the slug and role rather than a task ID the caller has never
    # seen.  Synthetic tasks are not in the plan list at this point.
    _manifest_roster = _parse_manifest_roster(_wf_caps)
    _check_plan_roster_against_manifest(tasks, _manifest_roster, workflow_type)

    # Step 1: assign default workspace if needed (before assigning IDs).
    # Synthetic CM/HUMAN tasks have working_directory="none" intentionally;
    # they are not yet in the list at this point so no filtering needed.
    if not args.dry_run:
        ensure_default_workspace(team_root, project_name, tasks)
    else:
        # In dry-run, simulate the same dev-tree-vs-workspaces decision that
        # ensure_default_workspace() uses, without creating directories.
        _dry_dev_tree = _resolve_dev_tree_path(team_root)
        _dry_dev_tree_path = Path(_dry_dev_tree)
        _dry_use_dev_tree = (
            _dry_dev_tree_path.is_dir()
            and (_dry_dev_tree_path / ".git").exists()
        )
        _dry_default = (
            _dry_dev_tree_path
            if _dry_use_dev_tree
            else Path(team_root) / "workspaces" / "projects" / project_name
        )
        for task in tasks:
            wd = normalize_workspace_value(task.get("working_directory"))
            if wd.lower() in ("none", ""):
                task["working_directory"] = str(_dry_default)

    # Step 2: assign task IDs and feature branches to the original work tasks.
    # Returns (date_str, start_seq) so we can compute non-colliding CM seq numbers.
    date_str, start_seq = assign_task_ids(tasks, args.owner, tasks_root)

    # Step 2b: override git_repo for every PM-emitted task with the canonical
    # URL from project.cfg [project] git_repo_url.  PM's emitted value is
    # treated as advisory only.  When project.cfg is absent or has no
    # git_repo_url, fall through unchanged.
    # This override runs BEFORE bookend/TESTER injection so that synthetic tasks
    # (CM bookends, TESTER) created with git_repo='none' are never affected.
    _canonical_git_repo = load_project_git_repo_url(team_root)
    if _canonical_git_repo:
        _n_overridden = apply_git_repo_override(
            tasks, _canonical_git_repo, dry_run=args.dry_run
        )
        if _n_overridden:
            print(
                f"[pm-materialize] GIT-REPO-URL GUARD: applied git_repo override to "
                f"{_n_overridden} task(s) from project.cfg git_repo_url "
                f"'{_canonical_git_repo}'",
                file=sys.stderr,
            )

    # Step 3: inject bookend / prefix tasks based on workflow_type.
    #
    # release workflow: CM bookend injection is UNCONDITIONAL — every release
    #   plan gets CM-open-rc at position 1 and CM-release at the final position
    #   regardless of whether target_version is set in the plan JSON. Bookend
    #   injection must not be conditioned on target_version; use an unconditional
    #   else branch so PM cannot omit target_version and silently skip bookends.
    #
    # feature workflow (shared-branch decomposition mode): CODER create-shared-branch
    #                   as ticket 1; TESTER appended when test_required=True;
    #                   NO CM bookends regardless of target_version.
    #
    # document workflow:
    #   Dispatches to inject_document_workflow_tasks() using the workflow
    #   definition loaded from workflows/document/pipeline.yaml (the superset
    #   pipeline with when: gating for short-form vs long-form).
    if workflow_type == "document":
        # Read sections from the plan — empty list → short-form, non-empty → long-form.
        _doc_sections = plan.get("sections") or []
        if not isinstance(_doc_sections, list):
            print(
                f"[pm-materialize] WARNING: 'sections' field is not a list "
                f"(got {type(_doc_sections).__name__}); treating as empty (short-form path).",
                file=sys.stderr,
            )
            _doc_sections = []

        # --- ## Artifact Name ---
        # Read from the plan; fall back to deriving from the requirements filename.
        _raw_artifact_name = (plan.get("artifact_name") or "").strip()
        if _raw_artifact_name:
            _doc_artifact_name = _raw_artifact_name
            print(
                f"[pm-materialize] Artifact name (from plan): {_doc_artifact_name}",
                file=sys.stderr,
            )
        else:
            _doc_artifact_name = _derive_artifact_name(requirements_path)
            print(
                f"[pm-materialize] Artifact name (derived from filename): {_doc_artifact_name}",
                file=sys.stderr,
            )

        # --- ## Source Documents ---
        # Read from the plan; resolve each slug to an absolute path under
        # projects/<proj>/artifacts/.  Absent = start-fresh, no resolution.
        _raw_source_docs = plan.get("source_documents") or []
        if isinstance(_raw_source_docs, str):
            # Tolerate a single string value by wrapping it.
            _raw_source_docs = [_raw_source_docs] if _raw_source_docs.strip() else []
        if not isinstance(_raw_source_docs, list):
            print(
                f"[pm-materialize] WARNING: 'source_documents' field is not a list "
                f"(got {type(_raw_source_docs).__name__}); treating as empty.",
                file=sys.stderr,
            )
            _raw_source_docs = []

        _doc_resolved_sources: list = []
        if _raw_source_docs:
            # The artifacts directory is at <team_root>/artifacts/ in single-project
            # layout or at the project root level.  In multi-project layout (the
            # project is under projects/<name>/), the artifacts dir is at the same
            # level as tasks/ and requirements/ inside team_root.
            _artifacts_dir = Path(team_root) / "artifacts"
            _unresolved: list = []
            _doc_resolved_sources, _unresolved = _resolve_source_documents(
                _raw_source_docs, _artifacts_dir, project_name
            )
            if _unresolved:
                # Missing source files are a hard error: a clear, named failure
                # is required, not a silent skip.
                _missing_msg = ", ".join(repr(s) for s in _unresolved)
                _artifacts_path_str = str(_artifacts_dir)
                print(
                    f"[pm-materialize] ERROR: source_documents resolution failed — "
                    f"the following slugs could not be found in "
                    f"'{_artifacts_path_str}': {_missing_msg}. "
                    f"Ensure each named artifact exists in the artifacts/ directory "
                    f"before materializing this plan.",
                    file=sys.stderr,
                )
                sys.exit(1)
            print(
                f"[pm-materialize] Source documents resolved "
                f"({len(_doc_resolved_sources)}): "
                + ", ".join(_doc_resolved_sources),
                file=sys.stderr,
            )
        else:
            print(
                "[pm-materialize] Source documents: none (start-fresh path)",
                file=sys.stderr,
            )

        _doc_base_seq = start_seq + len(tasks)
        # Resolve project.cfg path for the review_agent_configured predicate.
        # Prefer project.cfg (lowercase); fall back to PROJECT.cfg if absent.
        # The predicate is True iff the project declares review_agent in project.cfg.
        _tr = Path(team_root)
        _doc_cfg_path = (_tr / "project.cfg") if (_tr / "project.cfg").is_file() else (_tr / "PROJECT.cfg")
        synthetic = inject_document_workflow_tasks(
            tasks,
            workflow_def=_workflow_def,
            sections=_doc_sections,
            date_str=date_str,
            base_seq=_doc_base_seq,
            owner=args.owner,
            target_version=target_version,
            project_cfg_path=_doc_cfg_path,
            artifact_name=_doc_artifact_name,
            resolved_source_paths=_doc_resolved_sources,
        )
        if synthetic:
            _labels = [t["task_id"] for t in synthetic]
            print(
                f"[pm-materialize] Injected {len(synthetic)} {workflow_type} workflow tasks: "
                f"{', '.join(_labels)}",
                file=sys.stderr,
            )

        # Invariant assertion: document workflow must have open-doc and finalize bookends.
        _doc_open_present = any(t.get("_prose_open_doc") for t in tasks)
        _doc_finalize_present = any(t.get("_prose_finalize") for t in tasks)
        if not _doc_open_present or not _doc_finalize_present:
            print(
                f"[pm-materialize] ERROR: {workflow_type} workflow bookend invariant violated — "
                "open-doc and/or finalize missing after injection. "
                f"open_doc_present={_doc_open_present} finalize_present={_doc_finalize_present}",
                file=sys.stderr,
            )
            sys.exit(1)

    elif workflow_type == "feature":
        # Determine git_repo from the first task that has one (or "none")
        plan_git_repo = "none"
        for t in tasks:
            gr = normalize_workspace_value(t.get("git_repo"))
            if gr.lower() != "none":
                plan_git_repo = gr
                break

        feature_base_seq = start_seq + len(tasks)
        synthetic = inject_feature_workflow_tasks(
            tasks,
            source_branch=feature_source_branch,
            test_required=test_required,
            parent_branch=parent_branch,
            date_str=date_str,
            base_seq=feature_base_seq,
            owner=args.owner,
            git_repo=plan_git_repo,
        )
        if synthetic:
            _labels = [t["task_id"] for t in synthetic]
            print(
                f"[pm-materialize] Injected {len(synthetic)} feature workflow tasks: "
                f"{', '.join(_labels)}",
                file=sys.stderr,
            )
    else:
        # All other workflow types (e.g. release, testing-only, custom plugins).
        #
        # Derive graph shape from the plugin's declared capabilities rather than
        # hardcoding the release-shaped bookend pattern unconditionally.  This is
        # the engine-queries-capabilities invariant (B39→B42→B45→B47→B51).
        #
        # Gate: emit CM bookends only when the plugin declares:
        #   git_mode = rw   AND   finalize in {tag, publish}
        # This matches exactly the release-lifecycle shape.  Plugins that declare
        # git_mode=ro (testing-only) or finalize=report never need CM bookends.
        #
        # When the manifest is not found (empty _wf_caps), _workflow_requires_cm_bookends
        # returns True — the conservative fallback preserves the previous behavior
        # for custom plugins that predate the manifest contract.
        _emit_cm_bookends = _workflow_requires_cm_bookends(_wf_caps)
        print(
            f"[pm-materialize] Capability check: emit_cm_bookends={_emit_cm_bookends} "
            f"(git_mode={_wf_caps.get('git_mode', '?')!r} "
            f"finalize={_wf_caps.get('finalize', '?')!r})",
            file=sys.stderr,
        )

        # Determine the git_repo from the first task that has one (or "none")
        plan_git_repo = "none"
        for t in tasks:
            gr = normalize_workspace_value(t.get("git_repo"))
            if gr.lower() != "none":
                plan_git_repo = gr
                break

        if _emit_cm_bookends:
            # Release-lifecycle shape: CM-open-rc at position 1 and CM-release at
            # the final position.  When target_version is "none" (PM omitted it),
            # use "unversioned" as a fallback label so bookends are still created
            # and the invariant holds.  A warning is emitted so the operator knows
            # the plan is missing a version tag.
            if target_version.lower() == "none":
                print(
                    "[pm-materialize] WARNING: release-lifecycle workflow has "
                    "target_version='none'. CM bookends will still be injected "
                    "using 'unversioned' as the version label. PM should include "
                    "target_version in the plan JSON for a properly named release.",
                    file=sys.stderr,
                )
                effective_version = "unversioned"
            else:
                effective_version = target_version

            cm_base_seq = start_seq + len(tasks)
            synthetic = inject_cm_bookends(
                tasks,
                target_version=effective_version,
                date_str=date_str,
                base_seq=cm_base_seq,
                owner=args.owner,
                git_repo=plan_git_repo,
                human_approval_required=human_approval_required,
                test_required=test_required,
            )
            if synthetic:
                _har_norm = (human_approval_required or "auto").strip().lower()
                if not test_required:
                    _bookend_label = "CM-open, CM-release"
                elif _har_norm == "required":
                    _bookend_label = "CM-open, TESTER, HUMAN-APPROVE, CM-release"
                else:
                    _bookend_label = "CM-open, TESTER, CM-release"
                print(
                    f"[pm-materialize] Injected {len(synthetic)} bookend tasks"
                    f" ({_bookend_label})",
                    file=sys.stderr,
                )

            # Invariant assertion: verify bookends were actually injected.
            # This catches any future regression where inject_cm_bookends silently
            # returns without adding the required tasks.
            _cm_open_present = any(
                t.get("_synthetic") and t.get("cm_operation") == "open-rc" for t in tasks
            )
            _cm_release_present = any(
                t.get("_synthetic") and t.get("cm_operation") == "release" for t in tasks
            )
            if not _cm_open_present or not _cm_release_present:
                print(
                    "[pm-materialize] ERROR: release-lifecycle bookend invariant violated — "
                    "CM-open-rc and/or CM-release missing after injection. "
                    f"cm_open_present={_cm_open_present} "
                    f"cm_release_present={_cm_release_present}",
                    file=sys.stderr,
                )
                sys.exit(1)
        else:
            # Non-CM workflow (e.g. testing-only with finalize=report): no CM bookends.
            # Inject a standalone TESTER task when test_required=True so verification
            # still runs and the terminal task carries the finalize responsibility.
            _finalize_mode = _wf_caps.get("finalize", "report")
            if test_required:
                simple_tester_base_seq = start_seq + len(tasks)
                synthetic = inject_simple_tester_task(
                    tasks,
                    date_str=date_str,
                    base_seq=simple_tester_base_seq,
                    owner=args.owner,
                    git_repo=plan_git_repo,
                    target_version=target_version,
                    finalize_mode=_finalize_mode,
                )
                if synthetic:
                    print(
                        f"[pm-materialize] Injected {len(synthetic)} simple tester task(s) "
                        f"(finalize_mode={_finalize_mode!r}, no CM bookends)",
                        file=sys.stderr,
                    )
            else:
                print(
                    f"[pm-materialize] Non-CM workflow with test_required=False: "
                    "no TESTER and no CM bookends injected.",
                    file=sys.stderr,
                )

    # Step 4: separate synthetic bookend tasks from work tasks, order work tasks,
    # then re-assemble in the correct bookend order.
    #
    # release-lifecycle (emit_cm_bookends=True):
    #   [CM-open] + [ordered work tasks] + [TESTER] + [HUMAN-APPROVE] + [CM-release]
    # non-CM workflow (emit_cm_bookends=False, e.g. testing-only with finalize=report):
    #   [ordered work tasks] + [TESTER (if test_required)]
    # feature workflow (shared-branch decomposition mode):
    #   [create-shared-branch] + [ordered work tasks] + [TESTER (if test_required)]
    # document workflow:
    #   tasks list is already fully assembled in pipeline order by
    #   inject_document_workflow_tasks() — use it directly without re-ordering.
    #
    # Synthetic tasks already have prerequisite_ids set; work tasks need
    # their sequence-number deps resolved after topological ordering.
    cm_open_tasks = [t for t in tasks if t.get("_synthetic") and t.get("cm_operation") == "open-rc"]
    tester_tasks = [t for t in tasks if t.get("_tester") and not t.get("_doc_review")]
    human_approve_tasks = [t for t in tasks if t.get("_human_approve")]
    cm_release_tasks = [t for t in tasks if t.get("_synthetic") and t.get("cm_operation") == "release"]
    create_branch_tasks = [t for t in tasks if t.get("_create_shared_branch")]
    feature_tasks = [t for t in tasks if not t.get("_synthetic")]

    ordered_features = topological_order(feature_tasks)

    # Step 5: resolve prerequisite sequence numbers to task IDs for work tasks.
    resolve_prerequisites(ordered_features)

    # Final ordered list depends on workflow type.
    if workflow_type == "document":
        # document: fully assembled by inject_document_workflow_tasks() — use as-is.
        ordered_tasks = list(tasks)
    elif workflow_type == "feature":
        # shared-branch decomposition mode: create-shared-branch, work tasks, TESTER (if injected)
        ordered_tasks = create_branch_tasks + ordered_features + tester_tasks
    else:
        # release: CM-open, work tasks, TESTER, HUMAN-APPROVE, CM-release
        ordered_tasks = cm_open_tasks + ordered_features + tester_tasks + human_approve_tasks + cm_release_tasks

    # Step 4a: default-fill plan-level source_branch onto every ticket that lacks one.
    # This ensures tickets emitted by PM without an explicit source_branch (including
    # synthetic TESTER tasks created with "source_branch": "none") inherit the plan's
    # shared source ref.  Per-ticket explicit values are never overwritten.
    _effective_plan_sb = _resolve_plan_source_branch(plan, requirements_path)
    if _effective_plan_sb:
        _sb_updated = _propagate_plan_source_branch(ordered_tasks, _effective_plan_sb)
        print(
            f"[pm-materialize] SOURCE-REF PROPAGATION: filled plan source_branch "
            f"'{_effective_plan_sb}' onto {_sb_updated} ticket(s) that lacked an explicit value.",
            file=sys.stderr,
        )
    else:
        print(
            "[pm-materialize] SOURCE-REF PROPAGATION: no plan-level source_branch resolved; "
            "per-ticket values used as-is.",
            file=sys.stderr,
        )

    # Step 4b: fail-closed guard — refuse to write any task folders when a
    # worktree-requiring role (e.g. TESTER) still has no resolvable source ref.
    # This fires BEFORE create_task_folder so no partial output is written.
    _validate_source_ref_for_worktree_roles(ordered_tasks, _wf_caps, workflow_type)

    # Post-guard invariant: snapshot the task count after all injection (Step 3)
    # and source-ref transformations (Step 4a/4b) have completed.  The count is
    # verified immediately before folder creation to ensure no ticket-synthesis
    # mutation occurs between the guard and create_task_folder.  Any code path
    # that appends, removes, or replaces tasks in ordered_tasks after this point
    # bypasses the source-ref propagation and guard, and will be caught here.
    _guard_task_count = len(ordered_tasks)

    print(f"[pm-materialize] Materializing {len(ordered_tasks)} tasks into {team_root}/tasks/", file=sys.stderr)
    print(f"[pm-materialize] Owner: {args.owner}", file=sys.stderr)
    print(f"[pm-materialize] Project: {project_name}", file=sys.stderr)

    # Approach B: collect existing (role, slug) pairs before materializing any tasks.
    # This set is passed to create_task_folder and updated as new tasks are created,
    # so the dedup check works both for pre-existing tasks and tasks created earlier
    # in the same materialization run.
    existing_role_slugs = collect_existing_role_slugs(tasks_root)
    print(
        f"[pm-materialize] Found {len(existing_role_slugs)} existing (role, slug) pairs for dedup check.",
        file=sys.stderr,
    )

    # recycle_map accumulates {old_task_id -> new_task_id} for every Option A
    # (rename) decision so apply_rename_map_globally can fix cross-task
    # prerequisite references after all folder operations complete.
    recycle_map: dict = {}
    # option_b_blocked tracks whether any task triggered Option B (active collision).
    # When True, the materializer exits non-zero after writing all possible tasks,
    # so the PM task itself can go BLOCKED.
    option_b_blocked = False

    # Enforce the post-guard invariant: ordered_tasks must not have been mutated
    # since the snapshot taken immediately after Step 4b.  A mismatch means a
    # ticket was added or removed after source-ref propagation and the guard ran,
    # which would allow an uninspected ticket to reach create_task_folder.
    if len(ordered_tasks) != _guard_task_count:
        print(
            f"[pm-materialize] ERROR: post-guard invariant violated — ordered_tasks "
            f"length changed from {_guard_task_count} (after Step 4b) to "
            f"{len(ordered_tasks)} (before folder creation). "
            "No ticket-synthesis mutation is permitted between Step 4b and "
            "create_task_folder. Check for injectors or list mutations added "
            "after the source-ref guard.",
            file=sys.stderr,
        )
        sys.exit(1)

    created_tasks = []
    for task in ordered_tasks:
        # HUMAN-APPROVE tasks get a specialized README explaining the gate mechanism
        if task.get("_human_approve"):
            task_id = create_human_approve_folder(
                task, team_root, dry_run=args.dry_run, tasks_root=str(tasks_root)
            )
            created_tasks.append(task)
        else:
            task_id = create_task_folder(
                task,
                team_root,
                dry_run=args.dry_run,
                release_version=target_version,
                requirements_path=requirements_path,
                existing_role_slugs=existing_role_slugs,
                workflow_type=workflow_type,
                tasks_root=str(tasks_root),
                recycle_map=recycle_map,
            )
            if task_id is None:
                # Option B: collision blocked this task — no folder, no queue entry.
                option_b_blocked = True
                print(
                    f"[pm-materialize] WARNING: task {task.get('task_id', '?')} skipped "
                    f"(Option B collision block) — no folder created, no queue entry written.",
                    file=sys.stderr,
                )
            else:
                # Task was created or recycled successfully — add to queue.
                created_tasks.append(task)

    # Apply the full rename map globally to fix cross-task prerequisite
    # references that still point at old (pre-recycle) task IDs.
    if recycle_map and not args.dry_run:
        print(
            f"[pm-materialize] INFO: applying rename map to cross-task prerequisite references "
            f"({len(recycle_map)} rename(s)): {list(recycle_map.items())}",
            file=sys.stderr,
        )
        apply_rename_map_globally(tasks_root, recycle_map)

    update_backlog(created_tasks, team_root, dry_run=args.dry_run)

    print(f"\n[pm-materialize] === DONE ===", file=sys.stderr)
    print(f"[pm-materialize] Tasks created: {len(created_tasks)}", file=sys.stderr)
    for i, task in enumerate(created_tasks, 1):
        print(f"[pm-materialize]   {i}. {task['task_id']}", file=sys.stderr)

    if option_b_blocked:
        # FATAL: one or more tasks blocked by active (non-terminal) collisions.
        # The marker is NOT written — it is a success receipt; a run with blocked
        # tasks is not a successful materialization.  Leaving the marker absent
        # ensures the operator can fix the collision and re-materialize without
        # first manually removing a stale marker.
        print(
            "\n[pm-materialize] FATAL: one or more tasks were blocked by active (role, slug) "
            "collisions (Option B). Materialization is incomplete. "
            "Resolve the active colliding tasks (bring them to DONE or WONT-DO) "
            "and re-materialize.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Approach A: write marker file only after a fully-successful materialization
    # so subsequent invocations with the same plan content are detected and skipped.
    # The marker is a success receipt — written here, after all task folders, queue
    # entries, and rename-map rewrites have landed, and only when no Option B block
    # occurred.  Every FATAL exit path above returns before reaching this point,
    # ensuring no partial marker is left behind.
    if not args.dry_run:
        materialized_ids = [t["task_id"] for t in created_tasks]
        marker = write_marker_file(plan_dir, plan_hash, materialized_ids)
        print(f"[pm-materialize] Marker file written: {marker}", file=sys.stderr)


if __name__ == "__main__":
    main()
