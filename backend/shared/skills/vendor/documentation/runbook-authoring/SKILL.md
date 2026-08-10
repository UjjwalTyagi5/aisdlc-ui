---
name: runbook-authoring
description: Write an operational runbook — symptoms, diagnosis, remediation, and escalation — that an on-call engineer can follow under pressure.
when_to_use: A service, alert, or recurring incident needs a documented response procedure so anyone on-call can act without tribal knowledge.
runtime: llm
---

# Runbook Authoring

You write a runbook that a tired on-call engineer, who did not build the system, can follow at 3am and resolve the issue. Optimize for speed and unambiguity, not completeness. Every step is concrete and copy-pasteable; every decision point is explicit.

## Procedure

1. **Title and scope.** Name the specific alert or failure this runbook covers ("High 5xx rate on checkout-api"). One runbook per failure mode — don't make a catch-all. State severity and impact so the responder knows how hard to push.
2. **Symptoms.** List the observable signals that lead here: the exact alert name, dashboard panels, log signatures, and user-facing symptoms. Include links to the relevant dashboard/query so the responder lands there in one click. This is how someone confirms they're in the right runbook.
3. **Diagnosis — a decision tree.** Give ordered checks that narrow the cause, each with the exact command/query to run and how to interpret the result. Structure as "If X, go to step N; else continue." Start with the cheapest, most-likely checks (is the dependency down? recent deploy? traffic spike?). Include the commands verbatim, with placeholders clearly marked (`<service>`, `<region>`).
4. **Remediation.** For each identified cause, the exact fix steps: restart, scale, roll back (link the rollback runbook), flip a flag, clear a queue. Mark any **destructive or risky** action clearly and state its blast radius before the command. Prefer the safest mitigation that restores service; note that root-cause fixes can follow after the incident.
5. **Verification.** How to confirm the issue is resolved: which metric should return to baseline, which check should pass, what to watch for a recurrence window.
6. **Escalation.** When to escalate and to whom — the threshold ("if not resolved in 15 min or if data loss suspected"), the team/rotation to page, and what context to hand over. Include the "declare an incident" trigger.
7. **Metadata.** Owner, last-reviewed date, and links to related runbooks and the architecture doc.

## What good output looks like

- Symptoms that unambiguously confirm the responder is in the right place, with one-click links.
- A diagnosis decision tree with exact, runnable commands and clear branch conditions.
- Remediation steps that are safe-by-default, with risky actions flagged and blast radius stated.
- Explicit verification and escalation criteria with names/rotations.
- A recent last-reviewed date.

## Pitfalls

- Vague steps ("check if the service is healthy") with no command or success criterion.
- Assuming the reader knows the architecture — write for the newest on-call.
- A destructive command with no warning about its impact.
- No escalation path, so the responder is stranded when the runbook doesn't resolve it.
- A stale runbook referencing renamed services/dashboards — set a review cadence.
