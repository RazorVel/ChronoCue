"""Shared configuration validation and atomic, conflict-aware persistence."""

import copy
import fcntl
import hashlib
import json
import math
import os
import re
import stat
import tempfile
import uuid
from pathlib import Path


APP_NAME = "chronocue"
DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DEFAULT_CONFIG = {
    "settings": {
        "poll_seconds": 5,
        "max_late_seconds": 120,
        "notification_timeout_ms": 10000,
        "urgency": "normal",
    },
    "schedules": [],
}
_UNSET = object()


class ConfigConflictError(ValueError):
    """The file changed since an editor read it."""


def default_config_path() -> Path:
    value = os.environ.get("XDG_CONFIG_HOME", "")
    config_home = Path(value) if value and Path(value).is_absolute() else Path.home() / ".config"
    return config_home / APP_NAME / "schedule.json"


def resolve_config_path(path=None) -> Path:
    value = path or os.environ.get("CHRONOCUE_CONFIG") or default_config_path()
    return Path(value).expanduser().resolve()


def parse_clock(value: str):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{2}:[0-9]{2}", value):
        raise ValueError(f"Invalid time {value!r}. Expected 24-hour HH:MM.")
    hour, minute = map(int, value.split(":"))
    if hour > 23 or minute > 59:
        raise ValueError(f"Invalid time {value!r}. Expected 24-hour HH:MM.")
    return hour, minute


def validate_config(data):
    """Return a normalized copy; never mutate the caller or discard extra fields."""
    if not isinstance(data, dict):
        raise ValueError("Config root must be a JSON object")
    result = copy.deepcopy(data)
    supplied_settings = data.get("settings", {})
    if not isinstance(supplied_settings, dict):
        raise ValueError("'settings' must be an object")
    settings = {**DEFAULT_CONFIG["settings"], **supplied_settings}
    poll = settings["poll_seconds"]
    if (isinstance(poll, bool) or not isinstance(poll, (float, int))
            or not 1 <= poll <= 86400 or not math.isfinite(poll)):
        raise ValueError("settings.poll_seconds must be a finite number from 1 to 86400")
    late = settings["max_late_seconds"]
    if isinstance(late, bool) or not isinstance(late, int) or not 0 <= late <= 86400:
        raise ValueError("settings.max_late_seconds must be an integer from 0 to 86400")
    timeout = settings["notification_timeout_ms"]
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not -1 <= timeout <= 2147483647:
        raise ValueError("settings.notification_timeout_ms must be an integer from -1 to 2147483647")
    if settings["urgency"] not in ("low", "normal", "critical"):
        raise ValueError("settings.urgency must be low, normal, or critical")
    result["settings"] = settings

    schedules = data.get("schedules", [])
    if not isinstance(schedules, list):
        raise ValueError("'schedules' must be a list")
    normalized = []
    used_ids = set()
    legacy_counts = {}
    for index, original in enumerate(schedules):
        context = f"schedules[{index}]"
        if not isinstance(original, dict):
            raise ValueError(f"{context} must be an object")
        entry = copy.deepcopy(original)
        try:
            parse_clock(entry.get("time"))
        except ValueError as exc:
            raise ValueError(f"{context}: {exc}") from exc
        entry.setdefault("title", "Scheduled Alert")
        entry.setdefault("message", "")
        entry.setdefault("enabled", True)
        if not isinstance(entry["title"], str) or not entry["title"].strip():
            raise ValueError(f"{context}.title must be a nonempty string")
        if not isinstance(entry["message"], str):
            raise ValueError(f"{context}.message must be a string")
        if "\0" in entry["title"] or "\0" in entry["message"]:
            raise ValueError(f"{context}.title and message cannot contain null characters")
        if not isinstance(entry["enabled"], bool):
            raise ValueError(f"{context}.enabled must be true or false")
        days = entry.get("days")
        # Preserve the original daemon's every-day semantics for omitted/empty days.
        if days is None or days == []:
            days = list(DAY_NAMES)
        if not isinstance(days, list) or any(not isinstance(day, str) for day in days):
            raise ValueError(f"{context}.days must be a list of weekday names")
        days = [day.strip().lower() for day in days]
        if any(day not in DAY_NAMES for day in days):
            raise ValueError(f"{context}.days must contain only: {', '.join(DAY_NAMES)}")
        entry["days"] = [day for day in DAY_NAMES if day in days]
        if "id" not in entry:
            # Stable across reloads and reordering, without rewriting hand-edited files.
            canonical = json.dumps(entry, sort_keys=True, ensure_ascii=False, allow_nan=False)
            ordinal = legacy_counts.get(canonical, 0)
            legacy_counts[canonical] = ordinal + 1
            entry["id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, f"chronocue:{canonical}:{ordinal}"))
        if not isinstance(entry["id"], str) or not entry["id"].strip():
            raise ValueError(f"{context}.id must be a nonempty string")
        if entry["id"] in used_ids:
            raise ValueError(f"{context}.id duplicates {entry['id']!r}")
        used_ids.add(entry["id"])
        normalized.append(entry)
    result["schedules"] = normalized
    return result


def config_revision(path: Path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def ensure_config(path: Path):
    path = Path(path)
    if not path.exists():
        try:
            save_config(DEFAULT_CONFIG, path, expected_revision=None)
        except ConfigConflictError:
            # Another process initialized it first. Never overwrite that file.
            pass


def load_config_snapshot(path: Path):
    path = Path(path)
    ensure_config(path)
    content = path.read_bytes()
    data = validate_config(json.loads(content.decode("utf-8")))
    return data, hashlib.sha256(content).hexdigest()


def load_config(path: Path):
    return load_config_snapshot(path)[0]


def save_config(config, path: Path, *, expected_revision=_UNSET):
    """Write atomically and return its revision; reject stale cooperating editors."""
    path = Path(path)
    data = validate_config(config)
    content = (json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    # The lock has a stable inode; locking the JSON itself would break on replace.
    lock_fd = os.open(path.with_name(path.name + ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(lock_fd, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if expected_revision is not _UNSET and config_revision(path) != expected_revision:
            raise ConfigConflictError("The schedule file changed. Reload it before saving again.")
        mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as file:
                temporary_path = Path(file.name)
                os.fchmod(file.fileno(), mode)
                file.write(content)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary_path, path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
    return hashlib.sha256(content).hexdigest()
