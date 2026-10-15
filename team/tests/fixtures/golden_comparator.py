"""
golden_comparator.py
=====================
Test-fidelity helper: structural CLI-JSON vs REST-JSON comparator for
golden-pair parity fixtures.

WHY THIS EXISTS
---------------
Every golden-pair test asks the same question: does this read verb's CLI
output resolve to the SAME data as its REST twin, once the REST response's
own envelope (``exit_code``/``stderr``/``warnings`` -- transport plumbing no
CLI output carries) is set aside? Before this module, each parity test
(``test_cli_rest_verdict_parity.py``, ``test_cli_rest_agent_metrics_parity.py``)
answered that with a bare ``assert cli_dict == rest_dict``: true or false,
with no indication of WHERE the two diverged when they didn't match. A
comparator that names the differing key path turns a failing golden-pair
test from "these two blobs disagree, go diff them by hand" into a pointer
straight at the drifted field.

HOW TO USE
----------
    from tests.fixtures.golden_comparator import assert_json_parity, strip_envelope

    rest_body = strip_envelope(response.json(), envelope_keys=("warnings",))
    assert_json_parity(cli_value, rest_body)
    # Raises AssertionError naming the first differing key path(s) when the
    # two structures disagree; returns None (silently) when they match.

WHAT THIS MODULE DOES NOT DO
------------------------------
- It does not know anything about HTTP, subprocesses, or any particular
  verb's schema. It operates on plain JSON-shaped Python values (dict,
  list, str, int, float, bool, None) that the caller has already parsed
  from CLI stdout or a REST response body.
- It does not mutate either input. ``strip_envelope`` returns a shallow
  copy with the named top-level keys removed; the original REST body is
  left untouched for the caller to log on failure.
"""

from __future__ import annotations

from typing import Any, Iterable

__all__ = ["diff_json", "assert_json_parity", "strip_envelope"]


def strip_envelope(value: Any, envelope_keys: Iterable[str] = ("exit_code", "stderr", "warnings")) -> Any:
    """Return a shallow copy of *value* with the named top-level keys removed.

    Args:
        value:         The REST response body (typically a dict decoded
                       from JSON). Non-dict values are returned unchanged --
                       there is nothing to strip.
        envelope_keys: Top-level keys to remove. Defaults to the three
                       fields every shell-out envelope in this API carries
                       (``adapter.py``'s ``ShellResult`` envelope shape)
                       that no CLI stdout blob ever contains itself.

    Returns:
        A new dict (when *value* is a dict) with the envelope keys absent;
        *value* itself, unchanged, otherwise.
    """
    if not isinstance(value, dict):
        return value
    keys = set(envelope_keys)
    return {k: v for k, v in value.items() if k not in keys}


def _diff_json(cli_value: Any, rest_value: Any, path: str, ignore_keys: frozenset[str]) -> list[str]:
    """Recursive worker for ``diff_json``. See that function's docstring."""
    if isinstance(cli_value, dict) and isinstance(rest_value, dict):
        diffs: list[str] = []
        cli_keys = set(cli_value.keys()) - ignore_keys
        rest_keys = set(rest_value.keys()) - ignore_keys

        only_in_cli = sorted(cli_keys - rest_keys)
        only_in_rest = sorted(rest_keys - cli_keys)
        if only_in_cli:
            diffs.append(f"{path}: keys only in CLI: {only_in_cli}")
        if only_in_rest:
            diffs.append(f"{path}: keys only in REST: {only_in_rest}")

        for key in sorted(cli_keys & rest_keys):
            diffs.extend(
                _diff_json(cli_value[key], rest_value[key], f"{path}.{key}", ignore_keys)
            )
        return diffs

    if isinstance(cli_value, list) and isinstance(rest_value, list):
        diffs = []
        if len(cli_value) != len(rest_value):
            diffs.append(
                f"{path}: length mismatch: CLI has {len(cli_value)}, REST has {len(rest_value)}"
            )
        for idx, (cli_item, rest_item) in enumerate(zip(cli_value, rest_value)):
            diffs.extend(_diff_json(cli_item, rest_item, f"{path}[{idx}]", ignore_keys))
        return diffs

    # Type mismatch between the two branches (e.g. CLI has a dict where REST
    # has a list) or a scalar mismatch -- both are reported the same way,
    # naming the differing key path and both values.
    if cli_value != rest_value:
        return [f"{path}: CLI={cli_value!r} REST={rest_value!r}"]
    return []


def diff_json(
    cli_value: Any,
    rest_value: Any,
    ignore_keys: Iterable[str] = (),
) -> list[str]:
    """Return a list of human-readable differences between *cli_value* and *rest_value*.

    Walks both JSON-shaped values in lockstep (dict, list, or scalar) and
    reports every divergence as one string naming the JSON key path where it
    occurs -- e.g. ``"$.verdicts[0].verdict: CLI='RUNNING' REST='BLOCKED'"``
    -- rather than a single bare "these differ" verdict. An empty return
    value means the two structures are identical (modulo *ignore_keys*).

    Args:
        cli_value:   The CLI-derived structure (already parsed into plain
                     Python dict/list/scalar values by the caller).
        rest_value:  The REST-derived structure, in the same shape.
        ignore_keys: Dict keys to skip wherever they appear, at any nesting
                     depth -- e.g. envelope-only fields the caller did not
                     already strip with ``strip_envelope``.

    Returns:
        A list of diff strings, in the order encountered (dict keys are
        visited in sorted order for determinism); empty when no divergence
        is found.
    """
    return _diff_json(cli_value, rest_value, path="$", ignore_keys=frozenset(ignore_keys))


def assert_json_parity(
    cli_value: Any,
    rest_value: Any,
    ignore_keys: Iterable[str] = (),
) -> None:
    """Assert that *cli_value* and *rest_value* are structurally identical.

    Args:
        cli_value:   The CLI-derived structure.
        rest_value:  The REST-derived structure.
        ignore_keys: Dict keys to skip wherever they appear (see
                     ``diff_json``).

    Raises:
        AssertionError: naming every differing key path, when the two
                        structures disagree. Passes silently otherwise.
    """
    diffs = diff_json(cli_value, rest_value, ignore_keys=ignore_keys)
    if diffs:
        rendered = "\n".join(f"  - {d}" for d in diffs)
        raise AssertionError(
            "CLI and REST values diverge over the SAME fixture:\n"
            f"{rendered}\n"
            f"CLI:  {cli_value!r}\n"
            f"REST: {rest_value!r}"
        )
