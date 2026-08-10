# platform/backend/agents_orchestrator/requirements_agent/tools/nlp_quality_tool.py
"""spaCy-backed NLP quality check for the Requirements Agent (reference §4.1 Step 3).

Always runs a pure-Python weak-term scan. Uses spaCy (en_core_web_sm) for passive-voice
detection + entity extraction WHEN available; degrades gracefully when the model or the
spacy package is absent.
"""
from __future__ import annotations

import json
import logging
import re

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

# Vague / unquantified terms that make requirements untestable.
_WEAK_TERMS = [
    "fast", "slow", "quick", "user-friendly", "efficient", "robust", "scalable",
    "flexible", "seamless", "intuitive", "minimal", "optimal", "approximately",
    "several", "some", "various", "appropriate", "as needed", "etc", "and/or",
    "easy", "simple", "reliable", "secure enough", "state-of-the-art",
]

_spacy_nlp = None
_spacy_tried = False


def _get_spacy():
    global _spacy_nlp, _spacy_tried
    if _spacy_tried:
        return _spacy_nlp
    _spacy_tried = True
    try:
        import spacy  # noqa: PLC0415
        _spacy_nlp = spacy.load("en_core_web_sm")
    except Exception:
        _spacy_nlp = None
    return _spacy_nlp


@tool
async def run_nlp_quality_check(text: str) -> str:
    """Run deterministic NLP quality checks on requirement text.

    Flags vague/weak terms, passive-voice sentences (spaCy), and extracts named
    entities (spaCy) for traceability. Use BEFORE drafting stories/BRD so smells
    are caught measurably, not just by judgment.

    Args:
        text: Requirement text (a story, AC block, or pasted BRD section).

    Returns:
        JSON string of quality findings.
    """
    if not text or not text.strip():
        return json.dumps({"status": "empty", "weak_terms": [], "passive_sentences": [], "entities": []})

    lowered = text.lower()
    weak_terms = []
    for term in _WEAK_TERMS:
        for m in re.finditer(r"\b" + re.escape(term) + r"\b", lowered):
            weak_terms.append({"term": term, "offset": m.start()})

    nlp = _get_spacy()
    passive_sentences: list[str] = []
    entities: list[dict] = []
    if nlp is not None:
        try:
            doc = nlp(text)
            for sent in doc.sents:
                if any(tok.dep_ in ("nsubjpass", "auxpass") for tok in sent):
                    passive_sentences.append(sent.text.strip())
            entities = [{"text": e.text, "label": e.label_} for e in doc.ents]
        except Exception:
            pass

    return json.dumps({
        "status": "ok",
        "weak_terms": weak_terms,
        "passive_sentences": passive_sentences,
        "entities": entities,
        "spacy_available": nlp is not None,
    })
