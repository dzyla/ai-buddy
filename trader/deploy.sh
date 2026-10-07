#!/usr/bin/env bash
# Versioned deploy of the live trader, decoupled from the working tree.
#
#   bash trader/deploy.sh [--dry-run]
#
# Builds ~/.local/share/ai-trader/releases/<UTC-ts>-<shortsha>/ from the COMMITTED tree (trader/,
# robinhood_trader.py, VERSION), atomically repoints ~/.local/share/ai-trader/current at it, keeps the
# newest 5 releases and prints the release path on the last line of stdout.
#
# Refuses when the git tree is dirty or the trader tests fail. --dry-run skips the test gate (used by
# tests/test_trader_deploy.py to exercise the release mechanics quickly); never use it for a live deploy.
# The script never touches systemd: install services/robinhood-trader.service and restart it yourself.
set -euo pipefail

DRY_RUN=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
        *) echo "deploy: unknown argument: $arg" >&2; exit 2 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel)"
cd "$REPO"

if [ -n "$(git status --porcelain --untracked-files=normal)" ]; then
    echo "deploy: refusing: git tree is dirty (commit or stash first):" >&2
    git status --short >&2
    exit 1
fi

if [ "$DRY_RUN" = 1 ]; then
    echo "deploy: --dry-run: skipping the test gate" >&2
else
    echo "deploy: running trader tests..." >&2
    if ! python3 -m pytest tests/test_robinhood_trader.py tests/test_momentum_strategy.py \
            tests/test_trader_pulses.py -q -p no:cacheprovider >&2; then
        echo "deploy: refusing: trader tests failed" >&2
        exit 1
    fi
fi

SHA="$(git rev-parse --short HEAD)"
FULL_SHA="$(git rev-parse HEAD)"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
BASE="${HOME}/.local/share/ai-trader"
RELEASES="${BASE}/releases"
mkdir -p "$RELEASES"

NAME="${TS}-${SHA}"
n=1
while [ -e "${RELEASES}/${NAME}" ]; do
    n=$((n + 1))
    NAME="${TS}-${n}-${SHA}"
done
RELEASE="${RELEASES}/${NAME}"

# Stage from the committed tree (git archive: no untracked files, no __pycache__), then rename into place.
STAGE="$(mktemp -d "${RELEASES}/.staging-XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT
git archive --format=tar HEAD trader robinhood_trader.py | tar -x -C "$STAGE"
printf '%s\n' "$SHA" "commit=${FULL_SHA}" "built=${TS}" > "${STAGE}/VERSION"
chmod +x "${STAGE}/robinhood_trader.py"
mv "$STAGE" "$RELEASE"
trap - EXIT

# Atomic repoint: build the new link beside `current`, then rename over it.
ln -sfn "releases/${NAME}" "${BASE}/.current.tmp.$$"
mv -T "${BASE}/.current.tmp.$$" "${BASE}/current"

# Keep the newest 5 releases (names sort by UTC timestamp); never remove the active one.
mapfile -t ALL < <(find "$RELEASES" -mindepth 1 -maxdepth 1 -type d ! -name '.*' -printf '%f\n' | sort)
if [ "${#ALL[@]}" -gt 5 ]; then
    for old in "${ALL[@]:0:${#ALL[@]}-5}"; do
        [ "$old" = "$NAME" ] && continue
        rm -rf -- "${RELEASES:?}/${old}"
    done
fi

echo "deploy: ${NAME} is now current (${BASE}/current)" >&2
echo "$RELEASE"
