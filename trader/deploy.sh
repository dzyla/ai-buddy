#!/usr/bin/env bash
# Versioned deploy of the live trader, decoupled from the working tree.
#
#   bash trader/deploy.sh [--ref <commit>] [--skip-tests]
#
# Builds $AI_TRADER_HOME/releases/<UTC-ts>-<shortsha>/ (AI_TRADER_HOME defaults to
# ~/.local/share/ai-trader) from a COMMITTED tree (trader/, robinhood_trader.py, VERSION), atomically
# repoints $AI_TRADER_HOME/current at it, keeps the newest 5 releases (plus the release `current`
# pointed at before, which a running daemon may still be executing) and prints the release path on
# the last line of stdout.
#
# Default: deploys HEAD and refuses when the git tree is dirty. --ref <commit>: deploys that commit
# via `git archive`, whatever the state of the working tree (a dirty worktree never blocks it).
# Either way the test gate runs ALL trader tests against a temporary checkout of the deployed
# commit (never the working tree), and the deploy is refused when they fail. --skip-tests skips
# the gate and exists only for tests/test_trader_deploy.py: it is refused unless AI_TRADER_HOME is
# set to a non-default directory, so it can never produce a live release.
# The script never touches systemd: install services/robinhood-trader.service and restart it yourself.
set -euo pipefail

SKIP_TESTS=0
REF=""
while [ $# -gt 0 ]; do
    case "$1" in
        --skip-tests) SKIP_TESTS=1 ;;
        --ref) [ $# -ge 2 ] || { echo "deploy: --ref needs a commit" >&2; exit 2; }; REF="$2"; shift ;;
        --ref=*) REF="${1#--ref=}" ;;
        -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
        *) echo "deploy: unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
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

if [ -z "$REF" ]; then
    if [ -n "$(git status --porcelain --untracked-files=normal)" ]; then
        echo "deploy: refusing: git tree is dirty (commit or stash first, or deploy a commit with --ref):" >&2
        git status --short >&2
        exit 1
    fi
    REF="HEAD"
fi
if ! FULL_SHA="$(git rev-parse --verify --quiet "${REF}^{commit}")"; then
    echo "deploy: refusing: not a commit: ${REF}" >&2
    exit 2
fi
SHA="$(git rev-parse --short "$FULL_SHA")"

STAGE=""
GATE_DIR=""
cleanup() { [ -n "$STAGE" ] && rm -rf "$STAGE"; [ -n "$GATE_DIR" ] && rm -rf "$GATE_DIR"; return 0; }
trap cleanup EXIT

# Every trader test file. Inside a gate run (tests/test_trader_deploy.py deploys from within the
# gate), the deploy tests themselves are left out so the gate cannot recurse.
TRADER_TESTS=(tests/test_robinhood_trader.py tests/test_momentum_strategy.py tests/test_trader_pulses.py
              tests/test_trader_bear_park.py tests/test_trader_shadow.py tests/test_trader_summary.py tests/test_trader_package.py
              tests/test_trader_daemon_hooks.py tests/test_trader_deploy.py)
if [ -n "${AI_TRADER_DEPLOY_GATE:-}" ]; then
    TRADER_TESTS=("${TRADER_TESTS[@]/tests\/test_trader_deploy.py/}")
fi

if [ "$SKIP_TESTS" = 1 ]; then
    echo "deploy: --skip-tests: skipping the test gate (base ${BASE})" >&2
else
    # Run the gate against a temporary checkout of exactly the commit being deployed (a --shared
    # clone, so any commit of this repo resolves), never against the working tree.
    GATE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ai-trader-gate-XXXXXX")"
    git clone -q --shared --no-checkout "$REPO" "${GATE_DIR}/src"
    git -C "${GATE_DIR}/src" -c advice.detachedHead=false checkout -q --detach "$FULL_SHA"
    tests=()
    for t in "${TRADER_TESTS[@]}"; do
        [ -n "$t" ] && [ -f "${GATE_DIR}/src/${t}" ] && tests+=("$t")
    done
    echo "deploy: running trader tests at ${SHA} (${#tests[@]} files)..." >&2
    if ! (cd "${GATE_DIR}/src" && AI_TRADER_DEPLOY_GATE=1 python3 -m pytest "${tests[@]}" -q -p no:cacheprovider >&2); then
        echo "deploy: refusing: trader tests failed" >&2
        exit 1
    fi
fi
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
git archive --format=tar "$FULL_SHA" trader robinhood_trader.py | tar -x -C "$STAGE"
printf '%s\n' "$SHA" "commit=${FULL_SHA}" "built=${TS}" > "${STAGE}/VERSION"
chmod +x "${STAGE}/robinhood_trader.py"
mv "$STAGE" "$RELEASE"
STAGE=""

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
