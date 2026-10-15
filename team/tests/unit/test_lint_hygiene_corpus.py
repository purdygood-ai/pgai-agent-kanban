"""
test_lint_hygiene_corpus.py
=============================
The checked-in false-positive / true-positive corpus for
team/scripts/lint_hygiene.py, run with its "provenance",
"version-named-test", and "comment-drift" classes enabled together. The
"operator-literal" class, registered alongside them, is covered by
test_lint_hygiene_operator_literal.py and is not part of this corpus.

Every fixture below is materialized under pytest's ``tmp_path`` at test
time -- this suite never reads, writes, or asserts against the
repository's own release-notes/, bugs/, or requirements/ directories.

Two groups of tests:

  - **False positives** (``test_false_positive_corpus_runs_clean``): a
    single corpus of fixtures that must produce ZERO findings across
    those three classes at once. The shapes covered, at minimum:
      1. a version-followed-by-a-word slug exempted as an external/
         upstream reference, not an internal bundle citation.
      2. the release-notes prose surface (BUG-ID/PRIORITY-ID/bare-
         release-slug are its sanctioned content there).
      3. a version-shaped substring embedded inside a longer dotted
         token.
      4. a version-or-identifier-shaped string used as an ordinary
         test-data VALUE rather than cited in a comment/docstring.
      5. a version-shaped token as the SUBJECT under test in a test
         function's own name (version-named-test's false-positive risk).
      6. a general comment naming no checkable argument/parameter count,
         and a comment whose stated count IS accurate (comment-drift's
         two false-positive risks).
      7. a kanban task that is the product object under test, in a
         test-tree comment or docstring ("the task's own status.md ##
         State is read", "under the task's own logs/ directory", "the
         task's own source_branch", "the marker mirrors the task's ##
         State", a "prior task", a "TASK-1" lock holder), and a string
         literal carrying the words "Covers the task's acceptance
         criteria" (wording about a task, which is not checked).
      8. a work-item section name used as data in a test-tree file -- an
         f-string building "## Required Output", a ("Goal", "Required
         Output", ...) section tuple, a prose field list, a "## Known
         Issues" section that names a bug -- and the numbered acceptance
         labels "AC1:" and "(Acceptance Criterion 6)" (wording about
         work-item sections, which is not checked).

  - **True positives** (one test per class): a single seeded finding for
    each of the three classes the corpus runs, firing exactly once and
    naming its own class.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Load lint_hygiene.py as a module, under a name distinct from
# test_lint_hygiene.py's own import (both test modules load the same
# script independently; a shared sys.modules key would make the second
# import silently replace the first import's registered module).
# ---------------------------------------------------------------------------
_THIS_FILE = Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_LINT_SCRIPT = _SCRIPTS_DIR / "lint_hygiene.py"


def _import_lint_module():
    spec = importlib.util.spec_from_file_location("lint_hygiene_corpus_under_test", _LINT_SCRIPT)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load spec from {_LINT_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_lint = _import_lint_module()

_ALL_CLASSES = ["provenance", "version-named-test", "comment-drift"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _empty_dir(tmp_path: Path, name: str) -> Path:
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write(directory: Path, name: str, content: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(content, encoding="utf-8")
    return path


def _run_all_classes(
    tmp_path: Path,
    *,
    code_dir: Path | None = None,
    test_tree_dir: Path | None = None,
    prose_dir: Path | None = None,
    sidecar: Path | None = None,
) -> tuple[int, list[dict]]:
    """Invoke lint_hygiene.main() with the corpus classes (_ALL_CLASSES)
    enabled and every surface pinned to an explicit, caller-controlled
    directory. Returns (exit_code, findings)."""
    findings_path = tmp_path / "corpus_findings.json"
    argv = [
        "--code-dirs", str(code_dir or _empty_dir(tmp_path, "empty_code")),
        "--test-tree-dirs", str(test_tree_dir or _empty_dir(tmp_path, "empty_test_tree")),
        "--prose-dirs", str(prose_dir or _empty_dir(tmp_path, "empty_prose")),
        "--classes", *_ALL_CLASSES,
        "--repo-root", str(tmp_path),
        "--sidecar", str(sidecar or (tmp_path / "sidecar.json")),
        "--findings-file", str(findings_path),
    ]
    result = _lint.main(argv)
    findings = json.loads(findings_path.read_text(encoding="utf-8")) if findings_path.exists() else []
    return result, findings


# ---------------------------------------------------------------------------
# False-positive corpus
# ---------------------------------------------------------------------------


def _build_false_positive_corpus(tmp_path: Path) -> tuple[Path, Path, Path]:
    code_dir = _empty_dir(tmp_path, "fp_code")
    test_tree_dir = _empty_dir(tmp_path, "fp_test_tree")
    prose_dir = _empty_dir(tmp_path, "fp_prose")

    # Shape 1: a version-followed-by-a-word slug exempted as an external/
    # upstream reference, not an internal bundle citation.
    _write(
        code_dir,
        "upstream_pin.py",
        "def configure():\n"
        "    # The upstream linter package pins v2.16.0-legacy-format for compatibility.\n"
        "    return True\n",
    )

    # Shape 2: the release-notes prose surface -- BUG-ID/PRIORITY-ID/
    # bare-release-slug are its sanctioned, intended content.
    _write(
        prose_dir,
        "vTEST.md",
        "## Bug Fixes\n"
        "- BUG-0010: fixed the thing.\n"
        "- PRIORITY-0002: addressed the priority item.\n"
        "- v1.2.3-widget-name shipped in this release.\n",
    )

    # Shape 3: a version-shaped substring embedded inside a longer dotted
    # token -- not a standalone citation.
    _write(
        code_dir,
        "dotted_config.py",
        "def load():\n"
        '    slug = "config.v2.16.0.yaml"  # embedded, not a citation\n'
        "    return slug\n",
    )

    # Shape 4: a version-or-identifier-shaped string used as an ordinary
    # test-data VALUE rather than cited in a comment/docstring.
    _write(
        test_tree_dir,
        "test_value.py",
        "def test_thing():\n"
        '    bug_id = "BUG-0001-foo.md"  # test-data VALUE, not a citation\n'
        "    assert bug_id\n",
    )

    # Shape 5 (version-named-test false-positive risk): a version-shaped
    # token as the SUBJECT under test, not a release-provenance label.
    _write(
        test_tree_dir,
        "test_version_comparator.py",
        "def test_compares_v2_16_0_against_v2_15_4_correctly():\n"
        '    assert compare("v2.16.0", "v2.15.4") == 1\n',
    )

    # Shape 6 (comment-drift false-positive risk, part A): a general
    # comment naming no checkable argument/parameter count.
    _write(
        code_dir,
        "general_comment.py",
        "# Adds two numbers together.\ndef add(a, b):\n    return a + b\n",
    )

    # Shape 6 (comment-drift false-positive risk, part B): a comment
    # whose stated argument count IS accurate -- the "made true" side of
    # the drift check.
    _write(
        code_dir,
        "accurate_comment.py",
        "# Takes two arguments and returns their sum.\ndef add(a, b):\n    return a + b\n",
    )

    # Shape 7: a kanban task that is the product object under test, and a
    # string literal carrying task wording.
    _write(
        test_tree_dir,
        "test_task_state_reconcile.py",
        '"""The task\'s own status.md ## State is read before the marker.\n'
        "\n"
        "The marker mirrors the task's ## State; a prior task stays DONE.\n"
        '"""\n'
        "\n"
        "\n"
        "def test_evidence_path_and_branch():\n"
        "    # The evidence file resolves under the task's own logs/ directory.\n"
        "    # determine_source_branch returns the task's own source_branch.\n"
        "    # TASK-1 holds the lock while TASK-2 waits.\n"
        "    expected = \"Covers the task's acceptance criteria:\"\n"
        "    assert expected\n",
    )

    # Shape 8: a work-item section name used as data, a heading-spelled
    # section that names something, and numbered acceptance labels.
    _write(
        test_tree_dir,
        "test_readme_sections.py",
        '"""Prose fields (Goal, Required Output, Constraints, Acceptance Criteria,\n'
        "Notes) keep the project's prefixed branch form.\n"
        "\n"
        'Its "## Known Issues" section names the causing bug by substance.\n'
        '"""\n'
        "\n"
        '_PROSE_SECTIONS = ("Goal", "Required Output", "Constraints", "Acceptance Criteria")\n'
        "\n"
        "\n"
        "def test_recycled_readme(tmp_path):\n"
        "    # Operator-HALT inviolability (Acceptance Criterion 6)\n"
        "    # AC1: the README is rebuilt fresh.\n"
        '    readme = f"## Goal\\nx\\n\\n" f"## Required Output\\nnone\\n\\n"\n'
        "    assert readme and _PROSE_SECTIONS\n",
    )

    return code_dir, test_tree_dir, prose_dir


def test_false_positive_corpus_runs_clean(tmp_path: Path) -> None:
    code_dir, test_tree_dir, prose_dir = _build_false_positive_corpus(tmp_path)

    result, findings = _run_all_classes(
        tmp_path, code_dir=code_dir, test_tree_dir=test_tree_dir, prose_dir=prose_dir
    )

    assert result == 0
    assert findings == [], f"every corpus shape must run clean across the corpus classes: {findings}"


# ---------------------------------------------------------------------------
# True positives -- one seeded finding per corpus class
# ---------------------------------------------------------------------------


def test_true_positive_provenance_fires_once(tmp_path: Path) -> None:
    code_dir = _empty_dir(tmp_path, "tp_provenance_code")
    _write(
        code_dir,
        "dirty.py",
        "def foo():\n    # This references BUG-0042 directly in a comment.\n    return 1\n",
    )

    result, findings = _run_all_classes(tmp_path, code_dir=code_dir)

    assert result == 0
    assert len(findings) == 1, f"expected exactly one finding, got {findings}"
    assert findings[0]["class"] == "provenance"


def test_true_positive_version_named_test_fires_once(tmp_path: Path) -> None:
    test_tree_dir = _empty_dir(tmp_path, "tp_version_named_test_tree")
    _write(test_tree_dir, "test_v2_16_0_fix.py", "def test_thing():\n    assert True\n")

    result, findings = _run_all_classes(tmp_path, test_tree_dir=test_tree_dir)

    assert result == 0
    assert len(findings) == 1, f"expected exactly one finding, got {findings}"
    assert findings[0]["class"] == "version-named-test"


def test_true_positive_comment_drift_fires_once(tmp_path: Path) -> None:
    code_dir = _empty_dir(tmp_path, "tp_comment_drift_code")
    _write(
        code_dir,
        "drift.py",
        "# Takes three arguments and returns their sum.\ndef add(a, b):\n    return a + b\n",
    )

    result, findings = _run_all_classes(tmp_path, code_dir=code_dir)

    assert result == 0
    assert len(findings) == 1, f"expected exactly one finding, got {findings}"
    assert findings[0]["class"] == "comment-drift"
