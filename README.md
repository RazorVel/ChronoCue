# ChronoCue

A small Linux desktop reminder daemon with a graphical schedule editor. Built
for lightweight desktop environments such as i3, using Python's standard
library, Tk, `notify-send`, and a user-level systemd service.

Schedules live outside the application, normally at
`~/.config/chronocue/schedule.json`. The daemon reloads valid edits automatically.

## Requirements

- Linux with Python 3.10 or newer and a running systemd user session.
- Tk for the editor and `notify-send` for notifications.
- A desktop notification daemon, such as dunst on i3.

On Ubuntu/Debian:

```bash
sudo apt install python3 python3-tk libnotify-bin
```

If your desktop does not already provide notifications:

```bash
sudo apt install dunst
```

Check desktop notification delivery before installing:

```bash
notify-send "Test" "Notifications work"
```

## Install or update

From this repository's root, in your desktop session:

```bash
./scripts/install.sh
```

The installer checks dependencies and user-systemd access, copies the
application, and enables and restarts `chronocue.service`. Run it again after
updating the source to install the new version. Existing schedules are preserved.
Do not run it with `sudo`.

Default installed locations:

```text
~/.local/bin/chronocue-daemon
~/.local/bin/chronocue-ui
~/.local/share/chronocue/
~/.config/chronocue/schedule.json
~/.config/systemd/user/chronocue.service
```

`XDG_DATA_HOME` and `XDG_CONFIG_HOME` are supported; when set for installation,
they must be absolute paths. Both launchers remember the configuration path
chosen during installation. Add `~/.local/bin` to your `PATH`, or start the
editor with its full path:

```bash
~/.local/bin/chronocue-ui
```

The editor can add, update, disable, delete, and test reminders. Saves are
atomic. If another editor or process changes the file, a stale save is rejected
with instructions to reload. Opening the editor does not rewrite an existing
configuration.

## Configuration

The first launch creates an empty configuration if the file is absent:

```json
{
  "settings": {
    "poll_seconds": 5,
    "max_late_seconds": 120,
    "notification_timeout_ms": 10000,
    "urgency": "normal"
  },
  "schedules": []
}
```

Add entries to `schedules`, for example:

```json
{
  "id": "lunch",
  "time": "13:00",
  "title": "Lunch / Rest",
  "message": "Proper Break",
  "days": ["mon", "tue", "wed", "thu", "fri"],
  "enabled": true
}
```

See [examples/schedule.example.json](examples/schedule.example.json) for a full
configuration.

| Field | Accepted values |
| --- | --- |
| `poll_seconds` | Number from 1 to 86400; default 5 |
| `max_late_seconds` | Integer from 0 to 86400; default 120 |
| `notification_timeout_ms` | Integer from -1 to 2147483647; -1 uses the notification server's default, 0 requests no expiry |
| `urgency` | `low`, `normal`, or `critical` |
| `id` | Unique, nonempty string; keep it stable when editing a reminder |
| `time` | Strict 24-hour `HH:MM`, such as `09:05` |
| `title` | Nonempty string; defaults to `Scheduled Alert` |
| `message` | String; defaults to empty |
| `days` | List of `mon` through `sun`; case and surrounding spaces are normalized |
| `enabled` | JSON `true` or `false`; defaults to `true` |

For compatibility, omitted, `null`, or empty `days` means every day. Use
`"enabled": false` to disable a reminder. Entries without IDs receive stable
generated IDs in memory; the editor persists them when you save. Explicit IDs
are recommended for schedules edited by hand. Unrecognized fields are preserved
by the editor.

Check a configuration without starting the daemon or sending a notification:

```bash
chronocue-daemon --check
chronocue-daemon --config ~/my-schedule.json --check
```

If an edit contains invalid JSON or invalid values, the running daemon keeps its
last valid configuration and logs the error. At startup, an invalid file is
retried until corrected. If a file disappears after loading, the daemon keeps
using the last valid schedule until a replacement appears.

### Timing and delivery

Times use the computer's local clock. A reminder is eligible from its scheduled
time through `max_late_seconds` afterward, inclusive. This grace period also
works across midnight and uses the scheduled day's weekday. For example, a
Sunday `23:59` reminder can still arrive on Monday at `00:00:30` with a 120-second
grace period. Older missed reminders are skipped.

Keep `poll_seconds` shorter than the grace period. Setting the grace period to
zero requires an exact clock match and will usually miss reminders. A failed
notification is retried on later polls while the reminder is still eligible.
Notification commands time out after 10 seconds so a stuck notification server
cannot block the daemon indefinitely. The desktop notification server controls
whether and how expiry is honored.

Successful deliveries are remembered across reloads and ordinary restarts in
`$XDG_STATE_HOME/chronocue/`, defaulting to `~/.local/state/chronocue/`. Each
configuration path has separate history and a lock that prevents a second
daemon from running for that path. Editing a reminder's title does not repeat
an already delivered occurrence when its ID and time are unchanged.

Delivery is not an exactly-once guarantee: a crash between sending and saving
history, a history-write failure, or deleting the history can cause a repeat.
Clock and daylight-saving changes follow local wall time: skipped times are
subject to the grace period, and a repeated local time shares the same daily
occurrence. ChronoCue does not wake the computer from suspend.

### Custom configuration location

The daemon and editor both accept an explicit path:

```bash
chronocue-daemon --config ~/my-schedule.json
chronocue-ui --config ~/my-schedule.json
```

For an installed service and editor to share a custom default, install with:

```bash
CHRONOCUE_CONFIG="$HOME/my-schedule.json" ./scripts/install.sh
```

Precedence is `--config`, then `CHRONOCUE_CONFIG` in the launcher's environment,
then its remembered installation default. When running directly from source or
a Python package installation, the default is
`$XDG_CONFIG_HOME/chronocue/schedule.json`, falling back to `~/.config` when that
variable is empty or relative. Exporting a variable in a terminal does not
change an already-running service's environment.

## Service management

```bash
systemctl --user status chronocue
systemctl --user restart chronocue
systemctl --user stop chronocue
systemctl --user start chronocue
journalctl --user -u chronocue -f
```

If notifications work in a terminal but not from the service, export your
graphical session environment to the user systemd instance. For i3, add:

```text
exec_always --no-startup-id dbus-update-activation-environment --systemd DISPLAY XAUTHORITY DBUS_SESSION_BUS_ADDRESS
exec_always --no-startup-id systemctl --user import-environment DISPLAY XAUTHORITY DBUS_SESSION_BUS_ADDRESS
```

Then reload i3 or log out and back in. If dunst is your notification daemon,
start it from i3 with `exec --no-startup-id dunst`; do not start a second
notification daemon alongside one already provided by your desktop.

## Development

Run without installing a service:

```bash
PYTHONPATH=src python3 -m chronocue.daemon --config /tmp/chronocue-dev.json --check
PYTHONPATH=src python3 -m chronocue.ui --config /tmp/chronocue-dev.json
PYTHONPATH=src python3 -m chronocue.daemon --config /tmp/chronocue-dev.json
```

Run the test suite:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest discover -s tests -v
bash -n scripts/install.sh scripts/uninstall.sh
```

Tests use temporary configurations, delivery history, and isolated installation
homes; notification and service commands are replaced with test doubles. No
personal schedules or desktop services are changed. Editor behavior is tested
without displaying a window; Python/Tk must still be installed.

The package also provides `chronocue-daemon` and `chronocue-ui` entry points when
installed with `pip` in a virtual environment. This installs the Python package
only; `scripts/install.sh` manages the desktop service independently.

## Uninstall

Run with the same `XDG_DATA_HOME` and `XDG_CONFIG_HOME` used for installation:

```bash
./scripts/uninstall.sh
```

This stops the service and removes the installed application while retaining
schedules. To remove the default configuration directory as well:

```bash
./scripts/uninstall.sh --purge
```

Custom configuration files outside the ChronoCue configuration directory and
delivery history are preserved. If the user manager cannot be reached or the
service cannot be stopped, uninstall exits before removing application files.

## License

MIT. See [LICENSE](LICENSE).
