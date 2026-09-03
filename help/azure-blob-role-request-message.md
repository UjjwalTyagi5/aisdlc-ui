Hi — I need one Azure role assignment granted; I don't have the permission to do it
myself. Should take two minutes.

WHAT'S WRONG
Our SDLC platform generates documents and uploads them to Blob Storage. Every upload
fails with AuthorizationPermissionMismatch.

WHY
Azure Storage has two separate permission planes. I have Contributor, which covers the
management plane (create the account, create containers, read settings) but grants no
data-plane access — reading and writing actual blobs needs a "Storage Blob Data *"
role. I have none.

I confirmed nothing else is misconfigured: a write using the account key succeeds, so
the container, network rules, TLS and HTTPS settings are all fine. The missing role is
the only cause.

WHY I CAN'T DO IT MYSELF
Contributor explicitly excludes Microsoft.Authorization/roleAssignments/write. I'm also
a guest in this directory and hold no directory roles, so I can't use the Entra ID
"Access management for Azure resources" elevation path either. This needs Owner or
User Access Administrator.

WHAT I'M ASKING FOR
Role:  Storage Blob Data Contributor
Scope: the single storage account sdlcartifacts97290 (not the subscription, not the
       resource group — this grants nothing beyond that one account)

--- COMMAND 1: my user account, for local development ---

az role assignment create \
  --assignee-object-id a2ba62d3-7127-4bd9-a603-b9481f8463e4 \
  --assignee-principal-type User \
  --role "Storage Blob Data Contributor" \
  --scope "/subscriptions/e7e3aa7d-4668-4eb1-a86d-abb6701162ab/resourceGroups/agentic-team-group/providers/Microsoft.Storage/storageAccounts/sdlcartifacts97290"

--- COMMAND 2: the application's managed identity, for deployed environments ---

PLEASE CONFIRM THE IDENTITY BEFORE RUNNING THIS ONE. The app authenticates with
DefaultAzureCredential, so in AKS it runs as a managed identity rather than as me.
Command 1 alone fixes local dev and leaves deployed environments broken.

agenthelix_identity (resource group platform_aicore, principal
474dd5a9-394f-4ff2-92f5-84890335e3b8) is the likely candidate, but nothing in the
application repo binds it — whoever owns the AKS deployment manifests should confirm
which identity the backend pod actually uses first.

az role assignment create \
  --assignee-object-id 474dd5a9-394f-4ff2-92f5-84890335e3b8 \
  --assignee-principal-type ServicePrincipal \
  --role "Storage Blob Data Contributor" \
  --scope "/subscriptions/e7e3aa7d-4668-4eb1-a86d-abb6701162ab/resourceGroups/agentic-team-group/providers/Microsoft.Storage/storageAccounts/sdlcartifacts97290"

--- IF YOU PREFER THE PORTAL ---

1. Portal > Storage accounts > sdlcartifacts97290
2. Access Control (IAM) > + Add > Add role assignment
3. Role tab: search "Storage Blob Data Contributor", select it, Next
4. Members tab: Assign access to = User, group, or service principal
   > + Select members > search "ujjwaltyagi.fg" > Select
5. Review + assign

Repeat with Assign access to = "Managed identity" for the application identity.

--- HOW I'LL VERIFY ---

Assignments take a minute or two to propagate. I'll run:

az storage blob upload --account-name sdlcartifacts97290 \
  --container-name sdlc-artifacts --name _probe/check.txt \
  --file check.txt --auth-mode login --overwrite

and delete the probe blob afterwards.

--- ONE NOTE ---

allowSharedKeyAccess is currently unset, which Azure treats as enabled — that's why the
portal browses the container via "Access key". I deliberately did not switch the app to
an account key: it's a long-lived secret granting full access to the whole account,
can't be scoped to one container, leaves no per-identity audit trail, and would
exercise a different auth path than production. Worth setting allowSharedKeyAccess to
false once these assignments are in place.

Thanks.
