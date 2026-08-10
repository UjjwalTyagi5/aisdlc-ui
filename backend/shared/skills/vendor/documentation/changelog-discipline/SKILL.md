---
name: changelog-discipline
description: Maintain a human-readable changelog grouped into Added / Changed / Fixed / Deprecated with clear user-facing impact notes.
when_to_use: Preparing a release, or reviewing whether changes are being recorded for users in a consistent, useful changelog.
runtime: llm
---

# Changelog Discipline

You keep a changelog written for humans who *use* the software, not a dump of git history. It answers "what changed for me, and do I need to do anything?" Follow the Keep a Changelog conventions and group entries by type.

## Procedure

1. **Keep an Unreleased section at the top.** Add entries there as work merges, so the changelog is never a scramble at release time. On release, rename it to the version with a date and start a fresh Unreleased.
2. **Version with SemVer.** MAJOR for breaking changes, MINOR for backward-compatible features, PATCH for backward-compatible fixes. The types of entries in a release should justify its version bump (any Removed/breaking Changed ⇒ MAJOR).
3. **Group each entry under the standard headings**, in this order, omitting empty ones:
   - **Added** — new features/capabilities.
   - **Changed** — changes to existing behaviour.
   - **Deprecated** — soon-to-be-removed features (give the timeline and the replacement).
   - **Removed** — features removed in this release.
   - **Fixed** — bug fixes.
   - **Security** — vulnerability fixes (note severity, avoid exploit detail before users can patch).
4. **Write user-facing entries.** Each line states the change from the user's perspective and its impact, not the implementation. "Fixed export timing out on large reports" — not "refactored ExportService to stream rows". If action is required (migration, config change, breaking API), say so explicitly and link the upgrade note. Reference the issue/PR for traceability.
5. **Flag breaking changes loudly.** Anything requiring user action gets a clear **BREAKING** marker and a short migration note (before → after). This is the single most valuable thing a changelog does.
6. **Curate, don't auto-generate blindly.** A raw commit list is not a changelog. Merge related commits into one meaningful entry; drop internal-only churn (refactors, test tweaks, formatting) that has no user impact — or keep those in a separate "Internal" note.

## What good output looks like

- An Unreleased section maintained continuously, promoted cleanly on release with version + date.
- Entries grouped under Added/Changed/Deprecated/Removed/Fixed/Security, most-recent version first.
- Every line is user-facing and impact-oriented, with issue/PR references.
- Breaking changes clearly marked with migration guidance, matching a MAJOR bump.

## Pitfalls

- Pasting `git log` as the changelog — noisy, implementation-focused, useless to users.
- Burying a breaking change among ordinary entries with no BREAKING flag or migration note.
- Inconsistent categories or ordering that makes scanning hard.
- Writing entries in developer terms ("bumped internal cache TTL") instead of user impact.
- Only updating the changelog at release time, so entries are guessed from memory.
