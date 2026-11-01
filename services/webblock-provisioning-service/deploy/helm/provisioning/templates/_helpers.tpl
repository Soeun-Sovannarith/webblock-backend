{{/* The labels of one component (task PK's shared contract). Argument: dict "root" $ "component" <name>. */}}
{{- define "provisioning.labels" }}
app.kubernetes.io/name: provisioning
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
app.kubernetes.io/part-of: webblock-provisioning
{{- end }}

{{/* The selector of one component's pods. Argument as above. */}}
{{- define "provisioning.selector" }}
app.kubernetes.io/name: provisioning
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{/* PodSecurity restricted, which webblock enforces (deploy/prototype/rbac/00-namespaces.yaml): the pod part. */}}
{{- define "provisioning.podSecurity" }}
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
seccompProfile:
  type: RuntimeDefault
{{- end }}

{{/* ... and the container part. */}}
{{- define "provisioning.containerSecurity" }}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop:
    - ALL
{{- end }}

{{/* Where every pod runs (values nodeSelector and tolerations). Argument: $. */}}
{{- define "provisioning.placement" }}
{{- with .Values.nodeSelector }}
nodeSelector:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.tolerations }}
tolerations:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end }}

{{/* The secret keys of one process, each by name from its Secret; never envFrom on a Secret, so no
stray key reaches the process. Argument: dict "component" <name> "keys" <list>. */}}
{{- define "provisioning.secretEnv" }}
{{- range .keys }}
- name: {{ . }}
  valueFrom:
    secretKeyRef:
      name: provisioning-{{ $.component }}-secret
      key: {{ . }}
{{- end }}
{{- end }}

{{/* The probes on the ops port: startup and liveness on /healthz, readiness on .ready. */}}
{{- define "provisioning.probes" }}
startupProbe:
  httpGet:
    path: /healthz
    port: ops
  periodSeconds: 2
  failureThreshold: 30
readinessProbe:
  httpGet:
    path: {{ .ready }}
    port: ops
  periodSeconds: 5
  failureThreshold: 3
livenessProbe:
  httpGet:
    path: /healthz
    port: ops
  periodSeconds: 10
  failureThreshold: 6
{{- end }}

{{/* /tmp, the only writable path (readOnlyRootFilesystem). */}}
{{- define "provisioning.tmpVolume" }}
- name: tmp
  emptyDir:
    medium: Memory
    sizeLimit: 16Mi
{{- end }}

{{/* The worker's and the bootstrap's projected ServiceAccount token (decision 0016 section 9 ruling 2):
it expires after 600 s and sits beside the cluster's CA; never the default automount. */}}
{{- define "provisioning.kubeVolume" }}
- name: kube
  projected:
    sources:
      - serviceAccountToken:
          path: token
          expirationSeconds: 600
      - configMap:
          name: kube-root-ca.crt
          items:
            - key: ca.crt
              path: ca.crt
{{- end }}

{{/* The stand-in files as one ConfigMap, mounted whole as a directory (never subPath, which never
updates; decision 0016 section 9 ruling 3). */}}
{{- define "provisioning.standinsVolume" }}
- name: standins
  configMap:
    name: provisioning-standins
{{- end }}

{{- define "provisioning.standinsMount" }}
- name: standins
  mountPath: /etc/webblock/standins
  readOnly: true
{{- end }}

{{/* Egress to the cluster's DNS. */}}
{{- define "provisioning.dnsEgress" }}
- toEndpoints:
    - matchLabels:
        k8s:io.kubernetes.pod.namespace: kube-system
        k8s:k8s-app: kube-dns
  toPorts:
    - ports:
        - port: "53"
          protocol: UDP
        - port: "53"
          protocol: TCP
{{- end }}

{{/* Egress to one address outside the cluster on one TCP port; nothing when the address is empty.
Argument: dict "cidr" <address> "port" <number>. */}}
{{- define "provisioning.cidrEgress" }}
{{- if .cidr }}
- toCIDR:
    - {{ .cidr | quote }}
  toPorts:
    - ports:
        - port: {{ .port | quote }}
          protocol: TCP
{{- end }}
{{- end }}
