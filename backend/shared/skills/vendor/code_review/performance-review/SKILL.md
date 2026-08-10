---
name: performance-review
description: Review a change for performance defects — N+1 queries, unbounded loops and allocations, and regressions on hot paths.
when_to_use: Reviewing a diff that touches data access, loops over collections, request handlers, or any code on a latency-sensitive path.
runtime: llm
---

# Performance Review

You review the change for performance defects that scale badly with data or traffic. The goal is to catch order-of-magnitude problems and hot-path regressions, not to micro-optimize code that runs once. Always reason about behaviour as N (rows, users, items) grows.

## Procedure

1. **N+1 and query patterns.** Scan every loop for a database/HTTP/RPC call inside it — the classic N+1. The fix is to batch: eager-load/join, `IN (...)` a set of ids, or a single bulk call. Also check for queries missing an index on their filter/join columns, `SELECT *` pulling wide rows, and queries with no `LIMIT` on user-facing lists.
2. **Unbounded work.** Look for anything whose size is controlled by input or data and has no cap: loading a whole table into memory, unbounded pagination, recursion without a depth limit, regexes with catastrophic backtracking, or building a collection that grows with request volume. Every unbounded collection is a latent OOM/timeout.
3. **Algorithmic complexity.** Identify nested loops over the same data (O(n²)) where a hash/set lookup would make it O(n). Check for repeated linear scans (`list.contains` in a loop), sorting inside a loop, or recomputing an invariant each iteration. State the complexity you observe and the achievable target.
4. **Allocations & hot paths.** On code that runs per-request or per-item, watch for allocation in tight loops (building strings with `+` in a loop, boxing, defensive copies), repeated serialization/deserialization, and creating clients/connections per call instead of reusing a pool. Flag work done eagerly that could be lazy or cached.
5. **Concurrency & I/O.** Blocking I/O on an async/event-loop path; synchronous calls that could be parallelized; missing timeouts on external calls (a slow dependency shouldn't hang the whole request); lock contention on a shared resource in the hot path.
6. **Regression check.** Compare against the prior version: did this change add a call in a loop, remove a cache, widen a query, or move work from batch to per-item? Name the before/after cost.

## What good output looks like

- Findings tied to specific lines, each stating the cost as a function of N and the concrete fix (batch, index, cache, cap, better data structure).
- A distinction between "will hurt at scale" (must fix) and "minor" (optional).
- Any assumptions about data volume and call frequency made explicit.

## Pitfalls

- Micro-optimizing cold paths while missing an N+1 on the hot one.
- Assuming small test data hides an O(n²) that explodes in production.
- Adding a cache without considering invalidation/staleness.
- Missing external-call timeouts, turning a slow dependency into a full outage.
- "Optimizing" without a measurement or a clear complexity argument.
