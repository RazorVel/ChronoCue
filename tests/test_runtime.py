import fcntl
import io
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from chronocue.daemon import (
    DeliveryHistory,
    _run_loop,
    deliver_due,
    delivery_state_path,
    due_occurrence,
    entry_is_due,
    main,
    run,
)


class StopLoop(BaseException):
    pass


def example_config(**entry_changes):
    entry = {
        "id": "lunch",
        "time": "13:00",
        "days": ["sun"],
        "title": "Lunch",
        "message": "Break",
        "enabled": True,
    }
    entry.update(entry_changes)
    return {
        "settings": {
            "poll_seconds": 5,
            "max_late_seconds": 120,
            "notification_timeout_ms": 10000,
            "urgency": "normal",
        },
        "schedules": [entry],
    }


class MidnightScheduleTest(unittest.TestCase):
    def test_grace_uses_previous_scheduled_date_and_weekday(self):
        entry = {"time": "23:59", "days": ["sun"]}
        now = datetime(2026, 9, 28, 0, 0, 30)
        self.assertEqual(due_occurrence(entry, now, 120), datetime(2026, 9, 27, 23, 59))
        self.assertFalse(entry_is_due({**entry, "days": ["mon"]}, now, 120))

    def test_grace_boundary_and_before_scheduled_time(self):
        entry = {"time": "23:59", "days": ["sun"]}
        self.assertTrue(entry_is_due(entry, datetime(2026, 9, 28, 0, 1), 120))
        self.assertFalse(entry_is_due(entry, datetime(2026, 9, 28, 0, 1, 1), 120))
        self.assertFalse(entry_is_due(entry, datetime(2026, 9, 27, 23, 58, 59), 120))

    def test_disabled_and_zero_grace(self):
        entry = {"time": "13:00", "enabled": False}
        self.assertFalse(entry_is_due(entry, datetime(2026, 9, 27, 13), 0))
        entry["enabled"] = True
        self.assertTrue(entry_is_due(entry, datetime(2026, 9, 27, 13), 0))
        self.assertFalse(entry_is_due(entry, datetime(2026, 9, 27, 13, 0, 1), 0))


class RuntimeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / "schedule.json"
        self.config_path.write_text(json.dumps(example_config()), encoding="utf-8")
        self.history_path = self.root / "state.json"

    def test_success_survives_restart_and_config_reload(self):
        history = DeliveryHistory(self.history_path)
        config = example_config()
        now = datetime(2026, 9, 27, 13)
        with patch("chronocue.daemon.send_notification", return_value=True) as send:
            deliver_due(config, now, history)
            config["schedules"][0]["title"] = "Changed title"
            deliver_due(config, now, history)
            deliver_due(config, now, DeliveryHistory(self.history_path))
        send.assert_called_once()

    def test_failed_notification_retries(self):
        history = DeliveryHistory(self.history_path)
        now = datetime(2026, 9, 27, 13)
        with patch("chronocue.daemon.send_notification", side_effect=[False, True]) as send:
            with self.assertLogs(level="ERROR"):
                deliver_due(example_config(), now, history)
            self.assertFalse(history.sent)
            deliver_due(example_config(), now, history)
        self.assertEqual(send.call_count, 2)
        self.assertEqual(len(history.sent), 1)

    def test_midnight_delivery_is_not_repeated_after_date_changes(self):
        history = DeliveryHistory(self.history_path)
        config = example_config(time="23:59")
        with patch("chronocue.daemon.send_notification", return_value=True) as send:
            deliver_due(config, datetime(2026, 9, 27, 23, 59), history)
            deliver_due(config, datetime(2026, 9, 28, 0, 0, 30), DeliveryHistory(self.history_path))
        send.assert_called_once()
        self.assertIn(("2026-09-27", "lunch", "23:59"), history.sent)

    def test_new_day_has_new_occurrence(self):
        config = example_config(days=["sun", "mon"])
        history = DeliveryHistory(self.history_path)
        with patch("chronocue.daemon.send_notification", return_value=True) as send:
            deliver_due(config, datetime(2026, 9, 27, 13), history)
            deliver_due(config, datetime(2026, 9, 28, 13), history)
        self.assertEqual(send.call_count, 2)

    def test_invalid_startup_recovers_after_edit(self):
        self.config_path.write_text("{", encoding="utf-8")
        history = DeliveryHistory(self.history_path)

        def sleep(_seconds):
            if self.config_path.read_text(encoding="utf-8") == "{":
                self.config_path.write_text(json.dumps(example_config()), encoding="utf-8")
            else:
                raise StopLoop

        with patch("chronocue.daemon.time.sleep", side_effect=sleep), patch(
            "chronocue.daemon.datetime"
        ) as clock, patch("chronocue.daemon.send_notification", return_value=True) as send:
            clock.now.return_value = datetime(2026, 9, 27, 13)
            with self.assertLogs(level="ERROR"), self.assertRaises(StopLoop):
                _run_loop(self.config_path, history)
        send.assert_called_once()

    def test_invalid_reload_retains_last_valid_config(self):
        self._check_bad_reload(remove=False)

    def test_missing_file_retains_last_valid_config(self):
        self._check_bad_reload(remove=True)

    def _check_bad_reload(self, remove):
        history = DeliveryHistory(self.history_path)
        sleeps = 0

        def sleep(_seconds):
            nonlocal sleeps
            sleeps += 1
            if sleeps == 1:
                if remove:
                    self.config_path.unlink()
                else:
                    self.config_path.write_text('{"settings":{"poll_seconds":"bad"}}', encoding="utf-8")
            else:
                raise StopLoop

        with patch("chronocue.daemon.time.sleep", side_effect=sleep), patch(
            "chronocue.daemon.datetime"
        ) as clock, patch("chronocue.daemon.send_notification", return_value=True) as send:
            clock.now.side_effect = [datetime(2026, 9, 27, 12, 59, 59), datetime(2026, 9, 27, 13)]
            with self.assertLogs(level="ERROR"), self.assertRaises(StopLoop):
                _run_loop(self.config_path, history)
        send.assert_called_once_with("Lunch", "Break", 10000, "normal")

    def test_corrupt_history_recovers(self):
        self.history_path.write_text('{"sent": [null]}', encoding="utf-8")
        with self.assertLogs(level="ERROR"):
            history = DeliveryHistory(self.history_path)
        self.assertEqual(history.sent, set())
        with patch("chronocue.daemon.send_notification", return_value=True) as send:
            deliver_due(example_config(), datetime(2026, 9, 27, 13), history)
        send.assert_called_once()
        self.assertEqual(len(json.loads(self.history_path.read_text())["sent"]), 1)

    def test_cannot_persist_history_keeps_in_memory_deduplication(self):
        history = DeliveryHistory(self.history_path)
        with patch("chronocue.daemon.os.replace", side_effect=OSError("read only")), patch(
            "chronocue.daemon.send_notification", return_value=True
        ) as send:
            with self.assertLogs(level="ERROR"):
                deliver_due(example_config(), datetime(2026, 9, 27, 13), history)
            deliver_due(example_config(), datetime(2026, 9, 27, 13), history)
        send.assert_called_once()
        self.assertEqual(list(self.root.glob(".*.tmp")), [])

    def test_state_path_is_separate_for_each_config(self):
        with patch.dict(os.environ, {"XDG_STATE_HOME": str(self.root)}):
            first = delivery_state_path(self.config_path)
            self.assertEqual(first.parent, self.root / "chronocue")
            self.assertEqual(first, delivery_state_path(self.root / "." / "schedule.json"))
            self.assertNotEqual(first, delivery_state_path(self.root / "other.json"))

    def test_single_instance_lock_is_released_on_exit(self):
        with patch.dict(os.environ, {"XDG_STATE_HOME": str(self.root)}):
            state_path = delivery_state_path(self.config_path)
            state_path.parent.mkdir(parents=True)
            with state_path.with_suffix(".lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch("chronocue.daemon._run_loop") as loop, self.assertLogs(level="ERROR"):
                    self.assertFalse(run(self.config_path))
                    loop.assert_not_called()
            with patch("chronocue.daemon._run_loop") as loop:
                self.assertTrue(run(self.config_path))
                self.assertTrue(run(self.config_path))
            self.assertEqual(loop.call_count, 2)

    def test_check_validates_without_starting_daemon(self):
        with patch("chronocue.daemon.run") as daemon, patch("sys.stdout", new_callable=io.StringIO) as output:
            main(["--config", str(self.config_path), "--check"])
        daemon.assert_not_called()
        self.assertIn("Configuration valid:", output.getvalue())

    def test_check_reports_invalid_configuration(self):
        self.config_path.write_text("{", encoding="utf-8")
        with patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit) as stopped:
            main(["--config", str(self.config_path), "--check"])
        self.assertEqual(stopped.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
