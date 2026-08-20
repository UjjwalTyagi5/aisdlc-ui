"""Selectable LLM providers + models for BYOK onboarding.

The catalog is sourced DYNAMICALLY from LiteLLM's own metadata (provider list,
per-provider models, and the model-cost map) so onboarding tracks LiteLLM's full
provider universe instead of a hardcoded enum — exactly what the gateway can
execute. A small curated set is merged on top (nicer labels + internal aliases)
and pinned first for UX. Built once and cached; pricing is in USD per 1M tokens.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Curated presets: friendly labels + the internal model aliases wired in
# litellm-config.yaml. Merged into the dynamic catalog so these always resolve
# (and existing offerings/tests keep validating) even if LiteLLM renames things.
_CURATED: dict[str, dict] = {
    "anthropic": {
        "label": "Anthropic",
        "models": [
            {"model_id": "claude-opus-4-8", "label": "Claude Opus 4.8"},
            {"model_id": "claude-sonnet-4-6", "label": "Claude Sonnet 4.6"},
            {"model_id": "claude-haiku-4-5-20251001", "label": "Claude Haiku 4.5"},
        ],
    },
    "openai": {
        "label": "OpenAI",
        "models": [
            {"model_id": "gpt-4o", "label": "GPT-4o"},
            {"model_id": "gpt-4.1", "label": "GPT-4.1"},
            {"model_id": "o4-mini", "label": "o4-mini"},
        ],
    },
    "google": {
        "label": "Google",
        "models": [
            {"model_id": "gemini-2.5-pro", "label": "Gemini 2.5 Pro"},
            {"model_id": "gemini-2.5-flash", "label": "Gemini 2.5 Flash"},
        ],
    },
}

# Providers surfaced at the top of the dropdown (most common in enterprises).
_PINNED = ["anthropic", "openai", "google", "azure", "bedrock", "vertex_ai", "mistral", "groq"]

# Friendly labels for common slugs; everything else is title-cased from the slug.
_LABELS = {
    "openai": "OpenAI", "azure": "Azure OpenAI", "azure_ai": "Azure AI",
    "bedrock": "AWS Bedrock", "vertex_ai": "Google Vertex AI", "google": "Google",
    "gemini": "Google Gemini", "mistral": "Mistral", "groq": "Groq",
    "together_ai": "Together AI", "openrouter": "OpenRouter", "fireworks_ai": "Fireworks AI",
    "anthropic": "Anthropic", "cohere": "Cohere", "ollama": "Ollama", "deepseek": "DeepSeek",
    "perplexity": "Perplexity", "xai": "xAI",
}


def _label_for(slug: str) -> str:
    return _LABELS.get(slug) or slug.replace("_", " ").replace("-", " ").title()


def _per_million(cost_per_token) -> float | None:
    try:
        return round(float(cost_per_token) * 1_000_000, 4) if cost_per_token is not None else None
    except (TypeError, ValueError):
        return None


def _build_catalog() -> dict[str, dict]:
    """provider -> {label, models:[{model_id,label,input_price_per_million,output_price_per_million}]}."""
    catalog: dict[str, dict] = {}
    try:
        import litellm

        for model_id, entry in (litellm.model_cost or {}).items():
            if not isinstance(entry, dict):
                continue
            if entry.get("mode") != "chat":  # exclude image/embedding/audio noise
                continue
            provider = entry.get("litellm_provider")
            if not provider:
                continue
            catalog.setdefault(provider, {"label": _label_for(provider), "models": []})
            catalog[provider]["models"].append({
                "model_id": model_id,
                "label": model_id,
                "input_price_per_million": _per_million(entry.get("input_cost_per_token")),
                "output_price_per_million": _per_million(entry.get("output_cost_per_token")),
            })
    except Exception as exc:  # noqa: BLE001 — never let metadata shape break onboarding
        logger.warning("LiteLLM catalog build failed (%s); using curated only", type(exc).__name__)

    # Merge curated on top: ensure provider exists + curated model ids are present.
    for slug, cur in _CURATED.items():
        node = catalog.setdefault(slug, {"label": cur["label"], "models": []})
        node["label"] = cur["label"]
        have = {m["model_id"] for m in node["models"]}
        for m in cur["models"]:
            if m["model_id"] not in have:
                node["models"].insert(0, {**m, "input_price_per_million": None,
                                          "output_price_per_million": None})

    for node in catalog.values():
        node["models"].sort(key=lambda m: m["model_id"])
    return catalog


# BUILT ON FIRST USE, NOT AT IMPORT. `_build_catalog()` imports litellm to read its
# price table, and litellm costs ~7s to import. Doing that at module scope meant every
# importer of this module paid it -- `pytest --collect-only`, every `uvicorn --reload`
# restart, every CLI touching a router -- to build a table most of them never read.
#
# The cache is the module global below, so the build still happens exactly once per
# process. Callers go through `_catalog()`; nothing outside this module reads it.
_CATALOG_CACHE: dict[str, dict] | None = None


def _catalog() -> dict[str, dict]:
    global _CATALOG_CACHE
    if _CATALOG_CACHE is None:
        _CATALOG_CACHE = _build_catalog()
    return _CATALOG_CACHE

# Back-compat alias — the curated presets were historically exported as PROVIDERS.
PROVIDERS = _CURATED


def _ordered_providers() -> list[str]:
    pinned = [p for p in _PINNED if p in _catalog()]
    rest = sorted(p for p in _catalog() if p not in set(pinned))
    return pinned + rest


def list_providers() -> list[dict]:
    """[{provider, label, models:[...]}] for the catalog API (pinned first)."""
    return [
        {"provider": p, "label": _catalog()[p]["label"], "models": _catalog()[p]["models"]}
        for p in _ordered_providers()
    ]


def models_for_provider(provider: str) -> list[dict]:
    return _catalog().get(provider, {}).get("models", [])


def is_valid_model(provider: str, model_id: str) -> bool:
    return any(m["model_id"] == model_id for m in models_for_provider(provider))


def is_known_provider(provider: str) -> bool:
    """True when the provider is in the (LiteLLM + curated) catalog."""
    return provider in _catalog()


def price_for(provider: str, model_id: str) -> tuple[float | None, float | None]:
    """Catalog pricing (USD per 1M tokens) for a model, if known."""
    for m in models_for_provider(provider):
        if m["model_id"] == model_id:
            return m.get("input_price_per_million"), m.get("output_price_per_million")
    return None, None
