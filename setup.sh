#!/usr/bin/env bash
# setup.sh — first-time MediaMate install (safe to run again to change settings).
#
# Usage:
#   ./setup.sh                      interactive: asks a few questions
#   ./setup.sh -y --media /mnt/nas  unattended, with defaults for everything else
#
# Options:
#   --port <n>         Web UI port on this host (default 4260)
#   --media <path>     A media folder to give MediaMate (repeat for more). It is
#                      mounted at the same path inside the container, so paths
#                      match what qBittorrent reports when it runs on this host.
#   --tz <zone>        Time zone, e.g. America/Toronto (default: this host's)
#   --gpu | --no-gpu   Use an NVIDIA GPU for encoding (default: detect)
#   --build            Build the image from this checkout instead of pulling it
#   --agent | --no-agent
#                      Install the host helper for in-app upgrades (needs root)
#   -y, --yes          Don't ask; use the options and defaults
#
# Writes .env and docker-compose.override.yml (both kept out of git, and both
# picked up by update.sh), starts the container and waits until it is healthy.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"
ENV_FILE="$REPO_DIR/.env"
OVERRIDE="$REPO_DIR/docker-compose.override.yml"

PORT=""
TZ_OPT=""
GPU=""          # yes | no | "" (detect)
BUILD=false
AGENT=""        # yes | no | "" (ask)
ASSUME_YES=false
MEDIA=()

die()  { echo "ERROR: $*" >&2; exit 1; }
info() { echo "==> $*"; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --port)     PORT="${2:-}"; shift 2 ;;
        --media)    MEDIA+=("${2:-}"); shift 2 ;;
        --tz)       TZ_OPT="${2:-}"; shift 2 ;;
        --gpu)      GPU=yes; shift ;;
        --no-gpu)   GPU=no; shift ;;
        --build)    BUILD=true; shift ;;
        --agent)    AGENT=yes; shift ;;
        --no-agent) AGENT=no; shift ;;
        -y|--yes)   ASSUME_YES=true; shift ;;
        -h|--help)  sed -n '2,22p' "$0"; exit 0 ;;
        *) die "Unknown option: $1 (see ./setup.sh --help)" ;;
    esac
done

INTERACTIVE=false
if [[ "$ASSUME_YES" == false && -t 0 ]]; then INTERACTIVE=true; fi

ask() {  # ask <prompt> <default> → answer on stdout
    local reply=""
    if [[ "$INTERACTIVE" == true ]]; then
        read -r -p "$1 [$2]: " reply || true
    fi
    echo "${reply:-$2}"
}

yes_no() {  # yes_no <prompt> <default y|n> → succeeds on yes
    local d="$2" reply
    reply="$(ask "$1 (y/n)" "$d")"
    [[ "${reply,,}" == y* ]]
}

# Set KEY=VALUE in .env, replacing an existing line (commented or not).
set_env() {
    local key="$1" value="$2" tmp
    tmp="$(mktemp)"
    if [[ -f "$ENV_FILE" ]]; then
        grep -vE "^[#[:space:]]*${key}=" "$ENV_FILE" > "$tmp" || true
    fi
    printf '%s=%s\n' "$key" "$value" >> "$tmp"
    cat "$tmp" > "$ENV_FILE"
    rm -f "$tmp"
}

get_env() {
    [[ -f "$ENV_FILE" ]] || return 0
    sed -n "s/^${1}=//p" "$ENV_FILE" | tail -1
}

echo "=== MediaMate setup ==="

# ── Requirements ───────────────────────────────────────────────────────────────
command -v docker >/dev/null 2>&1 || die "Docker is not installed — see https://docs.docker.com/engine/install/"
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is needed ('docker compose …'). Install the docker-compose-plugin package."
docker info >/dev/null 2>&1 || die "Can't talk to the Docker daemon. Is it running, and are you root or in the 'docker' group?"
command -v curl >/dev/null 2>&1 || die "curl is needed to check the app came up — install it and run setup again"

# ── Questions (defaults come from the last run) ────────────────────────────────
default_tz="$(get_env TZ)"
if [[ -z "$default_tz" ]]; then
    default_tz="$( (timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null) | head -1)"
fi
default_tz="${default_tz:-UTC}"
default_port="$(get_env SCREENMATE_PORT)"
default_port="${default_port:-4260}"

PORT="${PORT:-$(ask "Web UI port" "$default_port")}"
if ! [[ "$PORT" =~ ^[0-9]+$ ]] || (( PORT < 1 || PORT > 65535 )); then
    die "Port must be a number from 1 to 65535"
fi
TZ_VALUE="${TZ_OPT:-$(ask "Time zone" "$default_tz")}"

if [[ ${#MEDIA[@]} -eq 0 ]]; then
    existing="$(get_env MEDIA_PATHS)"
    if [[ "$INTERACTIVE" == true ]]; then
        echo "Media folders MediaMate may scan, encode and split (the folder qBittorrent"
        echo "saves to, and your library). Comma-separated, e.g. /mnt/nas/tv,/mnt/nas/movies"
    fi
    answer="$(ask "Media folders" "${existing:-none}")"
    [[ "$answer" == "none" ]] && answer=""
    IFS=',' read -r -a MEDIA <<< "$answer"
fi
CLEAN_MEDIA=()
for m in "${MEDIA[@]}"; do
    m="${m#"${m%%[![:space:]]*}"}"; m="${m%"${m##*[![:space:]]}"}"; m="${m%/}"
    [[ -z "$m" ]] && continue
    [[ "$m" == /* ]] || die "Media folders must be absolute paths: $m"
    [[ -d "$m" ]] || echo "    Warning: $m doesn't exist on this host (yet)."
    CLEAN_MEDIA+=("$m")
done

if [[ -z "$GPU" ]]; then
    if command -v nvidia-smi >/dev/null 2>&1 && docker info 2>/dev/null | grep -qi nvidia; then
        if yes_no "NVIDIA GPU found — use it for encoding (5–10× faster)?" y; then GPU=yes; else GPU=no; fi
    else
        GPU=no
    fi
fi

# ── Folders ────────────────────────────────────────────────────────────────────
info "Creating ./data (database) and ./upgrade (in-app upgrades)"
mkdir -p data upgrade
chmod 770 upgrade

# ── .env ───────────────────────────────────────────────────────────────────────
if [[ ! -f "$ENV_FILE" && -f "$REPO_DIR/.env.example" ]]; then
    cp "$REPO_DIR/.env.example" "$ENV_FILE"
fi
# Older .env files carried container-only paths copied from .env.example. The container
# never read them (docker-compose.yml sets both), but the host-side upgrade helper did,
# and tried to create /upgrade on the host. Comment them out.
if grep -qE '^(DATABASE_URL|APP_UPGRADE_FOLDER)=' "$ENV_FILE" 2>/dev/null; then
    [[ -f "$ENV_FILE.bak" ]] || cp "$ENV_FILE" "$ENV_FILE.bak"
    sed -i -E 's/^(DATABASE_URL|APP_UPGRADE_FOLDER)=/# &/' "$ENV_FILE"
    info "Commented out container-only paths in .env (the old file is .env.bak)"
fi
media_csv="$(IFS=','; echo "${CLEAN_MEDIA[*]:-}")"
set_env SCREENMATE_PORT "$PORT"
set_env TZ "$TZ_VALUE"
set_env MEDIA_PATHS "$media_csv"
set_env ENCODER_BACKEND "$([[ "$GPU" == yes ]] && echo nvenc || echo cpu)"
info "Wrote .env (port $PORT, time zone $TZ_VALUE)"

# ── docker-compose.override.yml ────────────────────────────────────────────────
if [[ -f "$OVERRIDE" ]] && ! grep -q "Generated by setup.sh" "$OVERRIDE"; then
    cp "$OVERRIDE" "$OVERRIDE.bak"
    info "Your own docker-compose.override.yml was saved as docker-compose.override.yml.bak"
fi
{
    echo "# Generated by setup.sh — run ./setup.sh again to change it."
    echo "# Docker Compose merges this file into docker-compose.yml automatically."
    echo "services:"
    echo "  screenmate:"
    [[ "$BUILD" == true ]] && echo "    build: ."
    if [[ ${#CLEAN_MEDIA[@]} -gt 0 ]]; then
        echo "    volumes:"
        for m in "${CLEAN_MEDIA[@]}"; do echo "      - \"$m:$m\""; done
    fi
    if [[ "$GPU" == yes ]]; then
        echo "    deploy:"
        echo "      resources:"
        echo "        reservations:"
        echo "          devices:"
        echo "            - driver: nvidia"
        echo "              count: 1"
        echo "              capabilities: [gpu, video]"
    fi
    if [[ "$BUILD" == false && ${#CLEAN_MEDIA[@]} -eq 0 && "$GPU" != yes ]]; then
        echo "    {}"
    fi
} > "$OVERRIDE"
info "Wrote docker-compose.override.yml (${#CLEAN_MEDIA[@]} media folder(s), GPU: $GPU)"

# ── Start ──────────────────────────────────────────────────────────────────────
if [[ "$BUILD" == true ]]; then
    info "Building the image (a few minutes the first time)"
    docker compose build
else
    info "Pulling the image"
    docker compose pull
fi
info "Starting MediaMate"
docker compose up -d --remove-orphans

info "Waiting for it to come up"
healthy=false
for _ in $(seq 1 60); do
    if curl -fsS "http://localhost:$PORT/health" >/dev/null 2>&1; then healthy=true; break; fi
    sleep 2
done
if [[ "$healthy" != true ]]; then
    docker compose ps || true
    die "MediaMate didn't answer on port $PORT within 2 minutes — check 'docker logs mediamate'"
fi

# ── Upgrade helper ─────────────────────────────────────────────────────────────
if [[ -z "$AGENT" ]]; then
    if yes_no "Install the host helper so the app can upgrade itself (runs every 15 min as root)?" y; then
        AGENT=yes
    else
        AGENT=no
    fi
fi
if [[ "$AGENT" == yes ]]; then
    if [[ "$(id -u)" -eq 0 ]]; then
        bash "$REPO_DIR/scripts/upgrade-agent.sh" --install || echo "    The helper couldn't be installed — see the message above."
    else
        echo "    In-app upgrades need root to install the helper. Run:"
        echo "      sudo $REPO_DIR/scripts/upgrade-agent.sh --install"
    fi
fi

host_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo ""
echo "=== MediaMate is running ==="
echo "Open: http://${host_ip:-localhost}:$PORT"
echo ""
echo "Next:"
echo "  • The setup wizard connects qBittorrent — or imports your AuTorrent/FileSplitter data."
echo "  • Settings → Auto pipeline: add a rule (e.g. 'Shrink to x265') and, if qBittorrent sees"
echo "    downloads under a different path than MediaMate, a path mapping."
echo "  • ./update.sh installs new versions; ./setup.sh changes these settings."
