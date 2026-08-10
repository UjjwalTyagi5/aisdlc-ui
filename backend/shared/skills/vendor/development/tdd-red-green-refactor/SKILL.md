---
name: tdd-red-green-refactor
description: Drive implementation with the test-first loop — write a failing test, make it pass with the minimum code, then refactor.
when_to_use: Implementing a new behaviour or fixing a bug where you want tests to define and lock the intended behaviour.
runtime: llm
---

# TDD: Red → Green → Refactor

You implement behaviour by writing the test before the code. Each cycle is small and covers exactly one behaviour. The discipline is not optional ordering — writing the test first is what forces a testable design and gives you an executable specification.

## Procedure

1. **Pick one behaviour.** Take the smallest slice of the requirement/AC you can express as a single assertion. If you can't state it in one sentence, split it.
2. **RED — write a failing test.** Write a test that asserts the desired behaviour through the public interface. Use a concrete example (Arrange the inputs, Act on the unit, Assert the outcome). Name it after the behaviour (`returns_422_when_email_missing`).
3. **Run it and watch it fail** for the *right reason* — an assertion failure, not an import error or typo. A test that never failed proves nothing. If it fails to compile/import, fix that first so the failure is a genuine assertion failure.
4. **GREEN — minimal code.** Write the least code that makes the test pass. Hardcoding or an obvious-but-incomplete implementation is fine at this step; resist adding untested generality. Run the full suite; it must be green.
5. **REFACTOR.** With tests green, improve the design: remove duplication, rename for clarity, extract functions, tidy the code you just wrote *and* nearby code the change touched. Change structure, never behaviour. Re-run the suite after each refactor step; it stays green.
6. **Repeat** with the next behaviour. Add negative and edge-case cases as their own red-green cycles. Commit at green points so you always have a working checkpoint.

## What good output looks like

- A sequence of small commits/steps, each starting from a failing test and ending green.
- Tests that read as a specification: clear Arrange-Act-Assert, one behaviour each, named for intent.
- Production code with no untested branches; coverage is a by-product, not the goal.
- Refactoring visibly separated from behaviour change.

## Pitfalls

- Writing the implementation first, then a test that trivially passes — you lose the design pressure and the confidence that the test can fail.
- Skipping the "watch it fail" step, so a broken assertion silently passes.
- Making the test pass by over-building — adding branches no test exercises.
- Refactoring while tests are red (you can't tell if you broke behaviour).
- One giant test asserting five things; split into focused cases.
