#!/usr/bin/env bash

set -euo pipefail

if [ "$#" -ne 0 ]; then
    echo "Usage: $0" >&2
    exit 2
fi

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
STATE_HOME="${XDG_STATE_HOME:-$HOME/.local/state}"
CACHE_HOME="${XDG_CACHE_HOME:-$HOME/.cache}"

for directory in "$HOME" "$DATA_HOME" "$CONFIG_HOME" "$STATE_HOME" "$CACHE_HOME"; do
    if [[ "$directory" != /* ]]; then
        echo "HOME and XDG data, config, state, and cache directories must be absolute paths." >&2
        exit 1
    fi
done

APP_DIR="$DATA_HOME/chronocue"
BIN_DIR="$HOME/.local/bin"
SYSTEMD_DIR="$CONFIG_HOME/systemd/user"
CONFIG_DIR="$CONFIG_HOME/chronocue"
CONFIG_PATH="${CHRONOCUE_CONFIG:-$CONFIG_DIR/config.json}"
LEGACY_CONFIG_PATH="$CONFIG_DIR/schedule.json"
case "$CONFIG_PATH" in
    '~') CONFIG_PATH="$HOME" ;;
    '~/'*) CONFIG_PATH="$HOME/${CONFIG_PATH:2}" ;;
esac
if [[ "$CONFIG_PATH" != /* ]]; then
    CONFIG_PATH="$PWD/$CONFIG_PATH"
fi

echo "Checking installation requirements..."

if ! PYTHON_BIN="$(type -P python3)"; then
    echo "Missing python3. Install Python 3.10 or newer." >&2
    exit 1
fi
if [[ "$PYTHON_BIN" != /* ]]; then
    PYTHON_BIN="$PWD/$PYTHON_BIN"
fi

if ! "$PYTHON_BIN" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
    echo "Python 3.10 or newer is required." >&2
    exit 1
fi

if ! "$PYTHON_BIN" -c 'import tkinter' >/dev/null 2>&1; then
    echo "Missing tkinter. On Ubuntu/Debian: sudo apt install python3-tk" >&2
    exit 1
fi

if ! command -v notify-send >/dev/null 2>&1; then
    echo "Missing notify-send. On Ubuntu/Debian: sudo apt install libnotify-bin" >&2
    exit 1
fi

if ! command -v paplay >/dev/null 2>&1 &&
   ! command -v pw-play >/dev/null 2>&1 &&
   ! command -v aplay >/dev/null 2>&1; then
    echo "Missing audio player. On Ubuntu/Debian: sudo apt install pulseaudio-utils" >&2
    exit 1
fi

if ! command -v systemctl >/dev/null 2>&1 ||
   ! systemctl --user show-environment >/dev/null; then
    echo "Cannot contact the user systemd manager. Run this from your desktop session." >&2
    exit 1
fi

if [ ! -d "$PROJECT_ROOT/src/chronocue" ] ||
   [ ! -f "$PROJECT_ROOT/systemd/chronocue.service" ]; then
    echo "Application source or service file is missing." >&2
    exit 1
fi

echo "Installing ChronoCue..."
mkdir -p "$APP_DIR" "$BIN_DIR" "$SYSTEMD_DIR" "$(dirname "$CONFIG_PATH")"

# Keep schedules from releases that used the pre-rename default filename.
if [ -z "${CHRONOCUE_CONFIG:-}" ] &&
   [ ! -e "$CONFIG_PATH" ] && [ ! -L "$CONFIG_PATH" ] &&
   { [ -e "$LEGACY_CONFIG_PATH" ] || [ -L "$LEGACY_CONFIG_PATH" ]; }; then
    cp -P -- "$LEGACY_CONFIG_PATH" "$CONFIG_PATH"
    echo "Migrated existing schedule to: $CONFIG_PATH"
fi

# Finish copying and generating files before replacing the installed source.
STAGING_DIR="$(mktemp -d "$APP_DIR/.install.XXXXXXXX")"
trap 'rm -rf -- "$STAGING_DIR"' EXIT
cp -R "$PROJECT_ROOT/src" "$STAGING_DIR/src"
cp "$PROJECT_ROOT/systemd/chronocue.service" "$STAGING_DIR/chronocue.service"

for module in daemon ui; do
    {
        printf '#!/usr/bin/env bash\n\n'
        printf 'app_source=%q\n' "$APP_DIR/src"
        printf 'default_config=%q\n' "$CONFIG_PATH"
        printf 'default_state_home=%q\n' "$STATE_HOME"
        printf 'default_cache_home=%q\n' "$CACHE_HOME"
        printf 'python_bin=%q\n' "$PYTHON_BIN"
        printf '%s\n' \
            'export PYTHONPATH="$app_source${PYTHONPATH:+:$PYTHONPATH}"' \
            'export CHRONOCUE_CONFIG="${CHRONOCUE_CONFIG:-$default_config}"' \
            'export XDG_STATE_HOME="${XDG_STATE_HOME:-$default_state_home}"' \
            'export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$default_cache_home}"'
        printf 'exec "$python_bin" -m chronocue.%s "$@"\n' "$module"
    } > "$STAGING_DIR/chronocue-$module"
    chmod +x "$STAGING_DIR/chronocue-$module"
done

rm -rf -- "$APP_DIR/src"
mv "$STAGING_DIR/src" "$APP_DIR/src"
mv "$STAGING_DIR/chronocue-daemon" "$BIN_DIR/chronocue-daemon"
mv "$STAGING_DIR/chronocue-ui" "$BIN_DIR/chronocue-ui"
mv "$STAGING_DIR/chronocue.service" "$SYSTEMD_DIR/chronocue.service"

# A reinstall must never replace an existing schedule (including symlinks).
if [ ! -e "$CONFIG_PATH" ] && [ ! -L "$CONFIG_PATH" ]; then
    cat > "$CONFIG_PATH" <<'EOF'
{
  "settings": {
    "poll_seconds": 5,
    "max_late_seconds": 120,
    "notification_timeout_ms": 0,
    "urgency": "normal",
    "sound_enabled": true,
    "ringtone": "bright-bell",
    "volume": 80
  },
  "schedules": [],
  "presets": [],
  "countdown": {"duration_seconds": 300, "ringtone": null},
  "pomodoro": {
    "focus_minutes": 25,
    "short_break_minutes": 5,
    "long_break_minutes": 15,
    "long_break_every": 4,
    "auto_start_breaks": false,
    "auto_start_focus": false,
    "ringtone": null
  }
}
EOF
fi

systemctl --user daemon-reload
# An absolute unit path also works when the manager has a different XDG environment.
systemctl --user enable "$SYSTEMD_DIR/chronocue.service"
systemctl --user restart chronocue.service
if ! systemctl --user is-active --quiet chronocue.service; then
    echo "ChronoCue did not stay active. Check: journalctl --user -u chronocue" >&2
    exit 1
fi

echo
echo "Installation complete."
echo "Schedule: $CONFIG_PATH"
echo "Open the editor: $BIN_DIR/chronocue-ui"
echo "Check the daemon: systemctl --user status chronocue"
