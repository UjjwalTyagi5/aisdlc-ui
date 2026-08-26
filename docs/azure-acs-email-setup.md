# Outbound email via Azure Communication Services

How to give this platform a working SMTP sender using an Azure subscription, with no
code change.

## Why this is needed

Two things in the platform send email, and both are currently inert:

| Trigger | Endpoint | What it sends | Link TTL |
|---|---|---|---|
| Onboarding a person with no account | `POST /onboarding` | set-your-password invite | 48h |
| Forgotten password | `POST /auth/forgot-password` | reset link | 2h |

With no `SMTP_HOST`, `send_email` logs the message at WARNING and returns `False` —
a supported state, not an error, so local development and the test suite need no mail
server. The practical consequence: **onboarding appears to work and nobody receives a
link.** The link is in the backend log instead.

Notifications do *not* use email. They are in-app rows addressed to a person and/or a
role (`shared/services/notifications.py`); nothing there imports the mail service.

## Why ACS, and what it costs

`shared/services/email.py` is deliberately provider-agnostic: Gmail, SES, SendGrid,
Mailgun and Azure Communication Services all speak SMTP, so switching provider is
configuration and never code. ACS is the natural fit for an Azure subscription and
needs no M365 licence — unlike Teams/SharePoint, which need one and are therefore not
reachable the same way.

**It is not in the always-free tier.** ACS Email is pay-as-you-go, roughly $0.00025 per
email plus data. At invite/reset volume that is fractions of a cent, and a free
account's credit covers it comfortably. Deleting the resource group ends all charges.

## Names to decide first

Everything below uses these. Change them here and carry them through.

| Thing | Value | Notes |
|---|---|---|
| Resource group | `rg-sdlc-email` | The kill switch — keep ONLY these resources in it |
| Email Communication Service | `sdlc-email-svc` | Holds the domain |
| Communication Services | `sdlc-acs` *(preferred — may need a suffix, see below)* | Sends. **Becomes the first segment of `SMTP_USERNAME`** |
| App registration | `sdlc-smtp-sender` | The identity ACS SMTP authenticates as |
| Data location | `United States` | **Cannot be changed after creation** — pick your users' region |

### The ACS name will probably collide

ACS resource names live in a namespace shared by **every Azure tenant**, not just
yours, so a plain name like `sdlc-acs` is very likely already reserved by somebody
else. Creating it fails with:

```
(NameReservationTaken) Specified name reservation is not available
```

This is the normal case, not bad luck. The CLI script tries the preferred name, then
`<you>-sdlc-acs`, then `sdlc-acs-<you>`, then a random suffix, and reports which it
settled on. In the portal you simply retype until one is accepted.

Whichever name wins **becomes the first segment of `SMTP_USERNAME`**, so note it —
it is not purely cosmetic.

## Before you start: check WHICH identity you are

The most likely failure is not permissions, it is being signed in as the wrong account
or in the wrong directory. The portal showing **"You don't have access" (401)** on
*Default Directory → Overview* almost always means this, not a missing role.

```bash
az account show --query "{tenantId:tenantId, user:user.name, subscription:name}" -o json
az rest --method GET --url "https://graph.microsoft.com/v1.0/me/memberOf?\$select=displayName"
```

The second command should list **Global Administrator** (or at least a role that can
create app registrations). If the CLI looks right but the portal 401s, the *browser* is
the problem: avatar (top right) → **Switch directory** → pick the directory whose id
matches `tenantId` above. If it is not listed, sign out and back in as the account the
CLI reports.

---

### On Windows, run the Azure CLI from PowerShell — not Git Bash

Git Bash rewrites arguments that look like Unix paths. An Azure resource id starts
with `/subscriptions/...`, so linking the domain fails with:

```
(LinkedInvalidPropertyId) Property id 'C:/Program Files/Git/subscriptions/...' is invalid
```

The id was mangled before Azure ever saw it. Use PowerShell for any `az` command
that takes a resource id, or set `MSYS_NO_PATHCONV=1`.

---

# Option A — Azure CLI (recommended)

Fastest, and it cannot create resources in the wrong tenant because the CLI is already
authenticated as the identity you just verified.

```powershell
cd C:\pwc_work\frontend\backend
az login                      # only if `az account show` failed above
.\scripts\setup_acs_email.ps1
```

**Run it in your own terminal.** It writes the client secret directly into
`backend/.env` and echoes only its length — piping the output anywhere else would
expose a live credential.

The script creates all four resources, the app registration, the role assignment, and
appends the settings block to `.env`. Each step checks the one before it, so a rejected
flag stops there rather than leaving a half-built resource group.

---

# Option B — Azure portal

## 1. Email Communication Service

1. **Create a resource** → search **"Email Communication Services"** → **Create**
2. Resource group: *Create new* → `rg-sdlc-email`
3. Name: `sdlc-email-svc`
4. **Data location**: your region — this cannot be changed later
5. **Review + create** → **Create**

## 2. Azure-managed domain

1. Open the resource → **Provision domains** → **+ Add domain** → **Azure managed domain**
2. Wait ~30 seconds

You get a domain like `8f3c1a2b-….azurecomm.net`. **Copy the sender address:**

```
DoNotReply@8f3c1a2b-….azurecomm.net
```

That is your `EMAIL_FROM`. No DNS records are needed — Azure verifies it for you.

## 3. Communication Services resource

Separate from step 1: the email service holds the domain, this one sends.

1. **Create a resource** → **"Communication Services"** → **Create**
2. Same resource group; name `sdlc-acs` — if rejected as unavailable, add a prefix
   (e.g. `<you>-sdlc-acs`) and **note the name that works**
3. **Data location must match step 1**
4. Create, then open it → **Email** → **Domains** → **+ Connect domain** → pick the
   domain from step 2 → **Connect**

## 4. App registration

ACS's SMTP relay authenticates as an application, not a user.

1. **Microsoft Entra ID** → **App registrations** → **+ New registration**
2. Name `sdlc-smtp-sender`, single tenant → **Register**
3. From **Overview**, copy **Application (client) ID** and **Directory (tenant) ID**
4. **Certificates & secrets** → **+ New client secret** → description `smtp`, 12 months
   → **Add**
5. **Copy the `Value` column immediately** — shown once, never again. Not "Secret ID".

Then grant it access:

6. Open the **`sdlc-acs`** resource → **Access control (IAM)** → **+ Add** →
   **Add role assignment**
7. Role **Contributor** → **Next** → **User, group, or service principal** →
   **+ Select members** → `sdlc-smtp-sender` → **Review + assign**

`Contributor` is broader than ideal; it is what ACS SMTP has historically required. If
a narrower built-in role now covers it, prefer that.

---

## 5. Settings — `backend/.env`

```ini
SMTP_HOST=smtp.azurecomm.net
SMTP_PORT=587
SMTP_USE_TLS=true
SMTP_USE_SSL=false
SMTP_USERNAME=<the-ACS-name-that-worked>.<Application-client-ID>.<Directory-tenant-ID>
SMTP_PASSWORD=<the secret Value from 4.5>
EMAIL_FROM=DoNotReply@<your-guid>.azurecomm.net
EMAIL_FROM_NAME=SDLC Platform
```

**`SMTP_USERNAME` is ONE string with two dots** joining three values — the ACS
resource name *as actually created*, the client ID, and the tenant ID. No spaces, no
angle brackets. This is the single most common mistake, and the name is the part
people get wrong after a collision forced a rename.

`EMAIL_FROM` must be an address on the domain you connected; ACS rejects anything else.

## 6. Prove it delivers

Before any real person depends on it:

```powershell
cd C:\pwc_work\frontend\backend
.\.venv\Scripts\python.exe -m scripts.email_smoke you@example.com
```

This sends the **real invite template** with a dummy token, so what lands in the inbox
is what a new joiner would actually see — including how it renders and whether it goes
to spam. It never prints `SMTP_PASSWORD`.

Then test the real path: onboard someone from the Users page and confirm the invite
arrives and the link works. `POST /onboarding` reports `invited: false` when the send
failed, which is the difference between an admin who knows to follow up and one who
assumes the person was told.

## Troubleshooting

| Symptom | Cause |
|---|---|
| Portal 401 on *Default Directory* | Browser signed into a different account/directory — see "check which identity" above |
| `535 Authentication failed` | `SMTP_USERNAME` malformed, or the role assignment has not propagated. Entra takes a few minutes — a smoke test run immediately after provisioning often fails once and then works |
| `(NameReservationTaken)` | The ACS name is reserved somewhere in Azure. Pick a more distinctive one — see "The ACS name will probably collide" |
| `(LinkedInvalidPropertyId)` with a path like `C:/Program Files/Git/subscriptions/...` | Git Bash mangled the resource id. Re-run that command in PowerShell |
| `az role assignment create` prints `"role": null` | Expected — it reports null even on success. Verify with `az role assignment list --assignee <appId> --scope <acsId>` |
| `Sender not allowed` / 403 | `EMAIL_FROM` is not on the connected domain |
| Mail sends but never arrives | Azure-managed domains are rate-limited and land in spam more readily; attach your own domain for production |
| `emails deliver: False` in logs | `SMTP_HOST` or `EMAIL_FROM` still unset — both are required before anything sends |

## Teardown

```powershell
az group delete --name rg-sdlc-email --yes
```

Removes every resource and stops all charges. The app registration lives in Entra, not
the resource group — delete it separately from **App registrations** if you want it gone.

## A caveat on this document

Azure's portal labels, CLI flags and the ACS username format have all changed before.
Where this document and the current Azure docs disagree, believe Azure. If a step's UI
does not match what is written here, that is worth correcting in this file rather than
guessing at the adjacent option.
