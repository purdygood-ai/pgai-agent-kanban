"""
skills_install.py — read, compare, install and list provider CLI skills.

A *skill* is a directory ``<skill-name>/`` holding a ``SKILL.md`` whose YAML
front matter carries ``name`` (equal to the directory name) and
``description`` (non-empty, under 500 characters), followed by Markdown, and
optionally ``scripts/``, ``references/`` and ``assets/``. Claude Code and
Codex both read skills from a *provider home*, resolved by
``pgai_agent_kanban.lib.provider_home`` (the one provider-home rule, shared
with the agent-wrapper deploy):

    claude  $CLAUDE_CONFIG_DIR/skills when CLAUDE_CONFIG_DIR is set and
            non-empty, otherwise $HOME/.claude/skills
    codex   $HOME/.codex/skills (any other provider: $HOME/.<provider>/skills)

This is the one implementation of skill listing, comparison and install
(the byte comparison itself is ``pgai_agent_kanban.lib.tree_compare``'s):
the install-skills verb, the upgrade's skill refresh and the operator API's
skills route all call it, in process or through the command line below.

Source layouts
--------------
A source directory is either

* **provider layout** — ``<provider>/<skill-name>/SKILL.md`` (the staging
  directory ``$KANBAN_ROOT/skills`` and the repository's ``skills/``); each
  requested provider reads its own subdirectory, a missing one has no
  skills, and a directory named for no known provider is not read; or
* **flat layout** — ``<skill-name>/SKILL.md`` directly under the source. A
  source is flat when any directory directly under it holds a ``SKILL.md``.
  A flat source belongs to exactly one provider, so it is refused unless one
  provider (not ``all``) is named.

Only directories holding a ``SKILL.md`` are skills; anything else, and any
entry whose name starts with ``.``, is ignored.

States
------
Each source skill is compared with ``<home>/<skill-name>/``:

* ``new``      — no copy in the home;
* ``same``     — every directory and file of the source skill exists in the
  home copy and every file is byte-identical;
* ``changed``  — any source file is missing or differs in the home copy
  (any file: ``scripts/`` and ``references/`` count, not only ``SKILL.md``),
  or a source directory is missing there;
* ``invalid``  — the source skill's ``SKILL.md`` breaks a front-matter rule;
  it is listed with the rule it broke and never installed.

A file that exists only in the home copy is not a difference: installing
never removes anything, so counting it would leave a skill ``changed``
forever after a forced install.

Install
-------
``install_skills`` copies each ``new`` skill whole, leaves ``same`` skills
alone (writing nothing), and leaves a ``changed`` skill untouched and
reported unless *force* is given, in which case every differing or missing
file is written over the home copy. A skill, file or directory that exists
only in the home is never removed. Each file lands through a temporary file
in its target directory and an atomic rename, keeping the source file's
permission bits.

Shipped skills
--------------
Given the shipped set, each listed skill is marked ``shipped`` or
``operator-added``. ``read_shipped_manifest`` reads the set from a file:
one entry per line, blank lines and ``#`` comments ignored; the entry is the
line's last whitespace-separated field (so a ``sha256sum``-style
``<digest>  <path>`` line works), read as ``[skills/]<provider>/<name>[/...]``;
a bare ``<name>`` counts as shipped for every provider.

A kanban root keeps its staging directory at ``STAGING_DIR`` (``skills``)
and the shipped set install recorded at ``SHIPPED_MANIFEST``
(``.installed-skills``), both directly under the root.

``validate_shipped_skill`` adds the repository hygiene rule to the
front-matter rules: no file of the skill may carry an operator home-directory
literal or a private ssh host alias, as ``team/scripts/lint_hygiene.py``'s
operator-literal class defines them (that lint is loaded and its detector
called; its patterns are not copied here). Operator-added skills are never
held to this rule.

Command line::

    python3 -m pgai_agent_kanban.lib.skills_install list [--json] [OPTIONS]
    python3 -m pgai_agent_kanban.lib.skills_install install [--force] [--dry-run] [OPTIONS]
    python3 -m pgai_agent_kanban.lib.skills_install home PROVIDER

    python3 team/pgai_agent_kanban/lib/skills_install.py ...   (same, by path)

``list_skills`` and ``install_skills`` are aliases of ``list`` and
``install``, named for the function each verb runs, so an operator script
calls ``pp_run_ops pgai_agent_kanban.lib.skills_install list_skills ...``
the way every other module-backed verb names its function.

OPTIONS (list and install):
    --dir DIR               source directory (default $KANBAN_ROOT/skills)
    --provider P            claude, codex or all (default all)
    --claude-home DIR       override the claude skill home
    --codex-home DIR        override the codex skill home
    --shipped-manifest FILE mark each skill shipped or operator-added
    --skip-missing-cli      skip, with a one-line notice, each provider whose
                            CLI binary is not on PATH
    --missing-ok            read --dir and --shipped-manifest as the operator
                            API's skills route reads a kanban root: no source
                            directory lists every provider with no skills,
                            and no manifest file is an empty shipped set
install only:
    --force                 overwrite a changed skill in a provider home
    --dry-run               print the install plan; write nothing
    --rerun-command CMD     the command that re-runs the caller, used in the
                            remedy printed when a changed skill is left

Exit codes: 0 everything installed or already the same (``list`` and
``home`` always, after a usage check); 1 a file could not be written; 2
usage error; 3 a changed skill was left unwritten because ``--force`` was
not given (``--dry-run`` predicts the same code). An invalid skill is
reported on stderr and in the output, is not installed, and does not change
the exit code.

Public API:
    PROVIDERS, STAGING_DIR, SHIPPED_MANIFEST, STATE_*, ORIGIN_*, RULE_*, ACTION_*, EXIT_*
    SkillInvalid, SkillsUsageError, SkillsWriteError
    Skill, SkillEntry, ProviderListing, SkillsListing, SkillOutcome, InstallReport
    provider_skills_home(provider, env=None) -> Path
    parse_front_matter(text) -> dict
    load_skill(skill_dir) -> Skill
    operator_literal_findings(text, path=...) -> list[tuple[str, str, int]]
    validate_shipped_skill(skill_dir) -> Skill
    source_skill_dirs(source_dir, providers) -> dict[str, list[Path]]
    compare_skill_dir(source, installed) -> str
    read_shipped_manifest(path, *, missing_ok=False) -> dict[str, frozenset[str]]
    providers_missing_cli(providers, path=None) -> list[str]
    list_skills(source_dir, *, provider="all", homes=None, shipped=None,
                exclude=(), env=None, missing_ok=False) -> SkillsListing
    install_skills(listing, *, force=False, dry_run=False) -> InstallReport
    render_list_json(listing) -> str
    render_list_text(listing) -> list[str]
    render_install_text(report) -> list[str]
    main(argv=None) -> int
"""

from __future__ import annotations

import argparse
import dataclasses
import importlib.util
import json
import os
import pathlib
import shlex
import shutil
import sys
import tempfile
from collections.abc import Iterable, Mapping, Sequence

# Started by file path (python3 .../lib/skills_install.py) there is no parent
# package: put the package root this file sits in -- the directory holding
# pgai_agent_kanban/ -- at the front of sys.path so the imports below resolve
# from the same tree, never another. Imported as a package member, sys.path
# is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

import yaml  # noqa: E402

from pgai_agent_kanban.env import resolve_kanban_root  # noqa: E402
from pgai_agent_kanban.lib.error_contract import emit_error_contract, module_file_command  # noqa: E402
from pgai_agent_kanban.lib.provider_home import provider_home_dir  # noqa: E402
from pgai_agent_kanban.lib.subcommand_cli import subcommand_main  # noqa: E402
from pgai_agent_kanban.lib.tree_compare import tree_differing_paths, tree_entries  # noqa: E402

__all__ = [
    "ACTION_INSTALLED",
    "ACTION_LEFT_CHANGED",
    "ACTION_OVERWRITTEN",
    "ACTION_SKIPPED_INVALID",
    "ACTION_UNCHANGED",
    "DESCRIPTION_LIMIT",
    "EXIT_CHANGED_LEFT",
    "EXIT_OK",
    "EXIT_USAGE",
    "EXIT_WRITE_FAILED",
    "InstallReport",
    "ORIGIN_OPERATOR",
    "ORIGIN_SHIPPED",
    "PROVIDERS",
    "ProviderListing",
    "RULE_DESCRIPTION_MISSING",
    "RULE_DESCRIPTION_TOO_LONG",
    "RULE_FRONT_MATTER_INVALID",
    "RULE_FRONT_MATTER_MISSING",
    "RULE_NAME_MISMATCH",
    "RULE_NAME_MISSING",
    "RULE_OPERATOR_LITERAL",
    "RULE_SKILL_FILE_UNREADABLE",
    "SHIPPED_MANIFEST",
    "STAGING_DIR",
    "STATE_CHANGED",
    "STATE_INVALID",
    "STATE_NEW",
    "STATE_SAME",
    "Skill",
    "SkillEntry",
    "SkillInvalid",
    "SkillOutcome",
    "SkillsListing",
    "SkillsUsageError",
    "SkillsWriteError",
    "compare_skill_dir",
    "install_skills",
    "list_skills",
    "load_skill",
    "main",
    "operator_literal_findings",
    "parse_front_matter",
    "provider_skills_home",
    "providers_missing_cli",
    "read_shipped_manifest",
    "render_install_text",
    "render_list_json",
    "render_list_text",
    "source_skill_dirs",
    "validate_shipped_skill",
]

PROVIDERS: tuple[str, ...] = ("claude", "codex")
SKILL_FILE = "SKILL.md"
# Directly under a kanban root: the staging directory install deposits the
# shipped skills into, and the file recording which skills it shipped.
STAGING_DIR = "skills"
SHIPPED_MANIFEST = ".installed-skills"
# "under 500 characters": a description of 500 characters or more is refused.
DESCRIPTION_LIMIT = 500

STATE_NEW = "new"
STATE_SAME = "same"
STATE_CHANGED = "changed"
STATE_INVALID = "invalid"

ORIGIN_SHIPPED = "shipped"
ORIGIN_OPERATOR = "operator-added"

RULE_SKILL_FILE_UNREADABLE = "skill-file-unreadable"
RULE_FRONT_MATTER_MISSING = "front-matter-missing"
RULE_FRONT_MATTER_INVALID = "front-matter-invalid"
RULE_NAME_MISSING = "name-missing"
RULE_NAME_MISMATCH = "name-mismatch"
RULE_DESCRIPTION_MISSING = "description-missing"
RULE_DESCRIPTION_TOO_LONG = "description-too-long"
RULE_OPERATOR_LITERAL = "operator-literal"

EXIT_OK = 0
EXIT_WRITE_FAILED = 1
EXIT_USAGE = 2
EXIT_CHANGED_LEFT = 3

ACTION_INSTALLED = "installed"
ACTION_OVERWRITTEN = "overwritten"
ACTION_UNCHANGED = "unchanged"
ACTION_LEFT_CHANGED = "left-changed"
ACTION_SKIPPED_INVALID = "skipped-invalid"

# A bare manifest entry (no provider segment) is shipped for every provider.
_ANY_PROVIDER = "*"

# The repository hygiene lint, self-located: team/scripts/ in the dev tree and
# scripts/ in the live install both sit two levels above this file's lib/.
_LINT_HYGIENE_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "lint_hygiene.py"
_LINT_HYGIENE_MODULE_NAME = "_skills_install_lint_hygiene"
_TEMP_PREFIX = ".skills-install-"
_OWN_FILE = pathlib.Path(__file__).resolve()


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SkillInvalid(ValueError):
    """A skill broke one validation rule.

    Attributes:
        rule:   the rule's stable key (one of the ``RULE_*`` constants).
        path:   the skill directory.
        detail: what was found, in plain words.
    """

    def __init__(self, rule: str, path: str | os.PathLike[str], detail: str) -> None:
        self.rule = rule
        self.path = pathlib.Path(path)
        self.detail = detail
        super().__init__(f"{self.path}: {rule}: {detail}")


class SkillsUsageError(ValueError):
    """The request cannot be served as given (a usage error, exit 2).

    Carries the dual-channel triple a surface prints or returns: a stable
    ``error`` key, a plain-language ``message`` and a runnable ``remedy``.
    """

    def __init__(self, error: str, message: str, remedy: str) -> None:
        self.error = error
        self.message = message
        self.remedy = remedy
        super().__init__(message)


class SkillsWriteError(OSError):
    """A file of a skill could not be written into a provider home."""

    def __init__(self, path: str | os.PathLike[str], cause: OSError) -> None:
        self.path = pathlib.Path(path)
        self.cause = cause
        super().__init__(f"could not write {self.path}: {cause}")


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Skill:
    """A skill whose SKILL.md passed the front-matter rules."""

    name: str
    description: str
    path: pathlib.Path


@dataclasses.dataclass(frozen=True)
class SkillEntry:
    """One source skill compared with its provider home.

    ``origin`` is None when no shipped set was given; ``rule`` and
    ``detail`` are set only for an ``invalid`` skill.
    """

    provider: str
    name: str
    source: pathlib.Path
    installed: pathlib.Path
    state: str
    origin: str | None = None
    rule: str | None = None
    detail: str | None = None

    def to_dict(self) -> dict:
        """The entry as the list output renders it (no paths)."""
        out: dict = {"name": self.name, "state": self.state}
        if self.origin is not None:
            out["origin"] = self.origin
        if self.rule is not None:
            out["error"] = self.rule
            out["message"] = self.detail
        return out


@dataclasses.dataclass(frozen=True)
class ProviderListing:
    """Every source skill of one provider, compared with that provider's home."""

    provider: str
    home: pathlib.Path
    entries: tuple[SkillEntry, ...]

    def to_dict(self) -> dict:
        """The provider as the list output renders it: its ``home`` path,
        ``provider`` name and ``skills`` (each entry's :meth:`SkillEntry.to_dict`)."""
        return {
            "home": str(self.home),
            "provider": self.provider,
            "skills": [e.to_dict() for e in self.entries],
        }


@dataclasses.dataclass(frozen=True)
class SkillsListing:
    """The whole list: the source directory and one listing per provider,
    providers in ``PROVIDERS`` order, skills sorted by name."""

    source: pathlib.Path
    providers: tuple[ProviderListing, ...]

    def to_dict(self) -> dict:
        """The whole list as JSON-ready data: ``source`` path and ``providers``
        (each :meth:`ProviderListing.to_dict`) -- the object ``--list --json``
        prints and GET /skills returns."""
        return {
            "providers": [p.to_dict() for p in self.providers],
            "source": str(self.source),
        }


@dataclasses.dataclass(frozen=True)
class SkillOutcome:
    """What install did (or, in a dry run, would do) with one skill.

    ``written`` lists the skill-relative paths of the files written (or that
    would be written), sorted.
    """

    entry: SkillEntry
    action: str
    written: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class InstallReport:
    """The outcome of one install run."""

    listing: SkillsListing
    outcomes: tuple[SkillOutcome, ...]
    force: bool
    dry_run: bool

    @property
    def left_changed(self) -> tuple[SkillOutcome, ...]:
        """The changed skills left unwritten because force was not given."""
        return tuple(o for o in self.outcomes if o.action == ACTION_LEFT_CHANGED)

    @property
    def invalid(self) -> tuple[SkillOutcome, ...]:
        """The invalid source skills, never installed."""
        return tuple(o for o in self.outcomes if o.action == ACTION_SKIPPED_INVALID)

    @property
    def exit_code(self) -> int:
        """``EXIT_CHANGED_LEFT`` when a changed skill was left, else ``EXIT_OK``."""
        return EXIT_CHANGED_LEFT if self.left_changed else EXIT_OK


# ---------------------------------------------------------------------------
# Provider homes
# ---------------------------------------------------------------------------


def provider_skills_home(provider: str, env: Mapping[str, str] | None = None) -> pathlib.Path:
    """Return the directory *provider*'s CLI reads skills from.

    :func:`pgai_agent_kanban.lib.provider_home.provider_home_dir` with kind
    ``skills`` -- the rule the agent-wrapper deploy resolves through too:
    claude reads ``$CLAUDE_CONFIG_DIR/skills`` when ``CLAUDE_CONFIG_DIR`` is
    set and non-empty, otherwise ``$HOME/.claude/skills``; every other
    provider reads ``$HOME/.<provider>/skills``.

    Args:
        provider: provider name, e.g. ``claude`` or ``codex``.
        env:      environment to read (default ``os.environ``). ``HOME``
                  falls back to the account's home directory when unset.
    """
    return provider_home_dir(provider, "skills", env)


def providers_missing_cli(providers: Iterable[str], path: str | None = None) -> list[str]:
    """Return the providers in *providers* whose CLI binary (named for the
    provider) is not found on *path* (default ``$PATH``)."""
    return [p for p in providers if shutil.which(p, path=path) is None]


def _requested_providers(provider: str) -> tuple[str, ...]:
    """``all`` is every known provider; a known name is itself."""
    if provider == "all":
        return PROVIDERS
    if provider in PROVIDERS:
        return (provider,)
    raise SkillsUsageError(
        "skills_install.unknown_provider",
        f"provider {provider!r} is not one of: {', '.join(PROVIDERS)}, all.",
        module_file_command(_OWN_FILE, "list", "--provider", "all"),
    )


# ---------------------------------------------------------------------------
# Parsing and validation
# ---------------------------------------------------------------------------


def parse_front_matter(text: str) -> dict:
    """Return the YAML front matter of a SKILL.md as a dict.

    The file must open with a ``---`` line (a byte-order mark and trailing
    whitespace are tolerated) and the front matter ends at the next line
    that is ``---`` or ``...``.

    Raises:
        SkillInvalid: ``front-matter-missing`` when there is no opening or
            closing line, ``front-matter-invalid`` when the block is not a
            YAML mapping. The path on the error is empty; ``load_skill``
            raises with the skill's own path.
    """
    lines = text.lstrip("﻿").splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillInvalid(RULE_FRONT_MATTER_MISSING, "", "SKILL.md does not open with a '---' front-matter line")
    for idx in range(1, len(lines)):
        if lines[idx].strip() in ("---", "..."):
            block = "\n".join(lines[1:idx])
            break
    else:
        raise SkillInvalid(RULE_FRONT_MATTER_MISSING, "", "the front matter has no closing '---' line")
    try:
        data = yaml.safe_load(block) if block.strip() else {}
    except yaml.YAMLError as exc:
        detail = " ".join(str(exc).split())
        raise SkillInvalid(RULE_FRONT_MATTER_INVALID, "", f"the front matter is not valid YAML ({detail})") from exc
    if not isinstance(data, dict):
        raise SkillInvalid(RULE_FRONT_MATTER_INVALID, "", "the front matter is not a YAML mapping of keys to values")
    return data


def load_skill(skill_dir: str | os.PathLike[str]) -> Skill:
    """Read *skill_dir*/SKILL.md and check the front-matter rules.

    Rules, each refused with its key: SKILL.md readable as UTF-8
    (``skill-file-unreadable``); front matter present (``front-matter-missing``)
    and a YAML mapping (``front-matter-invalid``); ``name`` present
    (``name-missing``) and equal to the directory name (``name-mismatch``);
    ``description`` present and non-blank (``description-missing``) and
    under ``DESCRIPTION_LIMIT`` characters (``description-too-long``).

    Raises:
        SkillInvalid: naming the first rule the skill breaks.
    """
    skill_dir = pathlib.Path(skill_dir)
    try:
        text = (skill_dir / SKILL_FILE).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SkillInvalid(RULE_SKILL_FILE_UNREADABLE, skill_dir, f"{SKILL_FILE} could not be read ({exc})") from exc
    try:
        data = parse_front_matter(text)
    except SkillInvalid as exc:
        raise SkillInvalid(exc.rule, skill_dir, exc.detail) from exc
    name = data.get("name")
    if name is None or (isinstance(name, str) and not name.strip()):
        raise SkillInvalid(RULE_NAME_MISSING, skill_dir, "the front matter has no 'name'")
    if str(name) != skill_dir.name:
        raise SkillInvalid(
            RULE_NAME_MISMATCH, skill_dir, f"'name' is {str(name)!r} but the directory is {skill_dir.name!r}"
        )
    description = data.get("description")
    if not isinstance(description, str) or not description.strip():
        raise SkillInvalid(RULE_DESCRIPTION_MISSING, skill_dir, "the front matter has no non-empty 'description'")
    if len(description) >= DESCRIPTION_LIMIT:
        raise SkillInvalid(
            RULE_DESCRIPTION_TOO_LONG,
            skill_dir,
            f"'description' is {len(description)} characters; it must be under {DESCRIPTION_LIMIT}",
        )
    return Skill(name=str(name), description=description, path=skill_dir)


def _lint_hygiene():
    """Load ``lint_hygiene.py`` from this tree once and return the module.

    Raises:
        RuntimeError: the lint is not where this tree keeps it.
    """
    cached = sys.modules.get(_LINT_HYGIENE_MODULE_NAME)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(_LINT_HYGIENE_MODULE_NAME, _LINT_HYGIENE_SCRIPT)
    if spec is None or spec.loader is None or not _LINT_HYGIENE_SCRIPT.is_file():
        raise RuntimeError(f"the hygiene lint is missing at {_LINT_HYGIENE_SCRIPT}; the shipped-skill check needs it")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_LINT_HYGIENE_MODULE_NAME] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(_LINT_HYGIENE_MODULE_NAME, None)
        raise
    return module


def operator_literal_findings(text: str, path: str | os.PathLike[str] = SKILL_FILE) -> list[tuple[str, str, int]]:
    """Return ``(label, token, line_number)`` for each operator literal in
    *text*, as ``lint_hygiene.py``'s operator-literal class finds them: label
    ``home-path`` for a ``/home/<account>`` path naming a real account,
    ``ssh-host-alias`` for a ``git@<host>:`` remote whose host is a private
    alias, and ``operator-handle`` for an operator's forge login outside the
    organisation name. At most one finding per line per label."""
    lint = _lint_hygiene()
    found = lint._scan_operator_literal_lines(RULE_OPERATOR_LITERAL, pathlib.Path(path), text.splitlines())
    return [(f.label, f.token, f.lineno) for f in found]


def validate_shipped_skill(skill_dir: str | os.PathLike[str]) -> Skill:
    """Check a skill the repository ships: the front-matter rules of
    :func:`load_skill`, then the hygiene rule — no file of the skill may
    carry an operator literal (:func:`operator_literal_findings`).

    Raises:
        SkillInvalid: naming the first rule broken; for the hygiene rule the
            rule is ``operator-literal`` and the detail names the file, the
            line, the lint label and the literal.
    """
    skill = load_skill(skill_dir)
    _dirs, files = tree_entries(skill.path)
    for rel in files:
        try:
            text = (skill.path / rel).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue  # a binary asset carries no prose to check
        for label, token, lineno in operator_literal_findings(text, skill.path / rel):
            raise SkillInvalid(
                RULE_OPERATOR_LITERAL,
                skill.path,
                f"{rel}:{lineno}: {label} {token!r} names one operator's machine; "
                "write $HOME/... or ~/... and git@github.com: instead",
            )
    return skill


# ---------------------------------------------------------------------------
# Source enumeration and shipped set
# ---------------------------------------------------------------------------


def _skill_children(directory: pathlib.Path) -> list[pathlib.Path]:
    """The directories directly under *directory* that hold a SKILL.md,
    sorted by name; dot-named entries are skipped."""
    if not directory.is_dir():
        return []
    return sorted(
        (c for c in directory.iterdir() if not c.name.startswith(".") and c.is_dir() and (c / SKILL_FILE).is_file()),
        key=lambda c: c.name,
    )


def source_skill_dirs(source_dir: str | os.PathLike[str], providers: Sequence[str]) -> dict[str, list[pathlib.Path]]:
    """Return each provider's source skill directories, sorted by name.

    See the module docstring for the two layouts.

    Raises:
        SkillsUsageError: the source is not a directory
            (``skills_install.source_missing``), or it is flat and not
            exactly one provider was requested
            (``skills_install.flat_needs_one_provider``).
    """
    source = pathlib.Path(source_dir)
    if not source.is_dir():
        raise SkillsUsageError(
            "skills_install.source_missing",
            f"the skills source directory {source} does not exist or is not a directory.",
            f"ls -ld {shlex.quote(str(source))}",
        )
    flat = _skill_children(source)
    if flat:
        if len(providers) != 1:
            names = ", ".join(c.name for c in flat[:3])
            raise SkillsUsageError(
                "skills_install.flat_needs_one_provider",
                f"{source} holds skills directly ({names}), so it belongs to one provider; "
                f"name that provider instead of {len(providers)}.",
                module_file_command(_OWN_FILE, "list", "--dir", str(source), "--provider", PROVIDERS[0]),
            )
        return {providers[0]: flat}
    return {p: _skill_children(source / p) for p in providers}


def read_shipped_manifest(path: str | os.PathLike[str], *, missing_ok: bool = False) -> dict[str, frozenset[str]]:
    """Read the shipped set from *path*: ``{provider: {skill-name, ...}}``,
    with key ``"*"`` for bare names shipped for every provider. The line
    format is in the module docstring.

    With *missing_ok*, an absent file is an empty shipped set (nothing was
    recorded as shipped, so every listed skill is operator-added).

    Raises:
        SkillsUsageError: the file cannot be read
            (``skills_install.shipped_manifest_unreadable``).
    """
    path = pathlib.Path(path)
    if missing_ok and not path.exists() and not path.is_symlink():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SkillsUsageError(
            "skills_install.shipped_manifest_unreadable",
            f"the shipped-skills manifest {path} could not be read ({exc}).",
            f"ls -l {shlex.quote(str(path))}",
        ) from exc
    shipped: dict[str, set[str]] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p for p in line.split()[-1].split("/") if p and p != "."]
        if parts and parts[0] == "skills" and len(parts) > 2:
            parts = parts[1:]
        if len(parts) == 1:
            shipped.setdefault(_ANY_PROVIDER, set()).add(parts[0])
        elif len(parts) >= 2:
            shipped.setdefault(parts[0], set()).add(parts[1])
    return {k: frozenset(v) for k, v in shipped.items()}


def _origin(shipped: Mapping[str, Iterable[str]] | None, provider: str, name: str) -> str | None:
    if shipped is None:
        return None
    if name in shipped.get(provider, ()) or name in shipped.get(_ANY_PROVIDER, ()):
        return ORIGIN_SHIPPED
    return ORIGIN_OPERATOR


# ---------------------------------------------------------------------------
# Comparison and listing
# ---------------------------------------------------------------------------


def compare_skill_dir(source: str | os.PathLike[str], installed: str | os.PathLike[str]) -> str:
    """Compare a source skill directory with its copy in a provider home.

    Returns ``new`` when *installed* does not exist, ``same`` when every
    source directory exists there and every source file is byte-identical
    there, else ``changed``. Files only in *installed* are not differences.
    The comparison is ``tree_compare.tree_differing_paths``, the one
    deployed-copy comparator the bash deploy steps run too.
    """
    source = pathlib.Path(source)
    installed = pathlib.Path(installed)
    if not installed.exists() and not installed.is_symlink():
        return STATE_NEW
    if not installed.is_dir():
        return STATE_CHANGED
    return STATE_SAME if not tree_differing_paths(source, installed) else STATE_CHANGED


def _absolute(path: str | os.PathLike[str]) -> pathlib.Path:
    """*path* made absolute against the working directory, without
    resolving symlinks, so output and remedies read the same from anywhere."""
    return pathlib.Path(os.path.abspath(path))


def list_skills(
    source_dir: str | os.PathLike[str],
    *,
    provider: str = "all",
    homes: Mapping[str, str | os.PathLike[str]] | None = None,
    shipped: Mapping[str, Iterable[str]] | None = None,
    exclude: Iterable[str] = (),
    env: Mapping[str, str] | None = None,
    missing_ok: bool = False,
) -> SkillsListing:
    """List every source skill per provider with its state against the home.

    The listing's source and home paths are absolute. Listing only reads:
    nothing is created or written, in the source or in any home.

    Args:
        source_dir: the source directory (either layout).
        provider:   ``claude``, ``codex`` or ``all``.
        homes:      per-provider home overrides; others resolve through
                    :func:`provider_skills_home` with *env*.
        shipped:    the shipped set (see :func:`read_shipped_manifest`);
                    when given, every entry carries an origin.
        exclude:    providers to leave out after the layout check (a
                    provider whose CLI is absent, say). A flat source is
                    still judged against the providers requested.
        env:        environment for home resolution (default ``os.environ``).
        missing_ok: when nothing exists at *source_dir*, list every
                    requested provider with no skills instead of refusing.

    Raises:
        SkillsUsageError: an unknown provider, a missing source (unless
            *missing_ok*), or a flat source with more than one provider
            requested.
    """
    requested = _requested_providers(provider)
    source_dir = _absolute(source_dir)
    if missing_ok and not source_dir.exists() and not source_dir.is_symlink():
        by_provider: dict[str, list[pathlib.Path]] = {}
    else:
        by_provider = source_skill_dirs(source_dir, requested)
    excluded = set(exclude)
    homes = homes or {}
    listings = []
    for prov in requested:
        if prov in excluded:
            continue
        home = _absolute(homes[prov] if homes.get(prov) else provider_skills_home(prov, env))
        entries = []
        for src in by_provider.get(prov, []):
            dest = home / src.name
            origin = _origin(shipped, prov, src.name)
            try:
                load_skill(src)
            except SkillInvalid as exc:
                entries.append(SkillEntry(prov, src.name, src, dest, STATE_INVALID, origin, exc.rule, exc.detail))
                continue
            entries.append(SkillEntry(prov, src.name, src, dest, compare_skill_dir(src, dest), origin))
        listings.append(ProviderListing(prov, home, tuple(entries)))
    return SkillsListing(source_dir, tuple(listings))


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------


def _copy_file(src: pathlib.Path, dest: pathlib.Path) -> None:
    """Copy *src* over *dest* through a temporary file in *dest*'s directory
    and an atomic rename, keeping *src*'s permission bits."""
    fd, tmp = tempfile.mkstemp(prefix=_TEMP_PREFIX, dir=dest.parent)
    os.close(fd)
    try:
        shutil.copyfile(src, tmp)
        shutil.copymode(src, tmp)
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def _write_skill(entry: SkillEntry, paths: list[str]) -> None:
    """Create the home copy's missing directories and write *paths*."""
    dirs, _files = tree_entries(entry.source)
    target = entry.installed
    try:
        target.mkdir(parents=True, exist_ok=True)
        for d in dirs:
            (target / d).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SkillsWriteError(target, exc) from exc
    for rel in paths:
        if rel in dirs:
            continue
        dest = target / rel
        try:
            _copy_file(entry.source / rel, dest)
        except OSError as exc:
            raise SkillsWriteError(dest, exc) from exc


def install_skills(listing: SkillsListing, *, force: bool = False, dry_run: bool = False) -> InstallReport:
    """Install the skills of *listing* into their provider homes.

    ``new`` skills are copied whole; ``same`` skills are left (nothing is
    written); a ``changed`` skill is left untouched unless *force*, when its
    missing and differing files are written over the home copy; ``invalid``
    skills are never installed. Nothing is ever removed from a home. With
    *dry_run* the outcomes say what would happen and nothing is written.

    Raises:
        SkillsWriteError: a directory or file could not be written; skills
            already written stay written.
    """
    outcomes = []
    for prov in listing.providers:
        for entry in prov.entries:
            if entry.state == STATE_INVALID:
                outcomes.append(SkillOutcome(entry, ACTION_SKIPPED_INVALID))
                continue
            if entry.state == STATE_SAME:
                outcomes.append(SkillOutcome(entry, ACTION_UNCHANGED))
                continue
            if entry.state == STATE_CHANGED and not force:
                differing = tuple(tree_differing_paths(entry.source, entry.installed))
                outcomes.append(SkillOutcome(entry, ACTION_LEFT_CHANGED, differing))
                continue
            if entry.state == STATE_NEW:
                paths = tree_entries(entry.source)[1]
                action = ACTION_INSTALLED
            else:
                paths = tree_differing_paths(entry.source, entry.installed)
                action = ACTION_OVERWRITTEN
            if not dry_run:
                _write_skill(entry, paths)
            outcomes.append(SkillOutcome(entry, action, tuple(p for p in paths if not (entry.source / p).is_dir())))
    return InstallReport(listing, tuple(outcomes), force, dry_run)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_list_json(listing: SkillsListing) -> str:
    """The list as JSON: keys sorted, two-space indent, trailing newline."""
    return json.dumps(listing.to_dict(), indent=2, sort_keys=True) + "\n"


def _entry_suffix(entry: SkillEntry) -> str:
    parts = []
    if entry.origin is not None:
        parts.append(entry.origin)
    if entry.rule is not None:
        parts.append(f"{entry.rule}: {entry.detail}")
    return f"  ({'; '.join(parts)})" if parts else ""


def render_list_text(listing: SkillsListing) -> list[str]:
    """The list as text: one ``<provider>: <home>`` header per provider, then
    one ``  <state>  <name>`` line per skill (origin and any rule in
    parentheses), or ``  (no skills)``."""
    lines = []
    for prov in listing.providers:
        lines.append(f"{prov.provider}: {prov.home}")
        if not prov.entries:
            lines.append("  (no skills)")
        for e in prov.entries:
            lines.append(f"  {e.state:<8} {e.name}{_entry_suffix(e)}")
    return lines


_ACTION_WORDS = {
    ACTION_INSTALLED: ("installed", "would install"),
    ACTION_OVERWRITTEN: ("overwritten", "would overwrite"),
    ACTION_UNCHANGED: ("same", "same"),
    ACTION_LEFT_CHANGED: ("changed, left as is", "changed, would leave as is"),
    ACTION_SKIPPED_INVALID: ("invalid, not installed", "invalid, would not install"),
}


def render_install_text(report: InstallReport) -> list[str]:
    """The install outcome as text: one ``<provider>: <home>`` header per
    provider, then one ``  <name>: <what happened>`` line per skill."""
    lines = []
    by_provider: dict[str, list[SkillOutcome]] = {}
    for o in report.outcomes:
        by_provider.setdefault(o.entry.provider, []).append(o)
    for prov in report.listing.providers:
        prefix = "[dry-run] " if report.dry_run else ""
        lines.append(f"{prefix}{prov.provider}: {prov.home}")
        outcomes = by_provider.get(prov.provider, [])
        if not outcomes:
            lines.append("  (no skills)")
        for o in outcomes:
            word = _ACTION_WORDS[o.action][1 if report.dry_run else 0]
            extra = ""
            if o.action == ACTION_LEFT_CHANGED:
                extra = f" (differs: {', '.join(o.written)})" if o.written else ""
            elif o.action == ACTION_SKIPPED_INVALID:
                extra = f" ({o.entry.rule}: {o.entry.detail})"
            lines.append(f"  {o.entry.name}: {word}{extra}")
    return lines


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def _homes_from_args(args: argparse.Namespace) -> dict[str, str]:
    return {p: v for p, v in (("claude", args.claude_home), ("codex", args.codex_home)) if v}


def _listing_from_args(args: argparse.Namespace) -> SkillsListing:
    """Build the listing a list or install run asks for, narrating each
    provider skipped for a missing CLI binary on stderr."""
    if args.dir:
        source = args.dir
    else:
        try:
            source = str(resolve_kanban_root() / "skills")
        except RuntimeError as exc:
            raise SkillsUsageError(
                "skills_install.source_unset",
                f"no --dir was given and the default $KANBAN_ROOT/skills cannot be resolved ({exc}).",
                module_file_command(_OWN_FILE, args.verb, "--dir", "skills"),
            ) from exc
    shipped = (
        read_shipped_manifest(args.shipped_manifest, missing_ok=args.missing_ok) if args.shipped_manifest else None
    )
    exclude: list[str] = []
    if args.skip_missing_cli:
        exclude = providers_missing_cli(_requested_providers(args.provider))
        for prov in exclude:
            print(f"skills: skipping {prov}: CLI binary not found on PATH", file=sys.stderr)
    return list_skills(
        source,
        provider=args.provider,
        homes=_homes_from_args(args),
        shipped=shipped,
        exclude=exclude,
        missing_ok=args.missing_ok,
    )


def _emit_usage(exc: SkillsUsageError) -> int:
    # BLOCK-WORTHY: the request names no source or providers the module can
    # act on; acting anyway would install into the wrong home or nowhere.
    emit_error_contract(exc.error, exc.message, exc.remedy)
    # CENSUS-ANCHOR: skills-install-usage-01
    return EXIT_USAGE


def _narrate_invalid(entries: Iterable[SkillEntry]) -> None:
    for e in entries:
        print(f"skills: {e.provider}/{e.name} is not a valid skill ({e.rule}: {e.detail})", file=sys.stderr)


def _cmd_list(args: argparse.Namespace) -> int:
    try:
        listing = _listing_from_args(args)
    except SkillsUsageError as exc:
        return _emit_usage(exc)
    if args.json:
        sys.stdout.write(render_list_json(listing))
    else:
        for line in render_list_text(listing):
            print(line)
    return EXIT_OK


def _force_remedy(args: argparse.Namespace, source: pathlib.Path) -> str:
    """The command that re-runs this install with --force: the caller's own
    command when it passed one, else this module by file path with the same
    source, providers, homes and options."""
    if args.rerun_command:
        return f"{args.rerun_command} --force"
    cmd = ["install", "--dir", str(source), "--provider", args.provider]
    for flag, value in (
        ("--claude-home", args.claude_home),
        ("--codex-home", args.codex_home),
        ("--shipped-manifest", args.shipped_manifest),
    ):
        if value:
            cmd += [flag, value]
    if args.skip_missing_cli:
        cmd.append("--skip-missing-cli")
    if args.missing_ok:
        cmd.append("--missing-ok")
    return module_file_command(_OWN_FILE, *cmd, "--force")


def _cmd_install(args: argparse.Namespace) -> int:
    try:
        listing = _listing_from_args(args)
    except SkillsUsageError as exc:
        return _emit_usage(exc)
    try:
        report = install_skills(listing, force=args.force, dry_run=args.dry_run)
    except SkillsWriteError as exc:
        # BLOCK-WORTHY: a run that reports success while a skill is half
        # written leaves the provider CLI loading a broken skill unseen.
        emit_error_contract(
            "skills_install.write_failed",
            f"{exc}; skills earlier in the run may already be written, and nothing was removed.",
            f"ls -ld {shlex.quote(str(exc.path.parent))}",
        )
        # CENSUS-ANCHOR: skills-install-write-01
        return EXIT_WRITE_FAILED
    for line in render_install_text(report):
        print(line)
    _narrate_invalid(o.entry for o in report.invalid)
    if report.left_changed:
        names = ", ".join(f"{o.entry.provider}/{o.entry.name}" for o in report.left_changed)
        # BLOCK-WORTHY: an operator may have edited their installed copy; it
        # is overwritten only on an explicit --force. Every other skill was
        # still installed.
        emit_error_contract(
            "skills_install.changed_left",
            f"{names} differ from the installed copy and were left as is; "
            "re-run with --force to overwrite them (nothing in the home is removed).",
            _force_remedy(args, listing.source),
        )
        # CENSUS-ANCHOR: skills-install-changed-left-01
        return EXIT_CHANGED_LEFT
    return EXIT_OK


def _cmd_home(args: argparse.Namespace) -> int:
    print(provider_skills_home(args.provider))
    return EXIT_OK


def _add_source_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--dir",
        metavar="DIR",
        default=None,
        help="Source directory: <provider>/<skill>/SKILL.md, or <skill>/SKILL.md with one --provider "
        "(default: $KANBAN_ROOT/skills).",
    )
    parser.add_argument(
        "--provider",
        choices=(*PROVIDERS, "all"),
        default="all",
        help="Which provider homes to read or install into (default: all).",
    )
    parser.add_argument("--claude-home", metavar="DIR", default=None, help="Override the claude skill home.")
    parser.add_argument("--codex-home", metavar="DIR", default=None, help="Override the codex skill home.")
    parser.add_argument(
        "--shipped-manifest",
        metavar="FILE",
        default=None,
        help="File naming the shipped skills (provider/name per line); marks each skill shipped or operator-added.",
    )
    parser.add_argument(
        "--skip-missing-cli",
        action="store_true",
        help="Skip, with a one-line notice, each provider whose CLI binary is not on PATH.",
    )
    parser.add_argument(
        "--missing-ok",
        action="store_true",
        help="No source directory lists every provider with no skills; no --shipped-manifest file "
        "is an empty shipped set.",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skills_install",
        description="List and install provider CLI skills (directories holding a SKILL.md) into the "
        "provider skill homes. Exit codes: 0 done or already the same, 1 write failed, 2 usage error, "
        "3 a changed skill was left because --force was not given.",
    )
    sub = parser.add_subparsers(dest="verb", metavar="VERB", required=True)

    p_list = sub.add_parser(
        "list", aliases=["list_skills"], help="List each source skill per provider as new, same or changed."
    )
    _add_source_options(p_list)
    p_list.add_argument("--json", action="store_true", help="Print the list as JSON (keys sorted).")
    p_list.set_defaults(func=_cmd_list)

    p_install = sub.add_parser(
        "install", aliases=["install_skills"], help="Copy each source skill directory into the provider homes."
    )
    _add_source_options(p_install)
    p_install.add_argument("--force", action="store_true", help="Overwrite a changed skill in a provider home.")
    p_install.add_argument("--dry-run", action="store_true", help="Print the install plan; write nothing.")
    p_install.add_argument(
        "--rerun-command",
        metavar="CMD",
        default="",
        help="The command that re-runs the caller; the changed-skill remedy appends --force to it.",
    )
    p_install.set_defaults(func=_cmd_install)

    p_home = sub.add_parser("home", help="Print a provider's skill home.")
    p_home.add_argument("provider", choices=PROVIDERS, help="claude or codex.")
    p_home.set_defaults(func=_cmd_home)
    return parser


_dispatch = subcommand_main(_build_parser)


def main(argv: Sequence[str] | None = None) -> int:
    """Command-line entry point; returns the exit code (2 on a usage error)."""
    try:
        return _dispatch(None if argv is None else list(argv))
    except SystemExit as exc:
        return EXIT_OK if exc.code in (0, None) else EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
