#!/usr/bin/env bash
#
# Substitute the verification license into the test-deployment schema overlay.
#
# The license arrives as a BuildKit secret rather than a build argument. A build
# argument is recorded verbatim in the image history, so `docker history` would
# hand the entitlement to anyone able to pull the deployer without them even
# extracting a layer.
#
# The token still ends up inside /data-test/schema.yaml in the finished image,
# which is unavoidable: the deployer has to carry it to install in test mode.
# That is why the verification license is issued short-lived rather than for a
# normal license term — see docs/adr/0029-marketplace-automated-test-deployment.md.
#
# Usage: inject-test-license.sh <schema-path> [secret-path]
#
# FedRAMP: IA-5 (no credential in source or image metadata), SC-28.
set -euo pipefail

schema="${1:?usage: inject-test-license.sh <schema-path> [secret-path]}"
secret="${2:-/run/secrets/apptest_license}"

license=""
if [[ -f "${secret}" ]]; then
  license="$(tr -d '\r\n' <"${secret}")"
fi

if [[ -z "${license}" ]]; then
  echo "WARNING: built without an apptest license. Rendering and customer" >&2
  echo "installs are unaffected, but Marketplace's automated test deployment" >&2
  echo "will abort on the required license.key property." >&2
fi

# Passed through the environment rather than argv so the token does not appear
# in this container's process list during the build.
SCHEMA_PATH="${schema}" LICENSE_KEY="${license}" python3 -c "
import os, pathlib

path = pathlib.Path(os.environ['SCHEMA_PATH'])
path.write_text(path.read_text().replace('__APPTEST_LICENSE_KEY__', os.environ['LICENSE_KEY']))
"
