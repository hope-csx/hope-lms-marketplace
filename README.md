# HOPE MTP — Google Cloud Marketplace deployment assets

This repository contains the **deployment configuration** for installing
[HOPE MTP](https://www.cornerstonex.ai) — the Metahuman Training Platform
bundled with the HOPE Metahuman Service it runs on — from Google Cloud
Marketplace: the Helm chart (with the HOPE Metahuman Service chart vendored
as a subchart), the Marketplace parameter schema, and the deployer's container
build files. It does **not** contain application source code — both
applications are proprietary and are distributed exclusively as container
images through Cloud Marketplace. See [NOTICE](NOTICE) for the license scope.

This guide covers deploying from a **command-line interface**
(`kubectl` / `helm` / [`mpdev`](https://github.com/GoogleCloudPlatform/marketplace-k8s-app-tools)),
as an alternative to the guided flow in the Google Cloud Console. For the
Console flow, prerequisites, and the full parameter reference, see the
[Deployment & Configuration Guide](docs/deploy-guide.html) in this repository
or on the product's
[Cloud Marketplace listing](https://console.cloud.google.com/marketplace/product/cornerstonex-public/hope-lms).

> **Version 0.2 is a new application generation.** It replaces the 0.1.x
> "HOPE LMS" releases with a bundle of two products, and image names,
> parameter names and Kubernetes resource names changed. A 0.1.x install must
> be removed before 0.2 is installed into the same namespace; see
> [Upgrading from 0.1.x](#upgrading-from-01x).

## Contents

| Path                                      | Purpose                                                                        |
| ----------------------------------------- | ------------------------------------------------------------------------------ |
| `hope-lms/`                               | Umbrella Helm chart rendered by the deployer at install time                   |
| `hope-lms/charts/hope-metahuman-service/` | The HOPE Metahuman Service chart, vendored from the commit in `HOPE_REF`       |
| `HOPE_REF`                                | The HOPE Metahuman Service commit this release's chart and images come from    |
| `schema.yaml`                             | Marketplace parameter schema (mounted at `/data/schema.yaml`)                  |
| `Dockerfile.deployer`                     | Builds the deployer image (Helm base image + chart + schema)                   |
| `apptest/deployer/`                       | Overlay used only by Marketplace's automated test deployment                   |
| `deployer/`                               | Readiness wait that reports which container blocked a failed install           |
| `docs/deploy-guide.html`                  | Customer-facing Deployment & Configuration Guide (self-contained HTML)         |
| `LICENSE` / `NOTICE`                      | Apache License 2.0 terms for the contents of this repository                   |

`Dockerfile.deployer` is here for transparency and for customers who prefer to
rebuild the deployer image themselves; a normal install uses the pre-built
`deployer` image published to the registry below and never builds anything. The
build applies operating-system security updates and rebuilds `helm` and
`kubectl` from source as it runs, so it needs internet access — build it on a
connected host and mirror the result if your install environment is disconnected.

`apptest/deployer/` supports Marketplace's own release verification, not your
install. `mpdev install`, documented below, ignores it entirely. `mpdev verify`
does not: it installs in test mode, which substitutes throwaway settings and
in-cluster Postgres and Redis, so use it to sanity-check a rebuilt deployer
rather than to validate a real deployment.

## Overview

Installing HOPE MTP deploys two products into a single namespace on your GKE
cluster:

**The Metahuman Training Platform**

- **mtp-web** — the learner, author and administrator portal.
- **mtp-api** — the control plane and sole owner of the MTP database. A
  license-enforcement point; it will not start without a valid, unexpired
  license.
- **mtp-session-gateway** — the WebSocket broker for live training sessions.
- **mtp-assessment-engine**, **mtp-curriculum-engine** — the inference tier
  (Vertex AI via Private Google Access).
- **qdrant** — an in-cluster vector store (StatefulSet + PersistentVolumeClaim)
  used by the curriculum engine.

**The HOPE Metahuman Service**

- **api** — the HOPE backend; the second license-enforcement point.
- **admin-web** — the HOPE administration portal.
- **agent-engine** — the conversational AI tier (Vertex AI).
- **a2f3d-engine** — the Audio2Face-3D bridge to your customer-operated inference server.
- **avatar-bridge** — optional, only when live Premium avatars are enabled.

All durable state except the vector index lives in Cloud SQL (PostgreSQL 16,
**two databases** — one per product) and Memorystore for Redis, which you
provision in your own project. Nothing calls out to the public internet, so the
bundle runs correctly in a tenant with no egress.

## Registry layout

Every image is published under a single product root path. The MTP `api` is
the **primary image** and therefore lives at that root itself, with no name
segment — the other images are siblings beneath it:

| Image                            | Path                                                                |
| -------------------------------- | ------------------------------------------------------------------- |
| MTP `api` (product root)         | `us-docker.pkg.dev/cornerstonex-public/hope-mtp/hope-lms`           |
| MTP `web`                        | `.../hope-mtp/hope-lms/web`                                         |
| MTP `session-gateway`            | `.../hope-mtp/hope-lms/session-gateway`                             |
| MTP `assessment-engine`          | `.../hope-mtp/hope-lms/assessment-engine`                           |
| MTP `curriculum-engine`          | `.../hope-mtp/hope-lms/curriculum-engine`                           |
| `qdrant`                         | `.../hope-mtp/hope-lms/qdrant`                                      |
| HOPE `api`                       | `.../hope-mtp/hope-lms/hope-api`                                    |
| HOPE `admin-web`                 | `.../hope-mtp/hope-lms/hope-admin-web`                              |
| HOPE `agent-engine`              | `.../hope-mtp/hope-lms/hope-agent-engine`                           |
| HOPE `a2f3d-engine`              | `.../hope-mtp/hope-lms/hope-a2f3d-engine`                           |
| HOPE `avatar-bridge`             | `.../hope-mtp/hope-lms/hope-avatar-bridge`                          |
| HOPE `workflow-runner`           | `.../hope-mtp/hope-lms/hope-workflow-runner` (declared, not deployed) |
| `deployer`                       | `.../hope-mtp/hope-lms/deployer`                                    |

Each is tagged with both the release track (`0.2`) and the exact version
(`0.2.3`). When Marketplace installs the app it re-publishes these images into
its own registry and rewrites the chart's image values accordingly, so the
paths above matter only if you are mirroring images into an internal registry
for a disconnected install:

```bash
export ROOT=us-docker.pkg.dev/cornerstonex-public/hope-mtp/hope-lms
for img in "" /web /session-gateway /assessment-engine /curriculum-engine /qdrant \
           /hope-api /hope-admin-web /hope-agent-engine /hope-a2f3d-engine \
           /hope-avatar-bridge /hope-workflow-runner /deployer; do
  crane copy "$ROOT$img:0.2.3" "YOUR_REGISTRY/hope-lms$img:0.2.3"
done
```

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

4. **Provision the customer-side prerequisites** in your GCP project:

   - GKE with Workload Identity and Dataplane V2.
   - Cloud SQL for PostgreSQL 16 with **two databases**, one for MTP and one
     for HOPE. They must never share a database — both run schema migrations.
   - Memorystore for Redis (one instance, shared).
   - **Cloud KMS**, HSM-backed. Each product keeps one key per data domain so
     any one can be rotated or revoked alone; a shared key ring is fine.
     MTP needs one `ASYMMETRIC_SIGN` key version and one `ENCRYPT_DECRYPT` key
     (a second, for learner documents, is optional). HOPE needs two
     `ASYMMETRIC_SIGN` key versions (access tokens, workflow signing) and six
     `ENCRYPT_DECRYPT` keys (auth data, tool credentials, guardrail data,
     webhook secrets, conversation data, workflow logs). **The HOPE api
     refuses to start without every one of them.**
   - Workload Identity service accounts for each workload that calls a Google
     API.
   - An SSD StorageClass for the 20 GiB Qdrant volume.
   - Two RS256 keypairs for HOPE's internal service tokens.
   - **An SMTP relay for HOPE** — a host and a sender address it is authorised
     to send for. HOPE relays invitations and password resets and refuses to
     start a production deployment on its logging transport, which would write
     single-use invitation tokens into the application log. MTP's own mail
     settings stay optional.
   - A valid HOPE MTP license from your CornerstoneX representative.
   - For animated Standard 3D avatars, an NVIDIA L4-class GPU node and a
     customer-operated Audio2Face-3D inference server. NVIDIA's NIM is
     end-of-life; use a server built from NVIDIA's open-source Audio2Face-3D
     SDK and model weights, or another server implementing the same
     `nvidia_ace` `A2FControllerService` gRPC contract. The Marketplace package
     does not install or manage this server.

5. **Prepare Audio2Face-3D (only when using animated Standard 3D avatars):**

   1. Deploy the inference server on the GPU node before installing HOPE MTP.
      It must be reachable from the target namespace over gRPC, normally on
      port `52000`. Bake or pre-stage the model weights so no runtime internet
      access is required.
   2. Issue an mTLS server certificate whose DNS SAN covers the hostname you
      will configure, and a client certificate for HOPE's `a2f3d-engine`.
      Configure the inference server to trust that client certificate.
   3. Set `hope.a2f3d.nimUrl` to the reachable `HOST:PORT`, leave
      `hope.a2f3d.nimSecureMode` at `mtls`, and provide the root CA, client
      certificate and client key in the three `hope.a2f3d.nimMtls.*`
      parameters. The `nim*` names are retained for compatibility; no NVIDIA
      NIM or NGC entitlement is required.

   Every parameter's meaning is in the
   [Deployment & Configuration Guide](docs/deploy-guide.html) — the parameter
   names there match the `x-google-marketplace` schema names used below exactly.

## Installation

1. **Create the target namespace:**

   ```bash
   export NAMESPACE=hope-mtp
   kubectl create namespace "$NAMESPACE"
   ```

2. **Write a parameters file** with the values collected during one-time
   setup. Every key corresponds to a property in [`schema.yaml`](schema.yaml);
   required properties are listed under `required:` in that file. Keys
   prefixed `hope.` configure the bundled HOPE Metahuman Service; keys
   prefixed `global.` are shared by both products.

   ```bash
   cat > params.json <<'EOF'
   {
     "name": "hope-mtp",
     "namespace": "hope-mtp",

     "global.gcp.projectId": "YOUR_PROJECT_ID",
     "global.secrets.redisUrl": "redis://HOST:6379",
     "global.license.key": "PASTE_YOUR_SIGNED_LICENSE_STRING_HERE",

     "domains.appUrl": "https://train.agency.gov",
     "domains.apiUrl": "https://api.train.agency.gov",
     "domains.cookieDomain": "agency.gov",
     "kms.jwtKmsKeyVersion": "projects/YOUR_PROJECT_ID/locations/us/keyRings/mtp/cryptoKeys/jwt-signing/cryptoKeyVersions/1",
     "kms.authDataKmsKey": "projects/YOUR_PROJECT_ID/locations/us/keyRings/mtp/cryptoKeys/auth-data",
     "serviceAccounts.apiGsa": "mtp-api@YOUR_PROJECT_ID.iam.gserviceaccount.com",
     "serviceAccounts.assessmentEngineGsa": "mtp-assessment-engine@YOUR_PROJECT_ID.iam.gserviceaccount.com",
     "serviceAccounts.curriculumEngineGsa": "mtp-curriculum-engine@YOUR_PROJECT_ID.iam.gserviceaccount.com",
     "secrets.databaseUrl": "postgresql://USER:PASSWORD@HOST:5432/mtp",
     "qdrant.storageClass": "premium-rwo",

     "hope.domains.appUrl": "https://metahuman.agency.gov",
     "hope.domains.jwtIssuer": "https://api.metahuman.agency.gov",
     "hope.domains.jwtAudience": "https://metahuman.agency.gov",
     "hope.domains.cookieDomain": "agency.gov",
     "hope.kms.jwtKmsKeyVersion": "projects/YOUR_PROJECT_ID/locations/us/keyRings/hope/cryptoKeys/jwt-signing/cryptoKeyVersions/1",
     "hope.kms.authDataKmsKey": "projects/YOUR_PROJECT_ID/locations/us/keyRings/hope/cryptoKeys/auth-data",
     "hope.kms.toolCredentialKmsKey": "projects/YOUR_PROJECT_ID/locations/us/keyRings/hope/cryptoKeys/tool-credentials",
     "hope.kms.workflowSigningKmsKeyVersion": "projects/YOUR_PROJECT_ID/locations/us/keyRings/hope/cryptoKeys/workflow-signing/cryptoKeyVersions/1",
     "hope.kms.guardrailDataKmsKey": "projects/YOUR_PROJECT_ID/locations/us/keyRings/hope/cryptoKeys/guardrail-data",
     "hope.kms.webhookSecretKmsKey": "projects/YOUR_PROJECT_ID/locations/us/keyRings/hope/cryptoKeys/webhook-secrets",
     "hope.kms.conversationDataKmsKey": "projects/YOUR_PROJECT_ID/locations/us/keyRings/hope/cryptoKeys/conversation-data",
     "hope.kms.workflowLogDataKmsKey": "projects/YOUR_PROJECT_ID/locations/us/keyRings/hope/cryptoKeys/workflow-log-data",
     "hope.mail.smtpHost": "smtp.agency.gov",
     "hope.mail.fromAddress": "no-reply@agency.gov",
     "hope.a2f3d.nimUrl": "a2f3d-inference.gpu-services.svc.cluster.local:52000",
     "hope.a2f3d.nimSecureMode": "mtls",
     "hope.a2f3d.nimMtls.rootCaPem": "-----BEGIN CERTIFICATE-----\n...\n-----END CERTIFICATE-----",
     "hope.a2f3d.nimMtls.clientCertPem": "-----BEGIN CERTIFICATE-----\n...\n-----END CERTIFICATE-----",
     "hope.a2f3d.nimMtls.clientKeyPem": "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----",
     "hope.serviceAccounts.apiGsa": "hope-api@YOUR_PROJECT_ID.iam.gserviceaccount.com",
     "hope.serviceAccounts.agentEngineGsa": "hope-agent-engine@YOUR_PROJECT_ID.iam.gserviceaccount.com",
     "hope.secrets.databaseUrl": "postgresql://USER:PASSWORD@HOST:5432/hope_metahuman",
     "hope.secrets.internalSvcJwtPrivateKey": "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----",
     "hope.secrets.internalSvcJwtPublicKey": "-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----",
     "hope.secrets.agentEngineCallbackJwtPublicKey": "-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----",
     "hope.secrets.apiCallbackJwtPrivateKey": "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----"
   }
   EOF
   ```

   > Peppers, service tokens and the Qdrant API key (`secrets.sessionPepper`,
   > `secrets.gatewayServiceToken`, `qdrant.apiKey`, `hope.secrets.sessionPepper`,
   > …) are auto-generated — omit them and Marketplace tooling fills them in.
   > Never commit `params.json` to version control; it contains credentials.

3. **Deploy** using the deployer image, pinned to the release track (`0.2`)
   or an exact version (`0.2.3`):

   ```bash
   export REGISTRY=us-docker.pkg.dev/cornerstonex-public/hope-mtp/hope-lms
   export TAG=0.2

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

4. **Verify the install** — confirm pods are running and both licenses were
   accepted:

   ```bash
   kubectl -n hope-mtp get pods
   kubectl -n hope-mtp logs deploy/mtp-api -c migrate
   kubectl -n hope-mtp logs deploy/mtp-api -c api | grep LICENSE_
   kubectl -n hope-mtp logs deploy/api | grep LICENSE_
   kubectl -n hope-mtp exec deploy/mtp-web -- wget -qO- http://mtp-api/health/ready
   ```

   > Workload names are fixed by the chart and do not vary with the release
   > name: MTP's are prefixed `mtp-` (`mtp-api`, `mtp-web`, …), HOPE's are
   > unprefixed (`api`, `admin-web`, …), and the vector store is `qdrant`. The
   > web and API Services listen on port 80.

5. **Connect your training tenant to HOPE.** The chart deploys HOPE alongside
   MTP but does not bind a tenant to it. Sign in to the HOPE admin portal
   (`hope.domains.appUrl`) as its application owner, create the HOPE
   organization and a machine credential for your training tenant, then sign
   in to the MTP portal (`domains.appUrl`), open **Platform → Tenants**, and
   **Connect to HOPE** with the organization id, your public HOPE API URL
   (`hope.domains.jwtIssuer`) and the credential. Section 7 of the
   [Deployment & Configuration Guide](docs/deploy-guide.html) walks through it.

## Basic usage

- **DNS, ingress, TLS.** Expose `mtp-web`, `mtp-api`, `admin-web` and `api`
  through your ingress with TLS you control (TLS 1.2 minimum, 1.3 preferred).
  Route `/ws` and `/ws/*` on the MTP API host to `mtp-session-gateway:80` with
  a long backend timeout; WebSocket upgrades must pass. `domains.appUrl` and
  `domains.apiUrl` (and their `hope.domains.*` counterparts) must match your
  actual hostnames, or logins will fail.
- **Signing in.** Browse to `domains.appUrl` and `hope.domains.appUrl` once
  pods report `Running`. With `seedOnBoot` / `hope.seedOnBoot` at their default
  (`true`), an initial application owner account exists in each product.
- **License lifecycle.** Both APIs check the license at every boot and once
  per day thereafter. Within 30 days of expiry they log a
  `LICENSE_EXPIRY_WARNING`; an expired or invalid license causes the API pods
  to exit and enter `CrashLoopBackOff`. Renew by requesting a new license from
  CornerstoneX and updating the `global.license.key` parameter (or the
  `hope-lms-license` and `hope-metahuman-license` Secrets directly), then
  letting the API pods roll — no connectivity to CornerstoneX is required.
- **Consumption tracking.** Every Pod carries the `goog-partner-solution`
  label Google uses to attribute consumption. Do not remove it: `mtp-api`
  checks its own label every 15 minutes and serves only `/health` while it is
  missing.

## Backup and restore

Durable state lives in Cloud SQL (PostgreSQL 16, two databases) and, for the
vector index, the `qdrant-storage` PersistentVolumeClaim. Redis holds only
ephemeral cache and queue state. Back up Cloud SQL with the standard GCP
mechanisms:

```bash
# Backup (on-demand)
gcloud sql backups create --instance=YOUR_CLOUD_SQL_INSTANCE

# Restore to a new instance from a backup
gcloud sql backups restore BACKUP_ID \
  --restore-instance=YOUR_CLOUD_SQL_INSTANCE
```

The vector index can be rebuilt by re-indexing documents; to preserve it,
snapshot the Persistent Disk behind `qdrant-storage` with your usual disk
snapshot schedule.

## Image updates

To move to a newer patch version on the same track, re-run the install with
the new deployer tag — `mpdev` (and the Cloud Console) treat this as an update
of the existing `Application` resource:

```bash
export TAG=0.2
mpdev install \
  --deployer="$REGISTRY/deployer:$TAG" \
  --parameters="$(cat params.json)"
```

Review the release notes for the target version (`publishedVersionMetadata`
in `schema.yaml`) before upgrading, and re-pin to the new immutable digest
per the [Installation](#installation) step above.

## Upgrading from 0.1.x

0.2 is not an in-place upgrade of a 0.1.x install: the image set, the
parameter names (`global.*`, `mtp.*`, `hope.*`) and the Kubernetes resource
names (`mtp-*`) all changed, and the bundled HOPE Metahuman Service needs its
own database, KMS keys and service accounts. Uninstall the 0.1.x release
(see [Deletion](#deletion)), provision the additional prerequisites, then
install 0.2 into the namespace with a new `params.json`.

## Scaling

Replica counts are Helm values (`mtp.api.replicas`, `mtp.web.replicas`,
`hope.api.replicas`, …), defaulting to `2`. Horizontal Pod Autoscalers are
included for every Deployment and scale within the `clusterConstraints`
declared in [`schema.yaml`](schema.yaml). Qdrant is a single replica by
design (ReadWriteOnce storage). Scale a Deployment directly with:

```bash
kubectl -n hope-mtp scale deployment/mtp-api --replicas=4
```

## Deletion

```bash
mpdev uninstall \
  --deployer="$REGISTRY/deployer:$TAG" \
  --parameters="$(cat params.json)"

# Or, without mpdev:
kubectl -n hope-mtp delete application hope-mtp
kubectl delete namespace hope-mtp
```

Deleting the `Application` removes every in-cluster resource created by this
chart **except** the `qdrant-storage` PersistentVolumeClaim, which is kept so
the vector index survives a reinstall; delete it explicitly
(`kubectl -n hope-mtp delete pvc qdrant-storage`) when decommissioning. Your
Cloud SQL instance, Memorystore instance and Cloud KMS keys are
customer-managed and outlive the install — clean those up separately.

## Troubleshooting

| Symptom                                                                          | Likely cause                                                        | Resolution                                                                                                                                                    |
| -------------------------------------------------------------------------------- | ------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `mtp-api` or `api` pod `CrashLoopBackOff`; log shows `LICENSE_VALIDATION_FAILED` | Missing, malformed, or expired license                              | Re-paste the exact license string into `global.license.key`; if expired, install a renewed license                                                            |
| An API fails at boot with a KMS or permission error                              | KMS key path wrong, or the GSA lacks signer/decrypter roles         | Verify the `kms.*` / `hope.kms.*` paths and the Workload Identity bindings                                                                                    |
| `migrate` init container fails                                                   | Wrong connection URL, or the two products share one database        | Check `secrets.databaseUrl` and `hope.secrets.databaseUrl` point at two different databases the cluster can reach                                             |
| `qdrant-0` stays `Pending`                                                       | The PersistentVolumeClaim did not bind                              | Confirm `qdrant.storageClass` exists and can provision a 20 GiB ReadWriteOnce volume                                                                          |
| 503 on every MTP route except `/health`                                          | The `goog-partner-solution` label was removed from the `mtp-api` Pod | Restore the label; the API recovers on its next 15-minute check                                                                                                |
| Training sessions never start                                                    | Tenant not bound to HOPE, or `/ws` not routed to the session gateway | Complete the post-install binding (Installation step 5) and route `/ws` on the API host to `mtp-session-gateway`                                              |
| Avatar animation is unavailable                                                  | No reachable Audio2Face-3D inference server configured              | Deploy a compatible open-source-SDK inference server, set `hope.a2f3d.nimUrl`, and supply the three `hope.a2f3d.nimMtls.*` certificates                        |
| Install reports that the application did not become ready                       | One workload never started; a cold cluster pulls every image first  | The deployer allows 25 minutes and then prints the status and log tail of every pod that is not ready — read that first. `kubectl logs job/<name>-deployer -n <namespace>` has the full output |

Both APIs apply database migrations in an init container before their own
container starts. A first install on a cold cluster therefore takes several
minutes before any API pod reports ready; this is expected.

## Support

For license requests, renewals, and deployment assistance, contact your
CornerstoneX representative.

## License

The contents of this repository (the Helm charts — the HOPE MTP umbrella and
the vendored HOPE Metahuman Service chart — the schema, the deployment guide
and the deployer's build files) are licensed under the
[Apache License 2.0](LICENSE). See [NOTICE](NOTICE) for how this applies
alongside the separate commercial license that governs the applications
themselves.
