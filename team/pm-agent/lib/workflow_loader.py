"""lib/workflow_loader.py — Load and validate workflow YAML definitions.

Workflow YAML files define a complete pipeline: what inputs a workflow expects,
which agents participate, what steps run in order, and what output is produced.

Each workflow type is a plugin directory under ``team/workflows/<type>/``
containing at minimum ``workflow.cfg`` and ``workflow.sh``.  A type that needs
PM decomposition richness beyond the simple wf_agents roster carries an
optional ``pipeline.yaml`` inside that directory.  Types without a
``pipeline.yaml`` use the simple path (roster from ``wf_agents``) — the same
behavior ``testing-only`` runs, now the documented default for custom types.

File search order for load_workflow (first match wins):
  1. $KANBAN_ROOT/workflows/<name>.yaml              (project-local flat override)
  2. $KANBAN_ROOT/team/workflows/<name>/pipeline.yaml  (plugin directory — canonical)
  3. $KANBAN_ROOT/team/workflows/<name>.yaml          (legacy flat path — backward compat)

Public API
----------
    load_workflow(workflow_name, kanban_root=None) -> WorkflowDefinition
    list_workflows(kanban_root=None) -> list[str]

Raises WorkflowError with a descriptive message on any validation failure.
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


# ---------------------------------------------------------------------------
# Constants: valid values for enum-like fields
# ---------------------------------------------------------------------------

VALID_ROLES = {"PM", "CODER", "WRITER", "TESTER", "CM", "PO", "any"}

VALID_VERSIONING = {"auto-increment", "from_requirements", "none"}

VALID_OPERATIONS = {"create_branch", "tag_and_push", "open_doc", "finalize"}

VALID_AUTONOMOUS_CRITERION = {"required", "optional", "none"}

VALID_WHEN_PREDICATES = {"foreach_was_used", "review_agent_configured"}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class InputsSpec:
    """Specification of workflow inputs."""
    required: list  # list[str]
    optional: list  # list[str]
    context: list   # list[str]


@dataclass
class PipelineStep:
    """One step in the workflow pipeline."""
    role: str
    name: str
    operation: Optional[str] = None
    foreach: Optional[str] = None
    deliverable: Optional[str] = None
    inputs: list = field(default_factory=list)  # list[str]
    optional: bool = False
    branch: Optional[str] = None
    branch_pattern: Optional[str] = None
    target_branch: Optional[str] = None
    autonomous_criterion: Optional[str] = None
    when: Optional[str] = None


@dataclass
class OutputsSpec:
    """Specification of workflow outputs."""
    format: object  # str or list[str]
    location: str


@dataclass
class WorkflowDefinition:
    """A fully validated workflow definition loaded from YAML."""
    name: str
    description: str
    inputs: InputsSpec
    agents: dict  # dict[str, str] — purpose -> role
    pipeline: list  # list[PipelineStep]
    outputs: OutputsSpec
    versioning: str  # auto-increment | from_requirements | none


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class WorkflowError(Exception):
    """Raised when a workflow YAML is missing, malformed, or invalid."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _kanban_root(kanban_root=None) -> Path:
    """Resolve the kanban root path from argument or environment.

    Precedence: explicit argument > PGAI_AGENT_KANBAN_ROOT_PATH (canonical) >
    ~/pgai_agent_kanban.
    """
    if kanban_root is not None:
        return Path(kanban_root)
    return Path(
        os.environ.get("PGAI_AGENT_KANBAN_ROOT_PATH")
        or str(Path.home() / "pgai_agent_kanban")
    )


def _find_workflow_file(name: str, root: Path) -> tuple:
    """Return (path, expected_name) for the workflow YAML file.

    Search order (first match wins):
      1. $KANBAN_ROOT/workflows/<name>.yaml          (project-local flat override)
      2. $KANBAN_ROOT/team/workflows/<name>/pipeline.yaml  (plugin directory — canonical)
      3. $KANBAN_ROOT/team/workflows/<name>.yaml     (legacy flat path — backward compat)

    The second return value is the workflow name the caller should validate the
    YAML ``name`` field against.  For the plugin-directory form the directory
    name is the workflow type, not the filename stem (``pipeline``), so the
    expected name is explicitly set to ``name`` (the type string).

    Raises WorkflowError if no candidate exists.
    """
    # Candidate list: each entry is (path, expected_name_in_yaml).
    # expected_name is None when it equals the argument `name` (covers flat forms).
    candidates = [
        (root / "workflows" / f"{name}.yaml", name),
        (root / "team" / "workflows" / name / "pipeline.yaml", name),
        (root / "team" / "workflows" / f"{name}.yaml", name),
    ]
    for path, expected_name in candidates:
        if path.is_file():
            return path, expected_name
    searched = ", ".join(str(p) for p, _ in candidates)
    raise WorkflowError(
        f"Workflow '{name}' not found. Searched: {searched}"
    )


def _load_yaml(path: Path) -> dict:
    """Load and parse a YAML file. Raises WorkflowError on parse failure."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise WorkflowError(f"Cannot read workflow file '{path}': {exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise WorkflowError(f"YAML parse error in '{path}': {exc}") from exc
    if not isinstance(data, dict):
        raise WorkflowError(
            f"Workflow file '{path}' must be a YAML mapping at top level"
        )
    return data


def _require_str(data: dict, key: str, context: str) -> str:
    """Return data[key] as a non-empty string, raising WorkflowError if missing or wrong type."""
    if key not in data:
        raise WorkflowError(f"Missing required field '{key}' in {context}")
    val = data[key]
    if not isinstance(val, str):
        raise WorkflowError(
            f"Field '{key}' in {context} must be a string, got {type(val).__name__}"
        )
    val = val.strip()
    if not val:
        raise WorkflowError(f"Field '{key}' in {context} must not be empty")
    return val


def _require_list(data: dict, key: str, context: str) -> list:
    """Return data[key] as a list, raising WorkflowError if missing or wrong type."""
    if key not in data:
        raise WorkflowError(f"Missing required field '{key}' in {context}")
    val = data[key]
    if not isinstance(val, list):
        raise WorkflowError(
            f"Field '{key}' in {context} must be a list, got {type(val).__name__}"
        )
    return val


def _coerce_str_list(val, key: str, context: str) -> list:
    """Coerce a value to a list of strings. Accepts None (-> []), str (-> [str]), or list."""
    if val is None:
        return []
    if isinstance(val, str):
        return [val]
    if isinstance(val, list):
        # Validate all elements are strings
        for i, item in enumerate(val):
            if not isinstance(item, str):
                raise WorkflowError(
                    f"Item {i} in '{key}' ({context}) must be a string, "
                    f"got {type(item).__name__}"
                )
        return val
    raise WorkflowError(
        f"Field '{key}' in {context} must be a string or list, got {type(val).__name__}"
    )


# Liberal field alias: accept both 'output' and 'outputs'
_OUTPUTS_ALIASES = re.compile(r'^outputs?$', re.IGNORECASE)


def _find_outputs_key(data: dict) -> Optional[str]:
    """Find the outputs key, accepting 'output' or 'outputs' (liberal parsing)."""
    for k in data:
        if _OUTPUTS_ALIASES.match(str(k)):
            return k
    return None


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _parse_inputs(data: dict, context: str) -> InputsSpec:
    """Parse and validate the 'inputs' block."""
    raw = data.get("inputs")
    if raw is None:
        raise WorkflowError(f"Missing required field 'inputs' in {context}")
    if not isinstance(raw, dict):
        raise WorkflowError(
            f"Field 'inputs' in {context} must be a mapping, got {type(raw).__name__}"
        )
    required = _coerce_str_list(raw.get("required"), "inputs.required", context)
    optional = _coerce_str_list(raw.get("optional"), "inputs.optional", context)
    ctx_list = _coerce_str_list(raw.get("context"), "inputs.context", context)
    return InputsSpec(required=required, optional=optional, context=ctx_list)


def _parse_agents(data: dict, context: str) -> dict:
    """Parse and validate the 'agents' block."""
    raw = data.get("agents")
    if raw is None:
        raise WorkflowError(f"Missing required field 'agents' in {context}")
    if not isinstance(raw, dict):
        raise WorkflowError(
            f"Field 'agents' in {context} must be a mapping, got {type(raw).__name__}"
        )
    if not raw:
        raise WorkflowError(f"Field 'agents' in {context} must not be empty")
    agents = {}
    for purpose, role in raw.items():
        if not isinstance(role, str):
            raise WorkflowError(
                f"Agent role for '{purpose}' in {context} must be a string, "
                f"got {type(role).__name__}"
            )
        role = role.strip()
        if role not in VALID_ROLES:
            raise WorkflowError(
                f"Unknown role '{role}' for agent '{purpose}' in {context}. "
                f"Valid roles: {', '.join(sorted(VALID_ROLES))}"
            )
        agents[str(purpose)] = role
    return agents


def _parse_pipeline_step(raw: dict, step_index: int, context: str) -> PipelineStep:
    """Parse and validate one pipeline step."""
    if not isinstance(raw, dict):
        raise WorkflowError(
            f"Pipeline step {step_index} in {context} must be a mapping, "
            f"got {type(raw).__name__}"
        )
    step_ctx = f"pipeline step {step_index} of {context}"

    role = _require_str(raw, "role", step_ctx)
    if role not in VALID_ROLES:
        name_hint = raw.get("name", f"(step {step_index})")
        raise WorkflowError(
            f"Unknown role '{role}' in step '{name_hint}'. "
            f"Valid roles: {', '.join(sorted(VALID_ROLES))}"
        )

    name = _require_str(raw, "name", step_ctx)

    operation = raw.get("operation")
    if operation is not None:
        if not isinstance(operation, str):
            raise WorkflowError(
                f"Field 'operation' in step '{name}' must be a string"
            )
        operation = operation.strip()
        if operation not in VALID_OPERATIONS:
            raise WorkflowError(
                f"Unknown operation '{operation}' in step '{name}'. "
                f"Valid operations: {', '.join(sorted(VALID_OPERATIONS))}"
            )
        # Operation-specific required fields
        if operation == "create_branch":
            if not raw.get("branch_pattern"):
                raise WorkflowError(
                    f"Step '{name}' uses create_branch but missing branch_pattern"
                )
        elif operation == "tag_and_push":
            if not raw.get("target_branch"):
                raise WorkflowError(
                    f"Step '{name}' uses tag_and_push but missing target_branch"
                )

    foreach = raw.get("foreach")
    if foreach is not None and not isinstance(foreach, str):
        raise WorkflowError(
            f"Field 'foreach' in step '{name}' must be a string"
        )

    deliverable = raw.get("deliverable")
    if deliverable is not None and not isinstance(deliverable, str):
        raise WorkflowError(
            f"Field 'deliverable' in step '{name}' must be a string"
        )

    inputs_raw = raw.get("inputs")
    step_inputs = _coerce_str_list(inputs_raw, "inputs", f"step '{name}'")

    optional_val = raw.get("optional", False)
    if not isinstance(optional_val, bool):
        raise WorkflowError(
            f"Field 'optional' in step '{name}' must be a boolean"
        )

    branch = raw.get("branch")
    if branch is not None and not isinstance(branch, str):
        raise WorkflowError(
            f"Field 'branch' in step '{name}' must be a string"
        )

    branch_pattern = raw.get("branch_pattern")
    if branch_pattern is not None and not isinstance(branch_pattern, str):
        raise WorkflowError(
            f"Field 'branch_pattern' in step '{name}' must be a string"
        )

    target_branch = raw.get("target_branch")
    if target_branch is not None and not isinstance(target_branch, str):
        raise WorkflowError(
            f"Field 'target_branch' in step '{name}' must be a string"
        )

    autonomous_criterion = raw.get("autonomous_criterion")
    if autonomous_criterion is not None:
        if not isinstance(autonomous_criterion, str):
            raise WorkflowError(
                f"Field 'autonomous_criterion' in step '{name}' must be a string"
            )
        autonomous_criterion = autonomous_criterion.strip()
        if autonomous_criterion not in VALID_AUTONOMOUS_CRITERION:
            raise WorkflowError(
                f"Unknown autonomous_criterion '{autonomous_criterion}' in step '{name}'. "
                f"Valid values: {', '.join(sorted(VALID_AUTONOMOUS_CRITERION))}"
            )

    when = raw.get("when")
    if when is not None:
        if not isinstance(when, str):
            raise WorkflowError(
                f"Field 'when' in step '{name}' must be a string"
            )
        when = when.strip()
        if when not in VALID_WHEN_PREDICATES:
            raise WorkflowError(
                f"Unknown when predicate '{when}' in step '{name}'. "
                f"Valid predicates: {', '.join(sorted(VALID_WHEN_PREDICATES))}"
            )

    return PipelineStep(
        role=role,
        name=name,
        operation=operation,
        foreach=foreach,
        deliverable=deliverable,
        inputs=step_inputs,
        optional=optional_val,
        branch=branch,
        branch_pattern=branch_pattern,
        target_branch=target_branch,
        autonomous_criterion=autonomous_criterion,
        when=when,
    )


def _parse_pipeline(data: dict, context: str) -> list:
    """Parse and validate the 'pipeline' list."""
    raw = _require_list(data, "pipeline", context)
    if not raw:
        raise WorkflowError("Pipeline must contain at least one step")
    steps = []
    for i, step_raw in enumerate(raw):
        steps.append(_parse_pipeline_step(step_raw, i, context))
    return steps


def _parse_outputs(data: dict, context: str) -> OutputsSpec:
    """Parse and validate the 'outputs' block (also accepts 'output' alias)."""
    key = _find_outputs_key(data)
    if key is None:
        raise WorkflowError(f"Missing required field 'outputs' in {context}")
    raw = data[key]
    if not isinstance(raw, dict):
        raise WorkflowError(
            f"Field 'outputs' in {context} must be a mapping, got {type(raw).__name__}"
        )
    # format: string or list
    if "format" not in raw:
        raise WorkflowError(f"Missing required field 'outputs.format' in {context}")
    fmt = raw["format"]
    if isinstance(fmt, str):
        pass  # ok as-is
    elif isinstance(fmt, list):
        for i, item in enumerate(fmt):
            if not isinstance(item, str):
                raise WorkflowError(
                    f"outputs.format[{i}] in {context} must be a string, "
                    f"got {type(item).__name__}"
                )
    else:
        raise WorkflowError(
            f"outputs.format in {context} must be a string or list, "
            f"got {type(fmt).__name__}"
        )

    location = _require_str(raw, "location", f"outputs block of {context}")

    return OutputsSpec(format=fmt, location=location)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_workflow(workflow_name: str, kanban_root=None) -> WorkflowDefinition:
    """Load a workflow definition by name.

    Resolves the pipeline file as ``workflows/<type>/pipeline.yaml`` constructed
    from ``workflow_name``, then falls back to legacy flat and project-local paths.
    Full search order (first match wins):

      1. $KANBAN_ROOT/workflows/<name>.yaml          (project-local flat override)
      2. $KANBAN_ROOT/team/workflows/<name>/pipeline.yaml  (plugin directory — canonical)
      3. $KANBAN_ROOT/team/workflows/<name>.yaml     (legacy flat path — backward compat)

    Validates structure and returns a parsed WorkflowDefinition.
    Raises WorkflowError with a descriptive message on any validation failure.

    Parameters
    ----------
    workflow_name:
        Name of the workflow (the type string). E.g. 'release', 'document'.
    kanban_root:
        Override for the kanban root directory. Defaults to
        PGAI_AGENT_KANBAN_ROOT_PATH (canonical) /
        ~/pgai_agent_kanban.

    Returns
    -------
    WorkflowDefinition
        Fully validated workflow definition.

    Raises
    ------
    WorkflowError
        If the workflow file is not found, cannot be parsed, or fails validation.
    """
    root = _kanban_root(kanban_root)
    path, expected_name = _find_workflow_file(workflow_name, root)
    data = _load_yaml(path)

    context = f"workflow '{workflow_name}' ({path})"

    # --- name ---
    name = _require_str(data, "name", context)
    # For the plugin-directory form (pipeline.yaml), the expected name is the
    # workflow type (directory name), not the file stem.  For flat forms the
    # expected name equals the file stem, which also equals workflow_name.
    if name != expected_name:
        raise WorkflowError(
            f"Workflow name '{name}' does not match filename '{expected_name}' "
            f"(from {path})"
        )

    # --- description ---
    description = _require_str(data, "description", context)

    # --- inputs ---
    inputs = _parse_inputs(data, context)

    # --- agents ---
    agents = _parse_agents(data, context)

    # --- pipeline ---
    pipeline = _parse_pipeline(data, context)

    # --- outputs ---
    outputs = _parse_outputs(data, context)

    # --- versioning ---
    versioning = _require_str(data, "versioning", context)
    if versioning not in VALID_VERSIONING:
        raise WorkflowError(
            f"Unknown versioning mode '{versioning}' in {context}. "
            f"Valid values: {', '.join(sorted(VALID_VERSIONING))}"
        )

    return WorkflowDefinition(
        name=name,
        description=description,
        inputs=inputs,
        agents=agents,
        pipeline=pipeline,
        outputs=outputs,
        versioning=versioning,
    )


def list_workflows(kanban_root=None) -> list:
    """Return names of all available workflow types.

    Collects workflow names from:
      - $KANBAN_ROOT/team/workflows/<type>/pipeline.yaml  (plugin directory — canonical)
      - $KANBAN_ROOT/team/workflows/*.yaml                (legacy flat paths)
      - $KANBAN_ROOT/workflows/*.yaml                     (project-local flat overrides)

    Returns a sorted list of unique workflow names (without .yaml extension).

    Parameters
    ----------
    kanban_root:
        Override for the kanban root directory. Defaults to
        PGAI_AGENT_KANBAN_ROOT_PATH (canonical) /
        ~/pgai_agent_kanban.

    Returns
    -------
    list[str]
        Sorted list of workflow names available (canonical names + aliases).
    """
    root = _kanban_root(kanban_root)
    names = set()

    # Flat YAML files: project-local overrides and legacy team definitions.
    flat_dirs = [
        root / "team" / "workflows",
        root / "workflows",
    ]
    for search_dir in flat_dirs:
        if not search_dir.is_dir():
            continue
        for yaml_file in search_dir.glob("*.yaml"):
            stem = yaml_file.stem
            # Skip anything that doesn't look like a workflow name.
            if stem and not stem.startswith("."):
                names.add(stem)

    # Plugin directories: each subdirectory of team/workflows/ that contains a
    # pipeline.yaml is a workflow type whose name is the directory name.
    team_wf_dir = root / "team" / "workflows"
    if team_wf_dir.is_dir():
        for subdir in team_wf_dir.iterdir():
            if subdir.is_dir() and (subdir / "pipeline.yaml").is_file():
                names.add(subdir.name)

    return sorted(names)
