#!/usr/bin/env bash
# Versioned deploy of the live trader, decoupled from the working tree.
#
#   bash trader/deploy.sh [--skip-tests]
#
# Builds $AI_TRADER_HOME/releases/<UTC-ts>-<shortsha>/ (AI_TRADER_HOME defaults to
# ~/.local/share/ai-trader) from the COMMITTED tree (trader/, robinhood_trader.py, VERSION), atomically
# repoints $AI_TRADER_HOME/current at it, keeps the newest 5 releases (plus the release `current`
# pointed at before, which a running daemon may still be executing) and prints the release path on
# the last line of stdout.
#
# Refuses when the git tree is dirty or the trader tests fail. --skip-tests skips the test gate and
# exists only for tests/test_trader_deploy.py: it is refused unless AI_TRADER_HOME is set to a
# non-default directory, so it can never produce a live release.
# The script never touches systemd: install services/robinhood-trader.service and restart it yourself.
set -euo pipefail

SKIP_TESTS=0
for arg in "$@"; do
    case "$arg" in
        --skip-tests) SKIP_TESTS=1 ;;
        -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
        *) echo "deploy: unknown argument: $arg" >&2; exit 2 ;;
    esac
done

DEFAULT_BASE="${HOME}/.local/share/ai-trader"
BASE="${AI_TRADER_HOME:-$DEFAULT_BASE}"
if [ "$SKIP_TESTS" = 1 ]; then
    if [ -z "${AI_TRADER_HOME:-}" ] || \
       [ "$(realpath -m -- "$BASE")" = "$(realpath -m -- "$DEFAULT_BASE")" ]; then
        echo "deploy: refusing: --skip-tests is test-only and needs AI_TRADER_HOME set to a non-default directory" >&2
        exit 2
    fi
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel)"
cd "$REPO"

if [ -n "$(git status --porcelain --untracked-files=normal)" ]; then
    echo "deploy: refusing: git tree is dirty (commit or stash first):" >&2
    git status --short >&2
    exit 1
fi

if [ "$SKIP_TESTS" = 1 ]; then
    echo "deploy: --skip-tests: skipping the test gate (base ${BASE})" >&2
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

# The release a running daemon may still execute: never pruned by this deploy.
PREVIOUS=""
if [ -L "${BASE}/current" ]; then
    PREVIOUS="$(basename "$(readlink "${BASE}/current")")"
fi

# Atomic repoint: build the new link beside `current`, then rename over it.
ln -sfn "releases/${NAME}" "${BASE}/.current.tmp.$$"
mv -T "${BASE}/.current.tmp.$$" "${BASE}/current"

# Keep the newest 5 releases (names sort by UTC timestamp); never remove the new or the previous one.
mapfile -t ALL < <(find "$RELEASES" -mindepth 1 -maxdepth 1 -type d ! -name '.*' -printf '%f\n' | sort)
if [ "${#ALL[@]}" -gt 5 ]; then
    for old in "${ALL[@]:0:${#ALL[@]}-5}"; do
        [ "$old" = "$NAME" ] && continue
        [ -n "$PREVIOUS" ] && [ "$old" = "$PREVIOUS" ] && continue
        rm -rf -- "${RELEASES:?}/${old}"
    done
fi

echo "deploy: ${NAME} is now current (${BASE}/current)" >&2
echo "$RELEASE"
