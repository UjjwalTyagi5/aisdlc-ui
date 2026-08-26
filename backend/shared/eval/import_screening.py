"""Credential-leakage detection for Agent Studio imports (sub-project 5).

Reuses the DETECTION half of agents_orchestrator/development_agent/tools/
sandbox_policy.py's _SECRET_PATTERNS -- the same regexes that redact agent tool
output -- but for a different purpose: refusing an import outright rather than
redacting and continuing. Never imports sanitize_output/SandboxPolicy; that
module's redaction behavior is a different concern from this one's refuse-or-pass
decision.
"""
from __future__ import annotations

from agents_orchestrator.development_agent.tools.sandbox_policy import _SECRET_PATTERNS

# One label per _SECRET_PATTERNS entry, same order -- never echoes the matched
# text itself, only which category matched (an error message that echoed a
# leaked secret back to the caller would defeat the point of catching it).
_CATEGORY_LABELS: list[str] = [
    "password/secret assignment",
    "Bearer token",
    "GitHub personal access token",
    "API key (OpenAI/Anthropic-shaped)",
    "credentials embedded in a URL",
]

# zip() below silently truncates to the shorter list -- a future _SECRET_PATTERNS
# entry added in sandbox_policy.py (an unrelated module, by someone with no reason
# to know this file exists) would otherwise go unscanned with no error anywhere
# (final whole-branch review, sub-project 5, Important #3). Fail loudly at import
# time instead of silently under-scanning at request time.
assert len(_SECRET_PATTERNS) == len(_CATEGORY_LABELS), (
    f"_CATEGORY_LABELS ({len(_CATEGORY_LABELS)}) must have exactly one entry per "
    f"_SECRET_PATTERNS entry ({len(_SECRET_PATTERNS)}) -- update both together."
)


def scan_for_credentials(text: str) -> list[str]:
    """Category names of every _SECRET_PATTERNS entry that matches `text`, in
    _SECRET_PATTERNS' own order. Empty list = clean. Never returns the matched
    text itself."""
    hits: list[str] = []
    for (pattern, _replacement), label in zip(_SECRET_PATTERNS, _CATEGORY_LABELS):
        if pattern.search(text or ""):
            hits.append(label)
    return hits
