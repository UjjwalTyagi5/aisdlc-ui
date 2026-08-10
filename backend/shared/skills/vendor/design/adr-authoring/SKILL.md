---
name: adr-authoring
description: Draft an Architecture Decision Record capturing context, the decision, its consequences, and the alternatives considered.
when_to_use: A significant, hard-to-reverse architectural or technology choice is being made and needs to be recorded with its rationale.
runtime: llm
---

# ADR Authoring

You write a single Architecture Decision Record (ADR): a short, immutable document that captures one decision and the reasoning behind it so future readers understand *why*, not just *what*. An ADR is not a design doc — it records one choice at one point in time.

## Procedure

1. Confirm the decision is ADR-worthy: it is architecturally significant (affects structure, dependencies, interfaces, or cross-cutting concerns) and costly to reverse. Trivial or easily-changed choices do not need an ADR.
2. Assign a sequential number and a short imperative title naming the decision, e.g. "0007: Use Postgres row-level security for tenant isolation".
3. Set **Status**: Proposed → Accepted → (later) Deprecated/Superseded-by-NNNN. New ADRs are Proposed or Accepted.
4. Write **Context**: the forces in play — requirements, constraints, non-functional targets, current pain, assumptions. State the problem neutrally and factually. This section should let a newcomer understand the pressure without already knowing the answer. Avoid naming the decision here.
5. Write **Decision**: state the choice in active voice — "We will …". Be specific and singular. Include the key parameters that make it real (which library, which boundary, which pattern).
6. Write **Consequences**: the honest results — positive, negative, and neutral. What becomes easier, what becomes harder, what new obligations or risks appear, what you now cannot easily do. A consequences section with only upsides is a red flag.
7. Write **Alternatives Considered**: for each serious option, one or two lines on what it was and the specific reason it was rejected (not just "worse"). This is what makes the ADR trustworthy and prevents re-litigating the same debate later.
8. Keep it to roughly one page. Link out to specs, benchmarks, or spikes rather than inlining them.

## What good output looks like

- Number, title, status, and date at the top.
- Context that motivates the decision without presupposing it.
- A crisp "We will …" decision statement.
- Consequences that name real trade-offs and downsides.
- 2–4 alternatives each with a concrete rejection reason.

## Pitfalls

- Editing an accepted ADR to change the decision — instead write a new ADR that supersedes it and mark the old one Superseded.
- Mixing several decisions into one ADR.
- A consequences section that hides the costs.
- Vague alternatives ("we could have used something else") with no rejection rationale.
