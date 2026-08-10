"""Agent Profile resolution (decisions D5, D6, DP3, DP8).

Resolves org -> workspace -> project layers into one ResolvedProfile and injects
the prompt layers statically (no RAG). The in-code base role prompt is the floor;
this only adds to it. Developers inherit and may only tighten, never weaken.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Iterable, Optional

from sqlalchemy import select

from shared.db import get_db_session_for_tenant
from shared.models.orm import AgentProfile

logger = logging.getLogger(__name__)

_SCOPE_ORDER = {"org": 0, "workspace": 1, "project": 2}


@dataclass
class ResolvedProfile:
    prompt_prepend: str = ""
    prompt_append: str = ""
    disabled_curated: set = field(default_factory=set)
    primary_overrides: dict = field(default_factory=dict)
    thresholds: dict = field(default_factory=dict)
    reference_doc_summaries: list = field(default_factory=list)
    output_contract_extra: str = ""
    version_chain: list = field(default_factory=list)


def merge_profiles(rows: "list[AgentProfile]") -> ResolvedProfile:
    """Pure merge. Prompt layers concatenate in scope order; dicts deep-overlay
    (later scope wins per key); sets union; later non-empty scalars win."""
    ordered = sorted(rows, key=lambda r: _SCOPE_ORDER.get(r.scope, 99))
    out = ResolvedProfile()
    prepends, appends, contracts = [], [], []
    for r in ordered:
        if r.prompt_prepend:
            prepends.append(r.prompt_prepend)
        if r.prompt_append:
            appends.append(r.prompt_append)
        if r.output_contract_extra:
            contracts.append(r.output_contract_extra)
        for key in (r.disabled_curated or []):
            out.disabled_curated.add(key)
        out.primary_overrides.update(r.primary_overrides or {})
        out.thresholds.update(r.thresholds or {})
        for s in (r.reference_doc_summaries or []):
            out.reference_doc_summaries.append(s)
        out.version_chain.append(f"{r.scope}:v{r.version}")
    out.prompt_prepend = "\n\n".join(prepends)
    out.prompt_append = "\n\n".join(appends)
    out.output_contract_extra = "\n\n".join(contracts)
    return out


async def resolve_profile(
    tenant_id: str,
    agent_id: str,
    workspace_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> ResolvedProfile:
    """Load active profile rows for the agent across scopes and merge them."""
    try:
        async with get_db_session_for_tenant(str(tenant_id)) as session:
            stmt = select(AgentProfile).where(
                AgentProfile.agent_id == agent_id,
                AgentProfile.is_active.is_(True),
            )
            rows = list((await session.execute(stmt)).scalars().all())
    except Exception as exc:
        logger.warning("resolve_profile(%s/%s) failed: %s", tenant_id, agent_id, exc)
        return ResolvedProfile()

    # Keep only rows whose scope_id matches the current workspace/project (or org-wide).
    def _applies(r: AgentProfile) -> bool:
        if r.scope == "org":
            return True
        if r.scope == "workspace":
            return workspace_id is not None and str(r.scope_id) == str(workspace_id)
        if r.scope == "project":
            return project_id is not None and str(r.scope_id) == str(project_id)
        return False

    # For each scope keep the highest active version.
    applicable = [r for r in rows if _applies(r)]
    best: dict[tuple, AgentProfile] = {}
    for r in applicable:
        k = (r.scope, str(r.scope_id))
        if k not in best or r.version > best[k].version:
            best[k] = r
    return merge_profiles(list(best.values()))


def inject_prompt(
    base_prompt: str, profile: ResolvedProfile, skills_index: str = ""
) -> str:
    """Static prepend/append + pinned reference-doc summaries (DP8).

    `skills_index` (Phase 4 skills platform) is the compact catalog string from
    skill_store.build_skills_index — layered after reference docs / base prompt and
    before the output contract extra. Default "" keeps every existing caller unchanged;
    it is included even when the profile is otherwise empty (skills work without a
    published profile)."""
    parts: list[str] = []
    if profile.prompt_prepend:
        parts.append(profile.prompt_prepend)
    if profile.reference_doc_summaries:
        joined = "\n".join(f"- {s}" for s in profile.reference_doc_summaries)
        parts.append("Organization reference standards (must be followed):\n" + joined)
    parts.append(base_prompt)
    if skills_index:
        parts.append(skills_index)
    if profile.output_contract_extra:
        parts.append(profile.output_contract_extra)
    if profile.prompt_append:
        parts.append(profile.prompt_append)
    return "\n\n".join(p for p in parts if p).strip()


# ── Curated-toggle write path (powers the Agents & Capabilities panel) ──────────
#
# Toggling a curated tool on/off for an agent is recorded on the *project*-scoped
# Agent Profile's `disabled_curated` list — never the shipped catalog. The effective
# disabled set an agent sees is the union of its org + project layers (workspace is
# unused by the panel for now).

def _disabled_for_project(rows: "list[AgentProfile]", project_id: str) -> dict[str, set]:
    """Pure: per-agent union of disabled_curated across org + this-project profiles."""
    out: dict[str, set] = {}
    for r in rows:
        applies = r.scope == "org" or (
            r.scope == "project" and r.scope_id is not None
            and str(r.scope_id) == str(project_id)
        )
        if not applies:
            continue
        bucket = out.setdefault(r.agent_id, set())
        for key in (r.disabled_curated or []):
            bucket.add(key)
    return out


async def project_disabled_curated(
    tenant_id: str, project_id: str, agent_ids: Iterable[str]
) -> dict[str, list[str]]:
    """For each agent, the sorted disabled-curated keys effective in this project."""
    ids = list(agent_ids)
    base: dict[str, set] = {a: set() for a in ids}
    try:
        async with get_db_session_for_tenant(str(tenant_id)) as session:
            stmt = select(AgentProfile).where(
                AgentProfile.agent_id.in_(ids),
                AgentProfile.is_active.is_(True),
            )
            rows = list((await session.execute(stmt)).scalars().all())
    except Exception as exc:
        logger.warning("project_disabled_curated(%s/%s) failed: %s", tenant_id, project_id, exc)
        return {a: [] for a in ids}
    resolved = _disabled_for_project(rows, project_id)
    for agent_id, keys in resolved.items():
        base.setdefault(agent_id, set()).update(keys)
    return {a: sorted(s) for a, s in base.items()}


async def set_project_disabled_curated(
    tenant_id: str, agent_id: str, project_id: str, disabled: Iterable[str], created_by: str
) -> list[str]:
    """Upsert the project-scoped active profile's disabled_curated list."""
    cleaned = sorted({str(k) for k in disabled})
    async with get_db_session_for_tenant(str(tenant_id)) as session:
        stmt = (
            select(AgentProfile)
            .where(
                AgentProfile.agent_id == agent_id,
                AgentProfile.scope == "project",
                AgentProfile.scope_id == uuid.UUID(str(project_id)),
                AgentProfile.is_active.is_(True),
            )
            .order_by(AgentProfile.version.desc())
        )
        row = (await session.execute(stmt)).scalars().first()
        if row is None:
            row = AgentProfile(
                tenant_id=uuid.UUID(str(tenant_id)),
                agent_id=agent_id,
                scope="project",
                scope_id=uuid.UUID(str(project_id)),
                version=1,
                is_active=True,
                disabled_curated=cleaned,
                created_by=created_by or "system",
            )
            session.add(row)
        else:
            row.disabled_curated = cleaned
        await session.flush()
    return cleaned
