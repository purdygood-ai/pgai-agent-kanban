"""
test_lint_hygiene.py
======================
Tests for team/scripts/lint_hygiene.py -- the single hygiene-lint entry
point -- and its "provenance", "version-named-test", and "comment-drift"
classes. The "operator-literal" class, registered alongside them, is
covered by test_lint_hygiene_operator_literal.py.

Tests cover:

  1. **Registry** -- "provenance", "version-named-test", and "comment-drift"
     are each registered hygiene classes.

  2. **Baseline detection** -- a clean fixture produces zero findings; a
     genuine citation is flagged, naming the correct class/file/line/label,
     with a runnable remedy; the lint's own exit code is always 0 except
     for a malformed sidecar (see 5).

  3. **Every provenance pattern, both directions** -- for each of the seven
     patterns, a genuine citation produces exactly one finding naming that
     pattern, and the same citation wrapped in a documented exception
     (a format/usage example) produces none.

  3b. **task-ID's short-form spelling** -- the abbreviated <ROLE>-NNN shape
     (no date segment) is flagged exactly like the long <ROLE>-YYYYMMDD-NNN
     form; the first segment of an unrelated longer multi-segment
     identifier, and a digit run too long to be a real sequence number yet
     too short to be the long form's date segment, are both left alone.

  4. **Version-shaped matchers are anchored** -- for each of the four
     version-shaped patterns, a version-shaped substring embedded right
     after a dot in a longer identifier produces zero findings, while the
     same citation standing alone produces exactly one.

  5. **Sidecar suppression** -- a matching sidecar entry (file, class,
     content-hash-of-the-line) suppresses the finding; editing the flagged
     line's text changes the hash and the finding reappears; every
     malformed-sidecar shape (unreadable file, invalid JSON, a non-list
     top level, a non-object entry, a missing required field, a blank
     justification) is reported as its own finding and honours no
     suppression from the broken file, but never aborts the run; the OLD
     lint's in-file ``# provenance-allowlist: ...`` comment marker
     suppresses nothing here.

  6. **--add-suppression** appends a sidecar entry derived from the file's
     actual current text, and that entry suppresses the next run.

  7. **Surface exemptions survive the move** -- the prose surface exempts
     BUG-ID/PRIORITY-ID/bare-release-slug but still enforces task-ID; the
     test-tree surface exempts a bug/task-ID-shaped ordinary string VALUE
     (not a comment, docstring, assert-message clause, or pytest.fail
     argument) and never enforces unshipped-requirements-path/bare-release-
     slug at all, while a genuine comment-position citation, an
     assert-message citation, or a pytest.fail-argument citation there is
     still flagged -- and a bug-shaped value used as literal test data in
     the assert's own CONDITION (before its top-level comma) stays exempt
     even when the same statement carries a message clause.

  8. **Findings-file shape** -- each JSON entry carries lint/class/
     severity/file/line/label/token/cause/remedy.

  9. **version-named-test class** -- a version-named test MODULE and a
     version-named test FUNCTION each produce exactly one finding, naming
     the correct label; a test whose name contains a version-shaped token
     as the SUBJECT under test (not immediately after the ``test_``
     prefix) produces none; the file-name finding's line/text is the
     file's own line 1 (not the synthesized module stem), so
     ``--add-suppression`` suppresses it the same way as every other
     class.

  10. **comment-drift class** -- a ``#`` comment claiming an argument count
      immediately above a single-line ``def`` that contradicts it produces
      one finding; the same comment made true again produces none; the
      same two directions for a single-line docstring claiming its
      ENCLOSING function's own argument count; a comment that is merely
      general (states no checkable count) is never a candidate regardless
      of what def follows it.

  11. **Config surface** -- a planted provenance citation in a
      ``.manifest`` file, a ``.census`` file and a ``.txt`` file are each
      flagged the same way a code-surface citation is (no surface-specific
      exemption); a file under the config directory with none of those
      suffixes is not scanned; a clean config fixture produces zero
      findings.

  12. **Scan-surface routing** -- a ``tests/`` directory nested beneath a
      code-surface directory is routed to the test-tree surface rather
      than dropped: a bug-shaped VALUE literal inside it stays exempt
      (test-tree rules), a genuine comment-position citation inside it is
      still flagged (proving the file was read, not silently skipped),
      and the same VALUE literal is flagged when the nested directory is
      instead named directly as its own code-surface target (proving the
      exemption comes from routing, not a blanket rule).

  13. **Extensionless shebang executables and the fixtures/ test-tree
      surface** -- an executable regular file with no suffix
      whose first line is a shebang is collected on the code surface
      (mirroring ``team/scripts/wake/git-guard/git``), while the same
      shape without the executable bit, and a same-shaped file with no
      shebang, are both left uncollected -- the selector widened to
      "extensionless shebang executable", not to "every extensionless
      file". Separately, ``fixtures/`` is no longer a blanket exclusion on
      the test-tree surface: an ordinary module directly under a
      ``fixtures/`` directory is now scanned, while a name on the real
      by-name exclusion lists (a top-level ``fixture_*`` file in
      ``_FILE_WHITELIST``, or a subdirectory named in this surface's own
      exclude-segment set) stays excluded exactly as before.

  14. **Docs surface** -- a ``.md`` file under ``--docs-dirs`` (default
      ``docs/``) is a first-class scan surface enforcing the full pattern
      set with NO surface-specific exemption (a BUG-ID that would be
      sanctioned content on the release-notes prose surface is flagged
      here), and no subtree or filename inside it is carved out: an
      ``audits/`` or ``checkpoints/`` document is enforced like
      any other.

  15. **String-literal content stays out of scope for the provenance
      class, by documented decision** -- an ordinary string-literal
      VALUE on the test-tree surface (``PROBE = ("# ... BUG-9999 ...
      \\n")``) produces no finding whether or not its own text happens to
      look like a comment, pinning the module docstring's "What changed
      in the move" item 3 decision as actual lint behavior rather than
      prose that could silently drift from what the code does.

  16. **version-history and the surfaces it reaches** -- a seeded
      ``since v1.2.3`` line in a docs/ fixture and in a team/roles/
      fixture are both reported through the default surfaces; each of the
      four shapes (``since vN.N``, ``(vN.N.N)``, ``added in vN``,
      ``vN.N.N+``) is reported on a code comment and on a docs line, while
      a ``vX.Y.Z`` placeholder, a ``--since`` option value, a quoted call
      argument, version arithmetic and an e.g.-marked illustration are
      not; a release-notes/ line and the root CHANGELOG.md are not
      reported, and docs/api/ICD_CHANGELOG.md is exempt from
      version-history alone; on the test-tree surface a comment is
      reported while a string value, and a docstring naming the version
      its own fixture uses, are not; a root ``*.md``, ``subagents/``,
      ``team/templates/``, ``team/pm-agent/``, ``docker/``, a root
      ``*_example`` and a ``team/config/*.txt`` file are each scanned and
      the lint still exits 0; the shipped-text surface skips binary files
      and ``tests/``/``__pycache__/`` subtrees; and an explicit
      ``--text-dirs`` list leaves the repo-root files out.

  17. **Identifiers only, wording unchecked, on the test-tree surface** --
      with ``--classes provenance``, a test-tree comment or docstring
      reading "the fix ensures ..." or "per the ticket's acceptance
      criteria" gives zero findings; a comment carrying ``BUG-0123`` gives
      exactly one BUG-ID finding, also when it shares the line with such
      wording; a comment reading "since v1.2.3" gives exactly one
      version-history finding when that version is absent from the file's
      string literals; an e.g.-marked identifier stays exempt there as on
      every surface; and ``--help`` says wording is not checked.

Every fixture lives entirely under pytest's ``tmp_path`` and every
directory (code/test-tree/prose/sidecar/repo-root) is passed explicitly --
this suite never reads or writes the repository tree. The framework's own
temp-root convention (team/tests/conftest.py's ``PYTEST_DEBUG_TEMPROOT``
redirect) nests every ``tmp_path`` under a directory literally named
``tests``; every test below still gets real findings out of its ``tmp_path``
fixtures, proving the lint's scan-surface exclusion is relative to the
directory a caller actually names, never to an ancestor of it.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Load lint_hygiene.py as a module.
# Path: team/tests/unit/ -> team/ (two levels up) -> team/scripts/
# ---------------------------------------------------------------------------
_THIS_FILE = Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_LINT_SCRIPT = _SCRIPTS_DIR / "lint_hygiene.py"


def _import_lint_module():
    spec = importlib.util.spec_from_file_location("lint_hygiene", _LINT_SCRIPT)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load spec from {_LINT_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    # Register in sys.modules before exec: the module uses @dataclass, and
    # dataclasses.dataclass() resolves its own module via sys.modules while
    # processing fields -- an unregistered module makes that lookup return
    # None and crash instead of importing.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_lint = _import_lint_module()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _empty_dir(tmp_path: Path, name: str) -> Path:
    d = tmp_path / name
    d.mkdir(exist_ok=True)
    return d


def _write(directory: Path, name: str, content: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(content, encoding="utf-8")
    return path


def _run(
    tmp_path: Path,
    *,
    code_dir: Path | None = None,
    test_tree_dir: Path | None = None,
    prose_dir: Path | None = None,
    docs_dir: Path | None = None,
    config_dir: Path | None = None,
    text_dir: Path | None = None,
    sidecar: Path | None = None,
    findings_file: Path | None = None,
    classes: list[str] | None = None,
) -> tuple[int, list[dict]]:
    """Invoke lint_hygiene.main() with every surface pinned to an explicit,
    caller-controlled directory (never the repo-inferred default), and
    return (exit_code, findings). *findings_file* defaults to a fresh path
    under tmp_path so every call gets a machine-readable result regardless
    of what the caller passed. *classes*, when given, is passed as
    ``--classes``; otherwise every registered class runs."""
    findings_path = findings_file or (tmp_path / "findings.json")
    argv = [
        "--code-dirs", str(code_dir or _empty_dir(tmp_path, "empty_code")),
        "--test-tree-dirs", str(test_tree_dir or _empty_dir(tmp_path, "empty_test_tree")),
        "--prose-dirs", str(prose_dir or _empty_dir(tmp_path, "empty_prose")),
        "--docs-dirs", str(docs_dir or _empty_dir(tmp_path, "empty_docs")),
        "--config-dirs", str(config_dir or _empty_dir(tmp_path, "empty_config")),
        "--text-dirs", str(text_dir or _empty_dir(tmp_path, "empty_text")),
        "--repo-root", str(tmp_path),
        "--sidecar", str(sidecar or (tmp_path / "sidecar.json")),
        "--findings-file", str(findings_path),
    ]
    if classes is not None:
        argv += ["--classes", *classes]
    result = _lint.main(argv)
    findings = json.loads(findings_path.read_text(encoding="utf-8")) if findings_path.exists() else []
    return result, findings


# ---------------------------------------------------------------------------
# 1. Registry
# ---------------------------------------------------------------------------


def test_provenance_class_is_registered() -> None:
    assert "provenance" in _lint.registered_class_names()


def test_version_named_test_class_is_registered() -> None:
    assert "version-named-test" in _lint.registered_class_names()


def test_comment_drift_class_is_registered() -> None:
    assert "comment-drift" in _lint.registered_class_names()


# ---------------------------------------------------------------------------
# 2. Baseline detection
# ---------------------------------------------------------------------------


def test_clean_fixture_produces_zero_findings(tmp_path: Path) -> None:
    code_dir = _empty_dir(tmp_path, "code")
    _write(code_dir, "clean.py", "def add(a, b):\n    # Adds two numbers and returns the sum.\n    return a + b\n")

    result, findings = _run(tmp_path, code_dir=code_dir)

    assert result == 0
    assert findings == []


def test_genuine_bug_id_is_flagged_with_remedy(tmp_path: Path) -> None:
    code_dir = _empty_dir(tmp_path, "code")
    _write(code_dir, "dirty.py", "def foo():\n    # This references BUG-0042 directly in a comment.\n    return 1\n")

    result, findings = _run(tmp_path, code_dir=code_dir)

    assert result == 0, "a real finding must not make the lint exit non-zero"
    assert len(findings) == 1
    f = findings[0]
    assert f["class"] == "provenance"
    assert f["label"] == "BUG-ID"
    assert f["token"] == "BUG-0042"
    assert f["file"] == "code/dirty.py"
    assert f["line"] == 2
    assert f["remedy"], "every finding must carry a non-empty remedy"


# ---------------------------------------------------------------------------
# 3. Every provenance pattern, both directions (genuine vs. its exception)
# ---------------------------------------------------------------------------

_PATTERN_CASES = [
    ("BUG-ID", "# This references BUG-0042 directly in a comment.", "# e.g. BUG-0042 shows the expected key format."),
    ("task-ID", "# Filed as CODER-20260101-001 for tracking.", "# e.g. CODER-20260101-001 is the task-ID format."),
    ("PRIORITY-ID", "# Addressed by PRIORITY-0004 in this change.", "# e.g. PRIORITY-0004 is the format."),
    ("version-bundle", "# See v1.0.0-priority-bundle for background.", "# e.g. v1.0.0-priority-bundle is the bundle-name format."),
    ("requirement-Goal", "# See requirements v2.0.0 Goal 4 for design rationale.", "# e.g. v2.0.0 Goal 4 shows the citation format."),
    ("unshipped-requirements-path", "# See requirements/v2.0.0-notes.md for details.", "# e.g. requirements/v2.0.0-notes.md is the path format."),
    ("bare-release-slug", "# See v2.9.20-engine-decomposition for background.", "# e.g. v2.9.20-engine-decomposition is the slug format."),
]


@pytest.mark.parametrize("label,genuine_line,exempt_line", _PATTERN_CASES, ids=[c[0] for c in _PATTERN_CASES])
def test_pattern_genuine_citation_flagged(tmp_path: Path, label: str, genuine_line: str, exempt_line: str) -> None:
    code_dir = _empty_dir(tmp_path, f"code_{label}")
    _write(code_dir, "fixture.py", f"def f():\n    {genuine_line}\n    return 1\n")

    result, findings = _run(tmp_path, code_dir=code_dir)

    assert result == 0
    assert len(findings) == 1, f"expected exactly one finding for {label}, got {findings}"
    assert findings[0]["label"] == label


@pytest.mark.parametrize("label,genuine_line,exempt_line", _PATTERN_CASES, ids=[c[0] for c in _PATTERN_CASES])
def test_pattern_format_example_exempt(tmp_path: Path, label: str, genuine_line: str, exempt_line: str) -> None:
    code_dir = _empty_dir(tmp_path, f"code_{label}_exempt")
    _write(code_dir, "fixture.py", f"def f():\n    {exempt_line}\n    return 1\n")

    result, findings = _run(tmp_path, code_dir=code_dir)

    assert result == 0
    assert findings == [], f"a format/usage-example line must not be flagged for {label}: {findings}"


# ---------------------------------------------------------------------------
# 3b. task-ID's short-form spelling (<ROLE>-NNN, no date segment) is flagged
# alongside the long form, without opening the pattern to unrelated numbers
# ---------------------------------------------------------------------------


def test_short_form_task_id_is_flagged_with_remedy(tmp_path: Path) -> None:
    """The abbreviated <ROLE>-NNN spelling -- the shape agents actually write
    in prose, e.g. "ticket CODER-009's field" -- is a task-ID citation exactly
    like the long form, with the same finding shape and a runnable remedy."""
    code_dir = _empty_dir(tmp_path, "code_short_task_id")
    _write(
        code_dir,
        "fixture.py",
        "def f():\n    # Both RCs predate ticket CODER-009's own field.\n    return 1\n",
    )

    result, findings = _run(tmp_path, code_dir=code_dir)

    assert result == 0
    assert len(findings) == 1
    f = findings[0]
    assert f["label"] == "task-ID"
    assert f["token"] == "CODER-009"
    assert f["remedy"], "every finding must carry a non-empty remedy"


def test_short_form_task_id_first_segment_of_longer_identifier_not_flagged(tmp_path: Path) -> None:
    """A short-form-shaped PREFIX of a longer, unrelated multi-segment
    identifier (e.g. a fixture name built for prefix-matching tests) is not
    a task-ID citation. This is the exact collision risk the bug report
    names: a bare ROLE-<digits> must not fire on the first segment of
    something else shaped like it."""
    code_dir = _empty_dir(tmp_path, "code_short_task_id_prefix")
    _write(
        code_dir,
        "fixture.py",
        "def f():\n    # CODER-001-0020 should not match prefix CODER-001-002.\n    return 1\n",
    )

    result, findings = _run(tmp_path, code_dir=code_dir)

    assert result == 0
    assert findings == [], f"a multi-segment identifier's first segment must not be flagged: {findings}"


def test_short_form_task_id_bound_excludes_overlong_digit_runs(tmp_path: Path) -> None:
    """A ROLE-<digits> shape whose digit run is longer than any real
    short-form sequence number, and shorter than the long form's mandatory
    eight-digit date segment, is unrelated prose (e.g. a coincidentally
    ROLE-prefixed reference number) rather than a task-ID citation, and is
    left alone."""
    code_dir = _empty_dir(tmp_path, "code_short_task_id_overlong")
    _write(
        code_dir,
        "fixture.py",
        "def f():\n    # See ticket PM-123456 in the external tracker.\n    return 1\n",
    )

    result, findings = _run(tmp_path, code_dir=code_dir)

    assert result == 0
    assert findings == [], f"an overlong digit run must not be treated as a short-form task-ID: {findings}"


# ---------------------------------------------------------------------------
# 4. Version-shaped matchers are anchored against mid-identifier embedding
# ---------------------------------------------------------------------------

_ANCHOR_CASES = [
    ("version-bundle", 'slug = "pkg.v1.0.0-priority-bundle"  # embedded, not a citation', "# See v1.0.0-priority-bundle for background."),
    ("requirement-Goal", 'slug = "notes.v2.0.0 Goal 4 appendix"  # embedded, not a citation', "# See v2.0.0 Goal 4 for design rationale."),
    ("unshipped-requirements-path", 'slug = "cfg.requirements/v2.0.0-notes.md"  # embedded, not a citation', "# See requirements/v2.0.0-notes.md for details."),
    ("bare-release-slug", 'slug = "pkg.v2.16.0-widget-name"  # embedded, not a citation', "# See v2.16.0-widget-name for background."),
]


@pytest.mark.parametrize("label,embedded_line,genuine_line", _ANCHOR_CASES, ids=[c[0] for c in _ANCHOR_CASES])
def test_version_matcher_anchored_against_embedding(tmp_path: Path, label: str, embedded_line: str, genuine_line: str) -> None:
    embedded_dir = _empty_dir(tmp_path, f"embedded_{label}")
    _write(embedded_dir, "fixture.py", f"def f():\n    {embedded_line}\n    return 1\n")
    result, findings = _run(tmp_path, code_dir=embedded_dir)
    assert result == 0
    assert findings == [], (
        f"a {label}-shaped substring embedded right after a dot in a longer "
        f"identifier must not be flagged: {findings}"
    )

    genuine_dir = _empty_dir(tmp_path, f"genuine_{label}")
    _write(genuine_dir, "fixture.py", f"def f():\n    {genuine_line}\n    return 1\n")
    result, findings = _run(tmp_path, code_dir=genuine_dir, sidecar=tmp_path / f"sidecar_{label}.json")
    assert result == 0
    assert len(findings) == 1, f"the same citation standing alone must still be flagged for {label}: {findings}"
    assert findings[0]["label"] == label


# ---------------------------------------------------------------------------
# 5. Sidecar suppression
# ---------------------------------------------------------------------------


def test_sidecar_entry_suppresses_matching_finding(tmp_path: Path) -> None:
    code_dir = _empty_dir(tmp_path, "code")
    flagged_line = "# This references BUG-0042 directly in a comment."
    _write(code_dir, "dirty.py", f"def foo():\n    {flagged_line}\n    return 1\n")
    sidecar = tmp_path / "sidecar.json"

    result, findings = _run(tmp_path, code_dir=code_dir, sidecar=sidecar)
    assert len(findings) == 1

    content_hash = _lint._line_fingerprint(flagged_line)
    sidecar.write_text(
        json.dumps([
            {
                "file": "code/dirty.py",
                "class": "provenance",
                "content_hash": content_hash,
                "line_hint": 2,
                "justification": "retained intentionally for this test",
            }
        ]),
        encoding="utf-8",
    )

    result, findings = _run(tmp_path, code_dir=code_dir, sidecar=sidecar)
    assert result == 0
    assert findings == [], "a matching sidecar entry must suppress the finding"


def test_sidecar_entry_stops_matching_after_line_edited(tmp_path: Path) -> None:
    code_dir = _empty_dir(tmp_path, "code")
    original = "# This references BUG-0042 directly in a comment."
    _write(code_dir, "dirty.py", f"def foo():\n    {original}\n    return 1\n")
    sidecar = tmp_path / "sidecar.json"
    content_hash = _lint._line_fingerprint(original)
    sidecar.write_text(
        json.dumps([{
            "file": "code/dirty.py", "class": "provenance", "content_hash": content_hash,
            "line_hint": 2, "justification": "retained intentionally for this test",
        }]),
        encoding="utf-8",
    )
    result, findings = _run(tmp_path, code_dir=code_dir, sidecar=sidecar)
    assert findings == [], "sanity: the unedited line is suppressed"

    # Edit the flagged line's own text (beyond leading/trailing whitespace).
    # Content-hash anchoring means the entry now stops matching -- unlike a
    # line-number pin, a shift caused by edits ELSEWHERE would not have
    # affected it, but editing the suppressed line itself always does.
    _write(code_dir, "dirty.py", "def foo():\n    # This references BUG-0042 differently now.\n    return 1\n")

    result, findings = _run(tmp_path, code_dir=code_dir, sidecar=sidecar)
    assert result == 0
    assert len(findings) == 1, "an edited suppressed line must reappear as a finding"


def _sidecar_finding(findings: list[dict]) -> dict:
    """Return the single ``hygiene-sidecar``-class finding from *findings*,
    failing loudly if it is absent or duplicated."""
    matches = [f for f in findings if f["class"] == "hygiene-sidecar"]
    assert len(matches) == 1, f"expected exactly one sidecar finding, got {findings}"
    return matches[0]


def test_blank_justification_is_reported_as_a_finding_not_an_abort(tmp_path: Path) -> None:
    code_dir = _empty_dir(tmp_path, "code")
    _write(code_dir, "dirty.py", "def foo():\n    # This references BUG-0042 directly in a comment.\n    return 1\n")
    sidecar = tmp_path / "sidecar.json"
    sidecar.write_text(
        json.dumps([{
            "file": "code/dirty.py", "class": "provenance", "content_hash": "deadbeef0000",
            "line_hint": 2, "justification": "   ",
        }]),
        encoding="utf-8",
    )

    result, findings = _run(tmp_path, code_dir=code_dir, sidecar=sidecar)

    assert result == 0, "a malformed sidecar must never abort the run"
    finding = _sidecar_finding(findings)
    assert finding["label"] == "SIDECAR-MALFORMED"
    assert "code/dirty.py" in finding["cause"]
    assert "blank justification" in finding["cause"]
    assert str(tmp_path) not in finding["cause"], (
        "the recorded cause must name the sidecar relative to the scan root, "
        "not by the scan root's own (possibly ephemeral) absolute path"
    )
    # The unrelated real finding on the scanned file must still surface --
    # a malformed sidecar must not swallow findings it has nothing to do with.
    assert any(f["class"] == "provenance" for f in findings)


def test_malformed_sidecar_honours_no_suppression_from_itself(tmp_path: Path) -> None:
    """A sidecar carrying one entry that WOULD suppress a real finding and
    one blank-justification entry must honour neither: the whole file
    fails to load, so no suppression it names takes effect, not merely the
    malformed entry."""
    code_dir = _empty_dir(tmp_path, "code")
    flagged_line = "# This references BUG-0042 directly in a comment."
    _write(code_dir, "dirty.py", f"def foo():\n    {flagged_line}\n    return 1\n")
    sidecar = tmp_path / "sidecar.json"
    content_hash = _lint._line_fingerprint(flagged_line)
    sidecar.write_text(
        json.dumps([
            {
                "file": "code/dirty.py", "class": "provenance", "content_hash": content_hash,
                "line_hint": 2, "justification": "would suppress the finding if the sidecar loaded",
            },
            {
                "file": "code/other.py", "class": "provenance", "content_hash": "deadbeef0000",
                "line_hint": 1, "justification": "",
            },
        ]),
        encoding="utf-8",
    )

    result, findings = _run(tmp_path, code_dir=code_dir, sidecar=sidecar)

    assert result == 0
    assert any(f["class"] == "provenance" for f in findings), (
        "a suppression from a sidecar that also fails to load must not be honoured"
    )
    _sidecar_finding(findings)


def test_invalid_json_sidecar_is_reported_as_a_finding(tmp_path: Path) -> None:
    code_dir = _empty_dir(tmp_path, "code")
    sidecar = tmp_path / "sidecar.json"
    sidecar.write_text("{not valid json", encoding="utf-8")

    result, findings = _run(tmp_path, code_dir=code_dir, sidecar=sidecar)

    assert result == 0, "a malformed sidecar must never abort the run"
    finding = _sidecar_finding(findings)
    assert "not valid JSON" in finding["cause"]
    assert str(tmp_path) not in finding["cause"], (
        "the recorded cause must name the sidecar relative to the scan root, "
        "not by the scan root's own (possibly ephemeral) absolute path"
    )


def test_non_list_sidecar_is_reported_as_a_finding(tmp_path: Path) -> None:
    code_dir = _empty_dir(tmp_path, "code")
    sidecar = tmp_path / "sidecar.json"
    sidecar.write_text(json.dumps({"not": "a list"}), encoding="utf-8")

    result, findings = _run(tmp_path, code_dir=code_dir, sidecar=sidecar)

    assert result == 0, "a malformed sidecar must never abort the run"
    finding = _sidecar_finding(findings)
    assert "JSON array" in finding["cause"]


def test_non_object_entry_sidecar_is_reported_as_a_finding(tmp_path: Path) -> None:
    code_dir = _empty_dir(tmp_path, "code")
    sidecar = tmp_path / "sidecar.json"
    sidecar.write_text(json.dumps(["not an object"]), encoding="utf-8")

    result, findings = _run(tmp_path, code_dir=code_dir, sidecar=sidecar)

    assert result == 0, "a malformed sidecar must never abort the run"
    finding = _sidecar_finding(findings)
    assert "not a JSON object" in finding["cause"]


def test_missing_content_hash_sidecar_is_reported_as_a_finding(tmp_path: Path) -> None:
    code_dir = _empty_dir(tmp_path, "code")
    sidecar = tmp_path / "sidecar.json"
    sidecar.write_text(
        json.dumps([{"file": "code/dirty.py", "class": "provenance", "justification": "ok"}]),
        encoding="utf-8",
    )

    result, findings = _run(tmp_path, code_dir=code_dir, sidecar=sidecar)

    assert result == 0, "a malformed sidecar must never abort the run"
    finding = _sidecar_finding(findings)
    assert "missing required field" in finding["cause"]
    assert "content_hash" in finding["cause"]
    assert str(tmp_path) not in finding["cause"], (
        "the recorded cause must name the sidecar relative to the scan root, "
        "not by the scan root's own (possibly ephemeral) absolute path"
    )


def test_unreadable_sidecar_is_reported_as_a_finding(tmp_path: Path) -> None:
    """A sidecar path that cannot be read as a file (here: it names a
    directory instead) is the OSError shape -- reported the same way as
    every other malformed sidecar, never an aborted run."""
    code_dir = _empty_dir(tmp_path, "code")
    sidecar = tmp_path / "sidecar.json"
    sidecar.mkdir()

    result, findings = _run(tmp_path, code_dir=code_dir, sidecar=sidecar)

    assert result == 0, "a malformed sidecar must never abort the run"
    finding = _sidecar_finding(findings)
    assert "could not be read" in finding["cause"]
    assert str(tmp_path) not in finding["cause"], (
        "the recorded cause must name the sidecar relative to the scan root, not "
        "by the scan root's own absolute path -- including the copy of that path "
        "the underlying OSError's own message embeds a second time"
    )


def test_in_file_marker_does_not_suppress(tmp_path: Path) -> None:
    """The old lint_comment_provenance.py opt-out comment is not a supported
    fallback here -- a file carrying it gets no suppression from it."""
    code_dir = _empty_dir(tmp_path, "code")
    _write(
        code_dir,
        "dirty.py",
        "def foo():\n"
        "    # provenance-allowlist: old marker, should be ignored now\n"
        "    # This references BUG-0099 directly in a comment.\n"
        "    return 1\n",
    )

    result, findings = _run(tmp_path, code_dir=code_dir)

    assert result == 0
    assert len(findings) == 1
    assert findings[0]["token"] == "BUG-0099"


# ---------------------------------------------------------------------------
# 6. --add-suppression
# ---------------------------------------------------------------------------


def test_add_suppression_appends_and_then_suppresses(tmp_path: Path) -> None:
    code_dir = _empty_dir(tmp_path, "code")
    _write(code_dir, "dirty.py", "def foo():\n    # This references BUG-0042 directly in a comment.\n    return 1\n")
    sidecar = tmp_path / "sidecar.json"

    result, findings = _run(tmp_path, code_dir=code_dir, sidecar=sidecar)
    assert len(findings) == 1

    exit_code = _lint.main([
        "--add-suppression", "code/dirty.py", "2", "provenance", "test justification",
        "--repo-root", str(tmp_path),
        "--sidecar", str(sidecar),
    ])
    assert exit_code == 0

    entries = json.loads(sidecar.read_text(encoding="utf-8"))
    assert len(entries) == 1
    assert entries[0]["file"] == "code/dirty.py"
    assert entries[0]["class"] == "provenance"
    assert entries[0]["justification"] == "test justification"

    result, findings = _run(tmp_path, code_dir=code_dir, sidecar=sidecar)
    assert result == 0
    assert findings == []


def test_add_suppression_rejects_out_of_range_line(tmp_path: Path) -> None:
    code_dir = _empty_dir(tmp_path, "code")
    _write(code_dir, "dirty.py", "def foo():\n    return 1\n")
    sidecar = tmp_path / "sidecar.json"

    exit_code = _lint.main([
        "--add-suppression", "code/dirty.py", "99", "provenance", "test justification",
        "--repo-root", str(tmp_path),
        "--sidecar", str(sidecar),
    ])
    assert exit_code == 2
    assert not sidecar.exists()


# ---------------------------------------------------------------------------
# 7. Surface exemptions survive the move
# ---------------------------------------------------------------------------


def test_prose_surface_exempts_bug_and_priority_but_not_task_id(tmp_path: Path) -> None:
    prose_dir = _empty_dir(tmp_path, "release-notes")
    _write(
        prose_dir,
        "vTEST.md",
        "## Bug Fixes\n"
        "- BUG-0010: fixed the thing.\n"
        "- PRIORITY-0002: addressed the priority item.\n"
        "- v1.2.3-widget-name shipped in this release.\n",
    )

    result, findings = _run(tmp_path, prose_dir=prose_dir)
    assert result == 0
    assert findings == [], "BUG-ID/PRIORITY-ID/bare-release-slug are sanctioned prose content"

    _write(prose_dir, "vTEST2.md", "## Other Changes\n- Landed as CODER-20260101-005.\n")
    result, findings = _run(tmp_path, prose_dir=prose_dir)
    assert len(findings) == 1
    assert findings[0]["label"] == "task-ID", "task-ID citations remain enforced on the prose surface"


def test_test_tree_surface_exempts_data_value_but_flags_comment_citation(tmp_path: Path) -> None:
    test_tree_dir = _empty_dir(tmp_path, "team_tests")
    _write(
        test_tree_dir,
        "test_value.py",
        "def test_thing():\n"
        '    bug_id = "BUG-0001-foo.md"  # test-data VALUE, not a citation\n'
        "    assert bug_id\n",
    )
    result, findings = _run(tmp_path, test_tree_dir=test_tree_dir)
    assert result == 0
    assert findings == [], "a bug-ID-shaped ordinary string VALUE is test data, not a citation"

    _write(
        test_tree_dir,
        "test_comment.py",
        "def test_thing():\n"
        "    # This test exists because of BUG-0002 filed earlier.\n"
        "    assert True\n",
    )
    result, findings = _run(tmp_path, test_tree_dir=test_tree_dir)
    assert len(findings) == 1
    assert findings[0]["label"] == "BUG-ID", "a genuine comment-position citation is still flagged in the test tree"


def test_test_tree_surface_flags_bug_shaped_token_in_assert_message(tmp_path: Path) -> None:
    """A bug-shaped token inside an ``assert`` statement's own message clause
    is shipped prose a human reads at failure time -- it is flagged even
    though it lives in an ordinary string literal, the same string-literal
    shape the surrounding test-data exemption otherwise protects."""
    test_tree_dir = _empty_dir(tmp_path, "team_tests")
    _write(
        test_tree_dir,
        "test_assert_message.py",
        "def test_thing():\n"
        "    x = 1\n"
        '    assert x == 1, "this failure relates to BUG-0003 historically"\n',
    )
    result, findings = _run(tmp_path, test_tree_dir=test_tree_dir)
    assert result == 0
    assert len(findings) == 1, f"a bug-shaped token in an assert message must be flagged: {findings}"
    assert findings[0]["label"] == "BUG-ID"
    assert findings[0]["line"] == 3


def test_test_tree_surface_flags_bug_shaped_token_in_pytest_fail(tmp_path: Path) -> None:
    """A bug-shaped token inside a ``pytest.fail(...)`` call's argument is
    shipped prose read at failure time, exactly like an assert message, and
    is flagged the same way."""
    test_tree_dir = _empty_dir(tmp_path, "team_tests")
    _write(
        test_tree_dir,
        "test_pytest_fail.py",
        "import pytest\n\n"
        "def test_thing():\n"
        "    if True:\n"
        '        pytest.fail("failure tied to BUG-0004 in this scenario")\n',
    )
    result, findings = _run(tmp_path, test_tree_dir=test_tree_dir)
    assert result == 0
    assert len(findings) == 1, f"a bug-shaped token in a pytest.fail argument must be flagged: {findings}"
    assert findings[0]["label"] == "BUG-ID"


def test_test_tree_surface_assert_condition_data_stays_exempt_even_with_message(tmp_path: Path) -> None:
    """The narrowed exemption targets only the clause AFTER an assert's own
    top-level comma. A bug-shaped value used as literal test data in the
    CONDITION itself -- not the message -- stays exempt, matching a real
    shape in the tree: a test asserting a fixture's own bug-ID-shaped
    filename appears in generated output."""
    test_tree_dir = _empty_dir(tmp_path, "team_tests")
    _write(
        test_tree_dir,
        "test_condition_data.py",
        "def test_thing():\n"
        "    bug_id = \"BUG-0005\"\n"
        "    assert bug_id == \"BUG-0005\", \"mismatch\"\n",
    )
    result, findings = _run(tmp_path, test_tree_dir=test_tree_dir)
    assert result == 0
    assert findings == [], (
        "a bug-shaped value used as literal test data in the assert's own "
        f"condition must stay exempt: {findings}"
    )


def test_test_tree_surface_never_enforces_requirements_path_or_bare_slug(tmp_path: Path) -> None:
    test_tree_dir = _empty_dir(tmp_path, "team_tests")
    _write(
        test_tree_dir,
        "test_fixture_paths.py",
        "def test_thing():\n"
        "    # Fixture: requirements/v2.0.0-stranded.md\n"
        "    # Fixture: v2.0.0-stranded-bundle-name\n"
        "    assert True\n",
    )

    result, findings = _run(tmp_path, test_tree_dir=test_tree_dir)
    assert result == 0
    assert findings == [], (
        "unshipped-requirements-path and bare-release-slug are never enforced "
        f"on the sanctioned test-tree surface: {findings}"
    )


# ---------------------------------------------------------------------------
# 8. Findings-file shape
# ---------------------------------------------------------------------------


def test_findings_file_entries_carry_required_keys(tmp_path: Path) -> None:
    code_dir = _empty_dir(tmp_path, "code")
    _write(code_dir, "dirty.py", "def foo():\n    # This references BUG-0042 directly in a comment.\n    return 1\n")

    _, findings = _run(tmp_path, code_dir=code_dir)

    assert len(findings) == 1
    required = {"lint", "class", "severity", "file", "line", "label", "token", "cause", "remedy"}
    assert required.issubset(findings[0].keys())
    assert findings[0]["lint"] == "hygiene"
    assert findings[0]["severity"] == "quality"


# ---------------------------------------------------------------------------
# 9. version-named-test class
# ---------------------------------------------------------------------------


def test_version_named_test_module_is_flagged(tmp_path: Path) -> None:
    test_tree_dir = _empty_dir(tmp_path, "team_tests")
    _write(test_tree_dir, "test_v2_16_0_fix.py", "def test_thing():\n    assert True\n")

    result, findings = _run(tmp_path, test_tree_dir=test_tree_dir)

    assert result == 0
    assert len(findings) == 1
    f = findings[0]
    assert f["class"] == "version-named-test"
    assert f["label"] == "version-named-test-file"
    assert f["token"] == "v2_16_0"
    assert f["line"] == 1


def test_version_named_test_function_is_flagged(tmp_path: Path) -> None:
    test_tree_dir = _empty_dir(tmp_path, "team_tests")
    _write(
        test_tree_dir,
        "test_release_behaviors.py",
        "def test_thing():\n    assert True\n\n\ndef test_v2_16_0_config_migration():\n    assert True\n",
    )

    result, findings = _run(tmp_path, test_tree_dir=test_tree_dir)

    assert result == 0
    assert len(findings) == 1
    f = findings[0]
    assert f["class"] == "version-named-test"
    assert f["label"] == "version-named-test-function"
    assert f["token"] == "v2_16_0"
    assert f["line"] == 5, "the finding must name the def's own line, not line 1 of the file"


def test_version_shaped_token_as_subject_under_test_is_not_flagged(tmp_path: Path) -> None:
    test_tree_dir = _empty_dir(tmp_path, "team_tests")
    _write(
        test_tree_dir,
        "test_version_comparator.py",
        "def test_compares_v2_16_0_against_v2_15_4_correctly():\n"
        '    assert compare("v2.16.0", "v2.15.4") == 1\n',
    )

    result, findings = _run(tmp_path, test_tree_dir=test_tree_dir)

    assert result == 0
    assert findings == [], (
        "a version-shaped token that is the subject under test (not immediately "
        f"after the test_ prefix) must not be flagged: {findings}"
    )


def test_version_prefixed_function_reappearing_in_body_is_not_flagged(tmp_path: Path) -> None:
    """A version-prefixed function name is NOT flagged when the same
    version, in its dotted prose form, reappears in the function's own
    body -- the mechanical signal that the version is the subject under
    test (e.g. a changelog-writer test asserting on the literal "v1.0.0"
    entry it renders), not a release-provenance label. This mirrors a
    real shape found in the tree during this class's own development:
    ``test_v1_0_0_entry_carries_ki_1_0_0_1_with_tmux_symptom``, whose body
    repeats "v1.0.0" in a regex and in its assertion messages."""
    test_tree_dir = _empty_dir(tmp_path, "team_tests")
    _write(
        test_tree_dir,
        "test_changelog_entries.py",
        "def test_v1_0_0_entry_carries_expected_disclosure(self, tmp_path):\n"
        '    entry_match = re.search(r"## v1\\.0\\.0.*", content)\n'
        '    assert entry_match is not None, "v1.0.0 entry not found"\n',
    )

    result, findings = _run(tmp_path, test_tree_dir=test_tree_dir)

    assert result == 0
    assert findings == [], (
        "a version-prefixed function name must not be flagged when the dotted "
        f"version reappears as literal data in its own body: {findings}"
    )


def test_version_prefixed_module_reappearing_in_file_is_not_flagged(tmp_path: Path) -> None:
    """Same narrowing as the function-level check, applied to the
    module-name finding: the dotted version reappearing anywhere in the
    file's own text suppresses the file-name finding."""
    test_tree_dir = _empty_dir(tmp_path, "team_tests")
    _write(
        test_tree_dir,
        "test_v1_0_0_changelog_entry.py",
        "def test_entry():\n"
        '    assert "v1.0.0" in regenerate_changelog()\n',
    )

    result, findings = _run(tmp_path, test_tree_dir=test_tree_dir)

    assert result == 0
    assert findings == [], (
        "a version-prefixed module name must not be flagged when the dotted "
        f"version reappears as literal data in the file: {findings}"
    )


def test_version_named_test_file_suppression_uses_the_files_own_line_one(tmp_path: Path) -> None:
    """The file-name finding's content-hash must key on the file's OWN line
    1 text -- not the synthesized module stem -- so the same
    --add-suppression CLI every other class uses suppresses it too."""
    test_tree_dir = _empty_dir(tmp_path, "team_tests")
    _write(test_tree_dir, "test_v2_16_0_fix.py", "def test_thing():\n    assert True\n")
    sidecar = tmp_path / "sidecar.json"

    result, findings = _run(tmp_path, test_tree_dir=test_tree_dir, sidecar=sidecar)
    assert len(findings) == 1

    exit_code = _lint.main([
        "--add-suppression", "team_tests/test_v2_16_0_fix.py", "1", "version-named-test",
        "test justification",
        "--repo-root", str(tmp_path),
        "--sidecar", str(sidecar),
    ])
    assert exit_code == 0

    result, findings = _run(tmp_path, test_tree_dir=test_tree_dir, sidecar=sidecar)
    assert result == 0
    assert findings == [], "the sidecar entry authored via --add-suppression must suppress the finding"


# ---------------------------------------------------------------------------
# 10. comment-drift class
# ---------------------------------------------------------------------------


def test_comment_above_def_with_wrong_arg_count_is_flagged(tmp_path: Path) -> None:
    code_dir = _empty_dir(tmp_path, "code")
    _write(
        code_dir,
        "drift.py",
        "# Takes three arguments and returns their sum.\ndef add(a, b):\n    return a + b\n",
    )

    result, findings = _run(tmp_path, code_dir=code_dir)

    assert result == 0
    assert len(findings) == 1
    f = findings[0]
    assert f["class"] == "comment-drift"
    assert f["label"] == "argument-count-drift"
    assert f["line"] == 1


def test_comment_above_def_made_true_again_is_not_flagged(tmp_path: Path) -> None:
    code_dir = _empty_dir(tmp_path, "code")
    _write(
        code_dir,
        "drift.py",
        "# Takes two arguments and returns their sum.\ndef add(a, b):\n    return a + b\n",
    )

    result, findings = _run(tmp_path, code_dir=code_dir)

    assert result == 0
    assert findings == [], f"a comment whose claim matches the def below it must not be flagged: {findings}"


def test_docstring_claiming_enclosing_signature_with_wrong_count_is_flagged(tmp_path: Path) -> None:
    code_dir = _empty_dir(tmp_path, "code")
    _write(
        code_dir,
        "drift.py",
        'def add(a, b):\n    """Takes three arguments and returns their sum."""\n    return a + b\n',
    )

    result, findings = _run(tmp_path, code_dir=code_dir)

    assert result == 0
    assert len(findings) == 1
    f = findings[0]
    assert f["class"] == "comment-drift"
    assert f["label"] == "argument-count-drift"
    assert f["line"] == 2


def test_docstring_claiming_enclosing_signature_made_true_again_is_not_flagged(tmp_path: Path) -> None:
    code_dir = _empty_dir(tmp_path, "code")
    _write(
        code_dir,
        "drift.py",
        'def add(a, b, c):\n    """Takes three arguments and returns their sum."""\n    return a + b + c\n',
    )

    result, findings = _run(tmp_path, code_dir=code_dir)

    assert result == 0
    assert findings == [], f"a docstring whose claim matches its enclosing def must not be flagged: {findings}"


def test_general_comment_with_no_checkable_claim_is_not_a_candidate(tmp_path: Path) -> None:
    """A comment that merely describes intent at a level the code does not
    restate makes no checkable claim -- it is never a candidate, regardless
    of what def follows it, even one whose comment happens to mention a
    number in a context that names no argument/parameter count."""
    code_dir = _empty_dir(tmp_path, "code")
    _write(
        code_dir,
        "clean.py",
        "# Adds two numbers together.\ndef add(a, b):\n    return a + b\n\n\n"
        "# Retries the request up to three times before giving up.\n"
        "def fetch(url):\n    return url\n",
    )

    result, findings = _run(tmp_path, code_dir=code_dir)

    assert result == 0
    assert findings == [], (
        "a comment naming a count that is not an argument/parameter count "
        f"must not be flagged: {findings}"
    )


# ---------------------------------------------------------------------------
# 11. Config surface -- declarative config (.manifest/.census) and plain-
# text lists (.txt) are a first-class scan surface, enforcing the full
# provenance pattern set with no surface-specific exemption.
# ---------------------------------------------------------------------------


def test_config_surface_flags_a_planted_task_id_in_a_manifest_file(tmp_path: Path) -> None:
    config_dir = _empty_dir(tmp_path, "config")
    _write(
        config_dir,
        "operator_verbs.manifest",
        "# Header comment.\n"
        "# Planted for coverage: CODER-20260101-001.\n"
        "V\tfoo\tactive\tscripts/foo.sh\thelp\tno\tread-only\tABSENT\tABSENT\tABSENT\n",
    )

    result, findings = _run(tmp_path, config_dir=config_dir)

    assert result == 0
    assert len(findings) == 1, f"expected exactly one finding for the planted manifest citation, got {findings}"
    f = findings[0]
    assert f["class"] == "provenance"
    assert f["label"] == "task-ID"
    assert f["token"] == "CODER-20260101-001"
    assert f["file"] == "config/operator_verbs.manifest"
    assert f["line"] == 2


def test_config_surface_flags_a_planted_task_id_in_a_census_file(tmp_path: Path) -> None:
    """Covers the other declarative-config file kind the config surface
    scans -- a census-style file, not just the manifest suffix -- so the
    surface is not special-cased to one filename."""
    config_dir = _empty_dir(tmp_path, "config")
    _write(
        config_dir,
        "dashboard_windows.census",
        "# Header comment.\n"
        "# Planted for coverage: CODER-20260101-002.\n"
        "W\tqueues\t0\t0\t80\t24\n",
    )

    result, findings = _run(tmp_path, config_dir=config_dir)

    assert result == 0
    assert len(findings) == 1, f"expected exactly one finding for the planted census citation, got {findings}"
    f = findings[0]
    assert f["class"] == "provenance"
    assert f["label"] == "task-ID"
    assert f["token"] == "CODER-20260101-002"
    assert f["file"] == "config/dashboard_windows.census"


def test_config_surface_flags_a_planted_citation_in_a_txt_file(tmp_path: Path) -> None:
    """A plain-text list under the config directory (an allowlist, the docs
    manifest, a palette) is part of the config surface: its comments are
    read by an operator the same way a .manifest file's are."""
    config_dir = _empty_dir(tmp_path, "config")
    _write(config_dir, "default_palette.txt", "# Planted: CODER-20260101-003.\n")

    result, findings = _run(tmp_path, config_dir=config_dir)

    assert result == 0
    assert len(findings) == 1, f"a .txt file under the config surface must be scanned: {findings}"
    assert findings[0]["label"] == "task-ID"
    assert findings[0]["file"] == "config/default_palette.txt"


def test_config_surface_ignores_files_outside_its_suffixes(tmp_path: Path) -> None:
    """A file under the config directory that is none of .manifest, .census
    or .txt (e.g. a JSON data file) is not part of this surface -- the
    config surface selects by suffix, not every file in team/config/."""
    config_dir = _empty_dir(tmp_path, "config")
    _write(config_dir, "palette.json", '{"note": "Planted: CODER-20260101-003."}\n')

    result, findings = _run(tmp_path, config_dir=config_dir)

    assert result == 0
    assert findings == [], f"a file outside the config surface's suffixes must not be scanned: {findings}"


def test_config_surface_clean_fixture_produces_zero_findings(tmp_path: Path) -> None:
    config_dir = _empty_dir(tmp_path, "config")
    _write(
        config_dir,
        "operator_verbs.manifest",
        "# A clean manifest with no internal identifiers in its comments.\n"
        "V\tfoo\tactive\tscripts/foo.sh\thelp\tno\tread-only\tABSENT\tABSENT\tABSENT\n",
    )

    result, findings = _run(tmp_path, config_dir=config_dir)

    assert result == 0
    assert findings == []


# ---------------------------------------------------------------------------
# 12. Scan-surface routing -- a tests/ directory nested under the code
#     surface is routed to the test-tree surface, not dropped
# ---------------------------------------------------------------------------


def test_tests_dir_nested_under_code_surface_is_routed_not_dropped(tmp_path: Path) -> None:
    """A "tests" directory nested beneath a --code-dirs target is not
    silently excluded by the code surface's own exclusion rule: it is
    routed whole to the test-tree surface, so its files are read (not
    dropped) and evaluated under test-tree rules (the VALUE exemption),
    not code-surface rules."""
    code_dir = _empty_dir(tmp_path, "code_with_nested_tests")
    nested_tests_dir = code_dir / "tests"
    _write(
        nested_tests_dir,
        "test_value.py",
        "def test_thing():\n"
        '    bug_id = "BUG-0006-foo.md"  # test-data VALUE, not a citation\n'
        "    assert bug_id\n",
    )
    _write(
        nested_tests_dir,
        "test_comment.py",
        "def test_thing():\n"
        "    # This test exists because of BUG-0007 filed earlier.\n"
        "    assert True\n",
    )

    result, findings = _run(tmp_path, code_dir=code_dir)
    assert result == 0
    assert [f["label"] for f in findings] == ["BUG-ID"], (
        "the VALUE literal must stay exempt under routed test-tree rules while the "
        f"comment citation is still flagged, proving the file was read: {findings}"
    )
    assert "test_comment.py" in findings[0]["file"]


def test_tests_dir_scanned_directly_as_code_target_is_not_routed(tmp_path: Path) -> None:
    """The routing above comes from a "tests" directory being NESTED under a
    code-surface target, not from a blanket VALUE exemption: the identical
    VALUE-literal fixture, scanned with the "tests" directory itself as the
    named --code-dirs target, is evaluated under code-surface rules and IS
    flagged."""
    code_dir = _empty_dir(tmp_path, "code_root")
    tests_dir_as_target = code_dir / "tests"
    _write(
        tests_dir_as_target,
        "test_value.py",
        "def test_thing():\n"
        '    bug_id = "BUG-0006-foo.md"  # test-data VALUE, not a citation\n'
        "    assert bug_id\n",
    )

    result, findings = _run(tmp_path, code_dir=tests_dir_as_target)
    assert result == 0
    assert len(findings) == 1, (
        "a directory named 'tests' that IS the named code-surface target (not nested "
        f"beneath one) is scanned under code-surface rules, with no VALUE exemption: {findings}"
    )
    assert findings[0]["label"] == "BUG-ID"


# ---------------------------------------------------------------------------
# 13. Extensionless shebang executables and the fixtures/ test-tree surface
# ---------------------------------------------------------------------------


def _write_executable(directory: Path, name: str, content: str) -> Path:
    """Like ``_write``, but marks the resulting file executable for owner,
    group, and other -- the same shape ``team/scripts/wake/git-guard/git``
    carries in the shipped tree."""
    path = _write(directory, name, content)
    path.chmod(0o755)
    return path


def test_extensionless_shebang_executable_under_code_surface_is_collected(tmp_path: Path) -> None:
    """An executable, extensionless file whose first line is a shebang is a
    code-surface candidate, mirroring team/scripts/wake/git-guard/git: a
    genuine provenance citation inside it is flagged exactly as it would be
    in a same-shaped ``.sh`` file."""
    code_dir = _empty_dir(tmp_path, "code")
    _write_executable(
        code_dir,
        "git",
        "#!/usr/bin/env bash\n"
        "# This shim exists because of BUG-0366 filed earlier.\n"
        "exec /usr/bin/git \"$@\"\n",
    )

    result, findings = _run(tmp_path, code_dir=code_dir)
    assert result == 0
    assert [f["label"] for f in findings] == ["BUG-ID"], (
        f"an executable extensionless shebang script must be scanned like any other "
        f"code-surface file: {findings}"
    )
    assert findings[0]["file"].endswith("/git") or findings[0]["file"] == "git"


def test_extensionless_non_shebang_data_file_is_not_collected(tmp_path: Path) -> None:
    """An extensionless file that carries no shebang on its first line is
    never a candidate, whether or not it happens to be executable -- the
    selector widened to "extensionless shebang executable", not to "every
    extensionless file"."""
    code_dir = _empty_dir(tmp_path, "code")
    _write_executable(
        code_dir,
        "VERSION",
        "This VERSION file cites BUG-0366 in its own body but has no shebang.\n",
    )

    result, findings = _run(tmp_path, code_dir=code_dir)
    assert result == 0
    assert findings == [], (
        f"a non-shebang extensionless file must stay unscanned even when executable: {findings}"
    )


def test_extensionless_shebang_file_without_executable_bit_is_not_collected(tmp_path: Path) -> None:
    """A shebang first line alone does not make an extensionless file a
    candidate: the file must also carry an executable bit, the same test
    the shell's own PATH lookup applies."""
    code_dir = _empty_dir(tmp_path, "code")
    non_exec = _write(
        code_dir,
        "helper",
        "#!/usr/bin/env bash\n# Present because of BUG-0366.\necho hi\n",
    )
    non_exec.chmod(0o644)

    result, findings = _run(tmp_path, code_dir=code_dir)
    assert result == 0
    assert findings == [], (
        f"a shebang file with no executable bit must stay unscanned: {findings}"
    )


def test_fixtures_directory_module_is_scanned_on_test_tree_surface(tmp_path: Path) -> None:
    """team/tests/fixtures/ is no longer a blanket exclusion on the
    test-tree surface: an ordinary shared-helper-shaped module directly
    under a directory literally named "fixtures" is now scanned, and a
    genuine provenance citation inside it is flagged."""
    test_tree_dir = _empty_dir(tmp_path, "tests")
    fixtures_dir = test_tree_dir / "fixtures"
    _write(
        fixtures_dir,
        "shared_helper.py",
        "def helper():\n"
        "    # Written this way because of BUG-0366 filed earlier.\n"
        "    return True\n",
    )

    result, findings = _run(tmp_path, test_tree_dir=test_tree_dir)
    assert result == 0
    assert [f["label"] for f in findings] == ["BUG-ID"], (
        f"a fixtures/ module must be scanned under test-tree rules now that the "
        f"blanket fixtures/ exclusion is gone: {findings}"
    )
    assert "fixtures/shared_helper.py" in findings[0]["file"]


def test_fixtures_top_level_synthetic_file_stays_excluded_by_name(tmp_path: Path) -> None:
    """A file whose basename is on the real _FILE_WHITELIST -- a synthetic
    good/bad-example corpus authored for a DIFFERENT lint's own unit tests,
    e.g. fixture_no_help_script.sh -- stays excluded by name even though
    fixtures/ itself is now scanned; widening the surface does not turn a
    deliberate anti-pattern fixture into a citation candidate."""
    test_tree_dir = _empty_dir(tmp_path, "tests")
    fixtures_dir = test_tree_dir / "fixtures"
    _write(
        fixtures_dir,
        "fixture_no_help_script.sh",
        "#!/usr/bin/env bash\n# Exists because of BUG-0366.\necho hi\n",
    )

    result, findings = _run(tmp_path, test_tree_dir=test_tree_dir)
    assert result == 0
    assert findings == [], (
        f"a whitelisted synthetic-corpus basename must stay excluded from every "
        f"surface: {findings}"
    )


def test_own_basename_is_no_longer_whole_file_excluded(tmp_path: Path) -> None:
    """This module's own basename, "lint_hygiene.py", used to sit on
    _FILE_WHITELIST -- a whole-file exemption that meant a real ledger
    citation shipped inside the enforcing module itself could never be
    reported. A synthetic file sharing that basename, planted on the code
    surface with a genuine citation, is now scanned and flagged exactly
    like any other code-surface file -- proving the exemption is gone,
    not merely undetected by this synthetic case."""
    code_dir = _empty_dir(tmp_path, "code")
    _write(
        code_dir,
        "lint_hygiene.py",
        "def helper():\n"
        "    # Written this way because of BUG-0042 filed earlier.\n"
        "    return True\n",
    )

    result, findings = _run(tmp_path, code_dir=code_dir)
    assert result == 0
    assert [f["label"] for f in findings] == ["BUG-ID"], (
        f"a file named lint_hygiene.py must be scanned like any other code-"
        f"surface file now that its basename is off _FILE_WHITELIST: {findings}"
    )
    assert findings[0]["file"] == "code/lint_hygiene.py"
    assert findings[0]["line"] == 2


def test_other_whitelisted_basenames_stay_excluded_on_code_surface(tmp_path: Path) -> None:
    """The basename removal above is scoped to this module alone: a
    different real _FILE_WHITELIST entry (this module's own test module)
    still stays excluded on the code surface, proving the change widened
    coverage by exactly one name rather than weakening the whitelist
    mechanism itself."""
    code_dir = _empty_dir(tmp_path, "code")
    _write(
        code_dir,
        "test_lint_hygiene.py",
        "def helper():\n"
        "    # Written this way because of BUG-0042 filed earlier.\n"
        "    return True\n",
    )

    result, findings = _run(tmp_path, code_dir=code_dir)
    assert result == 0
    assert findings == [], (
        f"test_lint_hygiene.py must stay whitelisted by name: {findings}"
    )


def test_fixtures_synthetic_corpus_subdirectory_stays_excluded_by_name(tmp_path: Path) -> None:
    """A subdirectory of fixtures/ that is itself a synthetic per-lint
    corpus -- named on the test-tree surface's own exclude-segment set,
    e.g. container_engine_free/ -- stays excluded even though fixtures/
    itself is now scanned."""
    test_tree_dir = _empty_dir(tmp_path, "tests")
    corpus_dir = test_tree_dir / "fixtures" / "container_engine_free" / "violation"
    _write(
        corpus_dir,
        "test_seeded_container_invocation.py",
        "# Deliberate fixture for BUG-0366-shaped detection.\n"
        "def test_thing():\n"
        "    assert True\n",
    )

    result, findings = _run(tmp_path, test_tree_dir=test_tree_dir)
    assert result == 0
    assert findings == [], (
        f"a named synthetic-corpus subdirectory under fixtures/ must stay excluded: "
        f"{findings}"
    )


# ---------------------------------------------------------------------------
# 14. Docs surface -- .md files under --docs-dirs are a first-class scan
#     surface enforcing the full pattern set in every subtree alike.
# ---------------------------------------------------------------------------


def test_docs_surface_flags_a_bug_id_that_prose_surface_would_exempt(tmp_path: Path) -> None:
    """The whole point of the docs surface being a DIFFERENT surface from
    the release-notes prose surface: a BUG-ID that prose exempts (sanctioned
    release-note content) is flagged here, because standalone documentation
    is not a change record -- pins the exact shape acceptance criterion 1
    probes (appending a BUG-ID-shaped line to a docs/ file)."""
    docs_dir = _empty_dir(tmp_path, "docs")
    _write(
        docs_dir,
        "blocking-site-census.md",
        "| 1 | Some gate | some/script.py | Refuses on X -- BUG-9999 probe | BLOCK-WORTHY |\n",
    )

    result, findings = _run(tmp_path, docs_dir=docs_dir)
    assert result == 0
    assert len(findings) == 1, f"a BUG-ID citation in a docs/ file must be flagged: {findings}"
    assert findings[0]["label"] == "BUG-ID"
    assert findings[0]["token"] == "BUG-9999"
    assert findings[0]["file"] == "docs/blocking-site-census.md"

    # Removing the line restores the prior (clean) state.
    _write(docs_dir, "blocking-site-census.md", "| 1 | Some gate | some/script.py | Refuses on X | BLOCK-WORTHY |\n")
    result, findings = _run(tmp_path, docs_dir=docs_dir)
    assert result == 0
    assert findings == [], f"removing the citation must return the docs surface to clean: {findings}"


def test_docs_surface_clean_fixture_produces_zero_findings(tmp_path: Path) -> None:
    docs_dir = _empty_dir(tmp_path, "docs")
    _write(docs_dir, "guide.md", "# Operator Guide\n\nThis document has no internal identifiers in it.\n")

    result, findings = _run(tmp_path, docs_dir=docs_dir)
    assert result == 0
    assert findings == []


@pytest.mark.parametrize("subdir", ["audits", "checkpoints"])
def test_docs_surface_enforces_every_subtree_alike(tmp_path: Path, subdir: str) -> None:
    """No docs subtree is carved out by name: a BUG-ID in an audits/ or a
    checkpoints/ document is flagged exactly as it is anywhere
    else on the docs surface -- the full pattern set, no prose-surface
    exemption."""
    docs_dir = _empty_dir(tmp_path, "docs")
    _write(docs_dir / subdir, "example-record.md", "Closed by BUG-0087-some-fix.\n")

    result, findings = _run(tmp_path, docs_dir=docs_dir)
    assert result == 0
    assert [f["label"] for f in findings] == ["BUG-ID"], (
        f"a BUG-ID under docs/{subdir}/ must be flagged like any other docs file: {findings}"
    )
    assert findings[0]["file"] == f"docs/{subdir}/example-record.md"


def test_docs_surface_audit_data_filename_gets_full_enforcement(tmp_path: Path) -> None:
    """A file named like an audit-data ledger gets no filename carve-out:
    its BUG-ID citation is flagged like any other docs file."""
    docs_dir = _empty_dir(tmp_path, "docs")
    _write(docs_dir, "audit-data.md", "Closed by BUG-0087-some-fix.\n")

    result, findings = _run(tmp_path, docs_dir=docs_dir)
    assert result == 0
    assert [f["label"] for f in findings] == ["BUG-ID"], findings


# ---------------------------------------------------------------------------
# 15. String-literal content is deliberately out of scope for the
#     provenance class -- pins the module docstring's documented decision
#     as actual behavior (acceptance criterion 2's probe shape).
# ---------------------------------------------------------------------------


def test_test_tree_surface_does_not_read_comment_shaped_string_literal_content(tmp_path: Path) -> None:
    """A plain string-literal VALUE assignment whose own text reads as a
    ``#``-prefixed comment line (the shape a fixture uses to write a real
    comment into a generated file) produces no finding -- the module
    docstring's "What changed in the move" item 3 documents this as a
    deliberate decision, not an oversight; this test pins that decision as
    lint behavior."""
    test_tree_dir = _empty_dir(tmp_path, "team_tests")
    _write(
        test_tree_dir,
        "test_probe_fixture.py",
        "def build_fixture():\n"
        '    PROBE = (\n'
        '        "# kanban.cfg -- BUG-9999 render-path fixture\\n"\n'
        "    )\n"
        "    return PROBE\n",
    )

    result, findings = _run(tmp_path, test_tree_dir=test_tree_dir)
    assert result == 0
    assert findings == [], (
        "string-literal content is deliberately not read by the provenance "
        f"class on the test-tree surface: {findings}"
    )


# ---------------------------------------------------------------------------
# 16. version-history pattern -- a bare release version cited as the
#     history of a line -- and the shipped-text surface, the root-file
#     collection and the config surface's .txt files it reaches.
# ---------------------------------------------------------------------------


def _run_default_surfaces(tmp_path: Path, *extra: str) -> tuple[int, list[dict]]:
    """Invoke lint_hygiene.main() with --repo-root at *tmp_path* and every
    directory surface left at its default (each default resolves under
    --repo-root), except the code and test-tree surfaces: the code
    surface's default is the real team/scripts/ beside the lint, so both
    are pinned to empty directories. Exercises the real default wiring --
    docs/, team/roles/, subagents/, team/templates/, team/pm-agent/,
    docker/, team/config/, and the repo-root *.md / *_example files."""
    findings_path = tmp_path / "default_surfaces_findings.json"
    argv = [
        "--code-dirs", str(_empty_dir(tmp_path, "empty_code")),
        "--test-tree-dirs", str(_empty_dir(tmp_path, "empty_test_tree")),
        "--classes", "provenance",
        "--repo-root", str(tmp_path),
        "--sidecar", str(tmp_path / "sidecar.json"),
        "--findings-file", str(findings_path),
        *extra,
    ]
    result = _lint.main(argv)
    findings = json.loads(findings_path.read_text(encoding="utf-8")) if findings_path.exists() else []
    return result, findings


def test_seeded_since_version_in_docs_and_role_file_are_both_reported(tmp_path: Path) -> None:
    """The requirement's own probe: the line "since v1.2.3" seeded into a
    doc under docs/ and into a role file under team/roles/ is reported for
    each, under the provenance class, through the default surfaces."""
    _write(tmp_path / "docs", "guide.md", "# Guide\n\nThis behaviour holds since v1.2.3 for every project.\n")
    _write(tmp_path / "team" / "roles", "CODER.md", "# Role\n\nThe merge rule applies since v1.2.3.\n")

    result, findings = _run_default_surfaces(tmp_path)

    assert result == 0
    by_file = {f["file"]: f for f in findings}
    assert set(by_file) == {"docs/guide.md", "team/roles/CODER.md"}, findings
    for f in by_file.values():
        assert f["class"] == "provenance"
        assert f["label"] == "version-history"
        assert f["token"] == "since v1.2.3"
        assert f["line"] == 3


_VERSION_HISTORY_SHAPES = [
    ("since", "This check has run since v2.18 on every wake.", "since v2.18"),
    ("parenthetical", "Lane B narrow admission (v2.23.0) applies here.", "(v2.23.0)"),
    ("added-in", "The read layer was added in v1 for operators.", "added in v1"),
    ("trailing-plus", "Per-agent debug logs (v0.27.3+) are kanban-wide.", "v0.27.3+"),
]


@pytest.mark.parametrize(
    "shape,text,token", _VERSION_HISTORY_SHAPES, ids=[c[0] for c in _VERSION_HISTORY_SHAPES]
)
def test_version_history_shape_flagged_in_code_comment(tmp_path: Path, shape: str, text: str, token: str) -> None:
    code_dir = _empty_dir(tmp_path, f"code_{shape}")
    _write(code_dir, "fixture.py", f"def f():\n    # {text}\n    return 1\n")

    result, findings = _run(tmp_path, code_dir=code_dir)

    assert result == 0
    assert len(findings) == 1, f"expected one version-history finding for {shape}: {findings}"
    assert findings[0]["label"] == "version-history"
    assert findings[0]["token"] == token
    assert findings[0]["line"] == 2


@pytest.mark.parametrize(
    "shape,text,token", _VERSION_HISTORY_SHAPES, ids=[c[0] for c in _VERSION_HISTORY_SHAPES]
)
def test_version_history_shape_flagged_on_docs_line(tmp_path: Path, shape: str, text: str, token: str) -> None:
    docs_dir = _empty_dir(tmp_path, f"docs_{shape}")
    _write(docs_dir, "guide.md", f"# Guide\n\n{text}\n")

    result, findings = _run(tmp_path, docs_dir=docs_dir)

    assert result == 0
    assert len(findings) == 1, f"expected one version-history finding for {shape}: {findings}"
    assert findings[0]["label"] == "version-history"
    assert findings[0]["token"] == token


@pytest.mark.parametrize(
    "line",
    [
        "Release notes live at release-notes/vX.Y.Z.md (since vX.Y.Z).",
        "Pass --since v1.2.3 to start the span at that release.",
        'The comparator asserts index("v1.2.3") sorts first.',
        "The next open slot is v0.0.3+1 = v0.0.4.",
        "e.g. a fix shipped since v1.2.3 is listed under that version.",
    ],
    ids=["placeholder", "since-option", "quoted-call-argument", "version-arithmetic", "eg-example"],
)
def test_version_history_lookalikes_not_flagged(tmp_path: Path, line: str) -> None:
    """A placeholder version, a --since option value, a quoted value inside
    a call's parentheses, version arithmetic, and an e.g.-marked
    illustration each look like a version-history shape but cite no
    history, and stay unflagged."""
    docs_dir = _empty_dir(tmp_path, "docs")
    _write(docs_dir, "guide.md", f"# Guide\n\n{line}\n")

    result, findings = _run(tmp_path, docs_dir=docs_dir)

    assert result == 0
    assert findings == [], f"a version-history lookalike must not be flagged: {findings}"


def test_version_history_exempt_on_release_notes_prose_surface(tmp_path: Path) -> None:
    prose_dir = _empty_dir(tmp_path, "release-notes")
    _write(prose_dir, "v1.2.4.md", "## Other Changes\n- The retry path has held since v1.2.3.\n")

    result, findings = _run(tmp_path, prose_dir=prose_dir)

    assert result == 0
    assert findings == [], f"version-history is sanctioned release-note content: {findings}"


def test_root_changelog_is_not_scanned(tmp_path: Path) -> None:
    """The rendered root CHANGELOG.md is left out of the root *.md
    collection, so neither its versions nor any other token in it is
    reported, while a sibling root *.md carrying the same line is."""
    _write(tmp_path, "CHANGELOG.md", "## v1.2.4\n- Retry path unchanged since v1.2.3 (BUG-0042).\n")
    _write(tmp_path, "README.md", "# Readme\n\nRetry path unchanged since v1.2.3.\n")

    result, findings = _run_default_surfaces(tmp_path)

    assert result == 0
    assert [f["file"] for f in findings] == ["README.md"], findings


def test_published_icd_changelog_is_exempt_from_version_history_only(tmp_path: Path) -> None:
    """docs/api/ICD_CHANGELOG.md is an append-only published changelog:
    the versions it records are its content, so version-history is not
    reported there -- while every other provenance pattern still is."""
    docs_dir = _empty_dir(tmp_path, "docs")
    _write(
        docs_dir / "api",
        "ICD_CHANGELOG.md",
        "## 1.4\n- The /board endpoint shape is stable since v1.2.3.\n- Filed as CODER-20260101-001.\n",
    )

    result, findings = _run(tmp_path, docs_dir=docs_dir)

    assert result == 0
    assert [f["label"] for f in findings] == ["task-ID"], findings


def test_test_tree_version_history_in_comment_flagged_but_test_value_exempt(tmp_path: Path) -> None:
    """On the test-tree surface a version-history shape in a comment is
    reported; the same shape inside an ordinary string VALUE is test data
    and is not; and a docstring naming the version its own fixture builds
    (that version appears in one of the file's value strings) describes
    test data rather than citing history."""
    test_tree_dir = _empty_dir(tmp_path, "team_tests")
    _write(
        test_tree_dir,
        "test_release_span.py",
        "# This guard exists since v2.18.0 for every project.\n"
        "def test_span():\n"
        '    """The fixture tags the bare ancestor (v1.0.0) on HEAD~1."""\n'
        '    tag = "v1.0.0"\n'
        '    label = "shipped RCs since v0.20.0"\n'
        "    assert tag and label\n",
    )

    result, findings = _run(tmp_path, test_tree_dir=test_tree_dir)

    assert result == 0
    assert [(f["line"], f["token"]) for f in findings] == [(1, "since v2.18.0")], findings


@pytest.mark.parametrize(
    "relpath",
    [
        "ARCHITECTURE.md",
        "subagents/coder.md",
        "subagents/MANIFEST.txt",
        "team/templates/install/crontab.example",
        "team/templates/project/release/BUG-TEMPLATE.md",
        "team/pm-agent/pm_status.py",
        "team/pm-agent/README.md",
        "docker/rocky9/Dockerfile",
        "kanban.cfg_example",
        "team/config/docs-manifest.txt",
    ],
)
def test_default_surface_scans_file(tmp_path: Path, relpath: str) -> None:
    """Each shipped location the default surfaces reach -- a root *.md, a
    subagents/ file, a team/templates/ file of any suffix, a team/pm-agent/
    file, a docker/ build file with no suffix, a root *_example file and a
    team/config/*.txt file -- is scanned, and the lint still exits 0 with
    the finding present."""
    target = tmp_path / relpath
    _write(target.parent, target.name, "# Comment header.\n# Planted: CODER-20260101-001.\n")

    result, findings = _run_default_surfaces(tmp_path)

    assert result == 0, "a quality-class lint must exit 0 whatever it finds"
    assert [(f["file"], f["line"], f["label"]) for f in findings] == [(relpath, 2, "task-ID")], findings


def test_shipped_text_surface_skips_binary_files_and_excluded_segments(tmp_path: Path) -> None:
    """The shipped-text surface collects every TEXT file whatever its
    suffix: a file with a NUL byte is binary and skipped, and a tests/ or
    __pycache__/ directory under it is skipped like on the code surface."""
    text_dir = _empty_dir(tmp_path, "docker")
    (text_dir / "image.bin").write_bytes(b"\x00\x01 CODER-20260101-001\n")
    _write(text_dir / "tests", "check.sh", "# Planted: CODER-20260101-002.\n")
    _write(text_dir / "__pycache__", "stale.txt", "# Planted: CODER-20260101-003.\n")
    _write(text_dir, "build.env", "# Planted: CODER-20260101-004.\n")

    result, findings = _run(tmp_path, text_dir=text_dir)

    assert result == 0
    assert [f["file"] for f in findings] == ["docker/build.env"], findings


def test_nested_example_files_are_scanned_tree_wide(tmp_path: Path) -> None:
    """In a git work tree, a *_example or *.example file below the root is
    on the shipped-text surface whether tracked or not yet added; one under
    team/tests/ or release-notes/ is not; and a file a default directory
    surface also reaches (team/templates/) is reported once."""
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True, capture_output=True)
    _write(tmp_path / "team" / "config", "seedcfg_example", "# seed: since v1.9 default\nkey=1\n")
    _write(tmp_path / "team" / "scripts", "seed_example", "# since v1.2.3 cadence\n")
    _write(tmp_path / "team", "HALT.example", "# Added in v2.4.0 for operators.\n")
    _write(tmp_path / "team" / "templates" / "install", "crontab.example", "# since v1.4.0 cadence\n")
    _write(tmp_path / "team" / "tests" / "fixtures", "seed_example", "# since v1.2.3 cadence\n")
    _write(tmp_path / "release-notes", "notes.example", "# since v1.2.3 cadence\n")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "team/HALT.example"], check=True, capture_output=True
    )

    result, findings = _run_default_surfaces(tmp_path)

    assert result == 0, "a quality-class lint must exit 0 whatever it finds"
    assert sorted(f["file"] for f in findings) == [
        "team/HALT.example",
        "team/config/seedcfg_example",
        "team/scripts/seed_example",
        "team/templates/install/crontab.example",
    ], findings


def test_explicit_text_dirs_leave_out_repo_root_files(tmp_path: Path) -> None:
    """Root *.md and *_example files join the shipped-text surface only
    while --text-dirs is left at its default; an explicit list scans only
    the directories it names."""
    _write(tmp_path, "README.md", "# Readme\n\nPlanted: CODER-20260101-001.\n")
    _write(tmp_path, "secrets_example", "# Planted: CODER-20260101-002.\n")
    named = _empty_dir(tmp_path, "named")
    _write(named, "notes.md", "Planted: CODER-20260101-003.\n")

    result, findings = _run_default_surfaces(tmp_path, "--text-dirs", str(named))

    assert result == 0
    assert [f["file"] for f in findings] == ["named/notes.md"], findings


# ---------------------------------------------------------------------------
# 17. Identifiers only, wording unchecked, on the test-tree surface
# ---------------------------------------------------------------------------


def test_test_tree_wording_about_the_fix_or_the_ticket_gives_no_finding(tmp_path: Path) -> None:
    """Comments, docstrings and an assert message that describe a test by
    the fix or the ticket that wrote it carry no identifier, so they give
    zero provenance findings and the lint exits 0."""
    test_tree_dir = _empty_dir(tmp_path, "team_tests")
    _write(
        test_tree_dir,
        "test_block_parser.py",
        '"""Block parsing.\n'
        "\n"
        "The fix ensures a blank line never ends the block.\n"
        '"""\n'
        "\n"
        "\n"
        "def test_blank_line_keeps_block_open():\n"
        '    """Covers each bound, per the ticket\'s acceptance criteria."""\n'
        "    # The fix ensures the parser reads past the blank line.\n"
        "    # One fixture per bound, per the ticket's acceptance criteria.\n"
        "    lines = ['a', '', 'b']\n"
        '    assert len(lines) == 3, "the fix ensures three lines survive"\n',
    )

    result, findings = _run(tmp_path, test_tree_dir=test_tree_dir, classes=["provenance"])

    assert result == 0, "a quality-class lint must exit 0 whatever it finds"
    assert findings == [], f"wording without an identifier must not be flagged: {findings}"


def test_test_tree_bug_id_in_comment_gives_exactly_one_finding(tmp_path: Path) -> None:
    """A test-tree comment carrying BUG-0123 is exactly one BUG-ID finding,
    and wording sharing its line adds no second finding."""
    test_tree_dir = _empty_dir(tmp_path, "team_tests")
    _write(
        test_tree_dir,
        "test_block_parser.py",
        "def test_blank_line_keeps_block_open():\n"
        "    # The fix for BUG-0123 ensures a blank line never ends the block.\n"
        "    assert True\n",
    )

    result, findings = _run(tmp_path, test_tree_dir=test_tree_dir, classes=["provenance"])

    assert result == 0, "a quality-class lint must exit 0 whatever it finds"
    assert [(f["line"], f["label"], f["token"]) for f in findings] == [(2, "BUG-ID", "BUG-0123")], findings
    assert findings[0]["class"] == "provenance"
    assert "--add-suppression" in findings[0]["remedy"], findings[0]["remedy"]


def test_test_tree_version_cited_as_history_gives_exactly_one_finding(tmp_path: Path) -> None:
    """A test-tree comment reading "since v1.2.3", in a file whose string
    literals do not carry v1.2.3, is exactly one version-history finding."""
    test_tree_dir = _empty_dir(tmp_path, "team_tests")
    _write(
        test_tree_dir,
        "test_ordering.py",
        "def test_ordering_is_stable():\n"
        "    # Ordering has held since v1.2.3 for every board.\n"
        "    columns = ['todo', 'done']\n"
        "    assert columns == sorted(columns, key=columns.index)\n",
    )

    result, findings = _run(tmp_path, test_tree_dir=test_tree_dir, classes=["provenance"])

    assert result == 0, "a quality-class lint must exit 0 whatever it finds"
    assert [(f["line"], f["label"]) for f in findings] == [(2, "version-history")], findings
    assert "v1.2.3" in findings[0]["token"], findings[0]


def test_test_tree_example_marked_identifier_stays_exempt(tmp_path: Path) -> None:
    """An e.g.-marked identifier in a test-tree comment is an illustration,
    exempt there exactly as on the code surface."""
    test_tree_dir = _empty_dir(tmp_path, "team_tests")
    _write(
        test_tree_dir,
        "test_key_format.py",
        "def test_key_format():\n"
        "    # e.g. BUG-0042 shows the expected key format.\n"
        "    assert True\n",
    )

    result, findings = _run(tmp_path, test_tree_dir=test_tree_dir, classes=["provenance"])

    assert result == 0
    assert findings == [], f"an e.g.-marked identifier must stay exempt: {findings}"


def test_help_says_provenance_flags_identifiers_and_not_wording() -> None:
    """--help describes the provenance class and the test-tree surface as
    flagging IDs and versions cited as history, says wording is not
    checked, and lists no phrasing examples."""
    proc = subprocess.run(
        [sys.executable, str(_LINT_SCRIPT), "--help"],
        capture_output=True, text=True, check=False,
    )

    assert proc.returncode == 0, proc.stderr
    help_text = " ".join(proc.stdout.split())
    assert (
        "provenance: flags bug, priority, task and requirement IDs and versions cited as history"
        in help_text
    ), help_text
    assert help_text.count("wording is not checked") >= 2, help_text
    for phrase in ("this ticket", "before this fix", "Required Output (c)", "exercises the fix"):
        assert phrase not in help_text, f"--help still lists the phrasing example {phrase!r}"
