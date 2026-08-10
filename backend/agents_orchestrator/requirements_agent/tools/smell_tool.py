"""NLTK-backed rule-based requirement smell detection (reference §4.1 Step 3).

Uses NLTK's punkt sentence tokenizer when available; falls back to a regex sentence
split otherwise. All smell rules are pure-Python and run regardless of NLTK presence.
"""
from __future__ import annotations

import json
import logging
import re

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

# Verbs that are too vague to test without a measurable qualifier.
_UNTESTABLE_VERBS = {
    "support", "handle", "manage", "process", "deal with", "cope with",
    "accommodate", "facilitate", "leverage", "utilize",
}
_MEASURABLE_HINT = re.compile(
    r"\b(\d+|within|less than|greater than|at most|at least|per|ms|millisecond|second|%|percent)\b",
    re.IGNORECASE,
)
_AMBIGUOUS_PRONOUN_START = re.compile(r"^\s*(it|they|this|that|these|those)\b", re.IGNORECASE)


def _split_sentences(text: str) -> list[str]:
    try:
        import nltk  # noqa: PLC0415
        try:
            return nltk.sent_tokenize(text)
        except LookupError:
            try:
                nltk.download("punkt", quiet=True)
                return nltk.sent_tokenize(text)
            except Exception:
                pass
    except Exception:
        pass
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


@tool
async def run_requirement_smell_check(text: str) -> str:
    """Detect rule-based requirement smells (untestable verbs, compound requirements,
    ambiguous pronouns, missing measurability).

    Args:
        text: Requirement text (one or more sentences).

    Returns:
        JSON string of detected smells, one entry per (sentence, smell).
    """
    if not text or not text.strip():
        return json.dumps({"status": "empty", "smells": []})

    smells: list[dict] = []
    for sentence in _split_sentences(text):
        low = sentence.lower()

        for verb in _UNTESTABLE_VERBS:
            if re.search(r"\b" + re.escape(verb) + r"\b", low) and not _MEASURABLE_HINT.search(sentence):
                smells.append({"sentence": sentence, "smell": "untestable_verb", "detail": verb})
                break

        # Compound requirement: 3+ clause-joining conjunctions in one sentence.
        if len(re.findall(r"\b(and|or)\b", low)) >= 3:
            smells.append({"sentence": sentence, "smell": "compound_requirement",
                           "detail": "split into atomic requirements"})

        if _AMBIGUOUS_PRONOUN_START.match(sentence):
            smells.append({"sentence": sentence, "smell": "ambiguous_pronoun",
                           "detail": "sentence opens with an unresolved pronoun"})

    return json.dumps({"status": "ok", "smells": smells})
