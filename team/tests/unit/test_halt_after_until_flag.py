"""
test_halt_after_until_flag.py
==============================
Behavioral tests for team/scripts/halt-after.sh's --until flag: arming the
versioned rc:vX.Y.Z sentinel directly from the command line, and the three
refusals that keep it honest.

Acceptance criteria exercised:

  1. --until vX.Y.Z writes a HALT-AFTER file whose contents are exactly
     rc:vX.Y.Z, and a value with no leading 'v' normalises to the same
     byte-identical form.
  2. --key coder --until vX.Y.Z refuses (exit non-zero, no file written)
     with a message naming the release-boundary mismatch and a remedy that
     is a literal runnable command (no angle brackets).
  3. --until vX.Y.Z with no project named and no $PGAI_PROJECT_NAME refuses
     (exit non-zero, no file written).
  4. --until with a malformed value (e.g. "2.20") refuses, naming the
     malformed value, and writes no file.
  5. A refused invocation against a project that already carries a
     HALT-AFTER file leaves that file byte-identical.
  6. The arm confirmation for a version-pinned sentinel renders the target
     through describe_token ("until vX.Y.Z"), not a locally composed phrase.

Tests invoke halt-after.sh via subprocess, exercising the real production
shell code rather than a reimplementation of it, matching the pattern in
test_defer_operator_verbs.py.

Naming: function names describe behavior under test, never bug IDs or
task IDs (see roles/TESTER.md "Test Authoring Guidelines").
"""

from __future__ import annotations

import os
import pathlib
import subprocess

# ---------------------------------------------------------------------------
# Paths (relative to this file: team/tests/unit/ -> team/ -> repo root)
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_HALT_AFTER_SH = _TEAM_DIR / "scripts" / "halt-after.sh"


def _env(kanban_root: pathlib.Path) -> dict[str, str]:
    """An environment repointed at a throwaway kanban root, with
    PGAI_PROJECT_NAME unset -- Live-Install Safety (roles/CODER.md): a
    script invoked in a test must never be able to resolve back to the
    operator's real kanban root or a real project."""
    env = dict(os.environ)
    env.pop("PGAI_PROJECT_NAME", None)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    return env


def _run(args: list[str], kanban_root: pathlib.Path, timeout: int = 15) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(_HALT_AFTER_SH), *args],
        capture_output=True,
        text=True,
        env=_env(kanban_root),
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Arming with --until
# ---------------------------------------------------------------------------


def test_arming_with_until_writes_the_versioned_rc_token(tmp_path: pathlib.Path) -> None:
    (tmp_path / "projects" / "demo").mkdir(parents=True)

    result = _run(["--project", "demo", "--until", "v2.20.1"], tmp_path)

    assert result.returncode == 0, f"halt-after.sh failed: {result.stderr!r}"
    sentinel = tmp_path / "projects" / "demo" / "HALT-AFTER"
    assert sentinel.exists()
    assert sentinel.read_text(encoding="utf-8").strip() == "rc:v2.20.1"


def test_arming_normalises_an_until_value_with_no_leading_v(tmp_path: pathlib.Path) -> None:
    (tmp_path / "projects" / "demo").mkdir(parents=True)

    with_v = _run(["--project", "demo", "--until", "v2.20.1"], tmp_path)
    sentinel = tmp_path / "projects" / "demo" / "HALT-AFTER"
    content_with_v = sentinel.read_text(encoding="utf-8")
    sentinel.unlink()

    without_v = _run(["--project", "demo", "--until", "2.20.1"], tmp_path)
    content_without_v = sentinel.read_text(encoding="utf-8")

    assert with_v.returncode == 0 and without_v.returncode == 0
    assert content_with_v == content_without_v == "rc:v2.20.1\n"


def test_arm_confirmation_renders_the_target_through_describe_token(
    tmp_path: pathlib.Path,
) -> None:
    (tmp_path / "projects" / "demo").mkdir(parents=True)

    result = _run(["--project", "demo", "--until", "v2.20.1"], tmp_path)

    assert result.returncode == 0, f"halt-after.sh failed: {result.stderr!r}"
    assert "until v2.20.1" in result.stdout


# ---------------------------------------------------------------------------
# Refusal: --until combined with a non-rc scope
# ---------------------------------------------------------------------------


def test_until_combined_with_a_role_scope_refuses_and_writes_no_file(
    tmp_path: pathlib.Path,
) -> None:
    (tmp_path / "projects" / "demo").mkdir(parents=True)

    result = _run(["--project", "demo", "--key", "coder", "--until", "v2.20.1"], tmp_path)

    assert result.returncode != 0
    assert "release boundary" in result.stderr
    assert "coder" in result.stderr
    sentinel = tmp_path / "projects" / "demo" / "HALT-AFTER"
    assert not sentinel.exists()


def test_until_combined_with_a_role_scope_remedy_has_no_angle_brackets(
    tmp_path: pathlib.Path,
) -> None:
    result = _run(["--key", "coder", "--until", "v2.20.1"], tmp_path)

    assert result.returncode != 0
    assert "remedy:" in result.stderr
    remedy_line = next(line for line in result.stderr.splitlines() if line.startswith("remedy:"))
    assert "<" not in remedy_line and ">" not in remedy_line, (
        f"halt-after.sh's --until scope-mismatch remedy must be a literal "
        f"runnable command with no template slot, got: {remedy_line!r}"
    )


def test_until_combined_with_the_rc_scope_explicitly_is_not_a_mismatch(
    tmp_path: pathlib.Path,
) -> None:
    (tmp_path / "projects" / "demo").mkdir(parents=True)

    result = _run(["--project", "demo", "--key", "rc", "--until", "v2.20.1"], tmp_path)

    assert result.returncode == 0, f"halt-after.sh failed: {result.stderr!r}"
    sentinel = tmp_path / "projects" / "demo" / "HALT-AFTER"
    assert sentinel.read_text(encoding="utf-8").strip() == "rc:v2.20.1"


# ---------------------------------------------------------------------------
# Refusal: --until with no project resolvable
# ---------------------------------------------------------------------------


def test_until_with_no_project_named_refuses(tmp_path: pathlib.Path) -> None:
    result = _run(["--until", "v2.20.1"], tmp_path)

    assert result.returncode != 0
    assert "error:" in result.stderr
    assert "remedy:" in result.stderr


# ---------------------------------------------------------------------------
# Refusal: malformed --until value
# ---------------------------------------------------------------------------


def test_until_with_a_malformed_value_refuses_naming_the_value(
    tmp_path: pathlib.Path,
) -> None:
    (tmp_path / "projects" / "demo").mkdir(parents=True)

    result = _run(["--project", "demo", "--until", "2.20"], tmp_path)

    assert result.returncode != 0, "expected a malformed --until value to refuse loud"
    assert "2.20" in result.stderr
    assert "vX.Y.Z" in result.stderr
    sentinel = tmp_path / "projects" / "demo" / "HALT-AFTER"
    assert not sentinel.exists()


# ---------------------------------------------------------------------------
# A refusal leaves any existing sentinel untouched
# ---------------------------------------------------------------------------


def test_a_refused_arm_leaves_an_existing_sentinel_byte_identical(
    tmp_path: pathlib.Path,
) -> None:
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    sentinel = project_dir / "HALT-AFTER"
    sentinel.write_text("coder\n", encoding="utf-8")
    before = sentinel.read_bytes()

    malformed = _run(["--project", "demo", "--until", "2.20"], tmp_path)
    mismatch = _run(["--project", "demo", "--key", "coder", "--until", "v2.20.1"], tmp_path)

    assert malformed.returncode != 0
    assert mismatch.returncode != 0
    assert sentinel.read_bytes() == before, "a refusal must never touch an existing sentinel"
