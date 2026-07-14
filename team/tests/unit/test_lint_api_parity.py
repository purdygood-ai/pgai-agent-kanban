"""
test_lint_api_parity.py — Unit tests for team/scripts/lint_api_parity.py.

Tests cover:

  1. **Drift detection (the key acceptance criterion)**: inject the deliberate
     drift fixture (fixture_api_drift_script.sh, which declares --frobnicate
     in OPERATOR_VALID_FLAGS but has no corresponding body field) and assert
     that lint exits 1 with a message naming the script filename, the flag,
     and the model class name.

  2. **Parity pass**: a script whose OPERATOR_VALID_FLAGS matches the body
     model fields exactly should return zero violations.

  3. **Hyphen-to-underscore equivalence**: a script flag ``dry-run`` matched
     against body field ``dry_run`` must pass (not flagged as drift).

  4. **Intake equivalence**: a script with ``file`` in OPERATOR_VALID_FLAGS
     matched against a body with ``filename`` and ``content`` fields must
     pass (documented intake equivalence).

  5. **Meta-flag exclusion**: ``help`` and ``h`` in OPERATOR_VALID_FLAGS must
     never appear as violations regardless of the body model.

All tests use pytest's tmp_path and importlib for isolation — no bare /tmp
paths, no live kanban state.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Resolve path to lint_api_parity.py so we can import it as a module.
# This file lives at team/tests/unit/test_lint_api_parity.py.
# Going up three levels: unit/ → tests/ → team/
# ---------------------------------------------------------------------------
_THIS_FILE = Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent    # team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_LINT_SCRIPT = _SCRIPTS_DIR / "lint_api_parity.py"


def _import_lint_module():
    """Import lint_api_parity as a module (avoids polluting sys.modules permanently)."""
    spec = importlib.util.spec_from_file_location("lint_api_parity", _LINT_SCRIPT)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load spec from {_LINT_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# Import once at module load time; tests reference `_lint` attributes.
_lint = _import_lint_module()


# ---------------------------------------------------------------------------
# Minimal body model helpers
# ---------------------------------------------------------------------------


def _make_model(**fields: Any) -> type[BaseModel]:
    """Dynamically create a Pydantic BaseModel subclass with the given fields.

    Each keyword argument becomes an Optional[str] field on the model.
    The model name is ``DynamicBody`` so violation messages are predictable.
    """
    from typing import Optional

    annotations: dict[str, Any] = {name: Optional[str] for name in fields}
    defaults: dict[str, Any] = {name: None for name in fields}
    return type("DynamicBody", (BaseModel,), {"__annotations__": annotations, **defaults})


# ---------------------------------------------------------------------------
# Path to the deliberate drift fixture script
# ---------------------------------------------------------------------------
_FIXTURES_DIR = _TEAM_DIR / "tests" / "fixtures"
_DRIFT_FIXTURE = _FIXTURES_DIR / "fixture_api_drift_script.sh"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_drift_fixture_exists():
    """Sanity: the drift fixture script must be present in the fixtures directory."""
    assert _DRIFT_FIXTURE.exists(), (
        f"Drift fixture not found: {_DRIFT_FIXTURE}\n"
        "Expected at team/tests/fixtures/fixture_api_drift_script.sh"
    )


def test_drift_fixture_detected():
    """Core acceptance criterion: drift fixture script → lint exits 1.

    The fixture script declares OPERATOR_VALID_FLAGS=(project frobnicate help).
    A body model with only ``project`` lacks ``frobnicate`` — that is the
    injected drift.  The lint must:
      - exit 1 (return at least one violation)
      - produce a message that names the script filename, the flag, and the
        model class name.
    """
    # Body model that intentionally lacks 'frobnicate'.
    drift_body = _make_model(project=None)

    violations = _lint.check_parity(_DRIFT_FIXTURE, drift_body, verbose=False)

    assert len(violations) >= 1, (
        f"Expected at least one parity violation from {_DRIFT_FIXTURE.name} "
        f"but got none.  Script declares --frobnicate; body model has no such field."
    )

    joined = "\n".join(violations)

    # Violation message must name the script filename.
    assert _DRIFT_FIXTURE.name in joined, (
        f"Violation message does not name the script filename '{_DRIFT_FIXTURE.name}':\n"
        f"{joined}"
    )

    # Violation message must name the flag.
    assert "frobnicate" in joined, (
        f"Violation message does not name the flag 'frobnicate':\n{joined}"
    )

    # Violation message must name the model class.
    assert drift_body.__name__ in joined, (
        f"Violation message does not name the model class '{drift_body.__name__}':\n"
        f"{joined}"
    )


def test_parity_pass_exact_match(tmp_path: Path):
    """A script whose flags exactly match the body fields passes with zero violations."""
    script = tmp_path / "fake_op.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "OPERATOR_VALID_FLAGS=(project key help)\n"
    )

    body = _make_model(project=None, key=None)
    violations = _lint.check_parity(script, body, verbose=False)
    assert violations == [], f"Unexpected violations: {violations}"


def test_hyphen_underscore_equivalence(tmp_path: Path):
    """Script flag 'dry-run' is satisfied by body field 'dry_run' (no violation)."""
    script = tmp_path / "fake_dry.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "OPERATOR_VALID_FLAGS=(project dry-run help)\n"
    )

    body = _make_model(project=None, dry_run=None)
    violations = _lint.check_parity(script, body, verbose=False)
    assert violations == [], f"Unexpected violations: {violations}"


def test_intake_file_equivalence(tmp_path: Path):
    """Script flag 'file' is satisfied by body fields 'filename' and 'content'."""
    script = tmp_path / "fake_intake.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "OPERATOR_VALID_FLAGS=(project file help)\n"
    )

    body = _make_model(project=None, filename=None, content=None)
    violations = _lint.check_parity(script, body, verbose=False)
    assert violations == [], f"Unexpected violations: {violations}"


def test_meta_flags_excluded(tmp_path: Path):
    """'help' and 'h' in OPERATOR_VALID_FLAGS are never reported as violations."""
    script = tmp_path / "fake_meta.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "OPERATOR_VALID_FLAGS=(project help h)\n"
    )

    # Body has only 'project' — 'help' and 'h' must not be flagged.
    body = _make_model(project=None)
    violations = _lint.check_parity(script, body, verbose=False)
    assert violations == [], f"Unexpected violations: {violations}"


def test_missing_flag_reported(tmp_path: Path):
    """A flag present in the script but absent from the body is reported correctly."""
    script = tmp_path / "fake_missing.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "OPERATOR_VALID_FLAGS=(project missing-field help)\n"
    )

    body = _make_model(project=None)
    violations = _lint.check_parity(script, body, verbose=False)

    assert len(violations) == 1, f"Expected exactly one violation; got: {violations}"
    assert "missing-field" in violations[0], (
        f"Violation does not name the missing flag: {violations[0]}"
    )
    assert script.name in violations[0], (
        f"Violation does not name the script: {violations[0]}"
    )
    assert body.__name__ in violations[0], (
        f"Violation does not name the model class: {violations[0]}"
    )


def test_intake_file_equivalence_body_missing_one_field(tmp_path: Path):
    """If body has 'filename' but not 'content', the equivalence is not satisfied."""
    script = tmp_path / "fake_intake_partial.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "OPERATOR_VALID_FLAGS=(project file help)\n"
    )

    # Missing 'content' from the body → equivalence unsatisfied → violation expected.
    body = _make_model(project=None, filename=None)
    violations = _lint.check_parity(script, body, verbose=False)
    assert len(violations) >= 1, (
        "Expected a violation when body is missing 'content' for the intake equivalence"
    )


# ---------------------------------------------------------------------------
# Tests for check_op_model_has_dry_run
# ---------------------------------------------------------------------------


def test_op_model_with_dry_run_passes():
    """A body model that declares dry_run produces no violations."""
    body = _make_model(project=None, dry_run=None)
    violations = _lint.check_op_model_has_dry_run(body)
    assert violations == [], f"Unexpected violations: {violations}"


def test_op_model_missing_dry_run_scratch_negative():
    """Scratch negative: a synthesized op request model without dry_run fails the lint.

    This is the class-closer proof: any operation body model that omits
    dry_run is caught immediately.  The scratch model is built in-test and
    never left in the source tree.
    """
    # Synthesize a stub op request model that intentionally lacks dry_run.
    stub_without_dry_run = _make_model(project=None, key=None)

    violations = _lint.check_op_model_has_dry_run(stub_without_dry_run)

    assert len(violations) >= 1, (
        "Expected at least one violation from a stub op model missing 'dry_run', "
        f"but got none.  Model fields: {set(stub_without_dry_run.model_fields.keys())}"
    )
    # Violation must name the model class.
    joined = "\n".join(violations)
    assert stub_without_dry_run.__name__ in joined, (
        f"Violation does not name the model class '{stub_without_dry_run.__name__}':\n"
        f"{joined}"
    )
    # Violation must mention the missing field.
    assert "dry_run" in joined, (
        f"Violation does not mention 'dry_run':\n{joined}"
    )


def test_all_current_op_models_have_dry_run():
    """Regression: every *Body model in the live operations module has dry_run.

    Calls lint_all_op_models_dry_run() against the real team/ directory.
    A future verb that adds a Body class without dry_run makes this test fail.
    """
    violations = _lint.lint_all_op_models_dry_run(_TEAM_DIR)
    assert violations == [], (
        "The following operation body models are missing 'dry_run':\n"
        + "\n".join(f"  {v}" for v in violations)
    )


# ---------------------------------------------------------------------------
# Tests for check_response_model_has_warnings
# ---------------------------------------------------------------------------


def test_response_model_with_warnings_passes():
    """A response model that declares warnings produces no violations."""
    from typing import List

    # Use List[str] for the warnings field to mirror the real contract.
    annotations = {"exit_code": int, "stdout": str, "stderr": str, "warnings": List[str]}
    defaults = {"exit_code": 0, "stdout": "", "stderr": "", "warnings": []}
    from pydantic import BaseModel
    stub_ok = type("OperationEnvelope", (BaseModel,), {"__annotations__": annotations, **defaults})

    violations = _lint.check_response_model_has_warnings(stub_ok)
    assert violations == [], f"Unexpected violations: {violations}"


def test_response_model_missing_warnings_scratch_negative():
    """Scratch negative: a synthesized response model without warnings fails the lint.

    This is the class-closer proof for the response side: any response Pydantic
    model that omits warnings is caught immediately.  The stub is synthesized
    in-test and never committed to the source tree.
    """
    # Synthesize a stub response model that intentionally lacks warnings.
    stub_without_warnings = _make_model(exit_code=None, stdout=None, stderr=None)

    violations = _lint.check_response_model_has_warnings(stub_without_warnings)

    assert len(violations) >= 1, (
        "Expected at least one violation from a stub response model missing 'warnings', "
        f"but got none.  Model fields: {set(stub_without_warnings.model_fields.keys())}"
    )
    # Violation must name the model class.
    joined = "\n".join(violations)
    assert stub_without_warnings.__name__ in joined, (
        f"Violation does not name the model class '{stub_without_warnings.__name__}':\n"
        f"{joined}"
    )
    # Violation must mention the missing field.
    assert "warnings" in joined, (
        f"Violation does not mention 'warnings':\n{joined}"
    )


def test_all_current_response_models_have_warnings():
    """Regression: every *Response/*Envelope model in the live API package has warnings.

    Calls lint_all_response_models_warnings() against the real team/ directory.
    The current codebase has no Pydantic response models (responses use inline
    dicts), so this passes trivially with zero models checked.  A future
    developer who adds a Pydantic response class without warnings will make this
    test fail.
    """
    violations = _lint.lint_all_response_models_warnings(_TEAM_DIR)
    assert violations == [], (
        "The following response models are missing 'warnings':\n"
        + "\n".join(f"  {v}" for v in violations)
    )
