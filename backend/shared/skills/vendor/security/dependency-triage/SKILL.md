---
name: dependency-triage
description: Triage a reported dependency vulnerability by reachability and exploitability, then choose the safest upgrade path.
when_to_use: A scanner (Dependabot, Snyk, npm/pip audit, Trivy) reports a CVE in a dependency and you must decide whether and how urgently to act.
runtime: llm
---

# Dependency Vulnerability Triage

You turn a raw scanner alert into a decision. Not every CVE is exploitable in your context, and not every fix is a clean bump. The goal is to right-size urgency by reachability and exploitability, then pick the least-risky remediation.

## Procedure

1. **Read the advisory precisely.** Note the affected package, the vulnerable version range, the fixed version, the CVSS score/vector, and — critically — *what the vulnerability actually requires* to trigger (attacker-controlled input to a specific function? a particular config? a particular OS?).
2. **Confirm the dependency type.** Is it a direct or transitive dependency? Runtime, build-time, or test/dev-only? A dev-only or build-time-only vuln is usually far lower urgency than one in the runtime request path. Find the dependency path (`npm ls`, `pip show`, lockfile graph) to see who pulls it in.
3. **Assess reachability.** Does your code actually call the vulnerable code path? Search for imports/usages of the affected module and functions. If the vulnerable function is never invoked and can't be reached via your inputs, real risk drops sharply — record that reasoning explicitly (this is *not* the same as ignoring it).
4. **Assess exploitability in context.** Even if reached: can an attacker actually supply the triggering input given your trust boundaries, auth, and network exposure? A CVE requiring a malicious file upload is irrelevant if you never accept uploads. An RCE reachable from an unauthenticated public endpoint is an emergency.
5. **Choose the remediation, safest first:**
   - **Patch/minor upgrade** to the fixed version — preferred; run the full test suite.
   - **Transitive pin/override** (resolutions/constraints) when a direct dep hasn't updated its transitive requirement yet.
   - **Major upgrade** — only if no compatible fix exists; treat as its own change with regression testing.
   - **Mitigating control** (WAF rule, input restriction, feature disable) as a *temporary* bridge when no upgrade is available.
   - **Document accepted risk** with expiry if truly not reachable/exploitable.
6. **Verify after the fix.** Re-run the scanner to confirm the alert clears, run tests, and check no new transitive vulns were introduced by the bump.

## What good output looks like

- A verdict per alert: urgency (emergency/soon/scheduled/accepted), with reachability and exploitability reasoning stated.
- A specific remediation (exact target version or override), not "upgrade the library".
- Post-fix verification: scanner clean, tests green, no new alerts.

## Pitfalls

- Reacting to CVSS score alone while ignoring reachability — burning time on unreachable vulns.
- The opposite: dismissing a critical, reachable, unauthenticated RCE as "probably fine".
- Blindly running "audit fix --force" and shipping a breaking major bump untested.
- Fixing the direct dep but leaving the vulnerable transitive version pinned elsewhere.
- Accepting risk with no expiry, so it silently persists forever.
