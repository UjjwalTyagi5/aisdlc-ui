"""Redact secrets from trace payloads before they leave the process.

PRD 34.8 says traces are "read-only for everyone, secrets are redacted at write time".
Nothing implemented that. Traces carry prompts, tool call arguments and model output —
the richest payload this platform produces — and they land in a Langfuse instance shared
with other products, on a ClickHouse cluster in another region, retained for as long as
its policy says. A credential pasted into a chat turn, echoed by a tool, or read out of
an environment by an agent was being shipped verbatim and kept.

THIS IS APPLIED AS THE SDK'S `mask` HOOK, not at call sites. The Langfuse client runs it
over every input and output it serialises, so there is no path that can forget it — which
matters more than the pattern list, because the fifteen agent call sites had already
proved that per-site discipline does not hold.

IT FAILS CLOSED. If masking raises for any reason the original value is NOT passed
through; a placeholder goes instead. A redactor that degrades to "send everything" on an
unexpected input shape is worse than none, because the guarantee is still being claimed.

WHAT IT DOES NOT DO. This is secret redaction, not PII redaction — names, emails and
customer data in prompts are untouched, and the retention policy is what bounds those.
Pattern matching also cannot catch an opaque credential with no distinguishing shape (an
Azure client secret is 40-odd random characters); those are caught only when they appear
under a key that names them, which is why key-based redaction runs alongside the patterns.
"""
from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"

# Keys whose VALUE is a secret whatever it looks like. This is the half that catches
# high-entropy credentials with no recognisable shape — an Azure client secret is forty
# random characters and matches no pattern.
#
# MATCHED ON WORD PARTS, NOT SUBSTRINGS. A naive substring test redacts `tokens: 42`,
# because "token" is inside "tokens" — and input/output token counts are on essentially
# every generation span, so the cost and usage figures would have been blanked out of
# every trace by the control meant to protect them. Keys are split on separators and
# camelCase, then matched exactly.
_SECRET_WORDS = frozenset({
    "password", "passwd", "pwd",
    "secret", "token", "credential", "credentials",
    "authorization", "dsn",
})

# Compounds, matched against the key with separators stripped, so "x-api-key",
# "apiKey" and "API_KEY" all resolve to the same thing.
_SECRET_COMPOUNDS = (
    "apikey", "apisecret", "privatekey", "clientsecret", "secretkey",
    "accesskey", "sastoken", "authheader", "connectionstring", "connstring",
    "bearertoken", "refreshtoken", "accesstoken",
)

# The subset worth scanning for in FREE TEXT as `key=value`. "authorization" is
# deliberately absent: the Bearer/Basic rule already redacts that value and keeps the
# scheme, and running both turned "Authorization: Bearer <tok>" into two redactions with
# the scheme lost — which removes the one part of the header worth reading in a trace.
_TEXT_KEY_WORDS = (
    "password", "passwd", "secret", "api_key", "apikey", "api-key",
    "client_secret", "private_key", "access_token", "refresh_token",
    "connection_string", "sas_token",
)

# Value shapes worth catching wherever they appear — including inside free text, which is
# where a pasted key or an echoed env var actually shows up.
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Langfuse's own key pair. First because this codebase handles them directly and a
    # leaked sk-lf- grants read access to every trace in the project.
    (re.compile(r"\b[ps]k-lf-[A-Za-z0-9\-_]{8,}"), REDACTED),
    # Anthropic, then OpenAI-style. sk-ant- is matched first: the generic sk- rule would
    # otherwise truncate it and leave the tail in place.
    (re.compile(r"\bsk-ant-[A-Za-z0-9\-_]{8,}"), REDACTED),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}"), REDACTED),
    # Google / GCP.
    (re.compile(r"\bAIza[0-9A-Za-z\-_]{20,}"), REDACTED),
    # GitHub personal access / app tokens.
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"), REDACTED),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), REDACTED),
    # Slack.
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"), REDACTED),
    # AWS access key id (the secret itself is shapeless and relies on key matching).
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), REDACTED),
    # Pinecone.
    (re.compile(r"\bpcsk_[A-Za-z0-9\-_]{20,}"), REDACTED),
    # PEM private keys — the whole block, not just the header.
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.S,
        ),
        REDACTED,
    ),
    # JWTs (three base64url segments). Catches bearer tokens pasted without the scheme.
    (re.compile(r"\beyJ[A-Za-z0-9\-_]{8,}\.[A-Za-z0-9\-_]{8,}\.[A-Za-z0-9\-_]{8,}"), REDACTED),
    # Authorization headers, whatever the scheme.
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9\-._~+/=]{8,}"), r"\1 " + REDACTED),
    # Credentials embedded in a URL: postgresql://user:pass@host, amqps://…, https://…
    # Keeps the scheme, user and host so the log still says WHICH service; drops only
    # the password, which is the part that matters and the part people paste.
    (re.compile(r"\b([a-zA-Z][a-zA-Z0-9+.\-]*://[^\s:/@]+):([^\s@]+)@"), r"\1:" + REDACTED + "@"),
    # key=value / "key": "value" forms in free text, for the same key list used on dicts.
    (
        re.compile(
            r"(?i)\b(" + "|".join(re.escape(k) for k in _TEXT_KEY_WORDS) + r")"
            r"(\s*[:=]\s*)([\"']?)([^\s\"',}]{4,})\3"
        ),
        r"\1\2\3" + REDACTED + r"\3",
    ),
)

# Guards against pathological payloads. A trace can carry an arbitrarily nested tool
# result, and masking must stay cheap enough to run on every span.
_MAX_DEPTH = 12
_MAX_TEXT = 200_000


def redact_text(text: str) -> str:
    """Apply every value pattern to one string."""
    if not text:
        return text
    # Very large blobs are truncated before scanning rather than after: running a dozen
    # regexes (one with DOTALL) over a multi-megabyte tool result on every span is the
    # kind of cost that turns tracing into the bottleneck it is meant to measure.
    if len(text) > _MAX_TEXT:
        text = text[:_MAX_TEXT] + "…[truncated]"
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


_KEY_SPLIT = re.compile(r"[^A-Za-z0-9]+|(?<=[a-z0-9])(?=[A-Z])")


def _is_sensitive_key(key: Any) -> bool:
    """True when the key NAMES a secret, by whole word rather than by substring."""
    if not isinstance(key, str):
        return False
    parts = [p.lower() for p in _KEY_SPLIT.split(key) if p]
    if any(p in _SECRET_WORDS for p in parts):
        return True
    joined = "".join(parts)
    return any(c in joined for c in _SECRET_COMPOUNDS)


# Words whose value is redacted even when numeric. A PIN is plausible; a token COUNT is
# not a token. Everything else relies on the rule below: a credential is not an integer,
# so `token_count: 1234` and `max_tokens: 4096` keep their values while `token: "abc"`
# does not.
_NUMERIC_STILL_SECRET = frozenset({"password", "passwd", "pwd", "secret"})


def _redacts_numbers(key: str) -> bool:
    return any(p.lower() in _NUMERIC_STILL_SECRET for p in _KEY_SPLIT.split(key) if p)


def _by_key(key: Any, value: Any, depth: int) -> Any:
    if _is_sensitive_key(key) and (
        not isinstance(value, (int, float, bool))
        or (isinstance(key, str) and _redacts_numbers(key))
    ):
        return REDACTED
    return _walk(value, depth + 1)


def _walk(value: Any, depth: int) -> Any:
    if depth > _MAX_DEPTH:
        return REDACTED
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {k: _by_key(k, v, depth) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_walk(v, depth + 1) for v in value]
    # Numbers, bools, None and anything else JSON-native pass through. Objects the SDK
    # will str() later are stringified here so their contents are scanned too.
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return redact_text(str(value))


def mask_sensitive(*, data: Any, **_kwargs: Any) -> Any:
    """Langfuse `mask` hook: scrub secrets from one input/output payload.

    Signature is fixed by langfuse.types.MaskFunction (keyword-only `data`).
    """
    try:
        return _walk(data, 0)
    except Exception:
        # FAIL CLOSED. Returning `data` here would mean any input shape that trips the
        # walker silently ships unredacted — the exact failure a redactor must not have.
        return "[REDACTION FAILED]"
