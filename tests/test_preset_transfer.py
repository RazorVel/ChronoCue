import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from chronocue.config import validate_config
from chronocue.preset_transfer import blank_bundle, export_bundle, import_bundle, load_bundle, save_bundle


class PresetTransferTest(unittest.TestCase):
    def setUp(self):
        self.config = validate_config({
            "presets": [{"id": "work", "name": "Work", "enabled": True}],
            "schedules": [{
                "id": "standup", "time": "09:30", "title": "Stand-up",
                "message": "Prepare notes", "days": ["mon", "tue"],
                "enabled": True, "preset_id": "work", "ringtone": "radar",
            }],
        })

    def test_blank_template_is_valid_and_imports_disabled(self):
        imported, summary = import_bundle(validate_config({}), blank_bundle())
        self.assertEqual(summary, {"presets": 1, "schedules": 1, "renamed": []})
        self.assertFalse(imported["presets"][0]["enabled"])
        self.assertEqual(imported["schedules"][0]["title"], "Example reminder")

    def test_export_omits_internal_ids_and_import_creates_new_ones(self):
        bundle = export_bundle(self.config, ["work"])
        self.assertNotIn("id", bundle["presets"][0])
        self.assertNotIn("id", bundle["presets"][0]["schedules"][0])
        imported, _summary = import_bundle(validate_config({}), bundle)
        self.assertNotEqual(imported["presets"][0]["id"], "work")
        self.assertNotEqual(imported["schedules"][0]["id"], "standup")
        self.assertEqual(imported["schedules"][0]["ringtone"], "radar")

    def test_name_conflicts_import_as_inactive_copies(self):
        imported, summary = import_bundle(self.config, export_bundle(self.config, ["work"]))
        self.assertEqual([item["name"] for item in imported["presets"]], ["Work", "Work (imported)"])
        self.assertEqual(summary["renamed"], [("Work", "Work (imported)")])
        self.assertFalse(imported["presets"][1]["enabled"])
        self.assertEqual(len(imported["schedules"]), 2)

    def test_invalid_bundle_does_not_mutate_config(self):
        original = json.dumps(self.config, sort_keys=True)
        cases = [
            {},
            {"format": "wrong", "version": 1, "presets": []},
            {"format": "chronocue-preset-bundle", "version": 2, "presets": []},
            {"format": "chronocue-preset-bundle", "version": 1,
             "presets": [{"name": "Bad", "schedules": [{"time": "99:00", "title": "Bad"}]}]},
        ]
        for bundle in cases:
            with self.subTest(bundle=bundle), self.assertRaises(ValueError):
                import_bundle(self.config, bundle)
            self.assertEqual(json.dumps(self.config, sort_keys=True), original)

    def test_save_and_load_are_atomic(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "preset.json"
            save_bundle(blank_bundle(), path)
            self.assertEqual(load_bundle(path), blank_bundle())
            with patch("chronocue.preset_transfer.os.replace", side_effect=OSError("disk full")), self.assertRaises(OSError):
                save_bundle(export_bundle(self.config, ["work"]), path)
            self.assertEqual(load_bundle(path), blank_bundle())
            self.assertEqual(list(path.parent.glob(".*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
