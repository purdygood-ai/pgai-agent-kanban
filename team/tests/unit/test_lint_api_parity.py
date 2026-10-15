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

  6. **Census-walk gate (the BLOCK-WORTHY failure classes)**: each of
     route_missing_script, route_missing_census, schema_mismatch,
     script_missing_census, action_fixture_missing (with its mirror,
     action_fixture_unknown_verb), verb_missing_route, and
     read_fixture_missing (with its mirror, read_fixture_unknown_verb) is
     exercised with a seeded failing fixture and a seeded passing one, plus the
     exempt-list and out-of-scope-shape escape hatches each check
     documents. A live regression test
     (test_run_census_gate_is_clean_on_the_real_tree) asserts the real
     team/ tree itself reports zero violations -- the requirements
     document's own convergence criterion.

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
    """Import lint_api_parity as a module.

    Registers the module in sys.modules under its own name before exec --
    required for its ``@dataclass`` (CensusFinding) to resolve its own
    annotations under ``from __future__ import annotations``, since
    dataclasses looks its defining module up via
    ``sys.modules[cls.__module__]`` at class-creation time. Popped again
    immediately after so this stays test-local rather than polluting the
    real sys.modules for the rest of the test session.
    """
    spec = importlib.util.spec_from_file_location("lint_api_parity", _LINT_SCRIPT)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load spec from {_LINT_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    finally:
        sys.modules.pop(spec.name, None)
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
    # Deliberately exercises the real team/ directory (not a fixture): this
    # is a live regression guard over the actual op-model source, not an
    # isolated behavior a fixture could stand in for.
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
    # Deliberately exercises the real team/ directory (not a fixture): this
    # is a live regression guard over the actual response-model source, not
    # an isolated behavior a fixture could stand in for.
    violations = _lint.lint_all_response_models_warnings(_TEAM_DIR)
    assert violations == [], (
        "The following response models are missing 'warnings':\n"
        + "\n".join(f"  {v}" for v in violations)
    )


# ---------------------------------------------------------------------------
# Census-walk gate tests (the BLOCK-WORTHY failure classes).
#
# Each check function is exercised directly with hand-built Verb records and
# small tmp_path fixtures -- never against the live team/ tree for the
# failing cases (that tree is meant to stay green; see
# test_run_census_gate_is_clean_on_the_real_tree below for the live
# regression guard covering the passing case).
# ---------------------------------------------------------------------------

if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from pgai_agent_kanban.lib.operator_verb_manifest import Verb  # noqa: E402


def _make_verb(**overrides: object) -> Verb:
    """Build an "active, routed" Verb record with sane defaults, overridable
    per test."""
    defaults: dict = dict(
        name="probe",
        status="active",
        script="scripts/probe.sh",
        flags=("project", "help"),
        mutates=False,
        idempotence="read-only",
        rest_method="GET",
        rest_route="/probe",
        rest_body_model="NONE",
        verb_class="read",
        schema_ref="ShellEnvelope",
    )
    defaults.update(overrides)
    return Verb(**defaults)


class _FakeRouteDecl:
    """Minimal stand-in for lint_operator_verb_manifest._RouteDecl -- only
    the three attributes check_app_routes_have_census_row reads."""

    def __init__(self, method: str, route: str, fronted_script: str | None) -> None:
        self.method = method
        self.route = route
        self.fronted_script = fronted_script


# ---------------------------------------------------------------------------
# Class 1: route_missing_script
# ---------------------------------------------------------------------------


def test_check_route_script_existence_flags_missing_script(tmp_path: Path):
    verb = _make_verb(script="scripts/does-not-exist.sh")
    findings = _lint.check_route_script_existence({"probe": verb}, tmp_path)

    assert len(findings) == 1
    assert findings[0].kind == "route_missing_script"
    assert "probe" in findings[0].text
    assert "scripts/does-not-exist.sh" in findings[0].text


def test_check_route_script_existence_passes_when_script_present(tmp_path: Path):
    script_dir = tmp_path / "scripts"
    script_dir.mkdir()
    (script_dir / "probe.sh").write_text("#!/usr/bin/env bash\n")

    verb = _make_verb()
    findings = _lint.check_route_script_existence({"probe": verb}, tmp_path)
    assert findings == []


def test_check_route_script_existence_skips_verb_with_no_rest_twin(tmp_path: Path):
    """An active verb with no REST twin (both ABSENT) is not this check's
    concern -- there is no route to check the script against;
    check_active_verbs_have_route (class 6) is what reports the row."""
    verb = _make_verb(rest_method="ABSENT", rest_route="ABSENT", script="scripts/no-route.sh")
    findings = _lint.check_route_script_existence({"probe": verb}, tmp_path)
    assert findings == []


# ---------------------------------------------------------------------------
# Class 2: route_missing_census
# ---------------------------------------------------------------------------


def test_check_app_routes_have_census_row_flags_unregistered_route():
    verb = _make_verb(rest_route="/probe")  # census only knows about /probe
    declared = [_FakeRouteDecl("GET", "/frobnicate", "frobnicate.sh")]

    findings = _lint.check_app_routes_have_census_row({"probe": verb}, declared, frozenset())

    assert len(findings) == 1
    assert findings[0].kind == "route_missing_census"
    assert "/frobnicate" in findings[0].text
    assert "frobnicate.sh" in findings[0].text


def test_check_app_routes_have_census_row_passes_for_censused_route():
    verb = _make_verb(rest_method="GET", rest_route="/probe")
    declared = [_FakeRouteDecl("GET", "/probe", "probe.sh")]

    findings = _lint.check_app_routes_have_census_row({"probe": verb}, declared, frozenset())
    assert findings == []


def test_check_app_routes_have_census_row_respects_exempt_list():
    verb = _make_verb(rest_route="/probe")
    declared = [_FakeRouteDecl("GET", "/frobnicate", "frobnicate.sh")]

    findings = _lint.check_app_routes_have_census_row(
        {"probe": verb}, declared, frozenset({"frobnicate.sh"})
    )
    assert findings == []


def test_check_app_routes_have_census_row_ignores_routes_with_no_fronted_script():
    verb = _make_verb(rest_route="/probe")
    declared = [_FakeRouteDecl("GET", "/health", None)]

    findings = _lint.check_app_routes_have_census_row({"probe": verb}, declared, frozenset())
    assert findings == []


# ---------------------------------------------------------------------------
# Class 3: schema_mismatch
# ---------------------------------------------------------------------------

_ROUTER_FIXTURE_TEMPLATE = '''
from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/probe")
def get_probe():
    return JSONResponse(content={body}, status_code=200)
'''


def _write_router_fixture(tmp_path: Path, body: str) -> Path:
    api_dir = tmp_path / "pgai_agent_kanban" / "api"
    api_dir.mkdir(parents=True)
    reads_py = api_dir / "reads.py"
    reads_py.write_text(_ROUTER_FIXTURE_TEMPLATE.format(body=body), encoding="utf-8")
    return tmp_path


def test_check_response_schema_flags_seeded_mismatch(tmp_path: Path):
    # Census declares ShellEnvelope; the handler actually returns
    # VerdictsResponse's shape -- a seeded, deliberate mismatch.
    team_dir = _write_router_fixture(
        tmp_path, '{"verdicts": [], "warnings": []}'
    )
    verb = _make_verb(rest_method="GET", rest_route="/probe", schema_ref="ShellEnvelope")

    findings = _lint.check_response_schema({"probe": verb}, team_dir)

    assert len(findings) == 1
    assert findings[0].kind == "schema_mismatch"
    assert "probe" in findings[0].text
    assert "ShellEnvelope" in findings[0].text


def test_check_response_schema_passes_when_shape_matches(tmp_path: Path):
    team_dir = _write_router_fixture(
        tmp_path,
        '{"exit_code": 0, "stdout": "", "stderr": "", "warnings": []}',
    )
    verb = _make_verb(rest_method="GET", rest_route="/probe", schema_ref="ShellEnvelope")

    findings = _lint.check_response_schema({"probe": verb}, team_dir)
    assert findings == []


def test_check_response_schema_skips_unresolvable_shape(tmp_path: Path):
    """A route whose response this lightweight AST scanner cannot statically
    resolve (here: content built from a function call this scanner does not
    know) is skipped -- never a false mismatch."""
    team_dir = _write_router_fixture(tmp_path, "some_external_builder()")
    verb = _make_verb(rest_method="GET", rest_route="/probe", schema_ref="ShellEnvelope")

    findings = _lint.check_response_schema({"probe": verb}, team_dir)
    assert findings == []


# ---------------------------------------------------------------------------
# Class 4: script_missing_census
# ---------------------------------------------------------------------------


def test_check_scripts_have_census_row_flags_unregistered_script(tmp_path: Path):
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "orphan.sh").write_text(
        "#!/usr/bin/env bash\nOPERATOR_VALID_FLAGS=(project help)\n"
    )

    findings = _lint.check_scripts_have_census_row({}, tmp_path, _ovm_module(), frozenset())

    assert len(findings) == 1
    assert findings[0].kind == "script_missing_census"
    assert "orphan.sh" in findings[0].text


def test_check_scripts_have_census_row_passes_for_censused_script(tmp_path: Path):
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "probe.sh").write_text(
        "#!/usr/bin/env bash\nOPERATOR_VALID_FLAGS=(project help)\n"
    )
    verb = _make_verb(script="scripts/probe.sh")

    findings = _lint.check_scripts_have_census_row(
        {"probe": verb}, tmp_path, _ovm_module(), frozenset()
    )
    assert findings == []


def test_check_scripts_have_census_row_respects_exempt_list(tmp_path: Path):
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "orphan.sh").write_text(
        "#!/usr/bin/env bash\nOPERATOR_VALID_FLAGS=(project help)\n"
    )

    findings = _lint.check_scripts_have_census_row(
        {}, tmp_path, _ovm_module(), frozenset({"orphan.sh"})
    )
    assert findings == []


def test_check_scripts_have_census_row_ignores_scripts_with_no_flag_vocabulary(
    tmp_path: Path,
):
    """A script with no OPERATOR_VALID_FLAGS array and no hand-parsed
    registration proves nothing about being operator-invocable -- not this
    check's concern."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "helper.sh").write_text("#!/usr/bin/env bash\necho hi\n")

    findings = _lint.check_scripts_have_census_row({}, tmp_path, _ovm_module(), frozenset())
    assert findings == []


def _ovm_module():
    from pgai_agent_kanban.lib import operator_verb_manifest as ovm

    return ovm


# ---------------------------------------------------------------------------
# Live regression: the real tree's census-walk gate is clean.
# ---------------------------------------------------------------------------


def test_run_census_gate_is_clean_on_the_real_tree():
    """Regression: run_census_gate() against the real team/ directory finds
    zero violations -- the convergence criterion the requirements document
    names ("the parity lint reporting ZERO gaps on the fixing diff itself").
    A future verb, route, or script added without its census row (or
    exempt-list entry) makes this test fail.
    """
    findings = _lint.run_census_gate(_TEAM_DIR)
    assert findings == [], (
        "The following census-walk violations were found on the live tree:\n"
        + "\n".join(f"  [{f.kind}] {f.text}" for f in findings)
    )


def test_lint_api_parity_main_exits_0_on_the_real_tree():
    """Acceptance criterion: `python3 team/scripts/lint_api_parity.py` exits
    0 on a tree whose census is internally consistent."""
    result = _lint.main([])
    assert result == 0


# ---------------------------------------------------------------------------
# Class 5: action_fixture_missing / action_fixture_unknown_verb
# ---------------------------------------------------------------------------


def _action_verb(name: str) -> Verb:
    return _make_verb(
        name=name,
        script=f"scripts/{name}.sh",
        mutates=True,
        idempotence="idempotent",
        rest_method="POST",
        rest_route=f"/operations/{name}",
        rest_body_model="ProbeBody",
        verb_class="action",
    )


def test_check_action_verbs_have_parity_fixture_flags_an_unfixtured_action_verb():
    """An active action verb absent from the ledger is reported by name."""
    verbs = {"probe-act": _action_verb("probe-act"), "covered": _action_verb("covered")}
    findings = _lint.check_action_verbs_have_parity_fixture(
        verbs, _TEAM_DIR, ledger_verbs=frozenset({"covered"})
    )
    assert len(findings) == 1, findings
    assert findings[0].kind == "action_fixture_missing"
    assert "'probe-act'" in findings[0].text
    assert "action_parity_cases.py" in findings[0].text


def test_check_action_verbs_have_parity_fixture_ignores_read_verbs_and_planned_verbs():
    """Only active action-class rows need a case: a read verb and a planned
    verb with no ledger entry are not findings."""
    verbs = {
        "probe-read": _make_verb(name="probe-read"),  # verb_class="read" by default
        "probe-planned": _make_verb(name="probe-planned", status="planned", verb_class="-"),
    }
    findings = _lint.check_action_verbs_have_parity_fixture(verbs, _TEAM_DIR, ledger_verbs=frozenset())
    assert findings == []


def test_check_action_verbs_have_parity_fixture_flags_a_case_for_an_unknown_verb():
    """A ledger case naming a verb with no active action row is its own
    finding -- a fixture that would run against nothing the census knows."""
    verbs = {"probe-act": _action_verb("probe-act")}
    findings = _lint.check_action_verbs_have_parity_fixture(
        verbs, _TEAM_DIR, ledger_verbs=frozenset({"probe-act", "ghost-verb"})
    )
    assert [f.kind for f in findings] == ["action_fixture_unknown_verb"]
    assert "'ghost-verb'" in findings[0].text


def test_check_action_verbs_have_parity_fixture_reports_a_missing_ledger_once(tmp_path: Path):
    """A team dir with no ledger module at all yields one finding naming the
    expected path, not one per action verb."""
    verbs = {name: _action_verb(name) for name in ("a-act", "b-act", "c-act")}
    findings = _lint.check_action_verbs_have_parity_fixture(verbs, tmp_path)
    assert len(findings) == 1, findings
    assert findings[0].kind == "action_fixture_missing"
    assert str(tmp_path / "tests" / "fixtures" / "action_parity_cases.py") in findings[0].text
    assert "3 active action verb(s)" in findings[0].text


def test_check_action_verbs_have_parity_fixture_reads_the_real_ledger_and_is_clean():
    """Against the real census and the real ledger, every active action verb
    has a case and no case names an unknown verb."""
    from pgai_agent_kanban.lib.operator_verb_manifest import load_manifest

    findings = _lint.check_action_verbs_have_parity_fixture(load_manifest(), _TEAM_DIR)
    assert findings == [], "\n".join(f"  [{f.kind}] {f.text}" for f in findings)


def test_run_census_gate_reports_an_action_verb_added_without_a_fixture(monkeypatch: pytest.MonkeyPatch):
    """The seeded gap the acceptance criterion names: a census action row
    with no fixture fails the gate naming the verb. Seeded by making the
    ledger loader return a set missing one real action verb."""
    from pgai_agent_kanban.lib.operator_verb_manifest import load_manifest

    real = _lint._load_action_parity_verbs(_TEAM_DIR)
    assert "halt-after" in real
    monkeypatch.setattr(_lint, "_load_action_parity_verbs", lambda team_dir: real - {"halt-after"})
    findings = [f for f in _lint.run_census_gate(_TEAM_DIR) if f.kind == "action_fixture_missing"]
    assert len(findings) == 1, findings
    assert "'halt-after'" in findings[0].text
    # And the manifest still says halt-after is an action verb -- the seed
    # removed the fixture, not the census row.
    assert load_manifest()["halt-after"].verb_class == "action"


# ---------------------------------------------------------------------------
# Class 6: verb_missing_route
# ---------------------------------------------------------------------------


def _unrouted_active_verb(name: str = "frob") -> Verb:
    """An active census row for a real-looking script whose four REST
    columns all say ABSENT -- the seeded gap the gate must refuse."""
    return _make_verb(
        name=name,
        script=f"scripts/{name}.sh",
        rest_method="ABSENT",
        rest_route="ABSENT",
        rest_body_model="ABSENT",
        schema_ref="ABSENT",
    )


def test_check_active_verbs_have_route_flags_an_active_row_with_no_rest_twin():
    """The seeded gap: an active row whose REST columns are all ABSENT is
    reported once, naming both the verb and its script."""
    findings = _lint.check_active_verbs_have_route({"frob": _unrouted_active_verb()})

    assert len(findings) == 1, findings
    assert findings[0].kind == "verb_missing_route"
    assert "'frob'" in findings[0].text
    assert "scripts/frob.sh" in findings[0].text


def test_check_active_verbs_have_route_passes_for_a_routed_active_row():
    """An active row with a real rest_method and rest_route is not a finding."""
    findings = _lint.check_active_verbs_have_route({"probe": _make_verb()})
    assert findings == []


def test_check_active_verbs_have_route_exempts_a_planned_row():
    """A planned row has no script yet, so there is nothing to twin: ABSENT
    REST columns are the documented marker there, never a finding."""
    planned = _make_verb(
        name="someday",
        status="planned",
        script=None,
        flags=(),
        mutates=None,
        idempotence="-",
        rest_method="ABSENT",
        rest_route="ABSENT",
        rest_body_model="ABSENT",
        verb_class="-",
        schema_ref="ABSENT",
    )
    findings = _lint.check_active_verbs_have_route({"someday": planned})
    assert findings == []


def test_check_active_verbs_have_route_flags_a_half_declared_twin():
    """A row with a method but no route (or the reverse) declares no
    reachable twin either; it is reported rather than slipping past both
    this class and the route-driven classes, which never see it."""
    half = _make_verb(name="half", script="scripts/half.sh", rest_route="ABSENT")
    findings = _lint.check_active_verbs_have_route({"half": half})
    assert [f.kind for f in findings] == ["verb_missing_route"]
    assert "'half'" in findings[0].text


def test_run_census_gate_reports_an_active_verb_added_without_a_route(
    monkeypatch: pytest.MonkeyPatch,
):
    """End to end through run_census_gate: the real census plus one active
    row with ABSENT REST columns fails the gate naming that verb and its
    script, and no other class claims the same row."""
    from pgai_agent_kanban.lib import operator_verb_manifest as ovm

    seeded = dict(ovm.load_manifest())
    seeded["frob"] = _unrouted_active_verb()
    monkeypatch.setattr(ovm, "load_manifest", lambda path=None: seeded)

    findings = _lint.run_census_gate(_TEAM_DIR)

    assert [f.kind for f in findings] == ["verb_missing_route"], findings
    assert "'frob'" in findings[0].text
    assert "scripts/frob.sh" in findings[0].text


# ---------------------------------------------------------------------------
# Class 7: read_fixture_missing / read_fixture_unknown_verb
# ---------------------------------------------------------------------------


def _read_verb(name: str) -> Verb:
    """An active read-class census row -- _make_verb's own defaults are a
    read verb; only the name and script differ per row."""
    return _make_verb(name=name, script=f"scripts/{name}.sh")


def test_check_read_verbs_have_parity_fixture_flags_an_unfixtured_read_verb():
    """An active read verb absent from the read-parity ledger is reported
    by name, as a missing golden pair, naming the ledger to add it to."""
    verbs = {"probe-read": _read_verb("probe-read"), "covered": _read_verb("covered")}
    findings = _lint.check_read_verbs_have_parity_fixture(
        verbs, _TEAM_DIR, ledger_verbs=frozenset({"covered"})
    )
    assert len(findings) == 1, findings
    assert findings[0].kind == "read_fixture_missing"
    assert "'probe-read'" in findings[0].text
    assert "golden-pair" in findings[0].text
    assert "read_parity_cases.py" in findings[0].text


def test_check_read_verbs_have_parity_fixture_ignores_action_verbs_and_planned_verbs():
    """Only active read-class rows need a golden pair: an action verb and a
    planned verb with no ledger entry are not findings."""
    verbs = {
        "probe-act": _action_verb("probe-act"),
        "probe-planned": _make_verb(name="probe-planned", status="planned", verb_class="-"),
    }
    findings = _lint.check_read_verbs_have_parity_fixture(verbs, _TEAM_DIR, ledger_verbs=frozenset())
    assert findings == []


def test_check_read_verbs_have_parity_fixture_flags_a_case_for_an_unknown_verb():
    """A ledger case naming a verb with no active read row is its own
    finding -- a golden pair that would run against nothing the census
    knows."""
    verbs = {"probe-read": _read_verb("probe-read")}
    findings = _lint.check_read_verbs_have_parity_fixture(
        verbs, _TEAM_DIR, ledger_verbs=frozenset({"probe-read", "ghost-verb"})
    )
    assert [f.kind for f in findings] == ["read_fixture_unknown_verb"]
    assert "'ghost-verb'" in findings[0].text


def test_check_read_verbs_have_parity_fixture_reports_a_missing_ledger_once(tmp_path: Path):
    """A team dir with no read-parity ledger at all yields one finding
    naming the expected path, not one per read verb."""
    verbs = {name: _read_verb(name) for name in ("a-read", "b-read", "c-read")}
    findings = _lint.check_read_verbs_have_parity_fixture(verbs, tmp_path)
    assert len(findings) == 1, findings
    assert findings[0].kind == "read_fixture_missing"
    assert str(tmp_path / "tests" / "fixtures" / "read_parity_cases.py") in findings[0].text
    assert "3 active, routed read verb(s)" in findings[0].text


def test_check_read_verbs_have_parity_fixture_skips_an_unrouted_read_verb():
    """An active read row with no REST twin has nothing to pair the CLI
    with, and class 6 already reports the row -- it is not also a missing
    golden pair here; but a ledger case that names it is still not
    "unknown", since the census does carry the verb."""
    verbs = {"frob": _unrouted_active_verb("frob"), "covered": _read_verb("covered")}

    findings = _lint.check_read_verbs_have_parity_fixture(
        verbs, _TEAM_DIR, ledger_verbs=frozenset({"covered"})
    )
    assert findings == []

    findings = _lint.check_read_verbs_have_parity_fixture(
        verbs, _TEAM_DIR, ledger_verbs=frozenset({"covered", "frob"})
    )
    assert findings == []


def test_check_read_verbs_have_parity_fixture_reads_the_real_ledger_and_is_clean():
    """Against the real census and the real ledger, every active read verb
    has a golden-pair case and no case names an unknown verb."""
    from pgai_agent_kanban.lib.operator_verb_manifest import load_manifest

    findings = _lint.check_read_verbs_have_parity_fixture(load_manifest(), _TEAM_DIR)
    assert findings == [], "\n".join(f"  [{f.kind}] {f.text}" for f in findings)


def test_run_census_gate_reports_a_read_verb_whose_ledger_case_was_removed(
    monkeypatch: pytest.MonkeyPatch,
):
    """Removing a read case from the ledger fails the gate naming the verb.
    Seeded by making the ledger loader return a set missing one real read
    verb; the census row itself is untouched."""
    from pgai_agent_kanban.lib.operator_verb_manifest import load_manifest

    real = _lint._load_read_parity_verbs(_TEAM_DIR)
    assert "release-state" in real
    monkeypatch.setattr(_lint, "_load_read_parity_verbs", lambda team_dir: real - {"release-state"})
    findings = [f for f in _lint.run_census_gate(_TEAM_DIR) if f.kind == "read_fixture_missing"]
    assert len(findings) == 1, findings
    assert "'release-state'" in findings[0].text
    assert load_manifest()["release-state"].verb_class == "read"


def test_run_census_gate_reports_a_read_verb_added_without_a_golden_pair(
    monkeypatch: pytest.MonkeyPatch,
):
    """Adding a read census row with no ledger case fails the gate naming
    the new verb, and no other class claims the same row: the row is
    active, routed, and scripted (it points at an existing read script), so
    the missing golden pair is the only thing wrong with it."""
    from pgai_agent_kanban.lib import operator_verb_manifest as ovm

    seeded = dict(ovm.load_manifest())
    seeded["probe-read"] = _make_verb(
        name="probe-read",
        script="scripts/show.sh",
        rest_method="GET",
        rest_route="/show",
        verb_class="read",
        schema_ref="ShellEnvelope",
    )
    monkeypatch.setattr(ovm, "load_manifest", lambda path=None: seeded)

    findings = _lint.run_census_gate(_TEAM_DIR)

    assert [f.kind for f in findings] == ["read_fixture_missing"], findings
    assert "'probe-read'" in findings[0].text
