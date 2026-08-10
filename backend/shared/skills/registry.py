"""Vendor-skill registry — reads packaged SKILL.md files from disk, process-cached.

A vendor skill is a directory containing a SKILL.md file in the Anthropic Skills
convention:

    ---
    name: ...
    description: ...
    when_to_use: ...
    runtime: llm
    ---
    <markdown body>

The frontmatter parse is a small local reimplementation of the testing agent's
skill_loader (deliberately NOT imported — that module carries LangGraph-runtime
concerns we do not want here). Malformed SKILL.md files are logged and skipped so
one bad file never breaks the whole catalog.

Layout:
  shared/skills/vendor/<agent_id>/<skill_key>/SKILL.md   — the canonical vendor set
  agents_orchestrator/testing_agent/skills/<key>/SKILL.md — surfaced read-only for
      agent_id "testing" (its skills already live there; we do not duplicate them).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger("shared.skills.registry")

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)

VENDOR_ROOT = Path(__file__).parent / "vendor"

# platform/backend — shared/skills/registry.py -> shared/skills -> shared -> backend
_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_TESTING_SKILLS_DIR = _BACKEND_ROOT / "agents_orchestrator" / "testing_agent" / "skills"


@dataclass(frozen=True)
class VendorSkill:
    skill_key: str
    agent_id: str
    display_name: str
    description: str
    when_to_use: str
    runtime: str
    body: str


def _humanize(name: str) -> str:
    """Turn a frontmatter name / dir key into a display name.

    'story-splitting-spidr' -> 'Story Splitting Spidr'; already-spaced names pass
    through title-cased word-by-word without clobbering existing capitalization.
    """
    cleaned = name.replace("-", " ").replace("_", " ").strip()
    return " ".join(w[:1].upper() + w[1:] if w else w for w in cleaned.split())


def _parse_skill_md(skill_md: Path, agent_id: str, skill_key: str) -> Optional[VendorSkill]:
    text = skill_md.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise ValueError("missing YAML frontmatter (--- ... ---)")
    meta = yaml.safe_load(m.group(1)) or {}
    if not isinstance(meta, dict):
        raise ValueError("frontmatter is not a mapping")
    body = m.group(2) or ""
    raw_name = meta.get("name") or skill_key
    runtime = str(meta.get("runtime") or "llm").lower()
    if runtime not in ("llm", "shell"):
        logger.warning("Skill %s/%s: unknown runtime %r — defaulting to 'llm'", agent_id, skill_key, runtime)
        runtime = "llm"
    return VendorSkill(
        skill_key=skill_key,
        agent_id=agent_id,
        display_name=_humanize(str(raw_name)),
        description=str(meta.get("description") or ""),
        when_to_use=str(meta.get("when_to_use") or ""),
        runtime=runtime,
        body=body,
    )


def _load_dir(agent_id: str, skills_dir: Path) -> list[VendorSkill]:
    out: list[VendorSkill] = []
    if not skills_dir.exists():
        return out
    for entry in sorted(skills_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith("__"):
            continue
        skill_md = entry / "SKILL.md"
        if not skill_md.exists():
            continue
        try:
            skill = _parse_skill_md(skill_md, agent_id, entry.name)
        except Exception as exc:  # fail-soft — one bad file never breaks the catalog
            logger.warning("Skipping malformed skill at %s: %s", skill_md, exc)
            continue
        if skill is not None:
            out.append(skill)
    return out


_CACHE: Optional[dict[str, list[VendorSkill]]] = None


def all_vendor_skills(reload: bool = False) -> dict[str, list[VendorSkill]]:
    """Return {agent_id: [VendorSkill, ...]} for every packaged vendor skill.

    Process-cached. For agent_id 'testing', the testing-agent's own skills dir is
    also read (read-only surfacing) and merged with any vendor/testing entries,
    de-duplicated by skill_key (vendor dir wins).
    """
    global _CACHE
    if _CACHE is not None and not reload:
        return _CACHE

    catalog: dict[str, list[VendorSkill]] = {}
    if VENDOR_ROOT.exists():
        for agent_dir in sorted(VENDOR_ROOT.iterdir()):
            if not agent_dir.is_dir() or agent_dir.name.startswith("__"):
                continue
            skills = _load_dir(agent_dir.name, agent_dir)
            if skills:
                catalog[agent_dir.name] = skills

    testing_extra = _load_dir("testing", _TESTING_SKILLS_DIR)
    if testing_extra:
        existing = {s.skill_key for s in catalog.get("testing", [])}
        merged = list(catalog.get("testing", []))
        merged.extend(s for s in testing_extra if s.skill_key not in existing)
        catalog["testing"] = merged

    _CACHE = catalog
    return catalog


def vendor_skills_for(agent_id: str) -> list[VendorSkill]:
    return list(all_vendor_skills().get(agent_id, []))


def get_vendor_skill(agent_id: str, skill_key: str) -> Optional[VendorSkill]:
    for skill in all_vendor_skills().get(agent_id, []):
        if skill.skill_key == skill_key:
            return skill
    return None
