"""
test_workflow_loader.py
=======================
Behavioral unit tests for team/pm-agent/lib/workflow_loader.py.

Tests use real YAML files written to tmp_path rather than mocking the YAML
parser — the parser's interaction with the file layout is itself behavior worth
testing.  No live filesystem or kanban tree is needed: all paths are under
tmp_path.

load_workflow() is the primary public surface; list_workflows() and internal
validation helpers are exercised through it.
"""

from __future__ import annotations

import pathlib
import textwrap

import pytest

try:
    from pm_agent.lib import workflow_loader
    from pm_agent.lib.workflow_loader import (
        load_workflow,
        list_workflows,
        WorkflowError,
        WorkflowDefinition,
        VALID_ROLES,
    )
except ImportError:
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent / "pm-agent"))
    from lib import workflow_loader  # type: ignore[no-redef]
    from lib.workflow_loader import (  # type: ignore[no-redef]
        load_workflow,
        list_workflows,
        WorkflowError,
        WorkflowDefinition,
        VALID_ROLES,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MINIMAL_YAML = textwrap.dedent("""\
    name: {name}
    description: A test workflow.

    inputs:
      required:
        - brief.md
      optional: []
      context: []

    agents:
      author: WRITER

    pipeline:
      - role: WRITER
        name: draft

    outputs:
      format: markdown
      location: output/

    versioning: auto-increment
""")

_RELEASE_YAML = textwrap.dedent("""\
    name: release
    description: Standard release workflow.

    inputs:
      required:
        - requirements.md
      context:
        - dev_tree_path

    agents:
      primary: CODER
      review: TESTER
      manage: CM

    pipeline:
      - role: CM
        name: open-rc
        operation: create_branch
        branch_pattern: rc/{version}

      - role: CODER
        name: implement
        foreach: requirements.tickets
        branch: feature/{task_id}

      - role: CM
        name: ship
        operation: tag_and_push
        target_branch: main

    outputs:
      format: git_tag
      location: refs/tags/

    versioning: from_requirements
""")


def _write_workflow(root: pathlib.Path, name: str, content: str) -> None:
    """Write workflow YAML to $root/team/workflows/<name>.yaml."""
    wf_dir = root / "team" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    (wf_dir / f"{name}.yaml").write_text(content, encoding="utf-8")


def _write_project_workflow(root: pathlib.Path, name: str, content: str) -> None:
    """Write workflow YAML to $root/workflows/<name>.yaml (project-local override)."""
    wf_dir = root / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    (wf_dir / f"{name}.yaml").write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# load_workflow() — happy path
# ---------------------------------------------------------------------------


def test_load_workflow_returns_workflow_definition(tmp_path: pathlib.Path) -> None:
    """load_workflow returns a WorkflowDefinition for a valid YAML file."""
    _write_workflow(tmp_path, "test", _MINIMAL_YAML.format(name="test"))
    result = load_workflow("test", kanban_root=tmp_path)
    assert isinstance(result, WorkflowDefinition)


def test_load_workflow_populates_name_from_yaml(tmp_path: pathlib.Path) -> None:
    """load_workflow sets WorkflowDefinition.name from the YAML 'name' field."""
    _write_workflow(tmp_path, "test", _MINIMAL_YAML.format(name="test"))
    result = load_workflow("test", kanban_root=tmp_path)
    assert result.name == "test"


def test_load_workflow_populates_description(tmp_path: pathlib.Path) -> None:
    """load_workflow sets description from the YAML 'description' field."""
    _write_workflow(tmp_path, "test", _MINIMAL_YAML.format(name="test"))
    result = load_workflow("test", kanban_root=tmp_path)
    assert result.description == "A test workflow."


def test_load_workflow_populates_inputs_required(tmp_path: pathlib.Path) -> None:
    """load_workflow parses the inputs.required list correctly."""
    _write_workflow(tmp_path, "test", _MINIMAL_YAML.format(name="test"))
    result = load_workflow("test", kanban_root=tmp_path)
    assert "brief.md" in result.inputs.required


def test_load_workflow_populates_agents(tmp_path: pathlib.Path) -> None:
    """load_workflow parses the agents mapping correctly."""
    _write_workflow(tmp_path, "test", _MINIMAL_YAML.format(name="test"))
    result = load_workflow("test", kanban_root=tmp_path)
    assert result.agents == {"author": "WRITER"}


def test_load_workflow_populates_pipeline_steps(tmp_path: pathlib.Path) -> None:
    """load_workflow parses the pipeline list into PipelineStep objects."""
    _write_workflow(tmp_path, "test", _MINIMAL_YAML.format(name="test"))
    result = load_workflow("test", kanban_root=tmp_path)
    assert len(result.pipeline) == 1
    assert result.pipeline[0].role == "WRITER"
    assert result.pipeline[0].name == "draft"


def test_load_workflow_populates_versioning(tmp_path: pathlib.Path) -> None:
    """load_workflow parses the versioning field."""
    _write_workflow(tmp_path, "test", _MINIMAL_YAML.format(name="test"))
    result = load_workflow("test", kanban_root=tmp_path)
    assert result.versioning == "auto-increment"


def test_load_workflow_outputs_alias_accepted(tmp_path: pathlib.Path) -> None:
    """load_workflow accepts 'output' as an alias for 'outputs' in YAML."""
    yaml_with_output_alias = _MINIMAL_YAML.format(name="test").replace(
        "outputs:", "output:"
    )
    _write_workflow(tmp_path, "test", yaml_with_output_alias)
    result = load_workflow("test", kanban_root=tmp_path)
    assert result.outputs is not None


def test_load_workflow_release_yaml_has_cm_pipeline_steps(tmp_path: pathlib.Path) -> None:
    """load_workflow parses a release workflow with CM operation steps."""
    _write_workflow(tmp_path, "release", _RELEASE_YAML)
    result = load_workflow("release", kanban_root=tmp_path)
    roles = [s.role for s in result.pipeline]
    assert "CM" in roles
    assert "CODER" in roles


def test_load_workflow_create_branch_step_has_branch_pattern(tmp_path: pathlib.Path) -> None:
    """load_workflow parses the branch_pattern field for create_branch operations."""
    _write_workflow(tmp_path, "release", _RELEASE_YAML)
    result = load_workflow("release", kanban_root=tmp_path)
    open_rc_step = next(s for s in result.pipeline if s.operation == "create_branch")
    assert open_rc_step.branch_pattern == "rc/{version}"


def test_load_workflow_tag_and_push_step_has_target_branch(tmp_path: pathlib.Path) -> None:
    """load_workflow parses the target_branch field for tag_and_push operations."""
    _write_workflow(tmp_path, "release", _RELEASE_YAML)
    result = load_workflow("release", kanban_root=tmp_path)
    ship_step = next(s for s in result.pipeline if s.operation == "tag_and_push")
    assert ship_step.target_branch == "main"


# ---------------------------------------------------------------------------
# load_workflow() — unknown name raises error (prose alias removed)
# ---------------------------------------------------------------------------


def test_load_workflow_raises_for_prose_name(tmp_path: pathlib.Path) -> None:
    """load_workflow raises WorkflowError for 'prose' — the alias was removed; use 'document'."""
    # document.yaml exists, but 'prose' is no longer a valid workflow name.
    _write_workflow(tmp_path, "document", _MINIMAL_YAML.format(name="document"))
    with pytest.raises(WorkflowError, match="not found"):
        load_workflow("prose", kanban_root=tmp_path)


# ---------------------------------------------------------------------------
# load_workflow() — project-local override search order
# ---------------------------------------------------------------------------


def test_load_workflow_project_local_override_wins_over_team_definition(
    tmp_path: pathlib.Path,
) -> None:
    """Project-local workflows/$name.yaml is preferred over team/workflows/$name.yaml."""
    team_yaml = _MINIMAL_YAML.format(name="custom") + "\n# from team\n"
    project_yaml = _MINIMAL_YAML.format(name="custom").replace(
        "A test workflow.", "Project override description."
    )
    _write_workflow(tmp_path, "custom", team_yaml)
    _write_project_workflow(tmp_path, "custom", project_yaml)
    result = load_workflow("custom", kanban_root=tmp_path)
    assert result.description == "Project override description."


# ---------------------------------------------------------------------------
# load_workflow() — error cases
# ---------------------------------------------------------------------------


def test_load_workflow_raises_for_missing_file(tmp_path: pathlib.Path) -> None:
    """load_workflow raises WorkflowError when the workflow YAML does not exist."""
    with pytest.raises(WorkflowError, match="not found"):
        load_workflow("nonexistent", kanban_root=tmp_path)


def test_load_workflow_raises_for_invalid_yaml(tmp_path: pathlib.Path) -> None:
    """load_workflow raises WorkflowError when the YAML is malformed."""
    _write_workflow(tmp_path, "bad", "not: valid: yaml: ][")
    with pytest.raises(WorkflowError, match="YAML parse error"):
        load_workflow("bad", kanban_root=tmp_path)


def test_load_workflow_raises_when_name_does_not_match_filename(
    tmp_path: pathlib.Path,
) -> None:
    """load_workflow raises WorkflowError when 'name' field differs from filename stem."""
    _write_workflow(tmp_path, "correct-name", _MINIMAL_YAML.format(name="wrong-name"))
    with pytest.raises(WorkflowError, match="does not match filename"):
        load_workflow("correct-name", kanban_root=tmp_path)


def test_load_workflow_raises_for_unknown_agent_role(tmp_path: pathlib.Path) -> None:
    """load_workflow raises WorkflowError when an agent has an unknown role."""
    yaml_with_bad_role = _MINIMAL_YAML.format(name="test").replace(
        "author: WRITER", "author: UNKNOWN_ROLE"
    )
    _write_workflow(tmp_path, "test", yaml_with_bad_role)
    with pytest.raises(WorkflowError, match="Unknown role"):
        load_workflow("test", kanban_root=tmp_path)


def test_load_workflow_raises_for_missing_pipeline(tmp_path: pathlib.Path) -> None:
    """load_workflow raises WorkflowError when the 'pipeline' field is absent."""
    yaml_no_pipeline = textwrap.dedent("""\
        name: test
        description: Missing pipeline.
        inputs:
          required: []
        agents:
          author: WRITER
        outputs:
          format: markdown
          location: output/
        versioning: none
    """)
    _write_workflow(tmp_path, "test", yaml_no_pipeline)
    with pytest.raises(WorkflowError, match="pipeline"):
        load_workflow("test", kanban_root=tmp_path)


def test_load_workflow_raises_for_unknown_versioning_mode(tmp_path: pathlib.Path) -> None:
    """load_workflow raises WorkflowError for an unknown versioning value."""
    yaml_bad_versioning = _MINIMAL_YAML.format(name="test").replace(
        "versioning: auto-increment", "versioning: bad-mode"
    )
    _write_workflow(tmp_path, "test", yaml_bad_versioning)
    with pytest.raises(WorkflowError, match="versioning"):
        load_workflow("test", kanban_root=tmp_path)


def test_load_workflow_raises_for_create_branch_without_branch_pattern(
    tmp_path: pathlib.Path,
) -> None:
    """load_workflow raises WorkflowError when create_branch step lacks branch_pattern."""
    yaml_no_pattern = textwrap.dedent("""\
        name: test
        description: Missing branch_pattern.
        inputs:
          required: []
        agents:
          manage: CM
        pipeline:
          - role: CM
            name: open
            operation: create_branch
        outputs:
          format: git_tag
          location: refs/tags/
        versioning: none
    """)
    _write_workflow(tmp_path, "test", yaml_no_pattern)
    with pytest.raises(WorkflowError, match="branch_pattern"):
        load_workflow("test", kanban_root=tmp_path)


def test_load_workflow_raises_for_tag_and_push_without_target_branch(
    tmp_path: pathlib.Path,
) -> None:
    """load_workflow raises WorkflowError when tag_and_push step lacks target_branch."""
    yaml_no_target = textwrap.dedent("""\
        name: test
        description: Missing target_branch.
        inputs:
          required: []
        agents:
          manage: CM
        pipeline:
          - role: CM
            name: ship
            operation: tag_and_push
        outputs:
          format: git_tag
          location: refs/tags/
        versioning: none
    """)
    _write_workflow(tmp_path, "test", yaml_no_target)
    with pytest.raises(WorkflowError, match="target_branch"):
        load_workflow("test", kanban_root=tmp_path)


# ---------------------------------------------------------------------------
# list_workflows()
# ---------------------------------------------------------------------------


def test_list_workflows_returns_sorted_list_of_names(tmp_path: pathlib.Path) -> None:
    """list_workflows returns a sorted list of workflow names."""
    _write_workflow(tmp_path, "release", _MINIMAL_YAML.format(name="release"))
    _write_workflow(tmp_path, "document", _MINIMAL_YAML.format(name="document"))
    result = list_workflows(kanban_root=tmp_path)
    assert "release" in result
    assert "document" in result
    assert result == sorted(result)


def test_list_workflows_does_not_include_prose(
    tmp_path: pathlib.Path,
) -> None:
    """list_workflows does not include 'prose' — the alias was removed; 'document' is canonical."""
    _write_workflow(tmp_path, "document", _MINIMAL_YAML.format(name="document"))
    result = list_workflows(kanban_root=tmp_path)
    assert "prose" not in result
    assert "document" in result


def test_list_workflows_returns_empty_list_for_empty_root(
    tmp_path: pathlib.Path,
) -> None:
    """list_workflows returns [] when neither team/workflows/ nor workflows/ exist."""
    result = list_workflows(kanban_root=tmp_path)
    assert result == []


def test_list_workflows_includes_project_local_overrides(tmp_path: pathlib.Path) -> None:
    """list_workflows finds names from project-local workflows/ as well as team/workflows/."""
    _write_workflow(tmp_path, "release", _MINIMAL_YAML.format(name="release"))
    _write_project_workflow(tmp_path, "custom", _MINIMAL_YAML.format(name="custom"))
    result = list_workflows(kanban_root=tmp_path)
    assert "release" in result
    assert "custom" in result
