"""Deterministic, offline scoring contract for the M9.3 eval harness (REQ-M9-10/13).

`score_output()` is the single public entrypoint plans 02 (emission) and 03 (read API)
import. It MUST stay deterministic and MUST NOT call an LLM (D-M9-03, REQ-M9-13) so CI
can run the eval suite without network access or API keys.

Scoring strategies:
  - "design_architecture": required-section presence ratio. Mirrors the 7 mandatory
    HLD/LLD/... headers documented in CLAUDE.md / messageParser.jsx parseArtifactSections.
  - all other agent_types (default): normalized token-set overlap (Jaccard-style)
    between `actual` and `expected`.

Both strategies are pure string/set operations — no I/O, no network, no randomness.
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

# The 7 mandatory Design Agent output sections (see CLAUDE.md "Design Agent — 7
# mandatory output sections" and messageParser.jsx parseArtifactSections).
DESIGN_REQUIRED_SECTIONS: tuple[str, ...] = (
    "High-Level Design (HLD)",
    "Low-Level Design (LLD)",
    "C4 Architecture Diagram",
    "API Contracts",
    "Database Schema",
    "Architecture Decision Records (ADRs)",
    "Technology Stack & Infrastructure",
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


class EvalSignals(BaseModel):
    """Result of scoring an agent output against an expected/golden value.

    `score` is always clamped to [0.0, 1.0]. `signals` carries the raw,
    strategy-specific measurements that produced `score` (e.g. matched
    section names, token overlap counts) for downstream display/debugging.
    """

    score: float = Field(ge=0.0, le=1.0)
    signals: dict[str, Any] = Field(default_factory=dict)


def _tokenize(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def _score_design_sections(actual: str) -> EvalSignals:
    present = [section for section in DESIGN_REQUIRED_SECTIONS if section in actual]
    missing = [section for section in DESIGN_REQUIRED_SECTIONS if section not in actual]
    total = len(DESIGN_REQUIRED_SECTIONS)
    score = len(present) / total if total else 0.0
    return EvalSignals(
        score=round(score, 4),
        signals={
            "strategy": "design_section_presence",
            "present_sections": present,
            "missing_sections": missing,
            "required_count": total,
            "present_count": len(present),
        },
    )


def _score_token_overlap(actual: str, expected: str) -> EvalSignals:
    actual_tokens = _tokenize(actual)
    expected_tokens = _tokenize(expected)

    if not expected_tokens:
        # Nothing to compare against — perfect match only if actual is also empty.
        score = 1.0 if not actual_tokens else 0.0
        return EvalSignals(
            score=score,
            signals={
                "strategy": "token_set_overlap",
                "expected_token_count": 0,
                "actual_token_count": len(actual_tokens),
                "overlap_count": 0,
            },
        )

    overlap = actual_tokens & expected_tokens
    union = actual_tokens | expected_tokens
    score = len(overlap) / len(union) if union else 0.0

    return EvalSignals(
        score=round(score, 4),
        signals={
            "strategy": "token_set_overlap",
            "expected_token_count": len(expected_tokens),
            "actual_token_count": len(actual_tokens),
            "overlap_count": len(overlap),
            "union_count": len(union),
        },
    )


def score_output(agent_type: str, actual: str | None, expected: str) -> EvalSignals:
    """Score an agent's actual output against an expected/golden value.

    Deterministic and offline (D-M9-03, REQ-M9-13) — never calls an LLM.

    Args:
        agent_type: which agent produced `actual` (e.g. "design_architecture",
            "requirements"). Selects the scoring strategy.
        actual: the agent's actual output text. None or empty scores 0.0
            (unless `expected` is also empty, which scores 1.0 as a vacuous match).
        expected: the golden/expected output text.

    Returns:
        EvalSignals with `score` in [0.0, 1.0] and a `signals` dict describing
        how the score was computed.
    """
    actual_text = actual or ""

    if agent_type == "design_architecture":
        if not actual_text:
            return EvalSignals(
                score=0.0,
                signals={
                    "strategy": "design_section_presence",
                    "present_sections": [],
                    "missing_sections": list(DESIGN_REQUIRED_SECTIONS),
                    "required_count": len(DESIGN_REQUIRED_SECTIONS),
                    "present_count": 0,
                },
            )
        return _score_design_sections(actual_text)

    return _score_token_overlap(actual_text, expected)
