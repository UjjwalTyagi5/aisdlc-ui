def test_design_tools_bound_to_graph():
    from agents_orchestrator.design_architecture_agent.agents import architecture
    tool_names = {t.name for t in architecture.tools}
    assert "run_spectral_lint" in tool_names
    assert "validate_database_schema" in tool_names
    assert "analyze_existing_system" in tool_names


def test_prompt_has_security_section_header():
    from agents_orchestrator.design_architecture_agent.agents.architecture import DESIGN_SYS_MESSAGE
    assert "## SECURITY DESIGN CHECKLIST" in DESIGN_SYS_MESSAGE


def test_prompt_mentions_validation_loops_and_existing_system():
    from agents_orchestrator.design_architecture_agent.agents.architecture import DESIGN_SYS_MESSAGE
    low = DESIGN_SYS_MESSAGE.lower()
    assert "run_spectral_lint" in low
    assert "validate_database_schema" in low
    assert "analyze_existing_system" in low
    assert "owasp" in low


def test_write_design_artifact_accepts_security_checklist():
    import inspect
    from agents_orchestrator.design_architecture_agent.agents.architecture import write_design_artifact
    # @tool on an async function stores it in .coroutine (not .func).
    underlying = write_design_artifact.coroutine or write_design_artifact.func
    sig = inspect.signature(underlying)
    assert "security_checklist" in sig.parameters
    # Also verify against LangChain's LLM-visible schema (more durable check)
    assert "security_checklist" in write_design_artifact.args_schema.model_fields


def test_arch_gen_prompt_mandates_full_diagram_set():
    """The SDD generation template must require the full diagram set the old
    (good) design runs produced: HLD, LLD (component/class + sequence), all
    three C4 levels, and an ERD alongside the DB DDL. Regression guard for the
    "dropped diagrams" bug — a template that only asked for C4 + DB DDL."""
    from agents_orchestrator.design_architecture_agent.prompts.architecture_generation import ARCH_GEN_PROMPT
    low = ARCH_GEN_PROMPT.lower()

    # Exact canonical headers the frontend/parser split on (parse_design_markdown
    # in shared/services/orchestrator/artifacts_view.py splits on "## " headers).
    assert "## high-level design (hld)" in low
    assert "## low-level design (lld)" in low
    assert "## c4 architecture diagram" in low
    assert "## database schema" in low
    assert "## api contracts" in low
    assert "## architecture decision records (adrs)" in low
    assert "## technology stack & infrastructure" in low

    # Required diagram types, explicitly mandated.
    assert "classdiagram" in low  # LLD component/class diagram
    assert "sequencediagram" in low  # LLD sequence diagram
    assert "erdiagram" in low  # Database ERD
    assert "level 1" in low and "system context" in low
    assert "level 2" in low and "container" in low
    assert "level 3" in low and "component" in low
    assert "mandatory diagram checklist" in low


def test_arch_gen_prompt_headers_match_frontend_parser():
    """The headers in ARCH_GEN_PROMPT must actually be splittable by
    parse_design_markdown (which regex-splits on '^##\\s+' lines) and map to the
    same canonical titles the Copilot artifacts panel expects."""
    import re
    from agents_orchestrator.design_architecture_agent.prompts.architecture_generation import ARCH_GEN_PROMPT
    from shared.services.orchestrator.artifacts_view import _DESIGN_HEADER_MAP

    headers = re.findall(r"(?m)^##\s+(.+?)\s*$", ARCH_GEN_PROMPT)
    assert headers, "no level-2 (##) headers found in ARCH_GEN_PROMPT"

    canonical_titles = {title for _, title in _DESIGN_HEADER_MAP}
    matched = set()
    for h in headers:
        low = h.lower()
        for kw, title in _DESIGN_HEADER_MAP:
            if kw in low:
                matched.add(title)
                break
    required = {
        "High-Level Design (HLD)", "Low-Level Design (LLD)", "C4 Architecture Diagram",
        "API Contracts", "Database Schema", "Architecture Decision Records (ADRs)",
        "Technology Stack & Infrastructure",
    }
    assert required <= canonical_titles
    assert required <= matched
