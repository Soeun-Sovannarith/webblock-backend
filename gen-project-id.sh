#!/usr/bin/env bash
# ==============================================================================
# Generate a canonical lowercase UUID (e.g., c1e23456-789a-4bcd-ef01-23456789abcd)
# Usage:
#   ./gen-project-id.sh        # Generates and prints 1 ID (and copies to clipboard if on macOS)
#   ./gen-project-id.sh 5      # Generates 5 IDs
# ==============================================================================

COUNT="${1:-1}"

generate_uuid() {
    if command -v uuidgen >/dev/null 2>&1; then
        uuidgen | tr '[:upper:]' '[:lower:]'
    elif [ -f /proc/sys/kernel/random/uuid ]; then
        cat /proc/sys/kernel/random/uuid
    elif command -v python3 >/dev/null 2>&1; then
        python3 -c "import uuid; print(uuid.uuid4())"
    else
        od -x /dev/urandom | head -1 | awk '{OFS="-"; print $2$3,$4,$5,$6,$7$8$9}'
    fi
}

for ((i = 1; i <= COUNT; i++)); do
    ID="$(generate_uuid)"
    echo "$ID"
    # If generating a single ID on macOS, copy it to clipboard for convenience
    if [ "$COUNT" -eq 1 ] && command -v pbcopy >/dev/null 2>&1; then
        printf "%s" "$ID" | pbcopy
        echo "✔ Copied to clipboard!" >&2
    fi
done
