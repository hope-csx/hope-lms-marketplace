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

{{/*
Scale-up behaviour shared by every HorizontalPodAutoscaler in this chart.

Node and Python startup pegs the 100m CPU request for a minute or two. An
unstabilised HPA reads that as sustained load at several hundred percent of
target and answers by scaling straight to maxReplicas, before a single request
has been served. Stabilising scale-up makes the controller act on the LOWEST
recommendation across the window, so a boot spike is ignored while real
sustained load still scales within five minutes. Scale-down keeps the
controller default, which is already conservative.
*/}}
{{- define "hope-metahuman-service.hpaBehavior" -}}
behavior:
  scaleUp:
    stabilizationWindowSeconds: 300
    # And cap the rate. Stabilisation alone does not help when startup is slow
    # rather than spiky: the first replica to report ready is still finishing
    # its own boot, so the controller sees one hot pod and jumps to
    # maxReplicas. One pod a minute keeps a rollout from multiplying its own
    # startup work — every api replica runs the schema migration.
    policies:
      - type: Pods
        value: 1
        periodSeconds: 60
{{- end -}}
