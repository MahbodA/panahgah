#!/usr/bin/env bash
# Rebuild offline/panahgah-offline.zip from app/ + libraries downloaded from PyPI.
# Run on a machine WITH internet, ideally Ubuntu 24.04 / Python 3.12 (same as the server).
# Usage: bash tools/build-offline.sh            (optional: PIP_INDEX_URL=<mirror>)
set -euo pipefail
cd "$(dirname "$0")/.."
command -v python3 >/dev/null || { echo "python3 is required"; exit 1; }
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/panahgah"
cp -a app/. "$TMP/panahgah/"
cp docs/DEPLOY.fa.txt "$TMP/panahgah/DEPLOY.txt"
python3 -m pip install --disable-pip-version-check -q --no-compile \
  --target "$TMP/panahgah/libs" -r app/requirements.txt
rm -rf "$TMP/panahgah/libs/bin"
find "$TMP/panahgah" -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
python3 - "$TMP" <<'PY'
import os, sys, zipfile
root = sys.argv[1]
out = os.path.abspath("offline/panahgah-offline.zip")
if os.path.exists(out):
    os.remove(out)
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    for d, _, files in os.walk(os.path.join(root, "panahgah")):
        for f in sorted(files):
            p = os.path.join(d, f)
            z.write(p, os.path.relpath(p, root))
print("Built", out, os.path.getsize(out) // 1024, "KB")
PY
