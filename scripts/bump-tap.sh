#!/bin/sh
# bump-tap.sh: sync gonisulaimann/homebrew-tap/Formula/grounded.rb to a release.
#
# Usage: scripts/bump-tap.sh <version> [--push]
#
# Fetches the sdist URL and sha256 from the PyPI JSON API (no guesswork),
# updates url + sha256 in a fresh tap checkout, prints the diff, and pushes
# only with --push. Never touches credentials; push uses your existing
# git/ssh authentication.
set -eu

VERSION="${1:?usage: scripts/bump-tap.sh <version> [--push]}"
PUSH="${2:-}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT INT TERM

git clone -q https://github.com/gonisulaimann/homebrew-tap "$WORK/tap"
FORMULA="$WORK/tap/Formula/grounded.rb"

META="$(python3 -c "
import json, urllib.request, sys
d = json.load(urllib.request.urlopen('https://pypi.org/pypi/grounded-lint/$VERSION/json', timeout=60))
for u in d['urls']:
    if u['packagetype'] == 'sdist':
        print(u['url']); print(u['digests']['sha256']); break
")"
URL="$(printf '%s\n' "$META" | sed -n '1p')"
SHA="$(printf '%s\n' "$META" | sed -n '2p')"
[ -n "$URL" ] && [ -n "$SHA" ] || { echo "bump-tap: no sdist on PyPI for $VERSION" >&2; exit 1; }

python3 - "$FORMULA" "$URL" "$SHA" <<'EOF'
import re, sys
path, url, sha = sys.argv[1], sys.argv[2], sys.argv[3]
text = open(path).read()
text, n1 = re.subn(r'(?m)^\s*url\s".*"$', f'  url "{url}"', text, count=1)
text, n2 = re.subn(r'(?m)^\s*sha256\s".*"$', f'  sha256 "{sha}"', text, count=1)
if n1 != 1 or n2 != 1:
    sys.exit("bump-tap: formula shape changed, refusing")
open(path, "w").write(text)
EOF

echo "bump-tap: diff for $VERSION"
git -C "$WORK/tap" diff -- Formula/grounded.rb
if [ "$PUSH" = "--push" ]; then
  git -C "$WORK/tap" -c user.name="gonisulaimann" \
    -c user.email="gonisulaimann@users.noreply.github.com" \
    commit -qm "grounded $VERSION" -- Formula/grounded.rb
  git -C "$WORK/tap" push origin main
else
  echo "bump-tap: dry run (no --push given); nothing pushed."
fi
