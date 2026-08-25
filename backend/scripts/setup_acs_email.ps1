# Provision Azure Communication Services email, and write the SMTP settings into
# backend/.env so the existing sender (shared/services/email.py) can use them.
#
# RUN THIS YOURSELF. It creates BILLABLE resources on your subscription and
# generates a client secret. Nothing here needs to be seen by anyone else — the
# secret is written straight into .env and only ever echoed masked.
#
#   cd C:\pwc_work\frontend\backend
#   .\scripts\setup_acs_email.ps1
#
# Cost: ACS Email is pay-as-you-go, roughly $0.00025 per email plus data. At
# invite/reset volume that is fractions of a cent, but it is NOT in Azure's
# always-free tier.
#
# Each step checks the one before it. If a flag is rejected, fix that step and
# re-run — every create is idempotent enough to retry, and a half-provisioned
# resource group is easier to reason about than a script that ploughs on.

$ErrorActionPreference = "Stop"

$RG        = "rg-sdlc-email"
$LOCATION  = "global"          # ACS resources live in 'global'
$DATA_LOC  = "UnitedStates"    # where message data rests; pick your region
$EMAIL_SVC = "sdlc-email-svc"
# Preferred name. ACS names are reserved across ALL of Azure, not just your
# subscription, so a plain one like "sdlc-acs" is very likely already taken —
# step 4 falls back to suffixed variants and reports which it settled on.
$ACS_NAME  = "sdlc-acs"
$APP_NAME  = "sdlc-smtp-sender"
$ENV_FILE  = Join-Path $PSScriptRoot "..\.env"

function Step($n, $msg) { Write-Host "`n[$n] $msg" -ForegroundColor Cyan }

# ── 0. who are we, and is the CLI ready ──────────────────────────────────────
Step 0 "Checking login and the communication extension"
$account = az account show --output json | ConvertFrom-Json
if (-not $account) { throw "Not logged in. Run:  az login" }
Write-Host "    subscription: $($account.name)"
Write-Host "    tenant:       $($account.tenantId)"
az extension add --name communication --only-show-errors 2>$null

# ── 1. resource group ────────────────────────────────────────────────────────
Step 1 "Resource group $RG"
az group create --name $RG --location eastus --only-show-errors | Out-Null

# ── 2. Email Communication Service ───────────────────────────────────────────
Step 2 "Email Communication Service $EMAIL_SVC"
az communication email create --name $EMAIL_SVC --resource-group $RG `
    --location $LOCATION --data-location $DATA_LOC --only-show-errors | Out-Null

# ── 3. Azure-managed domain (instant, no DNS) ────────────────────────────────
Step 3 "Azure-managed domain"
az communication email domain create --domain-name AzureManagedDomain `
    --email-service-name $EMAIL_SVC --resource-group $RG `
    --location $LOCATION --domain-management AzureManaged --only-show-errors | Out-Null
$domain = az communication email domain show --domain-name AzureManagedDomain `
    --email-service-name $EMAIL_SVC --resource-group $RG --output json | ConvertFrom-Json
$SENDER = "DoNotReply@$($domain.mailFromSenderDomain)"
Write-Host "    sender: $SENDER"

# ── 4. Communication Services resource, linked to that domain ────────────────
Step 4 "Communication Services (name is globally reserved — may need a suffix)"

# NameReservationTaken is the common case, not the exception: ACS names live in a
# global namespace shared by every Azure tenant. Rather than failing and making you
# guess, try the preferred name and then progressively more specific ones. The
# winner matters beyond aesthetics — it becomes the first segment of SMTP_USERNAME.
$userPrefix = ($account.user.name -split "@")[0] -replace "[^a-zA-Z0-9]", ""
$candidates = @(
    $ACS_NAME,
    "$userPrefix-$ACS_NAME",
    "$ACS_NAME-$userPrefix",
    "$ACS_NAME-$(Get-Random -Minimum 1000 -Maximum 9999)"
)
$acsName = $null
foreach ($candidate in $candidates) {
    Write-Host "    trying $candidate"
    $out = az communication create --name $candidate --resource-group $RG `
        --location $LOCATION --data-location $DATA_LOC --only-show-errors 2>&1
    if ($LASTEXITCODE -eq 0) { $acsName = $candidate; break }
    if ("$out" -notmatch "NameReservationTaken") { throw "Unexpected failure: $out" }
    Write-Host "      taken"
}
if (-not $acsName) { throw "Every candidate name is taken. Set `$ACS_NAME to something distinctive and re-run." }
$ACS_NAME = $acsName
Write-Host "    using: $ACS_NAME" -ForegroundColor Green

az communication update --name $ACS_NAME --resource-group $RG `
    --linked-domains $domain.id --only-show-errors | Out-Null
$acs = az communication show --name $ACS_NAME --resource-group $RG --output json | ConvertFrom-Json

# ── 5. Entra app registration — what ACS SMTP authenticates as ───────────────
Step 5 "App registration $APP_NAME"
$appId = az ad app list --display-name $APP_NAME --query "[0].appId" --output tsv
if (-not $appId) {
    $appId = az ad app create --display-name $APP_NAME --query appId --output tsv
    az ad sp create --id $appId --only-show-errors | Out-Null
    Start-Sleep -Seconds 10   # directory replication before the role assignment
}
Write-Host "    appId: $appId"

Step 6 "Client secret (written to .env, never printed)"
$secret = az ad app credential reset --id $appId --append `
    --display-name "smtp" --years 1 --query password --output tsv
if (-not $secret) { throw "Secret creation failed" }

Step 7 "Granting the app Contributor on the ACS resource"
az role assignment create --assignee $appId --role Contributor `
    --scope $acs.id --only-show-errors | Out-Null
# `role assignment create` returns roleDefinitionName as null even when it worked,
# so its own output proves nothing. Read the assignment back instead.
$granted = az role assignment list --assignee $appId --scope $acs.id `
    --query "[?roleDefinitionName=='Contributor'] | length(@)" --output tsv
if ($granted -lt 1) { throw "Role assignment did not take — check directory permissions." }
Write-Host "    Contributor confirmed"

# ── 8. write the settings ────────────────────────────────────────────────────
Step 8 "Writing SMTP settings to backend\.env"
$username = "$ACS_NAME.$appId.$($account.tenantId)"
$block = @"

# ── Azure Communication Services email (added by scripts/setup_acs_email.ps1) ──
SMTP_HOST=smtp.azurecomm.net
SMTP_PORT=587
SMTP_USE_TLS=true
SMTP_USE_SSL=false
SMTP_USERNAME=$username
SMTP_PASSWORD=$secret
EMAIL_FROM=$SENDER
EMAIL_FROM_NAME=SDLC Platform
"@
Add-Content -Path $ENV_FILE -Value $block -Encoding utf8

Write-Host "`nDone." -ForegroundColor Green
Write-Host "  SMTP_USERNAME = $username"
Write-Host "  SMTP_PASSWORD = <written to .env, $($secret.Length) chars>"
Write-Host "  EMAIL_FROM    = $SENDER"
Write-Host "`nGive the role assignment a couple of minutes to propagate — a smoke test"
Write-Host "run immediately after this often fails with 535 and then works on retry."
Write-Host "`nNow prove it delivers before trusting it with a real invite:"
Write-Host "  .\.venv\Scripts\python.exe -m scripts.email_smoke you@example.com" -ForegroundColor Yellow
