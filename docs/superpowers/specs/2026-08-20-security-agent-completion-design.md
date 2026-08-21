# Security Agent — Completion Design

Date: 2026-08-20
Scope: close the confirmed, agent-specific gaps between the Security agent's current
implementation and the PRD's spec for it (§21.5, unchanged in §22.5/23.7/24.5/25.3),
after this session's live end-to-end verification proved the graph, its 4 scanning
tools, and `submit_security_review` are real and working.

## Context

Earlier this session, the Security agent's WS/REST access control was hardened
(PR #12/#13), and its actual tool loop was verified end-to-end without a live LLM key
(`backend/tests/test_security_agent_live_e2e.py` — real Trivy/Semgrep/Gitleaks runs,
scripted model responses). That verification, plus a direct read of the PRD and the
current code, surfaced three real, narrow gaps — everything else checked out clean:

- The system prompt (`prompts/security_prompt.py`) **already** correctly specifies
  reachability tracing, dedup, triage, and OWASP/CWE mapping — an earlier hypothesis
  that this was missing was wrong, corrected by actually reading the file before
  designing around it.
- Two things the PRD implies (automatic scan-on-handoff, and the orchestrator
  structurally blocking progression on a FAIL signoff) are real gaps but **out of
  scope for this pass** — both live in shared orchestrator code affecting all 8
  agents, not Security-specific files, and the auto-advance behavior directly
  conflicts with an existing, deliberately-reasoned "the pipeline never advances
  itself" design principle already in the code. Deferred by explicit user decision;
  tracked in `help/portfolio-1-agent-status.md`, not solved here.

## What this design covers

### 1. Swappable model key (BYOK)

**Problem:** `agents/scanner.py`'s `agent_node` builds `ChatAnthropic` inline, always
against the raw `.env` `ANTHROPIC_API_KEY`. It never even tries an in-app
BYOK-configured provider — unlike Code Review's `_resolve_model()`, which tries
`resolve_chat_model` (the BYOK path) first and only falls back to the raw key on
failure. Security is therefore unusable with any model a Business Unit or
Organization Admin actually configures in-app.

**Fix:** Extract a `_resolve_model(state)` function into `scanner.py`, structurally
identical to Code Review's:

```python
def _resolve_model(state: AgentState):
    from config.env import ANTHROPIC_API_KEY, ANTHROPIC_MODEL
    model_id = state.get("model_id") or ANTHROPIC_MODEL
    offering_id = state.get("offering_id")
    seen: set[str] = set()
    tools = []
    for t in _tools + get_skill_tools("security") + get_mcp_tools():
        name = getattr(t, "name", None)
        if name in seen:
            continue
        seen.add(name)
        tools.append(t)
    base = get_prompt_override("security") or SECURITY_SYSTEM_PROMPT
    try:
        from shared.services.model_resolver import resolve_chat_model
        return resolve_chat_model(
            model_id=model_id, offering_id=offering_id,
            tools=tools, system_prompt=base,
        )
    except Exception:
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=ANTHROPIC_MODEL, api_key=ANTHROPIC_API_KEY, max_tokens=8192,
        ).bind_tools(tools)
```

`agent_node` calls this instead of building the model itself. Behavior is unchanged
when no BYOK provider is configured (falls through to today's exact behavior) —
this is additive, not a breaking change.

**Testing:** `test_security_agent_live_e2e.py`'s mock target changes from
`langchain_anthropic.ChatAnthropic` to `agents_orchestrator.security_agent.agents.scanner._resolve_model`
(matching how Code Review's own live_e2e test already mocks its resolver) — this is
actually a **simplification** of that test, not just a compatibility update. Add one
new unit test confirming `_resolve_model` tries `resolve_chat_model` first and falls
back to `ChatAnthropic` only when that raises.

### 2. CWE tags dropped from Semgrep SAST output

**Problem:** `tools/semgrep_sast_tool.py` extracts `owasp_category` from each Semgrep
finding's `extra.metadata.owasp` field, but never reads `extra.metadata.cwe` — even
though it's the same shape, Semgrep's own rules populate it, and the prompt's output
schema explicitly asks for both (`"compliance":["OWASP A03:2021","CWE-89"]`).

**Fix:** One additional field in the same dict-building loop:
`"cwe": r.get("extra", {}).get("metadata", {}).get("cwe", [])`.

**Testing:** Extend the existing tool-level assertion (or add one) confirming a
Semgrep finding with `metadata.cwe` in its raw JSON surfaces `cwe` in the tool's
parsed output — same pattern already used to verify `owasp_category` if such a test
exists, otherwise a new small unit test with a canned Semgrep JSON fixture.

### 3. SBOM doesn't carry the vulnerability counts its own schema promises

**Problem:** The prompt's `submit_security_review` schema documents each SBOM entry
as `{"name":"lib","version":"1.2.3","license":"MIT","vulnerabilities":1}`, but
`generate_sbom` only ever produces `{name, version, manifest}` — no `vulnerabilities`
field at all. The model is left to manually cross-reference two separate tool
outputs (the Trivy scan and the SBOM) itself to fill that field in, which the
prompt's own "never invent findings — ground them in scan output" rule argues against
relying on.

**Scope, per explicit decision:** vulnerability cross-reference only. No license
field (real per-dependency license data needs a registry call — new external
dependency, new failure mode, for a cosmetic payoff) and no lockfile/transitive-
dependency parsing (more parsing surface for edge cases, deferred).

**Fix:**
- `config/session_state.py`'s `ScanSessionState` gains one new field:
  `last_trivy_findings: list[dict] = field(default_factory=list)`.
- `tools/security_tools.py::scan_dependencies` caches its parsed findings onto the
  session after a successful run (`s.last_trivy_findings = findings`), the same way
  `s.last_artifact` is already set by `submit_security_review` — no new pattern, just
  applying the existing one to another field.
- `generate_sbom` cross-references each parsed component's `(name, version)` against
  `s.last_trivy_findings`'s `(package, installed_version)` and sets a real
  `vulnerabilities` count (0 if `scan_dependencies` hasn't run yet this session, or if
  no match — never fabricated).

**Testing:** Extend `test_security_agent_live_e2e.py`'s scripted turn so
`scan_dependencies` runs *before* `generate_sbom` (already true in the existing
script's turn 1, since both are called together — verify the assertion checks the
SBOM's `vulnerabilities` field reflects the real Trivy finding, not just that the
component is present). Add a focused unit test for the cross-reference logic itself
(matching name+version → count populated; no match → 0; empty
`last_trivy_findings` → 0, not an error).

## Out of scope (confirmed, not silently dropped)

- Automatic scan-on-Development-completion (orchestrator-level; conflicts with the
  existing no-auto-advance design principle).
- Orchestrator enforcing a hard block when `signoff.decision == "fail"` (orchestrator-
  level, affects all mandatory-gated agents, not Security-specific).
- SBOM license fields and lockfile/transitive-dependency parsing (explicit decision:
  vulnerability cross-reference only).
- Full reachability/taint-analysis tooling — the prompt already directs the model to
  do heuristic reachability via `read_repo_file`/`search_repo`, which is the
  realistic, achievable version of this (matches how real tools like GitHub's
  reachability analysis work — usage-search heuristics, not full data-flow tracing).

## Files touched

- `backend/agents_orchestrator/security_agent/agents/scanner.py` (§1)
- `backend/agents_orchestrator/security_agent/tools/semgrep_sast_tool.py` (§2)
- `backend/agents_orchestrator/security_agent/config/session_state.py` (§3)
- `backend/agents_orchestrator/security_agent/tools/security_tools.py` (§3)
- `backend/tests/test_security_agent_live_e2e.py` (updated for §1 and §3)
- New: `backend/tests/test_security_agent_tools.py` — isolated unit coverage for §2's
  CWE extraction and §3's vulnerability cross-reference logic, independent of the
  full graph (nothing currently covers tool-level parsing in isolation)
- `help/portfolio-1-agent-status.md` — updated once done
