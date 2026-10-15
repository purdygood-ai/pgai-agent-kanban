"""
operator_verb_manifest.py — the one Python reader for the operator verb
manifest (team/config/operator_verbs.manifest).

That file is the machine-readable single source of truth for the operator
verb set: every verb's script, declared flag vocabulary, mutation/
idempotence class, REST twin, read|action enforcement class, REST
response schema reference, and implementation binding (see the
manifest's own header for the full field-by-field format and parse rule). This module is the ONLY place
in the Python stack that parses it -- every other Python consumer (the
reconciliation lint, lint_api_parity.py, the test suite) imports
:func:`load_manifest` / :func:`get_verb` instead of re-reading the file
itself, so there is never a second hand-transcribed copy of the verb list
to drift out of sync with the first.

The sibling bash reader is team/scripts/lib/operator_verb_manifest.sh --
same file, same parse rule, independent implementation because bash and
Python cannot share a parser, but both readers agree on the on-disk format
documented in the manifest file's header.

Public API
----------
    load_manifest(path=None) -> dict[str, Verb]
        Parse the manifest file into an ordered mapping of verb name ->
        Verb record (insertion order == file order).

    get_verb(name, path=None) -> Verb
        Convenience: load_manifest(path)[name], raising KeyError with a
        clear message when the verb is not declared.

    verb_names(path=None) -> list[str]
        The verb names in file order.

    default_manifest_path() -> pathlib.Path
        The manifest file's default location (team/config/
        operator_verbs.manifest, resolved relative to this module's own
        location -- "each tree finds itself").

    parse_impl(value) -> Impl
        Validate one ``impl`` column value (``module:<dotted.function>``,
        ``shell``, or ``-`` on a planned row) and split it into its kind and
        target. Raises ValueError naming the accepted forms on anything
        else. The reconciliation lint and lint_api_route_impl.py both call
        this, so the column has exactly one grammar.

    extract_operator_valid_flags(script_path) -> list[str]
        Parse a script's own ``OPERATOR_VALID_FLAGS=(...)`` declaration and
        return the flags in declared order. Used by the reconciliation
        check to compare a script's ACTUAL flags against what the manifest
        declares -- kept here (not duplicated a third time) since both the
        reconciliation lint and any future consumer need the exact same
        extraction rule the manifest's own ``flags`` column was built from.
"""

from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass
from typing import Optional

# ---------------------------------------------------------------------------
# Scripts that parse their own arguments by hand instead of declaring
# OPERATOR_VALID_FLAGS (team/scripts/lib/operator_args.sh). The
# reconciliation check trusts the manifest's ``flags`` column for these
# rather than attempting to re-derive it -- there is no declarative array to
# diff against. Paths are relative to team/, matching the manifest's
# ``script`` column.
#
# verify-rc-state.sh and scripts/dashboard/show-queues.sh both declared
# OPERATOR_VALID_FLAGS as of the read-verb-conformance ticket and were
# removed from this set; the reconciliation check now derives their flags
# the same way it does for every other "active" verb. show-metrics.sh,
# agent-metrics.sh, and cm/cancel-rc.sh joined this set with the verb-
# census-record ticket that first gave each of them a manifest row -- all
# three predate that ticket and already hand-parsed their own arguments;
# nothing about their argument handling changed.
# ---------------------------------------------------------------------------
NO_OPERATOR_VALID_FLAGS_ARRAY: frozenset[str] = frozenset(
    {
        "scripts/ship-rc.sh",
        "scripts/dashboard/show-metrics.sh",
        "scripts/dashboard/agent-metrics.sh",
        "scripts/cm/cancel-rc.sh",
    }
)


@dataclass(frozen=True)
class Verb:
    """One verb record (a single V line) from the manifest."""

    name: str
    status: str  # "active" | "planned"
    script: Optional[str]  # relative to team/; None when planned
    flags: tuple[str, ...]  # declared flag vocabulary, in manifest order
    mutates: Optional[bool]  # True/False; None when planned
    idempotence: str  # "idempotent" | "non-idempotent" | "read-only" | "-"
    rest_method: str  # "GET" | "POST" | "ABSENT"
    rest_route: str  # route path, or "ABSENT"
    rest_body_model: str  # class name, "NONE", or "ABSENT"
    # verb_class / schema_ref default so a caller constructing a Verb by
    # hand for an unrelated fixture (e.g. a doc-generator test that only
    # cares about flags/script) does not need to name two fields it has no
    # opinion on; load_manifest() below always supplies both explicitly for
    # every row it parses, so the default never masks a real parse.
    verb_class: str = "-"  # "read" | "action" for an active verb; "-" when planned
    schema_ref: str = "ABSENT"  # response schema name, "ABSENT", or "-" when planned
    # Raw ``impl`` column value: "module:<dotted.function>", "shell", or "-"
    # on a planned row. None when the row omits the column entirely -- the
    # reader accepts such a row so a fixture written before the column
    # existed still loads; lint_operator_verb_manifest.py reports it.
    impl: Optional[str] = None


IMPL_SHELL = "shell"
IMPL_MODULE_PREFIX = "module:"
IMPL_PLANNED = "-"

# A dotted Python path naming a function: at least one module segment and
# the function name, each a Python identifier.
_DOTTED_FUNCTION_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+$")


@dataclass(frozen=True)
class Impl:
    """A parsed ``impl`` column value.

    kind is "module", "shell", or "planned". For a "module" value, target
    is the dotted function path, module is everything before its last
    segment, and function is the last segment; all three are None
    otherwise.
    """

    kind: str
    target: Optional[str] = None

    @property
    def module(self) -> Optional[str]:
        """Return the dotted module path before the target's last segment, or None when there is no target."""
        return self.target.rsplit(".", 1)[0] if self.target else None

    @property
    def function(self) -> Optional[str]:
        """Return the target's last segment (the function name), or None when there is no target."""
        return self.target.rsplit(".", 1)[1] if self.target else None


def parse_impl(value: Optional[str]) -> Impl:
    """Parse one ``impl`` column value.

    Accepted forms: ``module:<dotted.function>`` (for example
    ``module:pgai_agent_kanban.ops.read.get_release_state``), ``shell``,
    and ``-`` (a planned row). Surrounding whitespace is tolerated.

    Raises:
        ValueError: *value* is None (the column is missing) or matches none
            of the accepted forms. The message names the value received and
            the accepted forms.
    """
    accepted = "accepted: 'module:<dotted.module.function>', 'shell', or '-' on a planned row"
    if value is None:
        raise ValueError(f"impl column is missing; {accepted}")
    text = value.strip()
    if text == IMPL_SHELL:
        return Impl("shell")
    if text == IMPL_PLANNED:
        return Impl("planned")
    if text.startswith(IMPL_MODULE_PREFIX):
        target = text[len(IMPL_MODULE_PREFIX):].strip()
        if _DOTTED_FUNCTION_RE.match(target):
            return Impl("module", target)
        raise ValueError(
            f"impl {value!r} does not name a dotted module.function after "
            f"'module:'; {accepted}"
        )
    raise ValueError(f"impl {value!r} is not a recognised value; {accepted}")


class ManifestFormatError(ValueError):
    """Raised when the manifest file does not parse to a coherent verb list.

    Never raised for cosmetic issues (comment wording, column alignment) --
    only for structural problems a reader cannot safely paper over: a V
    record with the wrong field count, or a duplicate verb name.
    """


_FIELD_COUNT = 13  # sigil + 12 fields
# A row without the trailing ``impl`` column still parses (impl=None), so a
# fixture authored against the older layout keeps loading; the
# reconciliation lint reports the missing value on the real manifest.
_FIELD_COUNT_WITHOUT_IMPL = 12


def default_manifest_path() -> pathlib.Path:
    """Return the manifest file's default path.

    Resolved from this module's own location (team/pgai_agent_kanban/lib/
    operator_verb_manifest.py -> team/config/operator_verbs.manifest) so the
    module finds its own tree's manifest regardless of the process's
    working directory.
    """
    this_file = pathlib.Path(__file__).resolve()
    team_dir = this_file.parent.parent.parent  # lib/ -> pgai_agent_kanban/ -> team/
    return team_dir / "config" / "operator_verbs.manifest"


def _parse_bool_or_none(value: str) -> Optional[bool]:
    if value == "-":
        return None
    if value == "yes":
        return True
    if value == "no":
        return False
    raise ManifestFormatError(f"expected 'yes', 'no', or '-'; got {value!r}")


def _parse_flags(value: str) -> tuple[str, ...]:
    if value == "-":
        return ()
    return tuple(value.split("|"))


def _parse_optional(value: str) -> Optional[str]:
    return None if value == "-" else value


def load_manifest(path: pathlib.Path | None = None) -> dict[str, Verb]:
    """Parse the manifest file into an ordered mapping of verb name -> Verb.

    Args:
        path: Manifest file to read. Defaults to :func:`default_manifest_path`.

    Returns:
        An ordered dict (insertion order == file order) mapping verb name to
        its Verb record.

    Raises:
        FileNotFoundError: the manifest file does not exist at *path*.
        ManifestFormatError: the file exists but does not parse to a
            coherent verb list (wrong field count, or a duplicate verb
            name).
    """
    manifest_path = path or default_manifest_path()
    if not manifest_path.exists():
        raise FileNotFoundError(f"operator verb manifest not found: {manifest_path}")

    verbs: dict[str, Verb] = {}

    for lineno, raw_line in enumerate(
        manifest_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        fields = raw_line.split("\t")
        if fields[0] != "V":
            # Any other first field (blank line, comment line, stray text)
            # is not a data line -- ignored per the manifest's own parse rule.
            continue

        if len(fields) == _FIELD_COUNT_WITHOUT_IMPL:
            fields = fields + [None]
        elif len(fields) != _FIELD_COUNT:
            raise ManifestFormatError(
                f"{manifest_path}:{lineno}: V record has {len(fields)} "
                f"fields, expected {_FIELD_COUNT} (or "
                f"{_FIELD_COUNT_WITHOUT_IMPL} without the trailing impl "
                f"column): {raw_line!r}"
            )

        (
            _sigil,
            name,
            status,
            script,
            flags_raw,
            mutates_raw,
            idempotence,
            rest_method,
            rest_route,
            rest_body_model,
            verb_class,
            schema_ref,
            impl,
        ) = fields

        if name in verbs:
            raise ManifestFormatError(
                f"{manifest_path}:{lineno}: duplicate verb name {name!r}"
            )

        if status not in ("active", "planned"):
            raise ManifestFormatError(
                f"{manifest_path}:{lineno}: status must be 'active' or "
                f"'planned'; got {status!r}"
            )

        verbs[name] = Verb(
            name=name,
            status=status,
            script=_parse_optional(script),
            flags=_parse_flags(flags_raw),
            mutates=_parse_bool_or_none(mutates_raw),
            idempotence=idempotence,
            rest_method=rest_method,
            rest_route=rest_route,
            rest_body_model=rest_body_model,
            verb_class=verb_class,
            schema_ref=schema_ref,
            impl=impl.strip() if impl is not None else None,
        )

    return verbs


def verb_names(path: pathlib.Path | None = None) -> list[str]:
    """Return every declared verb name, in manifest (file) order."""
    return list(load_manifest(path).keys())


def get_verb(name: str, path: pathlib.Path | None = None) -> Verb:
    """Return the Verb record for *name*.

    Raises:
        KeyError: *name* is not declared in the manifest.
    """
    verbs = load_manifest(path)
    try:
        return verbs[name]
    except KeyError:
        raise KeyError(
            f"operator verb manifest has no verb named {name!r} "
            f"(declared verbs: {sorted(verbs)})"
        ) from None


# ---------------------------------------------------------------------------
# OPERATOR_VALID_FLAGS extraction
#
# Same extraction rule team/scripts/lint_api_parity.py's own
# _extract_operator_valid_flags() implements, kept here as the shared
# primitive so the reconciliation check and the manifest's own ``flags``
# column are always read the same way. lint_api_parity.py keeps its own
# copy (its flag-extraction and equivalence rules, and so its findings,
# stay exactly as they are) -- this is a parallel implementation, not a
# shared import.
# ---------------------------------------------------------------------------

_OPERATOR_VALID_FLAGS_RE = re.compile(r"OPERATOR_VALID_FLAGS\s*=\s*\(", re.MULTILINE)
_TOKEN_RE = re.compile(r"'([^']*)'|\"([^\"]*)\"|(\S+)")


def extract_operator_valid_flags(script_path: pathlib.Path) -> list[str]:
    """Return the flags declared in *script_path*'s OPERATOR_VALID_FLAGS array.

    Raises:
        ValueError: no OPERATOR_VALID_FLAGS=(...) declaration is found in
            the script's source. Callers should check
            NO_OPERATOR_VALID_FLAGS_ARRAY first for scripts known to parse
            their own arguments by hand.
    """
    source = script_path.read_text(encoding="utf-8", errors="replace")

    match = _OPERATOR_VALID_FLAGS_RE.search(source)
    if not match:
        raise ValueError(
            f"OPERATOR_VALID_FLAGS declaration not found in {script_path}"
        )

    start = match.end()
    depth = 1
    pos = start
    while pos < len(source) and depth > 0:
        ch = source[pos]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        pos += 1

    array_body = source[start : pos - 1]

    flags: list[str] = []
    for tm in _TOKEN_RE.finditer(array_body):
        token = tm.group(1) or tm.group(2) or tm.group(3)
        if token and not token.startswith("#"):
            flags.append(token)
    return flags
