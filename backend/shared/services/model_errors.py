"""What to tell a person when a model call fails.

NEVER `str(exc)`. A BYOK provider error can echo the tenant's own API key back in its
message, which is why the agent nodes were reduced to `type(exc).__name__` — and that
is how a user came to be shown, as an entire reply:

    Agent error: RateLimitError

True, safe, and useless. It does not say the deployment is throttled rather than
broken, that waiting fixes it, that a different model would work now, or that nothing
they did caused it. A class name is a log line, not an answer.

So: map the failures that have a DIFFERENT action attached to a sentence naming that
action, and fall back to the type name for everything else — which keeps the safety
property while removing the cases that actually happen.
"""
from __future__ import annotations

#: Matched on the exception TYPE NAME, not by importing provider SDK classes: litellm
#: re-exports these under several module paths and the set shifts between versions, so
#: an isinstance chain here would silently stop matching after an upgrade. The name is
#: the stable part.
_BY_TYPE_NAME: dict[str, str] = {
    "RateLimitError": (
        "The model provider is rate limiting this deployment right now — this is a "
        "temporary limit on the provider's side, not a problem with your request. "
        "Wait a moment and try again, or switch to a different model for this project "
        "in Model Management."
    ),
    "AuthenticationError": (
        "The model provider rejected the configured credential. An administrator can "
        "re-enter and verify the API key in Org Settings → Model Providers."
    ),
    "PermissionDeniedError": (
        "The model credential was accepted but is not permitted to use this model. "
        "An administrator can check the deployment's access in Org Settings → Model "
        "Providers."
    ),
    "NotFoundError": (
        "The configured model or deployment could not be found at the provider. An "
        "administrator can re-check the model name and endpoint in Org Settings → "
        "Model Providers."
    ),
    "ContextWindowExceededError": (
        "This conversation has grown past the model's context limit. Start a new chat, "
        "or ask for a narrower piece of work."
    ),
    "BadRequestError": (
        "The model provider rejected the request as malformed. This is usually a model "
        "configuration problem rather than anything you typed — an administrator can "
        "check the model settings."
    ),
    "APIConnectionError": (
        "The model provider could not be reached. Check network access to the provider "
        "and try again."
    ),
    "APITimeoutError": (
        "The model took too long to respond and the request timed out. Try again, or "
        "ask for a smaller piece of work."
    ),
    "Timeout": (
        "The model took too long to respond and the request timed out. Try again, or "
        "ask for a smaller piece of work."
    ),
    "ServiceUnavailableError": (
        "The model provider is temporarily unavailable. Try again shortly."
    ),
    "InternalServerError": (
        "The model provider returned an internal error. Try again shortly."
    ),
}


def friendly_model_error(exc: BaseException) -> str:
    """A safe, actionable sentence for a failed model call.

    Falls back to the bare type name — the previous behaviour for everything — so an
    unrecognised failure is still reported without ever risking the exception text.
    """
    name = type(exc).__name__
    known = _BY_TYPE_NAME.get(name)
    if known:
        return known
    # Retry wrappers nest the real cause; check one level down before giving up, since
    # the outer type is often a generic wrapper with nothing useful in the name.
    cause = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)
    if cause is not None:
        nested = _BY_TYPE_NAME.get(type(cause).__name__)
        if nested:
            return nested
    return f"The agent hit an error while generating a response ({name}). Please try again."
