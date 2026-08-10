# TOMBSTONE — agents/ (Legacy Tree)

**Status:** PARTIALLY DEAD — most traffic re-routed as of M0; orchestrator/ still has one active import (see below)

**Date tombstoned:** 2026-05-27
**Tombstoned by:** M0 Codebase Stabilization

## What this directory is

This is the original pre-orchestrator agent implementation. It predates the
agents_orchestrator/ tree and should not be modified or extended.

## Traffic status

After the M0 routing fix in process_api.py, no HTTP traffic reaches any router
in this tree. The primary /sdlc/agent/requirement and /sdlc/agent/deployment
prefixes now serve agents_orchestrator/ implementations.

## Known remaining cross-references (not M0 scope)

**1. agents/orchestrator/orchestrator_api.py — ACTIVE IMPORT**
process_api.py line 5: `from agents.orchestrator.orchestrator_api import orchestrator_router`
Mounts at: `/sdlc/agent/orchestrator`
This module has no counterpart in agents_orchestrator/ yet. Migrating it in M0 would require
creating new code (violates M0's no-new-capabilities constraint). Schedule: migrate and delete
in M2 when agents/ tree is cleaned up.

**2. agents_orchestrator/requirements_agent/requirements_agent_api.py — LIVE RUNTIME DEPENDENCY**
Lines 33-34: imports `planning_app` (compiled LangGraph graph) and `INGESTION_SYS_MESSAGE`
from agents/requirements_agent/agents/planning.py, plus `shared` config.
`planning_app` is the live requirement graph — the requirements router cannot serve requests
without it. This is NOT a read-only data import; it is a runtime dependency.
Do NOT delete agents/requirements_agent/ in M2 without first migrating planning_app and its
dependencies to agents_orchestrator/requirements_agent/agents/planning.py.

## Deletion schedule

- agents/orchestrator/ — DELETE in M2 after orchestrator_api is migrated to agents_orchestrator/
- agents/requirements_agent/ — DELETE in M2 after prompt strings are migrated
- agents/deployment_agent/   — DELETE in M2
- agents/design_architecture_agent/ — DELETE in M2
- agents/development_agent/  — DELETE in M2
- agents/monitoring_feedback_agent/ — DELETE in M2 (shared_state.py has
  module-level prev_session_id and chat_histories; confirmed not imported by
  any active code as of M0 audit)

Do NOT delete anything in M0. Deletion is M2 scope.
