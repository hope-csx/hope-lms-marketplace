#!/usr/bin/env bash
# Generate throwaway RS256 service keypairs and substitute them into the test
# deployment's schema overlay. Runs once, inside the deployer image build.
#
# Why this exists: the four service-keypair properties are required, and they
# cannot be left empty. The API's secret loader treats an empty environment
# variable as "not supplied" and falls back to GCP Secret Manager, which is
# unreachable from Marketplace's verification cluster and fatal at boot.
# Committing PEM blocks instead would put private keys in git and trip secret
# scanning, so each build mints its own disposable pair.
#
# These keys are only ever used by the automated test deployment, which runs in
# a namespace Google deletes when verification finishes. They are not the keys
# any customer install uses.
#
# Usage: inject-test-keys.sh <path-to-test-schema.yaml>

set -euo pipefail

schema="${1:?usage: inject-test-keys.sh <path-to-test-schema.yaml>}"

if [[ ! -f "${schema}" ]]; then
  echo "inject-test-keys.sh: no such schema file: ${schema}" >&2
  exit 1
fi

tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

# Keypair 1: the API signs internal service tokens, the engines verify them.
# Keypair 2: the agent engine signs callbacks to the API, the API verifies them.
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "${tmp}/svc.key" 2>/dev/null
openssl rsa -in "${tmp}/svc.key" -pubout -out "${tmp}/svc.pub" 2>/dev/null
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "${tmp}/cb.key" 2>/dev/null
openssl rsa -in "${tmp}/cb.key" -pubout -out "${tmp}/cb.pub" 2>/dev/null

SCHEMA="${schema}" TMP="${tmp}" python3 - <<'PY'
import os
import pathlib

schema = pathlib.Path(os.environ['SCHEMA'])
tmp = pathlib.Path(os.environ['TMP'])
text = schema.read_text()

tokens = {
    '__APPTEST_INTERNAL_SVC_PRIVATE_KEY__': 'svc.key',
    '__APPTEST_INTERNAL_SVC_PUBLIC_KEY__': 'svc.pub',
    '__APPTEST_CALLBACK_PUBLIC_KEY__': 'cb.pub',
    '__APPTEST_CALLBACK_PRIVATE_KEY__': 'cb.key',
}

for token, filename in tokens.items():
    if token not in text:
        raise SystemExit(f'inject-test-keys.sh: token {token} missing from {schema}')
    pem = (tmp / filename).read_text().strip()
    # Schema defaults are single-line scalars. The chart's secret templates run
    # every value through Helm's `quote`, and the services normalise the
    # two-character \n sequence back into real newlines before parsing the PEM.
    text = text.replace(token, pem.replace('\n', '\\n'))

schema.write_text(text)
print(f'inject-test-keys.sh: substituted {len(tokens)} throwaway keys into {schema}')
PY
