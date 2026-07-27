# HOPE LMS — Google Cloud Marketplace deployment assets

This repository contains the **deployment configuration** for installing
[HOPE LMS](https://www.cornerstonex.ai) from Google Cloud Marketplace: the
Helm chart, the Marketplace parameter schema, and the deployer/tester
container build files. It does **not** contain the HOPE LMS application
source code — the application itself is proprietary and is distributed
exclusively as container images through Cloud Marketplace. See
[NOTICE](NOTICE) for the license scope.

This guide covers deploying HOPE LMS from a **command-line interface**
(`kubectl` / `helm` / [`mpdev`](https://github.com/GoogleCloudPlatform/marketplace-k8s-app-tools)),
as an alternative to the guided flow in the Google Cloud Console. For the
Console flow, prerequisites, and the full parameter reference, see the
[Deployment & Configuration Guide](https://console.cloud.google.com/marketplace/product/cornerstonex-public/hope-lms)
on the product's Cloud Marketplace listing.

## Contents

| Path                  | Purpose                                                          |
| --------------------- | ----------------------------------------------------------------- |
| `hope-lms/`           | Helm chart rendered by the deployer at install time                |
| `schema.yaml`         | Marketplace parameter schema (mounted at `/data/schema.yaml`)      |
| `Dockerfile.deployer` | Builds the deployer image (Helm base image + chart + schema)       |
| `Dockerfile.tester`   | Builds the optional `mpdev verify` smoke-test image                |
| `LICENSE` / `NOTICE`  | Apache License 2.0 terms for the contents of this repository       |

## Overview

Installing HOPE LMS deploys four workloads into a single namespace on your
GKE cluster:

- **web** — the Next.js browser application users sign in to.
- **api** — the NestJS backend. This is the license-enforcement point; it
  will not start without a valid, unexpired license.
- **agent-engine** — the AI tier that drives training scenarios (reaches
  Vertex AI via Private Google Access).
- **a2f3d-engine** — the Audio2Face-3D bridge that connects to your
  customer-supplied NVIDIA NIM.

All data is stored in Cloud SQL (PostgreSQL 16) and Memorystore for Redis,
which you provision in your own project. Nothing in HOPE LMS calls out to
the public internet, so it runs correctly in a tenant with no egress.

## One-time setup

Complete these steps once per cluster, before your first CLI install.

1. **Configure `kubectl`** against your target GKE cluster:

   ```bash
   gcloud container clusters get-credentials CLUSTER_NAME \
     --project PROJECT_ID \
     --region REGION
   ```

2. **Install the `Application` custom resource definition.** Cloud
   Marketplace apps report their status through an `Application` resource;
   the CRD must exist on the cluster before you install:

   ```bash
   kubectl apply -f "https://raw.githubusercontent.com/GoogleCloudPlatform/marketplace-k8s-app-tools/master/crd/app-crd.yaml"
   ```

3. **Install the [`mpdev`](https://github.com/GoogleCloudPlatform/marketplace-k8s-app-tools) tool.**
   `mpdev` is Google's reference client for installing/uninstalling
   Marketplace Kubernetes apps from the deployer image, and is the supported
   CLI path for this chart (the deployer performs parameter substitution and
   `Application` resource generation that a bare `helm install` does not):

   ```bash
   docker pull gcr.io/cloud-marketplace-tools/k8s/dev
   # See the mpdev README for the wrapper script that runs it via docker:
   # https://github.com/GoogleCloudPlatform/marketplace-k8s-app-tools/blob/master/docs/mpdev.md
   ```

4. **Provision the customer-side prerequisites** in your GCP project
   (GKE with Workload Identity, Cloud SQL for PostgreSQL 16, Memorystore for
   Redis, a Cloud KMS keyring, Workload Identity service accounts, and a
   valid HOPE LMS license from your CornerstoneX representative). The full
   prerequisite checklist and every parameter's meaning is in the
   [Deployment & Configuration Guide](https://console.cloud.google.com/marketplace/product/cornerstonex-public/hope-lms)
   — the parameter names there match the `x-google-marketplace` schema names
   used below exactly.

## Installation

1. **Create the target namespace:**

   ```bash
   export NAMESPACE=hope-lms
   kubectl create namespace "$NAMESPACE"
   ```

2. **Write a parameters file** with the values collected during one-time
   setup. Every key corresponds to a property in [`schema.yaml`](schema.yaml);
   required properties are listed under `required:` in that file.

   ```bash
   cat > params.json <<'EOF'
   {
     "name": "hope-lms",
     "namespace": "hope-lms",
     "gcp.projectId": "YOUR_PROJECT_ID",
     "gcp.deployEnv": "prod",
     "domains.appUrl": "https://hope.agency.gov",
     "domains.corsOrigin": "https://hope.agency.gov",
     "domains.jwtIssuer": "https://api.hope.agency.gov",
     "domains.jwtAudience": "https://hope.agency.gov",
     "domains.cookieDomain": "agency.gov",
     "domains.brandingAssetPublicBaseUrl": "https://api.hope.agency.gov",
     "kms.jwtKmsKeyVersion": "projects/YOUR_PROJECT_ID/locations/us/keyRings/hope-lms/cryptoKeys/jwt-signing/cryptoKeyVersions/1",
     "kms.authDataKmsKey": "projects/YOUR_PROJECT_ID/locations/us/keyRings/hope-lms/cryptoKeys/auth-data",
     "serviceAccounts.apiGsa": "hope-lms-api@YOUR_PROJECT_ID.iam.gserviceaccount.com",
     "serviceAccounts.agentEngineGsa": "hope-lms-agent-engine@YOUR_PROJECT_ID.iam.gserviceaccount.com",
     "secrets.databaseUrl": "postgresql://USER:PASSWORD@HOST:5432/hope_lms",
     "secrets.redisUrl": "redis://HOST:6379",
     "secrets.internalSvcJwtPrivateKey": "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----",
     "secrets.internalSvcJwtPublicKey": "-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----",
     "secrets.agentEngineCallbackJwtPublicKey": "-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----",
     "secrets.apiCallbackJwtPrivateKey": "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----",
     "license.key": "PASTE_YOUR_SIGNED_LICENSE_STRING_HERE"
   }
   EOF
   ```

   > `secrets.sessionPepper`, `secrets.invitationPepper`, `secrets.ipHashPepper`,
   > and `secrets.captchaHmacKey` are auto-generated — omit them and Marketplace
   > tooling fills them in. Never commit `params.json` to version control; it
   > contains credentials.

3. **Deploy** using the deployer image, pinned to the release track (`0.1`)
   or an exact version (`0.1.0`):

   ```bash
   export REGISTRY=us-docker.pkg.dev/cornerstonex-public/hope-mtp
   export TAG=0.1

   mpdev install \
     --deployer="$REGISTRY/deployer:$TAG" \
     --parameters="$(cat params.json)"
   ```

   For production installs, prefer **pinning the deployer to an immutable
   digest** rather than a mutable tag, so a later push to the same tag cannot
   silently change what gets deployed:

   ```bash
   export DIGEST=$(gcloud artifacts docker images describe \
     "$REGISTRY/deployer:$TAG" --format='value(image_summary.digest)')
   mpdev install \
     --deployer="$REGISTRY/deployer@$DIGEST" \
     --parameters="$(cat params.json)"
   ```

4. **Verify the install** — confirm pods are running, the license was
   accepted, and the app responds:

   ```bash
   kubectl -n hope-lms get pods
   kubectl -n hope-lms logs deploy/hope-lms-api -c migrate-and-seed
   kubectl -n hope-lms logs deploy/hope-lms-api | grep -i license
   kubectl -n hope-lms exec deploy/hope-lms-web -- wget -qO- http://hope-lms-api:3001/health
   ```

## Basic usage

- **DNS, ingress, TLS.** Point your DNS records for the web and API hosts
  at your cluster's ingress/load balancer. TLS terminates at the load
  balancer, not in the application — TLS 1.2 is the minimum, 1.3 preferred.
  `domains.appUrl`, `domains.corsOrigin`, and `domains.jwtAudience` must all
  agree, and the API host must match `domains.jwtIssuer`, or logins/CORS
  will fail.
- **Signing in.** Browse to `domains.appUrl` once pods report `Running`.
  If `seedOnBoot` was left at its default (`true`), an initial application
  owner account is created on first boot.
- **License lifecycle.** The API checks the license at every boot and once
  per day thereafter. Within 30 days of expiry it logs a
  `LICENSE_EXPIRY_WARNING` audit event; an expired or invalid license causes
  the API pod to exit and enter `CrashLoopBackOff`. Renew by requesting a new
  license from CornerstoneX and updating the `license.key` parameter (or the
  `hope-lms-license` Secret directly), then letting the API pods roll — no
  connectivity to CornerstoneX is required at renewal time.

## Backup and restore

HOPE LMS keeps all durable state in Cloud SQL (PostgreSQL 16) and
Memorystore for Redis, both of which you provision and manage outside this
chart. Back up and restore using the standard GCP mechanisms for those
services:

```bash
# Backup (on-demand)
gcloud sql backups create --instance=YOUR_CLOUD_SQL_INSTANCE

# Restore to a new instance from a backup
gcloud sql backups restore BACKUP_ID \
  --restore-instance=YOUR_CLOUD_SQL_INSTANCE
```

Redis in Memorystore is used only for caching and ephemeral session/queue
state; it does not require backup for disaster recovery. No application
data lives on pod-local disk or in a `PersistentVolumeClaim`.

## Image updates

To move to a newer release track or patch version, re-run the install with
the new deployer tag — `mpdev` (and the Cloud Console) treat this as an
upgrade of the existing `Application` resource rather than a fresh install:

```bash
export TAG=0.2
mpdev install \
  --deployer="$REGISTRY/deployer:$TAG" \
  --parameters="$(cat params.json)"
```

Review the release notes for the target version (`publishedVersionMetadata`
in `schema.yaml`) before upgrading, and re-pin to the new immutable digest
per the [Installation](#installation) step above.

## Scaling

Each workload's replica count is a Helm value (`api.replicas`,
`web.replicas`, `agentEngine.replicas`, `a2f3dEngine.replicas`), defaulting
to `2`. Scale by re-installing with updated values, or directly:

```bash
kubectl -n hope-lms scale deployment/hope-lms-api --replicas=4
```

Horizontal Pod Autoscalers are included in the chart for each workload and
will scale within the `clusterConstraints` bounds declared in
[`schema.yaml`](schema.yaml).

## Deletion

```bash
mpdev uninstall \
  --deployer="$REGISTRY/deployer:$TAG" \
  --parameters="$(cat params.json)"

# Or, without mpdev:
kubectl -n hope-lms delete application hope-lms
kubectl delete namespace hope-lms
```

Deleting the namespace removes all in-cluster resources created by this
chart (Deployments, Services, ConfigMaps, Secrets, ServiceAccounts, HPAs,
NetworkPolicies). It does **not** delete your Cloud SQL instance, Memorystore
instance, or Cloud KMS keys — those are customer-managed and outlive the
application install. Clean those up separately if you are decommissioning
the deployment entirely.

## Troubleshooting

| Symptom | Likely cause | Resolution |
| --- | --- | --- |
| API pod `CrashLoopBackOff`; log shows `LICENSE_VALIDATION_FAILED` | Missing, malformed, or expired license | Re-paste the exact license string; if expired, install a renewed license |
| API fails at boot with a KMS or permission error | KMS key path wrong, or the API GSA lacks signer/decrypter roles | Verify `kms.jwtKmsKeyVersion` / `kms.authDataKmsKey` and the Workload Identity binding |
| API cannot reach the database or Redis | Wrong connection URL, or the cluster subnet can't reach the instance | Check `secrets.databaseUrl` / `secrets.redisUrl` and VPC/firewall/private-service-access |
| Login fails or CORS errors in the browser | Domain parameters disagree with actual hostnames | Align `domains.*` with your DNS and re-deploy |
| Avatar/voice features unavailable | No reachable NVIDIA NIM configured | Set `a2f3d.nimUrl` (and `a2f3d.nimSecureMode`) to your NIM |

## Support

For license requests, renewals, and deployment assistance, contact your
CornerstoneX representative.

## License

The contents of this repository (Helm chart, schema, and deployer/tester
build files) are licensed under the [Apache License 2.0](LICENSE). See
[NOTICE](NOTICE) for how this applies alongside the separate HOPE LMS
commercial license that governs the application itself.
