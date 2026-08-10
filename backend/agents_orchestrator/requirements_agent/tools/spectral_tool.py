"""Re-export of the shared Spectral OpenAPI lint tool.

The implementation moved to `shared.tools.spectral_tool` so the design agent can
reuse it (reference §4.1 / §4.2 Step 5). This module is kept as a thin re-export
so existing imports (`agents_orchestrator.requirements_agent.tools.spectral_tool`)
keep working unchanged.
"""
from __future__ import annotations

from shared.tools.spectral_tool import run_spectral_lint

__all__ = ["run_spectral_lint"]
