---
name: mutation_testing
description: Run mutation testing (Stryker.NET / Stryker-JS / mutmut) over the test suite to measure kill-rate. Mutation tests prove the SUITE catches real bugs — high coverage with low kill-rate means tests run code without verifying behaviour.
when_to_use: code_analysis is non-empty AND a runnable test suite exists (after unit/negative_edge skills generated tests)
runtime: shell
shell_command: dotnet stryker --reporter json --output reports
shell_timeout_s: 600
output_parser: stryker_json
output_artifact_field: mutation_results
inputs: []
outputs:
  kill_rate_pct: float
  mutants_killed: int
  mutants_survived: int
---

# Mutation Testing — runtime: shell

This skill is a **shell-runtime** skill. It does NOT prompt the LLM. Instead,
the dispatcher executes `shell_command` via the existing sandbox, then pipes
the resulting `reports/mutation/mutation-report.json` through the registered
`stryker_json` parser, which fills in a `MutationResult` pydantic model and
routes it into `AggregatedResults.mutation_results`.

The `shell_command` above defaults to .NET / Stryker.NET. Multi-language
support: when a future runner-detection step lands, the dispatcher will pick
the right command per `state["language"]`:

- **Python:** `mutmut run --no-output && mutmut results --json > reports/mutation_testing.json` (parser: mutmut_summary)
- **JavaScript / TypeScript:** `npx stryker run --reporter=json --output=reports` (parser: stryker_json)
- **.NET:** `dotnet stryker --reporter json --output reports` (parser: stryker_json)

For now this SKILL.md is .NET-default; copy + edit the frontmatter for other languages once the dispatcher learns to pick by language.

# When kill-rate is low

A killed mutant means a test FAILED when the source was mutated — that's good (the test caught the bug). A surviving mutant means tests passed despite the buggy source — bad (the test didn't actually verify behaviour, just executed the code path).

Industry healthy ranges:
- < 50%: very weak suite — many tests are vanity tests that don't catch regressions
- 50-75%: typical — most teams land here without explicit attention
- > 75%: strong suite — tests catch real bugs
- > 90%: outstanding — usually requires deliberate property-based / boundary tests
