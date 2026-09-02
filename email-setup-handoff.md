# Pulling in outbound email (Azure Communication Services)

This is already on `main` — no need to wait on any open PR. Two things in the platform send
email, and both are currently silent until this is configured:

| Trigger | Endpoint | What it sends |
|---|---|---|
| Onboarding a new person | `POST /onboarding` | a set-your-password invite link |
| Forgotten password | `POST /auth/forgot-password` | a reset link |

With no SMTP host configured, the backend just **logs** the message (link included) instead
of sending it — that's how local dev has worked until now. This makes it actually send.

## 1. Pull latest `main`

```bash
git checkout main
git pull
```

No database migration is required — this is pure SMTP configuration, not a schema change.

## 2. Add the env lines to `backend/.env`

I'll send you the actual lines separately — paste them into `backend/.env`.

## 3. Restart the backend

`.env` is only read at process startup — a running `uvicorn --reload` picks up code changes
automatically but **not** `.env` changes. Restart it after editing `.env`.

## 4. Verify it actually sends

```bash
cd backend
.venv/Scripts/python.exe -m scripts.email_smoke you@example.com
```

Sends the real invite template with a dummy token to `you@example.com` — check it arrives
(and isn't in spam). It never prints the password.

Then test the real path: onboard someone from the Users page and confirm the invite arrives
and the link works.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `535 Authentication failed` | `SMTP_USERNAME` malformed, or the Azure role assignment hasn't propagated yet |
| `Sender not allowed` / 403 | `EMAIL_FROM` isn't on the connected/verified domain |
| Emails send but land in spam | Azure-managed domains are less trusted than a custom domain — expected for a shared/test resource |
| Still just logging, nothing sent | `SMTP_HOST` or `EMAIL_FROM` still unset |
