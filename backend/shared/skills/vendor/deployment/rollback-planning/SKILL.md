---
name: rollback-planning
description: Author a rollback plan — clear triggers, ordered steps, data-migration considerations, and post-rollback verification.
when_to_use: Planning a deployment or release that could need to be reverted, especially one involving schema changes, data migrations, or irreversible side effects.
runtime: llm
---

# Rollback Planning

You write the plan for undoing a deployment quickly and safely under pressure. A rollback plan is written *before* the deploy, when you can think clearly — not improvised at 3am during an incident. The hardest part is almost always data, not code.

## Procedure

1. **Define trigger conditions.** State the specific, observable signals that mean "roll back", with thresholds: error-rate above X% for Y minutes, p95 latency above budget, a failed health check, a data-integrity alarm, or a critical functional regression. Name who can pull the trigger and how the decision is made fast (no committee).
2. **Choose the rollback mechanism.** Match it to the deploy strategy: blue-green (flip traffic back), canary (halt and route 100% to stable), rolling (redeploy previous image/tag), or feature flag (flip off — fastest, no redeploy). Prefer a flag-guarded release precisely so rollback is a flag flip, not a redeploy.
3. **Handle data and migrations — the crux.** Determine whether schema/data changes are backward-compatible with the *old* code. Enforce **expand/contract**: deploy additive (expand) changes first so old and new code both work, and defer destructive (contract) changes until after the new version is proven. If a migration is not reversible (dropped column, transformed data), the rollback plan must say so and provide the alternative (roll forward with a fix, restore from backup/snapshot, or a compensating migration). Never plan a rollback that would lose data written by the new version.
4. **Write ordered, executable steps.** Number the exact commands/actions to revert code, config, flags, and (if safe) schema — in the correct order to keep the system consistent. Include how to drain/hold traffic during the switch.
5. **Specify verification.** After rollback, how do you confirm the system is healthy on the old version: health checks, key user flows, error rate back to baseline, data consistency check. Define "rollback complete".
6. **Note communication & timing.** Who is notified, where status is posted, and the expected time-to-rollback (it should be minutes).

## What good output looks like

- Explicit, measurable trigger conditions and a named decision owner.
- A backward-compatibility statement for every schema/data change, following expand/contract.
- Numbered, copy-pasteable rollback steps in the correct order.
- A verification checklist proving the old version is healthy.
- An honest "this migration is not reversible → do X instead" where applicable.

## Pitfalls

- Assuming rollback is just "redeploy the old image" while a non-reversible migration has already changed the data.
- Destructive schema change shipped in the same release as the code that needs it (no expand/contract), making rollback impossible.
- No measurable trigger, so the team debates whether to roll back while the incident grows.
- Forgetting to roll back config/flags along with code.
- No post-rollback verification, so a "successful" rollback silently leaves a broken state.
