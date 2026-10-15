"""
test_operator_verb_manifest.py
================================
Behavioral unit tests for the operator verb manifest and its machinery:

  team/config/operator_verbs.manifest                       (the data)
  team/pgai_agent_kanban/lib/operator_verb_manifest.py       (Python reader)
  team/scripts/lib/operator_verb_manifest.sh                 (bash reader)
  team/scripts/lint_operator_verb_manifest.py                (reconciliation check)
  team/scripts/lint_api_parity.py                            (now sources pairs
                                                                from the manifest)

Acceptance criteria exercised:

  1. The manifest exists, names every v2.20.0 verb, and its header states
     its own parse rule.
  2. The bash and Python readers agree on the verb list and on every verb's
     flag vocabulary.
  3. The reconciliation check exits 0 against the unmodified tree, with
     missing REST twins recorded as explicit ABSENT markers.
  4. A bogus flag injected into a script's OPERATOR_VALID_FLAGS makes the
     reconciliation check fail, naming the script and the flag; reverting
     makes it pass again.
  5. Deleting a route from operations.py that the manifest declares makes
     the reconciliation check fail, naming the route; reverting makes it
     pass again.
  6. lint_api_parity.py still passes, reading its pairings from the
     manifest instead of a hardcoded map.
  7. The drift refusal's message and remedy name the manifest file that
     was actually scanned -- the default path when no --manifest was
     given, the supplied path otherwise -- rather than always reporting
     the default regardless of which file was read.
  8. The impl column: every row carries a value, both readers expose it,
     parse_impl() accepts exactly its grammar, and the reconciliation
     check reports a malformed or missing value.

Tests that mutate a script or a router file do so on a throwaway copy of
just the subtrees the checks need (config/, scripts/, pgai_agent_kanban/),
built under pytest's tmp_path — the checked-in tree is only ever read, never
written, by this test module.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent  # team/tests/unit/ -> team/
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from pgai_agent_kanban.lib.operator_verb_manifest import (  # noqa: E402
    ManifestFormatError,
    default_manifest_path,
    extract_operator_valid_flags,
    load_manifest,
    parse_impl,
    verb_names,
)

_BASH_READER = _TEAM_DIR / "scripts" / "lib" / "operator_verb_manifest.sh"
_RECONCILIATION_LINT = _TEAM_DIR / "scripts" / "lint_operator_verb_manifest.py"
_API_PARITY_LINT = _TEAM_DIR / "scripts" / "lint_api_parity.py"

# The exact v2.20.0 verb set the task README names, plus the already-fronted
# operator scripts lint_api_parity.py's old hardcoded map covered.
_REQUIRED_VERBS = {
    "halt", "unhalt", "halt-global", "unhalt-global", "halt-after",
    "defer", "undefer", "deep-verify", "cold-build-now", "wake-now",
    "show", "show-queues", "why", "release-locks", "unwind-rc",
    "verify-rc-state",
    # already-fronted operator scripts
    "reset", "close", "wontdo", "delete", "intake", "set-version-ceiling",
    "switch-provider", "create-project", "add-project", "remove-project",
    "ship-rc",
    # v2.25.0 REST-parity census rows: closes the estate's remaining
    # census-row gaps for verbs the requirements document names.
    "rc-metrics", "agent-metrics", "cancel-rc",
    "release-state", "deep-verify-status",
    # archiving finished items: the CLI and its REST twin, registered together.
    "archive",
}


def _bash_verb_names() -> list[str]:
    result = subprocess.run(
        ["bash", "-c", f'source "{_BASH_READER}" && ovm_verb_names'],
        capture_output=True, text=True, check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def _bash_verb_flags(verb: str) -> str:
    result = subprocess.run(
        ["bash", "-c", f'source "{_BASH_READER}" && ovm_verb_flags {verb!r}'],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _copy_reconciliation_fixture(tmp_path: pathlib.Path) -> pathlib.Path:
    """Copy just the subtrees the reconciliation check reads into tmp_path.

    Returns the throwaway team_dir root. Mutating a file under this root
    never touches the checked-in tree.
    """
    fixture_team = tmp_path / "team"
    for sub in ("config", "scripts", "pgai_agent_kanban"):
        shutil.copytree(_TEAM_DIR / sub, fixture_team / sub)
    return fixture_team


def _run_reconciliation(team_dir: pathlib.Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_RECONCILIATION_LINT), "--team-dir", str(team_dir)],
        capture_output=True, text=True,
    )


# ---------------------------------------------------------------------------
# 1 -- the manifest exists, names every verb, and states its own parse rule
# ---------------------------------------------------------------------------


def test_default_manifest_path_resolves_to_checked_in_file():
    path = default_manifest_path()
    assert path.exists(), f"expected manifest at {path}"
    assert path.name == "operator_verbs.manifest"


def test_manifest_declares_every_required_verb():
    names = set(verb_names())
    missing = _REQUIRED_VERBS - names
    assert not missing, f"manifest is missing required verb(s): {sorted(missing)}"


def test_manifest_header_states_its_own_parse_rule():
    text = default_manifest_path().read_text(encoding="utf-8")
    assert "Parse rule:" in text, (
        "manifest header must state its own parse rule, per "
        "team/config/dashboard_windows.census's format discipline"
    )


def test_manifest_is_data_not_prose_every_line_is_comment_or_v_record():
    text = default_manifest_path().read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        first_field = line.split("\t", 1)[0]
        assert first_field == "V" or line.lstrip().startswith("#"), (
            f"line {lineno} is neither a comment nor a V record: {line!r}"
        )


def test_planned_verb_carries_placeholder_columns(tmp_path: pathlib.Path):
    """A 'planned' row's columns must all read as explicit placeholders
    rather than pretending a script/flags/REST twin exists. Verified against
    a synthetic fixture manifest rather than a live verb: a verb's planned
    status is a point-in-time thing, and release-locks itself -- the
    manifest's last remaining planned row -- has since shipped as an active
    verb and is asserted active below."""
    fixture = tmp_path / "fixture.manifest"
    fixture.write_text(
        "V\tsome-future-verb\tplanned\t-\t-\t-\t-\tABSENT\tABSENT\tABSENT\t-\tABSENT\n"
    )
    verbs = load_manifest(fixture)
    future_verb = verbs["some-future-verb"]
    assert future_verb.status == "planned"
    assert future_verb.script is None
    assert future_verb.flags == ()
    assert future_verb.mutates is None
    assert future_verb.rest_method == "ABSENT"
    assert future_verb.rest_route == "ABSENT"
    assert future_verb.rest_body_model == "ABSENT"
    assert future_verb.verb_class == "-"
    assert future_verb.schema_ref == "ABSENT"


def test_release_locks_verb_is_active_with_declared_script_and_flags():
    """release-locks has shipped as an active verb: the manifest row is now
    active with a real script and its declared flags, matching the script's
    own OPERATOR_VALID_FLAGS array."""
    verbs = load_manifest()
    release_locks = verbs["release-locks"]
    assert release_locks.status == "active"
    assert release_locks.script == "scripts/release-locks.sh"
    assert release_locks.flags == ("release", "force", "help")
    assert release_locks.mutates is True
    assert release_locks.idempotence == "idempotent"
    # The row names the verb's mutating --release mode (the body model is
    # what the flag-to-body parity check holds to the script's flags); the
    # read-only listing mode is GET /release-locks, the verb's second route,
    # registered in the census-exempt sidecar rather than as a second row.
    assert release_locks.rest_method == "POST"
    assert release_locks.rest_route == "/operations/release-locks"
    assert release_locks.rest_body_model == "ReleaseLocksBody"
    assert release_locks.schema_ref == "DualChannelShellEnvelope"


# ---------------------------------------------------------------------------
# 1b -- the two class/schema_ref columns, one dedicated test per reader
# ---------------------------------------------------------------------------


def test_python_reader_returns_class_and_schema_ref_for_a_fixture_row(
    tmp_path: pathlib.Path,
):
    """A fixture manifest (not the live file) proves the Python reader
    parses and exposes the two new columns for both an active read verb
    and a planned verb, independent of whatever the live manifest's own
    rows happen to say."""
    fixture = tmp_path / "fixture.manifest"
    fixture.write_text(
        "V\tfixture-read-verb\tactive\tscripts/does-not-exist.sh\thelp\tno"
        "\tread-only\tGET\t/fixture-read\tNONE\tread\tShellEnvelope\n"
        "V\tfixture-future-verb\tplanned\t-\t-\t-\t-\tABSENT\tABSENT\tABSENT"
        "\t-\tABSENT\n"
    )
    verbs = load_manifest(fixture)
    read_verb = verbs["fixture-read-verb"]
    assert read_verb.verb_class == "read"
    assert read_verb.schema_ref == "ShellEnvelope"

    future_verb = verbs["fixture-future-verb"]
    assert future_verb.verb_class == "-"
    assert future_verb.schema_ref == "ABSENT"


def test_bash_reader_returns_class_and_schema_ref_for_a_fixture_row(
    tmp_path: pathlib.Path,
):
    """Same proof as the Python-reader test above, against the bash reader's
    ovm_verb_class / ovm_verb_schema_ref wrappers and its own fixture
    manifest file."""
    fixture = tmp_path / "fixture.manifest"
    fixture.write_text(
        "V\tfixture-action-verb\tactive\tscripts/does-not-exist.sh\thelp\tyes"
        "\tidempotent\tPOST\t/operations/fixture-action\tFixtureBody\taction"
        "\tShellEnvelope\n"
    )
    class_result = subprocess.run(
        ["bash", "-c", f'source "{_BASH_READER}" && ovm_verb_class fixture-action-verb "{fixture}"'],
        capture_output=True, text=True, check=True,
    )
    schema_result = subprocess.run(
        ["bash", "-c", f'source "{_BASH_READER}" && ovm_verb_schema_ref fixture-action-verb "{fixture}"'],
        capture_output=True, text=True, check=True,
    )
    assert class_result.stdout.strip() == "action"
    assert schema_result.stdout.strip() == "ShellEnvelope"


def test_malformed_manifest_raises_format_error(tmp_path: pathlib.Path):
    bad = tmp_path / "bad.manifest"
    bad.write_text("V\thalt\tactive\n")  # too few fields
    with pytest.raises(ManifestFormatError):
        load_manifest(bad)


# ---------------------------------------------------------------------------
# 2 -- the bash and Python readers agree
# ---------------------------------------------------------------------------


def test_bash_and_python_readers_agree_on_verb_list():
    assert _bash_verb_names() == verb_names()


@pytest.mark.parametrize("verb", sorted(_REQUIRED_VERBS))
def test_bash_and_python_readers_agree_on_flags(verb: str):
    verbs = load_manifest()
    python_flags = "|".join(verbs[verb].flags) if verbs[verb].flags else "-"
    assert _bash_verb_flags(verb) == python_flags


# ---------------------------------------------------------------------------
# 3 -- the reconciliation check is green on the unmodified tree
# ---------------------------------------------------------------------------


def test_reconciliation_check_green_on_unmodified_tree():
    result = _run_reconciliation(_TEAM_DIR)
    assert result.returncode == 0, (
        f"reconciliation check failed against the unmodified tree:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "0 drift finding(s)" in result.stdout


def test_reconciliation_check_records_absent_rest_twins_without_failing(
    tmp_path: pathlib.Path,
):
    """An active verb with no REST twin yet is declared ABSENT in the manifest
    and must not make the check fail -- the marker is a documented gap, not
    drift. Every verb in the shipped tree has a twin today, so the scenario
    is built on a fixture copy: one extra active row fronting a real script
    (halt.sh, whose flags the row repeats) with every REST column ABSENT."""
    fixture_team = _copy_reconciliation_fixture(tmp_path)
    manifest = fixture_team / "config" / "operator_verbs.manifest"
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + "V\tfixture-absent-twin\tactive\tscripts/halt.sh\tproject|help\tyes"
        "\tidempotent\tABSENT\tABSENT\tABSENT\taction\tABSENT\tshell\n",
        encoding="utf-8",
    )
    absent_twins = [
        v.name for v in load_manifest(manifest).values()
        if v.rest_method == "ABSENT" and v.rest_route == "ABSENT"
    ]
    assert absent_twins == ["fixture-absent-twin"]
    result = _run_reconciliation(fixture_team)
    assert result.returncode == 0, (
        f"an ABSENT REST twin must not fail the reconciliation check:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# 4 -- a bogus flag makes the check fail, naming the script and flag
# ---------------------------------------------------------------------------


def test_bogus_flag_makes_reconciliation_check_fail_then_revert(tmp_path: pathlib.Path):
    fixture_team = _copy_reconciliation_fixture(tmp_path)
    halt_sh = fixture_team / "scripts" / "halt.sh"
    original = halt_sh.read_text(encoding="utf-8")
    assert "OPERATOR_VALID_FLAGS=(project help)" in original

    mutated = original.replace(
        "OPERATOR_VALID_FLAGS=(project help)",
        "OPERATOR_VALID_FLAGS=(project help frobnicate)",
    )
    assert mutated != original
    halt_sh.write_text(mutated, encoding="utf-8")

    result = _run_reconciliation(fixture_team)
    assert result.returncode != 0, "bogus flag must make the check fail"
    assert "halt.sh" in result.stdout
    assert "frobnicate" in result.stdout

    # Revert -- must go green again.
    halt_sh.write_text(original, encoding="utf-8")
    result = _run_reconciliation(fixture_team)
    assert result.returncode == 0, (
        f"reconciliation check did not go green after reverting:\n{result.stdout}"
    )


# ---------------------------------------------------------------------------
# 5 -- deleting a declared route makes the check fail, naming the route
# ---------------------------------------------------------------------------


def test_deleted_route_makes_reconciliation_check_fail_then_revert(tmp_path: pathlib.Path):
    fixture_team = _copy_reconciliation_fixture(tmp_path)
    operations_py = fixture_team / "pgai_agent_kanban" / "api" / "routers" / "operations.py"
    original = operations_py.read_text(encoding="utf-8")
    assert '"/unwind-rc"' in original

    mutated = original.replace('"/unwind-rc"', '"/unwind-rc-REMOVED"')
    assert mutated != original
    operations_py.write_text(mutated, encoding="utf-8")

    result = _run_reconciliation(fixture_team)
    assert result.returncode != 0, "deleting a declared route must make the check fail"
    assert "/operations/unwind-rc" in result.stdout

    # Revert -- must go green again.
    operations_py.write_text(original, encoding="utf-8")
    result = _run_reconciliation(fixture_team)
    assert result.returncode == 0, (
        f"reconciliation check did not go green after reverting:\n{result.stdout}"
    )


# ---------------------------------------------------------------------------
# 6 -- lint_api_parity.py still passes, reading pairings from the manifest
# ---------------------------------------------------------------------------


def test_lint_api_parity_passes_reading_from_manifest():
    result = subprocess.run(
        [sys.executable, str(_API_PARITY_LINT)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        f"lint_api_parity.py failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    # One flag-parity pair per active verb whose row names a real body
    # model -- derived from the manifest, never a literal count that drifts
    # each time a twin ships.
    expected_pairs = sum(
        1
        for v in load_manifest().values()
        if v.status == "active" and v.rest_body_model not in ("NONE", "ABSENT")
    )
    assert f"{expected_pairs} script/model pair(s) checked" in result.stdout


def test_extract_operator_valid_flags_matches_manifest_for_halt():
    flags = extract_operator_valid_flags(_TEAM_DIR / "scripts" / "halt.sh")
    assert set(flags) == set(load_manifest()["halt"].flags)


# ---------------------------------------------------------------------------
# 7 -- the drift refusal names the manifest it actually scanned
# ---------------------------------------------------------------------------


def _run_reconciliation_with_manifest(
    team_dir: pathlib.Path, manifest_path: pathlib.Path
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable, str(_RECONCILIATION_LINT),
            "--team-dir", str(team_dir),
            "--manifest", str(manifest_path),
        ],
        capture_output=True, text=True,
    )


def test_drift_refusal_names_supplied_manifest_path(tmp_path: pathlib.Path):
    """--manifest PATH against a drifted copy -- stored at a path distinct
    from the fixture's own default manifest -- names PATH, not the default,
    and the remedy's re-run command reproduces the --manifest invocation."""
    fixture_team = _copy_reconciliation_fixture(tmp_path)
    fixture_default_manifest = fixture_team / "config" / "operator_verbs.manifest"

    # A separate copy at a different path, exactly like the drifted.manifest
    # a reader points --manifest at in the field -- never the fixture's own
    # default file.
    drifted_manifest = tmp_path / "drifted.manifest"
    original = fixture_default_manifest.read_text(encoding="utf-8")
    assert "project|help" in original
    mutated = original.replace("project|help", "project|help|bogusflag", 1)
    assert mutated != original
    drifted_manifest.write_text(mutated, encoding="utf-8")

    result = _run_reconciliation_with_manifest(fixture_team, drifted_manifest)
    assert result.returncode != 0, "drifted manifest must make the check fail"
    assert str(drifted_manifest) in result.stderr, (
        f"message must name the manifest actually scanned:\n{result.stderr}"
    )
    assert f"--manifest {drifted_manifest}" in result.stderr, (
        f"remedy must reproduce the failing --manifest invocation:\n{result.stderr}"
    )
    assert str(fixture_default_manifest) not in result.stderr, (
        "message/remedy must not name the default manifest when a different "
        f"one was scanned:\n{result.stderr}"
    )


def test_drift_refusal_names_default_manifest_path_when_no_manifest_flag_given(
    tmp_path: pathlib.Path,
):
    """With no --manifest, the refusal still names the default path exactly
    as it did before the --manifest fix, and the remedy carries no
    --manifest flag."""
    fixture_team = _copy_reconciliation_fixture(tmp_path)
    halt_sh = fixture_team / "scripts" / "halt.sh"
    original = halt_sh.read_text(encoding="utf-8")
    mutated = original.replace(
        "OPERATOR_VALID_FLAGS=(project help)",
        "OPERATOR_VALID_FLAGS=(project help frobnicate)",
    )
    assert mutated != original
    halt_sh.write_text(mutated, encoding="utf-8")

    # The fixture team dir carries its own copy of the Python package, so
    # the subprocess's own default_manifest_path() resolves under
    # fixture_team, not the checked-in tree -- the same tree-finds-itself
    # resolution the reconciliation check relies on in production.
    expected_default_manifest = fixture_team / "config" / "operator_verbs.manifest"

    result = _run_reconciliation(fixture_team)  # no --manifest -> default path
    assert result.returncode != 0, "bogus flag must make the check fail"
    assert str(expected_default_manifest) in result.stderr, (
        f"message must still name the default manifest path:\n{result.stderr}"
    )
    assert "--manifest" not in result.stderr, (
        f"remedy must not add --manifest when none was supplied:\n{result.stderr}"
    )

    halt_sh.write_text(original, encoding="utf-8")


# ---------------------------------------------------------------------------
# 8 -- the impl column
# ---------------------------------------------------------------------------

_IMPL_FIXTURE_ROWS = (
    "V\tfixture-module-verb\tactive\tscripts/does-not-exist.sh\thelp\tno"
    "\tread-only\tGET\t/fixture-module\tNONE\tread\tShellEnvelope"
    "\tmodule:pgai_agent_kanban.ops.read.get_release_state\n"
    "V\tfixture-shell-verb\tactive\tscripts/does-not-exist.sh\thelp\tyes"
    "\tidempotent\tPOST\t/operations/fixture-shell\tNONE\taction"
    "\tDualChannelShellEnvelope\tshell\n"
    "V\tfixture-no-impl-verb\tactive\tscripts/does-not-exist.sh\thelp\tno"
    "\tread-only\tGET\t/fixture-no-impl\tNONE\tread\tShellEnvelope\n"
)


def test_every_manifest_row_carries_a_well_formed_impl_value():
    for verb in load_manifest().values():
        parsed = parse_impl(verb.impl)
        assert parsed.kind in ("module", "shell"), (verb.name, verb.impl)


def test_python_reader_exposes_impl_and_tolerates_a_row_without_it(
    tmp_path: pathlib.Path,
):
    fixture = tmp_path / "fixture.manifest"
    fixture.write_text(_IMPL_FIXTURE_ROWS)
    verbs = load_manifest(fixture)
    assert verbs["fixture-module-verb"].impl == (
        "module:pgai_agent_kanban.ops.read.get_release_state"
    )
    assert verbs["fixture-shell-verb"].impl == "shell"
    assert verbs["fixture-no-impl-verb"].impl is None


def test_bash_reader_exposes_impl(tmp_path: pathlib.Path):
    fixture = tmp_path / "fixture.manifest"
    fixture.write_text(_IMPL_FIXTURE_ROWS)

    def _impl(verb: str) -> str:
        return subprocess.run(
            ["bash", "-c", f'source "{_BASH_READER}" && ovm_verb_impl {verb} "{fixture}"'],
            capture_output=True, text=True, check=True,
        ).stdout.strip()

    assert _impl("fixture-module-verb") == "module:pgai_agent_kanban.ops.read.get_release_state"
    assert _impl("fixture-shell-verb") == "shell"
    assert _impl("fixture-no-impl-verb") == ""


def test_bash_and_python_readers_agree_on_impl_for_every_verb():
    for name, verb in load_manifest().items():
        bash_value = subprocess.run(
            ["bash", "-c", f'source "{_BASH_READER}" && ovm_verb_impl {name!r}'],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        assert bash_value == verb.impl, name


@pytest.mark.parametrize(
    "value, kind, module, function",
    [
        ("shell", "shell", None, None),
        ("-", "planned", None, None),
        (" shell ", "shell", None, None),
        ("module:pgai_agent_kanban.ops.write.halt", "module",
         "pgai_agent_kanban.ops.write", "halt"),
    ],
)
def test_parse_impl_accepts_its_grammar(value, kind, module, function):
    parsed = parse_impl(value)
    assert (parsed.kind, parsed.module, parsed.function) == (kind, module, function)


@pytest.mark.parametrize(
    "value",
    [None, "", "bash", "module:", "module:halt", "module:pkg.", "module:pkg..fn",
     "module:pkg.fn-name", "Shell"],
)
def test_parse_impl_rejects_values_outside_its_grammar(value):
    with pytest.raises(ValueError) as excinfo:
        parse_impl(value)
    assert "module:<dotted.module.function>" in str(excinfo.value)


def test_reconciliation_check_reports_malformed_and_missing_impl(
    tmp_path: pathlib.Path,
):
    """A copy of the real manifest with one impl value corrupted and one
    row's impl column dropped is reported for exactly those two verbs."""
    lines = []
    for line in default_manifest_path().read_text(encoding="utf-8").splitlines():
        fields = line.split("\t")
        if fields[0] == "V" and fields[1] == "halt":
            fields[-1] = "module:not-dotted"
        elif fields[0] == "V" and fields[1] == "unhalt":
            fields = fields[:-1]
        lines.append("\t".join(fields))
    manifest = tmp_path / "operator_verbs.manifest"
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = _run_reconciliation_with_manifest(_TEAM_DIR, manifest)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "2 drift finding(s)" in result.stdout
    assert "verb 'halt': impl 'module:not-dotted'" in result.stdout
    assert "verb 'unhalt': impl column is missing" in result.stdout


def test_reconciliation_check_reports_impl_that_does_not_fit_row_status(
    tmp_path: pathlib.Path,
):
    lines = []
    for line in default_manifest_path().read_text(encoding="utf-8").splitlines():
        fields = line.split("\t")
        if fields[0] == "V" and fields[1] == "halt":
            fields[-1] = "-"
        lines.append("\t".join(fields))
    manifest = tmp_path / "operator_verbs.manifest"
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = _run_reconciliation_with_manifest(_TEAM_DIR, manifest)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "verb 'halt': impl '-' is only valid on a planned row" in result.stdout
