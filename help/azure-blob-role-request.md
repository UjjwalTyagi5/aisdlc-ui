# Access request: Storage Blob Data Contributor on `sdlcartifacts97290`

## What is broken

The SDLC platform generates documents (BRD, PDD, architecture PDFs, diagrams) and
uploads them to Blob Storage. Every upload fails with `AuthorizationPermissionMismatch`.
The artifact row is still written to the database, so the file is *listed* in the UI
but the bytes were never stored.

## Why

Azure Storage has two independent permission planes. `Contributor` covers the
**management** plane (create the account, create containers, read settings) but grants
**no data-plane access** (read/write blobs). Those come only from the `Storage Blob
Data *` roles.

Verified on 2026-09-02:

- `az role assignment list --all` for the user returns exactly one assignment:
  `Contributor`, inherited from the subscription. No data-plane role at any scope.
- A blob write with `--auth-mode login` is rejected outright:
  `You do not have the required permissions needed to perform this operation.`
- The same write with `--auth-mode key` succeeds, so the container, network rules
  (`defaultAction: Allow`), TLS and HTTPS settings are all fine. The missing role is
  the only cause.

## Why the requester cannot fix it themselves

- `Contributor` explicitly excludes `Microsoft.Authorization/roleAssignments/write`.
- The requester is a **guest** in this directory
  (`ujjwaltyagi.fg_gmail.com#EXT#@durejapranjalgmail.onmicrosoft.com`).
- `GET /me/memberOf` returns `[]` — they hold no directory roles, so they cannot use
  the Entra ID "Access management for Azure resources" elevation path either.

This needs someone with **Owner** or **User Access Administrator**.

## The commands

Subscription: `e7e3aa7d-4668-4eb1-a86d-abb6701162ab`
Scope: `/subscriptions/e7e3aa7d-4668-4eb1-a86d-abb6701162ab/resourceGroups/agentic-team-group/providers/Microsoft.Storage/storageAccounts/sdlcartifacts97290`

**1. The developer, for local development:**

```bash
az role assignment create \
  --assignee-object-id a2ba62d3-7127-4bd9-a603-b9481f8463e4 \
  --assignee-principal-type User \
  --role "Storage Blob Data Contributor" \
  --scope "/subscriptions/e7e3aa7d-4668-4eb1-a86d-abb6701162ab/resourceGroups/agentic-team-group/providers/Microsoft.Storage/storageAccounts/sdlcartifacts97290"
```

**2. The application's managed identity, for deployed environments.**

The app authenticates with `DefaultAzureCredential`, so in AKS it runs as a managed
identity rather than as the developer. Assignment 1 alone will fix local dev and leave
deployed environments still broken.

`agenthelix_identity` (resource group `platform_aicore`, principal
`474dd5a9-394f-4ff2-92f5-84890335e3b8`) is the likely candidate, but this has **not**
been confirmed — nothing in the application repo binds it, so whoever owns the AKS
deployment manifests should confirm which identity the backend pod actually uses before
this one is granted.

```bash
az role assignment create \
  --assignee-object-id 474dd5a9-394f-4ff2-92f5-84890335e3b8 \
  --assignee-principal-type ServicePrincipal \
  --role "Storage Blob Data Contributor" \
  --scope "/subscriptions/e7e3aa7d-4668-4eb1-a86d-abb6701162ab/resourceGroups/agentic-team-group/providers/Microsoft.Storage/storageAccounts/sdlcartifacts97290"
```

The scope is a single storage account, not the subscription or resource group — this
grants nothing beyond the artifact container's account.

## Verifying afterwards

Role assignments take a minute or two to propagate.

```bash
az storage blob upload \
  --account-name sdlcartifacts97290 --container-name sdlc-artifacts \
  --name _probe/check.txt --file check.txt --auth-mode login --overwrite
```

Success means the platform's uploads will work. Delete the probe blob afterwards.

## Note on account keys

`allowSharedKeyAccess` is currently unset, which Azure treats as enabled — this is why
the portal browses the container via "Access key". Switching the application to an
account key would work today but was rejected deliberately: an account key is a
long-lived secret granting full access to the whole account, cannot be scoped to one
container, produces no per-identity audit trail, and would exercise a different auth
path than production. Consider setting `allowSharedKeyAccess: false` once the role
assignments are in place.
