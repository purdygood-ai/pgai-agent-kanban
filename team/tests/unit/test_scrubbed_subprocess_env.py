"""
test_scrubbed_subprocess_env.py
=================================
Unit coverage for the shared subprocess-environment builder
(tests/fixtures/scrubbed_subprocess_env.py).

The contract under test: `build_scrubbed_env` never inherits the invoking
process's own environment, builds PATH/HOME/LANG from an explicit set of
names, layers *extra_path_dirs* ahead of the standard toolchain, and lets
*extra_env* win over the base for any name both define.
"""

from __future__ import annotations

import os
import pathlib

import pytest

from tests.fixtures.scrubbed_subprocess_env import (
    AGENT_SCOPED_ENV_NAMES,
    build_scrubbed_env,
    scrub_agent_scoped_env,
)

# A name no production code or fixture in this tree sets -- safe to plant
# in the current process's environment for the duration of a single test
# without colliding with anything real.
_ARBITRARY_HOST_VAR = "PGAI_TEST_SCRUBBED_ENV_HOST_ONLY_MARKER"


def test_result_contains_no_name_the_invoking_shell_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Setting an arbitrary variable in the test process itself must not
    make it into the builder's result -- the whole point of building from
    an explicit set is that nothing ambient leaks through."""
    monkeypatch.setenv(_ARBITRARY_HOST_VAR, "leaked-if-present")
    assert os.environ.get(_ARBITRARY_HOST_VAR) == "leaked-if-present"

    env = build_scrubbed_env(home_dir=tmp_path / "home")

    assert _ARBITRARY_HOST_VAR not in env, (
        f"{_ARBITRARY_HOST_VAR!r} was set in the invoking shell but leaked "
        f"into the builder's result anyway: {env!r}"
    )


def test_result_contains_no_other_ambient_name_either(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """The negative claim generalised: the result's keys are exactly the
    three base names this builder constructs, nothing more -- not a subset
    of `os.environ` filtered down, but a fixed, small set built from
    scratch. A real ambient variable this test process happens to carry
    (PWD, if present) must not appear either."""
    env = build_scrubbed_env(home_dir=tmp_path / "home")

    assert set(env) == {"PATH", "HOME", "LANG"}, (
        f"expected exactly PATH/HOME/LANG with no extras or omissions; "
        f"got keys: {sorted(env)!r}"
    )
    if "PWD" in os.environ:
        assert "PWD" not in env


def test_caller_supplied_value_wins_over_the_builders_own_default(
    tmp_path: pathlib.Path,
) -> None:
    """extra_env is applied last, so a caller-supplied value for a name the
    base already builds (HOME, in this case) overrides the base's own
    default rather than being silently dropped."""
    base_home = tmp_path / "base-home"
    override_home = tmp_path / "caller-supplied-home"

    env = build_scrubbed_env(
        home_dir=base_home,
        extra_env={"HOME": str(override_home)},
    )

    assert env["HOME"] == str(override_home), (
        f"expected the caller-supplied HOME to win over the base default "
        f"{str(base_home)!r}; got {env['HOME']!r}"
    )


def test_caller_supplied_value_wins_for_a_name_outside_the_base_too(
    tmp_path: pathlib.Path,
) -> None:
    """The same override rule applies to a name the base never constructs
    at all (a case-specific budget variable) -- extra_env is how every
    such value reaches the result."""
    env = build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_env={"MAX_TASK_SECONDS": "600"},
    )

    assert env["MAX_TASK_SECONDS"] == "600"


def test_extra_path_dirs_are_prepended_ahead_of_the_standard_toolchain(
    tmp_path: pathlib.Path,
) -> None:
    """A caller-supplied directory (e.g. a stub-binary bin/) must resolve
    ahead of the standard POSIX toolchain directories, in the order given,
    never appended after them."""
    stub_bin = tmp_path / "stub_bin"

    env = build_scrubbed_env(home_dir=tmp_path / "home", extra_path_dirs=(stub_bin,))

    expected = os.pathsep.join([str(stub_bin), "/usr/local/bin", "/usr/bin", "/bin"])
    assert env["PATH"] == expected, f"expected {expected!r}, got {env['PATH']!r}"


def test_default_path_has_no_extra_dirs_when_none_are_given(
    tmp_path: pathlib.Path,
) -> None:
    """No *extra_path_dirs* -- the default, most common case -- yields
    exactly the standard toolchain, nothing prepended."""
    env = build_scrubbed_env(home_dir=tmp_path / "home")

    assert env["PATH"] == "/usr/local/bin:/usr/bin:/bin"


def test_lang_defaults_to_c_utf8_but_is_overridable(tmp_path: pathlib.Path) -> None:
    """LANG is pinned by default rather than left absent or inherited, and
    a caller that genuinely needs a different locale can still name one."""
    default_env = build_scrubbed_env(home_dir=tmp_path / "home")
    assert default_env["LANG"] == "C.UTF-8"

    overridden_env = build_scrubbed_env(home_dir=tmp_path / "home", lang="en_US.UTF-8")
    assert overridden_env["LANG"] == "en_US.UTF-8"


def test_home_is_required_and_reflected_verbatim(tmp_path: pathlib.Path) -> None:
    """HOME in the result is exactly the caller-supplied home_dir, as a
    string -- never the real invoking process's own $HOME."""
    home_dir = tmp_path / "isolated-home"

    env = build_scrubbed_env(home_dir=home_dir)

    assert env["HOME"] == str(home_dir)
    assert env["HOME"] != os.environ.get("HOME")


# scrub_agent_scoped_env operates only on a dict the test itself constructs
# -- never on os.environ -- so every assertion below reaches the same
# verdict whether or not the invoking shell happens to export one of the
# names in AGENT_SCOPED_ENV_NAMES. Coupling a test's verdict to the
# invoking shell's own exports is anti-pattern 2 (environment-coupled
# fixtures) from docs/test-authoring-guidelines.md; these tests build
# their own env dict from scratch precisely to avoid it.


def test_scrub_agent_scoped_env_removes_every_agent_scoped_name() -> None:
    """A dict carrying every name in AGENT_SCOPED_ENV_NAMES loses all of
    them -- driven from the tuple itself, so a name added to it without
    being wired into the removal loop is caught here."""
    env = {name: "planted-agent-scoped-value" for name in AGENT_SCOPED_ENV_NAMES}

    scrub_agent_scoped_env(env)

    assert not any(name in env for name in AGENT_SCOPED_ENV_NAMES), (
        f"expected every name in {AGENT_SCOPED_ENV_NAMES!r} to be removed; "
        f"remaining keys: {sorted(env)!r}"
    )


def test_scrub_agent_scoped_env_leaves_unrelated_names_untouched() -> None:
    """Names outside AGENT_SCOPED_ENV_NAMES survive the call with their
    values unchanged -- the helper removes exactly the named set, nothing
    more."""
    unrelated = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/scratch/home"}
    env = dict(unrelated)
    for name in AGENT_SCOPED_ENV_NAMES:
        env[name] = "planted-agent-scoped-value"

    scrub_agent_scoped_env(env)

    for name, value in unrelated.items():
        assert env.get(name) == value, (
            f"expected unrelated name {name!r} to survive with value "
            f"{value!r}; got {env.get(name)!r}"
        )


def test_scrub_agent_scoped_env_mutates_in_place_and_returns_same_object() -> None:
    """The helper mutates the caller's dict directly and returns that same
    object, so a caller can chain the call rather than reassign a copy."""
    env = {name: "planted-agent-scoped-value" for name in AGENT_SCOPED_ENV_NAMES}
    env["UNRELATED_NAME"] = "kept"

    result = scrub_agent_scoped_env(env)

    assert result is env
    assert "UNRELATED_NAME" in env


def test_agent_scoped_env_names_is_non_empty() -> None:
    """AGENT_SCOPED_ENV_NAMES must never be emptied -- an empty tuple would
    make scrub_agent_scoped_env a silent no-op while still reading as a
    guard being applied."""
    assert len(AGENT_SCOPED_ENV_NAMES) > 0
