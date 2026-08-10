---
name: api-contract-review
description: Review an API design for consistency, versioning, error semantics, pagination, and backward compatibility before it ships.
when_to_use: A new or changed API surface (REST/HTTP or RPC) is being designed or modified and needs a contract review before clients depend on it.
runtime: llm
---

# API Contract Review

You review an API contract for the qualities that are expensive to fix once clients depend on it. Focus on the interface — shapes, status codes, and evolution — not the implementation behind it.

## Procedure

1. **Resource & naming consistency.** Check nouns are plural and consistent (`/orders`, `/orders/{id}/items`), verbs live in the HTTP method not the path, and casing/pluralization is uniform across the whole surface. Flag RPC-style verbs bolted onto REST paths unless the API is deliberately RPC.
2. **HTTP semantics.** Verify methods match intent: GET safe & idempotent, PUT/DELETE idempotent, POST for creation/non-idempotent actions. Check status codes: 200 vs 201 (+ `Location`) vs 202, 400 vs 401 vs 403 vs 404 vs 409 vs 422, and 429 for rate limits. Reject 200-with-error-body designs.
3. **Error model.** Confirm a single, consistent, machine-readable error shape (e.g. a stable `code`, human `message`, and `details`/field errors). Errors must not leak stack traces or internal identifiers. A `trace_id` for correlation is a plus.
4. **Pagination, filtering, sorting.** Ensure list endpoints are bounded — no unbounded collections. Prefer cursor-based pagination for large/volatile data; if offset-based, cap page size and document the max. Check that filter/sort params are explicit and validated.
5. **Versioning & compatibility.** Confirm a versioning strategy (URI `/v1` or media-type). Then classify the change: additive (new optional field, new endpoint) is backward-compatible; removing/renaming a field, tightening validation, changing a type, or changing default behaviour is **breaking** and needs a new version or a deprecation window. List every breaking change explicitly.
6. **Contracts & types.** Check request/response schemas are fully specified (types, required vs optional, nullability, enums, formats for dates/money/ids). Money as integer minor-units or decimal-string, never float. Timestamps as RFC 3339 UTC.
7. **Cross-cutting.** Auth scheme and required scopes per endpoint; idempotency keys for unsafe retries; rate-limit headers; consistent field naming across endpoints.

## What good output looks like

- Findings grouped by the categories above, each with severity (blocker/should-fix/nit) and a concrete fix.
- An explicit list of any backward-incompatible changes and the migration/versioning path for each.
- Confirmation of what is already correct, so authors know it was checked.

## Pitfalls

- Approving additive-looking changes that are actually breaking (e.g. making an optional field required, or narrowing an enum).
- Unbounded list endpoints with no page cap.
- Inconsistent error shapes across endpoints.
- Floats for money; ambiguous local timestamps.
- Leaking internal details in error responses.
