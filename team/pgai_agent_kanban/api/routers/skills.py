"""
skills.py — GET /skills for the pgai-agent-kanban operator API.

Lists the kanban's skill staging directory, ``<kanban_root>/skills/
<provider>/<skill-name>/``: for each provider, its skill home and every staged
skill with whether the kanban shipped it or the operator added it, and its
state against that home (``new``, ``same``, ``changed``, or ``invalid`` for a
skill whose SKILL.md breaks a front-matter rule).

Everything comes from ``pgai_agent_kanban.lib.skills_install``, the module the
install-skills verb lists through, called in process:

  - ``list_skills`` reads the staging directory and compares each skill with
    its provider home; ``to_dict`` is the object ``render_list_json`` prints
    for the verb's ``--list --json``.
  - ``read_shipped_manifest`` reads ``<kanban_root>/.installed-skills``, the
    set install recorded as shipped; every other staged skill is
    operator-added.
  - ``provider_skills_home`` resolves each home from this server process's
    environment: claude ``$CLAUDE_CONFIG_DIR/skills`` (default
    ``$HOME/.claude/skills``), codex ``$HOME/.codex/skills``.

The route only reads. It never writes, creates or removes anything under the
staging directory or any provider home, and it does not skip a provider whose
CLI binary is missing from the server's PATH.
"""

from __future__ import annotations


from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from pgai_agent_kanban.lib import skills_install

from ..adapter import HTTP_UNPROCESSABLE_ENTITY
from ..dependencies import route_kanban_root, warn_unknown_query_params

__all__ = ["SkillsRefusal", "SkillsResponse", "router"]

router = APIRouter(tags=["skills"])


class SkillListEntry(BaseModel):
    """One staged skill compared with its provider home."""

    name: str
    state: str
    origin: str | None = None
    error: str | None = None
    message: str | None = None


class SkillProviderListing(BaseModel):
    """One provider: its skill home and the staged skills for it."""

    home: str
    provider: str
    skills: list[SkillListEntry]


class SkillsResponse(BaseModel):
    """GET /skills body: the skills module's list plus ``warnings``."""

    providers: list[SkillProviderListing]
    source: str
    warnings: list[str]


class SkillsRefusal(BaseModel):
    """GET /skills refusal: the skills module's own dual-channel triple."""

    error: str
    message: str
    remedy: str
    warnings: list[str]


@router.get(
    "/skills",
    summary="Skills in the staging directory, with their state in each provider home",
    response_model=SkillsResponse,
    responses={
        HTTP_UNPROCESSABLE_ENTITY: {
            "model": SkillsRefusal,
            "description": "The skills module refused the staging directory's layout or shipped-skills record.",
        }
    },
    response_description="JSON body with source, providers (each with home, provider and skills) and warnings.",
)
def get_skills(
    request: Request,
    warnings: list[str] = Depends(warn_unknown_query_params),
) -> JSONResponse:
    """List every skill in the kanban's staging directory, per provider.

    The REST twin of ``install-skills.sh --list --json``. Both list through
    ``pgai_agent_kanban.lib.skills_install.list_skills`` and the body carries
    the same keys and values that verb prints, plus ``warnings``. Read-only:
    nothing is created or written in the staging directory or any provider
    home.

    The staging directory is ``<kanban_root>/skills``, laid out
    ``<provider>/<skill-name>/SKILL.md``. A skill named in
    ``<kanban_root>/.installed-skills`` is ``shipped``; every other skill is
    ``operator-added``, as is every skill when that file is absent. Each
    provider home is resolved from the server's environment. The route takes
    no query parameters.

    Response body (200)::

        {"source": str,                 # absolute staging directory path
         "providers": [                 # claude, then codex
           {"provider": str,
            "home": str,                # absolute provider skill home
            "skills": [                 # sorted by name
              {"name": str,
               "state": "new"|"same"|"changed"|"invalid",
               "origin": "shipped"|"operator-added",
               "error": str,        # invalid only: the rule broken
               "message": str}]}],  # invalid only: what was found
         "warnings": [str, ...]}

    With no staging directory under the root, every provider is listed with
    ``"skills": []`` and the status is 200.

    Response body on refusal (422)::

        {"error": str, "message": str, "remedy": str, "warnings": [str, ...]}

    The skills module's own triple: ``skills_install.flat_needs_one_provider``
    when skills sit directly under ``skills/`` instead of under a provider
    directory, ``skills_install.source_missing`` when ``skills`` exists but
    is not a directory, and ``skills_install.shipped_manifest_unreadable``
    when ``.installed-skills`` exists but cannot be read.
    """
    kanban_root = route_kanban_root(request)
    try:
        shipped = skills_install.read_shipped_manifest(kanban_root / skills_install.SHIPPED_MANIFEST, missing_ok=True)
        listing = skills_install.list_skills(kanban_root / skills_install.STAGING_DIR, shipped=shipped, missing_ok=True)
    except skills_install.SkillsUsageError as exc:
        # BLOCK-WORTHY: relays the skills module's own usage refusal (censused
        # at its CLI site, skills-install-usage-01); a staging directory the
        # module cannot read as laid out has no listing to return.
        return JSONResponse(
            content={
                "error": exc.error,
                "message": exc.message,
                "remedy": exc.remedy,
                "warnings": warnings,
            },
            status_code=HTTP_UNPROCESSABLE_ENTITY,
        )

    body = listing.to_dict()
    return JSONResponse(
        content={
            "providers": body["providers"],
            "source": body["source"],
            "warnings": warnings,
        },
        status_code=200,
    )
