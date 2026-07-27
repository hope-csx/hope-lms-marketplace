#!/usr/bin/env bash
#
# Post-deploy smoke test executed by `mpdev verify`. It runs inside the target
# namespace and confirms the core tier is reachable in-cluster. Non-zero exit
# fails verification.
set -euo pipefail

NAMESPACE="${NAMESPACE:-hope-lms}"

echo "[smoke] checking API /health in namespace ${NAMESPACE}"
# The API Service exposes port 80 -> container 3001; /health is the only
# unauthenticated endpoint (FedRAMP IA-2 exception, documented in code).
curl --fail --silent --show-error --max-time 10 \
  "http://api.${NAMESPACE}.svc.cluster.local/health" > /dev/null
echo "[smoke] API health OK"

echo "[smoke] checking web root in namespace ${NAMESPACE}"
curl --fail --silent --show-error --max-time 10 \
  "http://web.${NAMESPACE}.svc.cluster.local/" > /dev/null
echo "[smoke] web root OK"

echo "[smoke] all checks passed"
