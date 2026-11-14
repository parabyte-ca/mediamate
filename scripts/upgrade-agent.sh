#!/usr/bin/env bash
# upgrade-agent.sh — MediaMate host-side upgrade helper.
# Runs as a cron job on the host (as root).  Writes a heartbeat to agent.json
# so the app can confirm the helper is connected, and processes upgrade
# requests written by the app into the shared upgrade folder.
#
# Usage:
#   sudo bash upgrade-agent.sh [--install | --uninstall | --print]
#
# Setup (setup.sh does all of this):
#   1.  docker-compose.yml mounts ./upgrade (next to this repo's scripts/) into
#       the container as /upgrade. This helper uses that same folder on the host:
#       <repo>/upgrade, or UPGRADE_HOST_FOLDER if you set it.
#       (APP_UPGRADE_FOLDER is the container's path, /upgrade, and is ignored here.)
#   2.  Run  sudo bash upgrade-agent.sh --install
#       This installs a cron entry that fires every 5 minutes.
#   3.  Verify with  sudo bash upgrade-agent.sh --print

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$REPO_DIR/.env"

EVERY_MINUTES=5   # How often cron fires this script (must match cron entry)

# ── Helpers ────────────────────────────────────────────────────────────────────

die()  { echo "ERROR: $*" >&2; exit 1; }
info() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*"; }

# ── Root check ─────────────────────────────────────────────────────────────────

[[ $EUID -eq 0 ]] || die "This script must be run as root (sudo)."

# ── Subcommands ────────────────────────────────────────────────────────────────

_install() {
    local cron_line="*/$EVERY_MINUTES * * * * root bash $SCRIPT_DIR/upgrade-agent.sh >> /var/log/upgrade-agent.log 2>&1"
    local cron_file="/etc/cron.d/screenmate-upgrade-agent"
    printf '%s\n' "$cron_line" > "$cron_file"
    chmod 644 "$cron_file"
    echo "Installed cron job: $cron_file"
    echo "  Schedule: every $EVERY_MINUTES minutes"
    echo "  Log:      /var/log/upgrade-agent.log"
    echo ""
    echo "Make sure the upgrade folder is mounted at the same path in the container."
}

_uninstall() {
    local cron_file="/etc/cron.d/screenmate-upgrade-agent"
    if [[ -f "$cron_file" ]]; then
        rm -f "$cron_file"
        echo "Removed $cron_file"
    else
        echo "No cron job found at $cron_file (nothing to remove)."
    fi
}

_print() {
    local cron_file="/etc/cron.d/screenmate-upgrade-agent"
    if [[ -f "$cron_file" ]]; then
        echo "=== $cron_file ==="
        cat "$cron_file"
    else
        echo "No cron job installed ($cron_file not found)."
    fi
}

if [[ "${1:-}" == "--install" ]];   then _install;   exit 0; fi
if [[ "${1:-}" == "--uninstall" ]]; then _uninstall; exit 0; fi
if [[ "${1:-}" == "--print" ]];     then _print;     exit 0; fi
if [[ -n "${1:-}" ]]; then
    die "Unknown argument: ${1}. Valid flags: --install, --uninstall, --print"
fi

# ── Regular run ────────────────────────────────────────────────────────────────

# Load .env if present (optional — UPGRADE_FOLDER defaults to $REPO_DIR/upgrade).
if [[ -f "$ENV_FILE" ]]; then
    while IFS='=' read -r key value; do
        [[ "$key" =~ ^[[:space:]]*# ]] && continue
        [[ -z "$key" ]] && continue
        key="${key// /}"
        value="${value%%#*}"          # strip inline comments
        value="${value#\"}"           # strip leading "
        value="${value%\"}"           # strip trailing "
        value="${value#\'}"
        value="${value%\'}"
        if [[ -n "$key" ]]; then declare "$key"="$value" 2>/dev/null || true; fi
    done < "$ENV_FILE"
fi

# The host side of the folder the container sees as /upgrade. Never APP_UPGRADE_FOLDER:
# that is the path inside the container (an old .env.example put it in .env), and on
# a read-only host root such as TrueNAS "mkdir /upgrade" fails before any heartbeat.
UPGRADE_FOLDER="${UPGRADE_HOST_FOLDER:-$REPO_DIR/upgrade}"

# Allow extra args to update.sh but ONLY --pull and --skip-preflight.
EXTRA_ARGS=""
if [[ -n "${APP_UPGRADE_ARGS:-}" ]]; then
    for arg in $APP_UPGRADE_ARGS; do
        if [[ "$arg" == "--pull" || "$arg" == "--skip-preflight" ]]; then
            EXTRA_ARGS="$EXTRA_ARGS $arg"
        fi
    done
fi

# Take an exclusive lock on the upgrade folder so only one copy runs at a time.
LOCK_FILE="$UPGRADE_FOLDER/.lock"
mkdir -p "$UPGRADE_FOLDER" 2>/dev/null \
    || die "Cannot create the upgrade folder $UPGRADE_FOLDER. It should be the folder docker-compose mounts as /upgrade (normally $REPO_DIR/upgrade); fix UPGRADE_HOST_FOLDER in .env or create it."
exec 9>"$LOCK_FILE"
BUSY=false
flock -n 9 || BUSY=true

# ── Write heartbeat ────────────────────────────────────────────────────────────
# Every run reports in, even while another run holds the lock (an upgrade in
# progress), so the app shows "busy" instead of "not reporting".

AGENT_FILE="$UPGRADE_FOLDER/agent.json"
AGENT_TMP="$AGENT_FILE.tmp.$$"
printf '{"last_seen":"%s","every_minutes":%d,"busy":%s}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    "$EVERY_MINUTES" "$BUSY" > "$AGENT_TMP"
mv -f "$AGENT_TMP" "$AGENT_FILE"

if [[ "$BUSY" == true ]]; then
    info "Another instance is running — exiting."
    exit 0
fi

# ── Check for an upgrade request ──────────────────────────────────────────────

REQUEST_FILE="$UPGRADE_FOLDER/request.json"
STATUS_FILE="$UPGRADE_FOLDER/status.json"
LOG_FILE="$UPGRADE_FOLDER/upgrade.log"

if [[ ! -f "$REQUEST_FILE" ]]; then
    exit 0   # nothing to do
fi

# Reject symlinks to prevent path-traversal attacks.
if [[ -L "$REQUEST_FILE" ]]; then
    info "request.json is a symlink — ignoring."
    rm -f "$REQUEST_FILE"
    exit 0
fi

# ── Read & validate request fields (no jq dependency) ─────────────────────────

# Use sed to extract JSON string values; safer than sourcing the file.
_json_str() {
    local field="$1" file="$2"
    sed -n "s/.*\"$field\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" "$file" | head -1
}

REQ_ID="$(_json_str id "$REQUEST_FILE")"
REQ_VERSION="$(_json_str version "$REQUEST_FILE")"
REQ_BY="$(_json_str requested_by "$REQUEST_FILE")"

# Consume the request immediately so the helper never double-processes it.
rm -f "$REQUEST_FILE"

# Validate id: 36 characters of hex digits and hyphens (UUID v4 shape).
if ! echo "$REQ_ID" | grep -qE '^[0-9a-f-]{36}$'; then
    info "Invalid request id ($REQ_ID) — ignoring."
    exit 0
fi

# Validate version: strict semver v?X.Y.Z
if ! echo "$REQ_VERSION" | grep -qE '^v?[0-9]+\.[0-9]+\.[0-9]+$'; then
    info "Invalid version ($REQ_VERSION) — ignoring."
    exit 0
fi

# Validate requested_by: printable ASCII chars excluding control chars and shell meta.
if ! echo "$REQ_BY" | grep -qE '^[[:print:]]{1,200}$'; then
    info "Invalid requested_by — ignoring."
    exit 0
fi

# ── Run the upgrade ────────────────────────────────────────────────────────────

info "Processing upgrade request id=$REQ_ID version=$REQ_VERSION by=$REQ_BY"

# Write "running" status.
STATUS_TMP="$STATUS_FILE.tmp.$$"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
printf '{"state":"running","started_at":"%s","id":"%s","version":"%s"}\n' \
    "$STARTED_AT" "$REQ_ID" "$REQ_VERSION" > "$STATUS_TMP"
mv -f "$STATUS_TMP" "$STATUS_FILE"

# Create a fresh log file.
: > "$LOG_FILE"

# Run update.sh from the repo root.
UPDATE_SH="$REPO_DIR/update.sh"
if [[ ! -f "$UPDATE_SH" ]]; then
    info "update.sh not found at $UPDATE_SH"
    printf '{"state":"failed","message":"update.sh not found","finished_at":"%s"}\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$STATUS_TMP"
    mv -f "$STATUS_TMP" "$STATUS_FILE"
    exit 0
fi

# An upgrade that hangs (a stuck pull, a prompt nobody can answer) would hold the
# lock forever; stop it after UPGRADE_TIMEOUT so the helper recovers on its own.
UPGRADE_TIMEOUT="${UPGRADE_TIMEOUT:-7200}"
[[ "$UPGRADE_TIMEOUT" =~ ^[0-9]+$ ]] || UPGRADE_TIMEOUT=7200
TIMEOUT_CMD=()
if command -v timeout &>/dev/null; then TIMEOUT_CMD=(timeout --kill-after=60 "$UPGRADE_TIMEOUT"); fi

set +e
# EXTRA_ARGS holds only allowlisted flags (see above); word splitting is intended.
# shellcheck disable=SC2086
"${TIMEOUT_CMD[@]}" bash "$UPDATE_SH" --version "$REQ_VERSION" $EXTRA_ARGS >> "$LOG_FILE" 2>&1
UPDATE_EXIT=$?
set -e
if [[ $UPDATE_EXIT -eq 124 || $UPDATE_EXIT -eq 137 ]]; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) Stopped: the upgrade ran longer than ${UPGRADE_TIMEOUT}s." >> "$LOG_FILE"
fi

FINISHED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

if [[ $UPDATE_EXIT -eq 0 ]]; then
    info "Upgrade to $REQ_VERSION succeeded."
    printf '{"state":"succeeded","started_at":"%s","finished_at":"%s","id":"%s","version":"%s"}\n' \
        "$STARTED_AT" "$FINISHED_AT" "$REQ_ID" "$REQ_VERSION" > "$STATUS_TMP"
else
    info "Upgrade to $REQ_VERSION FAILED (exit $UPDATE_EXIT)."
    printf '{"state":"failed","message":"update.sh exited %d","started_at":"%s","finished_at":"%s","id":"%s","version":"%s"}\n' \
        "$UPDATE_EXIT" "$STARTED_AT" "$FINISHED_AT" "$REQ_ID" "$REQ_VERSION" > "$STATUS_TMP"
fi

mv -f "$STATUS_TMP" "$STATUS_FILE"
