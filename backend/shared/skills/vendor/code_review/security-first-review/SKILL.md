---
name: security-first-review
description: Review a change for security defects — injection, broken authorization, secret handling, and unsafe input — before spending attention on style.
when_to_use: Reviewing a diff or PR, especially one touching auth, data access, external input, file/OS operations, or configuration.
runtime: llm
---

# Security-First Review

You review the change for exploitable defects before anything cosmetic. Order matters: a perfectly-formatted endpoint with an authorization hole is a failing review. Prioritize by blast radius.

## Procedure

Work through these in order; for each finding, state the vulnerability, how it could be exploited, and the concrete fix.

1. **Injection.** Any place untrusted input reaches an interpreter: SQL/NoSQL (must use parameterized queries/ORM binding, never string concatenation), OS commands (avoid shell; pass argument arrays; never interpolate input into a shell string), template engines (auto-escaping on), LDAP, and path traversal (`../`) in file operations. Flag every dynamic query built by concatenation.
2. **Authorization & authentication.** For each new/changed endpoint or action: is it authenticated, and is it *authorized* for this specific resource? Look for missing object-level checks (IDOR — can user A pass user B's id and get B's data?), role checks done in the UI but not the server, and privilege escalation paths. Verify tenant/row isolation is enforced server-side, not assumed.
3. **Secrets & sensitive data.** No hardcoded credentials, tokens, or keys in code or config committed to the repo. Secrets come from a vault/env, never logged. Check that error messages, logs, and responses don't leak PII, tokens, or internal detail. Confirm sensitive data at rest/in transit uses the expected encryption.
4. **Unsafe input handling.** Validate and canonicalize input at the trust boundary: type, length, range, allowed set. Prefer allow-lists over deny-lists. Check for deserialization of untrusted data, SSRF (server fetching a user-supplied URL), open redirects, and XXE in XML parsing. For web output, verify contextual output-encoding to prevent XSS.
5. **Dependencies & config.** New dependency — is it necessary and reputable? Any dangerous default left on (debug mode, permissive CORS `*` with credentials, disabled TLS verification)?

## What good output looks like

- Findings ordered by severity (critical/high/medium/low), each with: the exact location, the exploit scenario, and the specific remediation.
- A clear pass/block verdict — block if any critical/high is unmitigated.
- Explicit confirmation of the security-relevant things you checked and found sound.

## Pitfalls

- Leading with style nits and burying an authz hole at the bottom.
- Trusting client-side validation as a control.
- Assuming an ORM makes you injection-proof (raw fragments and `LIKE` patterns still bite).
- Missing object-level authorization (IDOR) because the endpoint "requires login".
- Approving a logged token or a plaintext secret because "it's only in dev".
