---
name: threat-modeling-stride
description: Enumerate threats against a system using STRIDE per element and propose a concrete mitigation for each credible threat.
when_to_use: A new feature, service, or data flow crosses a trust boundary and you need to systematically find and address security threats before or during design.
runtime: llm
---

# Threat Modeling with STRIDE

You produce a structured threat model: decompose the system, walk STRIDE against each element, and pair every credible threat with a mitigation. STRIDE = Spoofing, Tampering, Repudiation, Information disclosure, Denial of service, Elevation of privilege.

## Procedure

1. **Model the system.** Sketch (in text or a diagram) the elements and how data moves: external entities (users, third-party services), processes (services, functions), data stores (DBs, queues, caches), and data flows between them. Mark **trust boundaries** — every point where data crosses from less-trusted to more-trusted (internet→edge, service→DB, tenant A→shared store). Threats concentrate at boundaries.
2. **Walk STRIDE per element.** For each element and each crossing flow, ask the relevant STRIDE questions:
   - **Spoofing** — can an attacker pretend to be this entity/service? (auth, mutual TLS, signed tokens)
   - **Tampering** — can data in transit or at rest be modified? (integrity checks, signing, write authz)
   - **Repudiation** — can an actor deny an action? (audit logging, non-repudiable records)
   - **Information disclosure** — can data leak to someone unauthorized? (encryption, least-privilege, output filtering, tenant isolation)
   - **Denial of service** — can availability be exhausted? (rate limits, quotas, timeouts, backpressure)
   - **Elevation of privilege** — can an actor gain rights they shouldn't? (authz checks, sandboxing, input validation)
   Not every letter applies to every element — say why when you skip one.
3. **Rate each threat.** Give a rough likelihood × impact (or DREAD-style) rating so mitigations can be prioritized. Focus effort on high/critical.
4. **Assign a mitigation per threat.** For each credible threat, state a specific control (not "add security") and its type: mitigate, eliminate (remove the feature/flow), transfer (rely on a platform control), or accept (with explicit sign-off and rationale).
5. **Record residual risk.** List what remains after mitigations and any assumptions the model depends on.

## What good output looks like

- A short decomposition with trust boundaries called out.
- A table of threats: element, STRIDE category, description, rating, mitigation, status.
- Mitigations that are concrete and testable, mapped to owners or follow-up tickets.
- Explicit residual-risk and accepted-risk entries.

## Pitfalls

- Modeling the whole system at once — scope to the change/feature and its boundaries.
- Listing threats with no mitigations, or mitigations with no specific control.
- Ignoring repudiation/DoS because they feel less exciting than injection.
- Treating internal service-to-service traffic as automatically trusted.
- No prioritization, so a critical elevation threat gets the same attention as a minor one.
