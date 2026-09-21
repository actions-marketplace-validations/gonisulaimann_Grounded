#!/bin/sh
# The 30-second firewall demo: an agent renames a function in one file.
# The stale import lives on in another file the agent never touched --
# and `grounded scan . --changed` still screams before anything is
# committed, because the diff touched the symbol the lie names.
# Self-contained (no network), asserts its own outcome. Usage:
#   ./demo/firewall.sh                  # uses `grounded` on PATH
#   GROUNDED="python3 -m grounded.cli" ./demo/firewall.sh  # dev checkout
set -u
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="${REPO_DIR}/src:${PYTHONPATH:-}"
GROUNDED="${GROUNDED:-grounded}"
START=$(date +%s)
D=$(mktemp -d)
trap 'rm -rf "$D"' EXIT INT TERM
cd "$D" || exit 1

run() {
    # shellcheck disable=SC2086
    eval "$GROUNDED $*"
}

git init -q .
git config user.email demo@grounded
git config user.name demo
mkdir pkg
printf 'def get_user(uid):\n    return uid\n' > pkg/core.py
printf '"""Views."""\nfrom .core import get_user\n\n\ndef show(uid):\n    return get_user(uid)\n' > pkg/views.py
touch pkg/__init__.py
git add -A && git commit -qm baseline

echo "--- 1. clean tree: smoke detector silent"
run "scan . --no-color --quiet"

echo "--- 2. agent renames get_user -> get_account in core.py only"
printf 'def get_account(uid):\n    return uid\n' > pkg/core.py
git diff --stat

echo "--- 3. firewall on the diff: views.py untouched, lie caught anyway"
OUT=$(run "scan . --changed --no-color" 2>&1)
RC=$?
echo "$OUT"
if printf '%s' "$OUT" | grep -qF "[stale-import]" && printf '%s' "$OUT" | grep -qF "get_user"; then
    :
else
    echo "DEMO FAIL: expected the stale get_user import (rc=$RC)"
    exit 1
fi

echo "--- 4. blast radius before renaming for real"
run "impact get_user ." | head -8

ELAPSED=$(($(date +%s) - START))
echo "DEMO PASS in ${ELAPSED}s: rename fallout caught pre-commit"
