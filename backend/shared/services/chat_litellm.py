"""The ChatLiteLLM every agent builds — the same client, minus the process-global writes.

langchain_litellm's own `_client_params` runs on EVERY call and writes that call's
`api_base`, `api_key`, provider key fields (`anthropic_api_key`, ...), `organization` and
`extra_headers` onto `self.client` — and `self.client` is the litellm MODULE. The process
has exactly one, so whichever agent called last decides where every later litellm call
that does not pass its own value is sent, and with whose key.

Live consequence (2026-09-15): a requirements run on azure/gpt-5-mini left
`litellm.api_base` on a business unit's Azure AI Foundry endpoint. Every "Test" of a new
Anthropic key after it — blank API base, valid sk-ant key — went to that endpoint, came
back 404 "Resource not found", and the key itself was sent to Azure. Concurrent runs for
different units share the same globals, so the key half of this is a cross-unit
credential leak, not just a wrong URL.

This subclass sends exactly those values WITH the call instead. Importing it imports
litellm (~7s), so import it inside the function that builds a client, as every call site
already does. tests/test_byok_key_reaches_litellm.py fails if any code goes back to
importing the upstream class.
"""
from __future__ import annotations

from typing import Any

from langchain_litellm import ChatLiteLLM as _UpstreamChatLiteLLM

from shared.services.model_resolver import _LITELLM_NAMED_KEY_FIELD


class ChatLiteLLM(_UpstreamChatLiteLLM):
    @property
    def _client_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {
            **self._default_params,
            "model": self.model_name if self.model_name is not None else self.model,
            "timeout": self.request_timeout,
            "api_base": self.api_base,
        }
        key = self.api_key or self._named_key()
        if key:
            params["api_key"] = key
        if self.organization:
            params["organization"] = self.organization
        if self.extra_headers is not None:
            params["extra_headers"] = self.extra_headers
        return params

    def _named_key(self) -> str | None:
        """The provider's own key field, for a client built without a generic `api_key`.

        Second choice, never first: upstream defaults these fields from the environment
        (ANTHROPIC_API_KEY, ...), which is exactly how a platform key once beat a tenant's
        BYOK key. Call sites still pass `litellm_key_kwargs` so the field and `api_key`
        agree.
        """
        field = _LITELLM_NAMED_KEY_FIELD.get((self.custom_llm_provider or "").lower())
        if not field:
            return None
        return getattr(self, field, None) or None
