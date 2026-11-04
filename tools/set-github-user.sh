#!/usr/bin/env bash
# Replace the MahbodA placeholder everywhere. Usage: bash tools/set-github-user.sh my-username
set -euo pipefail
[ $# -eq 1 ] && [[ "$1" =~ ^[A-Za-z0-9-]+$ ]] || { echo "Usage: $0 GITHUB_USERNAME"; exit 1; }
cd "$(dirname "$0")/.."
grep -rl 'MahbodA' --exclude-dir=.git --exclude='*.zip' --exclude='*.pdf' . | while read -r f; do
  sed -i.bak "s/MahbodA/$1/g" "$f" && rm -f "$f.bak"
done
echo "Done. Remaining placeholders: $(grep -rl 'MahbodA' --exclude-dir=.git --exclude='*.zip' --exclude='*.pdf' . | wc -l)"
