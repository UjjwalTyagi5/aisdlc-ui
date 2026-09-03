# Provisioning Azure resources — the design, and what is deliberately not built

Phase 7 of the deployment agent. The request was: *an Azure connector through which the
agent can create and procure resources and deploy them, with user approval and proper
governance.*

This document is the design for that. **Provisioning itself is not built**, and the
reason is worth stating rather than filing as a gap: the governance model had to come
first, and the piece that actually blocks everything else is not the connector.

---

## What phase 7 built

**The ability to read a plan.** `provisioning.py`, plus two tools:

| Tool | Does | Deliberately does not |
|---|---|---|
| `find_infrastructure_code` | reports which Terraform / Bicep / ARM files exist | say what they would do |
| `read_infrastructure_plan` | summarises a Terraform plan or Azure what-if into create / update / **replace** / delete | run anything |

### Why this and not the connector

**You cannot approve what you cannot read.** An approval screen that says *"provision
the infrastructure?"* is not governance, it is a rubber stamp with extra steps. Before
anything can create a resource, a human has to be shown what appears, what changes, and
what disappears — in a form they can act on.

### The case the whole module is shaped around

Terraform reports a replacement as `["delete", "create"]`. Azure's what-if has its own
change types. **A replace reads like an update to anything counting actions naively**,
and a replaced storage account is a deleted storage account.

The difference between *"3 updates"* and *"1 deletion and 2 updates"* is the difference
between a routine approval and somebody losing data. Both orderings —
destroy-before-create and create-before-destroy — are classified as `replace`, and both
count as destructive.

Deleting something that holds state gets a second, separate warning: re-running the plan
recreates the **resource**, not the **data in it**. That warning fires for databases,
storage, key vaults and disks, and deliberately not for a load balancer — crying wolf
over stateless infrastructure teaches people to skim the warning that matters.

An unreadable plan raises rather than returning an empty summary. *"Nothing will change"*
and *"nobody looked"* are indistinguishable on screen, and only one of them is safe.

---

## The connector, when it is built

### Where approval sits

It already exists. `deployments` (migration 0043) and `deployment_gate` were built to be
action-agnostic: a request, a named approver who is not the requester, one approval that
fires exactly once, and an audit entry carrying the request itself.

Provisioning needs two new actions in the `action` CHECK constraint:

```
provision_resources    apply a plan
destroy_resources      tear an environment down
```

**`destroy_resources` should not reuse the same approval rule.** Every other gated action
is recoverable — a bad deployment is rolled back, a bad pipeline is deleted. A destroyed
database is not. What that needs is a decision, not a default:

- two approvers rather than one, or
- a typed confirmation of the environment name, or
- a refusal for production entirely, leaving it to the Azure portal where the audit trail
  and the blast radius are somebody else's problem

My recommendation is the third for production and the first elsewhere, but it is a
policy question and should be answered by whoever owns the environments.

### What the approver must be shown

The plan summary, not the template. Specifically `destructive`, `destroys_state`, the
warnings, and the environment. `request` on the deployment row is frozen at approval and
carried into the audit entry, so what was approved cannot be edited afterwards — that
property matters far more here than it does for a pipeline run.

### Cost

Not in `budget_status`. That meters **LLM spend** — what running the agents costs — and
the PM agent already keeps it apart from labour cost for the same reason. Cloud spend is
a third budget, and summing any two of them produces a number somebody will put in front
of a client.

Azure's Retail Prices API can price a plan, but a monthly estimate depends on usage the
plan does not contain (egress, transactions, hours running). The honest shape is the one
`cost_plan` already uses: price what can be priced, report the rest as **uncosted**, and
never present a total that quietly assumes zero for what it could not measure.

### Credentials

Not an `ado-pat` equivalent. Provisioning needs an Azure service principal or managed
identity with contributor rights on a subscription — a materially larger blast radius
than every credential the platform holds today.

Two things follow, and both are decisions rather than implementation details:

1. **Scope the identity to a resource group per project**, not a subscription. The
   platform's tenant isolation is RLS on rows; it has no equivalent for "this tenant may
   only touch these Azure resources" beyond what the credential itself permits.
2. **The platform should probably not hold the credential at all.** The pipeline-first
   decision from phase 3 applies with more force here: an ADO service connection running
   `terraform apply` keeps the credential in Azure DevOps, where the organisation already
   governs it, and reduces this feature to "create the pipeline, gate the run" — which
   phases 0–4 already do.

That last point is the real recommendation. **Provisioning may not need an Azure
connector at all.** A `terraform apply` stage in a pipeline, behind the approval gate
that already exists, achieves the goal without the platform ever holding a subscription
credential.

---

## What would need building, in order

1. `provision_resources` / `destroy_resources` in the action vocabulary, and the stricter
   rule for destroys **(policy decision required first)**
2. Generating the IaC pipeline stage — `terraform plan` on PR, `terraform apply` on the
   gated run — which is phase 2b's shape applied to infrastructure
3. Surfacing the plan summary on the approval screen, so the destructive changes are what
   the approver sees first
4. Cost estimation, as a third budget that is never added to the other two
5. An Azure connector **only if** step 2 proves insufficient — and if so, scoped to a
   resource group, never a subscription

## What must never be built

A path where the agent applies infrastructure changes without a human reading the plan
first. Every other refusal in this system protects against confidently wrong work.
This one protects against a deleted database.
