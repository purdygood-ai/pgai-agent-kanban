"""
test_defer_operator_verbs.py
=============================
Behavioral tests for the three operator verbs over the deferral-posture
resolver: team/scripts/defer.sh (arm), team/scripts/undefer.sh (clear), and
team/scripts/why.sh (read-only explainer).

Acceptance criteria exercised:

  (1) Arming with a well-formed --until value for a registered project
      creates projects/<name>/DEFER-LOW-BUGS carrying that until target,
      and the resolver then reports deferral in force from the
      campaign-file tier with that target.
  (2) Arming with an until value of 2.14 is refused with a non-zero exit
      naming the malformed value and the expected form, and no
      DEFER-LOW-BUGS file exists afterwards; arming twice with different
      until values leaves exactly one campaign file carrying the second
      value.
  (3) defer.sh and undefer.sh invoked without --project refuse dual-channel
      with a remedy naming the correct invocation; why.sh --details invoked
      without --project refuses the same way (why.sh alone, with neither
      --project nor --details, now walks every registered project instead
      of refusing -- see test_why_verdict_engine.py).
  (4) The clear verb removes an existing campaign file, after which the
      resolver reports the posture falling back to the project.cfg or
      kanban.cfg tier; run against a project with no campaign file it
      exits 0 and reports there was nothing to clear.
  (5) why.sh run against a project with no keys set and no campaign file
      reports deferred_bugs=false, level=low, piggyback=true, no until
      target, and names 'default' as the tier for each.
  (6) With deferred_bug_level=high in the project's project.cfg, why.sh
      reports level=high sourced from project.cfg while the other values
      still report their own tiers; with a campaign file in force it
      reports the campaign-file tier, the until target and the file path.
  (7) With a malformed campaign file, why.sh reports the malformed file by
      path and does not print a resolved posture.
  (8) Running why.sh twice in succession leaves every file under the
      project root byte-identical.

Tests invoke the scripts via subprocess, exercising the real production
shell code rather than a reimplementation of it, matching the pattern in
test_defer_posture_resolver.py.

why.sh's invocations below pass --details alongside --project: the
v2.15.0 verdict-engine change (team/pgai_agent_kanban/ops/verdict.py) moved the
four deferral-posture lines this file exercises behind that flag so
`why.sh --project NAME` alone now prints a one-line lane verdict
instead. The posture report itself is unchanged in content or
formatting -- see team/tests/unit/test_why_verdict_engine.py for the
verdict engine's own coverage.

Naming: function names describe behavior under test, never bug IDs or
task IDs (see roles/TESTER.md "Test Authoring Guidelines").
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import subprocess

import pytest

# ---------------------------------------------------------------------------
# Paths (relative to this file: team/tests/unit/ -> team/ -> repo root)
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_DEFER_SH = _TEAM_DIR / "scripts" / "defer.sh"
_UNDEFER_SH = _TEAM_DIR / "scripts" / "undefer.sh"
_WHY_SH = _TEAM_DIR / "scripts" / "why.sh"


def _env(kanban_root: pathlib.Path) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PGAI_PROJECT_NAME", None)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    return env


def _run(script: pathlib.Path, args: list[str], kanban_root: pathlib.Path, timeout: int = 15) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(script), *args],
        capture_output=True,
        text=True,
        env=_env(kanban_root),
        timeout=timeout,
    )


def _write_cfg(path: pathlib.Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _parse_why_output(stdout: str) -> dict[str, str]:
    """Parse why.sh's "<field>=<display>" lines into a dict keyed by field."""
    fields: dict[str, str] = {}
    for line in stdout.strip().splitlines():
        field, _, rest = line.partition("=")
        fields[field] = rest
    return fields


def _hash_tree(root: pathlib.Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    if not root.exists():
        return hashes
    for path in sorted(root.rglob("*")):
        if path.is_file():
            hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


# ---------------------------------------------------------------------------
# defer.sh -- the arm verb
# ---------------------------------------------------------------------------


def test_arming_with_well_formed_until_creates_campaign_file_the_resolver_reads(tmp_path: pathlib.Path) -> None:
    (tmp_path / "projects" / "demo").mkdir(parents=True)

    result = _run(_DEFER_SH, ["--project", "demo", "--until", "v2.14.0"], tmp_path)

    assert result.returncode == 0, f"defer.sh failed: {result.stderr!r}"
    campaign_file = tmp_path / "projects" / "demo" / "DEFER-LOW-BUGS"
    assert campaign_file.exists()
    assert "until=v2.14.0" in campaign_file.read_text(encoding="utf-8")

    why_result = _run(_WHY_SH, ["--project", "demo", "--details"], tmp_path)
    # This fixture is not registered in projects.cfg and has no tasks, so
    # the resolved verdict is legitimately IDLE (exit 2) -- --details exits
    # with that verdict's own code rather than always 0; != 1 is the
    # correct check here since a rendering refusal, not a specific verdict,
    # is what this test is guarding against.
    assert why_result.returncode != 1, f"why.sh failed: {why_result.stderr!r}"
    fields = _parse_why_output(why_result.stdout)
    assert "campaign file" in fields["until"]
    assert "v2.14.0" in fields["until"]


def test_arming_normalises_an_until_value_with_no_leading_v(tmp_path: pathlib.Path) -> None:
    (tmp_path / "projects" / "demo").mkdir(parents=True)

    result = _run(_DEFER_SH, ["--project", "demo", "--until", "2.14.0"], tmp_path)

    assert result.returncode == 0, f"defer.sh failed: {result.stderr!r}"
    campaign_file = tmp_path / "projects" / "demo" / "DEFER-LOW-BUGS"
    assert "until=v2.14.0" in campaign_file.read_text(encoding="utf-8")


def test_arming_with_malformed_until_refuses_and_creates_no_file(tmp_path: pathlib.Path) -> None:
    (tmp_path / "projects" / "demo").mkdir(parents=True)

    result = _run(_DEFER_SH, ["--project", "demo", "--until", "2.14"], tmp_path)

    assert result.returncode != 0, "expected a malformed --until value to refuse loud"
    assert "2.14" in result.stderr
    assert "vX.Y.Z" in result.stderr
    campaign_file = tmp_path / "projects" / "demo" / "DEFER-LOW-BUGS"
    assert not campaign_file.exists(), "a refusal must write nothing"


def test_arming_twice_with_different_values_leaves_exactly_one_file_with_the_second_value(
    tmp_path: pathlib.Path,
) -> None:
    (tmp_path / "projects" / "demo").mkdir(parents=True)

    first = _run(_DEFER_SH, ["--project", "demo", "--until", "v2.14.0"], tmp_path)
    second = _run(_DEFER_SH, ["--project", "demo", "--until", "v2.15.0"], tmp_path)

    assert first.returncode == 0 and second.returncode == 0
    project_dir = tmp_path / "projects" / "demo"
    campaign_files = list(project_dir.glob("DEFER-LOW-BUGS*"))
    assert len(campaign_files) == 1, f"expected exactly one campaign file, found {campaign_files}"
    assert "until=v2.15.0" in campaign_files[0].read_text(encoding="utf-8")


def test_arming_without_project_refuses_dual_channel(tmp_path: pathlib.Path) -> None:
    result = _run(_DEFER_SH, ["--until", "v2.14.0"], tmp_path)

    assert result.returncode != 0
    assert "error:" in result.stderr
    assert "remedy:" in result.stderr
    assert "--project" in result.stderr


def test_arming_without_until_refuses(tmp_path: pathlib.Path) -> None:
    (tmp_path / "projects" / "demo").mkdir(parents=True)

    result = _run(_DEFER_SH, ["--project", "demo"], tmp_path)

    assert result.returncode != 0
    assert "--until" in result.stderr


def test_arming_an_unregistered_project_refuses(tmp_path: pathlib.Path) -> None:
    result = _run(_DEFER_SH, ["--project", "ghost", "--until", "v2.14.0"], tmp_path)

    assert result.returncode != 0
    assert "ghost" in result.stderr


# ---------------------------------------------------------------------------
# undefer.sh -- the clear verb
# ---------------------------------------------------------------------------


def test_clearing_removes_the_campaign_file_and_posture_falls_back(tmp_path: pathlib.Path) -> None:
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    _write_cfg(project_dir / "project.cfg", ["[release]", "deferred_bug_level = high"])
    (project_dir / "DEFER-LOW-BUGS").write_text("until=v2.14.0\n", encoding="utf-8")

    result = _run(_UNDEFER_SH, ["--project", "demo"], tmp_path)

    assert result.returncode == 0, f"undefer.sh failed: {result.stderr!r}"
    assert not (project_dir / "DEFER-LOW-BUGS").exists()

    why_result = _run(_WHY_SH, ["--project", "demo", "--details"], tmp_path)
    fields = _parse_why_output(why_result.stdout)
    assert "project.cfg" in fields["deferred_bug_level"]
    assert "default" in fields["until"]


def test_clearing_a_project_with_no_campaign_file_succeeds_and_says_so(tmp_path: pathlib.Path) -> None:
    (tmp_path / "projects" / "demo").mkdir(parents=True)

    result = _run(_UNDEFER_SH, ["--project", "demo"], tmp_path)

    assert result.returncode == 0, f"undefer.sh must be idempotent: {result.stderr!r}"
    assert "nothing to clear" in result.stdout


def test_clearing_without_project_refuses_dual_channel(tmp_path: pathlib.Path) -> None:
    result = _run(_UNDEFER_SH, [], tmp_path)

    assert result.returncode != 0
    assert "error:" in result.stderr
    assert "remedy:" in result.stderr
    assert "--project" in result.stderr


def test_clearing_an_unregistered_project_refuses(tmp_path: pathlib.Path) -> None:
    result = _run(_UNDEFER_SH, ["--project", "ghost"], tmp_path)

    assert result.returncode != 0
    assert "ghost" in result.stderr


# ---------------------------------------------------------------------------
# why.sh -- the read-only explainer
# ---------------------------------------------------------------------------


def test_why_reports_documented_defaults_and_names_default_tier_for_every_field(
    tmp_path: pathlib.Path,
) -> None:
    (tmp_path / "projects" / "demo").mkdir(parents=True)

    result = _run(_WHY_SH, ["--project", "demo", "--details"], tmp_path)

    # This fixture is not registered in projects.cfg and has no tasks, so
    # the resolved verdict is legitimately IDLE (exit 2) -- --details exits
    # with that verdict's own code rather than always 0; != 1 is the
    # correct check here since a rendering refusal, not a specific verdict,
    # is what this test is guarding against.
    assert result.returncode != 1, f"why.sh failed: {result.stderr!r}"
    fields = _parse_why_output(result.stdout)
    assert fields["deferred_bugs"] == "false (default)"
    assert fields["deferred_bug_level"] == "low (default)"
    assert fields["defer_piggyback"] == "true (default)"
    assert fields["until"] == "(none) (default)"


def test_why_reports_project_cfg_override_while_other_fields_keep_their_own_tier(
    tmp_path: pathlib.Path,
) -> None:
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    _write_cfg(project_dir / "project.cfg", ["[release]", "deferred_bug_level = high"])

    result = _run(_WHY_SH, ["--project", "demo", "--details"], tmp_path)

    # This fixture is not registered in projects.cfg and has no tasks, so
    # the resolved verdict is legitimately IDLE (exit 2) -- --details exits
    # with that verdict's own code rather than always 0; != 1 is the
    # correct check here since a rendering refusal, not a specific verdict,
    # is what this test is guarding against.
    assert result.returncode != 1, f"why.sh failed: {result.stderr!r}"
    fields = _parse_why_output(result.stdout)
    assert fields["deferred_bug_level"] == "high (project.cfg)"
    assert fields["deferred_bugs"] == "false (default)"
    assert fields["defer_piggyback"] == "true (default)"


def test_why_reports_campaign_file_tier_until_target_and_file_path(tmp_path: pathlib.Path) -> None:
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    campaign_file = project_dir / "DEFER-LOW-BUGS"
    campaign_file.write_text("until=v2.14.0\n", encoding="utf-8")

    result = _run(_WHY_SH, ["--project", "demo", "--details"], tmp_path)

    # This fixture is not registered in projects.cfg and has no tasks, so
    # the resolved verdict is legitimately IDLE (exit 2) -- --details exits
    # with that verdict's own code rather than always 0; != 1 is the
    # correct check here since a rendering refusal, not a specific verdict,
    # is what this test is guarding against.
    assert result.returncode != 1, f"why.sh failed: {result.stderr!r}"
    fields = _parse_why_output(result.stdout)
    assert "campaign file" in fields["deferred_bugs"]
    assert "campaign file" in fields["deferred_bug_level"]
    assert "v2.14.0" in fields["until"]
    assert "campaign file" in fields["until"]
    assert str(campaign_file) in fields["until"]


def test_why_reports_a_malformed_campaign_file_by_path_without_printing_a_posture(
    tmp_path: pathlib.Path,
) -> None:
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    campaign_file = project_dir / "DEFER-LOW-BUGS"
    campaign_file.write_text("until=2.14\n", encoding="utf-8")

    result = _run(_WHY_SH, ["--project", "demo", "--details"], tmp_path)

    assert result.returncode != 0, "expected a malformed campaign file to refuse loud"
    assert str(campaign_file) in result.stderr
    assert result.stdout == "", "why.sh must not print a resolved posture over unreadable state"


def test_why_details_without_project_refuses_dual_channel(tmp_path: pathlib.Path) -> None:
    # --details reports one project's resolved deferral posture; with no
    # project named (and no $PGAI_PROJECT_NAME) there is nothing to resolve
    # against. Bare `why.sh` with no --project and no --details no longer
    # refuses as of the v2.15.0 verdict-engine change -- it walks every
    # registered project instead (see team/tests/unit/test_why_verdict_engine.py
    # for that behavior's coverage) -- so this refusal is scoped to --details.
    result = _run(_WHY_SH, ["--details"], tmp_path)

    assert result.returncode != 0
    assert "error:" in result.stderr
    assert "remedy:" in result.stderr
    assert "--project" in result.stderr


def test_why_against_an_unregistered_project_refuses(tmp_path: pathlib.Path) -> None:
    result = _run(_WHY_SH, ["--project", "ghost"], tmp_path)

    assert result.returncode != 0
    assert "ghost" in result.stderr


def test_why_run_twice_leaves_every_file_under_the_project_root_byte_identical(
    tmp_path: pathlib.Path,
) -> None:
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    _write_cfg(project_dir / "project.cfg", ["[release]", "deferred_bug_level = high"])
    _write_cfg(tmp_path / "kanban.cfg", ["[release]", "deferred_bug_level = medium"])
    (project_dir / "DEFER-LOW-BUGS").write_text("until=v2.14.0\n", encoding="utf-8")

    before = _hash_tree(tmp_path)

    first = _run(_WHY_SH, ["--project", "demo", "--details"], tmp_path)
    second = _run(_WHY_SH, ["--project", "demo", "--details"], tmp_path)

    # This fixture is not registered in projects.cfg and has no tasks, so
    # the resolved verdict is legitimately IDLE (exit 2) -- --details exits
    # with that verdict's own code rather than always 0; what this test
    # actually guards is that both runs agree and neither is a rendering
    # refusal (exit 1).
    assert first.returncode != 1 and second.returncode != 1
    assert first.returncode == second.returncode
    assert first.stdout == second.stdout

    after = _hash_tree(tmp_path)
    assert before == after, "why.sh must never write, edit, or remove any file under the project root"
