"""
test_config_loader_key_validation.py
====================================
Unit tests for the whole-file validation team/scripts/lib/config_loader.sh's
load_config() performs before it reads a single value:

  - Every [wake] integer key must be decimal digits only. A unit suffix
    ("300s"), stray text, or a non-numeric environment override refuses
    dual-channel (stable error key, message naming the key and value,
    runnable remedy) so no wake reaches bash arithmetic with a non-number.
    A blank value reads as absent and takes the key's default.
  - A key the Key Registry does not list under a known section, or a
    section it does not list at all, produces one WARNING naming the
    nearest known key or section — and never refuses.
  - The shipped kanban.cfg_example loads with no such warning, and the
    human-readable Key Registry comment agrees with its machine-readable
    twin _CONFIG_LOADER_KNOWN_KEYS, so neither can drift from the other
    unnoticed.

Tests invoke config_loader.sh via subprocess (bash -c "source ... && ..."),
matching test_config_loader_release_posture_keys.py, so the fixtures
exercise the real production shell code rather than a reimplementation.
The subprocess is given a UTF-8 locale: the loader's one-character check on
dashboard_status_glyph counts with wc -m, which counts bytes under the C
locale and would refuse the default glyph.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess

import pytest

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_REPO_ROOT = _TEAM_DIR.parent
_CONFIG_LOADER = _TEAM_DIR / "scripts" / "lib" / "config_loader.sh"
_CFG_EXAMPLE = _REPO_ROOT / "kanban.cfg_example"

_ERROR_KEY = "config_loader.wake.not_an_integer"


def _run_bash(script: str, env: dict[str, str], timeout: int = 20) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


def _base_env(**overrides: str) -> dict[str, str]:
    """The real environment (PATH, temp dir) with any [wake] env overrides
    cleared so a test's own values are the only ones the loader sees."""
    env = dict(os.environ)
    for name in (
        "MAX_TASKS_PER_WAKE",
        "MAX_RUNTIME_SECONDS",
        "PAUSE_BETWEEN_TASKS",
        "PM_MAX_TASKS",
        "MAX_TASK_SECONDS",
        "MAX_PROJECT_SECONDS",
        "KILL_GRACE_SECONDS",
        "TRANSIENT_RETRY_CAP",
        "ZERO_PROGRESS_CAP",
        "BLOCK_STORM_WINDOW",
        "BLOCK_HOLD_CAP",
        "SYSTEMIC_BLOCK_REASONS",
        "LOCK_WAIT_SECONDS",
        "LOCK_STARVATION_FIRINGS",
        "USAGE_LIMIT_PAUSE_SECONDS",
        "DASHBOARD_STATUS_GLYPH",
    ):
        env.pop(name, None)
    env["LC_ALL"] = "C.UTF-8"
    env.update(overrides)
    return env


def _write_cfg(path: pathlib.Path, text: str) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _load_config(cfg_file: pathlib.Path, *export_vars: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Run load_config against cfg_file, echoing each named exported variable
    as NAME=[value] on success so a test can assert the resolved value."""
    script = f"source {str(_CONFIG_LOADER)!r} && load_config {str(cfg_file)!r}"
    for var in export_vars:
        script += f' && echo "{var}=[${{{var}:-}}]"'
    return _run_bash(script, env or _base_env())


def _contract(stderr: str) -> dict[str, str]:
    """The three dual-channel fields from a refusal's stderr."""
    fields: dict[str, str] = {}
    for line in stderr.splitlines():
        m = re.match(r"^(error|message|remedy):\s+(.*)$", line)
        if m:
            fields[m.group(1)] = m.group(2)
    return fields


# ---------------------------------------------------------------------------
# [wake] integer keys refuse anything but digits
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key, value",
    [
        ("max_runtime_seconds", "300s"),
        ("max_tasks_per_wake", "five"),
        ("pause_between_tasks", "-5"),
        ("pm_max_tasks", "1.5"),
        ("max_task_seconds", "90m"),
        ("max_project_seconds", "4h"),
        ("kill_grace_seconds", "30 seconds"),
        ("transient_retry_cap", "3x"),
        ("zero_progress_cap", "three"),
        ("block_storm_window", "1h"),
        ("block_hold_cap", "60m"),
        ("usage_limit_pause_seconds", "1h"),
        ("lock_wait_seconds", "90m"),
        ("lock_starvation_firings", "three"),
    ],
)
def test_non_numeric_wake_key_refuses_dual_channel(tmp_path: pathlib.Path, key: str, value: str) -> None:
    """Every [wake] integer key refuses a non-digit value with the stable
    error key, a message naming key and value, and a remedy naming the file."""
    cfg = _write_cfg(tmp_path / "kanban.cfg", f"[wake]\n{key} = {value}\n")
    result = _load_config(cfg)
    assert result.returncode != 0, result.stderr
    fields = _contract(result.stderr)
    assert fields.get("error") == _ERROR_KEY, result.stderr
    assert key in fields["message"] and f"'{value}'" in fields["message"], fields["message"]
    assert str(cfg) in fields["remedy"] and key in fields["remedy"], fields["remedy"]
    assert "<" not in fields["remedy"], "remedy must be runnable as printed"


def test_refusal_remedy_keeps_the_digits_the_value_started_with(tmp_path: pathlib.Path) -> None:
    """'300s' suggests 300 in the remedy; a value with no leading digits
    suggests the key's default."""
    cfg = _write_cfg(tmp_path / "kanban.cfg", "[wake]\nmax_runtime_seconds = 300s\n")
    fields = _contract(_load_config(cfg).stderr)
    assert "max_runtime_seconds = 300/" in fields["remedy"], fields["remedy"]

    cfg = _write_cfg(tmp_path / "kanban2.cfg", "[wake]\nmax_runtime_seconds = six-hours\n")
    fields = _contract(_load_config(cfg).stderr)
    assert "max_runtime_seconds = 14400/" in fields["remedy"], fields["remedy"]


def test_refusal_remedy_is_runnable_and_fixes_the_file(tmp_path: pathlib.Path) -> None:
    """Pasting the printed remedy into a shell makes the same config load."""
    cfg = _write_cfg(tmp_path / "kanban.cfg", "[wake]\n  max_runtime_seconds   =  300s\n")
    fields = _contract(_load_config(cfg).stderr)
    fixed = _run_bash(fields["remedy"], _base_env())
    assert fixed.returncode == 0, fixed.stderr
    result = _load_config(cfg, "MAX_RUNTIME_SECONDS")
    assert result.returncode == 0, result.stderr
    assert "MAX_RUNTIME_SECONDS=[300]" in result.stdout


def test_non_numeric_environment_override_refuses(tmp_path: pathlib.Path) -> None:
    """An env override wins over the cfg value and is validated the same way;
    the remedy re-exports the variable rather than editing the file."""
    cfg = _write_cfg(tmp_path / "kanban.cfg", "[wake]\nmax_tasks_per_wake = 5\n")
    result = _load_config(cfg, env=_base_env(MAX_TASKS_PER_WAKE="abc"))
    assert result.returncode != 0
    fields = _contract(result.stderr)
    assert fields.get("error") == _ERROR_KEY
    assert "MAX_TASKS_PER_WAKE" in fields["message"] and "'abc'" in fields["message"]
    assert fields["remedy"] == "export MAX_TASKS_PER_WAKE=5", fields["remedy"]


def test_inline_comment_and_crlf_no_longer_reach_the_integer_check(tmp_path: pathlib.Path) -> None:
    """The bug's reproduction ('300 # five min') and a CRLF file both load
    with the numeric value alone."""
    cfg = _write_cfg(tmp_path / "kanban.cfg", "[wake]\nmax_runtime_seconds = 300 # five min\n")
    result = _load_config(cfg, "MAX_RUNTIME_SECONDS")
    assert result.returncode == 0, result.stderr
    assert "MAX_RUNTIME_SECONDS=[300]" in result.stdout

    crlf = tmp_path / "crlf.cfg"
    crlf.write_bytes(b"[wake]\r\nmax_runtime_seconds = 21600\r\nmax_tasks_per_wake = 2\r\n")
    result = _load_config(crlf, "MAX_RUNTIME_SECONDS", "MAX_TASKS_PER_WAKE", "MAX_PROJECT_SECONDS")
    assert result.returncode == 0, result.stderr
    assert "MAX_RUNTIME_SECONDS=[21600]" in result.stdout
    assert "MAX_TASKS_PER_WAKE=[2]" in result.stdout
    assert "MAX_PROJECT_SECONDS=[21600]" in result.stdout, "project cap defaults to the batch cap"


def test_blank_integer_value_takes_the_default(tmp_path: pathlib.Path) -> None:
    """'max_tasks_per_wake =' with nothing after it reads as absent."""
    cfg = _write_cfg(tmp_path / "kanban.cfg", "[wake]\nmax_tasks_per_wake =\n")
    result = _load_config(cfg, "MAX_TASKS_PER_WAKE")
    assert result.returncode == 0, result.stderr
    assert "MAX_TASKS_PER_WAKE=[5]" in result.stdout


def test_block_storm_window_absent_exports_the_default(tmp_path: pathlib.Path) -> None:
    """With [wake] block_storm_window absent, BLOCK_STORM_WINDOW is 3600."""
    cfg = _write_cfg(tmp_path / "kanban.cfg", "[wake]\nzero_progress_cap = 3\n")
    result = _load_config(cfg, "BLOCK_STORM_WINDOW")
    assert result.returncode == 0, result.stderr
    assert "BLOCK_STORM_WINDOW=[3600]" in result.stdout


def test_block_storm_window_set_value_is_honoured(tmp_path: pathlib.Path) -> None:
    """A configured whole-seconds value is exported as given."""
    cfg = _write_cfg(tmp_path / "kanban.cfg", "[wake]\nblock_storm_window = 900\n")
    result = _load_config(cfg, "BLOCK_STORM_WINDOW")
    assert result.returncode == 0, result.stderr
    assert "BLOCK_STORM_WINDOW=[900]" in result.stdout


def test_block_hold_cap_absent_exports_the_default(tmp_path: pathlib.Path) -> None:
    """With [wake] block_hold_cap absent, BLOCK_HOLD_CAP is 3600."""
    cfg = _write_cfg(tmp_path / "kanban.cfg", "[wake]\nzero_progress_cap = 3\n")
    result = _load_config(cfg, "BLOCK_HOLD_CAP")
    assert result.returncode == 0, result.stderr
    assert "BLOCK_HOLD_CAP=[3600]" in result.stdout


def test_block_hold_cap_set_value_is_honoured(tmp_path: pathlib.Path) -> None:
    """A configured whole-seconds cap is exported as given."""
    cfg = _write_cfg(tmp_path / "kanban.cfg", "[wake]\nblock_hold_cap = 1800\n")
    result = _load_config(cfg, "BLOCK_HOLD_CAP")
    assert result.returncode == 0, result.stderr
    assert "BLOCK_HOLD_CAP=[1800]" in result.stdout


def test_usage_limit_pause_seconds_absent_exports_the_default(tmp_path: pathlib.Path) -> None:
    """With [wake] usage_limit_pause_seconds absent, USAGE_LIMIT_PAUSE_SECONDS is 3600."""
    cfg = _write_cfg(tmp_path / "kanban.cfg", "[wake]\nzero_progress_cap = 3\n")
    result = _load_config(cfg, "USAGE_LIMIT_PAUSE_SECONDS")
    assert result.returncode == 0, result.stderr
    assert "USAGE_LIMIT_PAUSE_SECONDS=[3600]" in result.stdout


def test_usage_limit_pause_seconds_set_value_is_honoured(tmp_path: pathlib.Path) -> None:
    cfg = _write_cfg(tmp_path / "kanban.cfg", "[wake]\nusage_limit_pause_seconds = 900\n")
    result = _load_config(cfg, "USAGE_LIMIT_PAUSE_SECONDS")
    assert result.returncode == 0, result.stderr
    assert "USAGE_LIMIT_PAUSE_SECONDS=[900]" in result.stdout


def test_block_storm_window_environment_override_wins(tmp_path: pathlib.Path) -> None:
    """An already-exported BLOCK_STORM_WINDOW outranks the cfg value, the
    same precedence every other [wake] integer follows."""
    cfg = _write_cfg(tmp_path / "kanban.cfg", "[wake]\nblock_storm_window = 900\n")
    result = _load_config(cfg, "BLOCK_STORM_WINDOW", env=_base_env(BLOCK_STORM_WINDOW="120"))
    assert result.returncode == 0, result.stderr
    assert "BLOCK_STORM_WINDOW=[120]" in result.stdout


@pytest.mark.parametrize(
    "key, env_name, default",
    [
        ("lock_wait_seconds", "LOCK_WAIT_SECONDS", "120"),
        ("lock_starvation_firings", "LOCK_STARVATION_FIRINGS", "3"),
    ],
)
def test_lock_wait_keys_absent_or_empty_take_their_defaults(
    tmp_path: pathlib.Path, key: str, env_name: str, default: str
) -> None:
    """An absent key and an empty value both resolve to the key's default."""
    absent = _write_cfg(tmp_path / "absent.cfg", "[wake]\nmax_tasks_per_wake = 5\n")
    result = _load_config(absent, env_name)
    assert result.returncode == 0, result.stderr
    assert f"{env_name}=[{default}]" in result.stdout

    empty = _write_cfg(tmp_path / "empty.cfg", f"[wake]\n{key} =\n")
    result = _load_config(empty, env_name)
    assert result.returncode == 0, result.stderr
    assert f"{env_name}=[{default}]" in result.stdout
    assert "WARNING" not in result.stderr, result.stderr


def test_lock_wait_keys_set_values_are_honoured(tmp_path: pathlib.Path) -> None:
    """Configured whole numbers, 0 included, are exported as given."""
    cfg = _write_cfg(
        tmp_path / "kanban.cfg", "[wake]\nlock_wait_seconds = 0\nlock_starvation_firings = 5\n"
    )
    result = _load_config(cfg, "LOCK_WAIT_SECONDS", "LOCK_STARVATION_FIRINGS")
    assert result.returncode == 0, result.stderr
    assert "LOCK_WAIT_SECONDS=[0]" in result.stdout
    assert "LOCK_STARVATION_FIRINGS=[5]" in result.stdout


def test_lock_wait_keys_environment_override_wins(tmp_path: pathlib.Path) -> None:
    """An already-exported LOCK_WAIT_SECONDS / LOCK_STARVATION_FIRINGS
    outranks the cfg value -- the operator override
    (`LOCK_WAIT_SECONDS=45 wake-batch.sh ...`) -- even over a cfg that pins
    lock_wait_seconds = 0."""
    cfg = _write_cfg(
        tmp_path / "kanban.cfg", "[wake]\nlock_wait_seconds = 0\nlock_starvation_firings = 5\n"
    )
    env = _base_env(LOCK_WAIT_SECONDS="45", LOCK_STARVATION_FIRINGS="7")
    result = _load_config(cfg, "LOCK_WAIT_SECONDS", "LOCK_STARVATION_FIRINGS", env=env)
    assert result.returncode == 0, result.stderr
    assert "LOCK_WAIT_SECONDS=[45]" in result.stdout
    assert "LOCK_STARVATION_FIRINGS=[7]" in result.stdout


def test_lock_wait_seconds_unit_suffix_refuses_with_the_digits_as_remedy(tmp_path: pathlib.Path) -> None:
    """'90m' refuses at load and the remedy rewrites the line to 90."""
    cfg = _write_cfg(tmp_path / "kanban.cfg", "[wake]\nlock_wait_seconds = 90m\n")
    result = _load_config(cfg, "LOCK_WAIT_SECONDS")
    assert result.returncode != 0
    assert "LOCK_WAIT_SECONDS=" not in result.stdout
    fields = _contract(result.stderr)
    assert fields.get("error") == _ERROR_KEY, result.stderr
    assert "lock_wait_seconds = 90/" in fields["remedy"], fields["remedy"]


def test_systemic_block_reasons_absent_exports_blank_for_the_reader_default(tmp_path: pathlib.Path) -> None:
    """With [wake] systemic_block_reasons absent, SYSTEMIC_BLOCK_REASONS is
    exported blank: the reader (pgai_agent_kanban.overwatch.block_storm)
    owns the default list, so the loader never carries a second copy."""
    cfg = _write_cfg(tmp_path / "kanban.cfg", "[wake]\nblock_storm_window = 3600\n")
    result = _load_config(cfg, "SYSTEMIC_BLOCK_REASONS")
    assert result.returncode == 0, result.stderr
    assert "SYSTEMIC_BLOCK_REASONS=[]" in result.stdout
    assert "systemic_block_reasons" not in result.stderr


def test_systemic_block_reasons_set_value_is_exported_as_written(tmp_path: pathlib.Path) -> None:
    """A configured list, unknown members included, is exported as written
    and never refused: the reader warns about and drops what it does not
    know, so a typo cannot stop a wake."""
    cfg = _write_cfg(
        tmp_path / "kanban.cfg", "[wake]\nsystemic_block_reasons = plugin_load_failed, not_a_code\n"
    )
    result = _load_config(cfg, "SYSTEMIC_BLOCK_REASONS")
    assert result.returncode == 0, result.stderr
    assert "SYSTEMIC_BLOCK_REASONS=[plugin_load_failed, not_a_code]" in result.stdout


def test_systemic_block_reasons_environment_override_wins(tmp_path: pathlib.Path) -> None:
    """An already-exported SYSTEMIC_BLOCK_REASONS outranks the cfg value."""
    cfg = _write_cfg(tmp_path / "kanban.cfg", "[wake]\nsystemic_block_reasons = plugin_load_failed\n")
    result = _load_config(
        cfg, "SYSTEMIC_BLOCK_REASONS", env=_base_env(SYSTEMIC_BLOCK_REASONS="none")
    )
    assert result.returncode == 0, result.stderr
    assert "SYSTEMIC_BLOCK_REASONS=[none]" in result.stdout


def test_overwatch_block_triage_is_a_known_key(tmp_path: pathlib.Path) -> None:
    """[overwatch] block_triage loads with no unknown-section or
    unknown-key warning; a misspelling of it warns naming the real key."""
    cfg = _write_cfg(tmp_path / "kanban.cfg", "[overwatch]\nblock_triage = false\n")
    result = _load_config(cfg)
    assert result.returncode == 0, result.stderr
    assert "WARNING" not in result.stderr, result.stderr

    cfg = _write_cfg(tmp_path / "kanban2.cfg", "[overwatch]\nblock_triag = false\n")
    result = _load_config(cfg)
    assert result.returncode == 0, result.stderr
    assert "Nearest known key: block_triage" in result.stderr, result.stderr


def test_overwatch_requeue_budget_is_a_known_key(tmp_path: pathlib.Path) -> None:
    """[overwatch] requeue_budget loads with no unknown-section or
    unknown-key warning; a misspelling of it warns naming the real key."""
    cfg = _write_cfg(tmp_path / "kanban.cfg", "[overwatch]\nrequeue_budget = 3\n")
    result = _load_config(cfg)
    assert result.returncode == 0, result.stderr
    assert "WARNING" not in result.stderr, result.stderr

    cfg = _write_cfg(tmp_path / "kanban2.cfg", "[overwatch]\nrequeue_budgt = 3\n")
    result = _load_config(cfg)
    assert result.returncode == 0, result.stderr
    assert "Nearest known key: requeue_budget" in result.stderr, result.stderr


def test_archive_count_is_a_known_key(tmp_path: pathlib.Path) -> None:
    """[archive] archive_count loads with no unknown-section or unknown-key
    warning; a misspelling of it warns naming the real key."""
    cfg = _write_cfg(tmp_path / "kanban.cfg", "[archive]\narchive_count = 55\n")
    result = _load_config(cfg)
    assert result.returncode == 0, result.stderr
    assert "WARNING" not in result.stderr, result.stderr

    cfg = _write_cfg(tmp_path / "kanban2.cfg", "[archive]\narchive_cnt = 55\n")
    result = _load_config(cfg)
    assert result.returncode == 0, result.stderr
    assert "Nearest known key: archive_count" in result.stderr, result.stderr


def test_release_ship_posture_keys_are_known_keys(tmp_path: pathlib.Path) -> None:
    """[release] ship_posture and ship_posture_confirm_remote load with no
    unknown-key warning; a misspelling of either warns naming the real key."""
    cfg = _write_cfg(
        tmp_path / "kanban.cfg",
        "[release]\nship_posture = always\nship_posture_confirm_remote = true\n",
    )
    result = _load_config(cfg)
    assert result.returncode == 0, result.stderr
    assert "WARNING" not in result.stderr, result.stderr

    cfg = _write_cfg(tmp_path / "kanban2.cfg", "[release]\nship_postur = always\n")
    result = _load_config(cfg)
    assert result.returncode == 0, result.stderr
    assert "Nearest known key: ship_posture" in result.stderr, result.stderr

    cfg = _write_cfg(tmp_path / "kanban3.cfg", "[release]\nship_posture_confirm_remot = true\n")
    result = _load_config(cfg)
    assert result.returncode == 0, result.stderr
    assert "Nearest known key: ship_posture_confirm_remote" in result.stderr, result.stderr


# ---------------------------------------------------------------------------
# Unknown keys and sections warn, never refuse
# ---------------------------------------------------------------------------


def test_misspelled_key_warns_with_nearest_known_key_and_loads(tmp_path: pathlib.Path) -> None:
    cfg = _write_cfg(tmp_path / "kanban.cfg", "[wake]\nmax_task_per_wake = 3\n")
    result = _load_config(cfg, "MAX_TASKS_PER_WAKE")
    assert result.returncode == 0, result.stderr
    warnings = [l for l in result.stderr.splitlines() if "WARNING" in l]
    assert len(warnings) == 1, result.stderr
    assert "[wake] max_task_per_wake" in warnings[0]
    assert "Nearest known key: max_tasks_per_wake" in warnings[0]
    assert "MAX_TASKS_PER_WAKE=[5]" in result.stdout, "the misspelled key is ignored, default applies"


def test_unknown_section_warns_once_with_nearest_known_section(tmp_path: pathlib.Path) -> None:
    cfg = _write_cfg(tmp_path / "kanban.cfg", "[wak]\nmax_tasks_per_wake = 3\nzero_progress_cap = 1\n")
    result = _load_config(cfg)
    assert result.returncode == 0, result.stderr
    warnings = [l for l in result.stderr.splitlines() if "WARNING" in l]
    assert len(warnings) == 1, "keys under an unknown section are covered by the section warning"
    assert "section [wak]" in warnings[0] and "Nearest known section: [wake]" in warnings[0]


def test_key_above_first_section_warns(tmp_path: pathlib.Path) -> None:
    cfg = _write_cfg(tmp_path / "kanban.cfg", "max_tasks_per_wake = 3\n[wake]\n")
    result = _load_config(cfg)
    assert result.returncode == 0, result.stderr
    assert any("above the first section header" in l for l in result.stderr.splitlines()), result.stderr


def test_known_keys_produce_no_warning(tmp_path: pathlib.Path) -> None:
    cfg = _write_cfg(
        tmp_path / "kanban.cfg",
        "[paths]\ndev_tree_path = /x\n[wake]\nmax_tasks_per_wake = 3\n[models.claude]\ncoder = m\n[release]\ndeep_verify = off\n",
    )
    result = _load_config(cfg)
    assert result.returncode == 0, result.stderr
    assert "WARNING" not in result.stderr, result.stderr


def test_keys_nothing_reads_are_narrated_not_silently_accepted(tmp_path: pathlib.Path) -> None:
    """[chain] cron_stagger_minutes and the [models.gemini] section have no
    reader, so a cfg still carrying them draws a warning for each and still
    loads; the provider sections that are read stay known."""
    cfg = _write_cfg(
        tmp_path / "kanban.cfg",
        "[chain]\npm_mode = automatic\ncron_stagger_minutes = 2\n"
        "[models.claude]\ncoder = m\n[models.gemini]\ndefault = g\ncoder = g\n",
    )
    result = _load_config(cfg)
    assert result.returncode == 0, result.stderr
    warnings = [l for l in result.stderr.splitlines() if "WARNING" in l]
    assert len(warnings) == 2, result.stderr
    assert any("[chain] cron_stagger_minutes" in w and "is not a known kanban.cfg key" in w for w in warnings), warnings
    assert any("section [models.gemini]" in w and "is not a known kanban.cfg section" in w for w in warnings), warnings


# ---------------------------------------------------------------------------
# Registry drift guards
# ---------------------------------------------------------------------------


def test_shipped_cfg_example_loads_without_unknown_entry_warning(tmp_path: pathlib.Path) -> None:
    """Every section and key in kanban.cfg_example is in the Key Registry."""
    assert _CFG_EXAMPLE.is_file(), _CFG_EXAMPLE
    result = _load_config(_CFG_EXAMPLE, env=_base_env(PGAI_AGENT_KANBAN_TEMP_DIR=str(tmp_path / "tmp")))
    assert result.returncode == 0, result.stderr
    assert "WARNING" not in result.stderr, result.stderr


def _known_keys_from_array(source: str) -> set[str]:
    m = re.search(r"^_CONFIG_LOADER_KNOWN_KEYS=\(\n(.*?)^\)", source, re.S | re.M)
    assert m, "_CONFIG_LOADER_KNOWN_KEYS array not found"
    return set(m.group(1).split())


def _known_keys_from_registry_comment(source: str) -> set[str]:
    """'section:key' for every '#   key  REQUIRED|OPTIONAL' line under a
    '# [section]' heading in the Key Registry comment block."""
    block = source.split("# Key Registry", 1)[1].split("# Bootstrap:", 1)[0]
    found: set[str] = set()
    section = ""
    for line in block.splitlines():
        head = re.match(r"^# \[([a-z][a-z0-9_.]*)\]", line)
        if head:
            section = head.group(1)
            continue
        key = re.match(r"^#   ([a-z][a-z0-9_]*)\s+(?:REQUIRED|OPTIONAL)\b", line)
        if key and section:
            found.add(f"{section}:{key.group(1)}")
    return found


def test_registry_comment_and_known_keys_array_agree() -> None:
    """A key added to the comment block but not the array (or vice versa)
    is a drift the loader would warn about, or silently accept, wrongly."""
    source = _CONFIG_LOADER.read_text(encoding="utf-8")
    array_keys = _known_keys_from_array(source)
    comment_keys = _known_keys_from_registry_comment(source)
    assert comment_keys, "registry comment parsed to nothing; its format changed"
    missing_from_array = sorted(comment_keys - array_keys)
    assert not missing_from_array, f"in the Key Registry comment but not _CONFIG_LOADER_KNOWN_KEYS: {missing_from_array}"
    # The models.* sections list their roles in prose, not one per line, so
    # only keys the comment states one-per-line are checked in this direction.
    unexplained = sorted(k for k in array_keys - comment_keys if not k.startswith("models."))
    assert not unexplained, f"in _CONFIG_LOADER_KNOWN_KEYS but not the Key Registry comment: {unexplained}"
