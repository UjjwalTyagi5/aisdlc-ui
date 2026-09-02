# Provision an Azure Key Vault for the platform's secrets, grant yourself write access
# and the application read access, then hand off to the seeding script.
#
# RUN THIS YOURSELF. It creates a billable resource and grants role assignments.
#
#   cd C:\pwc_work\frontend\backend
#   .\scripts\setup_key_vault.ps1
#   .\scripts\setup_key_vault.ps1 -Env prod -AppId <managed-identity-or-app-client-id>
#   .\scripts\setup_key_vault.ps1 -Env prod -AppId <id> -TenantVault
#
# TWO VAULTS, SPLIT BY TRUST. Run this twice:
#   without -TenantVault  the PLATFORM vault (sdlc-kv-<env>). Secrets the app only READS:
#                         jwt-secret-key, redis-url, webhook secrets. App gets read-only.
#   with    -TenantVault  the TENANT vault (sdlc-kv-tenant-<env>). Per-tenant credentials
#                         the app also WRITES: BYOK model keys, connector tokens. App gets
#                         "Key Vault Secrets Officer".
# The split exists so granting write on tenant credentials does not also grant write on
# jwt-secret-key. Set AZURE_TENANT_VAULT_URL on the host to the second vault.
#
# Cost: Key Vault standard tier is about $0.03 per 10,000 operations, with no standing
# charge for the vault itself. Startup reads ~30 secrets once per process. This is
# effectively free at this scale, but it is NOT in the always-free tier.
#
# WHY RBAC AND NOT ACCESS POLICIES: access policies are per-vault ACLs that no other
# Azure resource uses, do not appear in `az role assignment list`, and are invisible to
# every tool that audits access. RBAC is the current default and the one an auditor can
# actually enumerate.

param(
    # The deployment environment these secrets belong to. Decides the secret-name prefix
    # (sdlc-<env>-*), and must match the ENV the deployed process runs with - the loader
    # builds the same names from its own ENV, so a mismatch reads an empty vault.
    [string]$Env = "prod",

    # Object id (not app id) of the managed identity or service principal the RUNNING
    # APPLICATION authenticates as. Given, it is granted read-only access. Omitted, only
    # you get access and the app cannot start against the vault.
    [string]$AppId = "",

    [string]$ResourceGroup = "rg-sdlc-secrets",
    [string]$Location = "eastus",
    [string]$VaultName = "sdlc-kv",

    # Provision the TENANT credential vault instead of the platform vault. Changes the
    # default vault name to sdlc-kv-tenant and, critically, grants the application
    # "Key Vault Secrets Officer" (write) instead of "Key Vault Secrets User" (read).
    [switch]$TenantVault
)

# The tenant vault is a different resource with a different grant; give it its own
# default name so the two cannot collide in the same resource group.
if ($TenantVault -and $VaultName -eq "sdlc-kv") { $VaultName = "sdlc-kv-tenant" }

# The application's role. Read-only for the platform vault; write for the tenant vault,
# because create/rotate/delete of a BYOK key and every connector OAuth callback calls
# store_secret/delete_secret. Granting only "Secrets User" there makes Azure return 403,
# which shared/keyvault.py catches and reports as False - the credential is then silently
# lost while the API reports success.
$AppRole = if ($TenantVault) { "Key Vault Secrets Officer" } else { "Key Vault Secrets User" }

$ErrorActionPreference = "Stop"

function Step($n, $msg) { Write-Host "`n[$n] $msg" -ForegroundColor Cyan }

# -- 0. who are we ------------------------------------------------------------
Step 0 "Checking the Azure CLI login"
$account = az account show --output json | ConvertFrom-Json
if (-not $account) { throw "Not logged in. Run:  az login" }
Write-Host "    subscription: $($account.name)"
Write-Host "    user:         $($account.user.name)"
$subId = $account.id

# -- 1. resource group --------------------------------------------------------
Step 1 "Resource group $ResourceGroup"
az group create --name $ResourceGroup --location $Location --only-show-errors | Out-Null

# -- 2. the vault -------------------------------------------------------------
Step 2 "Key Vault (the name is globally unique - a suffix may be needed)"

# Key Vault DNS names are global, exactly like the ACS resource in setup_acs_email.ps1,
# so a plain name is very likely taken by another tenant. Worse, SOFT DELETE means a
# vault you deleted yourself still holds its name for 90 days, and the error for that
# case reads the same as somebody else owning it. Both are handled by moving on to the
# next candidate rather than stopping.
$userPrefix = ($account.user.name -split "@")[0] -replace "[^a-zA-Z0-9]", ""
$candidates = @(
    "$VaultName-$Env",
    "$userPrefix-$VaultName-$Env",
    "$VaultName-$Env-$userPrefix",
    "$VaultName-$Env-$(Get-Random -Minimum 1000 -Maximum 9999)"
) | ForEach-Object { $_.Substring(0, [Math]::Min(24, $_.Length)).TrimEnd('-') }  # KV max is 24 chars

$vault = $null
foreach ($candidate in $candidates) {
    Write-Host "    trying $candidate"
    $out = az keyvault create --name $candidate --resource-group $ResourceGroup `
        --location $Location --enable-rbac-authorization true `
        --only-show-errors 2>&1
    if ($LASTEXITCODE -eq 0) { $vault = $candidate; break }
    if ("$out" -match "VaultAlreadyExists|already in use|ConflictError|has been soft deleted") {
        Write-Host "      taken (or soft-deleted and still reserved)"
        continue
    }
    throw "Unexpected failure creating ${candidate}: $out"
}
if (-not $vault) { throw "Every candidate vault name is taken. Pass -VaultName something distinctive." }

$vaultUrl = "https://$vault.vault.azure.net"
Write-Host "    using: $vault" -ForegroundColor Green
$scope = "/subscriptions/$subId/resourceGroups/$ResourceGroup/providers/Microsoft.KeyVault/vaults/$vault"

# -- 3. your own write access -------------------------------------------------
Step 3 "Granting you 'Key Vault Secrets Officer' (needed to seed)"

# Creating a vault does NOT grant you access to its secrets under RBAC. This surprises
# people constantly: the vault appears in the portal, and every secret operation returns
# Forbidden until this assignment exists.
$me = az ad signed-in-user show --query id --output tsv
az role assignment create --assignee-object-id $me --assignee-principal-type User `
    --role "Key Vault Secrets Officer" --scope $scope --only-show-errors | Out-Null

# `az role assignment create` reports success unreliably (see setup_acs_email.ps1, where
# it prints "role": null on a working assignment). Read it back instead.
$ok = az role assignment list --assignee $me --scope $scope `
    --query "[?roleDefinitionName=='Key Vault Secrets Officer'] | length(@)" --output tsv
if ($ok -lt 1) { throw "Role assignment did not take - check your directory permissions." }
Write-Host "    confirmed"

# -- 4. the application's read access -----------------------------------------
if ($AppId) {
    Step 4 "Granting the application '$AppRole'"
    # Platform vault: READ ONLY, deliberately. An app that can WRITE platform secrets
    # turns any code-execution bug into credential REPLACEMENT, a much longer outage
    # than credential theft.
    # Tenant vault: WRITE is required - the app creates, rotates and deletes per-tenant
    # credentials. Confining that grant to this vault is the entire point of the split.
    az role assignment create --assignee $AppId `
        --role $AppRole --scope $scope --only-show-errors | Out-Null
    $appOk = az role assignment list --assignee $AppId --scope $scope `
        --query "[?roleDefinitionName=='$AppRole'] | length(@)" --output tsv
    if ($appOk -lt 1) { throw "App role assignment did not take." }
    Write-Host "    confirmed for $AppId"
} else {
    Step 4 "No -AppId given - skipping the application's grant"
    Write-Host "    The deployed app will get 403 until you run:" -ForegroundColor Yellow
    Write-Host "      az role assignment create --assignee <object-id> ``"
    Write-Host "        --role '$AppRole' --scope $scope"
}

# -- 5. hand off --------------------------------------------------------------
Step 5 "Next: seed the secrets"
Write-Host @"

  Vault ready:
    AZURE_KEY_VAULT_URL=$vaultUrl

  Role assignments take a minute or two to propagate. A seed run immediately after
  this often fails with Forbidden and then works on retry - the same lag the ACS
  setup hits with 535.

  Review what would be written, then write it:

    .\.venv\Scripts\python.exe -m scripts.seed_key_vault --env $Env --vault-url $vaultUrl --dry-run
    .\.venv\Scripts\python.exe -m scripts.seed_key_vault --env $Env --vault-url $vaultUrl

  Then on the DEPLOYED host only (never in local .env, which stays ENV=dev):
    ENV=$Env
    AZURE_KEY_VAULT_URL=$vaultUrl

"@ -ForegroundColor Green
