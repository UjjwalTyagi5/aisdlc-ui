---
name: release-readiness-checklist
description: Verify a release is ready to ship — migrations, feature flags, monitoring, and rollback gates are all in place.
when_to_use: Before promoting a build to production (or a staging gate), to confirm the operational prerequisites are met, not just that tests pass.
runtime: llm
---

# Release Readiness Checklist

You confirm a release is *operationally* ready, not merely code-complete. Passing tests means the code probably works; readiness means you can deploy it, observe it, and recover from it. Walk the gates and give a go/no-go per item.

## Procedure

Assess each gate; any unmet critical gate is a no-go.

1. **Code & tests.** All required checks green (unit, integration, e2e), no known critical/high bugs open against this release, and the diff has been reviewed. Version/tag is set and the changelog is updated (see changelog-discipline).
2. **Migrations.** Any DB migration is backward-compatible with the currently-running code (expand/contract), tested against a production-like snapshot, and reversible or explicitly documented as not. Estimate lock/blocking time on large tables and confirm it's acceptable. Migration runs as a distinct, ordered step relative to the code deploy.
3. **Feature flags.** New behaviour is behind a flag, defaulting **off**, with a documented rollout plan (internal → % canary → GA) and a kill switch. Confirm the flag's default in production config, not just in code.
4. **Configuration & secrets.** All new config keys and secrets exist in the target environment's store, with correct values, before deploy. No new hardcoded values. Backward-compatible defaults for anything not yet set.
5. **Monitoring & alerting.** Dashboards and alerts exist for the new code path: error rate, latency, throughput, and any new business metric. SLO/threshold alerts are wired to notify the on-call. You can *see* the new feature's health after deploy — if you can't observe it, you can't safely ship it.
6. **Rollback gate.** A rollback plan exists (triggers, steps, data considerations, verification — see rollback-planning). Confirm the rollback path was actually validated, not just written.
7. **Dependencies & capacity.** Upstream/downstream services are compatible with this version; any new load has headroom; rate limits/quotas on third parties won't be tripped. Communicate the deploy window to dependents.
8. **Communication.** Deploy window, owner, on-call, and comms channel are set. Stakeholders know what's shipping.

## What good output looks like

- A checklist with go/no-go and evidence per gate (link to green pipeline, dashboard, flag config, rollback doc).
- Explicit blockers listed separately, with owners.
- A final overall go/no-go verdict with the reasoning.

## Pitfalls

- Treating "tests pass" as "ready" while monitoring or rollback is missing.
- New feature shipped without a flag, so a problem forces a full rollback instead of a flip.
- Migration verified only on an empty dev DB, then locking a huge production table.
- Secrets/config added to code but not to the target environment, causing a boot failure.
- A rollback plan that exists on paper but was never validated.
