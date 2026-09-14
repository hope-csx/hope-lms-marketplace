{{/*
Shared template helpers for the HOPE Metahuman Service Marketplace chart.
*/}}

{{/*
Standard labels applied to every rendered object. The app.kubernetes.io/*
labels let the Application resource (kubernetes-sigs/application) aggregate
component health in the Marketplace UI.
*/}}
{{- define "hope-metahuman-service.labels" -}}
app.kubernetes.io/name: {{ .Release.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: hope-metahuman-service
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end -}}

{{/*
In-cluster service DNS suffix for the release namespace. Cross-service URLs
(api <-> agent-engine <-> a2f3d-engine) are built from this so a customer can
install into any namespace without editing the app config.
*/}}
{{- define "hope-metahuman-service.svcDomain" -}}
{{- printf "svc.cluster.local" -}}
{{- end -}}

{{/*
Fail the render early with an actionable message when a required value is
missing. Marketplace also validates required schema properties, but this keeps
`helm template` / `mpdev verify` smoke tests honest.
*/}}
{{- define "hope-metahuman-service.requireLicense" -}}
{{- if not (include "hope-metahuman-service.licenseKey" .) -}}
{{- fail "license.key is required: paste the signed HOPE Metahuman Service license issued to your organization" -}}
{{- end -}}
{{- end -}}

{{/*
Values an umbrella chart may supply once for every bundled product through
Helm's `global` map. Each helper prefers the global value and falls back to
this chart's own, so the chart renders identically standalone. `dig` tolerates
a missing `global` subtree entirely.
*/}}
{{- define "hope-metahuman-service.licenseKey" -}}
{{- dig "license" "key" "" (.Values.global | default dict) | default .Values.license.key -}}
{{- end -}}

{{- define "hope-metahuman-service.projectId" -}}
{{- dig "gcp" "projectId" "" (.Values.global | default dict) | default .Values.gcp.projectId -}}
{{- end -}}

{{- define "hope-metahuman-service.deployEnv" -}}
{{- dig "gcp" "deployEnv" "" (.Values.global | default dict) | default .Values.gcp.deployEnv -}}
{{- end -}}

{{- define "hope-metahuman-service.redisUrl" -}}
{{- dig "secrets" "redisUrl" "" (.Values.global | default dict) | default .Values.secrets.redisUrl -}}
{{- end -}}

{{/*
Additional Pod-template labels supplied by an umbrella chart (for example the
Google Cloud Marketplace consumption-tracking label). Renders nothing when
unset. Pod labels only — the Deployment selector is deliberately left alone,
because a selector is immutable once created.
*/}}
{{- define "hope-metahuman-service.podLabels" -}}
{{- with (dig "podLabels" dict (.Values.global | default dict)) -}}
{{- toYaml . -}}
{{- end -}}
{{- end -}}
