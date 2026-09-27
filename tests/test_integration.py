"""Exercise real daemon processes with an isolated fake notification executable."""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

import chronocue


class DaemonIntegrationTest(unittest.TestCase):
    def test_delivery_reload_restart_and_single_instance(self):
        with tempfile.TemporaryDirectory(prefix="chronocue-integration-") as temporary:
            root = Path(temporary)
            binary_dir = root / "bin"
            binary_dir.mkdir()
            notifications = root / "notifications.jsonl"
            config_path = root / "schedule.json"
            log_path = root / "daemon.log"
            fake_notify = binary_dir / "notify-send"
            fake_notify.write_text(
                f"#!{sys.executable}\n"
                "import json, os, sys, time\n"
                "title = sys.argv[-2]\n"
                "success = title != '[ChronoCue] RetryProbe'\n"
                "row = {'title': title, 'success': success, 'at': time.monotonic()}\n"
                "with open(os.environ['CHRONOCUE_TEST_NOTIFICATION_LOG'], 'a', encoding='utf-8') as log:\n"
                "    log.write(json.dumps(row) + '\\n')\n"
                "sys.exit(0 if success else 1)\n",
                encoding="utf-8",
            )
            fake_notify.chmod(0o700)
            env = os.environ.copy()
            env.update({
                # Restrict PATH so a real desktop executable cannot be reached.
                "PATH": str(binary_dir),
                "PYTHONPATH": str(Path(chronocue.__file__).resolve().parent.parent),
                "PYTHONDONTWRITEBYTECODE": "1",
                "XDG_STATE_HOME": str(root / "state"),
                "XDG_CONFIG_HOME": str(root / "config"),
                "CHRONOCUE_TEST_NOTIFICATION_LOG": str(notifications),
            })
            command = [sys.executable, "-m", "chronocue.daemon", "--config", str(config_path)]
            scheduled_time = datetime.now().strftime("%H:%M")
            first = {"id": "first", "time": scheduled_time, "title": "First"}
            probe = {"id": "retry-probe", "time": scheduled_time, "title": "RetryProbe"}
            second = {"id": "second", "time": scheduled_time, "title": "Second"}
            config = {
                "settings": {"poll_seconds": 1, "max_late_seconds": 86400},
                "schedules": [first, probe],
            }

            def replace_config(value):
                pending = root / "pending.json"
                pending.write_text(json.dumps(value), encoding="utf-8")
                pending.replace(config_path)

            def entries():
                if not notifications.exists():
                    return []
                result = []
                for line in notifications.read_text(encoding="utf-8").splitlines():
                    try:
                        result.append(json.loads(line))
                    except json.JSONDecodeError:
                        # A reader can observe the final line while it is being written.
                        break
                return result

            def daemon_log():
                return log_path.read_text(encoding="utf-8") if log_path.exists() else ""

            def wait_for(predicate, process, description, timeout=8):
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    self.assertIsNone(process.poll(), f"Daemon exited while {description}:\n{daemon_log()}")
                    if predicate():
                        return
                    time.sleep(0.05)
                self.fail(f"Timed out {description}:\n{daemon_log()}")

            def stop(process):
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)
                for stream in (process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()

            replace_config(config)
            processes = []
            with log_path.open("ab", buffering=0) as log:
                try:
                    process = subprocess.Popen(command, env=env, stdout=log, stderr=log)
                    processes.append(process)
                    wait_for(
                        lambda: any(row["title"] == "[ChronoCue] First" and row["success"] for row in entries()),
                        process, "waiting for the first due delivery",
                    )

                    # Invalid settings must retain the previous config. A failed
                    # reminder in that config should still be retried afterward.
                    replace_config({"settings": {"poll_seconds": "invalid"}, "schedules": []})
                    wait_for(
                        lambda: "retaining last valid configuration" in daemon_log(),
                        process, "waiting for invalid reload rejection",
                    )
                    rejection_observed = time.monotonic()
                    wait_for(
                        lambda: any(row["title"] == "[ChronoCue] RetryProbe" and row["at"] > rejection_observed for row in entries()),
                        process, "waiting for a retry from the retained configuration",
                    )

                    config["schedules"] = [first, second]
                    replace_config(config)
                    wait_for(
                        lambda: any(row["title"] == "[ChronoCue] Second" and row["success"] for row in entries()),
                        process, "waiting for corrected configuration delivery",
                    )
                    # Wait until the successful delivery is durably recorded,
                    # closing the documented send-to-record crash window.
                    def both_recorded():
                        for state_path in (root / "state" / "chronocue").glob("*.json"):
                            rows = json.loads(state_path.read_text(encoding="utf-8"))["sent"]
                            if {row[1] for row in rows} == {"first", "second"}:
                                return True
                        return False

                    wait_for(both_recorded, process, "waiting for delivery history")
                    stop(process)

                    restart_log_offset = len(daemon_log())
                    process = subprocess.Popen(command, env=env, stdout=log, stderr=log)
                    processes.append(process)
                    wait_for(
                        lambda: "Configuration reloaded" in daemon_log()[restart_log_offset:],
                        process, "waiting for restart",
                    )
                    duplicate = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    processes.append(duplicate)
                    _stdout, stderr = duplicate.communicate(timeout=8)
                    self.assertEqual(duplicate.returncode, 1, stderr.decode())
                    self.assertIn(b"already running", stderr)

                    # Observe multiple real polling intervals after restart.
                    observation_end = time.monotonic() + 2.2
                    while time.monotonic() < observation_end:
                        self.assertIsNone(process.poll(), daemon_log())
                        time.sleep(0.05)
                    successful_titles = [row["title"] for row in entries() if row["success"]]
                    self.assertEqual(successful_titles, ["[ChronoCue] First", "[ChronoCue] Second"])
                finally:
                    for process in reversed(processes):
                        stop(process)


if __name__ == "__main__":
    unittest.main()
