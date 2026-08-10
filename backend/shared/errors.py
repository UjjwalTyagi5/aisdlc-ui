"""Shared user-facing error messages for all agents.

Import classify_error in tool except blocks to return actionable messages
instead of raw exception strings.
"""
from __future__ import annotations


def classify_error(exc: Exception, context: str = "processing") -> str:
    """Return a user-friendly error string for the given exception."""
    name = type(exc).__name__
    msg = str(exc)

    # ── OpenAI / Azure OpenAI errors ──────────────────────────────────────────
    if "RateLimitError" in name or "429" in msg:
        return (
            "The AI model is currently rate-limited. "
            "Please wait 30 seconds and try again."
        )
    if "AuthenticationError" in name or "401" in msg:
        return (
            "AI model authentication failed. "
            "Please check your Azure OpenAI API key in Settings → Connectors."
        )
    if "PermissionDeniedError" in name or "403" in msg:
        return (
            "Access denied by the AI model service. "
            "Verify your API key has the correct permissions."
        )
    if "APITimeoutError" in name or "timeout" in msg.lower():
        return (
            "The AI model request timed out. "
            "Please try again — large documents may need more time."
        )
    if "APIConnectionError" in name or "Connection" in name:
        return (
            "Could not reach the AI model. "
            "Check your network connection and try again."
        )

    # ── Azure DevOps / HTTP provider errors ───────────────────────────────────
    if "HTTPStatusError" in name:
        if "429" in msg:
            return (
                "Azure Boards is rate-limiting requests. "
                "Please wait 30 seconds and try again."
            )
        if "401" in msg or "403" in msg:
            return (
                "Authentication failed for Azure Boards. "
                "Please check your connector credentials in Settings → Connectors."
            )
        if "404" in msg:
            return (
                "The requested Azure Boards item was not found. "
                "Verify the project name and work item ID."
            )
        return f"Azure Boards returned an error during {context}. Please try again."

    if "TimeoutException" in name:
        return (
            "The request to Azure Boards timed out. "
            "Check your network connection and try again."
        )

    # ── File / IO errors ──────────────────────────────────────────────────────
    if "FileNotFoundError" in name:
        return f"File not found during {context}. Please re-upload the file and try again."
    if "PermissionError" in name:
        return f"Permission denied accessing a file during {context}."

    # ── Generic fallback ──────────────────────────────────────────────────────
    return (
        f"An unexpected error occurred during {context}. "
        "Please try again or contact support if the problem persists."
    )
