"""Skill loader — parses SKILL.md frontmatter + body, renders templates, evaluates applies() predicates.

Skills live at agents_orchestrator/testing_agent/skills/<name>/SKILL.md.
Format mirrors the Anthropic Skills convention so skills are portable to a future
Agent SDK migration. Today we use them as packaged prompt+metadata files loaded
by the testing-agent's LangGraph nodes.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger("testing_agent.skill_loader")


SKILLS_DIR = Path(__file__).parent.parent / "skills"
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


@dataclass
class Skill:
    name: str
    description: str
    when_to_use: str
    inputs: List[str]
    outputs: Dict[str, str]
    prompt_template: str
    output_schema_pydantic: Optional[str] = None
    # Phase 11.4 — shell-capable runtime. Default "llm" preserves all existing
    # skills' behaviour. When "shell", dispatch_test_types invokes shell_command
    # via the sandbox instead of the LLM and pipes the output through the
    # named output_parser registered in tools/skill_parsers.py.
    runtime: str = "llm"                                 # "llm" | "shell"
    shell_command: Optional[str] = None                  # required when runtime == "shell"
    shell_timeout_s: int = 600                           # bounded subprocess timeout
    output_parser: Optional[str] = None                  # registered parser name (e.g. "stryker_json")
    output_artifact_field: Optional[str] = None          # AggregatedResults field to populate
    _raw_path: Optional[Path] = field(default=None, repr=False)

    @classmethod
    def load(cls, skill_dir: Path) -> "Skill":
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            raise FileNotFoundError(f"SKILL.md not found at {skill_md}")
        text = skill_md.read_text(encoding="utf-8")
        m = _FRONTMATTER_RE.match(text)
        if not m:
            raise ValueError(f"{skill_md} missing YAML frontmatter (--- ... ---)")
        meta = yaml.safe_load(m.group(1)) or {}
        body = m.group(2)
        runtime = (meta.get("runtime") or "llm").lower()
        if runtime not in ("llm", "shell"):
            logger.warning(
                f"Skill {skill_dir.name!r}: unknown runtime {runtime!r} — defaulting to 'llm'"
            )
            runtime = "llm"
        return cls(
            name=meta.get("name") or skill_dir.name,
            description=meta.get("description", ""),
            when_to_use=meta.get("when_to_use", ""),
            inputs=list(meta.get("inputs", [])),
            outputs=dict(meta.get("outputs", {})),
            output_schema_pydantic=meta.get("output_schema_pydantic"),
            runtime=runtime,
            shell_command=meta.get("shell_command"),
            shell_timeout_s=int(meta.get("shell_timeout_s", 600)),
            output_parser=meta.get("output_parser"),
            output_artifact_field=meta.get("output_artifact_field"),
            prompt_template=body,
            _raw_path=skill_md,
        )

    def render(self, **kwargs: Any) -> str:
        for required in self.inputs:
            if required not in kwargs:
                raise KeyError(f"Skill {self.name!r}: missing required input {required!r}")
        rendered = self.prompt_template
        for key, val in kwargs.items():
            rendered = rendered.replace("{{" + key + "}}", str(val))
        return rendered

    def applies(self, state: Dict[str, Any]) -> bool:
        # Each skill encodes its own applies() rule by name. We keep this
        # explicit (vs. evaluating when_to_use as code) for safety + clarity.
        # New skills extend this dispatch table.
        rule = _APPLIES_RULES.get(self.name)
        if rule is None:
            logger.warning(f"Skill {self.name!r}: no applies() rule registered, defaulting to False")
            return False
        return rule(state)


def _applies_unit(state: Dict[str, Any]) -> bool:
    return bool(state.get("code_analysis"))


def _applies_negative_edge(state: Dict[str, Any]) -> bool:
    return bool(state.get("code_analysis"))


def _applies_smoke(state: Dict[str, Any]) -> bool:
    target = state.get("target_url")
    upstream_dev = state.get("upstream_development") or {}
    return bool(target) or bool(upstream_dev.get("service_url"))


def _applies_functional_api(state: Dict[str, Any]) -> bool:
    target = state.get("target_url")
    upstream_dev = state.get("upstream_development") or {}
    has_url = bool(target) or bool(upstream_dev.get("service_url"))
    has_routes = bool((upstream_dev or {}).get("api_routes"))
    return has_url or has_routes


def _applies_functional_ui(state: Dict[str, Any]) -> bool:
    target = state.get("target_url") or ""
    return target.startswith("http://") or target.startswith("https://")


# Phase 11.3 — 4 new LLM-only skills

def _applies_integration(state: Dict[str, Any]) -> bool:
    """Integration: cross-module flows. Need ≥2 distinct file_paths in
    code_analysis so there's actual cross-module surface to exercise."""
    ca = state.get("code_analysis")
    if not ca or not getattr(ca, "functions", None):
        return False
    file_paths = set()
    for f in ca.functions:
        fp = getattr(f, "file_path", None)
        if isinstance(fp, str) and fp:
            file_paths.add(fp)
    return len(file_paths) >= 2


def _applies_contract(state: Dict[str, Any]) -> bool:
    """Contract: needs design-agent's API contracts. Skip when design output
    didn't include them (or when running standalone with no upstream design)."""
    upstream_design = state.get("upstream_design") or {}
    api_contracts = upstream_design.get("api_contracts")
    if not api_contracts:
        return False
    if isinstance(api_contracts, dict):
        return len(api_contracts) > 0
    if isinstance(api_contracts, str):
        return bool(api_contracts.strip())
    return False


def _applies_accessibility(state: Dict[str, Any]) -> bool:
    """Accessibility: React-only (jest-axe). Other languages skip."""
    if (state.get("language") or "").lower() != "react":
        return False
    ca = state.get("code_analysis")
    if not ca or not getattr(ca, "functions", None):
        return False
    for f in ca.functions:
        fp = getattr(f, "file_path", None)
        if isinstance(fp, str) and fp.endswith((".jsx", ".tsx")):
            return True
    return False


_TYPED_INPUT_RE = re.compile(
    r"\b(int|str|float|bool|list|dict|tuple|number|string|boolean|array|integer)\b",
    re.IGNORECASE,
)


def _applies_property_based(state: Dict[str, Any]) -> bool:
    """Property-based: needs functions with typed inputs the framework can
    generate. Heuristic: at least one function whose input_format mentions a
    primitive or collection type."""
    ca = state.get("code_analysis")
    if not ca or not getattr(ca, "functions", None):
        return False
    for f in ca.functions:
        input_fmt = getattr(f, "input_format", "")
        if not isinstance(input_fmt, str):
            continue
        if _TYPED_INPUT_RE.search(input_fmt):
            return True
    return False


def _applies_mutation_testing(state: Dict[str, Any]) -> bool:
    """Mutation testing — runs after unit/negative_edge generated tests.
    Needs a runnable test suite to mutate. Heuristic: code_analysis present
    AND a work_dir exists (so the shell command can run somewhere)."""
    if not state.get("code_analysis"):
        return False
    return bool(state.get("work_dir"))


def _applies_security_static(state: Dict[str, Any]) -> bool:
    """Security static analysis — runs anywhere code_analysis is present.
    Per-language scanner is selected by the SKILL.md frontmatter (one
    SKILL.md per language is the current convention)."""
    return bool(state.get("code_analysis")) and bool(state.get("work_dir"))


def _applies_dependency_scan(state: Dict[str, Any]) -> bool:
    """Dependency scan — runs when there's a project to scan."""
    return bool(state.get("code_analysis")) and bool(state.get("work_dir"))


_APPLIES_RULES = {
    "unit": _applies_unit,
    "negative_edge": _applies_negative_edge,
    "smoke": _applies_smoke,
    "functional_api": _applies_functional_api,
    "functional_ui": _applies_functional_ui,
    # Phase 11.3 — LLM-only skills
    "integration": _applies_integration,
    "contract": _applies_contract,
    "accessibility": _applies_accessibility,
    "property_based": _applies_property_based,
    # Phase 11.4 — shell-runtime skills
    "mutation_testing": _applies_mutation_testing,
    "security_static": _applies_security_static,
    "dependency_scan": _applies_dependency_scan,
}


_CACHE: Dict[str, Skill] = {}


def load_all_skills(reload: bool = False) -> Dict[str, Skill]:
    global _CACHE
    if _CACHE and not reload:
        return _CACHE
    out: Dict[str, Skill] = {}
    if not SKILLS_DIR.exists():
        logger.warning(f"Skills dir not found at {SKILLS_DIR}")
        return out
    for entry in sorted(SKILLS_DIR.iterdir()):
        if not entry.is_dir() or entry.name.startswith("__"):
            continue
        skill_md = entry / "SKILL.md"
        if not skill_md.exists():
            continue
        try:
            out[entry.name] = Skill.load(entry)
        except Exception as exc:
            logger.warning(f"Failed to load skill at {entry}: {exc}")
    _CACHE = out
    return out
