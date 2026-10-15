"""
test_lint_doc_command_truth_keys_panes.py
==========================================
Tests for the two doc-truth lint extensions in
team/scripts/lint_doc_command_truth.py: config-key parity (every documented
kanban.cfg key reconciled against config_loader.sh's Key Registry) and
dashboard window/pane parity (every documented window/pane claim reconciled
against team/config/dashboard_windows.census).

Tests cover:

  1. **Unknown-key citation is flagged**: a seeded document naming a
     configuration key the loader does not accept produces an UNKNOWN
     CONFIG KEY finding naming the document, the line, and the key.

  2. **Default-mismatch citation is flagged, distinctly**: a seeded document
     misstating an accepted key's default produces a CONFIG DEFAULT
     MISMATCH finding, visibly different in shape from the unknown-key
     finding, and both are demonstrated side by side.

  3. **Removing each seed makes its finding disappear.**

  4. **A loader key no document mentions is reported and never suppressed**
     (UNDOCUMENTED CONFIG KEY) — this direction is a worklist entry, not a
     correctness bug, so it appears even in isolation.

  4b. **The project.cfg widen fix's own acceptance criterion**:
      against the REAL shipped registries, a genuinely-invented project.cfg
      key is flagged UNKNOWN CONFIG KEY while a real, correctly-documented
      project.cfg key in the same section is not.

  4c. **The "N/A" registry default** exempts a per-installation identity
      field from the default-value comparison, and a contrast test proves
      the marker is doing real work: without it, the identical example
      value produces a false CONFIG DEFAULT MISMATCH.

  5. **An unknown dashboard pane is flagged**: a seeded document describing
     a pane the census does not declare for that window produces an
     UNKNOWN DASHBOARD PANE finding naming the document and the claimed
     window/pane; removing the seed clears it.

  6. **A window name mismatch and an unknown window index are both
     flagged**, distinctly from the pane-level finding.

  7. **A census window no scanned document describes is reported and never
     suppressed** (UNDOCUMENTED DASHBOARD WINDOW).

  8. **The two new CENSUS-ANCHOR tokens resolve** in both the lint source
     and docs/blocking-site-census.md's Locator Index, with matching class
     tokens.

  9. **Real-tree runs** for both checks complete without a Python-level
     crash and scan a non-zero number of citations — the live regression
     guard proving the scan sets are not accidentally empty.

 10. **The Key Registry parser reads the real config_loader.sh** — a
     structural read, not a hand-copied key list — and the real census
     resolves to the documented 15 window ids / 16 rows.

All tests either use pytest's ``tmp_path`` for seeded fixture content or
point ``--config-loader``/``--census`` at small, isolated fixture files —
no bare ``/tmp`` paths, no mutation of live kanban state. The real-tree
regression tests use ``--repo-root`` against this checkout's own tree
(read-only) exactly like the sibling existence-check test module does.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Load lint_doc_command_truth.py as a module (same technique as the sibling
# existence-check test module, for the same dataclass/annotation reason).
# ---------------------------------------------------------------------------
_THIS_FILE = Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/
_REPO_ROOT = _TEAM_DIR.parent  # repo root
_LINT_SCRIPT = _TEAM_DIR / "scripts" / "lint_doc_command_truth.py"
_REAL_CONFIG_LOADER = _TEAM_DIR / "scripts" / "lib" / "config_loader.sh"
_CENSUS_DOC = _REPO_ROOT / "docs" / "blocking-site-census.md"


def _import_lint_module():
    spec = importlib.util.spec_from_file_location("lint_doc_command_truth_keys_panes", _LINT_SCRIPT)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load spec from {_LINT_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_lint = _import_lint_module()


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _fixture_empty_project_registry(tmp_path: Path) -> Path:
    """An isolated project.cfg Key Registry declaring zero keys.

    Used as ``_run``'s default ``--project-key-registry`` so the pre-
    existing kanban.cfg-only tests below do not silently pick up the real
    tree's ``project_paths.sh`` registry (and its dozen-plus entries)
    merely because ``--repo-root`` points at this checkout for --help
    sandboxing. Tests that specifically exercise the project.cfg widen fix
    pass their own fixture via ``project_key_registry=``.
    """
    body = (
        "#!/usr/bin/env bash\n"
        "# fixture project_paths.sh with no declared keys — isolates the\n"
        "# kanban.cfg-only tests below from the real project.cfg registry.\n"
        "#\n"
        "# Key Registry\n"
        "# ------------\n"
        "\n"
        "true\n"
    )
    return _write(tmp_path / "fixture_empty_project_paths.sh", body)


def _fixture_project_registry(tmp_path: Path) -> Path:
    """A minimal, isolated project.cfg Key Registry — same structural shape
    as the real project_paths.sh's block. Declares one key with a real
    canonical default (``workflow_type``) and one per-installation identity
    field marked ``N/A`` (``project_name``) so both the widen-fix's unknown-
    key direction and its default_checked=False mechanism are testable in
    isolation from the real tree's much larger registry."""
    body = (
        "#!/usr/bin/env bash\n"
        "# fixture project_paths.sh for key-parity tests\n"
        "#\n"
        "# Key Registry\n"
        "# ------------\n"
        "# [project]\n"
        "#   project_name       OPTIONAL  N/A\n"
        "#   workflow_type      OPTIONAL  release\n"
        "\n"
        "true\n"
    )
    return _write(tmp_path / "fixture_project_paths.sh", body)


def _fixture_config_loader(tmp_path: Path) -> Path:
    """A minimal, isolated Key Registry — same structural shape as the real
    config_loader.sh's block, small enough that seeded findings are
    unambiguous. Deliberately declares only two keys so the undocumented-
    key direction is easy to assert on in isolation."""
    body = (
        "#!/usr/bin/env bash\n"
        "# fixture config_loader.sh for key-parity tests\n"
        "#\n"
        "# Key Registry\n"
        "# ------------\n"
        "# [dashboard]\n"
        "#   rows_per_column         OPTIONAL  21\n"
        "#\n"
        "# [wake]\n"
        "#   max_tasks_per_wake      OPTIONAL  5\n"
        "\n"
        "true\n"
    )
    return _write(tmp_path / "fixture_config_loader.sh", body)


def _fixture_census(tmp_path: Path) -> Path:
    """A minimal, isolated census — two windows, deliberately small so the
    undocumented-window direction is unambiguous in tests."""
    rows = [
        "\t".join(["W", "0", "main", "-", "1"]),
        "\t".join(["P", "0", "-", "0", "single", "header.sh", "header.sh --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH"]),
        "\t".join(["W", "1", "second", "-", "1"]),
        "\t".join(["P", "1", "-", "0", "single", "second.sh", "second.sh --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH"]),
    ]
    body = "# fixture census\n" + "\n".join(rows) + "\n"
    return _write(tmp_path / "fixture.census", body)


def _run(
    doc: Path,
    config_loader: Path,
    census: Path,
    tmp_path: Path,
    extra: list[str] | None = None,
    project_key_registry: Path | None = None,
):
    if project_key_registry is None:
        project_key_registry = _fixture_empty_project_registry(tmp_path)
    args = [
        "--repo-root",
        str(_REPO_ROOT),
        "--doc",
        str(doc),
        "--config-loader",
        str(config_loader),
        "--project-key-registry",
        str(project_key_registry),
        "--census",
        str(census),
        "--help-timeout",
        "5",
    ]
    if extra:
        args.extend(extra)
    return _lint.main(args)


# ---------------------------------------------------------------------------
# Test 1: unknown-key citation is flagged
# ---------------------------------------------------------------------------


def test_unknown_key_citation_is_flagged(tmp_path, capsys) -> None:
    config_loader = _fixture_config_loader(tmp_path)
    census = _fixture_census(tmp_path)
    doc = _write(
        tmp_path / "fake.md",
        "# Fake doc\n\n```ini\n[dashboard]\ntotally_invented_key = 5\n```\n",
    )

    result = _run(doc, config_loader, census, tmp_path)
    out = capsys.readouterr().out

    assert result == 1
    assert "UNKNOWN CONFIG KEY: [dashboard] totally_invented_key" in out, (
        f"Finding does not name the unknown key.\nstdout: {out!r}"
    )
    assert "fake.md:5" in out, f"Finding does not name the doc:line.\nstdout: {out!r}"


# ---------------------------------------------------------------------------
# Test 2: default-mismatch citation is flagged, distinctly
# ---------------------------------------------------------------------------


def test_default_mismatch_and_unknown_key_are_distinct(tmp_path, capsys) -> None:
    config_loader = _fixture_config_loader(tmp_path)
    census = _fixture_census(tmp_path)
    doc = _write(
        tmp_path / "fake.md",
        "# Fake doc\n\n"
        "```ini\n"
        "[dashboard]\n"
        "rows_per_column = 99\n"
        "totally_invented_key = 5\n"
        "```\n",
    )

    result = _run(doc, config_loader, census, tmp_path)
    out = capsys.readouterr().out

    assert result == 1
    assert "CONFIG DEFAULT MISMATCH: [dashboard] rows_per_column" in out, (
        f"Finding does not name the default mismatch.\nstdout: {out!r}"
    )
    assert "documented default: '99'" in out
    assert "loader default:     '21'" in out
    assert "UNKNOWN CONFIG KEY: [dashboard] totally_invented_key" in out
    # The two finding headers are lexically distinct labels, not the same string.
    assert "CONFIG DEFAULT MISMATCH:" != "UNKNOWN CONFIG KEY:"


# ---------------------------------------------------------------------------
# Test 3: removing each seed makes its finding disappear
# ---------------------------------------------------------------------------


def test_removing_unknown_key_seed_clears_its_finding(tmp_path, capsys) -> None:
    config_loader = _fixture_config_loader(tmp_path)
    census = _fixture_census(tmp_path)
    doc_path = tmp_path / "fake.md"

    _write(
        doc_path,
        "# Fake doc\n\n"
        "```ini\n"
        "[dashboard]\n"
        "rows_per_column = 99\n"
        "totally_invented_key = 5\n"
        "```\n",
    )
    result_before = _run(doc_path, config_loader, census, tmp_path)
    out_before = capsys.readouterr().out
    assert result_before == 1
    assert "UNKNOWN CONFIG KEY: [dashboard] totally_invented_key" in out_before
    assert "CONFIG DEFAULT MISMATCH: [dashboard] rows_per_column" in out_before

    # Remove only the unknown-key seed; keep the default-mismatch seed.
    _write(
        doc_path,
        "# Fake doc\n\n```ini\n[dashboard]\nrows_per_column = 99\n```\n",
    )
    result_after = _run(doc_path, config_loader, census, tmp_path)
    out_after = capsys.readouterr().out

    assert result_after == 1, "The remaining default-mismatch seed must still fail the lint."
    assert "UNKNOWN CONFIG KEY" not in out_after, (
        f"Removing the unknown-key citation must clear its finding.\nstdout: {out_after!r}"
    )
    assert "CONFIG DEFAULT MISMATCH: [dashboard] rows_per_column" in out_after


# ---------------------------------------------------------------------------
# Test 4: undocumented registry key is reported and never suppressed
# ---------------------------------------------------------------------------


def test_undocumented_key_is_reported_and_never_suppressed(tmp_path, capsys) -> None:
    config_loader = _fixture_config_loader(tmp_path)
    census = _fixture_census(tmp_path)
    # A document that correctly cites the ONE key the fixture registry
    # declares under [dashboard] — [wake] max_tasks_per_wake is never
    # mentioned anywhere, so it must surface as undocumented regardless.
    doc = _write(
        tmp_path / "fake.md",
        "# Fake doc\n\n```ini\n[dashboard]\nrows_per_column = 21\n```\n",
    )

    result = _run(doc, config_loader, census, tmp_path)
    out = capsys.readouterr().out

    assert result == 1, "An undocumented registry key must still fail the lint (never suppressed)."
    assert "UNDOCUMENTED CONFIG KEY: [wake] max_tasks_per_wake" in out
    assert "UNKNOWN CONFIG KEY" not in out
    assert "CONFIG DEFAULT MISMATCH" not in out


# ---------------------------------------------------------------------------
# Test 4d: attribution is per-key, not a single hardcoded source. A key
# declared only in config_loader.sh's registry and a key declared only in
# project_paths.sh's registry must each name their OWN source registry in
# their finding's Cause line, not the other file, so a reader chasing
# either finding lands in the file that actually declares the key.
# ---------------------------------------------------------------------------


def test_undocumented_key_findings_name_their_own_source_registry(tmp_path: Path, capsys) -> None:
    config_loader = _fixture_config_loader(tmp_path)
    project_key_registry = _fixture_project_registry(tmp_path)
    census = _fixture_census(tmp_path)
    # A document citing neither registry's keys, so both the
    # config_loader.sh-declared key and the project_paths.sh-declared key
    # surface as UNDOCUMENTED CONFIG KEY findings.
    doc = _write(tmp_path / "empty.md", "# Empty doc\n\nNo config keys cited here.\n")

    result = _run(
        doc, config_loader, census, tmp_path, project_key_registry=project_key_registry
    )
    out = capsys.readouterr().out
    assert result == 1

    def _finding_block(header: str) -> str:
        start = out.index(header)
        end = out.find("\n\n", start)
        return out[start : end if end != -1 else len(out)]

    config_loader_finding = _finding_block("UNDOCUMENTED CONFIG KEY: [dashboard] rows_per_column")
    assert str(config_loader) in config_loader_finding, (
        f"the config_loader.sh-declared key's finding must name its own source file.\n"
        f"{config_loader_finding!r}"
    )
    assert str(project_key_registry) not in config_loader_finding

    project_registry_finding = _finding_block("UNDOCUMENTED CONFIG KEY: [project] workflow_type")
    assert str(project_key_registry) in project_registry_finding, (
        f"the project_paths.sh-declared key's finding must name its own source file.\n"
        f"{project_registry_finding!r}"
    )
    assert str(config_loader) not in project_registry_finding


# ---------------------------------------------------------------------------
# Test 4b: the project.cfg widen fix's acceptance criterion, exercised
# against the REAL shipped registries (config_loader.sh's kanban.cfg
# registry merged with project_paths.sh's project.cfg registry), not an
# isolated fixture. A genuinely-invented key under a real project.cfg
# section is still flagged; a real, correctly-documented project.cfg key in
# the same section is not — the exact discrimination that was previously
# broken (roughly thirty real project.cfg keys called unknown).
# ---------------------------------------------------------------------------


def test_genuinely_unknown_key_flags_but_real_project_cfg_key_does_not(tmp_path, capsys) -> None:
    doc = _write(
        tmp_path / "scratch_project_cfg.md",
        "# Scratch project.cfg doc\n\n"
        "```ini\n"
        "[project]\n"
        "workflow_type = release\n"
        "totally_invented_project_key = nonsense\n"
        "```\n",
    )

    # --key-doc scopes the key-parity check to exactly this document while
    # leaving --config-loader/--project-key-registry at their defaults, so
    # this run reconciles against the REAL, shipped merged registry.
    result = _lint.main(
        [
            "--repo-root",
            str(_REPO_ROOT),
            "--key-doc",
            str(doc),
            "--help-timeout",
            "5",
        ]
    )
    out = capsys.readouterr().out

    assert result == 1
    assert "UNKNOWN CONFIG KEY: [project] totally_invented_project_key" in out, (
        f"A genuinely-unknown project.cfg key must still be flagged.\nstdout: {out!r}"
    )
    assert "UNKNOWN CONFIG KEY: [project] workflow_type" not in out, (
        f"A real, correctly-documented project.cfg key must not be flagged unknown.\nstdout: {out!r}"
    )
    assert "CONFIG DEFAULT MISMATCH: [project] workflow_type" not in out, (
        f"workflow_type's claimed default matches the registry; it must not mismatch either.\nstdout: {out!r}"
    )


# ---------------------------------------------------------------------------
# Test 4c: the "N/A" registry default marks a per-installation identity
# field as exempt from the default-value comparison — without it, widening
# the registry to cover project.cfg's identity fields would trade the
# UNKNOWN CONFIG KEY false positive for a CONFIG DEFAULT MISMATCH false
# positive on the very same line.
# ---------------------------------------------------------------------------


def test_na_registry_default_skips_default_mismatch_check(tmp_path, capsys) -> None:
    config_loader = _fixture_config_loader(tmp_path)
    project_registry = _fixture_project_registry(tmp_path)  # project_name is N/A
    census = _fixture_census(tmp_path)
    doc = _write(
        tmp_path / "fake.md",
        "# Fake doc\n\n```ini\n[project]\nproject_name = my-example-project\n```\n",
    )

    result = _run(doc, config_loader, census, tmp_path, project_key_registry=project_registry)
    out = capsys.readouterr().out

    assert "UNKNOWN CONFIG KEY: [project] project_name" not in out
    assert "CONFIG DEFAULT MISMATCH: [project] project_name" not in out, (
        f"An N/A-marked registry default must never be compared against a document's "
        f"example value.\nstdout: {out!r}"
    )
    assert result == 1  # [project] workflow_type is declared but never cited — undocumented, not suppressed.


def test_ordinary_default_without_na_marker_would_mismatch(tmp_path, capsys) -> None:
    """Contrast case: WITHOUT the N/A marker, the identical per-installation
    example value produces the false CONFIG DEFAULT MISMATCH the N/A
    mechanism exists to avoid — proving the marker in the shipped registry
    does real work rather than being a no-op."""
    config_loader = _fixture_config_loader(tmp_path)
    plain_registry = _write(
        tmp_path / "fixture_project_paths_no_na.sh",
        "#!/usr/bin/env bash\n"
        "# fixture project_paths.sh — project_name given an ORDINARY default\n"
        "#\n"
        "# Key Registry\n"
        "# ------------\n"
        "# [project]\n"
        '#   project_name       OPTIONAL  ""\n'
        "\n"
        "true\n",
    )
    census = _fixture_census(tmp_path)
    doc = _write(
        tmp_path / "fake.md",
        "# Fake doc\n\n```ini\n[project]\nproject_name = my-example-project\n```\n",
    )

    result = _run(doc, config_loader, census, tmp_path, project_key_registry=plain_registry)
    out = capsys.readouterr().out

    assert result == 1
    assert "CONFIG DEFAULT MISMATCH: [project] project_name" in out, (
        f"An ordinary (non-N/A) default should mismatch a differing example value.\nstdout: {out!r}"
    )


# ---------------------------------------------------------------------------
# Test 5: unknown dashboard pane is flagged, and clears when the seed is removed
# ---------------------------------------------------------------------------


def test_unknown_pane_citation_is_flagged(tmp_path, capsys) -> None:
    config_loader = _fixture_config_loader(tmp_path)
    census = _fixture_census(tmp_path)
    doc = _write(
        tmp_path / "fake.md",
        "# Fake doc\n\n"
        "Window 0 (main) is the primary view.\n\n"
        "| Pane | Script | What it shows |\n"
        "|------|--------|---------------|\n"
        "| Left | `nonexistent-script.sh` | something |\n",
    )

    result = _run(doc, config_loader, census, tmp_path)
    out = capsys.readouterr().out

    assert result == 1
    assert "UNKNOWN DASHBOARD PANE: window 0, position 'Left'" in out, (
        f"Finding does not name the window/pane.\nstdout: {out!r}"
    )
    assert "nonexistent-script.sh" in out
    assert "fake.md" in out


def test_removing_pane_seed_clears_its_finding(tmp_path, capsys) -> None:
    config_loader = _fixture_config_loader(tmp_path)
    census = _fixture_census(tmp_path)
    doc_path = tmp_path / "fake.md"

    _write(
        doc_path,
        "# Fake doc\n\n"
        "Window 0 (main) is the primary view.\n\n"
        "| Pane | Script | What it shows |\n"
        "|------|--------|---------------|\n"
        "| Left | `nonexistent-script.sh` | something |\n",
    )
    result_before = _run(doc_path, config_loader, census, tmp_path)
    out_before = capsys.readouterr().out
    assert result_before == 1
    assert "UNKNOWN DASHBOARD PANE" in out_before

    # Correct the pane's script to the one the fixture census actually declares.
    _write(
        doc_path,
        "# Fake doc\n\n"
        "Window 0 (main) is the primary view.\n\n"
        "| Pane | Script | What it shows |\n"
        "|------|--------|---------------|\n"
        "| Left | `header.sh` | something |\n",
    )
    result_after = _run(doc_path, config_loader, census, tmp_path)
    out_after = capsys.readouterr().out

    assert "UNKNOWN DASHBOARD PANE" not in out_after, (
        f"Correcting the pane's script must clear its finding.\nstdout: {out_after!r}"
    )
    # Window 1 ('second') is still undocumented in this fixture — result stays 1 either way.
    assert result_after == 1


# ---------------------------------------------------------------------------
# Test 6: window name mismatch and unknown window index, distinctly
# ---------------------------------------------------------------------------


def test_window_name_mismatch_is_flagged(tmp_path, capsys) -> None:
    config_loader = _fixture_config_loader(tmp_path)
    census = _fixture_census(tmp_path)
    doc = _write(tmp_path / "fake.md", "# Fake doc\n\nStatus (Window 0) shows RC progress.\n")

    result = _run(doc, config_loader, census, tmp_path)
    out = capsys.readouterr().out

    assert result == 1
    assert "DASHBOARD WINDOW NAME MISMATCH: window 0" in out
    assert "documented name: 'Status'" in out
    assert "census name:     'main'" in out


def test_unknown_window_index_is_flagged(tmp_path, capsys) -> None:
    config_loader = _fixture_config_loader(tmp_path)
    census = _fixture_census(tmp_path)
    doc = _write(tmp_path / "fake.md", "# Fake doc\n\nWindow 99: Nonexistent\n")

    result = _run(doc, config_loader, census, tmp_path)
    out = capsys.readouterr().out

    assert result == 1
    assert "UNKNOWN DASHBOARD WINDOW: window 99" in out


# ---------------------------------------------------------------------------
# Test 7: undocumented census window is reported and never suppressed
# ---------------------------------------------------------------------------


def test_undocumented_window_is_reported_and_never_suppressed(tmp_path, capsys) -> None:
    config_loader = _fixture_config_loader(tmp_path)
    census = _fixture_census(tmp_path)
    # A document that correctly describes window 0 only — window 1
    # ('second') is never mentioned, so it must surface as undocumented.
    doc = _write(tmp_path / "fake.md", "# Fake doc\n\nWindow 0 (main) is the primary view.\n")

    result = _run(doc, config_loader, census, tmp_path)
    out = capsys.readouterr().out

    assert result == 1
    assert "UNDOCUMENTED DASHBOARD WINDOW: window 1 (second)" in out
    assert "DASHBOARD WINDOW NAME MISMATCH" not in out
    assert "UNKNOWN DASHBOARD WINDOW" not in out


# ---------------------------------------------------------------------------
# Test 8: both new CENSUS-ANCHOR tokens resolve
# ---------------------------------------------------------------------------


def test_new_census_anchors_resolve_in_source_and_index() -> None:
    source = _LINT_SCRIPT.read_text(encoding="utf-8")
    census_doc = _CENSUS_DOC.read_text(encoding="utf-8")

    for token in ("doc-key-truth-01", "doc-pane-truth-01"):
        marker = f"# CENSUS-ANCHOR: {token}"
        assert marker in source, f"{marker!r} not found in {_LINT_SCRIPT}"
        index_line = f"team/scripts/lint_doc_command_truth.py:{token} CONVERT"
        assert index_line in census_doc, f"{index_line!r} not found in {_CENSUS_DOC}"


# ---------------------------------------------------------------------------
# Test 9: real-tree runs complete without crashing and scan non-zero citations
# ---------------------------------------------------------------------------


def test_real_tree_key_parity_scans_nonzero_citations(capsys) -> None:
    result = _lint.main(["--repo-root", str(_REPO_ROOT), "--help-timeout", "5"])
    out = capsys.readouterr().out

    assert result in (0, 1), f"Unexpected exit code {result}.\nstdout: {out!r}"
    assert "key-parity: scanned 0 key citation" not in out, (
        f"The real key-parity scan set resolved to zero citations.\nstdout: {out!r}"
    )
    assert "lint_doc_command_truth: key-parity:" in out


def test_real_tree_pane_parity_scans_nonzero_citations(capsys) -> None:
    result = _lint.main(["--repo-root", str(_REPO_ROOT), "--help-timeout", "5"])
    out = capsys.readouterr().out

    assert result in (0, 1), f"Unexpected exit code {result}.\nstdout: {out!r}"
    assert "pane-parity: scanned 0 window/pane citation" not in out, (
        f"The real pane-parity scan set resolved to zero citations.\nstdout: {out!r}"
    )
    assert "lint_doc_command_truth: pane-parity:" in out
    assert "15 census window id(s) (16 row(s))" in out


# ---------------------------------------------------------------------------
# Test 10: the registry parser reads the real config_loader.sh; the real
# census resolves to 15 window ids across 16 rows.
# ---------------------------------------------------------------------------


def test_registry_parser_reads_real_config_loader() -> None:
    registry = _lint._parse_config_loader_registry(_REAL_CONFIG_LOADER)

    assert len(registry) > 40, (
        f"Expected the real Key Registry to declare more than 40 keys; got {len(registry)}."
    )
    assert registry[("dashboard", "rows_per_column")].default == "21"
    assert registry[("dashboard", "rows_per_column")].status == "OPTIONAL"
    assert registry[("paths", "dev_tree_path")].default == ""
    assert registry[("release", "deferred_bug_level")].default == "low"
    # The [models.<provider>] families of the shipped providers share the
    # same per-role vocabulary; a provider with no wake script has none.
    for provider in ("claude", "codex"):
        assert registry[(f"models.{provider}", "coder")].default == ""
        assert registry[(f"models.{provider}", "coder")].status == "OPTIONAL"
    assert not any(section == "models.gemini" for section, _ in registry)


def test_real_census_resolves_to_documented_window_and_row_counts() -> None:
    windows = _lint._load_dashboard_windows(_TEAM_DIR, None)

    assert len(windows) == 16, f"Expected 16 window rows (drill-N counted twice); got {len(windows)}."
    unique_ids = {w.tmux_index for w in windows}
    assert len(unique_ids) == 15, f"Expected 15 unique window ids; got {len(unique_ids)}."
    total_panes = sum(len(w.panes) for w in windows)
    assert total_panes == 47, f"Expected 47 pane (P) lines across all windows; got {total_panes}."
