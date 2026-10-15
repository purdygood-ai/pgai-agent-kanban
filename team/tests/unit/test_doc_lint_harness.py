"""
test_doc_lint_harness.py
=========================
Retrofit: each of the four doc-lint.sh suite failure
classes prints a remedy block per docs/coding-standards.md "Gates prescribe,
not just diagnose", classified as follows:

  - cmd suite            — non-deterministic (exempt): names the next
                            diagnostic step (a re-run command and the skip
                            mechanism), never a claimed fix.
  - links suite          — partial: locates the offending line
                            deterministically; the corrected target is
                            free-form.
  - fossil-greps suite   — partial: locates file:line deterministically;
                            the rewrite that removes the residue is
                            free-form.
  - feature-greps suite  — partial (must-find): names the missing token;
                            authoring the doc section is free-form.

Each test builds a synthetic doc-root under tmp_path containing only the
audited-doc-surface file(s) needed for that scenario (doc-lint.sh's
AUDITED_DOCS array is fixed; files it does not find are silently skipped, so
a sandbox only needs to populate the ones under test).  No live repo content
or the live kanban root is touched.
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest

# Self-located, never derived from an environment variable or from git, so a
# gitless host and a foreign invoking cwd both resolve the same repo root —
# the same contract doc-lint.sh itself uses for its own SCRIPT_DIR/REPO_ROOT.
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent
_REPO_ROOT = _TEAM_DIR.parent
_DOC_LINT_SH = _TEAM_DIR / "scripts" / "harness" / "doc-lint.sh"


def _run(args: list[str], *, cwd: pathlib.Path = _REPO_ROOT) -> subprocess.CompletedProcess:
    """Run a subprocess with an explicit, self-located cwd.

    Every subprocess this suite launches goes through this one function, so
    the invocation is anchored to the repo root computed from this test
    file's own location -- never the cwd the interpreter happened to inherit
    from whatever launched pytest -- and the suite's verdict is therefore a
    property of the tree, not of the invocation. The interpreter's own
    working directory is never changed (a chdir here would leak into every
    later test in the same process); the cwd is scoped to this one
    subprocess call.
    """
    return subprocess.run(
        args, capture_output=True, text=True, timeout=30, cwd=str(cwd)
    )


def _diagnostic(result: subprocess.CompletedProcess) -> str:
    """Render a subprocess result's full stdout+stderr for an assertion
    failure message.

    Every assertion in this suite that depends on a subprocess result routes
    its failure message through this one function, so a failure always shows
    what the subprocess actually said instead of a bare returncode with the
    explanation discarded.
    """
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _run_doc_lint(
    doc_root: pathlib.Path, suite: str, *, verbose: bool = False
) -> subprocess.CompletedProcess:
    args = ["bash", str(_DOC_LINT_SH), "--suite", suite, "--doc-root", str(doc_root)]
    if verbose:
        args.append("--verbose")
    return _run(args)


def _write(doc_root: pathlib.Path, rel_path: str, content: str) -> pathlib.Path:
    p = doc_root / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# cmd suite — non-deterministic (exempt)
# ---------------------------------------------------------------------------


class TestCmdSuiteRemedy:
    def test_failing_command_names_cause_and_next_diagnostic_step(
        self, tmp_path: pathlib.Path
    ) -> None:
        doc_root = tmp_path / "docs1"
        _write(
            doc_root,
            "README.md",
            "# Test doc\n\n```bash\nfalse\n```\n",
        )

        result = _run_doc_lint(doc_root, "cmd")
        assert result.returncode == 1, _diagnostic(result)
        assert "Cause:" in result.stdout
        assert "Next diagnostic step:" in result.stdout
        # The re-run pointer is a real, verbatim-runnable diagnostic command.
        assert f'(cd "{doc_root}" && false)' in result.stdout
        assert "doc-lint skip mechanisms" in result.stdout
        assert "Rule:" in result.stdout
        # Exempt from a numbered fix-command block — this is NOT a claimed fix.
        assert "To fix:" not in result.stdout

    def test_skip_annotation_named_in_remedy_actually_clears_the_gate(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The next-diagnostic-step direction (add a skip annotation) is
        actionable: applying it makes the suite pass."""
        doc_root = tmp_path / "docs2"
        _write(
            doc_root,
            "README.md",
            "# Test doc\n\n"
            "<!-- doc-lint: skip — deliberately-failing example for doc-lint test -->\n"
            "```bash\nfalse\n```\n",
        )

        result = _run_doc_lint(doc_root, "cmd")
        assert result.returncode == 0, _diagnostic(result)
        assert "SKIP-BLOCK" in result.stdout or "skip=1" in result.stdout

    def test_passing_command_reports_pass_with_no_remedy(
        self, tmp_path: pathlib.Path
    ) -> None:
        doc_root = tmp_path / "docs3"
        _write(doc_root, "README.md", "# Test doc\n\n```bash\ntrue\n```\n")

        result = _run_doc_lint(doc_root, "cmd")
        assert result.returncode == 0, _diagnostic(result)
        assert "Cause:" not in result.stdout


# ---------------------------------------------------------------------------
# links suite — partial
# ---------------------------------------------------------------------------


class TestLinksSuiteRemedy:
    def test_dead_link_prints_cause_to_fix_and_rule(
        self, tmp_path: pathlib.Path
    ) -> None:
        doc_root = tmp_path / "docs4"
        readme = _write(
            doc_root,
            "README.md",
            "# Test doc\n\nSee [the missing doc](does-not-exist.md) for details.\n",
        )

        result = _run_doc_lint(doc_root, "links")
        assert result.returncode == 1, _diagnostic(result)
        assert "DEAD LINK: does-not-exist.md" in result.stdout
        assert "Cause:" in result.stdout
        assert "To fix:" in result.stdout
        assert "Rule:" in result.stdout
        assert f'grep -n -F \'does-not-exist.md\' "{readme}"' in result.stdout
        assert "--suite links" in result.stdout  # re-run pointer names the suite to re-check

    def test_locate_command_in_remedy_finds_the_offending_line(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Step 1 of the 'To fix' block (grep -n -F) is deterministic and
        verbatim-runnable: executing it for real locates the offending line."""
        doc_root = tmp_path / "docs5"
        readme = _write(
            doc_root,
            "README.md",
            "# Test doc\n\nline one\n\nSee [the missing doc](does-not-exist.md) for details.\n",
        )

        result = _run_doc_lint(doc_root, "links")
        assert result.returncode == 1, _diagnostic(result)

        locate = _run(["grep", "-n", "-F", "does-not-exist.md", str(readme)])
        assert locate.returncode == 0, _diagnostic(locate)
        assert locate.stdout.startswith("5:")  # the link is on line 5

    def test_valid_link_passes_with_no_remedy(self, tmp_path: pathlib.Path) -> None:
        doc_root = tmp_path / "docs6"
        _write(doc_root, "README.md", "# Test doc\n\nSee [other](other.md).\n")
        _write(doc_root, "other.md", "# Other\n")

        result = _run_doc_lint(doc_root, "links")
        assert result.returncode == 0, _diagnostic(result)
        assert "Cause:" not in result.stdout


# ---------------------------------------------------------------------------
# fossil-greps suite — partial (zero-hit gate)
# ---------------------------------------------------------------------------


class TestFossilGrepsSuiteRemedy:
    def test_fossil_hit_prints_cause_to_fix_and_rule(
        self, tmp_path: pathlib.Path
    ) -> None:
        doc_root = tmp_path / "docs7"
        readme = _write(
            doc_root,
            "README.md",
            "# Test doc\n\nfossil path: /home/alice/pgai_agent_kanban/leftover\n",
        )

        result = _run_doc_lint(doc_root, "fossil-greps")
        assert result.returncode == 1, _diagnostic(result)
        assert "FOSSIL HIT: pattern=/home/alice/pgai_agent_kanban" in result.stdout
        assert "Cause:" in result.stdout
        assert "To fix:" in result.stdout
        assert "Rule:" in result.stdout
        assert f"sed -n '3p' \"{readme}\"" in result.stdout

    def test_remedy_commands_clear_the_gate_when_executed_verbatim(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Executing step 1 (locate) then removing the offending line — the
        prescribed shape of step 2 — clears the gate on re-run."""
        doc_root = tmp_path / "docs8"
        readme = _write(
            doc_root,
            "README.md",
            "# Test doc\n\nfossil path: /home/alice/pgai_agent_kanban/leftover\n",
        )

        first = _run_doc_lint(doc_root, "fossil-greps")
        assert first.returncode == 1, _diagnostic(first)

        # Step 1: locate (verbatim, deterministic).
        locate = _run(["sed", "-n", "3p", str(readme)])
        assert locate.returncode == 0, _diagnostic(locate)
        assert "/home/alice/pgai_agent_kanban" in locate.stdout

        # Step 2: remove the offending line (the prescribed shape of the fix).
        lines = readme.read_text(encoding="utf-8").splitlines()
        del lines[2]  # zero-indexed line 3
        readme.write_text("\n".join(lines) + "\n", encoding="utf-8")

        second = _run_doc_lint(doc_root, "fossil-greps")
        assert second.returncode == 0, _diagnostic(second)

    def test_clean_doc_passes_with_no_remedy(self, tmp_path: pathlib.Path) -> None:
        doc_root = tmp_path / "docs9"
        _write(doc_root, "README.md", "# Test doc\n\nNothing forbidden here.\n")

        result = _run_doc_lint(doc_root, "fossil-greps")
        assert result.returncode == 0, _diagnostic(result)
        assert "Cause:" not in result.stdout


# ---------------------------------------------------------------------------
# feature-greps suite — partial (must-find gate)
# ---------------------------------------------------------------------------


class TestFeatureGrepsSuiteRemedy:
    def test_missing_token_prints_cause_to_fix_and_rule(
        self, tmp_path: pathlib.Path
    ) -> None:
        doc_root = tmp_path / "docs10"
        _write(doc_root, "README.md", "# Test doc\n\nNothing feature-related here.\n")

        result = _run_doc_lint(doc_root, "feature-greps")
        assert result.returncode == 1, _diagnostic(result)
        assert "FEATURE MISSING: pattern=debian" in result.stdout
        assert "Cause:" in result.stdout
        assert "To fix:" in result.stdout
        assert "Rule:" in result.stdout
        assert f'grep -rniE \'debian\' "{doc_root}"' in result.stdout

    def test_locate_command_confirms_true_absence(self, tmp_path: pathlib.Path) -> None:
        doc_root = tmp_path / "docs11"
        _write(doc_root, "README.md", "# Test doc\n\nNothing feature-related here.\n")

        result = _run_doc_lint(doc_root, "feature-greps")
        assert result.returncode == 1, _diagnostic(result)

        confirm = _run(["grep", "-rniE", "debian", str(doc_root)])
        assert confirm.returncode == 1, _diagnostic(confirm)  # truly absent — grep found nothing

    def test_adding_the_token_clears_that_pattern(self, tmp_path: pathlib.Path) -> None:
        """Following the prescribed shape (document the token) clears that
        specific pattern's failure, demonstrating the fix direction works."""
        doc_root = tmp_path / "docs12"
        readme = _write(
            doc_root, "README.md", "# Test doc\n\nNothing feature-related here.\n"
        )

        first = _run_doc_lint(doc_root, "feature-greps", verbose=True)
        assert "FEATURE MISSING: pattern=debian" in first.stdout, _diagnostic(first)

        readme.write_text(
            readme.read_text(encoding="utf-8") + "\nSupports Debian and Rocky9.\n",
            encoding="utf-8",
        )
        second = _run_doc_lint(doc_root, "feature-greps", verbose=True)
        assert "FEATURE MISSING: pattern=debian" not in second.stdout, _diagnostic(second)
        assert "FEATURE MISSING: pattern=rocky9" not in second.stdout, _diagnostic(second)

    def test_all_tokens_present_passes_with_no_remedy(
        self, tmp_path: pathlib.Path
    ) -> None:
        doc_root = tmp_path / "docs13"
        _write(
            doc_root,
            "README.md",
            "# Test doc\n\n"
            "debian rocky9 ubi9 branch_prefix push_to_remote dev_tree_path "
            "wake-batch.sh install.sh blocked_minutes\n",
        )

        result = _run_doc_lint(doc_root, "feature-greps")
        assert result.returncode == 0, _diagnostic(result)
        assert "Cause:" not in result.stdout


# ---------------------------------------------------------------------------
# bash -n sanity (belt-and-suspenders; also run by the PHASE 1 pre-flight)
# ---------------------------------------------------------------------------


def test_doc_lint_sh_passes_bash_syntax_check() -> None:
    result = _run(["bash", "-n", str(_DOC_LINT_SH)])
    assert result.returncode == 0, _diagnostic(result)
