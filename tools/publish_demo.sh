#!/usr/bin/env bash
# Copies the public site (docs/index.html, docs/demo.html, docs/how-it-works.html, docs/img/) into the separate public repo that
# GitHub Pages serves, and commits it. The jobfinder repo itself stays private; only these files go public.
# Runs automatically on GitHub (.github/workflows/publish-demo.yml) on every push to main that changes the site.
# By hand: tools/publish_demo.sh [demo repo dir]   (default: ~/jobfinder-demo; pull it first), then push it yourself.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
dest="${1:-$HOME/jobfinder-demo}"

[ -d "$dest/.git" ] || { echo "no git repo at $dest" >&2; exit 1; }
# Only placeholder offers go public: at most 15, no LinkedIn, no real links or contact details.
python3 "$root/tools/make_demo_data.py" --check "$root/docs/demo.html" \
    || { echo "docs/demo.html is not safe to publish, nothing copied" >&2; exit 1; }

cp "$root/docs/index.html" "$root/docs/demo.html" "$root/docs/how-it-works.html" "$dest/"
mkdir -p "$dest/img"
cp "$root/docs/img/"*.png "$dest/img/"
touch "$dest/.nojekyll"   # plain static files, skip the Jekyll build

cd "$dest"
git add -A
if git diff --cached --quiet; then
    echo "demo site unchanged"
else
    git commit -q -m "Update demo site"
    echo "committed in $dest; push with: git -C $dest push"
fi
