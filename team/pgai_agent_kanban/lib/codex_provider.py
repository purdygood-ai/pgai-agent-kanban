#!/usr/bin/env python3
"""
codex_provider.py — the Python half of the Codex wake provider.

``team/scripts/lib/provider_codex.sh`` runs the ``codex`` CLI and calls this
module for everything that reads or rewrites what the CLI produced: the
model-preflight scan, the configured-model read, and the usage parse.

Usage:
    python3 -m pgai_agent_kanban.lib.codex_provider capture-stdin [--prefix TEXT]
    python3 -m pgai_agent_kanban.lib.codex_provider preflight-error MODEL CLI_EXIT [JSONL_FILE]
    python3 -m pgai_agent_kanban.lib.codex_provider config-model CONFIG_FILE
    python3 -m pgai_agent_kanban.lib.codex_provider parse-jsonl JSONL_FILE RESULT_TEXT_FILE
    python3 -m pgai_agent_kanban.lib.codex_provider stamp-model USAGE_JSON MODEL

Verbs:
  capture-stdin    Copy stdin, byte for byte, into a new file in the kanban
                   temp root (``resolve_temp_root()``, created when missing)
                   named ``<prefix>XXXXXXXX`` (default prefix
                   ``pf_jsonl_``), and print its path. The file is kept.
                   Exit 0; exit 1 when the file cannot be created.
  preflight-error  Scan a preflight's JSONL output for a rejection. Print
                   the error message and exit 0 when the first JSON event is
                   ``{"type":"error"}``, or when the CLI exited non-zero with
                   no output; print nothing and exit 1 when the model was
                   accepted. An empty, blank-only or unreadable JSONL_FILE
                   reads as no output. Output that is not UTF-8, or whose
                   first JSON line is not an object, exits 1 with one stderr
                   line.
  config-model     Print the ``model = ...`` value of a codex TOML config,
                   or ``unknown`` when the file is missing, unreadable or
                   has no such key. Exit 0.
  parse-jsonl      Write the agent's text from a ``codex exec --json``
                   stream to RESULT_TEXT_FILE and print the flat usage
                   record as one line of JSON (see
                   ``pgai_agent_kanban.lib.provider_usage``). Exit 0. When
                   JSONL_FILE cannot be read, RESULT_TEXT_FILE is emptied, a
                   ``{"captured":false,...}`` record is printed, and the
                   exit is 1. A malformed usage block exits 1 with one
                   stderr line and nothing on stdout.
  stamp-model      Print USAGE_JSON with ``canonical_model_id`` set to
                   MODEL, as one line of JSON. Exit 0; exit 1 when
                   USAGE_JSON is not a JSON object.

``--help`` (on its own or after a verb) prints usage and exits 0.

Public API:
    DEFAULT_CAPTURE_PREFIX
    copy_stream_to_temp_file(stream, prefix=DEFAULT_CAPTURE_PREFIX) -> str
    preflight_error_message(model, cli_exit, jsonl_path) -> str | None
    read_config_model(config_path) -> str
    main(argv=None) -> int
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import tempfile
from typing import BinaryIO, Optional, Union

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
from pgai_agent_kanban.lib.temp_root import resolve_temp_root  # noqa: E402

__all__ = [
    "DEFAULT_CAPTURE_PREFIX",
    "copy_stream_to_temp_file",
    "main",
    "preflight_error_message",
    "read_config_model",
]

#: File-name prefix of the file ``capture-stdin`` creates.
DEFAULT_CAPTURE_PREFIX = "pf_jsonl_"

# ``model = "x"``, ``model = 'x'`` or ``model = x`` at the start of a line;
# the value stops at a quote, a ``#`` comment or the end of the line.
_MODEL_LINE_RE = re.compile(r'^\s*model\s*=\s*["\']?([^"\'#\n\r]+)["\']?', re.M)

PathLike = Union[str, "os.PathLike[str]"]


def copy_stream_to_temp_file(stream: BinaryIO, prefix: str = DEFAULT_CAPTURE_PREFIX) -> str:
    """Copy everything readable from binary *stream* into a new temp file.

    The file is created in the kanban temp root
    (:func:`pgai_agent_kanban.lib.temp_root.resolve_temp_root`, created when
    missing) with a name starting *prefix*, and is not deleted: the caller
    owns it. ``TMPDIR`` plays no part, so a capture outside a wake lands in
    the same root as one inside it.

    Returns:
        The new file's path.

    Raises:
        OSError: The file cannot be created or written.
    """
    data = stream.read()
    root = pathlib.Path(resolve_temp_root())
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(delete=False, prefix=prefix, dir=root) as handle:
        handle.write(data)
        return handle.name


def preflight_error_message(model: str, cli_exit: int, jsonl_path: Optional[PathLike]) -> Optional[str]:
    """Return why a codex model preflight failed, or None when it passed.

    Reads the JSONL a ``codex exec --json`` preflight wrote to *jsonl_path*
    and looks at the first line that is non-blank and valid JSON:

    * an event with ``"type": "error"`` -- the model was refused:
      ``model '<model>' rejected by codex: <message>`` (the event's
      ``message``, or ``unknown error`` when it has none);
    * any other event -- the model was accepted.

    When no line decides it and the file holds no non-blank line (empty,
    only newlines or whitespace, missing, unreadable, or *jsonl_path*
    empty) while *cli_exit* is non-zero, the CLI itself is suspect:
    ``codex exited <cli_exit> with no output; check CLI installation``.
    Otherwise None.

    Args:
        model:      The model name the message quotes.
        cli_exit:   The preflight CLI's exit status.
        jsonl_path: The captured JSONL; empty or None reads as no output.

    Raises:
        ValueError: The file is not valid UTF-8.
        AttributeError: The first JSON line is not an object.
    """
    try:
        text = pathlib.Path(jsonl_path).read_text(encoding="utf-8") if jsonl_path else ""
        lines = text.splitlines()
    except OSError:
        lines = []

    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "error":
            return f"model '{model}' rejected by codex: {event.get('message', 'unknown error')}"
        # Any non-error event means the model was accepted.
        break

    if cli_exit != 0 and not any(line.strip() for line in lines):
        return f"codex exited {cli_exit} with no output; check CLI installation"
    return None


def read_config_model(config_path: PathLike) -> str:
    """Return the ``model`` value set in the codex TOML config at *config_path*.

    The first line of the form ``model = "<value>"`` (double, single or no
    quotes) wins; the value is stripped of surrounding whitespace. Returns
    ``unknown`` when the file is missing or unreadable or sets no model.
    A key such as ``model_reasoning_effort`` is not ``model``.
    """
    try:
        text = pathlib.Path(config_path).read_text(encoding="utf-8")
        match = _MODEL_LINE_RE.search(text)
        return match.group(1).strip() if match else "unknown"
    except Exception:  # noqa: BLE001 -- any failure to read means "not configured here"
        return "unknown"


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def _cmd_capture_stdin(args: argparse.Namespace) -> int:
    try:
        print(copy_stream_to_temp_file(sys.stdin.buffer, prefix=args.prefix))
    except OSError as exc:
        print(f"codex_provider: cannot capture stdin to a temp file: {exc}", file=sys.stderr)
        return 1
    return 0


def _cmd_preflight_error(args: argparse.Namespace) -> int:
    try:
        message = preflight_error_message(args.model, args.cli_exit, args.jsonl_file)
    except (ValueError, AttributeError) as exc:
        print(f"codex_provider: cannot scan preflight output {args.jsonl_file}: {exc}", file=sys.stderr)
        return 1
    if message is None:
        return 1
    print(message)
    return 0


def _cmd_config_model(args: argparse.Namespace) -> int:
    print(read_config_model(args.config_file))
    return 0


def _cmd_parse_jsonl(args: argparse.Namespace) -> int:
    try:
        record = provider_usage.parse_codex_jsonl(args.jsonl_file, args.result_text_file)
    except OSError as exc:
        print(f"{provider_usage.WARNING_PREFIX}: cannot read jsonl_file: {exc}", file=sys.stderr)
        print(json.dumps({"captured": False, "reason": f"cannot read jsonl_file: {exc}"}))
        return 1
    except (ValueError, TypeError, AttributeError) as exc:
        print(
            f"{provider_usage.WARNING_PREFIX}: cannot parse usage in {args.jsonl_file}: {exc}",
            file=sys.stderr,
        )
        return 1
    print(provider_usage.compact_json(record))
    return 0


def _cmd_stamp_model(args: argparse.Namespace) -> int:
    try:
        record = json.loads(args.usage_json)
        if not isinstance(record, dict):
            raise TypeError(f"expected a JSON object, got {type(record).__name__}")
    except (ValueError, TypeError) as exc:
        print(f"codex_provider: cannot stamp canonical_model_id: {exc}", file=sys.stderr)
        return 1
    print(provider_usage.compact_json(provider_usage.stamp_canonical_model_id(record, args.model)))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.lib.codex_provider",
        description="Read and rewrite what the codex CLI produced for the Codex wake provider.",
    )
    verbs = parser.add_subparsers(dest="verb", metavar="VERB")
    verbs.required = True

    capture = verbs.add_parser(
        "capture-stdin",
        help="Copy stdin into a new temp file and print its path.",
        description="Copy stdin into a new file in the kanban temp root and print its path.",
    )
    capture.add_argument(
        "--prefix", default=DEFAULT_CAPTURE_PREFIX,
        help=f"File-name prefix of the new file (default: {DEFAULT_CAPTURE_PREFIX}).",
    )
    capture.set_defaults(func=_cmd_capture_stdin)

    preflight = verbs.add_parser(
        "preflight-error",
        help="Print why a model preflight failed; exit 1 when it passed.",
        description="Print the preflight's error message and exit 0, or print nothing and exit 1 when the model was accepted.",
    )
    preflight.add_argument("model", help="The model name the message quotes.")
    preflight.add_argument("cli_exit", type=int, help="The preflight CLI's exit status.")
    preflight.add_argument(
        "jsonl_file", nargs="?", default="",
        help="The preflight's captured JSONL output (empty or omitted reads as no output).",
    )
    preflight.set_defaults(func=_cmd_preflight_error)

    config = verbs.add_parser(
        "config-model",
        help="Print the model a codex config.toml sets, or 'unknown'.",
        description="Print the model = value of a codex TOML config, or 'unknown'.",
    )
    config.add_argument("config_file", help="Path of the codex config.toml.")
    config.set_defaults(func=_cmd_config_model)

    parse = verbs.add_parser(
        "parse-jsonl",
        help="Write the agent text and print the usage record of a codex JSONL stream.",
        description="Write the agent's text to RESULT_TEXT_FILE and print the flat usage record as JSON.",
    )
    parse.add_argument("jsonl_file", help="The captured 'codex exec --json' stream.")
    parse.add_argument("result_text_file", help="Where the agent's text is written.")
    parse.set_defaults(func=_cmd_parse_jsonl)

    stamp = verbs.add_parser(
        "stamp-model",
        help="Print a usage record with canonical_model_id set.",
        description="Print USAGE_JSON with canonical_model_id set to MODEL, as one line of JSON.",
    )
    stamp.add_argument("usage_json", help="The usage record, as JSON text.")
    stamp.add_argument("model", help="The model id to record.")
    stamp.set_defaults(func=_cmd_stamp_model)

    return parser


main = subcommand_main(_build_parser)


if __name__ == "__main__":
    sys.exit(main())
