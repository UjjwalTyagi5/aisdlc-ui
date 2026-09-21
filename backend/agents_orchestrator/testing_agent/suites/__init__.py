"""Test case suites: generate → review and approve → run → report.

THE FLOW A TESTER WORKS IN. The Testing page used to have one button per test type that
generated tests, ran them and reported in one go, with nothing a person could check or
approve in between. This package splits it the way QA teams work:

1. `generate` writes three suites from the branch, the project's approved BRD and design,
   and the code — UNIT, FUNCTIONAL (browser) and API — each an Excel workbook filed as a
   draft document that can be downloaded, reviewed and raised for approval.
2. A run reads a suite BACK FROM ITS STORED WORKBOOK (`excel.read_suite`), so the cases
   that run are the cases on file — edited or not — and never a regeneration.
3. Every run files its own Excel report: each case passed, failed or not run, and why.

Modules: `models` (the case shapes), `excel` (the workbook format, both directions),
`page` (the page view), `context` (what generation reads), `generate`, `jobs` (background
work with progress), `store` (writing and filing documents).
"""
