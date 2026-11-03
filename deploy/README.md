Implement a Helm chart and CI pipeline. Staff provides runtime service account, registry-pull and runtime-config secrets, ingress, policies and namespace quotas. Your workloads need probes, bounded requests/limits, two production replicas, max-three HPA, an intentional batch CronJob, image digests and rollback. Use ClusterIP services; do not create namespace, RBAC, policies, ingress, hostPath, NodePort or privileged workloads.

The production namespace has a 3 CPU / 3 GiB budget. A 750m CPU / 768Mi API limit
allows three HPA replicas plus one rolling-update surge pod within that budget.
Keep two available replicas during model promotion. Drain requests before process
shutdown (10-second preStop and 45-second termination grace). Separate readiness
from liveness, and never send traffic to a model that failed validation.

A 750m CPU / 768Mi scoring job fits alongside three steady API replicas. At that
scale, wait for an active scoring job to finish before a manual rollout so its
surge pod has room. Keep scoring jobs bounded, prevent overlapping CronJob runs,
and keep expensive training in the admitted training queue.
