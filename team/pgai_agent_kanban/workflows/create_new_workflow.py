"""
create_new_workflow.py — scaffold a new workflow-type plugin directory.

Library function
----------------
    create_new_workflow(
        name,
        *,
        description,
        version_semantics,
        git_mode,
        agents,
        finalize,
        force=False,
        workflows_dir=None,
    )

    Creates ``<workflows_dir>/<name>/`` containing:

    - ``workflow.cfg``   — manifest with ``status = scaffold`` and the
                           capabilities supplied by the caller.
    - ``workflow.sh``    — hook stubs for every standard ``wf_*`` entry point;
                           each stub exits non-zero and prints
                           ``NOT IMPLEMENTED: <hook>`` to stderr.
    - ``contract_check.sh`` — self-contained script that verifies the manifest
                               and hook presence; operators run this before
                               flipping ``status = ready``.

    Refusals (raises WorkflowGeneratorError):
    - ``name`` is one of the shipped types: release, document, testing-only.
    - Target directory already exists and ``force=False``.

    When ``force=True`` and the directory exists, it is removed and recreated
    from scratch (full overwrite).

Output-root resolution (``workflows_dir`` priority order)
----------------------------------------------------------
    1. Explicit ``workflows_dir`` argument (or ``--workflows-dir`` on the CLI).
    2. ``$PGAI_AGENT_KANBAN_ROOT_PATH/workflows/`` — the live-operator default
       when the env var is set and no explicit path was given.
    3. ``team/workflows/`` relative to the git repository root — used when the
       process is running inside the dev tree (git root detectable) and the
       env var is not set.

    If none of the above resolves to a usable path, WorkflowGeneratorError
    is raised with a message explaining what to set.

CLI entry point
---------------
    python3 -m team.pgai_agent_kanban.workflows.create_new_workflow \\
        --name <name> [options]

    All options mirror the library function arguments.  There are no
    interactive prompts — argparse only.

Shipped-type refusal
--------------------
    The generator refuses to scaffold over names that ship with the
    framework (``release``, ``document``, ``testing-only``) to prevent
    accidental overwrite on upgrade.  Operators should use org-prefixed
    names (e.g. ``acme-deploy``) to avoid the restriction.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import subprocess
import sys
import textwrap
from typing import Optional


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class WorkflowGeneratorError(ValueError):
    """Raised when the generator refuses to proceed.

    Callers (both the library and the CLI) catch this and surface the
    message to the operator without a traceback.
    """


# ---------------------------------------------------------------------------
# Shipped-type refusal list
# ---------------------------------------------------------------------------

#: Names that ship with the framework.  These are the only names the generator
#: refuses; all other names are accepted including names that contain hyphens
#: or underscores.
SHIPPED_WORKFLOW_TYPES: frozenset[str] = frozenset(
    {"release", "document", "testing-only"}
)


# ---------------------------------------------------------------------------
# Manifest and hook-stub templates
# ---------------------------------------------------------------------------

_WORKFLOW_CFG_TEMPLATE = """\
[workflow]
name = {name}
description = {description}
status = scaffold

[capabilities]
version_semantics = {version_semantics}
git_mode = {git_mode}
finalize = {finalize}
agents = {agents}
"""

# Each wf_* hook stub exits non-zero and prints "NOT IMPLEMENTED: <hook>" so
# that an operator who drops an incomplete plugin into a live install gets a
# loud failure rather than silent wrong behavior.
_HOOK_NAMES = (
    "wf_git_mode",
    "wf_resolve_target_version",
    "wf_pre_task",
    "wf_post_task",
    "wf_finalize",
    "wf_agents",
    "wf_bundle_source_branch",
    "wf_dashboard_render",
)

_WORKFLOW_SH_HEADER = """\
#!/usr/bin/env bash
# workflows/{name}/workflow.sh
#
# Workflow plugin for '{name}'.
#
# STATUS: SCAFFOLD — all hooks below are stubs that exit non-zero.
# Implement each wf_* function and set status = ready in workflow.cfg
# before using this workflow type with the engine.
#
# Run ./contract_check.sh after implementing hooks to verify the plugin
# is structurally complete before flipping status = ready.
"""


def _make_hook_stub(hook_name: str) -> str:
    """Return a bash function stub for one wf_* hook."""
    return textwrap.dedent(
        f"""\
        # ---------------------------------------------------------------------------
        # {hook_name}
        # ---------------------------------------------------------------------------
        {hook_name}() {{
            echo "NOT IMPLEMENTED: {hook_name}" >&2
            exit 1
        }}
        """
    )


def _render_workflow_sh(name: str) -> str:
    """Return the full content of workflow.sh for a newly scaffolded plugin."""
    parts = [_WORKFLOW_SH_HEADER.format(name=name)]
    parts.append("")
    for hook in _HOOK_NAMES:
        parts.append(_make_hook_stub(hook))
    return "\n".join(parts)


_CONTRACT_CHECK_SH_TEMPLATE = """\
#!/usr/bin/env bash
# workflows/{name}/contract_check.sh
#
# Self-contained contract check for the '{name}' workflow plugin.
#
# Verifies that workflow.cfg is structurally complete (required sections and
# keys present, status value recognised) and that all standard hook functions
# are defined in workflow.sh.
#
# Run this script from the plugin directory before flipping status = ready.
# A non-zero exit means the plugin is not ready for the engine.
#
# Usage:
#   ./contract_check.sh
#   bash contract_check.sh
#
# Exit codes:
#   0 — plugin passes all contract checks
#   1 — one or more checks failed (error messages written to stderr)

set -euo pipefail

PLUGIN_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
CFG="$PLUGIN_DIR/workflow.cfg"
HOOK_FILE="$PLUGIN_DIR/workflow.sh"
ERRORS=0

# ---------------------------------------------------------------------------
# Check: workflow.cfg exists
# ---------------------------------------------------------------------------
if [[ ! -f "$CFG" ]]; then
    echo "ERROR: workflow.cfg not found at $CFG" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Check: required manifest keys
# ---------------------------------------------------------------------------
for key in name description status version_semantics git_mode finalize agents; do
    if ! grep -q "^$key" "$CFG"; then
        echo "ERROR: workflow.cfg is missing required key: $key" >&2
        ERRORS=$(( ERRORS + 1 ))
    fi
done

# ---------------------------------------------------------------------------
# Check: status value is not 'scaffold'
# ---------------------------------------------------------------------------
if grep -q "^status *= *scaffold" "$CFG"; then
    echo "ERROR: status = scaffold — flip to 'ready' after implementing hooks" >&2
    ERRORS=$(( ERRORS + 1 ))
fi

# ---------------------------------------------------------------------------
# Check: workflow.sh exists
# ---------------------------------------------------------------------------
if [[ ! -f "$HOOK_FILE" ]]; then
    echo "ERROR: workflow.sh not found at $HOOK_FILE" >&2
    ERRORS=$(( ERRORS + 1 ))
fi

# ---------------------------------------------------------------------------
# Check: all required hook functions are defined
# ---------------------------------------------------------------------------
REQUIRED_HOOKS=(
    wf_git_mode
    wf_resolve_target_version
    wf_pre_task
    wf_post_task
    wf_finalize
    wf_agents
    wf_bundle_source_branch
    wf_dashboard_render
)

for hook in "${{REQUIRED_HOOKS[@]}}"; do
    if ! grep -q "^$hook()" "$HOOK_FILE"; then
        echo "ERROR: workflow.sh is missing hook: $hook" >&2
        ERRORS=$(( ERRORS + 1 ))
    fi
done

# ---------------------------------------------------------------------------
# Check: no stubs remaining (NOT IMPLEMENTED sentinels)
# ---------------------------------------------------------------------------
if grep -q "NOT IMPLEMENTED" "$HOOK_FILE"; then
    echo "ERROR: workflow.sh contains NOT IMPLEMENTED stubs — implement all hooks" >&2
    ERRORS=$(( ERRORS + 1 ))
fi

# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------
if [[ "$ERRORS" -eq 0 ]]; then
    echo "OK: {name} plugin passes contract checks"
    exit 0
else
    echo "FAIL: $ERRORS contract check error(s) — see above" >&2
    exit 1
fi
"""


def _render_contract_check_sh(name: str) -> str:
    """Return the content of contract_check.sh for a newly scaffolded plugin."""
    return _CONTRACT_CHECK_SH_TEMPLATE.format(name=name)


# ---------------------------------------------------------------------------
# Output-root resolution
# ---------------------------------------------------------------------------


def _detect_git_root() -> Optional[pathlib.Path]:
    """Return the git repository root from the current working directory.

    Returns None if the CWD is not inside a git repository, or if git is
    not available.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return pathlib.Path(result.stdout.strip())
    except FileNotFoundError:
        # git not on PATH
        pass
    return None


def _resolve_workflows_dir(
    workflows_dir: Optional[pathlib.Path],
) -> pathlib.Path:
    """Resolve the output workflows root directory.

    Priority order (first match wins):
    1. Explicit ``workflows_dir`` argument.
    2. ``$PGAI_AGENT_KANBAN_ROOT_PATH/workflows/`` when the env var is set.
    3. ``<git-root>/team/workflows/`` when running inside the dev tree.

    Raises WorkflowGeneratorError if none of the above resolves.
    """
    # 1. Explicit argument wins unconditionally.
    if workflows_dir is not None:
        return pathlib.Path(workflows_dir)

    # 2. Live-operator default via the canonical resolver when the env var is set.
    try:
        from pgai_agent_kanban.env import resolve_kanban_root
        return resolve_kanban_root() / "workflows"
    except RuntimeError:
        pass  # Env var unset — fall through to the dev-tree fallback below.

    # 3. Dev-tree fallback: team/workflows/ relative to the git root.
    git_root = _detect_git_root()
    if git_root is not None:
        dev_workflows = git_root / "team" / "workflows"
        if dev_workflows.is_dir():
            return dev_workflows

    raise WorkflowGeneratorError(
        "Cannot determine the workflows output directory.\n"
        "Provide one of:\n"
        "  - --workflows-dir <path>  (explicit path)\n"
        "  - Set PGAI_AGENT_KANBAN_ROOT_PATH (live-install default)\n"
        "  - Run from inside the dev tree (team/workflows/ must be present)"
    )


# ---------------------------------------------------------------------------
# Core generator
# ---------------------------------------------------------------------------


def create_new_workflow(
    name: str,
    *,
    description: str,
    version_semantics: str,
    git_mode: str,
    agents: str,
    finalize: str,
    force: bool = False,
    workflows_dir: Optional[pathlib.Path] = None,
) -> pathlib.Path:
    """Scaffold a new workflow-type plugin directory.

    Parameters
    ----------
    name:
        The workflow type name.  Must not be one of the shipped types
        (``release``, ``document``, ``testing-only``).  Use an org-prefixed
        name (e.g. ``acme-deploy``) to avoid naming collisions on upgrade.
    description:
        Human-readable description written into the manifest.
    version_semantics:
        Capability value: ``semver``, ``label``, or ``none``.
    git_mode:
        Capability value: ``none``, ``ro``, or ``rw``.
    agents:
        Comma-separated ordered agent roster for PM decomposition.
        Example: ``pm,coder,tester``.
    finalize:
        Capability value: ``tag``, ``publish``, or ``report``.
    force:
        When ``True``, remove and recreate an existing plugin directory.
        When ``False`` (default), raise WorkflowGeneratorError if the
        directory already exists.
    workflows_dir:
        Explicit path to the workflows root directory.  When ``None``,
        the output root is resolved by ``_resolve_workflows_dir()`` using
        the priority order documented in that function.

    Returns
    -------
    pathlib.Path
        The absolute path of the newly created plugin directory.

    Raises
    ------
    WorkflowGeneratorError
        - When ``name`` is one of the shipped workflow types.
        - When the target directory exists and ``force=False``.
        - When the workflows root directory cannot be determined.
    OSError
        Propagated from filesystem operations if the directory cannot be
        created or files cannot be written.
    """
    # Guard: refuse shipped type names.
    if name in SHIPPED_WORKFLOW_TYPES:
        raise WorkflowGeneratorError(
            f"Cannot scaffold a workflow named {name!r}: that name is "
            f"reserved for a shipped workflow type "
            f"({', '.join(sorted(SHIPPED_WORKFLOW_TYPES))}).  "
            "Use an org-prefixed name (e.g. 'acme-deploy') to avoid "
            "naming collisions on upgrade."
        )

    # Resolve output root.
    root = _resolve_workflows_dir(workflows_dir)

    # Compute target directory.
    target = root / name

    # Guard: refuse existing directory unless force.
    if target.exists():
        if not force:
            raise WorkflowGeneratorError(
                f"Workflow directory already exists: {target}\n"
                "Pass --force (or force=True) to overwrite the existing scaffold."
            )
        shutil.rmtree(target)

    # Create the plugin directory.
    target.mkdir(parents=True, exist_ok=False)

    # Write workflow.cfg.
    cfg_content = _WORKFLOW_CFG_TEMPLATE.format(
        name=name,
        description=description,
        version_semantics=version_semantics,
        git_mode=git_mode,
        finalize=finalize,
        agents=agents,
    )
    (target / "workflow.cfg").write_text(cfg_content, encoding="utf-8")

    # Write workflow.sh (hook stubs).
    workflow_sh_content = _render_workflow_sh(name)
    workflow_sh_path = target / "workflow.sh"
    workflow_sh_path.write_text(workflow_sh_content, encoding="utf-8")
    workflow_sh_path.chmod(0o755)

    # Write contract_check.sh.
    contract_sh_content = _render_contract_check_sh(name)
    contract_sh_path = target / "contract_check.sh"
    contract_sh_path.write_text(contract_sh_content, encoding="utf-8")
    contract_sh_path.chmod(0o755)

    return target


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for the CLI wrapper."""
    parser = argparse.ArgumentParser(
        prog="python3 -m team.pgai_agent_kanban.workflows.create_new_workflow",
        description=(
            "Scaffold a new workflow-type plugin directory.\n\n"
            "Creates <workflows-dir>/<name>/ containing workflow.cfg "
            "(status=scaffold), workflow.sh (stub hooks that exit non-zero "
            "with NOT IMPLEMENTED), and contract_check.sh.\n\n"
            "Output root resolution: --workflows-dir > "
            "$PGAI_AGENT_KANBAN_ROOT_PATH/workflows/ > team/workflows/ "
            "when running inside the dev tree."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--name",
        required=True,
        metavar="NAME",
        help=(
            "Workflow type name.  Must not be one of the shipped types: "
            + ", ".join(sorted(SHIPPED_WORKFLOW_TYPES))
            + ".  Use an org-prefixed name (e.g. acme-deploy)."
        ),
    )
    parser.add_argument(
        "--description",
        default="(no description provided)",
        metavar="TEXT",
        help="Human-readable description for the workflow manifest.",
    )
    parser.add_argument(
        "--version-semantics",
        default="semver",
        choices=["semver", "label", "none"],
        metavar="{semver,label,none}",
        dest="version_semantics",
        help="Version semantics capability value (default: semver).",
    )
    parser.add_argument(
        "--git-mode",
        default="none",
        choices=["none", "ro", "rw"],
        metavar="{none,ro,rw}",
        dest="git_mode",
        help="Git access mode capability value (default: none).",
    )
    parser.add_argument(
        "--agents",
        default="pm,coder,writer,tester,cm",
        metavar="ROSTER",
        help=(
            "Comma-separated ordered agent roster for PM decomposition "
            "(default: pm,coder,writer,tester,cm)."
        ),
    )
    parser.add_argument(
        "--finalize",
        default="tag",
        choices=["tag", "publish", "report"],
        metavar="{tag,publish,report}",
        help="Finalize mode capability value (default: tag).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help=(
            "Overwrite an existing plugin directory.  Without this flag, "
            "the command exits non-zero when the target directory exists."
        ),
    )
    parser.add_argument(
        "--workflows-dir",
        default=None,
        metavar="PATH",
        dest="workflows_dir",
        help=(
            "Explicit path to the workflows root directory.  "
            "When not set, the root is resolved from "
            "$PGAI_AGENT_KANBAN_ROOT_PATH/workflows/ or team/workflows/ "
            "in the dev tree."
        ),
    )
    return parser


def _cli_main(argv: Optional[list[str]] = None) -> int:
    """Argparse entry point.  Returns an exit code (0 = success, 1 = error)."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    workflows_dir_path: Optional[pathlib.Path] = (
        pathlib.Path(args.workflows_dir) if args.workflows_dir else None
    )

    try:
        target = create_new_workflow(
            args.name,
            description=args.description,
            version_semantics=args.version_semantics,
            git_mode=args.git_mode,
            agents=args.agents,
            finalize=args.finalize,
            force=args.force,
            workflows_dir=workflows_dir_path,
        )
    except WorkflowGeneratorError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"error: filesystem operation failed: {exc}", file=sys.stderr)
        return 1

    print(f"Scaffolded workflow plugin: {target}")
    print(f"  workflow.cfg       — manifest (status = scaffold)")
    print(f"  workflow.sh        — hook stubs (NOT IMPLEMENTED)")
    print(f"  contract_check.sh  — run after implementing hooks")
    print(f"Next: implement hooks in {target}/workflow.sh, then run:")
    print(f"  bash {target}/contract_check.sh")
    print(f"When contract_check.sh passes, flip status = ready in workflow.cfg.")
    return 0


if __name__ == "__main__":
    sys.exit(_cli_main())
