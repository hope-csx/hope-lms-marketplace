{{/*
Shared template helpers for the HOPE MTP Marketplace chart.
*/}}

{{/*
Standard labels applied to every rendered object. The app.kubernetes.io/*
labels let the Application resource (kubernetes-sigs/application) aggregate
component health in the Marketplace UI. The vendored HOPE subchart labels its
objects with the same app.kubernetes.io/instance, so one Application selector
covers both products.
*/}}
{{- define "hope-lms.labels" -}}
app.kubernetes.io/name: {{ .Release.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: hope-lms
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end -}}

{{/*
Google Cloud Marketplace partner consumption tracking label.

Google requires this on the Pod template of every workload the product ships.
It must be at the resource level — a project-level or cluster-level label does
not satisfy the requirement — and each labelled Pod must declare CPU and memory
requests and limits, which Marketplace meters against. See
https://cloud.google.com/marketplace/docs/partners/terraform-kubernetes/partner-consumption-tracking

The value lives in global.podLabels so the vendored HOPE subchart stamps the
same label on its own Pods. Deliberately not a Marketplace-collectible property
in ../schema.yaml: the URN identifies the product listing, not the install, so a
customer must never be prompted for it or able to change it from the deploy UI.
`required` makes an empty value a render failure rather than a silently
untracked install.
*/}}
{{- define "hope-lms.solutionUrn" -}}
{{- required "global.podLabels.goog-partner-solution is required — Marketplace installs must be labelled for partner consumption tracking. Copy the URN from Producer Portal > Overview > 'Consumption tracking label'." (index .Values.global.podLabels "goog-partner-solution") -}}
{{- end -}}

{{- define "hope-lms.consumptionTrackingLabel" -}}
goog-partner-solution: {{ include "hope-lms.solutionUrn" . | quote }}
{{- end -}}

{{/*
In-cluster service DNS suffix for the release namespace. Cross-service URLs
(mtp-api <-> engines <-> qdrant, and the smoke test) are built from this so a
customer can install into any namespace without editing the app config.
*/}}
{{- define "hope-lms.svcDomain" -}}
{{- printf "svc.cluster.local" -}}
{{- end -}}

{{/*
Fail the render early with an actionable message when the license is missing.
Marketplace also validates required schema properties, but this keeps
`helm template` / `mpdev verify` smoke tests honest. The same value reaches the
HOPE subchart through global.license.key.
*/}}
{{- define "hope-lms.requireLicense" -}}
{{- if not .Values.global.license.key -}}
{{- fail "global.license.key is required: paste the signed HOPE MTP license issued to your organization" -}}
{{- end -}}
{{- end -}}

{{/*
Browser-facing WebSocket URL for live sessions. Derived from the api URL when
the customer leaves domains.gatewayWsUrl empty: the deploy guide has them route
/ws on the api host to the mtp-session-gateway Service, which is how the hosted
platform is wired too (infra/k8s/overlays/prod/ingress.yaml).
*/}}
{{- define "hope-lms.gatewayWsUrl" -}}
{{- if .Values.domains.gatewayWsUrl -}}
{{- .Values.domains.gatewayWsUrl -}}
{{- else -}}
{{- printf "wss://%s/ws" (.Values.domains.apiUrl | trimPrefix "https://" | trimSuffix "/") -}}
{{- end -}}
{{- end -}}

{{/*
Public URL of the bundled HOPE api, for the Application info panel and the
post-install tenant-binding step. Falls back to HOPE's JWT issuer, which is
HOPE's public api URL by convention.
*/}}
{{- define "hope-lms.hopeApiUrl" -}}
{{- .Values.domains.hopeApiUrl | default .Values.hope.domains.jwtIssuer -}}
{{- end -}}

{{/*
Workload Identity annotation block for a ServiceAccount; renders nothing when
the customer supplies no GSA (the verification cluster, or a workload that
needs no Google API).
*/}}
{{- define "hope-lms.gsaAnnotation" -}}
{{- if . }}
annotations:
  iam.gke.io/gcp-service-account: {{ . | quote }}
{{- end }}
{{- end -}}
