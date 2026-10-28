#!/usr/bin/env python3
"""
token_capture.py — the Python half of the wake's token-usage capture.

``team/scripts/lib/token_capture.sh`` and
``team/scripts/lib/wake_driver_finalize.sh`` call this module for the three
steps that read or write usage data: parsing a claude JSON response into a
usage record, writing that record into a task's ``tokens.json``, and naming
the model that produced most of a task's output tokens. The tokens.json
schemas are described in ``token_capture.sh``'s header.

Usage:
    python3 -m pgai_agent_kanban.lib.token_capture parse-claude-json JSON_FILE RESULT_TEXT_FILE [REQUESTED_MODEL]
    python3 -m pgai_agent_kanban.lib.token_capture write-tokens USAGE_JSON TOKENS_JSON MODEL PROVIDER AGENT ELAPSED_SECONDS [RC_VERSION] [EFFORT]
    python3 -m pgai_agent_kanban.lib.token_capture dominant-model TOKENS_JSON

Put ``--`` after the verb when an argument may start with ``-``.

Verbs:
  parse-claude-json  Write the agent's text from a ``claude -p
                     --output-format json`` response to RESULT_TEXT_FILE and
                     print the usage record as one line of JSON (see
                     ``pgai_agent_kanban.lib.provider_usage.parse_claude_json``).
                     Exit 0. When JSON_FILE cannot be read (RESULT_TEXT_FILE
                     is emptied) or holds no JSON (RESULT_TEXT_FILE gets the
                     raw text), one stderr line and a
                     ``{"captured": false, ...}`` record are printed and the
                     exit is 1. Any other malformed response exits 1 with one
                     stderr line and nothing on stdout.
  write-tokens       Write the usage record USAGE_JSON into the tokens.json
                     at TOKENS_JSON, creating it or merging into it (see
                     ``write_tokens_record``). Prints nothing; exit 0. Exit 1
                     with one stderr line when the file cannot be written or
                     the record or existing file is malformed.
  dominant-model     Print the model with the greatest output_tokens in
                     TOKENS_JSON's model_usage map and exit 0; print nothing
                     and exit 1 when that cannot be determined (see
                     ``dominant_output_token_model``).

``--help`` (on its own or after a verb) prints usage and exits 0.

Public API:
    WARNING_PREFIX
    TIMESTAMP_FORMAT
    coerce_elapsed_seconds(value) -> int
    load_usage_record(text) -> object
    write_tokens_record(usage, tokens_json_path, model, provider, agent,
                        elapsed_seconds, rc_version="none", effort="",
                        now=None) -> dict
    dominant_output_token_model(tokens_json_path) -> str | None
    main(argv=None) -> int
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import sys
from typing import Optional, Union

# Run by file path, this module has no parent package and only its own
# directory is on sys.path: put the package root this file sits in first,
# so the import below resolves from the same tree, never another. Imported
# as a package member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.lib import provider_usage  # noqa: E402
from pgai_agent_kanban.lib.subcommand_cli import subcommand_main  # noqa: E402

__all__ = [
    "TIMESTAMP_FORMAT",
    "WARNING_PREFIX",
    "coerce_elapsed_seconds",
    "dominant_output_token_model",
    "load_usage_record",
    "main",
    "write_tokens_record",
]

#: Prefix of every warning this module's command line writes to stderr.
WARNING_PREFIX = "token_capture"

#: strftime format of the tokens.json ``timestamp`` field (UTC).
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

PathLike = Union[str, "os.PathLike[str]"]

_TOKEN_COUNT_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


def coerce_elapsed_seconds(value: Union[int, str]) -> int:
    """Return *value* as whole elapsed seconds.

    An int is returned as is. A string counts only when ``str.isdigit()``
    holds for it; any other string -- empty, signed, fractional, words --
    reads as 0.
    """
    if isinstance(value, int):
        return value
    return int(value) if value.isdigit() else 0


def load_usage_record(text: str) -> object:
    """Parse a usage record passed as JSON text.

    Empty or whitespace-only text, ``{}``, and text that is not JSON all
    read as an empty record (``{}``); otherwise the parsed value is
    returned as is.
    """
    if text.strip() in ("", "{}"):
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


def _load_existing_record(tokens_file: pathlib.Path) -> object:
    """Return the parsed tokens.json at *tokens_file*, or ``{}`` when it is
    missing, empty, unreadable or not JSON."""
    if not (tokens_file.is_file() and tokens_file.stat().st_size > 0):
        return {}
    try:
        return json.loads(tokens_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_record(tokens_file: pathlib.Path, record: dict) -> None:
    """Write *record* to *tokens_file* as ``indent=2`` JSON plus a newline."""
    tokens_file.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


def _sum_model_usage(existing_mu: dict, new_mu: dict) -> dict:
    """Return *existing_mu* with each *new_mu* entry added to it: a model in
    both has its four token counts and ``cost_usd`` summed, a new model is
    added with those five fields copied as given (missing ones as 0)."""
    fields = _TOKEN_COUNT_KEYS + ("cost_usd",)
    merged = dict(existing_mu)
    for model_name, entry in new_mu.items():
        if model_name in merged:
            previous = merged[model_name]
            merged[model_name] = {key: previous.get(key, 0) + entry.get(key, 0) for key in fields}
        else:
            merged[model_name] = {key: entry.get(key, 0) for key in fields}
    return merged


def write_tokens_record(
    usage: object,
    tokens_json_path: PathLike,
    model: str,
    provider: str,
    agent: str,
    elapsed_seconds: Union[int, str],
    rc_version: str = "none",
    effort: str = "",
    now: Optional[str] = None,
) -> dict:
    """Write one invocation's usage record into a task's ``tokens.json``.

    The file is written as indented JSON (``indent=2``) plus a newline, and
    the written record is returned. Three shapes:

    * **Failure record** -- *usage* has ``"captured": false``: the file is
      replaced (never merged) by ``captured``, ``reason`` (the usage
      record's, default ``no usage emitted``), ``provider``, ``agent``,
      ``rc_version``, ``invocations: 1``, ``elapsed_seconds``,
      ``timestamp``, ``total_cost_usd: 0`` and ``model_usage: {}``.
    * **Per-model schema** -- ``total_cost_usd`` is a float and
      ``model_usage`` an object: ``provider``, ``agent``, ``rc_version``,
      ``invocations``, ``elapsed_seconds``, ``timestamp``,
      ``total_cost_usd`` (summed with the existing file's) and
      ``model_usage`` (summed per model with the existing file's).
    * **Flat schema** -- otherwise: ``model``, ``provider``, ``agent``,
      ``rc_version``, the four token counts (summed), ``invocations``,
      ``elapsed_seconds`` and ``timestamp``.

    In the merged shapes ``invocations`` is the existing count plus 1 and
    ``elapsed_seconds`` the existing total plus this one; a missing, empty,
    unreadable or non-JSON existing file reads as no prior record. The
    ``rc_version`` written is *rc_version* unless it is empty or ``none``,
    in which case the existing file's value is kept (default ``none``); the
    failure record always takes *rc_version*, empty reading as ``none``.
    ``effort`` is added last when *effort*, or failing that the existing
    file's ``effort``, is non-empty, and is otherwise absent.

    Args:
        usage:            The usage record (``load_usage_record`` of the
                          text the provider parse printed).
        tokens_json_path: The task's ``tokens.json``.
        model:            Model name written by the flat schema.
        provider, agent:  Written as given.
        elapsed_seconds:  This invocation's seconds; a string is read with
                          ``coerce_elapsed_seconds``.
        rc_version:       The RC version in scope, or ``none``.
        effort:           The reasoning effort in force, or empty.
        now:              The ``timestamp`` to write; default is the current
                          UTC time in ``TIMESTAMP_FORMAT``.

    Raises:
        OSError: The file cannot be written.
        AttributeError: *usage* or the existing file is JSON but not an
            object.
        TypeError, ValueError: A count in *usage* or the existing file is
            not a number.
    """
    tokens_file = pathlib.Path(tokens_json_path)
    elapsed = coerce_elapsed_seconds(elapsed_seconds)
    rc_version = rc_version or "none"
    if now is None:
        now = datetime.datetime.now(tz=datetime.timezone.utc).strftime(TIMESTAMP_FORMAT)

    if usage.get("captured") is False:
        record = {
            "captured": False,
            "reason": usage.get("reason", "no usage emitted"),
            "provider": provider,
            "agent": agent,
            "rc_version": rc_version,
            "invocations": 1,
            "elapsed_seconds": elapsed,
            "timestamp": now,
            "total_cost_usd": 0,
            "model_usage": {},
        }
        if effort:
            record["effort"] = effort
        _write_record(tokens_file, record)
        return record

    existing = _load_existing_record(tokens_file)
    effective_rc_version = (
        rc_version if rc_version != "none" else existing.get("rc_version", "none") or "none"
    )
    new_total_cost = usage.get("total_cost_usd")
    new_model_usage = usage.get("model_usage")

    if isinstance(new_total_cost, float) and isinstance(new_model_usage, dict):
        existing_cost = existing.get("total_cost_usd", 0)
        if not isinstance(existing_cost, (int, float)):
            existing_cost = 0
        existing_mu = existing.get("model_usage", {})
        if not isinstance(existing_mu, dict):
            existing_mu = {}
        record = {
            "provider": provider,
            "agent": agent,
            "rc_version": effective_rc_version,
            "invocations": existing.get("invocations", 0) + 1,
            "elapsed_seconds": existing.get("elapsed_seconds", 0) + elapsed,
            "timestamp": now,
            "total_cost_usd": existing_cost + new_total_cost,
            "model_usage": _sum_model_usage(existing_mu, new_model_usage),
        }
    else:
        record = {"model": model, "provider": provider, "agent": agent, "rc_version": effective_rc_version}
        for key in _TOKEN_COUNT_KEYS:
            record[key] = existing.get(key, 0) + int(usage.get(key, 0))
        record["invocations"] = existing.get("invocations", 0) + 1
        record["elapsed_seconds"] = existing.get("elapsed_seconds", 0) + elapsed
        record["timestamp"] = now

    effective_effort = effort or existing.get("effort", "")
    if effective_effort:
        record["effort"] = effective_effort

    _write_record(tokens_file, record)
    return record


def dominant_output_token_model(tokens_json_path: PathLike) -> Optional[str]:
    """Return the model whose tokens.json ``model_usage`` entry has the
    greatest ``output_tokens``, or None when that cannot be determined.

    Ranking is on output tokens alone. Keys are visited in sorted order and
    only a strictly greater count replaces the leader, so a tie goes to the
    alphabetically first model. An entry that is not an object, or whose
    ``output_tokens`` is not a whole number (``int()`` fails), is skipped.

    None when the file cannot be read, is not UTF-8 JSON, is not a JSON
    object, has no non-empty ``model_usage`` object (the flat schema and
    the failure record), or no entry has positive output tokens.
    """
    try:
        with open(tokens_json_path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None

    model_usage = data.get("model_usage")
    if not isinstance(model_usage, dict) or not model_usage:
        return None

    best_model = None
    best_output = 0
    for model_name in sorted(model_usage):
        entry = model_usage[model_name]
        if not isinstance(entry, dict):
            continue
        try:
            output_tokens = int(entry.get("output_tokens", 0))
        except (TypeError, ValueError):
            continue
        if output_tokens > best_output:
            best_output = output_tokens
            best_model = model_name
    return best_model


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def _cmd_parse_claude_json(args: argparse.Namespace) -> int:
    prefix = provider_usage.CLAUDE_WARNING_PREFIX
    try:
        record = provider_usage.parse_claude_json(args.json_file, args.result_text_file, args.requested_model)
    except OSError as exc:
        print(f"{prefix}: cannot read json_file: {exc}", file=sys.stderr)
        print(json.dumps({"captured": False, "reason": f"cannot read json_file: {exc}"}))
        return 1
    except provider_usage.UnparseableOutputError as exc:
        print(f"{prefix}: {exc}", file=sys.stderr)
        print(json.dumps({"captured": False, "reason": str(exc)}))
        return 1
    except (ValueError, TypeError, AttributeError) as exc:
        print(f"{prefix}: cannot parse usage in {args.json_file}: {exc}", file=sys.stderr)
        return 1
    print(provider_usage.compact_json(record))
    return 0


def _cmd_write_tokens(args: argparse.Namespace) -> int:
    try:
        write_tokens_record(
            load_usage_record(args.usage_json),
            args.tokens_json,
            args.model,
            args.provider,
            args.agent,
            args.elapsed_seconds,
            args.rc_version,
            args.effort,
        )
    except OSError as exc:
        print(f"{WARNING_PREFIX}: cannot write tokens.json: {exc}", file=sys.stderr)
        return 1
    except (AttributeError, TypeError, ValueError) as exc:
        print(f"{WARNING_PREFIX}: cannot merge usage into {args.tokens_json}: {exc}", file=sys.stderr)
        return 1
    return 0


def _cmd_dominant_model(args: argparse.Namespace) -> int:
    model = dominant_output_token_model(args.tokens_json)
    if model is None:
        return 1
    print(model)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.lib.token_capture",
        description="Parse, record and read the wake's per-task token usage.",
    )
    verbs = parser.add_subparsers(dest="verb", metavar="VERB")
    verbs.required = True

    parse = verbs.add_parser(
        "parse-claude-json",
        help="Write the agent text and print the usage record of a claude JSON response.",
        description="Write the agent's text to RESULT_TEXT_FILE and print the usage record as one line of JSON.",
    )
    parse.add_argument("json_file", help="The captured 'claude -p --output-format json' response.")
    parse.add_argument("result_text_file", help="Where the agent's text is written.")
    parse.add_argument(
        "requested_model", nargs="?", default="",
        help="The model the wake requested; preferred as canonical_model_id when the response used it.",
    )
    parse.set_defaults(func=_cmd_parse_claude_json)

    write = verbs.add_parser(
        "write-tokens",
        help="Create or merge a task's tokens.json from one usage record.",
        description="Write the usage record USAGE_JSON into TOKENS_JSON, creating or merging it.",
    )
    write.add_argument("usage_json", help="The usage record, as JSON text ('' or '{}' for none).")
    write.add_argument("tokens_json", help="The task's tokens.json path.")
    write.add_argument("model", help="Model name the flat schema records.")
    write.add_argument("provider", help="Provider name, e.g. claude.")
    write.add_argument("agent", help="Agent role, e.g. coder.")
    write.add_argument("elapsed_seconds", help="Seconds this invocation took; anything but digits reads as 0.")
    write.add_argument(
        "rc_version", nargs="?", default="none",
        help="RC version in scope, e.g. v1.2.3; empty or 'none' keeps the existing value (default: none).",
    )
    write.add_argument(
        "effort", nargs="?", default="",
        help="Reasoning effort in force, e.g. high; empty keeps the existing value or omits the key.",
    )
    write.set_defaults(func=_cmd_write_tokens)

    dominant = verbs.add_parser(
        "dominant-model",
        help="Print the model with the most output tokens in a tokens.json.",
        description="Print the model_usage key with the greatest output_tokens; exit 1 when undeterminable.",
    )
    dominant.add_argument("tokens_json", help="The task's tokens.json path.")
    dominant.set_defaults(func=_cmd_dominant_model)

    return parser


main = subcommand_main(_build_parser)


if __name__ == "__main__":
    sys.exit(main())
