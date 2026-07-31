# Deploying aisdlc-ui to Azure (AKS + ADO + ArgoCD)

Same flow as AgentCore: **GitHub main → ADO repo → ADO pipeline builds amd64 → ACR →
pipeline bumps the image tag in `Manifest/` → ArgoCD syncs AKS.** No manual `kubectl` after setup.

## What gets created

| Thing | Name | Notes |
|---|---|---|
| Region | `southeastasia` | southindia/centralindia are already at their 4-vCPU free-trial quota |
| Resource group | `aisdlc-rg` | |
| Registry | `aisdlcacr2026` | Basic SKU, admin enabled |
| Cluster | `aisdlc-aks` | 1 × `Standard_B2s_v2` (2 vCPU / 8 GiB) — half the regional quota, room to add a node |
| Static IP | `aisdlc-ingress-ip` | pinned to ingress-nginx so the URL survives every `aks stop/start` |
| Namespace | `aisdlc` | |
| ADO project | `AISDLC` in org `IDP-CORE` | repo `aisdlc-ui`, pipeline `aisdlc-ui` |

Subscription is **Free Trial with spending limit ON** — it cannot overspend; it disables
resources instead of billing you.

---

## STEP 1 — Azure infra (scripted)

```bash
./infra.sh
```

Idempotent, re-runnable. It creates the RG/ACR/AKS, reserves the static IP, installs
ingress-nginx pinned to it, rewrites `Manifest/ingress.yaml` with the real
`<ip>.nip.io` host, installs ArgoCD, and prints the passwords you need next.

> Terraform was skipped on purpose — one free-trial cluster doesn't earn a state file
> and a remote backend. Port it if this grows past one cluster.

Commit the ingress host change:

```bash
git add Manifest/ingress.yaml && git commit -m "chore: pin ingress host to allocated IP"
```

## STEP 2 — ADO project + repo (portal)

Already created (project `AISDLC`, repo `aisdlc-ui`). To recreate from scratch:

```bash
az devops project create --name AISDLC --org https://dev.azure.com/IDP-CORE \
  --visibility private --source-control git --process Agile
az repos create --name aisdlc-ui --project AISDLC --org https://dev.azure.com/IDP-CORE
```

Push:

```bash
git remote add ado https://dev.azure.com/IDP-CORE/AISDLC/_git/aisdlc-ui
git push ado main
```

## STEP 3 — Pipeline

1. **Pipelines → New pipeline → Azure Repos Git → `aisdlc-ui` → Existing Azure Pipelines YAML file →
   `/azure-pipelines.yml`** → **Save** (not Run yet).
2. **Edit → Variables → New variable**, twice:
   - `ACR_USERNAME` = `aisdlcacr2026`
   - `ACR_PASSWORD` = output of `az acr credential show -n aisdlcacr2026 --query passwords[0].value -o tsv` → **tick "Keep this value secret"**
3. **Project Settings → Repos → `aisdlc-ui` → Security →** select **`AISDLC Build Service (IDP-CORE)`**
   → set **Contribute** = **Allow**. Without this the tag-bump step can't push and every build fails at the last step.
4. **Run pipeline.**

## STEP 4 — Point ArgoCD at the repo

ADO private repos need credentials — ArgoCD can't clone anonymously.

1. ADO → User settings → **Personal access tokens** → New token, scope **Code (Read)**.
2. Then (passwords printed by `infra.sh`):

```bash
kubectl -n argocd port-forward svc/argocd-server 8080:443 &
argocd login localhost:8080 --username admin \
  --password "$(kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d)" --insecure
argocd repo add https://dev.azure.com/IDP-CORE/AISDLC/_git/aisdlc-ui --username azdo --password <PAT>
kubectl apply -f argocd/aisdlc-application.yaml
argocd app sync aisdlc
```

App is live at `http://aisdlc.<ip>.nip.io` (exact URL printed by `infra.sh`).

## Day-to-day: shipping a change

ADO is the deploy remote; GitHub (`origin`) is not wired into CI at all.

```bash
git commit -am "feat: ..."
git push ado main          # triggers the pipeline -> ACR -> manifest bump -> ArgoCD
```

---

## Cost control — up and down on demand

```bash
az aks stop  -n aisdlc-aks -g aisdlc-rg    # compute drops to ~zero; disks + ACR + static IP are pennies
az aks start -n aisdlc-aks -g aisdlc-rg    # ~5 min to come back
```

The static IP is reserved separately from the cluster, so the `nip.io` URL is identical
after every restart. PVCs and the ArgoCD install persist too — nothing to redo.

Your other three clusters (`agentcore-aks`, `idpflow-aks`, `langfuse-aks`) are in different
regions, so this one doesn't compete with them for quota. All four can be stopped/started
independently.

---

## Configuration

`NEXT_PUBLIC_*` values are **inlined into the client bundle at build time** — a ConfigMap
edit will not change them. To move the app off mock mode, change the pipeline variables
`NEXT_PUBLIC_AUTH_MODE` / `NEXT_PUBLIC_API_MOCKS` / `NEXT_PUBLIC_API_BASE` in
`azure-pipelines.yml` (or the Variables UI) and re-run — they're passed as `--build-arg`.

Server-side runtime values live in `Manifest/frontend-ConfigMap.yaml`. Secrets go in a
`frontend-secrets` Secret created out-of-band so ArgoCD never prunes or logs them:

```bash
kubectl create secret generic frontend-secrets -n aisdlc \
  --from-literal=AUTH0_SECRET=... --from-literal=AUTH0_CLIENT_SECRET=...
```

The Deployment mounts it with `optional: true`, so it works fine before you create it.

## Adding the Python backend later

`aisdlc` (github.com/dev-agentops/aisdlc) is a separate repo. When you deploy it, add a
`backend-deploy.yaml` to `Manifest/`, extend the pipeline with a second `docker build`, and
set `NEXT_PUBLIC_API_BASE` to the in-cluster URL. The 2 spare vCPU of regional quota are
there for exactly this — add a second node with
`az aks scale -n aisdlc-aks -g aisdlc-rg --node-count 2`.
