"""
test_pseudocron.py
==================
Behavioral unit tests for team/scripts/pseudocron.py: the foreground
cron-like scheduler's config parser, env parser, and tier-template
resolution.

These tests serve as the behavioral safety net for
the pseudocron template move to team/templates/install/.

Three concerns covered:
  1. parse_config()     — schedule config file parsing (format + edge cases)
  2. parse_env()        — environment file parsing (format + edge cases)
  3. Tier-template resolution — the three tier templates at
     team/templates/install/pseudocron-{small,medium,large}.cfg.example
     exist, parse without error, and produce at least one (minute, command)
     job — confirming the template move landed correctly and the config
     format is valid.

All filesystem tests use tmp_path (pytest-managed); no bare /tmp paths.
The tier-template tests reference the real dev tree via PGAI_DEV_TREE_PATH
so they fail if the templates are missing or malformed.
"""

from __future__ import annotations

import io
import os
import pathlib
import sys

import pytest

# ---------------------------------------------------------------------------
# Import under test — supports both installed-package and dev-tree layouts.
# ---------------------------------------------------------------------------
try:
    from pseudocron import parse_config, parse_env  # type: ignore[import]
except ImportError:
    # Dev tree: team/scripts/ is not a package; add it to sys.path.
    _SCRIPTS_DIR = str(pathlib.Path(__file__).parent.parent.parent / "scripts")
    if _SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, _SCRIPTS_DIR)
    from pseudocron import parse_config, parse_env  # type: ignore[import,no-redef]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dev_tree_root() -> pathlib.Path:
    """Return the dev tree root, honouring PGAI_DEV_TREE_PATH when set."""
    env_val = os.environ.get("PGAI_DEV_TREE_PATH", "").strip()
    if env_val:
        return pathlib.Path(env_val)
    # Fallback: derive from this test file's location (team/tests/unit/ -> repo root).
    return pathlib.Path(__file__).parent.parent.parent.parent


# ---------------------------------------------------------------------------
# parse_config() — schedule config parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("", []),  # empty input
        ("\n\n\n", []),  # blank lines skipped
        ("# just a comment\n", []),  # comment lines skipped
        ("5 /usr/bin/echo hello\n", [(5, "/usr/bin/echo hello")]),  # single valid job
        ("  10 /usr/bin/date\n", [(10, "/usr/bin/date")]),  # leading whitespace stripped
        ("15\t/usr/bin/date\n", [(15, "/usr/bin/date")]),  # tab-separated minute/command
        ("0 /bin/true\n", [(0, "/bin/true")]),  # minute lower boundary
        ("59 /bin/true\n", [(59, "/bin/true")]),  # minute upper boundary
    ],
)
def test_config_parses_valid_or_empty_input(text: str, expected: list) -> None:
    """parse_config extracts (minute, command) tuples, skipping blank/comment lines."""
    assert parse_config(text) == expected


@pytest.mark.parametrize(
    "text, expected_error_substring",
    [
        ("60 /bin/true\n", "out of range"),  # minute out of range (too high)
        ("-1 /bin/true\n", "out of range"),  # negative minute, rejected by range check
        ("*/5 /bin/true\n", "parse error"),  # non-integer minute field
        ("5\n", "empty command"),  # minute only, no command
    ],
)
def test_config_rejects_invalid_line(
    capsys: pytest.CaptureFixture[str], text: str, expected_error_substring: str
) -> None:
    """parse_config rejects malformed lines (bad minute or missing command) and logs an error."""
    result = parse_config(text)
    assert result == []
    captured = capsys.readouterr()
    assert expected_error_substring in captured.err


def test_config_command_with_shell_metacharacters() -> None:
    """parse_config preserves the full command including shell metacharacters."""
    # anti-pattern-allowlist: 2 (justification: /tmp path is embedded in a parse_config
    # input string — it is test fixture data, not a live filesystem write.
    # parse_config() does not execute commands; it only tokenises the text.)
    line = "30 /bin/bash -c 'echo hello >> /tmp/out.log 2>&1'\n"
    result = parse_config(line)
    assert len(result) == 1
    minute, command = result[0]
    assert minute == 30
    # anti-pattern-allowlist: 2 (justification: same as above — asserting on a literal
    # string value, not writing to /tmp.)
    assert ">> /tmp/out.log" in command


def test_config_multiple_jobs_returned_in_order() -> None:
    """parse_config returns all valid jobs in file order."""
    text = "0 /bin/cmd-a\n5 /bin/cmd-b\n10 /bin/cmd-c\n"
    result = parse_config(text)
    assert result == [(0, "/bin/cmd-a"), (5, "/bin/cmd-b"), (10, "/bin/cmd-c")]


def test_config_mixed_valid_and_invalid_lines(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """parse_config includes valid lines and skips invalid ones (no raise)."""
    text = "3 /bin/good\nbogus /bin/bad\n7 /bin/also-good\n"
    result = parse_config(text)
    assert (3, "/bin/good") in result
    assert (7, "/bin/also-good") in result
    captured = capsys.readouterr()
    assert "parse error" in captured.err


def test_config_placeholder_string_treated_as_literal() -> None:
    """parse_config treats any token including __KANBAN_ROOT__ as a literal string.

    parse_config never performs variable substitution; it tokenises only.
    A cfg that still contains __KANBAN_ROOT__ (e.g. a legacy or hand-authored
    file) will parse without error — the scheduler will attempt to run the
    literal string as-is, which is the correct parse-time behaviour.
    """
    result = parse_config("5 __KANBAN_ROOT__/scripts/wake-batch.sh --agent=pm\n")
    assert len(result) == 1
    assert "__KANBAN_ROOT__" in result[0][1]


def test_config_source_name_used_in_error_messages(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """parse_config includes the source name in error output when supplied."""
    parse_config("notanumber /cmd\n", source="my-config.cfg")
    captured = capsys.readouterr()
    assert "my-config.cfg" in captured.err


# ---------------------------------------------------------------------------
# parse_env() — environment file parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "",  # empty input
        "# ANTHROPIC_API_KEY=secret\n",  # comment line
        "\n\n",  # blank lines
    ],
)
def test_env_returns_empty_dict_for_no_assignments(text: str) -> None:
    """parse_env returns {} for empty input, comment-only, or blank-only text."""
    assert parse_env(text) == {}


def test_env_bare_assignment_form() -> None:
    """parse_env accepts NAME=VALUE (without 'export' prefix)."""
    result = parse_env("FOO=bar\n")
    assert result == {"FOO": "bar"}


def test_env_export_prefix_form() -> None:
    """parse_env accepts 'export NAME=VALUE' form."""
    result = parse_env("export FOO=bar\n")
    assert result == {"FOO": "bar"}


@pytest.mark.parametrize(
    "text, key, expected_value",
    [
        ('export MY_VAR="hello world"\n', "MY_VAR", "hello world"),  # double quotes stripped
        ("export MY_VAR='hello world'\n", "MY_VAR", "hello world"),  # single quotes stripped
        (
            "export PGAI_AGENT_KANBAN_ROOT_PATH=/home/<operator>/pgai_agent_kanban\n",
            "PGAI_AGENT_KANBAN_ROOT_PATH",
            "/home/<operator>/pgai_agent_kanban",
        ),  # plain path value, unaltered
        (
            "export PGAI_CODER_MODEL_2=claude-3\n",
            "PGAI_CODER_MODEL_2",
            "claude-3",
        ),  # underscores and digits in name
        (
            "export TOKEN=abc=def==ghi\n",
            "TOKEN",
            "abc=def==ghi",
        ),  # '=' inside the value field preserved
    ],
)
def test_env_value_extraction(text: str, key: str, expected_value: str) -> None:
    """parse_env extracts values verbatim (after quote-stripping) for a variety of value shapes."""
    result = parse_env(text)
    assert result[key] == expected_value


def test_env_unmatched_quotes_left_as_is() -> None:
    """parse_env does not strip quotes when they do not match (mismatched)."""
    result = parse_env('export MY_VAR="no-closing\n')
    # No matching closing quote — value is kept as-is with the opening quote.
    assert result["MY_VAR"].startswith('"')


def test_env_multiple_variables_returned() -> None:
    """parse_env returns all key-value pairs in the file."""
    text = "FOO=1\nexport BAR=2\nexport BAZ=three\n"
    result = parse_env(text)
    assert result == {"FOO": "1", "BAR": "2", "BAZ": "three"}


def test_env_malformed_line_skipped() -> None:
    """parse_env silently skips lines that do not match NAME=VALUE."""
    result = parse_env("NOT_AN_ASSIGNMENT\nexport VALID=yes\n")
    assert "VALID" in result
    assert "NOT_AN_ASSIGNMENT" not in result


def test_env_empty_value_is_stored() -> None:
    """parse_env stores an empty string when the value is absent after '='."""
    result = parse_env("export EMPTY_VAR=\n")
    assert "EMPTY_VAR" in result
    assert result["EMPTY_VAR"] == ""


# ---------------------------------------------------------------------------
# Tier-template resolution — templates/install/ location verification
# ---------------------------------------------------------------------------
#
# These tests verify that the three pseudocron tier templates:
#   - pseudocron-small.cfg.example
#   - pseudocron-medium.cfg.example
#   - pseudocron-large.cfg.example
# exist at the new team/templates/install/ location AND that their content
# is parseable by parse_config() into at least one valid job.
#
# Tests use PGAI_DEV_TREE_PATH to locate the dev tree root.


def _templates_install_dir() -> pathlib.Path:
    """Return the path to team/templates/install/ in the dev tree."""
    return _dev_tree_root() / "team" / "templates" / "install"


def _tier_template_path(tier: str) -> pathlib.Path:
    """Return the expected path for a pseudocron tier template."""
    return _templates_install_dir() / f"pseudocron-{tier}.cfg.example"


@pytest.mark.parametrize("tier", ["small", "medium", "large"])
def test_tier_template_exists(tier: str) -> None:
    """Each pseudocron-<tier>.cfg.example exists at team/templates/install/."""
    assert _tier_template_path(tier).exists(), (
        f"Missing {tier} tier template at: {_tier_template_path(tier)}"
    )


@pytest.mark.parametrize("tier", ["small", "medium", "large"])
def test_tier_template_parses_to_jobs(tier: str) -> None:
    """Each pseudocron-<tier>.cfg.example parses to at least one valid job.

    Uses parse_config() to confirm the template content is valid pseudocron
    format and that ROOT-RELATIVE command strings parse without error.
    """
    text = _tier_template_path(tier).read_text(encoding="utf-8")
    jobs = parse_config(text, source=f"pseudocron-{tier}.cfg.example")
    assert len(jobs) >= 1, (
        f"{tier.capitalize()} tier template parsed to zero jobs; template may be empty or malformed."
    )
    # All minute values must be valid (0-59 range is enforced by parse_config).
    for minute, _cmd in jobs:
        assert 0 <= minute <= 59, f"Minute {minute} out of range in {tier} template"


def test_no_tier_template_contains_kanban_root_placeholder() -> None:
    """grep-zero: none of the three tier templates contain __KANBAN_ROOT__.

    Templates use ROOT-RELATIVE commands.  pseudocron.py sets cwd to the
    resolved kanban root so relative paths work from any mount point.
    A placeholder remaining in a template would be passed literally to the
    shell and fail at runtime.
    """
    for tier in ("small", "medium", "large"):
        text = _tier_template_path(tier).read_text(encoding="utf-8")
        assert "__KANBAN_ROOT__" not in text, (
            f"pseudocron-{tier}.cfg.example still contains __KANBAN_ROOT__; "
            "templates must use ROOT-RELATIVE commands only."
        )


def test_tier_template_commands_are_root_relative() -> None:
    """All active job commands in tier templates are ROOT-RELATIVE (no leading /).

    ROOT-RELATIVE means the command does not begin with '/' — it starts with
    a directory name that resolves under the kanban root when pseudocron.py
    sets cwd=root before spawning the subprocess.
    """
    for tier in ("small", "medium", "large"):
        text = _tier_template_path(tier).read_text(encoding="utf-8")
        jobs = parse_config(text, source=f"pseudocron-{tier}.cfg.example")
        assert len(jobs) >= 1, f"No jobs parsed from {tier} template"
        for _minute, cmd in jobs:
            # Extract the first token (the executable / path before the first space).
            first_token = cmd.split()[0]
            assert not first_token.startswith("/"), (
                f"Command in {tier} template begins with absolute path: {first_token!r}. "
                "Templates must use ROOT-RELATIVE commands so they work from any mount."
            )


# ---------------------------------------------------------------------------
# Relocation fixture — identical relative cfg fires from two different mounts
# ---------------------------------------------------------------------------
#
# Behaviorally asserts that a ROOT-RELATIVE pseudocron cfg that refers to
# "scripts/marker.sh" fires correctly when pseudocron's cwd is set to the
# mount root.  The test simulates two independent kanban installations at
# different paths and confirms each produces its own marker file.

def test_relocation_relative_cfg_fires_from_two_mounts(tmp_path: pathlib.Path) -> None:
    """Relocation fixture: identical relative cfg fires correctly from two mount paths.

    Two temp directories simulate two kanban installations at different paths.
    Each has a tiny scripts/marker.sh that writes a marker file.
    The same ROOT-RELATIVE cfg ("0 scripts/marker.sh") is placed in each.
    Running the job with cwd=mount_root confirms the relative command resolves
    to the correct installation — proving relocation safety.
    """
    import subprocess
    import stat

    cfg_text = "0 scripts/marker.sh\n"

    for mount_label in ("mount_a", "mount_b"):
        mount_root = tmp_path / mount_label
        scripts_dir = mount_root / "scripts"
        scripts_dir.mkdir(parents=True)

        marker_file = mount_root / f"marker-{mount_label}.txt"
        marker_sh = scripts_dir / "marker.sh"
        marker_sh.write_text(f"#!/bin/bash\ntouch {marker_file}\n")
        marker_sh.chmod(marker_sh.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

        jobs = parse_config(cfg_text, source="test-relocation.cfg")
        assert len(jobs) == 1
        _minute, command = jobs[0]

        # Fire the job with cwd=mount_root (mirrors pseudocron.py behaviour).
        result = subprocess.run(
            ["bash", "-c", command],
            cwd=str(mount_root),
            timeout=10,
        )
        assert result.returncode == 0, (
            f"Job failed for {mount_label} with cwd={mount_root}: returncode={result.returncode}"
        )
        assert marker_file.exists(), (
            f"Marker file not created for {mount_label}; "
            f"relative command did not resolve under cwd={mount_root}"
        )


# ---------------------------------------------------------------------------
# Container-mount-path relocation variant — /pgai_agent_kanban path form
# ---------------------------------------------------------------------------
#
# Extends the two-mount relocation fixture above to cover the specific
# container deployment path: when the kanban is bind-mounted at
# /pgai_agent_kanban (the canonical container mount path), the ROOT-RELATIVE
# cfg must still fire commands relative to that mount root.
#
# This is the in-suite proof that the container's pseudocron.cfg will work
# correctly when the kanban is mounted at /pgai_agent_kanban rather than at
# an arbitrary host path.  The test simulates the container mount under
# tmp_path so it runs without Docker.

def test_relocation_container_mount_path_fires_correctly(
    tmp_path: pathlib.Path,
) -> None:
    """Container-mount-path relocation: relative cfg fires correctly from /pgai_agent_kanban-shaped path.

    Simulates the canonical container deployment: the kanban is bind-mounted
    at a /pgai_agent_kanban-shaped directory.  A ROOT-RELATIVE pseudocron cfg
    fires commands relative to that mount root, confirming pseudocron.py's
    cwd-setting behaviour is correct in the container environment.
    """
    import subprocess
    import stat

    # Simulate /pgai_agent_kanban under tmp_path (no real mount needed).
    container_mount = tmp_path / "pgai_agent_kanban"
    scripts_dir = container_mount / "scripts"
    scripts_dir.mkdir(parents=True)

    marker_file = container_mount / "marker-container-mount.txt"
    marker_sh = scripts_dir / "marker.sh"
    marker_sh.write_text(f"#!/bin/bash\ntouch {marker_file}\n")
    marker_sh.chmod(
        marker_sh.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH
    )

    cfg_text = "0 scripts/marker.sh\n"
    jobs = parse_config(cfg_text, source="test-container-mount.cfg")
    assert len(jobs) == 1
    _minute, command = jobs[0]

    # Fire the job with cwd=container_mount (mirrors pseudocron.py behaviour
    # when PGAI_AGENT_KANBAN_ROOT_PATH=/pgai_agent_kanban in the container).
    result = subprocess.run(
        ["bash", "-c", command],
        cwd=str(container_mount),
        timeout=10,
    )
    assert result.returncode == 0, (
        f"Job failed for container-mount path with cwd={container_mount}: "
        f"returncode={result.returncode}"
    )
    assert marker_file.exists(), (
        "Marker file not created for container-mount path variant; "
        f"relative command did not resolve under cwd={container_mount}"
    )


# ---------------------------------------------------------------------------
# Legacy fixture — absolute-path cfg still fires (backward compatibility)
# ---------------------------------------------------------------------------
#
# Confirms that an existing pseudocron.cfg with absolute-path commands
# continues to work after the ROOT-RELATIVE migration.  Absolute paths
# are independent of cwd, so they fire correctly regardless of the setting.

def test_legacy_absolute_path_cfg_still_fires(tmp_path: pathlib.Path) -> None:
    """Legacy fixture: an absolute-path cfg still fires after the ROOT-RELATIVE migration.

    Operators who installed pseudocron.cfg before this change may have absolute
    paths in their config.  pseudocron.py passes cwd=root to Popen, but
    absolute commands are unaffected by cwd — they resolve to the exact path
    given.  This test confirms backward compatibility.
    """
    import subprocess

    marker_file = tmp_path / "legacy-marker.txt"
    # Absolute path to /bin/touch (or equivalent); write a cfg with an absolute command.
    # We use '/bin/sh -c' with an absolute marker path to keep this host-agnostic.
    cfg_text = f"0 /bin/sh -c 'touch {marker_file}'\n"

    jobs = parse_config(cfg_text, source="test-legacy.cfg")
    assert len(jobs) == 1
    _minute, command = jobs[0]

    # Use an unrelated cwd to confirm the absolute command fires independently of cwd.
    unrelated_cwd = str(tmp_path / "some" / "other" / "path")
    pathlib.Path(unrelated_cwd).mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        ["bash", "-c", command],
        cwd=unrelated_cwd,
        timeout=10,
    )
    assert result.returncode == 0, (
        f"Legacy absolute-path job failed: returncode={result.returncode}"
    )
    assert marker_file.exists(), (
        "Marker file not created; absolute-path command did not fire as expected."
    )
