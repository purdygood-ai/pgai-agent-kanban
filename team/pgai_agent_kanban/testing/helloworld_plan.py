"""
helloworld_plan.py -- the decomposition plan the hello-world flow harness
hands to pm_materialize.py.

team/scripts/harness/helloworld-flow-check.sh drives one deterministic
release through a throwaway kanban root. Its decompose stage does not run a
model: it writes a fixed plan, matching the harness's own intake document,
that asks for one CODER task shipping ``hello.py`` and ``test_hello.py``,
and passes that plan to the real materializer. This module owns that plan.

Usage:
    python3 -m pgai_agent_kanban.testing.helloworld_plan \\
        PLAN_PATH PROJECT_NAME VERSION REPO_PATH REQUIREMENTS_PATH

writes the plan to PLAN_PATH as JSON indented by two spaces, with no
trailing newline, and prints nothing. VERSION is the release version the
plan targets (for example ``v1.0.0``); REPO_PATH is the task's working
directory; REQUIREMENTS_PATH is the deposited intake document, recorded as
both the plan's requirements path and the task's one input. Exit 0 on
success; 1, with one line on stderr, when PLAN_PATH cannot be written.
``--help`` prints this text and exits 0.

Public API:
    build_plan(project_name, version, repo_path, requirements_path) -> dict
    write_plan(plan_path, project_name, version, repo_path, requirements_path) -> None
    main(argv=None) -> int
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

__all__ = ["build_plan", "main", "write_plan"]


def build_plan(project_name: str, version: str, repo_path: str, requirements_path: str) -> dict:
    """Return the hello-world plan for *project_name* at *version*.

    The plan is a release-workflow plan with tests required and automatic
    approval, naming *requirements_path* as its requirements document. It
    holds one task, sequence 1, slug ``hello-world-script``, role CODER,
    working in *repo_path* with no git repository or source branch, whose
    single input is *requirements_path* and which depends on nothing. Every
    other field -- goal, required output, constraints, acceptance criteria,
    notes -- is fixed text that matches the harness's intake document.
    """
    return {
        "project_name": project_name,
        "workflow_type": "release",
        "test_required": "true",
        "target_version": version,
        "human_approval_required": "auto",
        "requirements_path": requirements_path,
        "tasks": [
            {
                "sequence": 1,
                "slug": "hello-world-script",
                "title": "Implement hello.py and test_hello.py",
                "role": "CODER",
                "working_directory": repo_path,
                "git_repo": "none",
                "source_branch": "none",
                "goal": (
                    "Ship hello.py (prints exactly 'Hello, World!') and "
                    "test_hello.py (asserts stdout equality) per the intake doc."
                ),
                "inputs": [requirements_path],
                "context_paths": [],
                "required_output": "hello.py and test_hello.py in the repo root.",
                "constraints": [
                    "No argparse, logging, config files, or dependencies beyond "
                    "the standard library.",
                ],
                "acceptance_criteria": [
                    "python3 hello.py outputs exactly 'Hello, World!'",
                    "python3 -m pytest test_hello.py -v exits 0",
                    "test asserts stdout equality to 'Hello, World!\\n', not a substring",
                    "python3 -m py_compile hello.py compiles clean",
                ],
                "depends_on": [],
                "notes": "Deterministic content fixed by the intake doc; see harness header.",
            }
        ],
    }


def write_plan(
    plan_path: str,
    project_name: str,
    version: str,
    repo_path: str,
    requirements_path: str,
) -> None:
    """Write :func:`build_plan`'s plan to *plan_path*, replacing any file there.

    The JSON is indented by two spaces, keeps the plan's own key order, and
    has no trailing newline. An ``OSError`` from opening or writing
    *plan_path* propagates unchanged.
    """
    plan = build_plan(project_name, version, repo_path, requirements_path)
    with open(plan_path, "w", encoding="utf-8") as fh:
        json.dump(plan, fh, indent=2)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.testing.helloworld_plan",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("plan_path", help="Where the plan JSON is written.")
    parser.add_argument("project_name", help="The project the plan decomposes for.")
    parser.add_argument("version", help="The target release version, e.g. v1.0.0.")
    parser.add_argument("repo_path", help="The task's working directory.")
    parser.add_argument("requirements_path", help="The deposited intake document.")
    return parser


def main(argv: Optional[list] = None) -> int:
    """Command-line entry point; see the module docstring."""
    args = _build_parser().parse_args(argv)
    try:
        write_plan(
            args.plan_path,
            args.project_name,
            args.version,
            args.repo_path,
            args.requirements_path,
        )
    except OSError as exc:
        print(f"helloworld_plan: cannot write {args.plan_path}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
