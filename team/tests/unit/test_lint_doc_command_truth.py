"""
test_lint_doc_command_truth.py
===============================
Tests for team/scripts/lint_doc_command_truth.py — the doc-truth lint's
existence half (every command a document cites must exist in this tree and
answer ``--help`` without erroring).

Tests cover the core acceptance criteria:

  1. **Missing-command citation is flagged**: a seeded document citing a
     script that does not exist produces a MISSING COMMAND finding naming
     the document, the line, and the cited command.

  2. **Broken-help citation is flagged, distinctly**: a seeded document
     citing a real script whose ``--help`` exits non-zero produces a
     HELP FAILS finding, visibly different in shape from the missing-command
     finding.

  3. **Removing each seed makes its finding disappear**: the lint is proven
     to discriminate, not to complain constantly — each seed is added and
     removed independently, and the corresponding finding appears/vanishes.

  4. **A well-behaved script produces no finding.**

  5. **Verb citations resolve through the operator verb manifest.**

  6. **Timeout is treated as a --help failure**, not a crash.

  7. **Placeholder citations are skipped** (angle-bracket script names).

  8. **Glob patterns and test-module basenames are not citations.**

  9. **Exit behaviour matches the CONVERT class**: the lint's own exit code
     is 1 with findings present and 0 with none; wrapped through
     ``gated_runner_run_quality_lint`` (the shape a calling runner uses),
     the wrapper always returns 0 regardless — see
     ``test_gated_runner_wrapper_never_aborts``.

 10. **Real-tree run scans a non-zero number of citations** and completes
     without a Python-level crash — the live regression guard proving the
     scan set is not accidentally empty.

 11. **No probe outlives the lint**: a cited script that falls through
     ``--help`` into a follow-mode ``tail -F | awk`` pipeline, in the
     foreground (probe times out) or backgrounded (probe exits 0), leaves
     no process from its process group and no sandbox directory behind.

 12. **Every CommonMark fence pairs with its own closer**: a fence tagged
     ``text``/``ini``/``python``/anything else, or a ``~~~`` fence, opens a
     block whose closer closes it, so a bad prose citation after it is
     still reported; a non-shell block's contents are never scanned.

All tests use pytest's ``tmp_path`` for isolation — no bare /tmp paths, no
live kanban state mutations. Every fixture script that intentionally has no
--help is deliberately non-executable-by-design (it just exits non-zero);
none is destructive.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Load lint_doc_command_truth.py as a module.
# Path: team/tests/unit/ -> team/ (two levels up) -> team/scripts/
# ---------------------------------------------------------------------------
_THIS_FILE = Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/
_REPO_ROOT = _TEAM_DIR.parent  # repo root
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_LINT_SCRIPT = _SCRIPTS_DIR / "lint_doc_command_truth.py"


def _import_lint_module():
    spec = importlib.util.spec_from_file_location("lint_doc_command_truth", _LINT_SCRIPT)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load spec from {_LINT_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    # Register under sys.modules before exec: the module uses
    # @dataclass(frozen=True) with `from __future__ import annotations`,
    # and dataclasses.__init__ resolves annotation strings via
    # sys.modules[cls.__module__] — it must already be there when the
    # class bodies execute, or dataclass processing raises AttributeError
    # on a None module lookup.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_lint = _import_lint_module()


# ---------------------------------------------------------------------------
# Fixture-tree builder
# ---------------------------------------------------------------------------


def _build_fixture_tree(tmp_path: Path) -> Path:
    """Build a minimal synthetic repo tree: <root>/team/scripts/ with one
    well-behaved script and one broken-help script, and <root>/docs/ empty.
    Returns the tree root."""
    root = tmp_path / "fixture_repo"
    scripts_dir = root / "team" / "scripts"
    scripts_dir.mkdir(parents=True)
    (root / "docs").mkdir()

    good = scripts_dir / "real_script.sh"
    good.write_text(
        "#!/usr/bin/env bash\n"
        'case "${1:-}" in\n'
        '  --help|-h) echo "Usage: real_script.sh [--help]"; exit 0 ;;\n'
        "esac\n"
        'echo "running"\n',
        encoding="utf-8",
    )
    good.chmod(0o755)

    broken = scripts_dir / "broken_help.sh"
    broken.write_text(
        "#!/usr/bin/env bash\n" 'echo "no help here" >&2\n' "exit 3\n",
        encoding="utf-8",
    )
    broken.chmod(0o755)

    hangs = scripts_dir / "hangs_forever.sh"
    hangs.write_text(
        "#!/usr/bin/env bash\n" "sleep 30\n",
        encoding="utf-8",
    )
    hangs.chmod(0o755)

    return root


def _write_doc(root: Path, name: str, body: str) -> Path:
    doc = root / "docs" / name
    doc.write_text(body, encoding="utf-8")
    return doc


# ---------------------------------------------------------------------------
# Test 1: missing-command citation is flagged
# ---------------------------------------------------------------------------


def test_missing_command_citation_is_flagged(tmp_path: Path) -> None:
    root = _build_fixture_tree(tmp_path)
    doc = _write_doc(
        root,
        "fake.md",
        "# Fake doc\n\n```bash\nteam/scripts/does_not_exist.sh --project demo\n```\n",
    )

    result = _lint.main(["--repo-root", str(root), "--doc", str(doc)])

    assert result == 1, "A citation naming a script that does not exist must fail the lint."


def test_missing_command_finding_names_doc_line_and_command(tmp_path: Path, capsys) -> None:
    root = _build_fixture_tree(tmp_path)
    doc = _write_doc(
        root,
        "fake.md",
        "# Fake doc\n\n```bash\nteam/scripts/does_not_exist.sh --project demo\n```\n",
    )

    result = _lint.main(["--repo-root", str(root), "--doc", str(doc)])
    out = capsys.readouterr().out

    assert result == 1
    assert "MISSING COMMAND: team/scripts/does_not_exist.sh" in out, (
        f"Finding does not name the missing command.\nstdout: {out!r}"
    )
    assert "docs/fake.md:4" in out, f"Finding does not name the doc:line.\nstdout: {out!r}"


# ---------------------------------------------------------------------------
# Test 2: broken-help citation is flagged, distinctly from missing-command
# ---------------------------------------------------------------------------


def test_broken_help_citation_is_flagged_distinctly(tmp_path: Path, capsys) -> None:
    root = _build_fixture_tree(tmp_path)
    doc = _write_doc(
        root,
        "fake.md",
        "# Fake doc\n\n```bash\nteam/scripts/broken_help.sh --project demo\n```\n",
    )

    result = _lint.main(["--repo-root", str(root), "--doc", str(doc)])
    out = capsys.readouterr().out

    assert result == 1, "A real script whose --help exits non-zero must fail the lint."
    assert "HELP FAILS: team/scripts/broken_help.sh" in out, (
        f"Finding does not name the broken-help command.\nstdout: {out!r}"
    )
    assert "exited 3" in out, f"Finding does not name the actual exit code.\nstdout: {out!r}"
    assert "MISSING COMMAND" not in out, (
        "A resolvable script with a broken --help must never be reported as missing."
    )


def test_missing_and_help_broken_findings_are_visibly_different(tmp_path: Path, capsys) -> None:
    """Both seed classes in one document: two distinct, differently-labelled
    findings, not a single generic 'bad citation' bucket."""
    root = _build_fixture_tree(tmp_path)
    doc = _write_doc(
        root,
        "fake.md",
        "# Fake doc\n\n"
        "```bash\n"
        "team/scripts/does_not_exist.sh --project demo\n"
        "team/scripts/broken_help.sh --project demo\n"
        "```\n",
    )

    result = _lint.main(["--repo-root", str(root), "--doc", str(doc)])
    out = capsys.readouterr().out

    assert result == 1
    assert "MISSING COMMAND: team/scripts/does_not_exist.sh" in out
    assert "HELP FAILS: team/scripts/broken_help.sh" in out
    # The two finding headers are lexically distinct labels, not the same string.
    assert "MISSING COMMAND:" != "HELP FAILS:"


# ---------------------------------------------------------------------------
# Test 3: removing each seed makes its finding disappear (discrimination)
# ---------------------------------------------------------------------------


def test_removing_missing_command_seed_clears_its_finding(tmp_path: Path, capsys) -> None:
    root = _build_fixture_tree(tmp_path)
    doc_path = root / "docs" / "fake.md"

    doc_path.write_text(
        "# Fake doc\n\n"
        "```bash\n"
        "team/scripts/does_not_exist.sh --project demo\n"
        "team/scripts/broken_help.sh --project demo\n"
        "```\n",
        encoding="utf-8",
    )
    result_before = _lint.main(["--repo-root", str(root), "--doc", str(doc_path)])
    out_before = capsys.readouterr().out
    assert result_before == 1
    assert "MISSING COMMAND: team/scripts/does_not_exist.sh" in out_before
    assert "HELP FAILS: team/scripts/broken_help.sh" in out_before

    # Remove only the missing-command seed; keep the broken-help seed.
    doc_path.write_text(
        "# Fake doc\n\n```bash\nteam/scripts/broken_help.sh --project demo\n```\n",
        encoding="utf-8",
    )
    result_after = _lint.main(["--repo-root", str(root), "--doc", str(doc_path)])
    out_after = capsys.readouterr().out

    assert result_after == 1, "The remaining broken-help seed must still fail the lint."
    assert "MISSING COMMAND" not in out_after, (
        "Removing the missing-command citation must clear its finding.\n"
        f"stdout: {out_after!r}"
    )
    assert "HELP FAILS: team/scripts/broken_help.sh" in out_after


def test_removing_help_broken_seed_clears_its_finding(tmp_path: Path, capsys) -> None:
    root = _build_fixture_tree(tmp_path)
    doc_path = root / "docs" / "fake.md"

    doc_path.write_text(
        "# Fake doc\n\n"
        "```bash\n"
        "team/scripts/does_not_exist.sh --project demo\n"
        "team/scripts/broken_help.sh --project demo\n"
        "```\n",
        encoding="utf-8",
    )
    result_before = _lint.main(["--repo-root", str(root), "--doc", str(doc_path)])
    capsys.readouterr()
    assert result_before == 1

    # Remove only the broken-help seed; keep the missing-command seed.
    doc_path.write_text(
        "# Fake doc\n\n```bash\nteam/scripts/does_not_exist.sh --project demo\n```\n",
        encoding="utf-8",
    )
    result_after = _lint.main(["--repo-root", str(root), "--doc", str(doc_path)])
    out_after = capsys.readouterr().out

    assert result_after == 1, "The remaining missing-command seed must still fail the lint."
    assert "HELP FAILS" not in out_after, (
        f"Removing the broken-help citation must clear its finding.\nstdout: {out_after!r}"
    )
    assert "MISSING COMMAND: team/scripts/does_not_exist.sh" in out_after


def test_removing_both_seeds_clears_the_lint_entirely(tmp_path: Path, capsys) -> None:
    root = _build_fixture_tree(tmp_path)
    doc_path = root / "docs" / "fake.md"

    doc_path.write_text(
        "# Fake doc\n\n"
        "```bash\n"
        "team/scripts/does_not_exist.sh --project demo\n"
        "team/scripts/broken_help.sh --project demo\n"
        "```\n",
        encoding="utf-8",
    )
    assert _lint.main(["--repo-root", str(root), "--doc", str(doc_path)]) == 1
    capsys.readouterr()

    doc_path.write_text(
        "# Fake doc\n\n```bash\nteam/scripts/real_script.sh --project demo\n```\n",
        encoding="utf-8",
    )
    result = _lint.main(["--repo-root", str(root), "--doc", str(doc_path)])
    out = capsys.readouterr().out

    assert result == 0, f"A doc citing only a well-behaved script must pass.\nstdout: {out!r}"
    assert "0 finding(s)" in out


# ---------------------------------------------------------------------------
# Test 4: well-behaved script alone produces no finding
# ---------------------------------------------------------------------------


def test_well_behaved_script_produces_no_finding(tmp_path: Path) -> None:
    root = _build_fixture_tree(tmp_path)
    doc = _write_doc(
        root,
        "fake.md",
        "# Fake doc\n\n```bash\nteam/scripts/real_script.sh --project demo\n```\n",
    )

    result = _lint.main(["--repo-root", str(root), "--doc", str(doc)])

    assert result == 0


# ---------------------------------------------------------------------------
# Test 5: verb citations resolve through the operator verb manifest
# ---------------------------------------------------------------------------


def _write_verb_manifest(root: Path, verb: str, script_rel: str) -> Path:
    manifest = root / "team" / "config" / "operator_verbs.manifest"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        "\t".join(
            [
                "V",
                verb,
                "active",
                script_rel,
                "help",
                "no",
                "read-only",
                "ABSENT",
                "ABSENT",
                "ABSENT",
                "read",
                "ABSENT",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return manifest


def test_verb_citation_resolves_through_manifest(tmp_path: Path) -> None:
    root = _build_fixture_tree(tmp_path)
    manifest = _write_verb_manifest(root, "realverb", "scripts/real_script.sh")
    doc = _write_doc(root, "fake.md", "The `realverb` command is read-only.\n")

    result = _lint.main(
        [
            "--repo-root",
            str(root),
            "--doc",
            str(doc),
            "--operator-verbs-manifest",
            str(manifest),
        ]
    )

    assert result == 0, "A verb citation naming a real, working script must pass."


def test_verb_citation_missing_script_is_flagged(tmp_path: Path, capsys) -> None:
    root = _build_fixture_tree(tmp_path)
    manifest = _write_verb_manifest(root, "ghostverb", "scripts/ghost_script.sh")
    doc = _write_doc(root, "fake.md", "The `ghostverb` command is read-only.\n")

    result = _lint.main(
        [
            "--repo-root",
            str(root),
            "--doc",
            str(doc),
            "--operator-verbs-manifest",
            str(manifest),
        ]
    )
    out = capsys.readouterr().out

    assert result == 1
    assert "MISSING COMMAND: ghostverb" in out


# ---------------------------------------------------------------------------
# Test 6: timeout is treated as a --help failure, not a crash
# ---------------------------------------------------------------------------


def test_help_timeout_is_a_finding_not_a_crash(tmp_path: Path, capsys) -> None:
    root = _build_fixture_tree(tmp_path)
    doc = _write_doc(
        root,
        "fake.md",
        "# Fake doc\n\n```bash\nteam/scripts/hangs_forever.sh --project demo\n```\n",
    )

    result = _lint.main(
        ["--repo-root", str(root), "--doc", str(doc), "--help-timeout", "1"]
    )
    out = capsys.readouterr().out

    assert result == 1
    assert "HELP FAILS: team/scripts/hangs_forever.sh" in out
    assert "did not return within" in out


# ---------------------------------------------------------------------------
# Test 7: placeholder citations are skipped
# ---------------------------------------------------------------------------


def test_placeholder_script_name_is_skipped(tmp_path: Path) -> None:
    root = _build_fixture_tree(tmp_path)
    doc = _write_doc(
        root,
        "fake.md",
        "# Fake doc\n\n```bash\n<script-name>.sh --project demo\n```\n",
    )

    result = _lint.main(["--repo-root", str(root), "--doc", str(doc)])

    assert result == 0, "An angle-bracket placeholder script name must never be a finding."


# ---------------------------------------------------------------------------
# Test 8: glob patterns and test-module basenames are not citations
# ---------------------------------------------------------------------------


def test_glob_pattern_and_test_module_are_not_citations(tmp_path: Path, capsys) -> None:
    root = _build_fixture_tree(tmp_path)
    doc = _write_doc(
        root,
        "fake.md",
        "# Fake doc\n\nSee `test_*.py` and `test_something_missing.py` for coverage.\n",
    )

    result = _lint.main(["--repo-root", str(root), "--doc", str(doc)])
    out = capsys.readouterr().out

    assert result == 0, f"Glob patterns and test-module mentions must not be findings.\nstdout: {out!r}"
    assert "scanned 0 citation" in out


# ---------------------------------------------------------------------------
# Test 9: exit behaviour matches the CONVERT class
# ---------------------------------------------------------------------------


def test_exit_code_is_1_with_findings_present(tmp_path: Path) -> None:
    root = _build_fixture_tree(tmp_path)
    doc = _write_doc(
        root,
        "fake.md",
        "# Fake doc\n\n```bash\nteam/scripts/does_not_exist.sh\n```\n",
    )
    assert _lint.main(["--repo-root", str(root), "--doc", str(doc)]) == 1


def test_gated_runner_wrapper_never_aborts(tmp_path: Path) -> None:
    """Invoked the way a runner would (through gated_runner_run_quality_lint),
    a non-zero lint exit is swallowed to 0 — CONVERT class: warn, report,
    continue. Demonstrates via a real shell invocation of the shared library,
    not a description of what it should do."""
    root = _build_fixture_tree(tmp_path)
    doc = _write_doc(
        root,
        "fake.md",
        "# Fake doc\n\n```bash\nteam/scripts/does_not_exist.sh\n```\n",
    )

    script = (
        f'source "{_SCRIPTS_DIR}/lib/gated_runner_lint.sh" && '
        f'gated_runner_run_quality_lint "doc_command_truth_test" -- '
        f'python3 "{_LINT_SCRIPT}" --repo-root "{root}" --doc "{doc}"; '
        f"echo WRAPPER_EXIT=$?"
    )
    proc = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert "WRAPPER_EXIT=0" in proc.stdout, (
        "gated_runner_run_quality_lint must always return 0 regardless of the "
        f"wrapped lint's own exit code (CONVERT class).\n"
        f"stdout: {proc.stdout!r}\nstderr: {proc.stderr!r}"
    )


# ---------------------------------------------------------------------------
# Test 10: real-tree run scans a non-zero number of citations
# ---------------------------------------------------------------------------


def test_real_tree_scans_nonzero_citations(capsys) -> None:
    """Live regression guard: the real scan set must never resolve to zero
    citations (an empty scan set is a failure of this lint's own purpose,
    not a clean pass) and the lint must complete without a Python-level
    crash. Findings, if any, are not asserted here — TESTER and the
    completion note track the real-tree finding count; this test only
    proves the lint runs end to end against the shipped tree."""
    result = _lint.main(["--repo-root", str(_REPO_ROOT), "--help-timeout", "5"])
    out = capsys.readouterr().out

    assert result in (0, 1), f"Unexpected exit code {result} (expected 0 or 1).\nstdout: {out!r}"
    assert "scanned 0 citation" not in out, (
        "The real scan set resolved to zero citations — a failure of this "
        f"lint's own purpose, not a pass.\nstdout: {out!r}"
    )


# ---------------------------------------------------------------------------
# Tests 11-14: the default scan set widens to
# cover the repository-root documents and docs/docker.md, via a scan-set
# source kept SEPARATE from team/config/docs-manifest.txt rather than an
# addition to it (docs-manifest.txt's own header states its purpose as the
# install-set closure; none of these four documents belong to that set —
# see lint_doc_command_truth.py's "Scan set" docstring section).
# ---------------------------------------------------------------------------


def test_default_scan_set_covers_repo_root_and_docker_docs(capsys) -> None:
    """Acceptance criterion: a default run against the real tree reports a
    scanned-document set that includes README.md, HOW_TO.md,
    ARCHITECTURE.md and docs/docker.md — the four documents previously
    absent from the default scan set."""
    result = _lint.main(
        ["--repo-root", str(_REPO_ROOT), "--help-timeout", "5", "--verbose"]
    )
    out = capsys.readouterr().out
    assert result in (0, 1), f"Unexpected exit code {result}.\nstdout: {out!r}"

    scanned_docs = {
        line.split("citation: ", 1)[1].split(":", 1)[0]
        for line in out.splitlines()
        if line.strip().startswith("citation:")
    }
    for expected in ("README.md", "HOW_TO.md", "ARCHITECTURE.md", "docs/docker.md"):
        assert expected in scanned_docs, (
            f"{expected} was not in the scanned-document set: {sorted(scanned_docs)}"
        )


def test_extra_scan_manifest_is_a_separate_source_from_docs_manifest(tmp_path: Path) -> None:
    """The four repo-root/docker additions come from a scan-set source that
    is separate from team/config/docs-manifest.txt, not folded into it: a
    fixture repo whose docs-manifest.txt is empty (no entries at all) still
    picks up a document named only in --extra-scan-manifest."""
    root = tmp_path / "fixture_repo"
    (root / "team" / "config").mkdir(parents=True)
    (root / "docs").mkdir()
    empty_manifest = root / "team" / "config" / "docs-manifest.txt"
    empty_manifest.write_text("# no entries\n", encoding="utf-8")

    extra_doc = root / "TOP_LEVEL.md"
    extra_doc.write_text("# Top-level doc\n\nNo commands cited here.\n", encoding="utf-8")
    extra_manifest = root / "team" / "config" / "extra-scan-set.txt"
    extra_manifest.write_text("TOP_LEVEL.md\n", encoding="utf-8")

    result = _lint.main(
        [
            "--repo-root",
            str(root),
            "--docs-manifest",
            str(empty_manifest),
            "--extra-scan-manifest",
            str(extra_manifest),
            "--verbose",
        ]
    )
    assert result == 0, "an empty manifest plus a clean extra doc should still be a clean run"


def test_extra_scan_manifest_missing_degrades_to_manifest_only(tmp_path: Path) -> None:
    """A missing --extra-scan-manifest degrades to the manifest-derived half
    of the scan set alone (with a stderr warning), matching the existing
    degrade-and-warn discipline for a missing individual scan-set entry —
    it does not abort the lint."""
    root = _build_fixture_tree(tmp_path)
    doc = _write_doc(root, "manifest_doc.md", "# Doc\n\nNo commands cited here.\n")
    manifest = root / "team" / "config" / "docs-manifest.txt"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("manifest_doc.md\n", encoding="utf-8")

    result = _lint.main(
        [
            "--repo-root",
            str(root),
            "--docs-manifest",
            str(manifest),
            "--extra-scan-manifest",
            str(root / "does" / "not" / "exist.txt"),
            "--verbose",
        ]
    )
    assert result == 0
    assert doc.is_file()  # sanity: the manifest-derived doc really was written


def test_docs_manifest_still_documents_only_the_install_set(capsys) -> None:
    """team/config/docs-manifest.txt keeps its stated, narrower purpose
    unchanged (leaving the manifest as exactly the install-set closure it
    documents): none of the four repo-root/docker
    additions are listed in it, and its own header still describes the
    install-set closure rather than this lint's scan set."""
    manifest_path = _REPO_ROOT / "team" / "config" / "docs-manifest.txt"
    text = manifest_path.read_text(encoding="utf-8")
    entries = {
        line.split("#", 1)[0].strip()
        for line in text.splitlines()
        if line.split("#", 1)[0].strip()
    }
    for excluded in ("README.md", "HOW_TO.md", "ARCHITECTURE.md", "docker.md"):
        assert excluded not in entries, (
            f"{excluded} must not be added to docs-manifest.txt's install-set "
            "closure — it belongs in team/config/doc-truth-extra-scan-docs.txt instead"
        )
    assert "install.sh and upgrade.sh" in text, (
        "docs-manifest.txt's header should still describe its install-set-closure "
        "purpose unchanged"
    )


def test_extra_scan_docs_file_names_exactly_the_four_documents() -> None:
    """team/config/doc-truth-extra-scan-docs.txt — the separate scan-set
    source, kept apart from the install-set manifest — names exactly the four documents
    called out above, no more and no fewer."""
    extra_manifest = _TEAM_DIR / "config" / "doc-truth-extra-scan-docs.txt"
    assert extra_manifest.is_file(), f"expected {extra_manifest} to exist"
    entries = [
        line.split("#", 1)[0].strip()
        for line in extra_manifest.read_text(encoding="utf-8").splitlines()
        if line.split("#", 1)[0].strip()
    ]
    assert sorted(entries) == sorted(["README.md", "HOW_TO.md", "ARCHITECTURE.md", "docs/docker.md"])


# ---------------------------------------------------------------------------
# HELP FAILS exempt list — process-internal citations
#
# team/config/doc-command-truth-help-exempt.txt gives this lint the same
# exempt-list seam team/scripts/help_presence_exempt.txt already gives
# lint_help_presence.py, narrowed to a citation whose resolved file is
# process-internal (a sourced prelude, a container entrypoint) rather than
# an operator command line. Two directions are proven here:
#
#   - an exempted citation's HELP FAILS finding is silenced;
#   - a non-exempted broken-help citation still fails, exactly as before;
#   - the existence half of the check is never blunted by an entry naming a
#     file that does not exist in this tree.
# ---------------------------------------------------------------------------


def _write_help_exempt_list(root: Path, entries: list[str]) -> Path:
    exempt_list = root / "team" / "config" / "help-exempt-test.txt"
    exempt_list.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# test fixture exempt list\n"]
    for entry in entries:
        lines.append(f"# process-internal: test fixture reason for {entry}\n")
        lines.append(f"{entry}\n")
    exempt_list.write_text("".join(lines), encoding="utf-8")
    return exempt_list


def test_help_exempt_list_silences_a_listed_process_internal_citation(tmp_path: Path, capsys) -> None:
    """A citation whose resolved file is on the exempt list produces no
    HELP FAILS finding, even though its --help genuinely exits non-zero."""
    root = _build_fixture_tree(tmp_path)
    doc = _write_doc(
        root,
        "fake.md",
        "# Fake doc\n\n```bash\nteam/scripts/broken_help.sh --project demo\n```\n",
    )
    exempt_list = _write_help_exempt_list(root, ["team/scripts/broken_help.sh"])

    result = _lint.main(
        ["--repo-root", str(root), "--doc", str(doc), "--help-exempt-list", str(exempt_list)]
    )
    out = capsys.readouterr().out

    assert result == 0, f"An exempted broken-help citation must not fail the lint.\nstdout: {out!r}"
    assert "HELP FAILS" not in out, f"Exempted citation must not be reported.\nstdout: {out!r}"
    assert "1 process-internal citation(s) exempted" in out, (
        f"Summary line must record the exemption.\nstdout: {out!r}"
    )


def test_help_exempt_list_does_not_silence_a_non_exempt_broken_help_citation(
    tmp_path: Path, capsys
) -> None:
    """Discrimination, direction 1: a broken-help citation whose resolved
    file is NOT on the exempt list is reported exactly as it is without the
    seam — the seam narrows nothing beyond the paths it explicitly names."""
    root = _build_fixture_tree(tmp_path)
    doc = _write_doc(
        root,
        "fake.md",
        "# Fake doc\n\n```bash\nteam/scripts/broken_help.sh --project demo\n```\n",
    )
    # Exempt list exists but names an unrelated path only.
    exempt_list = _write_help_exempt_list(root, ["team/scripts/real_script.sh"])

    result = _lint.main(
        ["--repo-root", str(root), "--doc", str(doc), "--help-exempt-list", str(exempt_list)]
    )
    out = capsys.readouterr().out

    assert result == 1, f"A non-exempt broken-help citation must still fail the lint.\nstdout: {out!r}"
    assert "HELP FAILS: team/scripts/broken_help.sh" in out, (
        f"Non-exempt citation must still be reported.\nstdout: {out!r}"
    )


def test_help_exempt_list_never_silences_the_existence_check(tmp_path: Path, capsys) -> None:
    """Discrimination, direction 2: an exempt-list entry naming a path that
    does not exist in this tree never suppresses the MISSING COMMAND
    finding — the exempt list only ever silences the --help half of the
    check, and only after resolution already succeeded."""
    root = _build_fixture_tree(tmp_path)
    doc = _write_doc(
        root,
        "fake.md",
        "# Fake doc\n\n```bash\nteam/scripts/deleted_but_exempt.sh --project demo\n```\n",
    )
    # Listed as exempt, but the file was never created (simulates deletion
    # after the exemption was recorded).
    exempt_list = _write_help_exempt_list(root, ["team/scripts/deleted_but_exempt.sh"])
    assert not (root / "team" / "scripts" / "deleted_but_exempt.sh").exists()

    result = _lint.main(
        ["--repo-root", str(root), "--doc", str(doc), "--help-exempt-list", str(exempt_list)]
    )
    out = capsys.readouterr().out

    assert result == 1, (
        f"An exempt path whose file no longer exists must still fail the lint.\nstdout: {out!r}"
    )
    assert "MISSING COMMAND: team/scripts/deleted_but_exempt.sh" in out, (
        f"Existence check must still fire for a deleted exempt path.\nstdout: {out!r}"
    )
    assert "HELP FAILS" not in out, (
        "A missing file was never --help-checked in the first place — only "
        f"MISSING COMMAND is expected here.\nstdout: {out!r}"
    )


def test_missing_help_exempt_list_degrades_to_nothing_exempted(tmp_path: Path, capsys) -> None:
    """A --help-exempt-list path that does not exist degrades to an empty
    exempt set with a warning (verbose) rather than aborting the lint —
    matching this module's existing degrade-and-warn discipline for a
    missing operator-verbs-manifest or Key Registry."""
    root = _build_fixture_tree(tmp_path)
    doc = _write_doc(
        root,
        "fake.md",
        "# Fake doc\n\n```bash\nteam/scripts/broken_help.sh --project demo\n```\n",
    )
    missing_exempt_list = root / "team" / "config" / "does-not-exist.txt"

    result = _lint.main(
        [
            "--repo-root",
            str(root),
            "--doc",
            str(doc),
            "--help-exempt-list",
            str(missing_exempt_list),
        ]
    )
    out = capsys.readouterr().out

    assert result == 1, "A missing exempt list must not silently exempt anything."
    assert "HELP FAILS: team/scripts/broken_help.sh" in out


def test_real_tree_default_exempt_list_closes_the_named_process_internal_citations() -> None:
    """Live-tree regression guard: the default
    team/config/doc-command-truth-help-exempt.txt closes every
    process-internal HELP FAILS finding the real tree produces (each entry's
    own comment names the citations it covers, e.g. docs/OPERATIONS.md:831,
    docs/docker.md:281, docs/docker.md:397) without touching any other
    finding class."""
    result_capture: dict[str, int] = {}

    import io
    import contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result_capture["code"] = _lint.main(["--repo-root", str(_REPO_ROOT)])
    out = buf.getvalue()

    # Findings print as blank-line-separated blocks (see main()'s
    # `print("")` before each finding), so splitting on a blank line and
    # keeping the HELP FAILS ones captures each finding's full body —
    # including its "Cause:" line, which carries the failing command's
    # exit status (or timeout) and captured stdout/stderr — not just the
    # one-line header a plain per-line filter would keep — a failure here
    # should name the command that failed, not just report that "some HELP
    # FAILS finding" exists.
    help_fails_blocks = [
        block for block in out.split("\n\n") if block.lstrip().startswith("HELP FAILS")
    ]
    assert not help_fails_blocks, (
        "The default exempt list must close every HELP FAILS finding in the "
        "real tree, including the three process-internal citations named "
        "above. Each block below names the failing command, its exit "
        "status (or timeout), and its captured stdout/stderr:\n\n"
        + "\n\n".join(help_fails_blocks)
    )
    assert "process-internal citation(s) exempted from the --help check" in out


# ---------------------------------------------------------------------------
# Test 11: no probe process outlives the lint
# ---------------------------------------------------------------------------

_FOLLOW_FOREGROUND = """#!/usr/bin/env bash
# Ignores --help and follows a log the way a dashboard logs pane does.
mkdir -p "$PGAI_AGENT_KANBAN_ROOT_PATH/logs"
touch "$PGAI_AGENT_KANBAN_ROOT_PATH/logs/cron-pm.log"
tail -F "$PGAI_AGENT_KANBAN_ROOT_PATH/logs/cron-pm.log" | awk '{ print }'
"""

_FOLLOW_BACKGROUND = """#!/usr/bin/env bash
# Prints help, but first leaves a follow pipeline running in the background.
mkdir -p "$PGAI_AGENT_KANBAN_ROOT_PATH/logs"
touch "$PGAI_AGENT_KANBAN_ROOT_PATH/logs/cron-pm.log"
tail -F "$PGAI_AGENT_KANBAN_ROOT_PATH/logs/cron-pm.log" </dev/null 2>/dev/null | awk '{ print }' >/dev/null 2>&1 &
echo "Usage: follow_bg.sh [--help]"
exit 0
"""


def _live_processes_with_args_containing(fragment: str) -> list[str]:
    """Return 'pid: args' for every live, non-zombie process whose
    command line contains *fragment* (read from /proc)."""
    hits: list[str] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        args = raw.replace(b"\0", b" ").decode("utf-8", "replace").strip()
        if fragment in args:
            hits.append(f"{entry.name}: {args}")
    return hits


def _wait_until_no_process_matches(fragment: str, deadline_seconds: float = 5.0) -> list[str]:
    """Poll until no live process carries *fragment*, or the deadline
    passes; return whatever still matches at the end. SIGKILL delivery is
    asynchronous, so a just-killed group can be visible for a moment."""
    stop = time.monotonic() + deadline_seconds
    hits = _live_processes_with_args_containing(fragment)
    while hits and time.monotonic() < stop:
        time.sleep(0.05)
        hits = _live_processes_with_args_containing(fragment)
    return hits


def _run_lint_over_follow_script(tmp_path: Path, monkeypatch, script_body: str, timeout: str):
    root = _build_fixture_tree(tmp_path)
    script = root / "team" / "scripts" / "follow_logs.sh"
    script.write_text(script_body, encoding="utf-8")
    script.chmod(0o755)
    doc = _write_doc(root, "fake.md", "# Fake doc\n\n```bash\nteam/scripts/follow_logs.sh\n```\n")
    temp_root = tmp_path / "framework_tmp"
    monkeypatch.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(temp_root))
    code = _lint.main(["--repo-root", str(root), "--doc", str(doc), "--help-timeout", timeout])
    sandbox_prefix = f"{temp_root}/lint_doc_command_truth/help-sandbox-"
    return code, temp_root, sandbox_prefix


def test_foreground_follow_pipeline_is_killed_with_its_process_group(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    code, temp_root, sandbox_prefix = _run_lint_over_follow_script(
        tmp_path, monkeypatch, _FOLLOW_FOREGROUND, "1"
    )
    out = capsys.readouterr().out

    assert code == 1
    assert "HELP FAILS: team/scripts/follow_logs.sh" in out
    assert "did not return within" in out
    leftover = _wait_until_no_process_matches(sandbox_prefix)
    assert not leftover, (
        "the lint returned but processes from the probe's group are still "
        "alive (tail -F and awk must both be gone):\n" + "\n".join(leftover)
    )
    assert not list((temp_root / "lint_doc_command_truth").glob("help-sandbox-*")), (
        "the help sandbox directory must be removed when the run ends"
    )


def test_backgrounded_follow_pipeline_is_killed_after_help_exits_zero(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    code, temp_root, sandbox_prefix = _run_lint_over_follow_script(
        tmp_path, monkeypatch, _FOLLOW_BACKGROUND, "10"
    )
    out = capsys.readouterr().out

    assert code == 0, out
    assert "HELP FAILS" not in out
    leftover = _wait_until_no_process_matches(sandbox_prefix)
    assert not leftover, (
        "--help exited 0 but the pipeline it backgrounded is still alive:\n"
        + "\n".join(leftover)
    )
    assert not list((temp_root / "lint_doc_command_truth").glob("help-sandbox-*"))


# ---------------------------------------------------------------------------
# Test 12: every CommonMark fence pairs with its own closer
#
# A fence opener tagged with anything other than bash/sh (or a tilde fence)
# must still open a fenced block, and its closer must close it — otherwise
# the closer is taken for an opener and every prose citation up to the next
# bare fence is silently skipped.
# ---------------------------------------------------------------------------

# Each opener is followed by a bad prose citation and then a bare fence, the
# shape that hid the citation when only bare and bash/sh openers were
# recognised.
_TAGGED_FENCE_OPENERS = [
    ("```text", "```"),
    ("```ini", "```"),
    ("```python", "```"),
    ("~~~", "~~~"),
    ("~~~yaml", "~~~"),
    ("````json title=\"x\"", "````"),
]


@pytest.mark.parametrize(("opener", "closer"), _TAGGED_FENCE_OPENERS)
def test_prose_citation_after_any_tagged_fence_is_scanned(
    tmp_path: Path, capsys, opener: str, closer: str
) -> None:
    root = _build_fixture_tree(tmp_path)
    doc = _write_doc(
        root,
        "fake.md",
        f"# Fake doc\n\n{opener}\nsome text\n{closer}\n\n"
        "Run `team/scripts/does_not_exist.sh --frobnicate` now.\n\n"
        "```\nok\n```\n",
    )

    result = _lint.main(["--repo-root", str(root), "--doc", str(doc)])
    out = capsys.readouterr().out

    assert result == 1, (
        f"The prose citation after a {opener!r} block must be scanned and "
        f"reported.\nstdout: {out!r}"
    )
    assert "scanned 1 citation(s)" in out, f"stdout: {out!r}"
    assert "MISSING COMMAND: team/scripts/does_not_exist.sh" in out, f"stdout: {out!r}"
    assert "docs/fake.md:7" in out, f"Finding does not name the prose line.\nstdout: {out!r}"


def test_non_shell_fenced_block_contents_are_not_scanned(tmp_path: Path, capsys) -> None:
    """A tagged non-shell block is paired and skipped: a script-shaped line
    inside it is never a citation, while bash/sh blocks (an info string with
    trailing attributes included) are still scanned for commands."""
    root = _build_fixture_tree(tmp_path)
    doc = _write_doc(
        root,
        "fake.md",
        "# Fake doc\n\n"
        "```python\nteam/scripts/inside_python_block.sh\n```\n\n"
        "~~~text\n`team/scripts/inside_text_block.sh`\n~~~\n\n"
        "```bash title=\"run it\"\nteam/scripts/inside_bash_block.sh --project demo\n```\n",
    )

    result = _lint.main(["--repo-root", str(root), "--doc", str(doc)])
    out = capsys.readouterr().out

    assert result == 1, f"stdout: {out!r}"
    assert "scanned 1 citation(s)" in out, f"stdout: {out!r}"
    assert "MISSING COMMAND: team/scripts/inside_bash_block.sh" in out, f"stdout: {out!r}"
    assert "inside_python_block.sh" not in out and "inside_text_block.sh" not in out, (
        f"A non-shell fenced block's contents must not be scanned.\nstdout: {out!r}"
    )


def test_shorter_fence_inside_longer_fence_does_not_close_it(tmp_path: Path, capsys) -> None:
    """A closer must be at least as long as its opener: a ``` line inside a
    ````markdown block is content, so the citation inside stays unscanned
    and the prose citation after the real closer is scanned."""
    root = _build_fixture_tree(tmp_path)
    doc = _write_doc(
        root,
        "fake.md",
        "# Fake doc\n\n"
        "````markdown\n```\n`team/scripts/inside_nested_example.sh`\n```\n````\n\n"
        "See `team/scripts/after_nested_example.sh` next.\n",
    )

    result = _lint.main(["--repo-root", str(root), "--doc", str(doc)])
    out = capsys.readouterr().out

    assert result == 1, f"stdout: {out!r}"
    assert "scanned 1 citation(s)" in out, f"stdout: {out!r}"
    assert "MISSING COMMAND: team/scripts/after_nested_example.sh" in out, f"stdout: {out!r}"
    assert "inside_nested_example.sh" not in out, f"stdout: {out!r}"


# ---------------------------------------------------------------------------
# Undocumented installed scripts (the reverse check): every script under
# team/scripts/ (top level, cm/, cleanup/, migrate/, migrations/) must be
# named by an operator doc or listed in the Internal checks section.
# ---------------------------------------------------------------------------

_INTERNAL_CHECKS_DOC = """\
# Operator commands

## halt — stop the wake loop

Run `real_script.sh` to see it work.

## Internal checks

| Script | What it checks | Who runs it |
|---|---|---|
| `broken_help.sh` | A fixture script with no --help. | Nobody. |
| hangs_forever.sh | A fixture script that sleeps. | Nobody. |
{extra}
## See also

Nothing else.
"""


def _write_operator_commands(root: Path, extra: str = "") -> Path:
    """Write docs/operator-commands.md naming the three fixture scripts; *extra* adds Internal checks rows."""
    path = root / "docs" / "operator-commands.md"
    path.write_text(_INTERNAL_CHECKS_DOC.format(extra=extra), encoding="utf-8")
    return path


def _write_script(root: Path, rel: str) -> Path:
    """Create an executable fixture script at team/scripts/<rel>."""
    path = root / "team" / "scripts" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/usr/bin/env bash\necho fixture\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _run_without_citations(root: Path) -> int:
    """Run the lint over a document citing nothing, so only the undocumented-script check can find anything."""
    doc = _write_doc(root, "no-citations.md", "# Nothing cited here\n")
    return _lint.main(["--repo-root", str(root), "--doc", str(doc)])


def test_undocumented_script_is_reported_by_name_and_listing_it_clears_it(tmp_path: Path, capsys) -> None:
    root = _build_fixture_tree(tmp_path)
    _write_operator_commands(root)
    assert _run_without_citations(root) == 0, capsys.readouterr().out
    capsys.readouterr()

    _write_script(root, "foo.sh")
    result = _run_without_citations(root)
    out = capsys.readouterr().out
    assert result == 1, f"stdout: {out!r}"
    assert "UNDOCUMENTED SCRIPT: scripts/foo.sh" in out, f"stdout: {out!r}"
    assert "1 finding(s)" in out.split("undocumented-scripts:", 1)[1].splitlines()[0], f"stdout: {out!r}"
    # The remedy opens the Internal checks heading (line 7 of the fixture doc): runnable, no placeholder.
    assert "${EDITOR:-vi} +7 docs/operator-commands.md" in out, f"stdout: {out!r}"

    _write_operator_commands(root, extra="| `foo.sh` | A new fixture check. | Unit runner. |\n")
    result = _run_without_citations(root)
    out = capsys.readouterr().out
    assert result == 0, f"listing foo.sh in Internal checks must clear the finding.\nstdout: {out!r}"
    assert "UNDOCUMENTED SCRIPT" not in out, f"stdout: {out!r}"


def test_internal_checks_section_ends_at_the_next_heading(tmp_path: Path, capsys) -> None:
    # A name after the section's end, in a doc that is not an operator doc,
    # does not count: team/TESTING.md contributes its Internal checks
    # section only.
    root = _build_fixture_tree(tmp_path)
    _write_operator_commands(root)
    _write_script(root, "late.sh")
    testing = root / "team" / "TESTING.md"
    testing.write_text(
        "# Testing\n\n## Internal checks\n\n- `early.sh`\n\n## Elsewhere\n\n- `late.sh`\n",
        encoding="utf-8",
    )
    _write_script(root, "early.sh")
    result = _run_without_citations(root)
    out = capsys.readouterr().out
    assert result == 1, f"stdout: {out!r}"
    assert "UNDOCUMENTED SCRIPT: scripts/late.sh" in out, f"stdout: {out!r}"
    assert "UNDOCUMENTED SCRIPT: scripts/early.sh" not in out, f"stdout: {out!r}"


def test_subfolder_scripts_are_checked_and_other_folders_are_not(tmp_path: Path, capsys) -> None:
    root = _build_fixture_tree(tmp_path)
    _write_operator_commands(root, extra="| cm/promote.py | Named in a plain cell. | CM. |\n")
    _write_script(root, "cm/promote.py")
    _write_script(root, "migrations/migrate_step.sh")
    _write_script(root, "lib/helper.sh")  # lib/ is not a checked folder
    _write_script(root, "cm/__init__.py")  # package markers are never reported
    result = _run_without_citations(root)
    out = capsys.readouterr().out
    assert result == 1, f"stdout: {out!r}"
    assert "UNDOCUMENTED SCRIPT: scripts/migrations/migrate_step.sh" in out, f"stdout: {out!r}"
    for silent in ("cm/promote.py", "lib/helper.sh", "__init__.py"):
        assert f"UNDOCUMENTED SCRIPT: scripts/{silent}" not in out, f"stdout: {out!r}"


def test_a_longer_name_containing_the_script_name_does_not_document_it(tmp_path: Path, capsys) -> None:
    root = _build_fixture_tree(tmp_path)
    _write_operator_commands(root, extra="| `other-foo.sh` | A different script. | Nobody. |\n")
    _write_script(root, "foo.sh")
    _write_script(root, "other-foo.sh")
    result = _run_without_citations(root)
    out = capsys.readouterr().out
    assert result == 1, f"stdout: {out!r}"
    assert "UNDOCUMENTED SCRIPT: scripts/foo.sh" in out, f"stdout: {out!r}"
    assert "UNDOCUMENTED SCRIPT: scripts/other-foo.sh" not in out, f"stdout: {out!r}"


def test_tree_without_operator_docs_skips_the_check_with_a_note(tmp_path: Path, capsys) -> None:
    root = _build_fixture_tree(tmp_path)
    _write_script(root, "foo.sh")
    result = _run_without_citations(root)
    captured = capsys.readouterr()
    assert result == 0, f"stdout: {captured.out!r}"
    assert "undocumented-script check skipped" in captured.err, f"stderr: {captured.err!r}"


def test_real_tree_ships_no_undocumented_script() -> None:
    undocumented, checked, where = _lint.run_undocumented_script_check(
        _TEAM_DIR / "scripts",
        [(_REPO_ROOT / rel, rel) for rel in _lint._OPERATOR_DOCS],
        [(_REPO_ROOT / rel, rel) for rel in _lint._INTERNAL_CHECKS_DOCS],
    )
    assert checked > 100, f"implausibly few installed scripts found: {checked}"
    assert where is not None and where[0] == "docs/operator-commands.md", where
    assert [u.rel for u in undocumented] == [], (
        "scripts the install ships that no operator doc names and no Internal checks section lists: "
        f"{[u.rel for u in undocumented]}; add a line for each to docs/operator-commands.md's Internal checks section"
    )
