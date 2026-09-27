import subprocess
import unittest
from unittest.mock import Mock, patch

from chronocue.notifier import send_notification


class NotifierTest(unittest.TestCase):
    def test_passes_option_looking_text_as_body_and_bounds_process_time(self):
        with patch("chronocue.notifier.shutil.which", return_value="/usr/bin/notify-send"), patch(
            "chronocue.notifier.subprocess.run", return_value=Mock(returncode=0)
        ) as run:
            self.assertTrue(send_notification("Title", "--urgency=critical", 0, "low"))
        command = run.call_args.args[0]
        self.assertEqual(command[-3:], ["--", "[ChronoCue] Title", "--urgency=critical"])
        self.assertIn("0", command)
        self.assertEqual(run.call_args.kwargs["timeout"], 10)

    def test_missing_binary_does_not_start_process(self):
        with patch("chronocue.notifier.shutil.which", return_value=None), patch(
            "chronocue.notifier.subprocess.run"
        ) as run, self.assertLogs(level="ERROR"):
            self.assertFalse(send_notification("Title", "Body"))
        run.assert_not_called()

    def test_launch_error_and_timeout_report_failure(self):
        for error in (OSError("launch failed"), ValueError("embedded null byte"), subprocess.TimeoutExpired("notify-send", 10)):
            with self.subTest(error=error), patch(
                "chronocue.notifier.shutil.which", return_value="/usr/bin/notify-send"
            ), patch("chronocue.notifier.subprocess.run", side_effect=error), self.assertLogs(level="ERROR"):
                self.assertFalse(send_notification("Title", "Body"))

    def test_nonzero_exit_reports_failure(self):
        with patch("chronocue.notifier.shutil.which", return_value="/usr/bin/notify-send"), patch(
            "chronocue.notifier.subprocess.run", return_value=Mock(returncode=1)
        ):
            self.assertFalse(send_notification("Title", "Body"))


if __name__ == "__main__":
    unittest.main()
