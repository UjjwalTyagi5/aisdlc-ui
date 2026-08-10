"""Sandbox policy for Development Agent command and code execution.

All tool execution passes through validate_command / sanitize_output so that:
- shell control operators are rejected before subprocess is called
- bash execution is disabled unless explicitly opted-in
- output length is capped to keep LLM context manageable
- common credential patterns are redacted from tool results
"""
from __future__ import annotations

import re
from dataclasses import dataclass


# ── Shell operator detection ──────────────────────────────────────────────────

# Matches metacharacters that make a command string behave differently under a
# shell than it would as a plain argv list.  We reject these even though
# run_command already uses shlex + shell=False — defense in depth ensures a
# future shell=True regression can't sneak past the agent boundary.
_SHELL_OPERATORS_RE = re.compile(
    r"""
    (?:&&|\|\|)   # AND / OR chaining
    | [;|`]       # semicolons, pipe, backtick substitution
    | \$\(        # $( ... ) command substitution
    | \$\{        # ${ ... } variable expansion
    | \n          # newline injection
    """,
    re.VERBOSE,
)


# ── Secret redaction patterns ─────────────────────────────────────────────────

_SECRET_PATTERNS: list[tuple[re.Pattern, str]] = [
    # password= / pwd= / secret= in connection strings or env output
    (re.compile(r"((?:password|pwd|secret)\s*=\s*)\S+", re.IGNORECASE), r"\1***"),
    # Bearer <token>
    (re.compile(r"(Bearer\s+)\S{8,}", re.IGNORECASE), r"\1***"),
    # GitHub PATs   ghp_...
    (re.compile(r"\bghp_[A-Za-z0-9_]{30,}\b"), "ghp_***"),
    # OpenAI / Anthropic SDK keys   sk-...
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"), "sk-***"),
    # Credentials embedded in clone URLs  https://user:token@host
    (re.compile(r"(https?://)([^:@\s]+):([^:@\s]{8,})@"), r"\1***:***@"),
]

_TRUNCATION_NOTICE = "\n... [output capped by sandbox policy]"

# ── Command classification ────────────────────────────────────────────────────

_TEST_PREFIXES = (
    "pytest ", "jest ", "npm test", "npm run test",
    "yarn test", "pnpm test", "python -m pytest",
)
_BUILD_PREFIXES = (
    "npm run build", "tsc ", "ng build",
    "dotnet build", "go build", "mvn ", "cargo build",
)


# ── Policy ────────────────────────────────────────────────────────────────────

@dataclass
class SandboxPolicy:
    """Runtime policy applied to every command / sandbox execution."""

    allow_bash: bool = False
    """Bash execution is disabled by default — set True only in dev environments."""

    allow_shell_operators: bool = False
    """Reject shell control chars even when shell=False is used."""

    output_cap_bytes: int = 50_000
    """Truncate combined stdout+stderr to this many bytes."""

    redact_secrets: bool = True
    """Redact known credential patterns from tool output."""


# Module-level default used by both run_command and execute_code_in_sandbox.
DEFAULT_POLICY = SandboxPolicy()


# ── Public helpers ────────────────────────────────────────────────────────────

def validate_command(command: str, policy: SandboxPolicy = DEFAULT_POLICY) -> str | None:
    """Return a user-facing error message if *command* violates *policy*, else None.

    Checks:
    - shell operator / metacharacter presence
    (allowlist prefix check lives in run_command itself)
    """
    if not policy.allow_shell_operators:
        m = _SHELL_OPERATORS_RE.search(command)
        if m:
            op = m.group().strip() or repr(m.group())
            return (
                f"Command rejected: shell operator '{op}' is not allowed. "
                "Provide a single command without pipes, semicolons, chaining, "
                "or variable substitution."
            )
    return None


def classify_command(command: str) -> str:
    """Return 'test', 'build', or 'command' based on the command prefix."""
    cmd = command.strip()
    if any(cmd.startswith(p) for p in _TEST_PREFIXES):
        return "test"
    if any(cmd.startswith(p) for p in _BUILD_PREFIXES):
        return "build"
    return "command"


def sanitize_output(output: str, policy: SandboxPolicy = DEFAULT_POLICY) -> str:
    """Apply secret redaction and length cap to command/sandbox output."""
    if policy.redact_secrets:
        for pattern, replacement in _SECRET_PATTERNS:
            output = pattern.sub(replacement, output)

    if len(output.encode()) > policy.output_cap_bytes:
        # Truncate to byte boundary, then decode safely
        truncated = output.encode()[: policy.output_cap_bytes].decode("utf-8", errors="ignore")
        output = truncated + _TRUNCATION_NOTICE

    return output
