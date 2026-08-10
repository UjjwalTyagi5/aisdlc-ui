---
name: story-splitting-spidr
description: Split an oversized user story into thin, independently valuable vertical slices using the SPIDR patterns (Spikes, Paths, Interfaces, Data, Rules).
when_to_use: A story is too large to finish in a single iteration, spans multiple sprints, is hard to estimate, or bundles several distinct behaviours behind one title.
runtime: llm
---

# Story Splitting with SPIDR

You split a large story into smaller stories that each deliver end-to-end value. Every slice must be shippable on its own — it walks through the whole stack (UI or API, logic, data) and could in principle be demoed to a user. Never split into horizontal layers ("build the DB table", "build the API", "build the UI"): those are tasks, not stories, and none is independently valuable.

## Procedure

1. Restate the story and its acceptance criteria in one paragraph. Confirm the single user outcome it targets. If you find two or more unrelated outcomes, that is your first and cleanest split.
2. Estimate roughly. If it is clearly deliverable in a few days by one pair, it may not need splitting — say so and stop.
3. Walk the SPIDR patterns in order and pick the ones that fit. Do not force all five.
   - **Spikes** — if the story is large only because it is *uncertain*, carve out a timeboxed research spike first, then re-split the now-understood remainder. A spike is not shippable value; flag it as an enabler.
   - **Paths** — split by the distinct routes through the workflow: happy path first, then each alternate/error path (valid payment vs. declined card vs. expired session). Ship the happy path as slice one.
   - **Interfaces** — split by entry point or client: one browser/API/format first (e.g. web before mobile, JSON before CSV export), or one supported input type before the rest.
   - **Data** — split by data variation: support one currency, one region, one file type, or one record shape first; add the others as follow-on slices.
   - **Rules** — relax business rules for the first slice, then add each rule back as its own story (start with no discounting, then add the discount-tier rule, then the loyalty-cap rule).
4. Write each resulting slice as an independent story with its own value statement ("As a … I want … so that …") and its own acceptance criteria. Sequence them: the thinnest end-to-end slice that proves the flow goes first.
5. Verify every slice against INVEST — especially **I**ndependent, **V**aluable, and **S**mall. If a slice is not independently valuable, merge it back or re-split.

## What good output looks like

- 2–5 slices, each demoable, ordered by value/risk with the walking-skeleton slice first.
- Each slice names the SPIDR pattern used to derive it.
- Enabler spikes are clearly labelled and not counted as user value.
- No slice is a pure technical layer.

## Pitfalls

- Splitting into front-end/back-end tasks — this is the most common mistake; keep slices vertical.
- Producing slices that all must ship together to be useful (false independence).
- Over-splitting into a dozen trivial stories that add coordination overhead.
- Forgetting to re-write acceptance criteria per slice, leaving them ambiguous.
