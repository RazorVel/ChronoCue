"""Human-editable import and export bundles for schedule presets."""

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import uuid

from .config import DAY_NAMES, validate_config
from .presets import find_preset


BUNDLE_FORMAT = "chronocue-preset-bundle"
BUNDLE_VERSION = 1


def blank_bundle():
    return {
        "format": BUNDLE_FORMAT,
        "version": BUNDLE_VERSION,
        "presets": [{
            "name": "My preset",
            "schedules": [{
                "time": "09:00",
                "title": "Example reminder",
                "message": "Replace this with your reminder text",
                "days": list(DAY_NAMES[:5]),
                "ringtone": None,
                "enabled": True,
            }],
        }],
    }


def export_bundle(config, preset_ids):
    exported = []
    for preset_id in preset_ids:
        preset = find_preset(config, preset_id)
        schedules = []
        for entry in config["schedules"]:
            if entry.get("preset_id") == preset_id:
                schedules.append({
                    "time": entry["time"], "title": entry["title"],
                    "message": entry.get("message", ""), "days": list(entry["days"]),
                    "ringtone": entry.get("ringtone"), "enabled": entry.get("enabled", True),
                })
        exported.append({"name": preset["name"], "schedules": schedules})
    if not exported:
        raise ValueError("Select at least one preset to export.")
    return {"format": BUNDLE_FORMAT, "version": BUNDLE_VERSION, "presets": exported}


def import_bundle(config, bundle):
    if not isinstance(bundle, dict):
        raise ValueError("Import file must contain a JSON object.")
    if bundle.get("format") != BUNDLE_FORMAT:
        raise ValueError(f"Import file format must be {BUNDLE_FORMAT!r}.")
    if bundle.get("version") != BUNDLE_VERSION:
        raise ValueError(f"Only preset bundle version {BUNDLE_VERSION} is supported.")
    imported = bundle.get("presets")
    if not isinstance(imported, list) or not imported:
        raise ValueError("Import file must contain at least one preset.")

    candidate = deepcopy(config)
    used_names = {item["name"].casefold() for item in candidate["presets"]}
    renamed = []
    schedule_count = 0
    for index, source in enumerate(imported):
        if not isinstance(source, dict):
            raise ValueError(f"presets[{index}] must be an object.")
        original_name = source.get("name")
        if not isinstance(original_name, str) or not original_name.strip():
            raise ValueError(f"presets[{index}].name must be a nonempty string.")
        original_name = original_name.strip()
        name = _unique_name(original_name, used_names)
        if name != original_name:
            renamed.append((original_name, name))
        used_names.add(name.casefold())
        preset_id = str(uuid.uuid4())
        candidate["presets"].append({"id": preset_id, "name": name, "enabled": False})
        schedules = source.get("schedules")
        if not isinstance(schedules, list):
            raise ValueError(f"presets[{index}].schedules must be a list.")
        for schedule_index, source_entry in enumerate(schedules):
            if not isinstance(source_entry, dict):
                raise ValueError(f"presets[{index}].schedules[{schedule_index}] must be an object.")
            candidate["schedules"].append({
                "id": str(uuid.uuid4()), "time": source_entry.get("time"),
                "title": source_entry.get("title"), "message": source_entry.get("message", ""),
                "days": source_entry.get("days", list(DAY_NAMES)),
                "ringtone": source_entry.get("ringtone"), "enabled": source_entry.get("enabled", True),
                "preset_id": preset_id,
            })
            schedule_count += 1
    return validate_config(candidate), {"presets": len(imported), "schedules": schedule_count, "renamed": renamed}


def load_bundle(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_bundle(bundle, path):
    path = Path(path)
    content = json.dumps(bundle, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp", delete=False) as file:
            temporary = Path(file.name)
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _unique_name(name, used_names):
    if name.casefold() not in used_names:
        return name
    candidate = f"{name} (imported)"
    ordinal = 2
    while candidate.casefold() in used_names:
        candidate = f"{name} (imported {ordinal})"
        ordinal += 1
    return candidate
