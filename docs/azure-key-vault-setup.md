# Secrets: `.env` in dev, Azure Key Vault everywhere else

One switch decides where every platform secret comes from.

```ini
ENV=dev     # read secrets from backend/.env, never contact Azure
ENV=prod    # read secrets from Azure Key Vault, refuse to start without it
```

`dev` is the default when `ENV` is unset, so a laptop or a CI runner with no Azure
access behaves exactly as it did before any of this existed.

## How it works

`config/env.py` reads roughly a hundred settings with plain `os.environ.get(...)`
calls, and every module in the codebase imports the constants it produces. Rather
than route each secret through a resolver — which would mean touching all of those
call sites and every test that patches them — `config/secret_bootstrap.py` runs
**once**, immediately after `load_dotenv()` and before the first constant is read,
and writes the values into `os.environ` itself.

So `env.py` is unchanged, and so is everything downstream. The only new lines are:

```python
load_dotenv(BACKEND_ROOT / ".env")

from config.secret_bootstrap import current_env, hydrate_environment
ENV: str = current_env()
hydrate_environment()
```

**That ordering is load-bearing.** It must run after `load_dotenv` (it needs `ENV`
and `AZURE_KEY_VAULT_URL`, which come from `.env`) and before anything reads a
setting. Move it below a constant and that constant silently keeps its `.env` value
in production.

## It fails closed, on purpose

With `ENV != dev` and the vault unreachable, **the process does not start**.

The alternative is to carry on with whatever `.env` happened to contain. That is how
a production API ends up running on `JWT_SECRET_KEY=change-me-in-production` —
signing tokens anybody can forge, with nothing in the logs that reads like an
incident. A refusal to boot is loud, immediate, and impossible to mistake for
working.

Three things are fatal:

| Condition | Why it stops the process |
|---|---|
| `AZURE_KEY_VAULT_URL` unset | Nothing to read from; `.env` is not trusted in this mode |
| Credential or vault unreachable | The configuration is unknown, not empty |
| Some reads succeeded, others failed on transport | Which secrets loaded is arbitrary — half a configuration is worse than none |

One thing is **not** fatal: an individual secret being **absent** from the vault. A
connector nobody has configured has no token, and demanding one would make every
optional integration mandatory.

## Secret names

`JWT_SECRET_KEY` becomes `sdlc-prod-jwt-secret-key`. Underscores become dashes and
everything is lowercased, because Key Vault permits only `[0-9a-zA-Z-]`.

The `sdlc-{env}-` prefix lets one vault hold several environments without their
secrets colliding, and matches the convention the Postgres DSN names already use.
Set `KV_SECRET_PREFIX=` (empty) to drop it when a vault serves one environment.

**The prefix is built from the running process's own `ENV`.** Seeding with
`--env prod` and deploying with `ENV=production` reads an empty vault and fails to
start. The two strings must match exactly.

### What is and is not a secret

`config/secret_bootstrap.PLATFORM_SECRETS` is the list, and it is the same list the
seeding script uses — so what gets written and what gets read can never drift.

The test is *"would this appear in an incident report if it leaked"*: credentials,
tokens, signing keys, webhook shared secrets. A Jira URL, a model name and a timeout
are configuration — they belong in `.env` in every environment, and putting them in
a vault only makes them harder to change.

### The database DSNs are deliberately absent

`shared/db.py`, `config/checkpoint.py` and `migrations/env.py` already resolve them
from Key Vault under their own `KV_SECRET_POSTGRES_*` names, and that path predates
this module. Two mechanisms writing the same value diverge the moment one convention
changes, and the resulting bug — the app talking to one database while migrations
talk to another — is not one anybody should have to diagnose.

---

# Setting it up

## 1. Create the vault

```powershell
cd C:\pwc_work\frontend\backend
az login          # only if `az account show` fails
.\scripts\setup_key_vault.ps1 -Env prod
```

Creates the resource group and vault, grants **you** `Key Vault Secrets Officer`
(write), and optionally grants the application `Key Vault Secrets User` (read only):

```powershell
.\scripts\setup_key_vault.ps1 -Env prod -AppId <managed-identity-object-id>
```

Read-only for the app is deliberate. It only ever calls `get_secret`; an app that can
also **write** turns any code-execution bug into credential *replacement*, which is a
much longer outage than credential theft.

### The vault name will probably collide

Key Vault DNS names are global, exactly like the ACS resource name. Worse, **soft
delete** means a vault you deleted yourself still reserves its name for 90 days, and
that error reads the same as another tenant owning it. The script tries
`sdlc-kv-prod`, then `<you>-sdlc-kv-prod`, then a random suffix, and reports which
won. Names are truncated to Key Vault's 24-character limit.

### Creating a vault does not grant you access to it

Under RBAC — which this uses, and which is the current default — the vault appears in
the portal and every secret operation returns **Forbidden** until a role assignment
exists. Step 3 of the script does this and reads the assignment back, because
`az role assignment create` reports success unreliably (it prints `"role": null` even
when it worked).

## 2. Seed the secrets

**Dry run first, always.** It shows exactly which names would be written without
touching the vault — the cheap way to catch a wrong `--env` before it scatters
production credentials under `sdlc-dev-*`.

```powershell
.\.venv\Scripts\python.exe -m scripts.seed_key_vault --env prod --vault-url https://VAULT.vault.azure.net --dry-run
.\.venv\Scripts\python.exe -m scripts.seed_key_vault --env prod --vault-url https://VAULT.vault.azure.net
```

**Run it yourself, and do not pipe its output anywhere you would not paste a
password.** It never prints a secret *value* — only names, lengths and the first two
characters — but the `.env` it reads is the real thing.

A setting that is empty or absent in `.env` is **skipped**, not written blank: Key
Vault rejects empty values, and a secret that exists holding nothing is worse than one
that does not exist, because the loader would treat it as configured.

A value already matching is left alone. Every `set_secret` creates a new *version*
even when nothing changed, and a vault whose history is mostly identical versions is
one where the audit trail no longer answers *"when did this credential actually
change"*.

## 3. Point the deployment at it

On the **deployed host only** — your local `.env` stays `ENV=dev`:

```ini
ENV=prod
AZURE_KEY_VAULT_URL=https://VAULT.vault.azure.net
```

Everything else that is a secret can come out of that host's `.env` entirely.

## 4. Confirm

```powershell
$env:ENV="prod"; $env:AZURE_KEY_VAULT_URL="https://VAULT.vault.azure.net"
.\.venv\Scripts\python.exe -c "import config.env; print('booted, secrets loaded')"
```

The startup log reports **names only**, never values:

```
ENV=prod - loading 30 secrets from https://VAULT.vault.azure.net
Key Vault hydration complete: 8 loaded, 22 not configured (ADO_PAT, FIGMA_PAT, ...)
```

That line exists so an operator can tell "not in the vault" apart from "in the vault
and not read". Printing what loaded would put every platform credential in the log.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `AZURE_KEY_VAULT_URL is not set` at boot | `ENV` is not `dev` and no vault is configured. Set one, or set `ENV=dev` |
| `Forbidden` when seeding | Role assignment has not propagated (wait a minute), or you hold `Secrets User` (read) rather than `Secrets Officer` (write) |
| App boots but a secret is empty | Name mismatch. The prefix comes from the running process's `ENV` — confirm it matches the `--env` you seeded with |
| `Could not authenticate` locally | `az login` expired |
| `Could not authenticate` on Azure | The managed identity has no `Key Vault Secrets User` assignment on the vault |
| Vault name rejected | Taken globally, or soft-deleted by you and still reserved for 90 days |
| `.ps1` fails with odd parse errors | Non-ASCII inside a double-quoted string. PowerShell 5.1 reads a BOM-less file as ANSI and a UTF-8 em-dash decodes into a curly quote that ends the string early. Both scripts here are pure ASCII for this reason |

## Teardown

```powershell
az keyvault delete --name <vault>
az keyvault purge  --name <vault>    # or the name stays reserved for 90 days
az group delete --name rg-sdlc-secrets --yes
```

`purge` is the step people forget, and it is why recreating a vault under the same
name fails a minute later.
