#!/usr/bin/env bash
# Replace the YOUR_GITHUB_USER placeholder everywhere. Usage: bash tools/set-github-user.sh my-username
set -euo pipefail
[ $# -eq 1 ] && [[ "$1" =~ ^[A-Za-z0-9-]+$ ]] || { echo "Usage: $0 GITHUB_USERNAME"; exit 1; }
cd "$(dirname "$0")/.."
grep -rl 'YOUR_GITHUB_USER' --exclude-dir=.git --exclude='*.zip' --exclude='*.pdf' . | while read -r f; do
  sed -i.bak "s/YOUR_GITHUB_USER/$1/g" "$f" && rm -f "$f.bak"
done
echo "Done. Remaining placeholders: $(grep -rl 'YOUR_GITHUB_USER' --exclude-dir=.git --exclude='*.zip' --exclude='*.pdf' . | wc -l)"
