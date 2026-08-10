---
name: acceptance-criteria-review
description: Critique or author acceptance criteria in Given-When-Then form and check the parent story against INVEST.
when_to_use: A story has vague, missing, or untestable acceptance criteria, or you are drafting AC before development starts.
runtime: llm
---

# Acceptance Criteria Review

You turn a story into unambiguous, testable acceptance criteria and judge whether the story itself is well-formed. Good AC are the contract between product and engineering and the seed for the test plan — each one should map to at least one automated test.

## Procedure

1. Read the story's value statement and identify the observable behaviours it promises. List them as candidate scenarios.
2. Write each scenario in **Given-When-Then**:
   - **Given** the starting context/state (be concrete: "Given a cart with 2 in-stock items").
   - **When** the single triggering action ("When the user applies coupon SAVE10").
   - **Then** the observable, verifiable outcome ("Then the order total drops by 10% and the coupon shows as applied").
   Keep one action per scenario; if you need "and When" twice, split it.
3. Cover the full behaviour space, not just the happy path:
   - Happy path(s).
   - Negative/error paths (invalid input, unauthorized, not found).
   - Boundaries and edge cases (empty, max, zero, expired, duplicate).
   - Non-functional constraints that are testable (latency budget, must audit-log, must be idempotent).
4. Make every Then **objectively verifiable**. Replace subjective words ("fast", "user-friendly", "properly") with a measurable condition ("responds within 300 ms at p95", "returns HTTP 422 with field-level errors").
5. Check each criterion is **independent and atomic** — one behaviour, testable in isolation, no hidden dependence on another criterion's side effects.
6. Assess the parent story against **INVEST**: Independent, Negotiable, Valuable, Estimable, Small, Testable. Flag violations and recommend a split (see story-splitting) if it is too large or bundles unrelated outcomes.

## What good output looks like

- A numbered list of Given-When-Then scenarios covering happy, negative, and edge cases.
- Every Then is measurable and would fail loudly if broken.
- Explicit callouts of gaps you filled and assumptions you had to make.
- An INVEST verdict per dimension with concrete remediation for any failing dimension.

## Pitfalls

- Restating the implementation ("When the service calls the repository") instead of user-observable behaviour.
- Compound Whens/Thens that can't map cleanly to a single test.
- Untestable adjectives left in the Then clause.
- Only the happy path — missing error and boundary scenarios is the top defect source.
- AC that silently assume data or auth state not established in the Given.
