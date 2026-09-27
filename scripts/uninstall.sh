#!/usr/bin/env bash

set -euo pipefail

PURGE=false
if [ "$#" -eq 1 ] && [ "$1" = "--purge" ]; then
    PURGE=true
elif [ "$#" -ne 0 ]; then
    echo "Usage: $0 [--purge]" >&2
    exit 2
fi

DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
for directory in "$HOME" "$DATA_HOME" "$CONFIG_HOME"; do
    if [[ "$directory" != /* ]]; then
        echo "HOME, XDG_DATA_HOME and XDG_CONFIG_HOME must be absolute paths." >&2
        exit 1
    fi
done

APP_DIR="$DATA_HOME/chronocue"
SYSTEMD_UNIT="$CONFIG_HOME/systemd/user/chronocue.service"
CONFIG_DIR="$CONFIG_HOME/chronocue"

if ! command -v systemctl >/dev/null 2>&1 ||
   ! systemctl --user show-environment >/dev/null; then
    echo "Cannot contact the user systemd manager; no files were removed." >&2
    exit 1
fi

LOAD_STATE="$(systemctl --user show --property=LoadState --value chronocue.service)"
if [ "$LOAD_STATE" != "not-found" ]; then
    if ! systemctl --user stop chronocue.service; then
        echo "Could not stop ChronoCue; no files were removed." >&2
        exit 1
    fi
    systemctl --user disable chronocue.service
fi

rm -f -- "$SYSTEMD_UNIT" \
    "$HOME/.local/bin/chronocue-daemon" \
    "$HOME/.local/bin/chronocue-ui"
rm -rf -- "$APP_DIR"

if "$PURGE"; then
    rm -rf -- "$CONFIG_DIR"
    echo "Configuration removed: $CONFIG_DIR"
    echo "Custom configuration files outside this directory are preserved."
else
    echo "Configuration preserved: $CONFIG_DIR"
fi

if ! systemctl --user daemon-reload; then
    echo "Files removed, but systemd reload failed. Run: systemctl --user daemon-reload" >&2
    exit 1
fi

echo "ChronoCue uninstalled."
