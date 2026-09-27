import io
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, patch

from chronocue.config import DAY_NAMES, load_config_snapshot, save_config
from chronocue.ui import ScheduleEditor, main


class Value:
    """Minimal Tk variable replacement for tests without a display server."""

    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class EditorTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "schedule.json"
        save_config({
            "settings": {"custom_setting": {"keep": True}},
            "metadata": {"owner": "test"},
            "schedules": [{
                "id": "first",
                "time": "12:00",
                "title": "Original",
                "message": "Existing message",
                "days": ["mon"],
                "enabled": True,
                "custom_entry": {"keep": True}
            }]
        }, self.path)
        self.editor = ScheduleEditor.__new__(ScheduleEditor)
        self.editor.config_path = self.path
        self.editor.config, self.editor.config_revision = load_config_snapshot(self.path)
        self.editor.selected_id = "first"
        self.editor.filter_id = "all"
        self.editor.preset_var = Value("Ungrouped")
        self.editor.entry_sound_var = Value("Use default")
        self.editor.time_var = Value("13:45")
        self.editor.title_var = Value("Updated")
        self.editor.message_var = Value("Updated message")
        self.editor.enabled_var = Value(True)
        self.editor.day_vars = {day: Value(day == "tue") for day in DAY_NAMES}
        self.editor.tree = Mock()
        self.editor.tree.selection.return_value = []
        self.editor.refresh_tree = Mock()
        self.editor.refresh_presets = Mock()
        self.editor.load_preferences = Mock()
        self.errors = patch("chronocue.ui.messagebox.showerror").start()
        self.addCleanup(patch.stopall)

    def test_failed_add_update_and_delete_leave_config_unchanged(self):
        for action in ("add", "update", "delete"):
            with self.subTest(action=action):
                self.editor.selected_id = None if action == "add" else "first"
                before = deepcopy(self.editor.config)
                before_revision = self.editor.config_revision
                original_bytes = self.path.read_bytes()
                with patch("chronocue.ui.save_config", side_effect=OSError("Disk full")):
                    if action == "delete":
                        self.editor.delete_entry()
                    else:
                        self.editor.save_entry()
                self.assertEqual(self.editor.config, before)
                self.assertEqual(self.editor.config_revision, before_revision)
                self.assertEqual(self.path.read_bytes(), original_bytes)
                self.assertEqual(self.editor.selected_id, None if action == "add" else "first")
                self.assertEqual(self.editor.title_var.get(), "Updated")
        self.assertEqual(self.errors.call_count, 3)
        self.editor.refresh_tree.assert_not_called()

    def test_update_preserves_unknown_fields_and_tracks_saved_revision(self):
        self.editor.save_entry()
        config, revision = load_config_snapshot(self.path)
        self.assertEqual(config, self.editor.config)
        self.assertEqual(revision, self.editor.config_revision)
        self.assertEqual(config["metadata"], {"owner": "test"})
        self.assertEqual(config["settings"]["custom_setting"], {"keep": True})
        entry = config["schedules"][0]
        self.assertEqual(entry["custom_entry"], {"keep": True})
        self.assertEqual(entry["title"], "Updated")
        self.assertEqual(entry["days"], ["tue"])
        self.assertEqual(entry["id"], "first")
        # Saving twice must use the revision of the first successful save.
        self.editor.title_var.set("Updated again")
        self.editor.save_entry()
        self.assertEqual(load_config_snapshot(self.path)[0]["schedules"][0]["title"], "Updated again")
        self.errors.assert_not_called()

    def test_add_then_delete_updates_disk_and_selection(self):
        self.editor.selected_id = None
        self.editor.save_entry()
        added_id = self.editor.selected_id
        config, _ = load_config_snapshot(self.path)
        self.assertEqual(len(config["schedules"]), 2)
        self.assertEqual(config["schedules"][1]["id"], added_id)
        self.editor.delete_entry()
        config, _ = load_config_snapshot(self.path)
        self.assertEqual([entry["id"] for entry in config["schedules"]], ["first"])
        self.assertIsNone(self.editor.selected_id)
        self.assertEqual(self.editor.title_var.get(), "")
        self.errors.assert_not_called()

    def test_external_change_blocks_add_update_and_delete_until_reload(self):
        external = deepcopy(self.editor.config)
        external["schedules"][0]["title"] = "Changed elsewhere"
        save_config(external, self.path)
        before = deepcopy(self.editor.config)
        external_bytes = self.path.read_bytes()
        for action in ("add", "update", "delete"):
            with self.subTest(action=action):
                self.editor.selected_id = None if action == "add" else "first"
                if action == "delete":
                    self.editor.delete_entry()
                else:
                    self.editor.save_entry()
                self.assertEqual(self.editor.config, before)
                self.assertEqual(self.path.read_bytes(), external_bytes)
                self.assertIn("Reload", self.errors.call_args.args[1])
        self.editor.reload_config()
        self.assertEqual(self.editor.config["schedules"][0]["title"], "Changed elsewhere")
        self.assertIsNone(self.editor.selected_id)
        self.editor.selected_id = "first"
        self.editor.time_var.set("14:00")
        self.editor.title_var.set("After reload")
        self.editor.save_entry()
        self.assertEqual(load_config_snapshot(self.path)[0]["schedules"][0]["title"], "After reload")
        self.assertEqual(self.errors.call_count, 3)

    def test_invalid_reload_keeps_previous_data_and_form(self):
        before = deepcopy(self.editor.config)
        revision = self.editor.config_revision
        self.path.write_text("{ broken json", encoding="utf-8")
        self.editor.reload_config()
        self.assertEqual(self.editor.config, before)
        self.assertEqual(self.editor.config_revision, revision)
        self.assertEqual(self.editor.selected_id, "first")
        self.assertEqual(self.editor.title_var.get(), "Updated")
        self.errors.assert_called_once()

    def test_time_validation_requires_two_digit_hour_and_minute(self):
        for value in ("9:00", "09:0", "24:00", "12:60", "noon"):
            with self.subTest(value=value):
                self.editor.time_var.set(value)
                with patch("chronocue.ui.save_config") as save:
                    self.editor.save_entry()
                save.assert_not_called()
                self.assertIn("HH:MM", self.errors.call_args.args[1])

    def test_opening_idless_config_does_not_modify_file(self):
        data = json.loads(self.path.read_text(encoding="utf-8"))
        del data["schedules"][0]["id"]
        self.path.write_text(json.dumps(data), encoding="utf-8")
        before = self.path.read_bytes()

        def build_headless(editor):
            editor.tree = Mock()
            editor.tree.get_children.return_value = []

        with patch.object(ScheduleEditor, "build_ui", build_headless), \
             patch.object(ScheduleEditor, "refresh_presets"), \
             patch.object(ScheduleEditor, "refresh_tree"), \
             patch.object(ScheduleEditor, "load_preferences"), \
             patch.object(ScheduleEditor, "timer_tick"), \
             patch.object(ScheduleEditor, "drain_results"):
            editor = ScheduleEditor(Mock(), self.path)
            editor.refresh_tree()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(editor.config["schedules"][0]["id"])


class StartupTest(unittest.TestCase):
    def test_missing_display_exits_with_readable_error(self):
        from tkinter import TclError

        error = io.StringIO()
        with patch("sys.argv", ["chronocue-ui"]), patch("sys.stderr", error):
            with patch("chronocue.ui.tk.Tk", side_effect=TclError("no display available")):
                with self.assertRaises(SystemExit) as result:
                    main()
        self.assertEqual(result.exception.code, 1)
        self.assertIn("no display available", error.getvalue())
        self.assertNotIn("Traceback", error.getvalue())

    def test_invalid_config_destroys_window_and_reports_error(self):
        error = io.StringIO()
        root = Mock()
        with patch("sys.argv", ["chronocue-ui"]), patch("sys.stderr", error):
            with patch("chronocue.ui.tk.Tk", return_value=root):
                with patch("chronocue.ui.ScheduleEditor", side_effect=ValueError("Invalid config")):
                    with self.assertRaises(SystemExit) as result:
                        main()
        self.assertEqual(result.exception.code, 1)
        root.destroy.assert_called_once()
        root.mainloop.assert_not_called()
        self.assertIn("Invalid config", error.getvalue())
        self.assertNotIn("Traceback", error.getvalue())


if __name__ == "__main__":
    unittest.main()
