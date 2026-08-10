---
name: safe-refactoring
description: Change code structure without changing behaviour, protected by characterization tests and small reversible steps.
when_to_use: Improving the internal structure of working code — untangling a large function, renaming, extracting modules — especially in code with thin or no test coverage.
runtime: llm
---

# Safe Refactoring

You improve internal structure while provably preserving observable behaviour. The core rule: refactoring and behaviour change never happen in the same step. If you must change behaviour, do it as a separate, clearly-labelled commit with its own tests.

## Procedure

1. **Pin behaviour first.** Before touching the code, ensure a safety net exists. If coverage is thin, write **characterization tests**: run the existing code, observe its actual outputs (including quirks), and assert exactly those. You are capturing current behaviour, not judging it — even a bug gets pinned so you notice if refactoring changes it.
2. **Identify the smell and the target shape.** Name what's wrong (long function, duplicated logic, feature envy, primitive obsession) and what you want instead. Plan the smallest sequence of behaviour-preserving moves to get there.
3. **Refactor in micro-steps**, each individually reversible and each keeping the suite green:
   - Extract Function/Method to name a block of logic.
   - Rename for intent (use the IDE/tooling rename so all references update).
   - Introduce a variable to name a sub-expression.
   - Move a function/field to where it belongs.
   - Replace duplication with a single call.
   Run the tests after each step. If a step goes red, revert just that step — never debug forward through a broken refactor.
4. **Keep the diff mechanical.** Prefer many tiny commits over one large rewrite. Each commit message should say "refactor: …" and be a no-op on behaviour.
5. **Separate behaviour changes.** If refactoring reveals a bug or you want new behaviour, stop, commit the refactor, then make the behaviour change with its own failing-first test.
6. **Verify at the boundary.** After the refactor, confirm the public interface, serialized formats, logs, and side effects are unchanged. Diff the characterization-test outputs.

## What good output looks like

- A characterization or existing test suite that is green before and after, with no test assertions weakened to accommodate the refactor.
- A series of small, individually-green steps rather than one big-bang change.
- Public API, data formats, and side effects demonstrably unchanged.
- Behaviour changes, if any, isolated in their own labelled commits.

## Pitfalls

- Refactoring with no safety net and "eyeballing" correctness.
- Weakening or deleting a failing test to make the refactor "pass" — that is changing behaviour.
- Mixing a bug fix or feature into the refactor commit.
- Large multi-concern rewrites that can't be bisected when something breaks.
