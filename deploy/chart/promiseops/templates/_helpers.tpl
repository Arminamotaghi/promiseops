{{- define "promiseops.fullname" -}}
{{- default "promiseops" .Values.nameOverride | trunc 50 | trimSuffix "-" -}}
{{- end -}}

{{- define "promiseops.selectorLabels" -}}
app.kubernetes.io/name: {{ include "promiseops.fullname" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "promiseops.labels" -}}
{{ include "promiseops.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{/* Image by immutable digest only. */}}
{{- define "promiseops.image" -}}
{{- $repo := required "image.repository is required" .Values.image.repository -}}
{{- $digest := required "image.digest is required" .Values.image.digest -}}
{{- if not (hasPrefix "sha256:" $digest) -}}
{{- fail "image.digest must look like sha256:<hex>; tags are not allowed" -}}
{{- end -}}
{{- printf "%s@%s" $repo $digest -}}
{{- end -}}

{{- define "promiseops.podSecurityContext" -}}
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
fsGroup: 10001
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{- define "promiseops.containerSecurityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: ["ALL"]
{{- end -}}

{{- define "promiseops.env" -}}
{{- range $name, $value := .Values.env }}
- name: {{ $name }}
  value: {{ $value | quote }}
{{- end }}
- name: PROMISEOPS_IMAGE_DIGEST
  value: {{ .Values.image.digest | quote }}
{{- end -}}

{{- define "promiseops.envFrom" -}}
- secretRef:
    name: {{ required "runtimeSecretName is required (staff-provided secret)" .Values.runtimeSecretName }}
{{- end -}}
