"""team/pm-agent/lib/acceptance_policy.py
Targeted-test and pre-merge policy for materialized CODER and WRITER tickets.

A ticket's acceptance criteria and constraints often arrive carrying a
full gated-runner invocation (a test runner named without the option that
narrows it to a list of files). Worker tickets run only the tests for the
files they change; the full gated suites run once per RC in the TESTER
task. Before merging, a worker ticket also runs the project's cheap
whole-tree pre-merge checks. This module enforces both in code,
independent of how the plan text was worded.

Which runner, which option and which pre-merge command apply are the
project's own: they come from its project.cfg ``[testing]`` keys, which
the materializer reads and passes in as a ``RunnerConfig``. This module
holds no runner path or flag of its own; a project that configures
nothing gets no runner criterion, no runner constraint and no rewrite.

Pure Python, no I/O. The materializer calls ``apply_acceptance_policy``
while emitting each ticket and is responsible for narrating the result
and recording it in the ticket's ``## Notes``.

Configuration
-------------
``targeted_test_command``
    The command that runs only the named test files. ``{files}`` marks
    where the file list goes; without it the list goes at the end. The
    option written immediately before ``{files}`` (``--opt {files}`` or
    ``--opt={files}``) is the runner's targeted flag, and the word before
    that the runner. Several runners that take files the same way are
    joined with ``&&``, each part carrying its own ``{files}``; the first
    is the one the criterion spells out, and each further runner is named
    after it by its script name with the extension and any ``run-`` prefix
    and ``-tests`` suffix removed. A value whose ``&&`` parts do not all
    carry ``{files}`` is one command.
``pre_merge_command``
    The command a ticket runs once, whole-tree, before it merges.

Public API
----------
apply_acceptance_policy(role, acceptance_criteria, constraints, runner_config=None) -> PolicyResult
    For a CODER or WRITER role with a targeted command configured: every
    acceptance criterion or constraint that names a full run of a
    configured runner is replaced by ``runner_config.targeted_criterion()``,
    and ``runner_config.targeted_constraint()`` is appended to the
    constraints unless an identical constraint is already present. A
    runner with a targeted flag runs in full when the text names it
    without that flag. A runner whose template has no flag before
    ``{files}`` takes files as plain arguments, so it runs in full when
    the text names its script with no file list after it: the next word
    that is not an option is not a path, a file name, a glob or a
    ``<...>``/``{...}`` placeholder, or the code span closes first.

    With a pre-merge command configured, a criterion or constraint that
    names that command is never rewritten, however it is worded around
    the command; the command must appear whole and not be followed by a
    further option (``--pre-merge --base x`` is a different run).
    ``runner_config.pre_merge_criterion()`` is then placed last in the
    acceptance criteria, exactly once: every plan criterion naming the
    command is dropped and the canonical text appended after the
    plan-authored criteria, without recording a rewrite. An empty or
    absent key adds nothing. Any other role is a no-op. Applying the
    policy to its own output changes nothing.

format_policy_notes(result) -> str
    The ``## Notes`` paragraph recording a result's rewrites, or an empty
    string when nothing was rewritten.

RunnerConfig(targeted_command="", pre_merge_command="")
    One project's runner commands, as read from its project.cfg, with the
    criterion and constraint texts they produce.

invokes_full_runner(text, runner_config=None) -> bool
    True when *text* names a full run of a configured runner: without its
    targeted flag, or, for a runner with no flag, with no file list.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Roles whose tickets carry the targeted-test and pre-merge policy. TESTER
# runs the full gated suites by design; CM and HUMAN tickets never invoke a
# runner.
POLICY_ROLES = frozenset({"CODER", "WRITER"})

#: Where a targeted command template takes its test files.
FILES_PLACEHOLDER = "{files}"

#: What stands in for the file list in the rendered criterion.
FILES_DESCRIPTION = "<the test files this ticket adds or changes>"

#: Joins the templates of several runners in one targeted_test_command value.
RUNNER_SEPARATOR = "&&"

_WHITESPACE_RE = re.compile(r"\s+")

# A word that reads as a test-file argument: it holds a path separator or a
# glob, or ends in a file extension.
_FILE_ARG_RE = re.compile(r"[/*]|\.[A-Za-z0-9]+$")

# Punctuation that may trail a file argument in prose ("tests/x.py,").
_TRAILING_PUNCTUATION = ".,;:!?)]'\""

# Parts of a runner script name dropped when naming it in the criterion:
# the extension, a leading "run-" and a trailing "-tests".
_LABEL_EXTENSION_RE = re.compile(r"\.[A-Za-z0-9]+$")
_LABEL_PREFIX_RE = re.compile(r"^run[-_]", re.IGNORECASE)
_LABEL_SUFFIX_RE = re.compile(r"[-_]tests?$", re.IGNORECASE)

FIELD_ACCEPTANCE = "acceptance criterion"
FIELD_CONSTRAINT = "constraint"


def _normalize(text: str) -> str:
    """Collapse whitespace so equality ignores spacing and line wrapping."""
    return _WHITESPACE_RE.sub(" ", text).strip()


def _token_re(token: str, *, separators_interchangeable: bool) -> re.Pattern[str]:
    """A pattern matching *token* as a standalone word.

    The token may not be glued to a word character or a hyphen on either
    side. With *separators_interchangeable*, ``-`` and ``_`` in the token
    each match either character, so a hand-typed ``run_unit_tests.sh``
    still names ``run-unit-tests.sh``.
    """
    parts = []
    for ch in token:
        if separators_interchangeable and ch in "-_":
            parts.append("[-_]")
        else:
            parts.append(re.escape(ch))
    return re.compile(r"(?<![\w-])" + "".join(parts) + r"(?![\w-])", re.IGNORECASE)


@dataclass(frozen=True)
class TargetedRunner:
    """One runner of a targeted command.

    template: the command, always carrying ``{files}``.
    script:   the runner's name (the last path component of the word before
              the flag), or ``""`` when the template names none.
    flag:     the option written immediately before ``{files}``, or ``""``
              when the runner takes files as plain arguments.
    """

    template: str
    script: str
    flag: str

    @classmethod
    def parse(cls, template: str) -> TargetedRunner:
        """Read one command template into its runner and targeted flag."""
        text = _normalize(template)
        if FILES_PLACEHOLDER not in text:
            text = f"{text} {FILES_PLACEHOLDER}"
        tokens = text.split(FILES_PLACEHOLDER, 1)[0].split()
        flag = ""
        if tokens and tokens[-1].startswith("-"):
            flag = tokens.pop().rstrip("=")
        script = tokens[-1].rsplit("/", 1)[-1] if tokens else ""
        return cls(template=text, script=script, flag=flag)

    def rendered(self) -> str:
        """The command with the file list described in words."""
        return self.template.replace(FILES_PLACEHOLDER, FILES_DESCRIPTION)

    def label(self) -> str:
        """The runner's name as the criterion speaks of it."""
        name = _LABEL_EXTENSION_RE.sub("", self.script)
        name = _LABEL_PREFIX_RE.sub("", name)
        return _LABEL_SUFFIX_RE.sub("", name) or self.script

    def names_full_run(self, text: str) -> bool:
        """True when *text* names this runner as a full, untargeted run.

        A runner with a targeted flag runs in full when *text* names it
        without that flag anywhere. A runner with no flag runs in full when
        *text* names it and no mention of it is followed by a file list.
        """
        if not self.script:
            return False
        script_re = _token_re(self.script, separators_interchangeable=True)
        if not script_re.search(text):
            return False
        if self.flag:
            return not _token_re(self.flag, separators_interchangeable=False).search(text)
        return not any(_lists_files(text[m.end() :]) for m in script_re.finditer(text))


def _lists_files(tail: str) -> bool:
    """True when *tail*, the text right after a runner's name, opens with files.

    Options (words starting with ``-``) are skipped. The first other word
    decides: a path, a file name, a glob, or a ``<...>``/``{...}``
    placeholder is a file list; anything else, or a closing backtick
    before any such word, means the runner was named with no files.
    """
    for word in tail.split():
        if word.startswith("`"):
            return False
        if word.startswith(("<", "{")):
            return True
        closes_span = "`" in word
        word = word.split("`", 1)[0].rstrip(_TRAILING_PUNCTUATION)
        if word.startswith("-"):
            if closes_span:
                return False
            continue
        return bool(word) and bool(_FILE_ARG_RE.search(word))
    return False


@dataclass(frozen=True)
class RunnerConfig:
    """One project's runner commands, from its project.cfg ``[testing]``.

    targeted_command:  ``targeted_test_command`` — see the module docstring.
    pre_merge_command: ``pre_merge_command``.

    Either may be empty, which means the matching criterion is not added.
    """

    targeted_command: str = ""
    pre_merge_command: str = ""

    def runners(self) -> tuple:
        """The configured targeted runners, first one first."""
        value = (self.targeted_command or "").strip()
        if not value:
            return ()
        parts = [p.strip() for p in value.split(RUNNER_SEPARATOR)]
        if len(parts) < 2 or not all(FILES_PLACEHOLDER in p for p in parts):
            parts = [value]
        return tuple(TargetedRunner.parse(p) for p in parts)

    def targeted_criterion(self) -> str:
        """The targeted-run acceptance criterion, or ``""`` when unconfigured."""
        runners = self.runners()
        if not runners:
            return ""
        text = f"`{runners[0].rendered()}`"
        for runner in runners[1:]:
            label = runner.label()
            text += f" (and the {label} runner the same way, if {label} tests were touched)"
        return f"{text} exits 0."

    def targeted_constraint(self) -> str:
        """The targeted-run constraint, or ``""`` when unconfigured."""
        runners = self.runners()
        if not runners:
            return ""
        how = runners[0].flag or runners[0].rendered()
        return (
            f"Run only the tests for the files you changed using `{how}`. "
            "The full gated suites run once per RC in the TESTER task."
        )

    def pre_merge_criterion(self) -> str:
        """The pre-merge acceptance criterion, or ``""`` when unconfigured."""
        command = _normalize(self.pre_merge_command or "")
        if not command:
            return ""
        return f"`{command}` exits 0 before merging."

    def names_pre_merge(self, text) -> bool:
        """True when *text* names the configured pre-merge command.

        The command's words must appear in order, separated by any
        whitespace, not glued to a longer word and not followed by a
        further option; the wording around the command does not matter.
        Always False when no pre-merge command is configured.
        """
        words = _normalize(self.pre_merge_command or "").split()
        if not words or not isinstance(text, str):
            return False
        pattern = r"(?<![\w/-])" + r"\s+".join(re.escape(w) for w in words) + r"(?![\w-])(?!\s*=)(?!\s+--?\w)"
        return re.search(pattern, text, re.IGNORECASE) is not None


@dataclass(frozen=True)
class PolicyRewrite:
    """One criterion or constraint the policy replaced.

    field:      FIELD_ACCEPTANCE or FIELD_CONSTRAINT.
    original:   the text as the plan supplied it.
    merged:     True when the replacement duplicated an item already in
                the list, so the item was dropped rather than repeated.
    """

    field: str
    original: str
    merged: bool = False


@dataclass(frozen=True)
class PolicyResult:
    """Outcome of applying the policy to one ticket.

    acceptance_criteria / constraints: the lists to render in the README.
    rewrites:          every replacement made, in input order (acceptance
                       criteria first, then constraints).
    constraint_added:  True when the targeted-run constraint was appended.
    """

    acceptance_criteria: list = field(default_factory=list)
    constraints: list = field(default_factory=list)
    rewrites: list = field(default_factory=list)
    constraint_added: bool = False


def invokes_full_runner(text, runner_config=None) -> bool:
    """Return True when *text* names a full run of a configured runner."""
    if not isinstance(text, str) or runner_config is None:
        return False
    return any(r.names_full_run(text) for r in runner_config.runners())


def _is_same_text(item, canonical: str) -> bool:
    """Return True when *item* is *canonical*, ignoring spacing."""
    return bool(canonical) and isinstance(item, str) and _normalize(item) == _normalize(canonical)


def _rewrite_items(items, field_name, runner_config, targeted):
    """Rewrite full-runner items in *items*; return (new_items, rewrites).

    An item naming the pre-merge command is passed through as written: it
    may name a runner without its targeted flag, but asks for the
    pre-merge run, not a full suite.
    """
    out = []
    seen = set()
    rewrites = []
    for item in items:
        if invokes_full_runner(item, runner_config) and not runner_config.names_pre_merge(item):
            merged = _normalize(targeted) in seen
            rewrites.append(PolicyRewrite(field=field_name, original=item, merged=merged))
            if merged:
                continue
            item = targeted
        out.append(item)
        if isinstance(item, str):
            seen.add(_normalize(item))
    return out, rewrites


def apply_acceptance_policy(role, acceptance_criteria, constraints, runner_config=None) -> PolicyResult:
    """Apply the targeted-test and pre-merge policy to one ticket.

    *role* is matched case-insensitively against POLICY_ROLES. For any other
    role, or with no runner configured, the lists come back as unchanged
    copies with no rewrites, so a ticket outside the policy renders exactly
    as it would without it.

    *runner_config* is the plan's project's ``RunnerConfig``; ``None`` is
    the same as an empty one. With a pre-merge command configured, a
    policy role's acceptance criteria always end with exactly one
    pre-merge criterion, which replaces every plan criterion naming the
    command. Placing it is not a rewrite: it is not listed in
    ``rewrites`` and adds nothing to the notes.

    The input lists are never mutated. ``None`` is treated as an empty list.
    """
    acceptance = list(acceptance_criteria or [])
    constraint_list = list(constraints or [])
    config = runner_config or RunnerConfig()

    if str(role or "").strip().upper() not in POLICY_ROLES:
        return PolicyResult(acceptance_criteria=acceptance, constraints=constraint_list)

    targeted = config.targeted_criterion()
    pre_merge = config.pre_merge_criterion()

    acc_rewrites = con_rewrites = []
    if targeted:
        acceptance, acc_rewrites = _rewrite_items(
            acceptance,
            FIELD_ACCEPTANCE,
            config,
            targeted,
        )
        constraint_list, con_rewrites = _rewrite_items(
            constraint_list,
            FIELD_CONSTRAINT,
            config,
            targeted,
        )

    if pre_merge:
        # Last and exactly once, whether or not (and wherever, however
        # worded) the plan wrote it.
        acceptance = [c for c in acceptance if not config.names_pre_merge(c)]
        acceptance.append(pre_merge)

    constraint_added = False
    fixed = config.targeted_constraint()
    if fixed and not any(_is_same_text(c, fixed) for c in constraint_list):
        constraint_list.append(fixed)
        constraint_added = True

    return PolicyResult(
        acceptance_criteria=acceptance,
        constraints=constraint_list,
        rewrites=acc_rewrites + con_rewrites,
        constraint_added=constraint_added,
    )


def format_policy_notes(result: PolicyResult) -> str:
    """Render *result*'s rewrites as a ``## Notes`` paragraph.

    Returns an empty string when the result holds no rewrites, so a ticket
    the policy did not rewrite keeps its notes exactly as supplied.
    """
    if not result.rewrites:
        return ""
    lines = ["Targeted-test policy: full gated-runner invocations were rewritten " "to the project's targeted run."]
    for rw in result.rewrites:
        suffix = " (duplicate of an earlier rewrite, dropped)" if rw.merged else ""
        lines.append(f'- {rw.field}{suffix}: "{_normalize(rw.original)}"')
    return "\n".join(lines)
