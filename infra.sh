#!/usr/bin/env bash
# ==============================================================================
# One-shot Azure provisioning for the AISDLC frontend. Idempotent — safe to re-run.
#
#   ./infra.sh
#
# Creates: aisdlc-rg / aisdlcacr2026 / aisdlc-aks (1x B2s_v2 = 2 vCPU, of the
# 4-vCPU free-trial regional quota) in southeastasia, then installs ingress-nginx
# pinned to a STATIC public IP (so the nip.io hostname survives every aks stop/start)
# and ArgoCD.
#
# Not Terraform on purpose: a free-trial single cluster doesn't earn a state file
# and a backend to keep it in. If this grows past one cluster, port it then.
# ==============================================================================
set -euo pipefail

SUB=ced0d1bb-7bfe-45c6-bffe-19a0869e9bdd
RG=aisdlc-rg
# uaenorth (Dubai): closest region to India where this Free Trial is actually ALLOWED to
# use B-series VMs, and it holds a full 4-vCPU quota nothing else competes for — so this
# cluster stops/starts independently of agentcore/idpflow/langfuse.
#
# southeastasia was the first pick and had to be abandoned: `az vm list-usage` reported
# "Standard Bsv2 Family 0/4" there, but the subscription is NOT PERMITTED to use B/D/A
# families in that region at all. Quota and the allowed-SKU list are independent gates.
# Authoritative check before changing this value:
#   az rest --method get --url ".../Microsoft.Compute/skus?api-version=2021-07-01&\$filter=location eq '<region>'" \
#     | jq '.value[]|select(.name=="Standard_B2s_v2").restrictions'
# An entry with "type": "Location" means the whole region is blocked for you.
LOC=uaenorth
ACR=aisdlcacr2026
AKS=aisdlc-aks
NODE_SIZE=Standard_B2s_v2   # 2 vCPU / 8 GiB, half the regional quota. Standard_B4s_v2 uses all 4.
PIP=aisdlc-ingress-ip

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

az account set --subscription "$SUB"

say "Resource group $RG ($LOC)"
az group create -n "$RG" -l "$LOC" -o none

say "Container registry $ACR"
az acr show -n "$ACR" -g "$RG" -o none 2>/dev/null \
  || az acr create -n "$ACR" -g "$RG" --sku Basic --admin-enabled true -o none

say "AKS cluster $AKS (1 node, $NODE_SIZE)"
az aks show -n "$AKS" -g "$RG" -o none 2>/dev/null || az aks create \
  -n "$AKS" -g "$RG" -l "$LOC" \
  --node-count 1 --node-vm-size "$NODE_SIZE" --tier free \
  --network-plugin azure --network-plugin-mode overlay \
  --attach-acr "$ACR" --generate-ssh-keys -o none

az aks get-credentials -n "$AKS" -g "$RG" --overwrite-existing

# The node resource group is the one AKS manages; putting the IP there means the
# cluster identity can already attach it — no extra role assignment needed.
NODE_RG=$(az aks show -n "$AKS" -g "$RG" --query nodeResourceGroup -o tsv)

say "Static public IP $PIP (in $NODE_RG)"
az network public-ip show -n "$PIP" -g "$NODE_RG" -o none 2>/dev/null || az network public-ip create \
  -n "$PIP" -g "$NODE_RG" -l "$LOC" --sku Standard --allocation-method Static -o none
IP=$(az network public-ip show -n "$PIP" -g "$NODE_RG" --query ipAddress -o tsv)
echo "Ingress IP: $IP"

say "ingress-nginx (pinned to $IP)"
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx >/dev/null 2>&1 || true
helm repo update >/dev/null
helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx \
  -n ingress-nginx --create-namespace \
  --set controller.service.loadBalancerIP="$IP" \
  --set-string controller.service.annotations."service\.beta\.kubernetes\.io/azure-pip-name"="$PIP" \
  --set-string controller.service.annotations."service\.beta\.kubernetes\.io/azure-load-balancer-resource-group"="$NODE_RG" \
  --set controller.replicaCount=1 \
  --wait --timeout 10m

say "Pinning ingress host to $IP.nip.io"
sed -i '' -E "s#(host: aisdlc\.).*#\1${IP}.nip.io#" Manifest/ingress.yaml 2>/dev/null \
  || sed -i -E "s#(host: aisdlc\.).*#\1${IP}.nip.io#" Manifest/ingress.yaml
grep 'host:' Manifest/ingress.yaml

say "ArgoCD"
kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -
# --server-side is REQUIRED: client-side apply stores the whole manifest in the
# kubectl.kubernetes.io/last-applied-configuration annotation, and the applicationsets
# CRD blows past the 262144-byte annotation limit ("CustomResourceDefinition ... is
# invalid: metadata.annotations: Too long").
kubectl apply --server-side --force-conflicts -n argocd \
  -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
kubectl -n argocd rollout status deploy/argocd-server --timeout=10m

cat <<EOF

$(say "Done — remaining manual steps")
ACR admin password (paste into the ADO pipeline variable ACR_PASSWORD, mark secret):
  az acr credential show -n $ACR --query passwords[0].value -o tsv

ArgoCD admin password:
  kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d

Point ArgoCD at the ADO repo (needs an ADO PAT with Code:Read), then create the app:
  kubectl -n argocd port-forward svc/argocd-server 8080:443 &
  argocd login localhost:8080 --username admin --password <above> --insecure
  argocd repo add https://dev.azure.com/IDP-CORE/AISDLC/_git/aisdlc-ui --username azdo --password <ADO_PAT>
  kubectl apply -f argocd/aisdlc-application.yaml

App will be at:  http://aisdlc.$IP.nip.io

Cost control (this IP and the manifests survive both):
  az aks stop  -n $AKS -g $RG
  az aks start -n $AKS -g $RG
EOF
