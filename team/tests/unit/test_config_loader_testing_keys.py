"""
test_config_loader_testing_keys.py
==================================
Unit tests for the kanban-wide ``[testing]`` keys resolved by
team/scripts/lib/config_loader.sh's ``config_testing_workers``,
``config_testing_coverage`` and ``config_testing_pre_merge_affected_cap``.
The last has its own section at the end of this file (default 60, 0 = no
cap); the bullets below describe the workers and coverage keys:

  - A value present in the cfg file resolves to itself (``workers`` with
    leading zeros normalised to plain decimal digits).
  - An absent key, a blank value, or no cfg file at all resolves to the
    documented default — ``workers`` 3, ``coverage`` false — and
    narrates that on stderr.
  - A malformed value (``workers = abc``, ``coverage = on``) refuses
    dual-channel: a stable ``error`` key, a ``message`` naming the value
    and the accepted set, and a ``remedy`` that is runnable as printed and
    actually fixes the file. Nothing is printed on stdout.
  - ``load_config`` registers both keys (no unknown-key warning) but does
    not validate them, so a malformed ``[testing]`` value never stops a
    wake; the resolvers default to the cfg file ``load_config`` recorded.
  - The shipped kanban.cfg_example carries both keys, documented inline,
    and project.cfg_example carries no ``[testing]`` section.

Tests invoke config_loader.sh via subprocess (bash -c "source ... && ..."),
matching test_config_loader_key_validation.py, so the fixtures exercise the
real production shell code rather than a reimplementation. Each subprocess
environment is built from an explicit set (build_scrubbed_env), so an
operator's exported variables cannot move a verdict.
"""

from __future__ import annotations

import pathlib
import re
import subprocess

import pytest

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_REPO_ROOT = _TEAM_DIR.parent
_CONFIG_LOADER = _TEAM_DIR / "scripts" / "lib" / "config_loader.sh"
_KANBAN_CFG_EXAMPLE = _REPO_ROOT / "kanban.cfg_example"
_PROJECT_CFG_EXAMPLE = _REPO_ROOT / "project.cfg_example"

_WORKERS_ERROR = "config_loader.testing.invalid_workers"
_COVERAGE_ERROR = "config_loader.testing.invalid_coverage"
_CAP_ERROR = "config_loader.testing.invalid_pre_merge_affected_cap"
_CAP_FUNC = "config_testing_pre_merge_affected_cap"


def _env(tmp_path: pathlib.Path) -> dict[str, str]:
    """An explicit environment with a private temp root for load_config."""
    return build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_env={"PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "kanban-tmp")},
    )


def _run(script: str, tmp_path: pathlib.Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f"source {str(_CONFIG_LOADER)!r} && {script}"],
        capture_output=True,
        text=True,
        env=_env(tmp_path),
        timeout=20,
    )


def _resolve(func: str, cfg: pathlib.Path | str, tmp_path: pathlib.Path) -> subprocess.CompletedProcess:
    return _run(f"{func} {str(cfg)!r}", tmp_path)


def _write_cfg(tmp_path: pathlib.Path, text: str, name: str = "kanban.cfg") -> pathlib.Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _contract(stderr: str) -> dict[str, str]:
    """The three dual-channel fields from a refusal's stderr."""
    fields: dict[str, str] = {}
    for line in stderr.splitlines():
        m = re.match(r"^(error|message|remedy):\s+(.*)$", line)
        if m:
            fields[m.group(1)] = m.group(2)
    return fields


# ---------------------------------------------------------------------------
# Values present in the cfg file resolve to themselves
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        pytest.param("3", "3", id="three"),
        pytest.param("0", "0", id="zero_serial"),
        pytest.param("1", "1", id="one_serial"),
        pytest.param("12", "12", id="two_digits"),
        pytest.param("03", "3", id="leading_zero_dropped"),
        pytest.param("00", "0", id="all_zeros_is_zero"),
    ],
)
def test_workers_value_resolves_to_plain_decimal(tmp_path: pathlib.Path, raw: str, expected: str) -> None:
    cfg = _write_cfg(tmp_path, f"[testing]\nworkers = {raw}\n")
    result = _resolve("config_testing_workers", cfg, tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected
    assert "absent" not in result.stderr, result.stderr


@pytest.mark.parametrize("raw", ["true", "false"])
def test_coverage_value_resolves_to_itself(tmp_path: pathlib.Path, raw: str) -> None:
    cfg = _write_cfg(tmp_path, f"[testing]\ncoverage = {raw}\n")
    result = _resolve("config_testing_coverage", cfg, tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == raw


def test_inline_comment_after_value_is_not_part_of_it(tmp_path: pathlib.Path) -> None:
    cfg = _write_cfg(tmp_path, "[testing]\nworkers = 3 # bounded budget\ncoverage = true # weekly\n")
    workers = _resolve("config_testing_workers", cfg, tmp_path)
    coverage = _resolve("config_testing_coverage", cfg, tmp_path)
    assert (workers.returncode, workers.stdout.strip()) == (0, "3"), workers.stderr
    assert (coverage.returncode, coverage.stdout.strip()) == (0, "true"), coverage.stderr


def test_same_named_key_in_another_section_is_not_read(tmp_path: pathlib.Path) -> None:
    """Only the [testing]-scoped value counts; a stray key elsewhere is ignored."""
    cfg = _write_cfg(tmp_path, "[wake]\nworkers = abc\ncoverage = on\n[testing]\nworkers = 2\n")
    workers = _resolve("config_testing_workers", cfg, tmp_path)
    coverage = _resolve("config_testing_coverage", cfg, tmp_path)
    assert (workers.returncode, workers.stdout.strip()) == (0, "2"), workers.stderr
    assert (coverage.returncode, coverage.stdout.strip()) == (0, "false"), coverage.stderr


# ---------------------------------------------------------------------------
# Absent key -> documented default, narrated
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cfg_text",
    [
        pytest.param("[wake]\nmax_tasks_per_wake = 5\n", id="section_absent"),
        pytest.param("[testing]\n", id="keys_absent"),
        pytest.param("[testing]\nworkers =\ncoverage =\n", id="values_blank"),
    ],
)
def test_absent_or_blank_keys_take_documented_defaults(tmp_path: pathlib.Path, cfg_text: str) -> None:
    cfg = _write_cfg(tmp_path, cfg_text)
    workers = _resolve("config_testing_workers", cfg, tmp_path)
    coverage = _resolve("config_testing_coverage", cfg, tmp_path)
    assert (workers.returncode, workers.stdout.strip()) == (0, "3"), workers.stderr
    assert (coverage.returncode, coverage.stdout.strip()) == (0, "false"), coverage.stderr
    assert "[testing] workers absent; applying documented default '3'" in workers.stderr
    assert "[testing] coverage absent; applying documented default 'false'" in coverage.stderr


def test_missing_cfg_file_takes_documented_defaults(tmp_path: pathlib.Path) -> None:
    """A dev tree with no installed kanban.cfg reads as an absent section."""
    missing = tmp_path / "no-such-kanban.cfg"
    workers = _resolve("config_testing_workers", missing, tmp_path)
    coverage = _resolve("config_testing_coverage", missing, tmp_path)
    assert (workers.returncode, workers.stdout.strip()) == (0, "3"), workers.stderr
    assert (coverage.returncode, coverage.stdout.strip()) == (0, "false"), coverage.stderr


def test_default_narration_is_written_once_per_process(tmp_path: pathlib.Path) -> None:
    cfg = _write_cfg(tmp_path, "[testing]\n")
    result = _run(
        f"config_testing_workers {str(cfg)!r} >/dev/null && config_testing_workers {str(cfg)!r}",
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr.count("[testing] workers absent") == 1, result.stderr


# ---------------------------------------------------------------------------
# Malformed values refuse dual-channel
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["abc", "3x", "-1", "+2", "1.5", "three", "3 workers"])
def test_malformed_workers_refuses_dual_channel(tmp_path: pathlib.Path, raw: str) -> None:
    cfg = _write_cfg(tmp_path, f"[testing]\nworkers = {raw}\n")
    result = _resolve("config_testing_workers", cfg, tmp_path)
    assert result.returncode != 0, result.stdout
    assert result.stdout == "", "a refused value must print nothing a caller could use"
    fields = _contract(result.stderr)
    assert fields.get("error") == _WORKERS_ERROR, result.stderr
    assert f"'{raw}'" in fields["message"], fields["message"]
    assert "[testing] workers" in fields["message"] and "0 or 1 = serial" in fields["message"]
    assert str(cfg) in fields["remedy"], fields["remedy"]
    assert "<" not in fields["remedy"], "remedy must be runnable as printed"


@pytest.mark.parametrize("raw", ["on", "off", "yes", "True", "FALSE", "1", "0"])
def test_malformed_coverage_refuses_dual_channel(tmp_path: pathlib.Path, raw: str) -> None:
    cfg = _write_cfg(tmp_path, f"[testing]\ncoverage = {raw}\n")
    result = _resolve("config_testing_coverage", cfg, tmp_path)
    assert result.returncode != 0, result.stdout
    assert result.stdout == ""
    fields = _contract(result.stderr)
    assert fields.get("error") == _COVERAGE_ERROR, result.stderr
    assert f"'{raw}'" in fields["message"], fields["message"]
    assert "true|false" in fields["message"], "message must name the accepted set"
    assert str(cfg) in fields["remedy"], fields["remedy"]
    assert "<" not in fields["remedy"], "remedy must be runnable as printed"


@pytest.mark.parametrize(
    "func, cfg_text, expected",
    [
        pytest.param("config_testing_workers", "[testing]\nworkers = 2x\n", "2", id="workers_keeps_leading_digits"),
        pytest.param("config_testing_workers", "[testing]\nworkers = abc\n", "3", id="workers_falls_back_to_default"),
        pytest.param("config_testing_coverage", "[testing]\ncoverage = on\n", "false", id="coverage_to_default"),
    ],
)
def test_refusal_remedy_runs_as_printed_and_fixes_the_file(
    tmp_path: pathlib.Path, func: str, cfg_text: str, expected: str
) -> None:
    cfg = _write_cfg(tmp_path, cfg_text)
    refused = _resolve(func, cfg, tmp_path)
    assert refused.returncode != 0
    remedy = _contract(refused.stderr)["remedy"]
    applied = subprocess.run(["bash", "-c", remedy], capture_output=True, text=True, env=_env(tmp_path), timeout=10)
    assert applied.returncode == 0, applied.stderr
    fixed = _resolve(func, cfg, tmp_path)
    assert (fixed.returncode, fixed.stdout.strip()) == (0, expected), fixed.stderr


# ---------------------------------------------------------------------------
# load_config: registered, not validated, and recorded for the resolvers
# ---------------------------------------------------------------------------


def test_load_config_knows_testing_keys_and_does_not_refuse_their_values(tmp_path: pathlib.Path) -> None:
    """Both keys are in the Key Registry (no warning), and a malformed value
    stops only the resolver, never load_config and so never a wake."""
    cfg = _write_cfg(tmp_path, "[testing]\nworkers = abc\ncoverage = on\n")
    result = _run(f"load_config {str(cfg)!r}", tmp_path)
    assert result.returncode == 0, result.stderr
    assert "WARNING" not in result.stderr, result.stderr
    assert "config_loader.testing" not in result.stderr, result.stderr


def test_resolvers_default_to_the_cfg_load_config_recorded(tmp_path: pathlib.Path) -> None:
    cfg = _write_cfg(tmp_path, "[testing]\nworkers = 4\ncoverage = true\n")
    result = _run(f"load_config {str(cfg)!r} && config_testing_workers && config_testing_coverage", tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["4", "true"]


def test_misspelled_testing_key_warns_with_nearest_known_key(tmp_path: pathlib.Path) -> None:
    cfg = _write_cfg(tmp_path, "[testing]\nworker = 3\n")
    result = _run(f"load_config {str(cfg)!r}", tmp_path)
    assert result.returncode == 0, result.stderr
    assert "[testing] worker" in result.stderr and "Nearest known key: workers." in result.stderr, result.stderr


# ---------------------------------------------------------------------------
# Shipped templates
# ---------------------------------------------------------------------------


def _section_lines(path: pathlib.Path, section: str) -> list[str]:
    lines: list[str] = []
    inside = False
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            inside = stripped == f"[{section}]"
            continue
        if inside:
            lines.append(stripped)
    return lines


def test_kanban_cfg_example_ships_testing_section_values(tmp_path: pathlib.Path) -> None:
    workers = _resolve("config_testing_workers", _KANBAN_CFG_EXAMPLE, tmp_path)
    coverage = _resolve("config_testing_coverage", _KANBAN_CFG_EXAMPLE, tmp_path)
    assert (workers.returncode, workers.stdout.strip()) == (0, "3"), workers.stderr
    assert (coverage.returncode, coverage.stdout.strip()) == (0, "false"), coverage.stderr


def test_kanban_cfg_example_documents_testing_keys_inline() -> None:
    """Each key's comment names its accepted values and its default, and
    workers states that 0 or 1 means serial."""
    text = "\n".join(_section_lines(_KANBAN_CFG_EXAMPLE, "testing"))
    workers_doc = text.split("workers = ", 1)[0]
    coverage_doc = text.split("workers = ", 1)[1].split("coverage = ", 1)[0]
    assert "0 or 1 means serial" in workers_doc
    assert "Accepted:" in workers_doc and "Default when absent or blank: 3" in workers_doc
    assert "Accepted: true|false" in coverage_doc and "Default: false" in coverage_doc


def test_project_cfg_example_has_no_testing_section() -> None:
    """[testing] is kanban-wide: no project.cfg key, no per-project override."""
    text = _PROJECT_CFG_EXAMPLE.read_text(encoding="utf-8")
    assert not re.search(r"^\s*#?\s*\[testing\]\s*$", text, re.M)


# ---------------------------------------------------------------------------
# [testing] pre_merge_affected_cap — the most affected test files a pre-merge
# run selects (default 60; 0 = no cap)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        pytest.param("40", "40", id="forty"),
        pytest.param("60", "60", id="the_default_value_set_explicitly"),
        pytest.param("1", "1", id="one"),
        pytest.param("0", "0", id="zero_no_cap"),
        pytest.param("040", "40", id="leading_zero_dropped"),
        pytest.param("00", "0", id="all_zeros_is_zero"),
        pytest.param("40 # a comment", "40", id="inline_comment_ignored"),
    ],
)
def test_pre_merge_cap_value_resolves_to_plain_decimal(tmp_path: pathlib.Path, raw: str, expected: str) -> None:
    cfg = _write_cfg(tmp_path, f"[testing]\npre_merge_affected_cap = {raw}\n")
    result = _resolve(_CAP_FUNC, cfg, tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected
    assert "absent" not in result.stderr, result.stderr


@pytest.mark.parametrize(
    "cfg_text",
    [
        pytest.param("[wake]\nmax_tasks_per_wake = 5\n", id="section_absent"),
        pytest.param("[testing]\nworkers = 3\n", id="key_absent"),
        pytest.param("[testing]\npre_merge_affected_cap =\n", id="value_blank"),
    ],
)
def test_pre_merge_cap_absent_or_blank_takes_default_60_narrated(tmp_path: pathlib.Path, cfg_text: str) -> None:
    cfg = _write_cfg(tmp_path, cfg_text)
    result = _resolve(_CAP_FUNC, cfg, tmp_path)
    assert (result.returncode, result.stdout.strip()) == (0, "60"), result.stderr
    assert (
        "config_testing_pre_merge_affected_cap: [testing] pre_merge_affected_cap absent; "
        "applying documented default '60'" in result.stderr
    ), result.stderr


def test_pre_merge_cap_missing_cfg_file_takes_default(tmp_path: pathlib.Path) -> None:
    result = _resolve(_CAP_FUNC, tmp_path / "no-such-kanban.cfg", tmp_path)
    assert (result.returncode, result.stdout.strip()) == (0, "60"), result.stderr


def test_pre_merge_cap_default_narration_is_written_once_per_process(tmp_path: pathlib.Path) -> None:
    cfg = _write_cfg(tmp_path, "[testing]\n")
    result = _run(f"{_CAP_FUNC} {str(cfg)!r} >/dev/null && {_CAP_FUNC} {str(cfg)!r}", tmp_path)
    assert (result.returncode, result.stdout.strip()) == (0, "60"), result.stderr
    assert result.stderr.count("[testing] pre_merge_affected_cap absent") == 1, result.stderr


def test_pre_merge_cap_in_another_section_is_not_read(tmp_path: pathlib.Path) -> None:
    """Only the [testing]-scoped value counts: a valid stray key elsewhere
    does not stand in for an absent one, and a malformed one is not refused."""
    absent = _write_cfg(tmp_path, "[wake]\npre_merge_affected_cap = 5\n[testing]\nworkers = 2\n", "absent.cfg")
    stray_bad = _write_cfg(
        tmp_path, "[release]\npre_merge_affected_cap = abc\n[testing]\npre_merge_affected_cap = 40\n", "bad.cfg"
    )
    result = _resolve(_CAP_FUNC, absent, tmp_path)
    assert (result.returncode, result.stdout.strip()) == (0, "60"), result.stderr
    result = _resolve(_CAP_FUNC, stray_bad, tmp_path)
    assert (result.returncode, result.stdout.strip()) == (0, "40"), result.stderr


def test_pre_merge_cap_does_not_change_workers_resolution(tmp_path: pathlib.Path) -> None:
    """The cap is a count of test files, separate from the worker budget."""
    cfg = _write_cfg(tmp_path, "[testing]\nworkers = 2\npre_merge_affected_cap = 40\n")
    result = _run(f"{_CAP_FUNC} {str(cfg)!r} && config_testing_workers {str(cfg)!r}", tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["40", "2"]


@pytest.mark.parametrize("raw", ["abc", "-5", "+5", "1.5", "40x", "sixty", "40 files"])
def test_malformed_pre_merge_cap_refuses_dual_channel(tmp_path: pathlib.Path, raw: str) -> None:
    cfg = _write_cfg(tmp_path, f"[testing]\npre_merge_affected_cap = {raw}\n")
    result = _resolve(_CAP_FUNC, cfg, tmp_path)
    assert result.returncode != 0, result.stdout
    assert result.stdout == "", "a refused value must print nothing a caller could use"
    fields = _contract(result.stderr)
    assert fields.get("error") == _CAP_ERROR, result.stderr
    assert f"'{raw}'" in fields["message"], fields["message"]
    assert "[testing] pre_merge_affected_cap" in fields["message"], fields["message"]
    assert "whole number in decimal digits" in fields["message"], "message must name the accepted form"
    assert str(cfg) in fields["remedy"], fields["remedy"]
    assert "<" not in fields["remedy"], "remedy must be runnable as printed"


@pytest.mark.parametrize(
    "raw, expected",
    [
        pytest.param("40x", "40", id="keeps_leading_digits"),
        pytest.param("-5", "60", id="negative_falls_back_to_default"),
        pytest.param("abc", "60", id="letters_fall_back_to_default"),
    ],
)
def test_pre_merge_cap_remedy_runs_as_printed_and_fixes_only_testing(
    tmp_path: pathlib.Path, raw: str, expected: str
) -> None:
    """The remedy repairs the [testing] key and leaves a same-named key in
    another section exactly as it was."""
    cfg = _write_cfg(
        tmp_path,
        f"[wake]\npre_merge_affected_cap = 7\n[testing]\npre_merge_affected_cap = {raw}\n"
        "[api]\npre_merge_affected_cap = 9\n",
    )
    refused = _resolve(_CAP_FUNC, cfg, tmp_path)
    assert refused.returncode != 0
    remedy = _contract(refused.stderr)["remedy"]
    applied = subprocess.run(["bash", "-c", remedy], capture_output=True, text=True, env=_env(tmp_path), timeout=10)
    assert applied.returncode == 0, applied.stderr
    fixed = _resolve(_CAP_FUNC, cfg, tmp_path)
    assert (fixed.returncode, fixed.stdout.strip()) == (0, expected), fixed.stderr
    assert _section_lines(cfg, "wake") == ["pre_merge_affected_cap = 7"]
    assert _section_lines(cfg, "api") == ["pre_merge_affected_cap = 9"]


def test_workers_and_coverage_remedies_run_as_printed_and_fix_only_testing(tmp_path: pathlib.Path) -> None:
    """Both remedies repair their [testing] key and leave the same-named
    workers and coverage keys in every other section byte-identical."""
    cfg = _write_cfg(
        tmp_path,
        "[api]\nworkers = 4\ncoverage = yes\n\n[testing]\nworkers = abc\ncoverage = on\n\n"
        "[wake]\nworkers = 2x\ncoverage = maybe\n",
    )
    for func in ("config_testing_workers", "config_testing_coverage"):
        refused = _resolve(func, cfg, tmp_path)
        assert refused.returncode != 0, refused.stderr
        remedy = _contract(refused.stderr)["remedy"]
        applied = subprocess.run(["bash", "-c", remedy], capture_output=True, text=True, env=_env(tmp_path), timeout=10)
        assert applied.returncode == 0, applied.stderr

    workers = _resolve("config_testing_workers", cfg, tmp_path)
    coverage = _resolve("config_testing_coverage", cfg, tmp_path)
    assert (workers.returncode, workers.stdout.strip()) == (0, "3"), workers.stderr
    assert (coverage.returncode, coverage.stdout.strip()) == (0, "false"), coverage.stderr
    assert _section_lines(cfg, "testing") == ["workers = 3", "coverage = false", ""]
    assert _section_lines(cfg, "api") == ["workers = 4", "coverage = yes", ""]
    assert _section_lines(cfg, "wake") == ["workers = 2x", "coverage = maybe"]


def test_load_config_knows_pre_merge_cap_and_does_not_refuse_its_value(tmp_path: pathlib.Path) -> None:
    cfg = _write_cfg(tmp_path, "[testing]\npre_merge_affected_cap = abc\n")
    result = _run(f"load_config {str(cfg)!r}", tmp_path)
    assert result.returncode == 0, result.stderr
    assert "WARNING" not in result.stderr, result.stderr
    assert "config_loader.testing" not in result.stderr, result.stderr


def test_pre_merge_cap_defaults_to_the_cfg_load_config_recorded(tmp_path: pathlib.Path) -> None:
    cfg = _write_cfg(tmp_path, "[testing]\npre_merge_affected_cap = 25\n")
    result = _run(f"load_config {str(cfg)!r} && {_CAP_FUNC}", tmp_path)
    assert (result.returncode, result.stdout.strip()) == (0, "25"), result.stderr


def test_kanban_cfg_example_ships_and_documents_pre_merge_cap(tmp_path: pathlib.Path) -> None:
    """The shipped value is the default 60, and its comment names the
    meaning, the accepted values, what 0 means and the default."""
    result = _resolve(_CAP_FUNC, _KANBAN_CFG_EXAMPLE, tmp_path)
    assert (result.returncode, result.stdout.strip()) == (0, "60"), result.stderr
    text = "\n".join(_section_lines(_KANBAN_CFG_EXAMPLE, "testing"))
    cap_block = text.split("coverage = ", 1)[1].split("pre_merge_affected_cap = ", 1)[0]
    # Read the comment as prose, so re-wrapping its lines never moves the verdict.
    cap_doc = " ".join(line.lstrip("#").strip() for line in cap_block.splitlines() if line.strip())
    assert "most affected test files a pre-merge run" in cap_doc
    assert "0 means no cap" in cap_doc
    assert "Accepted: a non-negative whole number in decimal digits" in cap_doc
    assert "Default when absent or blank: 60" in cap_doc
