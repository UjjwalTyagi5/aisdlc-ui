"""Shared skills platform — vendor skill registry + parsing.

Vendor skills are packaged SKILL.md files on disk (shared/skills/vendor/<agent_id>/<skill_key>/SKILL.md).
Custom skills and enable/disable state are persisted in Postgres (see shared.models.orm.AgentSkill /
AgentSkillToggle). This package owns the on-disk read path only.
"""
from shared.skills.registry import (
    VendorSkill,
    all_vendor_skills,
    get_vendor_skill,
    vendor_skills_for,
)

__all__ = [
    "VendorSkill",
    "all_vendor_skills",
    "get_vendor_skill",
    "vendor_skills_for",
]
