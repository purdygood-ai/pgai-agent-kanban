"""
test_defer_posture_resolver.py
================================
Unit tests for team/scripts/lib/defer_posture.sh — the single resolver for
the deferral posture in force right now, layering a DEFER-LOW-BUGS campaign
file over a project's project.cfg [release] section over kanban.cfg
[release] over the built-in default.

Acceptance criteria exercised:

  (1) Nothing set anywhere -> deferred_bugs=false, level=low, piggyback=true,
      no until target, tier 'default' for each.
  (2) deferred_bug_level=medium in kanban.cfg only -> level=medium, tier
      'kanban.cfg'.
  (3) kanban.cfg medium + project.cfg high -> level=high, tier 'project.cfg'.
  (4) A project-scoped campaign file with until=v2.14.0 forces deferral on
      with that until target, tier 'campaign file', overriding both cfg
      files.
  (5) Project-scoped and root-scoped campaign files both present -> the
      project-scoped one resolves.
  (6) A campaign file whose body reads 'until=2.14' refuses loud naming the
      malformed value and the expected vX.Y.Z form; the resolver does not
      fall through to the cfg tiers.
  (7) An out-of-range value for each of the three posture keys refuses,
      naming that key's accepted set with the same wording config_loader.sh
      uses for the same key.
  (8) No side effects: the campaign file and both cfg files are
      byte-identical after two resolutions.

The resolver also reports a fifth field, campaign_file (the exact path a
live campaign resolved from, sharing until's tier) — it lets the why verb
name the file without re-deriving the project/root campaign-file lookup
itself; that field is asserted alongside the fields above wherever a
campaign file is or is not in force.

  (9) The deferral bounds and bundle-mixing keys (deferred_max_age_days,
      deferred_max_count, bundle_mixing, ride_along_max) resolve through
      project.cfg > kanban.cfg > default in both resolvers -- the bash one
      above and pgai_agent_kanban.lib.defer_posture -- with the same value
      and tier; a campaign file never touches them; an invalid value
      (negative, fractional or worded number, a mixing mode outside
      separate|ride-along) is refused by both with the same dual-channel
      block naming the accepted set.

Tests invoke defer_posture.sh via subprocess (bash -c "source ... && ..."),
matching the pattern in test_config_loader_effort_resolution.py, so the
fixtures exercise the real production shell code rather than a
reimplementation of it.

Naming: function names describe behavior under test, never bug IDs or
task IDs (see roles/TESTER.md "Test Authoring Guidelines").
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import subprocess
import sys

import pytest

# ---------------------------------------------------------------------------
# Paths (relative to this file: team/tests/unit/ -> team/ -> repo root)
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_DEFER_POSTURE = _TEAM_DIR / "scripts" / "lib" / "defer_posture.sh"
if str(_TEAM_DIR.resolve()) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR.resolve()))

from pgai_agent_kanban.lib import defer_posture  # noqa: E402


def _run_bash(script: str, env: dict[str, str], timeout: int = 15) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


def _base_env(kanban_root: pathlib.Path, project: str | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PGAI_PROJECT_NAME", None)
    env["KANBAN_ROOT"] = str(kanban_root)
    if project is not None:
        env["PGAI_PROJECT_NAME"] = project
    return env


def _write_cfg(path: pathlib.Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _resolve(kanban_root: pathlib.Path, project: str | None = "demo") -> subprocess.CompletedProcess:
    script = f"source {str(_DEFER_POSTURE)!r} && resolve_defer_posture {project or ''}"
    return _run_bash(script, _base_env(kanban_root, project=None))


def _parse_lines(stdout: str) -> dict[str, tuple[str, str]]:
    """Parse the resolver's "<field>=<value>|<tier>" stdout lines into a dict."""
    fields: dict[str, tuple[str, str]] = {}
    for line in stdout.strip().splitlines():
        field, rest = line.split("=", 1)
        value, tier = rest.split("|", 1)
        fields[field] = (value, tier)
    return fields


# ---------------------------------------------------------------------------
# (1) Nothing set anywhere -> every value at its built-in default.
# ---------------------------------------------------------------------------


def test_absent_everywhere_resolves_to_documented_defaults(tmp_path: pathlib.Path) -> None:
    (tmp_path / "projects" / "demo").mkdir(parents=True)

    result = _resolve(tmp_path)

    assert result.returncode == 0, f"resolve_defer_posture failed: {result.stderr!r}"
    fields = _parse_lines(result.stdout)
    assert fields["deferred_bugs"] == ("false", "default")
    assert fields["deferred_bug_level"] == ("low", "default")
    assert fields["defer_piggyback"] == ("true", "default")
    assert fields["until"] == ("", "none")
    assert fields["campaign_file"] == ("", "none")


# ---------------------------------------------------------------------------
# (2) kanban.cfg sets the level; project.cfg is silent on it.
# ---------------------------------------------------------------------------


def test_kanban_cfg_level_resolves_with_kanban_cfg_tier(tmp_path: pathlib.Path) -> None:
    (tmp_path / "projects" / "demo").mkdir(parents=True)
    _write_cfg(tmp_path / "kanban.cfg", ["[release]", "deferred_bug_level = medium"])

    result = _resolve(tmp_path)

    assert result.returncode == 0, f"resolve_defer_posture failed: {result.stderr!r}"
    fields = _parse_lines(result.stdout)
    assert fields["deferred_bug_level"] == ("medium", "kanban.cfg")


# ---------------------------------------------------------------------------
# (3) project.cfg overrides kanban.cfg for the same key.
# ---------------------------------------------------------------------------


def test_project_cfg_level_overrides_kanban_cfg(tmp_path: pathlib.Path) -> None:
    _write_cfg(tmp_path / "kanban.cfg", ["[release]", "deferred_bug_level = medium"])
    _write_cfg(
        tmp_path / "projects" / "demo" / "project.cfg",
        ["[release]", "deferred_bug_level = high"],
    )

    result = _resolve(tmp_path)

    assert result.returncode == 0, f"resolve_defer_posture failed: {result.stderr!r}"
    fields = _parse_lines(result.stdout)
    assert fields["deferred_bug_level"] == ("high", "project.cfg")


# ---------------------------------------------------------------------------
# (4) A project-scoped campaign file forces deferral on, overriding both cfg
#     files, and reports the until target with tier 'campaign file'.
# ---------------------------------------------------------------------------


def test_project_scoped_campaign_file_overrides_both_cfg_files(tmp_path: pathlib.Path) -> None:
    _write_cfg(tmp_path / "kanban.cfg", ["[release]", "deferred_bugs = false", "deferred_bug_level = high"])
    _write_cfg(
        tmp_path / "projects" / "demo" / "project.cfg",
        ["[release]", "deferred_bugs = false", "deferred_bug_level = medium"],
    )
    (tmp_path / "projects" / "demo" / "DEFER-LOW-BUGS").write_text("until=v2.14.0\n", encoding="utf-8")

    result = _resolve(tmp_path)

    assert result.returncode == 0, f"resolve_defer_posture failed: {result.stderr!r}"
    fields = _parse_lines(result.stdout)
    assert fields["deferred_bugs"] == ("true", "campaign file")
    assert fields["deferred_bug_level"] == ("low", "campaign file")
    assert fields["until"] == ("v2.14.0", "campaign file")
    assert fields["campaign_file"] == (
        str(tmp_path / "projects" / "demo" / "DEFER-LOW-BUGS"),
        "campaign file",
    )


# ---------------------------------------------------------------------------
# (5) Project-scoped and root-scoped campaign files both present -> the
#     project-scoped file is the one that resolves.
# ---------------------------------------------------------------------------


def test_project_scoped_campaign_file_wins_over_root_scoped(tmp_path: pathlib.Path) -> None:
    (tmp_path / "projects" / "demo").mkdir(parents=True)
    (tmp_path / "DEFER-LOW-BUGS").write_text("until=v9.9.9\n", encoding="utf-8")
    (tmp_path / "projects" / "demo" / "DEFER-LOW-BUGS").write_text("until=v2.14.0\n", encoding="utf-8")

    result = _resolve(tmp_path)

    assert result.returncode == 0, f"resolve_defer_posture failed: {result.stderr!r}"
    fields = _parse_lines(result.stdout)
    assert fields["until"] == ("v2.14.0", "campaign file")
    assert fields["campaign_file"][0] == str(tmp_path / "projects" / "demo" / "DEFER-LOW-BUGS")


def test_root_scoped_campaign_file_covers_a_project_with_no_scoped_file(tmp_path: pathlib.Path) -> None:
    (tmp_path / "projects" / "demo").mkdir(parents=True)
    (tmp_path / "DEFER-LOW-BUGS").write_text("until=v3.0.0\n", encoding="utf-8")

    result = _resolve(tmp_path)

    assert result.returncode == 0, f"resolve_defer_posture failed: {result.stderr!r}"
    fields = _parse_lines(result.stdout)
    assert fields["campaign_file"][0] == str(tmp_path / "DEFER-LOW-BUGS")
    assert fields["until"] == ("v3.0.0", "campaign file")


# ---------------------------------------------------------------------------
# (6) Malformed until= refuses loud; no fallthrough to deferral-off.
# ---------------------------------------------------------------------------


def test_malformed_until_value_refuses_loud_without_falling_through(tmp_path: pathlib.Path) -> None:
    (tmp_path / "projects" / "demo").mkdir(parents=True)
    (tmp_path / "projects" / "demo" / "DEFER-LOW-BUGS").write_text("until=2.14\n", encoding="utf-8")

    result = _resolve(tmp_path)

    assert result.returncode != 0, "expected malformed until= to refuse loud"
    assert "2.14" in result.stderr
    assert "vX.Y.Z" in result.stderr
    assert "deferred_bugs=" not in result.stdout, (
        "resolver must not print any resolution — partial or fallback — "
        "when the campaign file fails to parse"
    )


def test_campaign_file_with_no_until_line_refuses_loud(tmp_path: pathlib.Path) -> None:
    (tmp_path / "projects" / "demo").mkdir(parents=True)
    (tmp_path / "projects" / "demo" / "DEFER-LOW-BUGS").write_text("not a key=value line\n", encoding="utf-8")

    result = _resolve(tmp_path)

    assert result.returncode != 0, "expected an unparseable campaign file to refuse loud"
    assert "until=" in result.stderr


# ---------------------------------------------------------------------------
# (7) Out-of-range values for each of the three keys refuse, naming the
#     accepted set with the same wording config_loader.sh uses.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key, bad_value, expected_substrings",
    [
        pytest.param(
            "deferred_bugs", "maybe",
            ["deferred_bugs", "true|false", "on"],
            id="invalid_deferred_bugs_names_true_false",
        ),
        pytest.param(
            "deferred_bug_level", "extreme",
            ["deferred_bug_level", "low|medium|high"],
            id="invalid_deferred_bug_level_names_enum",
        ),
        pytest.param(
            "defer_piggyback", "on",
            ["defer_piggyback", "true|false"],
            id="invalid_defer_piggyback_names_true_false",
        ),
    ],
)
def test_out_of_range_value_refuses_naming_accepted_set(
    tmp_path: pathlib.Path, key: str, bad_value: str, expected_substrings: list[str]
) -> None:
    _write_cfg(tmp_path / "projects" / "demo" / "project.cfg", ["[release]", f"{key} = {bad_value}"])

    result = _resolve(tmp_path)

    assert result.returncode != 0, f"expected {key}={bad_value!r} to refuse loud"
    for substring in expected_substrings:
        assert substring in result.stderr, (
            f"expected {substring!r} named in the refusal; stderr: {result.stderr!r}"
        )


def test_invalid_value_at_an_unconsulted_tier_does_not_refuse(tmp_path: pathlib.Path) -> None:
    """An invalid deferred_bug_level sitting in kanban.cfg never gets read
    once project.cfg supplies a valid value for the same key -- only the
    tier the precedence chain actually lands on is validated."""
    _write_cfg(tmp_path / "kanban.cfg", ["[release]", "deferred_bug_level = not-a-level"])
    _write_cfg(
        tmp_path / "projects" / "demo" / "project.cfg",
        ["[release]", "deferred_bug_level = medium"],
    )

    result = _resolve(tmp_path)

    assert result.returncode == 0, f"resolve_defer_posture failed: {result.stderr!r}"
    fields = _parse_lines(result.stdout)
    assert fields["deferred_bug_level"] == ("medium", "project.cfg")


# ---------------------------------------------------------------------------
# (8) No side effects: running the resolver twice leaves every file
#     byte-identical.
# ---------------------------------------------------------------------------


def _hash_file(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


def test_resolver_has_no_side_effects_on_campaign_or_cfg_files(tmp_path: pathlib.Path) -> None:
    kanban_cfg = tmp_path / "kanban.cfg"
    project_cfg = tmp_path / "projects" / "demo" / "project.cfg"
    campaign_file = tmp_path / "projects" / "demo" / "DEFER-LOW-BUGS"
    _write_cfg(kanban_cfg, ["[release]", "deferred_bug_level = medium"])
    _write_cfg(project_cfg, ["[release]", "deferred_bug_level = high"])
    campaign_file.write_text("until=v2.14.0\n", encoding="utf-8")

    before = {p: _hash_file(p) for p in (kanban_cfg, project_cfg, campaign_file)}

    result1 = _resolve(tmp_path)
    result2 = _resolve(tmp_path)

    assert result1.returncode == 0 and result2.returncode == 0
    assert result1.stdout == result2.stdout

    after = {p: _hash_file(p) for p in (kanban_cfg, project_cfg, campaign_file)}
    assert before == after, "resolve_defer_posture must never write, edit, or remove any file it reads"


# ---------------------------------------------------------------------------
# No project context: root-scoped resolution only (no project.cfg tier).
# ---------------------------------------------------------------------------


def test_no_project_context_resolves_root_scope_only(tmp_path: pathlib.Path) -> None:
    _write_cfg(tmp_path / "kanban.cfg", ["[release]", "deferred_bug_level = medium"])

    script = f"source {str(_DEFER_POSTURE)!r} && resolve_defer_posture"
    env = dict(os.environ)
    env.pop("PGAI_PROJECT_NAME", None)
    env["KANBAN_ROOT"] = str(tmp_path)
    result = _run_bash(script, env)

    assert result.returncode == 0, f"resolve_defer_posture failed: {result.stderr!r}"
    fields = _parse_lines(result.stdout)
    assert fields["deferred_bug_level"] == ("medium", "kanban.cfg")


# ---------------------------------------------------------------------------
# (9) Deferral bounds and bundle mixing: both resolvers, every tier.
# ---------------------------------------------------------------------------

_CARRY_DEFAULTS = {
    "deferred_max_age_days": "",
    "deferred_max_count": "",
    "bundle_mixing": "separate",
    "ride_along_max": "3",
}


def _python_fields(kanban_root: pathlib.Path, project: str | None = "demo") -> dict[str, tuple[str, str]]:
    """The Python resolver's answer, in the bash resolver's line shape."""
    posture = defer_posture.resolve_defer_posture(kanban_root, project)
    return _parse_lines(posture.resolver_lines())


def test_bounds_and_mixing_keys_default_identically_in_both_resolvers(tmp_path: pathlib.Path) -> None:
    (tmp_path / "projects" / "demo").mkdir(parents=True)

    result = _resolve(tmp_path)

    assert result.returncode == 0, f"resolve_defer_posture failed: {result.stderr!r}"
    bash_fields = _parse_lines(result.stdout)
    py_fields = _python_fields(tmp_path)
    for key, default in _CARRY_DEFAULTS.items():
        assert bash_fields[key] == (default, "default"), key
        assert py_fields[key] == (default, "default"), key
    assert result.stdout == defer_posture.resolve_defer_posture(tmp_path, "demo").resolver_lines()


@pytest.mark.parametrize(
    "key, project_value, kanban_value",
    [
        pytest.param("deferred_max_age_days", "30", "90", id="max_age_days"),
        pytest.param("deferred_max_count", "5", "12", id="max_count"),
        pytest.param("bundle_mixing", "ride-along", "separate", id="bundle_mixing"),
        pytest.param("ride_along_max", "0", "7", id="ride_along_max"),
    ],
)
@pytest.mark.parametrize("tier", ["project.cfg", "kanban.cfg", "default"])
def test_bounds_and_mixing_keys_resolve_the_same_value_and_tier_in_both_resolvers(
    tmp_path: pathlib.Path, key: str, project_value: str, kanban_value: str, tier: str
) -> None:
    (tmp_path / "projects" / "demo").mkdir(parents=True)
    if tier in ("project.cfg", "kanban.cfg"):
        _write_cfg(tmp_path / "kanban.cfg", ["[release]", f"{key} = {kanban_value}"])
    if tier == "project.cfg":
        _write_cfg(tmp_path / "projects" / "demo" / "project.cfg", ["[release]", f"{key} = {project_value}"])
    expected = {
        "project.cfg": (project_value, "project.cfg"),
        "kanban.cfg": (kanban_value, "kanban.cfg"),
        "default": (_CARRY_DEFAULTS[key], "default"),
    }[tier]

    result = _resolve(tmp_path)

    assert result.returncode == 0, f"resolve_defer_posture failed: {result.stderr!r}"
    assert _parse_lines(result.stdout)[key] == expected
    assert _python_fields(tmp_path)[key] == expected
    assert result.stdout == defer_posture.resolve_defer_posture(tmp_path, "demo").resolver_lines()


def test_campaign_file_leaves_bounds_and_mixing_keys_to_the_cfg_chain(tmp_path: pathlib.Path) -> None:
    _write_cfg(
        tmp_path / "projects" / "demo" / "project.cfg",
        ["[release]", "deferred_max_count = 4", "bundle_mixing = ride-along"],
    )
    _write_cfg(tmp_path / "kanban.cfg", ["[release]", "deferred_max_age_days = 21"])
    (tmp_path / "projects" / "demo" / "DEFER-LOW-BUGS").write_text("until=v2.14.0\n", encoding="utf-8")

    result = _resolve(tmp_path)

    assert result.returncode == 0, f"resolve_defer_posture failed: {result.stderr!r}"
    for fields in (_parse_lines(result.stdout), _python_fields(tmp_path)):
        assert fields["deferred_bugs"] == ("true", "campaign file")
        assert fields["deferred_max_count"] == ("4", "project.cfg")
        assert fields["deferred_max_age_days"] == ("21", "kanban.cfg")
        assert fields["bundle_mixing"] == ("ride-along", "project.cfg")
        assert fields["ride_along_max"] == ("3", "default")


@pytest.mark.parametrize(
    "key, bad_value, accepted",
    [
        pytest.param("deferred_max_age_days", "-1", "non-negative whole number", id="negative_age"),
        pytest.param("deferred_max_age_days", "1.5", "non-negative whole number", id="fractional_age"),
        pytest.param("deferred_max_count", "five", "non-negative whole number", id="worded_count"),
        pytest.param("deferred_max_count", "-3", "non-negative whole number", id="negative_count"),
        pytest.param("ride_along_max", "2.0", "non-negative whole number", id="fractional_ride_along_max"),
        pytest.param("ride_along_max", "-1", "non-negative whole number", id="negative_ride_along_max"),
        pytest.param("bundle_mixing", "on", "separate|ride-along", id="bundle_mixing_on"),
        pytest.param("bundle_mixing", "ride_along", "separate|ride-along", id="bundle_mixing_underscore"),
        pytest.param("bundle_mixing", "true", "separate|ride-along", id="bundle_mixing_boolean"),
    ],
)
@pytest.mark.parametrize("cfg_name", ["project.cfg", "kanban.cfg"])
def test_invalid_bound_or_mixing_value_is_refused_dual_channel_by_both_resolvers(
    tmp_path: pathlib.Path, key: str, bad_value: str, accepted: str, cfg_name: str
) -> None:
    (tmp_path / "projects" / "demo").mkdir(parents=True)
    cfg = tmp_path / "kanban.cfg" if cfg_name == "kanban.cfg" else tmp_path / "projects" / "demo" / "project.cfg"
    _write_cfg(cfg, ["[release]", f"{key} = {bad_value}"])

    result = _resolve(tmp_path)

    assert result.returncode != 0, f"expected {key}={bad_value!r} to refuse loud"
    assert "deferred_bugs=" not in result.stdout, "a refusal prints no partial resolution"
    for channel in ("error", "message", "remedy"):
        assert f"{channel}:" in result.stderr, f"missing {channel!r} channel: {result.stderr!r}"
    assert f"defer_posture.invalid_{key}" in result.stderr
    assert f"{key}='{bad_value}'" in result.stderr
    assert str(cfg) in result.stderr
    assert accepted in result.stderr

    with pytest.raises(defer_posture.DeferPostureError) as excinfo:
        defer_posture.resolve_defer_posture(tmp_path, "demo")
    assert excinfo.value.error == f"defer_posture.invalid_{key}"
    assert excinfo.value.file == str(cfg)
    assert result.stderr == f"{excinfo.value}\n"
