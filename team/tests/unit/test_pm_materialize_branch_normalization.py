"""
test_pm_materialize_branch_normalization.py
=============================================
Unit tests for the branch-normalization trust-boundary guard in
pm_materialize.py, which stops the materializer from trusting a
model-authored branch name verbatim.

Covers the acceptance criteria for the materializer-as-authority fix:

  1. A doubled-prefix source_branch ("<prefix>rc/<prefix>v<version>") for a
     release decompose normalizes to the canonical RC branch, narrated once.
  2. The canonical form, the unprefixed "rc/<version>", and the bare
     "<version>" spelling all normalize to the same canonical value.
  3. An irreconcilable value (wrong version, garbage string) is refused —
     the guard fails loud, naming given vs expected, before any task folder
     is written.
  4. Feature-mode parent_branch gets the same normalize-or-fail treatment
     against the canonical mainline branch.
  5. The branch_prefix is read from project.cfg — never hardcoded 'ai_' — so
     an alternate prefix (e.g. 'x_') produces the analogous canonical branch.

All temp paths use pytest's tmp_path fixture (never bare /tmp). No git or
subprocess calls are exercised here — see
team/tests/integration/test_cert_branch_normalization_guard.py for the
production-entry-point (subprocess) proof of the same behavior.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

# ---------------------------------------------------------------------------
# Import with installed-package / direct-checkout fallback
# ---------------------------------------------------------------------------
try:
    import pm_agent.pm_materialize as pm  # installed via pm_agent package
except ImportError:
    sys.path.insert(
        0, str(pathlib.Path(__file__).parent.parent.parent / "pm-agent")
    )
    import pm_materialize as pm  # type: ignore[no-redef]


# ---------------------------------------------------------------------------
# _normalize_rc_branch_value — release RC branch normalization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, branch_prefix, resolved_expected, changed_expected",
    [
        ("ai_rc/v9.9.9", "ai_", "ai_rc/v9.9.9", False),  # canonical form, golden path
        ("rc/v9.9.9", "ai_", "ai_rc/v9.9.9", True),  # unprefixed form normalizes
        ("v9.9.9", "ai_", "ai_rc/v9.9.9", True),  # bare version alone normalizes
        # The known model-authored corruption '<prefix>rc/<prefix>v<version>':
        # a reduced-capability model conflates the branch-prefix namespace
        # with the tag-prefix namespace and emits 'ai_rc/ai_v9.9.9'.
        ("ai_rc/ai_v9.9.9", "ai_", "ai_rc/v9.9.9", True),
        ("rc/v9.9.9", "", "rc/v9.9.9", False),  # empty prefix -> canonical is bare rc/<version>
    ],
)
def test_normalize_rc_branch_value(
    raw: str, branch_prefix: str, resolved_expected: str, changed_expected: bool
) -> None:
    """_normalize_rc_branch_value accepts canonical, unprefixed, bare-version, and doubled-prefix spellings."""
    resolved, changed = pm._normalize_rc_branch_value(raw, branch_prefix, "v9.9.9")
    assert resolved == resolved_expected
    assert changed is changed_expected


def test_normalize_rc_branch_value_wrong_version_is_irreconcilable() -> None:
    """A syntactically valid RC branch for the WRONG version is refused."""
    resolved, changed = pm._normalize_rc_branch_value("rc/v8.8.8", "ai_", "v9.9.9")
    assert resolved is None
    assert changed is None


def test_normalize_rc_branch_value_garbage_string_is_irreconcilable() -> None:
    """An unrecognizable garbage string is refused, not silently accepted."""
    resolved, changed = pm._normalize_rc_branch_value("banana-branch", "ai_", "v9.9.9")
    assert resolved is None
    assert changed is None


def test_normalize_rc_branch_value_empty_given_is_not_a_match() -> None:
    """An empty/none given value returns (None, None) — caller treats as 'no value'."""
    assert pm._normalize_rc_branch_value("", "ai_", "v9.9.9") == (None, None)
    assert pm._normalize_rc_branch_value("none", "ai_", "v9.9.9") == (None, None)


def test_normalize_rc_branch_value_alternate_prefix_no_hardcoded_ai() -> None:
    """An alternate prefix ('x_') produces the analogous canonical branch — no 'ai_' anywhere."""
    resolved, changed = pm._normalize_rc_branch_value("x_rc/x_v9.9.9", "x_", "v9.9.9")
    assert resolved == "x_rc/v9.9.9"
    assert changed is True
    assert "ai_" not in resolved


# ---------------------------------------------------------------------------
# _normalize_mainline_branch_value — feature-mode mainline normalization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, branch_prefix, resolved_expected, changed_expected",
    [
        ("ai_main", "ai_", "ai_main", False),  # canonical form is unchanged
        ("main", "ai_", "ai_main", True),  # unprefixed form normalizes
        ("ai_ai_main", "ai_", "ai_main", True),  # doubled-prefix corruption normalizes
        ("main", "", "main", False),  # empty prefix -> canonical is bare 'main'
    ],
)
def test_normalize_mainline_branch_value(
    raw: str, branch_prefix: str, resolved_expected: str, changed_expected: bool
) -> None:
    """_normalize_mainline_branch_value accepts canonical, unprefixed, and doubled-prefix spellings."""
    resolved, changed = pm._normalize_mainline_branch_value(raw, branch_prefix)
    assert resolved == resolved_expected
    assert changed is changed_expected


def test_normalize_mainline_branch_value_wrong_branch_is_irreconcilable() -> None:
    """A value naming a different, real-looking branch is refused."""
    resolved, changed = pm._normalize_mainline_branch_value("develop", "ai_")
    assert resolved is None
    assert changed is None


# ---------------------------------------------------------------------------
# _validate_and_normalize_release_source_branches — release fail-closed guard
# ---------------------------------------------------------------------------


def _release_task(task_id: str, source_branch: str, cm_operation: str = "") -> dict:
    task = {
        "task_id": task_id,
        "slug": task_id.split("-")[-1],
        "role": "CODER",
        "assigned_agent": "coder",
        "source_branch": source_branch,
    }
    if cm_operation:
        task["cm_operation"] = cm_operation
    return task


@pytest.mark.parametrize(
    "source_branch, target_version",
    [
        ("ai_rc/v9.9.9", "v9.9.9"),  # already canonical
        ("ai_rc/ai_v9.9.9", "v9.9.9"),  # doubled-prefix corruption, accepted spelling
        ("totally-invalid-branch", "none"),  # no validation when target_version is 'none'
    ],
)
def test_validate_release_source_branches_does_not_exit(
    tmp_path: pathlib.Path, source_branch: str, target_version: str
) -> None:
    """No SystemExit for canonical branches, accepted corruption spellings, or a 'none' target_version."""
    (tmp_path / "project.cfg").write_text(
        "[project]\nbranch_prefix = ai_\n", encoding="utf-8"
    )
    tasks = [_release_task("CODER-20260101-001-impl", source_branch)]
    pm._validate_and_normalize_release_source_branches(tasks, str(tmp_path), target_version)


def test_validate_release_source_branches_exits_on_irreconcilable_value(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """An irreconcilable source_branch causes sys.exit(1) naming given vs expected."""
    (tmp_path / "project.cfg").write_text(
        "[project]\nbranch_prefix = ai_\n", encoding="utf-8"
    )
    tasks = [_release_task("CODER-20260101-001-impl", "rc/v8.8.8")]
    with pytest.raises(SystemExit) as exc_info:
        pm._validate_and_normalize_release_source_branches(tasks, str(tmp_path), "v9.9.9")
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "rc/v8.8.8" in captured.err
    assert "ai_rc/v9.9.9" in captured.err
    assert "CODER-20260101-001-impl" in captured.err


def test_validate_release_source_branches_skips_cm_open_bookend(
    tmp_path: pathlib.Path,
) -> None:
    """A CM-open task (cm_operation='open-rc') is excluded from the RC-branch check."""
    (tmp_path / "project.cfg").write_text(
        "[project]\nbranch_prefix = ai_\n", encoding="utf-8"
    )
    tasks = [_release_task("CM-20260101-001-open-rc", "main", cm_operation="open-rc")]
    # 'main' would not normalize against the RC canonical, but CM-open is excluded.
    pm._validate_and_normalize_release_source_branches(tasks, str(tmp_path), "v9.9.9")


def test_validate_release_source_branches_skips_tasks_with_no_value(
    tmp_path: pathlib.Path,
) -> None:
    """Tasks without an explicit source_branch (synthetic bookends) are skipped."""
    (tmp_path / "project.cfg").write_text(
        "[project]\nbranch_prefix = ai_\n", encoding="utf-8"
    )
    tasks = [_release_task("TESTER-20260101-002-verify", "none")]
    pm._validate_and_normalize_release_source_branches(tasks, str(tmp_path), "v9.9.9")


# ---------------------------------------------------------------------------
# _validate_and_normalize_parent_branch — feature-mode fail-closed guard
# ---------------------------------------------------------------------------


def test_validate_parent_branch_returns_canonical_on_bare_main(
    tmp_path: pathlib.Path,
) -> None:
    """The default 'main' normalizes to the configured (prefixed) mainline."""
    (tmp_path / "project.cfg").write_text(
        "[project]\nbranch_prefix = ai_\n", encoding="utf-8"
    )
    result = pm._validate_and_normalize_parent_branch("main", str(tmp_path))
    assert result == "ai_main"


def test_validate_parent_branch_accepts_doubled_prefix_corruption(
    tmp_path: pathlib.Path,
) -> None:
    """A doubled-prefix parent_branch normalizes to the canonical mainline."""
    (tmp_path / "project.cfg").write_text(
        "[project]\nbranch_prefix = ai_\n", encoding="utf-8"
    )
    result = pm._validate_and_normalize_parent_branch("ai_ai_main", str(tmp_path))
    assert result == "ai_main"


def test_validate_parent_branch_exits_on_irreconcilable_value(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """An irreconcilable parent_branch causes sys.exit(1) naming given vs expected."""
    (tmp_path / "project.cfg").write_text(
        "[project]\nbranch_prefix = ai_\n", encoding="utf-8"
    )
    with pytest.raises(SystemExit) as exc_info:
        pm._validate_and_normalize_parent_branch("develop", str(tmp_path))
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "develop" in captured.err
    assert "ai_main" in captured.err


def test_validate_parent_branch_no_prefix_project_unchanged(
    tmp_path: pathlib.Path,
) -> None:
    """No branch_prefix configured (pure-AI install): canonical mainline is bare 'main'."""
    result = pm._validate_and_normalize_parent_branch("main", str(tmp_path))
    assert result == "main"
