"""
test_operator_verb_docs_generator.py
=====================================
Behavioral unit tests for the census-derived reference generator:

  team/scripts/generate_operator_verb_docs.py    (render + --check)
  team/config/operator_verbs.manifest            (the census it reads)
  docs/operator-commands.md                      (the verb reference it renders)
  docs/operator-api.md                           (the API reference it renders)
  docs/api/icd.json                              (route parameters it resolves)

Covers the acceptance criteria:

  1. A render writes a block covering every active verb the census
     declares (32 as of this suite's tree), and --check exits 0 against a
     freshly rendered copy of both docs.
  2. The generated API reference lists every census row's route, method,
     parameters, schema reference and read/action class.
  3. Adding a census row and regenerating makes the new verb appear in
     both references with no other edit -- no hand-added marker pair.
  4. Hand-written prose outside the section markers survives a
     regeneration byte-for-byte.
  5. --check fails, naming the verb, when a census row is changed without
     regenerating, and when a rendered block is hand-edited; it never
     writes.
  6. A doc missing its section marker pair is refused loud (a structural
     problem, not a drift finding) rather than silently skipped.
  7. The freshness check is wired into both gated runners through the
     quality-class wrapper.

All filesystem mutation happens on throwaway tmp_path copies of the
checked-in docs and census -- the checked-in tree is only ever read, never
written, by this test module. Every generator invocation passes BOTH
--doc and --api-doc so no test can fall back to the real documents.
"""

from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys

import pytest

# ---------------------------------------------------------------------------
# Ensure the package root is importable regardless of cwd.
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/tests/unit/ -> team/
_PKG_ROOT = _TEAM_DIR
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from pgai_agent_kanban.lib import operator_verb_manifest as ovm  # noqa: E402

_GENERATOR = _TEAM_DIR / "scripts" / "generate_operator_verb_docs.py"
_REAL_DOC = _TEAM_DIR.parent / "docs" / "operator-commands.md"
_REAL_API_DOC = _TEAM_DIR.parent / "docs" / "operator-api.md"
_REAL_ICD = _TEAM_DIR.parent / "docs" / "api" / "icd.json"
_REAL_MANIFEST = ovm.default_manifest_path()

_SECTION_BEGIN = "<!-- OPERATOR-VERBS-GENERATED:BEGIN section={name} -->"
_SECTION_END = "<!-- OPERATOR-VERBS-GENERATED:END section={name} -->"

# White-box import of the generator module itself (a script, not a package
# member) for the function-level tests below.
_spec = importlib.util.spec_from_file_location(
    "generate_operator_verb_docs_under_test", _GENERATOR
)
_gen = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules[_spec.name] = _gen  # dataclass() needs the module registered to resolve types
_spec.loader.exec_module(_gen)


@pytest.fixture
def docs(tmp_path: pathlib.Path) -> dict[str, pathlib.Path]:
    """Throwaway copies of both checked-in documents."""
    doc = tmp_path / "operator-commands.md"
    doc.write_text(_REAL_DOC.read_text(encoding="utf-8"), encoding="utf-8")
    api_doc = tmp_path / "operator-api.md"
    api_doc.write_text(_REAL_API_DOC.read_text(encoding="utf-8"), encoding="utf-8")
    return {"doc": doc, "api_doc": api_doc}


def _run(
    docs: dict[str, pathlib.Path], *extra: str, timeout: float = 120
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable, str(_GENERATOR),
            "--doc", str(docs["doc"]), "--api-doc", str(docs["api_doc"]),
            *extra,
        ],
        cwd=str(_TEAM_DIR),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _active_verbs() -> list[ovm.Verb]:
    return [v for v in ovm.load_manifest().values() if v.status == "active"]


def _section(content: str, name: str) -> str:
    begin = _SECTION_BEGIN.format(name=name)
    end = _SECTION_END.format(name=name)
    start = content.index(begin)
    stop = content.index(end, start) + len(end)
    return content[start:stop]


def _outside_section(content: str, name: str) -> tuple[str, str]:
    """The text before and after the named section -- the hand-written part."""
    begin = _SECTION_BEGIN.format(name=name)
    end = _SECTION_END.format(name=name)
    start = content.index(begin)
    stop = content.index(end, start) + len(end)
    return content[:start], content[stop:]


# ---------------------------------------------------------------------------
# 1 -- render covers every active verb; --check passes on a fresh render
# ---------------------------------------------------------------------------


def test_render_covers_every_active_verb(docs: dict[str, pathlib.Path]) -> None:
    active_names = [v.name for v in _active_verbs()]
    assert len(active_names) == 33, (
        "this suite's expected count has drifted from the census's actual "
        f"active-verb count ({len(active_names)}); update the assertion to match"
    )

    result = _run(docs)
    assert result.returncode == 0, result.stdout + result.stderr
    # The checked-in docs may already be up to date (this test copies them
    # verbatim before rendering), so either the "wrote" or the "already up
    # to date" branch is acceptable -- both name the full verb count.
    assert (
        f"{len(active_names)} verb(s) rendered" in result.stdout
        or f"{len(active_names)} verb(s):" in result.stdout
    )

    content = docs["doc"].read_text(encoding="utf-8")
    section = _section(content, "verb-reference")
    for name in active_names:
        assert f"<!-- OPERATOR-VERBS-GENERATED:BEGIN verb={name} -->" in section
        assert f"<!-- OPERATOR-VERBS-GENERATED:END verb={name} -->" in section


def test_check_passes_on_freshly_rendered_files(docs: dict[str, pathlib.Path]) -> None:
    render = _run(docs)
    assert render.returncode == 0, render.stdout + render.stderr

    check = _run(docs, "--check")
    assert check.returncode == 0, check.stdout + check.stderr
    assert "OK" in check.stdout


def test_checked_in_docs_are_fresh() -> None:
    """The tree's own artifacts pass the gate the runners carry -- a
    regeneration is byte-identical to what is checked in."""
    result = subprocess.run(
        [sys.executable, str(_GENERATOR), "--check"],
        cwd=str(_TEAM_DIR), capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


# ---------------------------------------------------------------------------
# 2 -- the API reference lists every census row's route, method,
#      parameters, schema reference and class
# ---------------------------------------------------------------------------


def test_api_reference_lists_every_census_row(docs: dict[str, pathlib.Path]) -> None:
    render = _run(docs)
    assert render.returncode == 0, render.stdout + render.stderr

    section = _section(docs["api_doc"].read_text(encoding="utf-8"), "api-reference")
    rows = {
        line.split("|")[1].strip().strip("`"): line
        for line in section.splitlines()
        if line.startswith("| `")
    }
    for verb in _active_verbs():
        if verb.rest_route == "ABSENT":
            assert f"- `{verb.name}`" in section, f"{verb.name} (no twin) missing"
            continue
        assert verb.name in rows, f"no API reference row for census verb {verb.name!r}"
        row = rows[verb.name]
        assert f"| {verb.verb_class} |" in row, row
        assert f"`{verb.rest_method}`" in row, row
        assert f"`{verb.rest_route}`" in row, row
        assert f"`{verb.schema_ref}`" in row, row
        assert "not in `docs/api/icd.json`" not in row, (
            f"{verb.name}'s route is missing from the checked-in ICD: {row}"
        )


def test_api_row_parameters_come_from_the_icd() -> None:
    icd = _gen.load_icd(_REAL_ICD)
    params = _gen.resolve_route_parameters(icd, "GET", "/show")
    assert params.ok
    assert set(params.names) == {"project", "key", "file"}
    assert params.required == {"project", "key"}
    # Required first, then optional.
    assert params.names.index("project") < params.names.index("file")
    assert params.names.index("key") < params.names.index("file")

    body = _gen.resolve_route_parameters(icd, "POST", "/operations/halt-after")
    assert body.ok
    assert {"project", "key", "until", "dry_run"} <= set(body.names)
    # Every action-route body field is declared optional in the schema so a
    # missing route-required one is refused dual-channel rather than by the
    # framework; the ICD therefore reports no required body field here, and
    # with nothing required the names fall into plain alphabetical order.
    assert body.required == frozenset()
    assert body.names == tuple(sorted(body.names))

    missing = _gen.resolve_route_parameters(icd, "POST", "/operations/does-not-exist")
    assert missing.ok is False
    assert "not in the ICD" in missing.reason
    assert "generate-icd.sh" in _gen._parameters_cell(missing)


# ---------------------------------------------------------------------------
# 3 -- adding a census row is the only edit a new verb needs
# ---------------------------------------------------------------------------


def _manifest_with_extra_row(tmp_path: pathlib.Path) -> pathlib.Path:
    manifest = tmp_path / "operator_verbs.manifest"
    text = _REAL_MANIFEST.read_text(encoding="utf-8")
    row = "\t".join([
        "V", "zz-synthetic-verb", "active", "scripts/halt.sh", "project|help",
        "yes", "idempotent", "POST", "/operations/zz-synthetic", "ZzSyntheticBody",
        "action", "ShellEnvelope",
    ])
    manifest.write_text(text.rstrip("\n") + "\n" + row + "\n", encoding="utf-8")
    return manifest


def test_new_census_row_appears_in_both_references_with_no_other_edit(
    tmp_path: pathlib.Path, docs: dict[str, pathlib.Path]
) -> None:
    manifest = _manifest_with_extra_row(tmp_path)
    for path in docs.values():
        assert "zz-synthetic-verb" not in path.read_text(encoding="utf-8")

    render = _run(docs, "--manifest", str(manifest))
    assert render.returncode == 0, render.stdout + render.stderr

    verb_section = _section(docs["doc"].read_text(encoding="utf-8"), "verb-reference")
    assert "<!-- OPERATOR-VERBS-GENERATED:BEGIN verb=zz-synthetic-verb -->" in verb_section
    assert "#### `zz-synthetic-verb`" in verb_section
    assert "- **REST twin:** `POST /operations/zz-synthetic`" in verb_section

    api_section = _section(docs["api_doc"].read_text(encoding="utf-8"), "api-reference")
    row = [l for l in api_section.splitlines() if l.startswith("| `zz-synthetic-verb`")]
    assert len(row) == 1, api_section
    assert "`/operations/zz-synthetic`" in row[0]
    assert "`ZzSyntheticBody`" in row[0]
    # A route the ICD does not carry says so and names the regeneration
    # command instead of inventing a parameter list.
    assert "not in `docs/api/icd.json`" in row[0]
    assert "generate-icd.sh" in row[0]

    check = _run(docs, "--check", "--manifest", str(manifest))
    assert check.returncode == 0, check.stdout + check.stderr


# ---------------------------------------------------------------------------
# 4 -- hand-written prose outside the markers survives unchanged
# ---------------------------------------------------------------------------


def test_prose_outside_the_markers_survives_regeneration(
    docs: dict[str, pathlib.Path]
) -> None:
    sentinel_before = "\nHAND-WRITTEN SENTINEL BEFORE THE GENERATED SECTION\n\n"
    sentinel_after = "\n\nHAND-WRITTEN SENTINEL AFTER THE GENERATED SECTION\n"
    for path, name in ((docs["doc"], "verb-reference"), (docs["api_doc"], "api-reference")):
        content = path.read_text(encoding="utf-8")
        begin = _SECTION_BEGIN.format(name=name)
        end = _SECTION_END.format(name=name)
        content = content.replace(begin, sentinel_before + begin, 1)
        content = content.replace(end, end + sentinel_after, 1)
        path.write_text(content, encoding="utf-8")

    before = {
        n: _outside_section(p.read_text(encoding="utf-8"), n)
        for p, n in ((docs["doc"], "verb-reference"), (docs["api_doc"], "api-reference"))
    }

    render = _run(docs)
    assert render.returncode == 0, render.stdout + render.stderr

    for path, name in ((docs["doc"], "verb-reference"), (docs["api_doc"], "api-reference")):
        after = _outside_section(path.read_text(encoding="utf-8"), name)
        assert after == before[name], f"hand-written prose around {name} changed"
        assert sentinel_before.strip() in after[0]
        assert sentinel_after.strip() in after[1]


# ---------------------------------------------------------------------------
# 5 -- drift is caught, named, refused, and nothing is written
# ---------------------------------------------------------------------------


def test_check_fails_when_a_census_row_changes_without_regenerating(
    tmp_path: pathlib.Path, docs: dict[str, pathlib.Path]
) -> None:
    render = _run(docs)
    assert render.returncode == 0, render.stdout + render.stderr

    manifest = tmp_path / "operator_verbs.manifest"
    text = _REAL_MANIFEST.read_text(encoding="utf-8")
    halt_row = next(l for l in text.splitlines() if l.startswith("V\thalt\t"))
    assert "\tidempotent\t" in halt_row
    manifest.write_text(
        text.replace(halt_row, halt_row.replace("\tidempotent\t", "\tnon-idempotent\t", 1), 1),
        encoding="utf-8",
    )
    snapshot = {k: p.read_text(encoding="utf-8") for k, p in docs.items()}

    check = _run(docs, "--check", "--manifest", str(manifest))
    assert check.returncode == 1, check.stdout + check.stderr
    assert "verb='halt'" in check.stderr
    assert "error:" in check.stderr
    assert "message:" in check.stderr
    assert "remedy:" in check.stderr
    for k, p in docs.items():
        assert p.read_text(encoding="utf-8") == snapshot[k], "--check must never write"


def test_check_fails_when_a_census_row_is_added_without_regenerating(
    tmp_path: pathlib.Path, docs: dict[str, pathlib.Path]
) -> None:
    render = _run(docs)
    assert render.returncode == 0, render.stdout + render.stderr
    manifest = _manifest_with_extra_row(tmp_path)

    check = _run(docs, "--check", "--manifest", str(manifest))
    assert check.returncode == 1, check.stdout + check.stderr
    assert "verb='zz-synthetic-verb' has no block" in check.stderr
    assert "api-reference" in check.stderr
    assert "zz-synthetic-verb" in check.stderr


def test_check_reports_drift_on_hand_edited_block_and_writes_nothing(
    docs: dict[str, pathlib.Path],
) -> None:
    render = _run(docs)
    assert render.returncode == 0, render.stdout + render.stderr

    original = docs["doc"].read_text(encoding="utf-8")
    end_marker = "<!-- OPERATOR-VERBS-GENERATED:END verb=halt -->"
    needle = "  --help, -h         Show this help and exit\n```\n" + end_marker
    assert needle in original, "halt's rendered block shape changed -- update this test's literal"
    corrupted = original.replace(
        needle,
        "  --tamper-flag      injected by test, not a real flag\n" + needle,
        1,
    )
    assert corrupted != original
    docs["doc"].write_text(corrupted, encoding="utf-8")

    check = _run(docs, "--check")
    assert check.returncode != 0
    combined = check.stdout + check.stderr
    assert "halt" in combined
    assert "OPERATOR-VERBS-GENERATED" in combined
    assert "error:" in check.stderr
    assert "message:" in check.stderr
    assert "remedy:" in check.stderr

    after = docs["doc"].read_text(encoding="utf-8")
    assert after == corrupted, "--check must never write to the doc it is checking"


def test_check_reports_hand_edited_api_row(docs: dict[str, pathlib.Path]) -> None:
    render = _run(docs)
    assert render.returncode == 0, render.stdout + render.stderr

    original = docs["api_doc"].read_text(encoding="utf-8")
    needle = "| `halt` | action | `POST` | `/operations/halt` |"
    assert needle in original
    docs["api_doc"].write_text(
        original.replace(needle, "| `halt` | read | `POST` | `/operations/halt` |", 1),
        encoding="utf-8",
    )

    check = _run(docs, "--check")
    assert check.returncode == 1
    assert "api-reference" in check.stderr
    assert "rows differing: halt" in check.stderr


# ---------------------------------------------------------------------------
# 4 (cont.) -- the rendered usage text is the verb's own --help output,
#      verbatim, not a transcription of the census's flags column
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "verb_name,script",
    [
        ("halt", "halt.sh"),
        ("show", "show.sh"),
        ("why", "why.sh"),
    ],
)
def test_rendered_block_contains_verbs_own_help_output_verbatim(
    docs: dict[str, pathlib.Path], verb_name: str, script: str
) -> None:
    render = _run(docs)
    assert render.returncode == 0, render.stdout + render.stderr

    help_result = subprocess.run(
        ["bash", str(_TEAM_DIR / "scripts" / script), "--help"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert help_result.returncode == 0
    help_text = (help_result.stdout or help_result.stderr).strip("\n")
    assert help_text, f"{script} --help produced no output on stdout or stderr"

    content = docs["doc"].read_text(encoding="utf-8")
    begin = f"<!-- OPERATOR-VERBS-GENERATED:BEGIN verb={verb_name} -->"
    end = f"<!-- OPERATOR-VERBS-GENERATED:END verb={verb_name} -->"
    start_idx = content.index(begin)
    stop_idx = content.index(end, start_idx)
    block = content[start_idx:stop_idx]
    assert help_text in block, (
        f"{verb_name}'s rendered block does not contain {script}'s own "
        "--help output verbatim"
    )


# ---------------------------------------------------------------------------
# 6 -- a doc missing its section marker pair is a structural refusal
# ---------------------------------------------------------------------------


def test_render_reports_missing_section_markers_as_usage_error(
    docs: dict[str, pathlib.Path]
) -> None:
    docs["doc"].write_text("# empty doc, no markers\n", encoding="utf-8")

    result = _run(docs)
    assert result.returncode == 2
    assert "marker" in result.stderr.lower()
    assert "section=verb-reference" in result.stderr
    # Nothing should have been written to a doc the render refused to touch.
    assert docs["doc"].read_text(encoding="utf-8") == "# empty doc, no markers\n"
    assert docs["api_doc"].read_text(encoding="utf-8") == _REAL_API_DOC.read_text(encoding="utf-8")


def test_check_reports_missing_section_markers_as_usage_error(
    docs: dict[str, pathlib.Path]
) -> None:
    docs["api_doc"].write_text("# empty doc, no markers\n", encoding="utf-8")

    result = _run(docs, "--check")
    assert result.returncode == 2
    assert "marker" in result.stderr.lower()
    assert "section=api-reference" in result.stderr


def test_missing_icd_is_a_usage_error(
    tmp_path: pathlib.Path, docs: dict[str, pathlib.Path]
) -> None:
    result = _run(docs, "--icd", str(tmp_path / "absent.json"))
    assert result.returncode == 2
    assert "ICD" in result.stderr
    assert "generate-icd.sh" in result.stderr


# ---------------------------------------------------------------------------
# 7 -- the freshness check is wired into both gated runners
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("runner", ["run-unit-tests.sh", "run-integration-tests.sh"])
def test_gated_runner_carries_the_freshness_gate(runner: str) -> None:
    text = (_TEAM_DIR / "scripts" / runner).read_text(encoding="utf-8")
    assert 'gated_runner_run_quality_lint "operator_verb_docs_freshness"' in text, (
        f"{runner} must run generate_operator_verb_docs.py --check through "
        "gated_runner_run_quality_lint"
    )
    assert "generate_operator_verb_docs.py --check" in text


# ---------------------------------------------------------------------------
# White-box: capture_help / render_verb_block never fall back to the
# census's flags column when a verb's --help cannot be captured.
# ---------------------------------------------------------------------------


def test_capture_help_reports_failure_reason_for_missing_script(tmp_path: pathlib.Path) -> None:
    fake_verb = ovm.Verb(
        name="does-not-exist",
        status="active",
        script="scripts/does-not-exist.sh",
        flags=("project", "help"),
        mutates=True,
        idempotence="idempotent",
        rest_method="ABSENT",
        rest_route="ABSENT",
        rest_body_model="ABSENT",
    )
    capture = _gen.capture_help(fake_verb, _TEAM_DIR)
    assert capture.ok is False
    assert capture.text is None
    assert "not found" in capture.reason


def test_render_verb_block_on_failed_capture_does_not_leak_manifest_flags() -> None:
    fake_verb = ovm.Verb(
        name="does-not-exist",
        status="active",
        script="scripts/does-not-exist.sh",
        flags=("project", "some-secret-flag-name", "help"),
        mutates=True,
        idempotence="idempotent",
        rest_method="ABSENT",
        rest_route="ABSENT",
        rest_body_model="ABSENT",
    )
    failed_capture = _gen.HelpCapture(False, None, "script not found at /nonexistent")
    block = _gen.render_verb_block(fake_verb, failed_capture)
    assert "could not be captured" in block
    assert "- **REST twin:** none" in block
    assert "some-secret-flag-name" not in block, (
        "a failed --help capture must not fall back to printing the "
        "census's flags column as a substitute"
    )


def test_api_reference_lists_a_verb_without_a_twin() -> None:
    no_twin = ovm.Verb(
        name="twinless", status="active", script="scripts/twinless.sh", flags=(),
        mutates=False, idempotence="read-only", rest_method="ABSENT",
        rest_route="ABSENT", rest_body_model="ABSENT", verb_class="read",
        schema_ref="ABSENT",
    )
    section = _gen.render_api_reference_section([no_twin], {"paths": {}})
    assert "| `twinless`" not in section
    assert "- `twinless` (`scripts/twinless.sh`, class read)" in section


def test_rendered_references_carry_the_census_impl_value() -> None:
    module_verb = ovm.Verb(
        name="moduled", status="active", script="scripts/moduled.sh", flags=(),
        mutates=False, idempotence="read-only", rest_method="GET",
        rest_route="/moduled", rest_body_model="NONE", verb_class="read",
        schema_ref="ShellEnvelope", impl="module:pkg.mod.fn",
    )
    capture = _gen.HelpCapture(True, "Usage: moduled.sh", None)
    block = _gen.render_verb_block(module_verb, capture)
    assert "- **Implementation:** `module:pkg.mod.fn`" in block

    row = _gen.render_api_row(module_verb, _gen.RouteParameters(True))
    assert row.endswith("| `scripts/moduled.sh` | `module:pkg.mod.fn` |")

    section = _gen.render_api_reference_section([module_verb], {"paths": {}})
    assert "| Script | Implementation |" in section


def test_capture_help_succeeds_for_a_real_active_verb() -> None:
    verbs = ovm.load_manifest()
    verb = verbs["halt"]
    capture = _gen.capture_help(verb, _TEAM_DIR)
    assert capture.ok is True
    assert capture.reason is None
    assert "Usage:" in capture.text
