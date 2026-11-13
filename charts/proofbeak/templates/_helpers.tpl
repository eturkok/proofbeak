{{- define "proofbeak.name" -}}
{{- printf "%s-proofbeak" .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- define "proofbeak.labels" -}}
app.kubernetes.io/name: proofbeak
app.kubernetes.io/instance: {{ .Release.Name | quote }}
app.kubernetes.io/component: server
{{- end -}}
{{- define "proofbeak.image" -}}
{{- if .Values.image.digest -}}
{{ .Values.image.repository }}@{{ .Values.image.digest }}
{{- else -}}
{{ .Values.image.repository }}:{{ .Values.image.tag }}
{{- end -}}
{{- end -}}
