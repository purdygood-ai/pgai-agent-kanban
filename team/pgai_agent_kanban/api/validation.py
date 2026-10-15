"""
validation.py — request-value checks shared by the operator API routers.

Two checks, one strict superset of the other:

``has_traversal`` refuses a caller-supplied value that could escape the
directory it is joined to (``..``, a path separator, their URL-encoded
forms, NUL). The logs and traces routers, the task README route and the
intake filename contract apply it.

``require_component`` is the confinement gate every ``project``, ``key``
and selector field passes before a route acts on it. It refuses everything
``has_traversal`` refuses and, in addition, every value that is safe as a
path but not as a single *name*: an empty value, a lone ``.``, the glob
metacharacters ``* ? [ ]`` (the item resolver globs on a key prefix),
control characters including newline (a value written into a one-line
sentinel), and a leading ``-`` (a value placed on a script's argv after
its flag would be parsed as a flag of its own). Valid project names, task
keys, bug/priority/requirement IDs, versions and role names contain none of
these, so they pass unchanged.

Both run BEFORE any filesystem access, subprocess or ops call.

The ops layer applies the same check again (``ops.resolve.resolve_item``,
``ops.projects`` and ``cm.cancel``), so a CLI script that reaches an ops
function refuses the value just as the route does; ``ComponentRefused.block``
gives those callers the refusal as the shared emitter's three-line block.
``control_character_fault`` is the control-character half on its own, for a
value that may legitimately hold a ``/`` (a path or URL written into a
config file) but must stay on one line.

Public API:
    has_traversal(value) -> bool
    require_component(value, field) -> str
    control_character_fault(value) -> str | None
    ComponentRefused            (raised by require_component)
    COMPONENT_RULE              (the rule, in words, for refusal messages)
"""

from __future__ import annotations

import unicodedata

from ..lib.error_contract import format_error_contract, parse_error_contract

__all__ = [
    "COMPONENT_RULE",
    "ComponentRefused",
    "control_character_fault",
    "has_traversal",
    "require_component",
]

# The rule require_component enforces, phrased for a refusal message.
COMPONENT_RULE = (
    "A project, key or selector value must be one plain name: not empty, "
    "not '.' or containing '..', with no '/', '\\', NUL, control character "
    "(newline included), glob character (* ? [ ]) or URL-encoded separator, "
    "and not starting with '-'."
)

# Glob metacharacters the resolver's prefix glob would interpret.
_GLOB_CHARS = frozenset("*?[]")

# Unicode categories refused as control characters: Cc covers C0 (newline,
# carriage return, tab, NUL ...), DEL and C1; Zl/Zp are the line and
# paragraph separators, which line-oriented readers also split on.
_CONTROL_CATEGORIES = frozenset({"Cc", "Zl", "Zp"})


def has_traversal(value: str) -> bool:
    """Return True when *value* could escape the directory it is joined to.

    A value is rejected when it contains any of:
      - ``..`` (a parent-directory segment)
      - ``/`` or ``\\`` (a path separator -- this also rejects every
        absolute path)
      - the URL-encoded forms ``%2F`` (slash), ``%5C`` (backslash) or
        ``%2E`` (dot), matched case-insensitively
      - a NUL byte, raw or encoded as ``%00``

    Args:
        value: The caller-supplied string, exactly as received.

    Returns:
        True if the value must be refused, False if it is safe to use as a
        single path component.
    """
    upper = value.upper()
    if ".." in value:
        return True
    if "/" in value or "\\" in value:
        return True
    if "%2F" in upper or "%5C" in upper or "%2E" in upper:
        return True
    if "\x00" in value or "%00" in upper:
        return True
    return False


class ComponentRefused(ValueError):
    """A request field is not a single plain name; raised by ``require_component``.

    Attributes:
        field:  The request field name the value arrived in (``project``,
                ``key``, ``agent`` ...).
        reason: What is wrong with the value, in words (``contains a glob
                character '*'``). Never echoes the whole value, which may
                carry control characters.
    """

    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
        super().__init__(f"'{field}' {reason}. {COMPONENT_RULE}")

    def error_key(self, verb: str) -> str:
        """Return the stable machine key for this refusal on *verb*.

        ``<verb>.<field>_invalid`` with the verb's hyphens as underscores --
        ``remove_project.project_invalid``, ``close.key_invalid``,
        ``status.project_invalid`` -- the same shape as the routers'
        ``<verb>.<field>_required`` key for a missing field.
        """
        return f"{verb.replace('-', '_')}.{self.field}_invalid"

    def contract(self, verb: str, remedy: str) -> dict[str, str]:
        """Return the dual-channel triple a route refuses this value with.

        Built through the shared emitter (``format_error_contract``, read
        back with ``parse_error_contract``), so the three fields are the
        house shape the CLI prints and a blank one is a ``ValueError``
        rather than a two-field refusal.

        Args:
            verb:   The route's verb name (``remove-project``, ``status``).
            remedy: A runnable next step -- the route's own corrected
                    request.

        Returns:
            ``{"error": str, "message": str, "remedy": str}``.
        """
        triple = parse_error_contract(self.block(verb, remedy))
        if triple is None:  # pragma: no cover -- format and parse are inverses
            raise ValueError("error contract round-trip failed for a component refusal")
        return triple

    def block(self, verb: str, remedy: str) -> str:
        """Return this refusal as the shared emitter's three-line block.

        The text an ops-layer refusal carries (``str(exc)`` of the OpsError
        it raises), so a CLI script prints it through the emitter and a
        route lifts the same ``error``/``message``/``remedy`` out of it.

        Args:
            verb:   The verb name (``resolve_item``, ``add_project``,
                    ``cancel_rc``); its hyphens become underscores in the key.
            remedy: A runnable next step, no placeholder.

        Returns:
            ``error: <verb>.<field>_invalid`` / ``message: ...`` /
            ``remedy: ...``, as ``format_error_contract`` writes it.
        """
        message = (
            f"{verb} refused the '{self.field}' field: the value {self.reason}. "
            f"{COMPONENT_RULE} Nothing was read, written or run."
        )
        return format_error_contract(self.error_key(verb), message, remedy)


def control_character_fault(value: str) -> str | None:
    """Return why *value* holds a control character, or None when it holds none.

    Refused: every C0 control (newline, carriage return, tab, NUL ...),
    DEL, the C1 range, and the Unicode line and paragraph separators --
    anything a line-oriented reader would split on or a terminal would act
    on. The reason names the first such character by code point and never
    echoes it.
    """
    for ch in value:
        if unicodedata.category(ch) in _CONTROL_CATEGORIES:
            return f"contains a control character (U+{ord(ch):04X})"
    return None


def _component_fault(value: str) -> str | None:
    """Return why *value* is not a single plain name, or None when it is one.

    The checks run from the most to the least specific reason, so a value
    with several faults is described by the one an operator most needs to
    see first.
    """
    if value == "":
        return "is empty"
    if "\x00" in value:
        return "contains a NUL byte"
    control = control_character_fault(value)
    if control is not None:
        return control
    if "/" in value or "\\" in value:
        return "contains a path separator ('/' or '\\')"
    if value == "." or ".." in value:
        return "names the current or a parent directory ('.' or '..')"
    if has_traversal(value):
        return "contains a URL-encoded separator, dot or NUL (%2F, %5C, %2E or %00)"
    for ch in value:
        if ch in _GLOB_CHARS:
            return f"contains a glob character '{ch}'"
    if value.startswith("-"):
        return "starts with '-', which a script would read as a flag"
    return None


def require_component(value: object, field: str) -> str:
    """Return *value* when it is one plain name, else raise ``ComponentRefused``.

    The single check every ``project``, ``key`` and selector request field
    passes before a route joins it into a path, globs on it, writes it into
    a sentinel or places it on a script's argv. Refused:

      - an empty value, or one that is not a string at all
      - ``/`` or ``\\`` (so every absolute path), ``..`` and a lone ``.``
      - NUL, and every control character (newline, carriage return, tab,
        DEL, the C1 range, and the Unicode line/paragraph separators)
      - the URL-encoded separators and dot ``has_traversal`` refuses
      - the glob metacharacters ``*``, ``?``, ``[`` and ``]``
      - a leading ``-``

    Args:
        value: The caller-supplied value, exactly as received.
        field: The request field it arrived in; named in the refusal.

    Returns:
        *value*, unchanged.

    Raises:
        ComponentRefused: *value* is not a single plain name.
    """
    fault = (
        f"is not a string (got {type(value).__name__})"
        if not isinstance(value, str)
        else _component_fault(value)
    )
    if fault is not None:
        # BLOCK-WORTHY (docs/blocking-site-census.md): an invariant by
        # design -- the value is about to become one path component, glob
        # prefix, sentinel line or argv value, and anything but a plain name
        # there reaches outside the kanban root or changes what runs. The
        # caller turns this into its dual-channel refusal (ComponentRefused.
        # contract) before anything is read, written or run.
        # CENSUS-ANCHOR: validation-not-component-01
        raise ComponentRefused(field, fault)
    return value
