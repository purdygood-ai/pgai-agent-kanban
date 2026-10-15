"""
test_config_loader_release_posture_keys.py
============================================
Unit tests for the [release] deferral-posture key validation inside
team/scripts/lib/config_loader.sh's load_config() — the loader's own
refusal behaviour for deferred_bugs, deferred_bug_level, defer_piggyback
and deep_verify, independent of team/scripts/lib/defer_posture.sh (the
resolver), which has its own test coverage in
test_defer_posture_resolver.py.

Acceptance criteria exercised:

  - deferred_bugs = on is refused, naming true and false as the accepted
    set; an equivalent case covers defer_piggyback.
  - deferred_bug_level = critical is refused, naming low, medium and high.
  - deep_verify accepts all four of true, false, weekly and off, refuses an
    unrecognised value naming the file, and exports no PGAI_DEEP_VERIFY
    (pp_deep_verify_mode reads the kanban.cfg tier itself).
  - A cfg carrying defer_piggyback in a section other than [release] loads
    without refusing (this project's own project.cfg has that exact shape
    today under [training]).
  - The resolver's refusal for an out-of-range value names the same
    accepted set the loader names for that key (cross-check with
    defer_posture.sh).

Tests invoke config_loader.sh via subprocess (bash -c "source ... && ..."),
matching the pattern in test_config_loader_effort_resolution.py, so the
fixtures exercise the real production shell code rather than a
reimplementation of it.

Naming: function names describe behavior under test, never bug IDs or
task IDs (see roles/TESTER.md "Test Authoring Guidelines").
"""

from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

# ---------------------------------------------------------------------------
# Paths (relative to this file: team/tests/unit/ -> team/ -> repo root)
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_CONFIG_LOADER = _TEAM_DIR / "scripts" / "lib" / "config_loader.sh"
_DEFER_POSTURE = _TEAM_DIR / "scripts" / "lib" / "defer_posture.sh"


def _run_bash(script: str, env: dict[str, str], timeout: int = 15) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


def _base_env() -> dict[str, str]:
    """A copy of the real environment (so PATH, PGAI_AGENT_KANBAN_TEMP_DIR,
    etc. resolve exactly as run-unit-tests.sh's own launch environment
    does), matching test_config_loader_effort_resolution.py's convention."""
    return dict(os.environ)


def _write_cfg(path: pathlib.Path, lines: list[str]) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _load_config(cfg_file: pathlib.Path, *, export_var: str | None = None) -> subprocess.CompletedProcess:
    """Run load_config against cfg_file. When export_var is given, echo its
    resolved value on success so the test can assert the exported value."""
    script = f"source {str(_CONFIG_LOADER)!r} && load_config {str(cfg_file)!r}"
    if export_var:
        script += f' && echo "{export_var}=[${{{export_var}:-}}]"'
    return _run_bash(script, _base_env())


# ---------------------------------------------------------------------------
# deferred_bugs — accepts true/false only; 'on'/'off' and other stray values
# refuse, naming true and false as the accepted set.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_value", ["on", "off", "maybe"], ids=["on", "off", "maybe"])
def test_deferred_bugs_rejects_unaccepted_value_naming_true_and_false(
    tmp_path: pathlib.Path, bad_value: str
) -> None:
    cfg_file = _write_cfg(tmp_path / "kanban.cfg", ["[release]", f"deferred_bugs = {bad_value}"])

    result = _load_config(cfg_file)

    assert result.returncode != 0, f"expected deferred_bugs={bad_value!r} to refuse loud"
    assert "deferred_bugs" in result.stderr
    assert "true" in result.stderr and "false" in result.stderr


def test_deferred_bugs_accepts_true_and_false(tmp_path: pathlib.Path) -> None:
    for value in ("true", "false"):
        cfg_file = _write_cfg(tmp_path / f"kanban-{value}.cfg", ["[release]", f"deferred_bugs = {value}"])
        result = _load_config(cfg_file, export_var="PGAI_DEFERRED_BUGS")
        assert result.returncode == 0, f"deferred_bugs={value} should load: {result.stderr!r}"
        assert f"PGAI_DEFERRED_BUGS=[{value}]" in result.stdout


# ---------------------------------------------------------------------------
# defer_piggyback — the equivalent boolean-key case: the on/off rejection
# is covered explicitly for both boolean keys, not inferred from one.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_value", ["on", "off", "sometimes"], ids=["on", "off", "sometimes"])
def test_defer_piggyback_rejects_unaccepted_value_naming_true_and_false(
    tmp_path: pathlib.Path, bad_value: str
) -> None:
    cfg_file = _write_cfg(tmp_path / "kanban.cfg", ["[release]", f"defer_piggyback = {bad_value}"])

    result = _load_config(cfg_file)

    assert result.returncode != 0, f"expected defer_piggyback={bad_value!r} to refuse loud"
    assert "defer_piggyback" in result.stderr
    assert "true" in result.stderr and "false" in result.stderr


def test_defer_piggyback_accepts_true_and_false(tmp_path: pathlib.Path) -> None:
    for value in ("true", "false"):
        cfg_file = _write_cfg(tmp_path / f"kanban-{value}.cfg", ["[release]", f"defer_piggyback = {value}"])
        result = _load_config(cfg_file, export_var="PGAI_DEFER_PIGGYBACK")
        assert result.returncode == 0, f"defer_piggyback={value} should load: {result.stderr!r}"
        assert f"PGAI_DEFER_PIGGYBACK=[{value}]" in result.stdout


# ---------------------------------------------------------------------------
# deferred_bug_level — accepts low/medium/high only; an out-of-range word
# refuses, naming all three accepted words.
# ---------------------------------------------------------------------------


def test_deferred_bug_level_rejects_out_of_range_value_naming_low_medium_high(
    tmp_path: pathlib.Path,
) -> None:
    cfg_file = _write_cfg(tmp_path / "kanban.cfg", ["[release]", "deferred_bug_level = critical"])

    result = _load_config(cfg_file)

    assert result.returncode != 0, "expected deferred_bug_level=critical to refuse loud"
    assert "deferred_bug_level" in result.stderr
    for word in ("low", "medium", "high"):
        assert word in result.stderr, f"expected {word!r} named in the refusal: {result.stderr!r}"


def test_deferred_bug_level_accepts_low_medium_and_high(tmp_path: pathlib.Path) -> None:
    for value in ("low", "medium", "high"):
        cfg_file = _write_cfg(tmp_path / f"kanban-{value}.cfg", ["[release]", f"deferred_bug_level = {value}"])
        result = _load_config(cfg_file, export_var="PGAI_DEFERRED_BUG_LEVEL")
        assert result.returncode == 0, f"deferred_bug_level={value} should load: {result.stderr!r}"
        assert f"PGAI_DEFERRED_BUG_LEVEL=[{value}]" in result.stdout


# ---------------------------------------------------------------------------
# deep_verify — the one [release] key with a deliberate legacy-spelling
# exception: weekly|off PLUS true|false are all accepted (true=weekly,
# false=off). An unrecognized value still refuses. The loader validates the
# kanban.cfg value but exports nothing for it: pp_deep_verify_mode reads the
# project.cfg > kanban.cfg chain itself, so an exported copy would have no
# reader.
# ---------------------------------------------------------------------------


def _load_config_without_inherited_deep_verify(cfg_file: pathlib.Path) -> subprocess.CompletedProcess:
    """load_config with any inherited PGAI_DEEP_VERIFY removed from the
    environment, echoing whether load_config itself exported one."""
    env = _base_env()
    env.pop("PGAI_DEEP_VERIFY", None)
    script = (
        f"source {str(_CONFIG_LOADER)!r} && load_config {str(cfg_file)!r} && "
        'echo "PGAI_DEEP_VERIFY=[${PGAI_DEEP_VERIFY-unset}]"'
    )
    return _run_bash(script, env)


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("weekly", id="weekly_word_spelling"),
        pytest.param("off", id="off_word_spelling"),
        pytest.param("true", id="true_accepted_as_legacy_alias"),
        pytest.param("false", id="false_accepted_as_legacy_alias"),
    ],
)
def test_deep_verify_accepts_all_four_documented_spellings_without_exporting_a_copy(
    tmp_path: pathlib.Path, value: str
) -> None:
    cfg_file = _write_cfg(tmp_path / "kanban.cfg", ["[release]", f"deep_verify = {value}"])

    result = _load_config_without_inherited_deep_verify(cfg_file)

    assert result.returncode == 0, f"deep_verify={value} should load: {result.stderr!r}"
    assert "PGAI_DEEP_VERIFY=[unset]" in result.stdout, (
        f"load_config must not export PGAI_DEEP_VERIFY (no reader); stdout: {result.stdout!r}"
    )


def test_deep_verify_rejects_an_unrecognised_value_naming_the_file_and_key(
    tmp_path: pathlib.Path,
) -> None:
    cfg_file = _write_cfg(tmp_path / "kanban.cfg", ["[release]", "deep_verify = sometimes"])

    result = _load_config(cfg_file)

    assert result.returncode != 0, "expected deep_verify=sometimes to refuse loud"
    assert "deep_verify" in result.stderr
    assert str(cfg_file) in result.stderr, f"refusal must name the kanban.cfg path: {result.stderr!r}"
    assert "weekly" in result.stderr and "off" in result.stderr


# A test that stops a later reader from "cleaning up" the legacy true/false
# spellings and wedging an existing kanban.cfg that uses them.
def test_deep_verify_legacy_boolean_spellings_remain_accepted_after_the_word_spellings(
    tmp_path: pathlib.Path,
) -> None:
    for value in ("true", "false"):
        cfg_file = _write_cfg(tmp_path / f"kanban-{value}.cfg", ["[release]", f"deep_verify = {value}"])
        result = _load_config_without_inherited_deep_verify(cfg_file)
        assert result.returncode == 0, (
            f"deep_verify={value} (legacy boolean spelling) must keep resolving: {result.stderr!r}"
        )


# ---------------------------------------------------------------------------
# Stray-key case: a same-named key sitting in a section other than
# [release] is never read by the [release] registry and must not cause a
# refusal. This project's own project.cfg carries defer_piggyback = any
# under [training] today; a regression here wedges the self-build chain.
# ---------------------------------------------------------------------------


def test_defer_piggyback_outside_release_section_loads_without_refusing(
    tmp_path: pathlib.Path,
) -> None:
    cfg_file = _write_cfg(
        tmp_path / "project.cfg",
        [
            "[training]",
            "reasoning_trace = false",
            "defer_piggyback = any",
        ],
    )

    result = _load_config(cfg_file, export_var="PGAI_DEFER_PIGGYBACK")

    assert result.returncode == 0, (
        f"a stray defer_piggyback outside [release] must not cause load_config to "
        f"refuse: {result.stderr!r}"
    )
    # [release] never carried the key, so the built-in default (true) applies --
    # the [training]-scoped "any" value is invisible to this registry by design.
    assert "PGAI_DEFER_PIGGYBACK=[true]" in result.stdout


def test_deferred_bug_level_outside_release_section_does_not_trigger_the_release_refusal(
    tmp_path: pathlib.Path,
) -> None:
    """The same stray-key immunity for the enum key: an out-of-range word
    sitting in an unrelated section is never read by the [release] registry
    and the documented default still applies."""
    cfg_file = _write_cfg(
        tmp_path / "project.cfg",
        ["[training]", "deferred_bug_level = not-a-real-level"],
    )

    result = _load_config(cfg_file, export_var="PGAI_DEFERRED_BUG_LEVEL")

    assert result.returncode == 0, (
        f"a stray deferred_bug_level outside [release] must not cause load_config to "
        f"refuse: {result.stderr!r}"
    )
    assert "PGAI_DEFERRED_BUG_LEVEL=[low]" in result.stdout


# ---------------------------------------------------------------------------
# Loader/resolver accepted-set wording agreement: the resolver's own
# refusal for an out-of-range value names the same accepted set the loader
# names for that key, so an operator reading either refusal sees the same
# answer to "what values are right".
# ---------------------------------------------------------------------------


def _resolver_refusal_stderr(tmp_path: pathlib.Path, key: str, bad_value: str) -> str:
    project_cfg = _write_cfg(
        tmp_path / "projects" / "demo" / "project.cfg", ["[release]", f"{key} = {bad_value}"]
    )
    (tmp_path / "kanban.cfg").write_text("", encoding="utf-8")
    env = _base_env()
    env.pop("PGAI_PROJECT_NAME", None)
    env["KANBAN_ROOT"] = str(tmp_path)
    script = f"source {str(_DEFER_POSTURE)!r} && resolve_defer_posture demo"
    result = _run_bash(script, env)
    assert result.returncode != 0, f"expected resolver to refuse {key}={bad_value!r}"
    return result.stderr


@pytest.mark.parametrize(
    "key, bad_value, accepted_set_substring",
    [
        pytest.param("deferred_bugs", "on", "true|false", id="deferred_bugs"),
        pytest.param("defer_piggyback", "on", "true|false", id="defer_piggyback"),
        pytest.param("deferred_bug_level", "critical", "low|medium|high", id="deferred_bug_level"),
    ],
)
def test_resolver_refusal_names_the_same_accepted_set_as_the_loader(
    tmp_path: pathlib.Path, key: str, bad_value: str, accepted_set_substring: str
) -> None:
    loader_cfg = _write_cfg(tmp_path / "kanban.cfg", ["[release]", f"{key} = {bad_value}"])
    loader_result = _load_config(loader_cfg)
    assert loader_result.returncode != 0, f"expected loader to refuse {key}={bad_value!r}"
    assert accepted_set_substring in loader_result.stderr, (
        f"loader refusal for {key} did not name {accepted_set_substring!r}: "
        f"{loader_result.stderr!r}"
    )

    resolver_stderr = _resolver_refusal_stderr(tmp_path / "resolver_side", key, bad_value)
    assert accepted_set_substring in resolver_stderr, (
        f"resolver refusal for {key} did not name {accepted_set_substring!r}: {resolver_stderr!r}"
    )
