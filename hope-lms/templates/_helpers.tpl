{{/*
Shared template helpers for the HOPE LMS Marketplace chart.
*/}}

{{/*
Standard labels applied to every rendered object. The app.kubernetes.io/*
labels let the Application resource (kubernetes-sigs/application) aggregate
component health in the Marketplace UI.
*/}}
{{- define "hope-lms.labels" -}}
app.kubernetes.io/name: {{ .Release.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: hope-lms
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end -}}

{{/*
In-cluster service DNS suffix for the release namespace. Cross-service URLs
(api <-> agent-engine <-> a2f3d-engine) are built from this so a customer can
install into any namespace without editing the app config.
*/}}
{{- define "hope-lms.svcDomain" -}}
{{- printf "svc.cluster.local" -}}
{{- end -}}

{{/*
Fail the render early with an actionable message when a required value is
missing. Marketplace also validates required schema properties, but this keeps
`helm template` / `mpdev verify` smoke tests honest.
*/}}
{{- define "hope-lms.requireLicense" -}}
{{- if not .Values.license.key -}}
{{- fail "license.key is required: paste the signed HOPE LMS license issued to your organization" -}}
{{- end -}}
{{- end -}}
