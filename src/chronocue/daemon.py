import argparse
import fcntl
import hashlib
import json
import logging
import os
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

from .config import DAY_NAMES, load_config, parse_clock, resolve_config_path
from .notifier import send_notification


def due_occurrence(entry, now: datetime, max_late_seconds: int):
    """Return the scheduled date/time, including yesterday's grace window."""
    if not entry.get("enabled", True):
        return None

    days = {str(day).strip().lower() for day in entry.get("days") or DAY_NAMES}
    hour, minute = parse_clock(entry["time"])
    scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    # The maximum configured grace period is one day. Check the occurrence's
    # weekday, since a reminder shortly before midnight may arrive tomorrow.
    for offset in (0, 1):
        candidate = scheduled - timedelta(days=offset)
        delta = (now - candidate).total_seconds()
        if DAY_NAMES[candidate.weekday()] in days and 0 <= delta <= max_late_seconds:
            return candidate
    return None


def entry_is_due(entry, now: datetime, max_late_seconds: int) -> bool:
    return due_occurrence(entry, now, max_late_seconds) is not None


def delivery_state_path(config_path: Path) -> Path:
    state_home = os.environ.get("XDG_STATE_HOME", "")
    state_root = Path(state_home) if state_home else Path.home() / ".local" / "state"
    if not state_root.is_absolute():
        state_root = Path.home() / ".local" / "state"
    config_id = hashlib.sha256(str(config_path.resolve()).encode("utf-8")).hexdigest()
    return state_root / "chronocue" / f"{config_id}.json"


class DeliveryHistory:
    """Keep successful deliveries across config reloads and daemon restarts."""

    def __init__(self, path: Path):
        self.path = path
        self.sent = set()
        try:
            with path.open(encoding="utf-8") as file:
                data = json.load(file)
            rows = data["sent"]
            if not isinstance(rows, list):
                raise ValueError("Delivery history must contain a list")
            for row in rows:
                if not isinstance(row, list) or len(row) != 3 or not all(
                    isinstance(value, str) for value in row
                ):
                    raise ValueError("Invalid delivery history entry")
                datetime.strptime(row[0], "%Y-%m-%d")
                parse_clock(row[2])
            self.sent = {tuple(row) for row in rows}
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError):
            logging.exception("Could not read delivery history: %s", path)

    def prune(self, now: datetime):
        dates = {
            now.date().isoformat(),
            (now.date() - timedelta(days=1)).isoformat(),
        }
        self.sent = {key for key in self.sent if key[0] in dates}

    def record(self, occurrence):
        self.sent.add(occurrence)
        temp_path = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent,
                prefix=f".{self.path.name}.", suffix=".tmp", delete=False,
            ) as file:
                temp_path = Path(file.name)
                json.dump({"sent": sorted(self.sent)}, file)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_path, self.path)
        except OSError:
            logging.exception(
                "Could not save delivery history; restart may repeat reminders: %s",
                self.path,
            )
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    logging.warning("Could not remove temporary history file: %s", temp_path)


def deliver_due(config, now: datetime, history: DeliveryHistory):
    settings = config["settings"]
    history.prune(now)
    for index, entry in enumerate(config["schedules"]):
        try:
            scheduled = due_occurrence(entry, now, settings["max_late_seconds"])
            if scheduled is None:
                continue
            entry_id = str(entry.get("id", f"{index}:{entry.get('time')}"))
            occurrence = (scheduled.date().isoformat(), entry_id, entry["time"])
            if occurrence in history.sent:
                continue

            title = entry.get("title", "Scheduled Alert")
            if send_notification(
                title,
                entry.get("message", ""),
                settings["notification_timeout_ms"],
                settings["urgency"],
            ):
                history.record(occurrence)
                logging.info("Notification sent: %s", title)
            else:
                logging.error("notify-send failed for: %s", title)
        except Exception:
            logging.exception("Could not process schedule entry: %r", entry)


def run(config_path):
    config_path = Path(config_path)
    logging.info("Using config: %s", config_path)
    state_path = delivery_state_path(config_path)
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        with state_path.with_suffix(".lock").open("a", encoding="utf-8") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                logging.error("A daemon is already running for this configuration: %s", config_path)
                return False
            _run_loop(config_path, DeliveryHistory(state_path))
    except OSError:
        logging.exception("Could not access daemon state: %s", state_path.parent)
        return False
    return True


def _run_loop(config_path, history):
    cached_config = None
    cached_version = None

    while True:
        try:
            try:
                stat = config_path.stat()
                current_version = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
            except FileNotFoundError:
                if cached_config is not None:
                    raise
                current_version = None
            if cached_config is None or current_version != cached_version:
                # Publish the new configuration only after validation succeeds.
                candidate = load_config(config_path)
                cached_config = candidate
                cached_version = current_version
                logging.info("Configuration reloaded")
        except Exception:
            logging.exception("Could not load configuration; retaining last valid configuration")
            if cached_config is None:
                time.sleep(5)
                continue

        deliver_due(cached_config, datetime.now(), history)
        time.sleep(cached_config["settings"]["poll_seconds"])


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Desktop time threshold notification daemon"
    )
    parser.add_argument("--config", help="Override schedule configuration path")
    parser.add_argument(
        "--check", action="store_true",
        help="Validate the configuration and exit without sending notifications",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )
    config_path = resolve_config_path(args.config)
    if args.check:
        try:
            config = load_config(config_path)
        except (OSError, ValueError, TypeError) as exc:
            parser.exit(1, f"Invalid configuration: {exc}\n")
        print(f"Configuration valid: {config_path} ({len(config['schedules'])} schedules)")
        return
    try:
        if not run(config_path):
            parser.exit(1)
    except KeyboardInterrupt:
        logging.info("ChronoCue stopped")


if __name__ == "__main__":
    main()
