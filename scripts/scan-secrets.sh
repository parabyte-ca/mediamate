#!/usr/bin/env bash
# scan-secrets.sh — fail if anything that looks like a credential is in the given
# files or folders (default: the tracked tree). CI runs it on the source and on
# the production frontend bundle.
#
#   scripts/scan-secrets.sh                 scan tracked files (tests excluded)
#   scripts/scan-secrets.sh frontend/dist   scan a build output

set -euo pipefail

PATTERN='AKIA[0-9A-Z]{16}'
PATTERN+='|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{20,}'
PATTERN+='|xox[abprs]-[A-Za-z0-9-]{10,}'
PATTERN+='|sk-[A-Za-z0-9]{20,}|sk_live_[A-Za-z0-9]{16,}'
PATTERN+='|-----BEGIN [A-Z ]*PRIVATE KEY-----'
PATTERN+='|eyJ[A-Za-z0-9_-]{15,}\.eyJ[A-Za-z0-9_-]{15,}\.'
PATTERN+='|(postgres(ql)?|mysql|mongodb(\+srv)?|redis|amqp)://[^ :/"'"'"']+:[^@ "'"'"']+@'
PATTERN+='|[?&](apikey|api_key|passkey|authkey|torrent_pass|X-Plex-Token)=[A-Za-z0-9_-]{16,}'
PATTERN+='|(X-Plex-Token|api_?key|apikey|password|secret)["'"'"']?[[:space:]]*[:=][[:space:]]*["'"'"'][A-Za-z0-9+/_-]{16,}["'"'"']'

hits=0
if [[ $# -eq 0 ]]; then
    # Tracked files only; tests hold deliberate fake secrets.
    if git grep -nIE "$PATTERN" -- . ':!**/tests/**' ':!scripts/scan-secrets.sh' ':!**/package-lock.json'; then
        hits=1
    fi
else
    for target in "$@"; do
        [[ -e "$target" ]] || { echo "scan-secrets: $target not found" >&2; exit 2; }
        if grep -rnIE "$PATTERN" "$target"; then hits=1; fi
        if find "$target" -name '*.map' -print -quit | grep -q .; then
            echo "scan-secrets: source maps found in $target (they expose the original source)" >&2
            find "$target" -name '*.map' >&2
            hits=1
        fi
    done
fi

if [[ $hits -ne 0 ]]; then
    echo "scan-secrets: possible credential found (see above). Move it to server-side settings." >&2
    exit 1
fi
echo "scan-secrets: clean"
