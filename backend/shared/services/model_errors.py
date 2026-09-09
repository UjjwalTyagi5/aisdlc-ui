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


#: Checked BEFORE the type map, because these arrive under a type whose name is
#: actively misleading — a spend cap is a `BadRequestError`, indistinguishable by type
#: from a genuinely malformed request. Matched against the exception text but NEVER
#: returning it: the provider's message is a signal here, not output, so the module's
#: rule about never echoing `str(exc)` still holds.
_BY_MESSAGE_SIGNAL: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("usage limit", "spend limit", "spending limit", "usage limits",
         "credit balance", "insufficient_quota", "insufficient quota",
         "exceeded your current quota", "billing hard cap", "quota exceeded"),
        "The model provider has stopped accepting requests on this credential because "
        "the account or workspace has reached its usage or spend limit. Nothing is "
        "wrong with your request, and retrying will not help. An administrator can "
        "raise the limit with the provider, or pick a model from a different provider "
        "connection for this project in Model Management.",
    ),
)


def friendly_model_error(exc: BaseException) -> str:
    """A safe, actionable sentence for a failed model call.

    Falls back to the bare type name — the previous behaviour for everything — so an
    unrecognised failure is still reported without ever risking the exception text.
    """
    # The signal pass first — a spend cap would otherwise be answered as a
    # configuration problem, which is the wrong action and sends people into the code.
    haystack = f"{exc}".lower()
    for needles, sentence in _BY_MESSAGE_SIGNAL:
        if any(n in haystack for n in needles):
            return sentence

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
